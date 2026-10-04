"""A ``schema.sql`` that promises to provision a database from scratch must provision the database production has.

The incident (Upwork ``realtime_applications``, audit RA5, 2026-10-03): ``sql/schema.sql`` said it builds the schema, and nothing ever
compared what it builds with production. Five ``created_at``/``added_at`` columns were ``timestamp`` where production has ``timestamptz``
(a 3-hour shift in every ``created_at > now() - ...`` on a Moscow-zone server), eleven live columns were missing from tables the file
creates (cost columns the dashboard reads), and thirteen live tables were absent although each exists in some migration file. A static
parse sees none of it: only PROVISIONING the file on an empty server and reading the catalogue does.

The gate starts a private throwaway server through :mod:`py_ci_shared.embedded_postgres`, creates an empty database, applies the file
statement by statement (twice, so an idempotence break is a finding), reads ``information_schema`` (and ``pg_indexes`` when asked) and
compares it with a committed snapshot of production. It never connects to any other database. With no Postgres binaries it says so
loudly and reports NOT CHECKED (``ParityResult.checked`` is False, exit code ``--missing-exit``), never a pass.

Snapshot JSON, ``schema_version`` 1 (written by :func:`refresh_snapshot`, never by the gate)::

    {
      "schema_version": 1,
      "_source": "free text: where and when it was read",
      "tables": {
        "<schema>.<table>": {
          "columns": {
            "<column>": {
              "data_type": "timestamp with time zone",   # information_schema.columns.data_type; timezone-ness is part of it
              "is_nullable": "NO",                       # "YES" | "NO"
              "udt_name": "vector",                      # optional; written for USER-DEFINED and ARRAY columns
              "generated": false,                        # a generation expression exists
              "identity": null                           # null | "ALWAYS" | "BY DEFAULT"
            }
          },
          "indexes": ["<index name>", ...]               # optional; compared only with check_indexes=True
        }
      }
    }

A snapshot without ``schema_version`` is the legacy layout ``{"tables": {"<schema>.<table>": {"<column>": {"data_type", "is_nullable"}}}}``
and is still read: a column attribute the snapshot does not declare is not compared. Every version-1 attribute above except ``data_type``
and ``is_nullable`` is optional for the same reason. Tables are the relations ``information_schema.columns`` lists (tables, views,
foreign tables; not materialized views).

Usage::

    from py_ci_shared.schema_snapshot_parity import assert_schema_matches_snapshot

    def test_schema_sql_provisions_production():
        result = assert_schema_matches_snapshot("sql/schema.sql", "sql/production_columns_snapshot.json", extensions=("vector",))
        if not result.checked:
            pytest.skip(result.reason)   # loud already; the result is never a silent pass

CLI::

    python -m py_ci_shared.schema_snapshot_parity check sql/schema.sql sql/production_columns_snapshot.json --extension vector --missing-exit 3
    python -m py_ci_shared.schema_snapshot_parity refresh sql/production_columns_snapshot.json --dsn-env PROD_READONLY_DSN --schema jobs_matching

``refresh`` is a human action against a database you name by environment variable: the session is read-only
(``default_transaction_read_only=on`` plus ``set_session(readonly=True)``), keepalives are on, and the DSN is never printed (a failure
prints the exception type and SQLSTATE only). The gate itself never runs it.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ._core import Finding
from ._core.baseline import atomic_write_text, dump_json
from .embedded_postgres import embedded_postgres, find_pg_bin

__all__ = [
    "SCHEMA_VERSION",
    "ParityResult",
    "SchemaParityError",
    "assert_schema_matches_snapshot",
    "compare_catalogues",
    "connect_read_only",
    "find_schema_snapshot_problems",
    "load_snapshot",
    "read_catalogue",
    "refresh_snapshot",
    "split_statements",
]

SCHEMA_VERSION = 1
_NULLABLE = ("YES", "NO")
_IDENTITY = (None, "ALWAYS", "BY DEFAULT")
_COLUMN_KEYS = frozenset({"data_type", "is_nullable", "udt_name", "generated", "identity"})
_MIN_REASON = 12
#: libpq keepalives so a long catalogue read through a NAT or pooler is not silently dropped.
KEEPALIVES = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5}

Column = dict  # {"data_type", "is_nullable", ...}
Catalogue = dict  # {"schema.table": {"columns": {name: Column}, "indexes": [names] | None}}


class SchemaParityError(Exception):
    """A usage or environment error (unreadable file, bad snapshot, missing driver or extension): fix the input, not the schema."""


@dataclass
class ParityResult:
    """What a check found. ``checked`` False means NOTHING was compared (no server): never read it as parity."""

    checked: bool
    findings: list = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        """True only when the comparison ran and found nothing."""
        return self.checked and not self.findings


# ---------------------------------------------------------------------------------------------------------------- snapshot


def _bad(path: object, what: str) -> SchemaParityError:
    return SchemaParityError(f"snapshot {path}: {what}. Regenerate it with `python -m py_ci_shared.schema_snapshot_parity refresh`.")


def _check_column(path: object, table: str, name: str, col: object) -> Column:
    where = f"{table}.{name}"
    if not isinstance(col, dict):
        raise _bad(path, f"column {where} is not an object")
    unknown = sorted(set(col) - _COLUMN_KEYS)
    if unknown:
        raise _bad(path, f"column {where} has unknown keys {unknown}")
    if not isinstance(col.get("data_type"), str) or col.get("is_nullable") not in _NULLABLE:
        raise _bad(path, f"column {where} needs a string data_type and is_nullable of YES or NO")
    if "generated" in col and not isinstance(col["generated"], bool):
        raise _bad(path, f"column {where}: generated must be true or false")
    if "identity" in col and col["identity"] not in _IDENTITY:
        raise _bad(path, f"column {where}: identity must be null, ALWAYS or BY DEFAULT")
    return dict(col)


def parse_snapshot(data: object, path: object = "<snapshot>") -> Catalogue:
    """Validate a decoded snapshot (version 1 or the legacy layout) and return ``{table: {"columns": ..., "indexes": ...}}``."""
    if not isinstance(data, dict) or not isinstance(data.get("tables"), dict):
        raise _bad(path, "top level must be an object with a `tables` object")
    version = data.get("schema_version")
    if version not in (None, SCHEMA_VERSION):
        raise _bad(path, f"schema_version {version!r} is not one this gate reads (it reads {SCHEMA_VERSION} and the legacy layout)")
    out: Catalogue = {}
    for table, body in data["tables"].items():
        if not re.fullmatch(r"[^.]+\.[^.]+", table):
            raise _bad(path, f"table key {table!r} must be `schema.table`")
        if not isinstance(body, dict):
            raise _bad(path, f"table {table} is not an object")
        legacy = version is None
        columns = body if legacy else body.get("columns")
        if not isinstance(columns, dict):
            raise _bad(path, f"table {table} has no `columns` object")
        indexes = None if legacy else body.get("indexes")
        if indexes is not None and not (isinstance(indexes, list) and all(isinstance(i, str) for i in indexes)):
            raise _bad(path, f"table {table}: indexes must be a list of names")
        out[table] = {
            "columns": {name: _check_column(path, table, name, col) for name, col in columns.items()},
            "indexes": sorted(indexes) if indexes is not None else None,
        }
    return out


def load_snapshot(path: "str | Path") -> Catalogue:
    """Read and validate the snapshot file; unreadable or malformed is a :class:`SchemaParityError`, never an empty comparison."""
    import json

    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SchemaParityError(f"snapshot {p} is unreadable ({type(exc).__name__}); pass the committed snapshot JSON") from None
    return parse_snapshot(data, p)


# ---------------------------------------------------------------------------------------------------------------- statements


_TOKEN = re.compile(
    r"(?P<line>--[^\n]*)|(?P<block>/\*)|(?P<quote>'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")"
    r"|(?P<dollar>\$[A-Za-z_]?[A-Za-z0-9_]*\$)|(?P<semi>;)|(?P<text>[^-/'\"$;]+|.)",
    re.S,
)


def _block_comment_end(sql: str, i: int) -> int:
    """Index after the (nestable) block comment whose opener is at *i*."""
    depth, i = 1, i + 2
    while i < len(sql) and depth:
        pair = sql[i : i + 2]
        depth += {"/*": 1, "*/": -1}.get(pair, 0)
        i += 2 if pair in ("/*", "*/") else 1
    return i


def split_statements(sql: str) -> "list[str]":
    """Split a script on top-level ``;``, honouring quotes, ``$tag$..$tag$``, ``--`` and nested block comments, as psql would.

    Comments are dropped; a ``DO $$ ... ; ... $$`` block stays one statement.
    """
    out: list = []
    buf: list = []
    i = 0
    while i < len(sql):
        m = _TOKEN.match(sql, i)
        assert m is not None  # the last alternative matches any single character
        kind, tok, start, i = m.lastgroup, m.group(0), m.start(), m.end()
        if kind == "block":
            i = _block_comment_end(sql, start)
            buf.append(" ")
        elif kind == "dollar":
            end = sql.find(tok, i)
            i = len(sql) if end < 0 else end + len(tok)
            buf.append(sql[start:i])
        elif kind == "semi":
            stmt = "".join(buf).strip()
            out += [stmt + ";"] if stmt else []
            buf = []
        elif kind != "line":
            buf.append(tok)
    tail = "".join(buf).strip()
    return out + ([tail + ";"] if tail else [])


# ---------------------------------------------------------------------------------------------------------------- catalogue

_COLUMNS_SQL = (
    "SELECT table_schema, table_name, column_name, data_type, is_nullable, udt_name, "
    "(generation_expression IS NOT NULL) AS generated, "
    "CASE WHEN is_identity = 'YES' THEN identity_generation END AS identity "
    "FROM information_schema.columns WHERE table_schema = ANY(%s)"
)
_INDEXES_SQL = "SELECT schemaname, tablename, indexname FROM pg_indexes WHERE schemaname = ANY(%s)"


def read_catalogue(conn: Any, schemas: Sequence[str], *, with_indexes: bool = False, snapshot_form: bool = False) -> Catalogue:
    """The catalogue of *schemas* as the snapshot shape. ``snapshot_form`` keeps ``udt_name`` only for USER-DEFINED and ARRAY columns."""
    out: Catalogue = {}
    with conn.cursor() as cur:
        cur.execute(_COLUMNS_SQL, (list(schemas),))
        for schema, table, name, dtype, nullable, udt, generated, identity in cur.fetchall():
            col: Column = {"data_type": dtype, "is_nullable": nullable, "generated": bool(generated), "identity": identity}
            if not snapshot_form or dtype in ("USER-DEFINED", "ARRAY"):
                col["udt_name"] = udt
            out.setdefault(f"{schema}.{table}", {"columns": {}, "indexes": None})["columns"][name] = col
        if with_indexes:
            for table in out.values():
                table["indexes"] = []
            cur.execute(_INDEXES_SQL, (list(schemas),))
            for schema, table, index in cur.fetchall():
                out.setdefault(f"{schema}.{table}", {"columns": {}, "indexes": []})["indexes"].append(index)
            for table in out.values():
                table["indexes"].sort()
    return out


# ---------------------------------------------------------------------------------------------------------------- comparison


def _validated_allowances(label: str, allowances: "Mapping[str, str]") -> "dict[str, str]":
    for key, reason in allowances.items():
        if not isinstance(reason, str) or len(reason.strip()) < _MIN_REASON:
            raise SchemaParityError(
                f"{label}[{key!r}] needs a reason of at least {_MIN_REASON} characters saying WHY the difference is accepted; "
                "an allowance without one is a silent exemption"
            )
    return dict(allowances)


def _covers(allowances: "Mapping[str, str]", key: str, table: str) -> "Optional[str]":
    """The allowance key covering *key*: itself, or its whole table."""
    if key in allowances:
        return key
    return table if table in allowances else None


class _Comparison:
    """One comparison run: the findings, and which allowances were actually needed (the rest are stale)."""

    def __init__(self, path: str, extra_ok: "Mapping[str, str]", missing_ok: "Mapping[str, str]") -> None:
        self.path, self.extra_ok, self.missing_ok = path, extra_ok, missing_ok
        self.used_extra: set = set()
        self.used_missing: set = set()
        self.findings: list = []

    def add(self, rule: str, message: str) -> None:
        self.findings.append(Finding(path=self.path, line=1, rule=rule, message=message))

    def allowed(self, extra: bool, key: str, table: str) -> bool:
        allowances, used = (self.extra_ok, self.used_extra) if extra else (self.missing_ok, self.used_missing)
        hit = _covers(allowances, key, table)
        if hit is not None:
            used.add(hit)
        return hit is not None

    def column(self, key: str, w: Column, h: Column) -> None:
        if w["data_type"] != h["data_type"]:
            self.add("type-mismatch", f"{key}: {h['data_type']} -> {w['data_type']} (provisioned -> production): change the file to production's type")
        elif "udt_name" in w and w["udt_name"] != h.get("udt_name"):
            self.add("type-mismatch", f"{key}: {h.get('udt_name')} -> {w['udt_name']} (provisioned -> production udt_name)")
        if w["is_nullable"] != h["is_nullable"]:
            self.add("nullability-mismatch", f"{key}: nullable {h['is_nullable']} -> {w['is_nullable']} (provisioned -> production)")
        if "generated" in w and w["generated"] != h["generated"]:
            self.add("generated-mismatch", f"{key}: generated expression {h['generated']} -> {w['generated']} (provisioned -> production)")
        if "identity" in w and w["identity"] != h["identity"]:
            self.add("identity-mismatch", f"{key}: identity {h['identity']} -> {w['identity']} (provisioned -> production)")

    def columns(self, table: str, want: dict, have: dict) -> None:
        for name in sorted(want):
            key = f"{table}.{name}"
            if name in have:
                self.column(key, want[name], have[name])
            elif not self.allowed(False, key, table):
                self.add(
                    "column-missing",
                    f"{key} exists in production ({want[name]['data_type']}) and not in the provisioned table: "
                    "add it to the CREATE TABLE (a fresh provision breaks every query that reads it), or allow it in allowed_missing",
                )
        for name in sorted(set(have) - set(want)):
            if not self.allowed(True, f"{table}.{name}", table):
                self.add(
                    "extra-column",
                    f"{table}.{name} is created by the file and production does not have it: drop it from the file, or list it in "
                    "allowed_extra with the reason it is ahead of production",
                )

    def indexes(self, table: str, want: "Optional[list]", have: "Optional[list]") -> None:
        if want is None:
            self.add("snapshot-incomplete", f"{table} has no `indexes` in the snapshot; refresh it with indexes, or check_indexes=False")
            return
        for index in sorted(set(want) - set(have or [])):
            if not self.allowed(False, f"{table}#{index}", table):
                self.add("index-missing", f"index {index} on {table} exists in production and not in the provision: declare it in the file")
        for index in sorted(set(have or []) - set(want)):
            if not self.allowed(True, f"{table}#{index}", table):
                self.add("index-extra", f"index {index} on {table} is declared by the file and absent from production: allowed_extra or drop")

    def stale(self) -> None:
        for key in sorted(set(self.extra_ok) - self.used_extra):
            self.add("stale-allowance", f"allowed_extra[{key!r}] matches nothing the provision has beyond the snapshot: delete it ({self.extra_ok[key]})")
        for key in sorted(set(self.missing_ok) - self.used_missing):
            self.add("stale-allowance", f"allowed_missing[{key!r}] matches nothing production has that the provision lacks: delete it")


def compare_catalogues(
    snapshot: Catalogue,
    actual: Catalogue,
    *,
    path: str = "schema.sql",
    allowed_extra: "Mapping[str, str] | None" = None,
    allowed_missing: "Mapping[str, str] | None" = None,
    check_indexes: bool = False,
) -> "list[Finding]":
    """Findings for every way *actual* (the provisioned catalogue) departs from *snapshot* (production)."""
    run = _Comparison(path, _validated_allowances("allowed_extra", allowed_extra or {}), _validated_allowances("allowed_missing", allowed_missing or {}))
    for table in sorted(snapshot):
        want = snapshot[table]
        if table not in actual:
            if not run.allowed(False, table, table):
                run.add(
                    "table-missing",
                    f"production has {table} ({len(want['columns'])} columns) and a provision from the file does not create it: "
                    "add its CREATE TABLE to the file (a migration file may already hold it), or list it in allowed_missing with a reason",
                )
            continue
        run.columns(table, want["columns"], actual[table]["columns"])
        if check_indexes:
            run.indexes(table, want["indexes"], actual[table]["indexes"])
    for table in sorted(set(actual) - set(snapshot)):
        if not run.allowed(True, table, table):
            run.add(
                "extra-table",
                f"{table} is created by the file and absent from the snapshot: add it to production's snapshot if it is live there, "
                "else list it in allowed_extra with a reason",
            )
    run.stale()
    return run.findings


# ---------------------------------------------------------------------------------------------------------------- provision


def _first_line(exc: BaseException) -> str:
    """The first message line, with any line carrying a URL dropped (libpq quotes connection strings)."""
    lines = [ln for ln in str(exc).strip().splitlines() if "://" not in ln]
    return (lines[0] if lines else type(exc).__name__)[:200]


def _import_psycopg2() -> Any:
    try:
        import psycopg2
    except ImportError:
        raise SchemaParityError("psycopg2 is not installed: `pip install psycopg2-binary` (the gate talks to the private server with it)") from None
    return psycopg2


def _banner(reason: str, schema_sql: object) -> None:
    sys.stderr.write(
        "\n" + "!" * 78 + "\n"
        f"!! NOT CHECKED: {reason}\n"
        f"!! {schema_sql} was NOT provisioned and compared with the snapshot, so schema drift is unguarded here.\n" + "!" * 78 + "\n\n"
    )


def _try_statement(cur: Any, stmt: str, psycopg2: Any) -> "list[tuple[str, str]]":
    try:
        cur.execute(stmt)
    except psycopg2.Error as exc:
        return [(" ".join(stmt.split())[:80], _first_line(exc))]
    return []


def _apply(cur: Any, statements: "Sequence[str]", psycopg2: Any) -> "list[tuple[str, str]]":
    failures: list = []
    for stmt in statements:
        failures += _try_statement(cur, stmt, psycopg2)
    return failures


def _create_extensions(cur: Any, extensions: "Sequence[str]", psycopg2: Any) -> None:
    for ext in extensions:
        if _try_statement(cur, f'CREATE EXTENSION IF NOT EXISTS "{ext}"', psycopg2):
            raise SchemaParityError(
                f"extension {ext!r} is not available in this server's binaries; install them with it, "
                "or drop it from `extensions` if the file does not need it"
            )


@dataclass
class _Provisioned:
    first: dict
    first_failures: list
    second: "Optional[dict]" = None
    second_failures: list = field(default_factory=list)


def _provision(dsn: str, statements: "Sequence[str]", extensions: "Sequence[str]", wanted: "Sequence[str]", indexes: bool, twice: bool) -> _Provisioned:
    """Apply *statements* in a fresh database of the server at *dsn* (once, or twice), read its catalogue after each pass, drop the database."""
    psycopg2 = _import_psycopg2()
    name = f"schema_parity_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        conn = psycopg2.connect(dsn, dbname=name)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                _create_extensions(cur, extensions, psycopg2)
                run = _Provisioned(first={}, first_failures=_apply(cur, statements, psycopg2))
                run.first = read_catalogue(conn, wanted, with_indexes=indexes)
                if twice:
                    run.second_failures = _apply(cur, statements, psycopg2)
                    run.second = read_catalogue(conn, wanted, with_indexes=indexes)
            return run
        finally:
            conn.close()
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        admin.close()


def _require_local_server(dsn: str) -> None:
    """*server_dsn* gets a database created and dropped on it, so it must be a throwaway server on this machine, never a shared one."""
    from psycopg2.extensions import parse_dsn

    try:
        host = parse_dsn(dsn).get("host", "")
    except Exception:
        raise SchemaParityError("server_dsn is not a parsable libpq DSN") from None
    if host not in ("127.0.0.1", "localhost", "::1") and not host.startswith(("/", "\\")):
        raise SchemaParityError(
            "server_dsn must name a local throwaway server (host 127.0.0.1, localhost or a socket directory): the gate creates and drops a "
            "database on it. Leave server_dsn unset to let the gate start its own."
        )


def _idempotence_findings(label: str, run: _Provisioned) -> "list[Finding]":
    if run.second is None:
        return []
    known = {head for head, _ in run.first_failures}
    out = [
        Finding(label, 1, "not-idempotent", f"statement fails when the file is applied a second time: {head} ({why}); use IF NOT EXISTS")
        for head, why in run.second_failures
        if head not in known
    ]
    if run.second != run.first:
        changed = sorted(k for k in set(run.first) | set(run.second) if run.first.get(k) != run.second.get(k))
        out.append(
            Finding(label, 1, "not-idempotent", f"applying the file a second time changed the catalogue of {changed}: make every statement a no-op on re-apply")
        )
    return out


def find_schema_snapshot_problems(
    schema_sql: "str | Path",
    snapshot_json: "str | Path",
    *,
    extensions: Sequence[str] = (),
    schemas: "Sequence[str] | None" = None,
    allowed_extra: "Mapping[str, str] | None" = None,
    allowed_missing: "Mapping[str, str] | None" = None,
    check_indexes: bool = False,
    run_twice: bool = True,
    pg_bin: "Path | None" = None,
    server_dsn: "str | None" = None,
    min_tables: int = 1,
) -> ParityResult:
    """Provision *schema_sql* on a private server and compare with *snapshot_json*; ``checked=False`` when there is no server.

    *schemas* defaults to the schemas of the snapshot's tables. ``allowed_extra`` / ``allowed_missing`` map ``schema.table``
    (the whole table), ``schema.table.column`` or ``schema.table#index`` to a mandatory reason. A statement that fails to apply is an
    ``apply-failed`` finding and the comparison still runs, so one run names every drift. *min_tables* is the floor on the snapshot's
    size, so an emptied snapshot cannot pass. *server_dsn* reuses a throwaway server the caller already runs (a pre-push hook's, a test
    fixture's: a server start costs seconds to minutes) by creating and dropping one private database on it; it must be local.
    """
    sql_path = Path(schema_sql)
    try:
        text = sql_path.read_text(encoding="utf-8-sig")
    except OSError:
        raise SchemaParityError(f"schema file {sql_path} is unreadable; pass the path of the SQL file that provisions the database") from None
    snapshot = load_snapshot(snapshot_json)
    if len(snapshot) < min_tables:
        raise SchemaParityError(f"snapshot {snapshot_json} lists {len(snapshot)} tables, fewer than min_tables={min_tables}: it is empty or truncated")
    statements = split_statements(text)
    if not statements:
        raise SchemaParityError(f"schema file {sql_path} holds no SQL statement: nothing to provision")
    wanted = sorted(set(schemas) if schemas is not None else {t.split(".")[0] for t in snapshot})
    if server_dsn is not None:
        _require_local_server(server_dsn)
    bin_dir = pg_bin or find_pg_bin()
    if bin_dir is None and server_dsn is None:
        reason = "no Postgres binaries (set PG_BIN, run `python -m py_ci_shared.embedded_postgres fetch`, or `pip install pgserver`)"
        _banner(reason, sql_path)
        return ParityResult(checked=False, reason=reason)
    label = str(sql_path)
    if server_dsn is not None:
        run = _provision(server_dsn, statements, extensions, wanted, check_indexes, run_twice)
    else:
        assert bin_dir is not None
        with embedded_postgres(bin_dir) as dsn:
            run = _provision(dsn, statements, extensions, wanted, check_indexes, run_twice)
    findings = [
        Finding(label, 1, "apply-failed", f"statement failed on an empty database: {head} ({why}); fix the file so it provisions cleanly")
        for head, why in run.first_failures
    ]
    findings += _idempotence_findings(label, run)
    findings += compare_catalogues(snapshot, run.first, path=label, allowed_extra=allowed_extra, allowed_missing=allowed_missing, check_indexes=check_indexes)
    return ParityResult(checked=True, findings=findings)


def assert_schema_matches_snapshot(
    schema_sql: "str | Path",
    snapshot_json: "str | Path",
    *,
    extensions: Sequence[str] = (),
    schemas: "Sequence[str] | None" = None,
    allowed_extra: "Mapping[str, str] | None" = None,
    allowed_missing: "Mapping[str, str] | None" = None,
    check_indexes: bool = False,
    run_twice: bool = True,
    require_server: bool = False,
    pg_bin: "Path | None" = None,
    server_dsn: "str | None" = None,
    min_tables: int = 1,
) -> ParityResult:
    """Raise ``AssertionError`` listing every finding; return the result otherwise (read ``.checked``: False means NOT compared).

    With no Postgres binaries the call prints a loud banner and returns ``checked=False``; with *require_server* it raises instead,
    for CI where a missing server must fail the build.
    """
    result = find_schema_snapshot_problems(
        schema_sql,
        snapshot_json,
        extensions=extensions,
        schemas=schemas,
        allowed_extra=allowed_extra,
        allowed_missing=allowed_missing,
        check_indexes=check_indexes,
        run_twice=run_twice,
        pg_bin=pg_bin,
        server_dsn=server_dsn,
        min_tables=min_tables,
    )
    if not result.checked and require_server:
        raise AssertionError(f"schema_snapshot_parity NOT CHECKED and require_server is set: {result.reason}")
    if result.findings:
        raise AssertionError(
            f"{schema_sql} does not provision what {snapshot_json} says production has ({len(result.findings)} findings):\n  "
            + "\n  ".join(f.render() for f in result.findings)
        )
    return result


# ---------------------------------------------------------------------------------------------------------------- refresh


def connect_read_only(dsn: str) -> Any:
    """A psycopg2 connection whose session cannot write: ``default_transaction_read_only=on`` at connect and ``set_session(readonly=True)``."""
    psycopg2 = _import_psycopg2()
    try:
        conn = psycopg2.connect(dsn, options="-c default_transaction_read_only=on", connect_timeout=15, **KEEPALIVES)
    except Exception as exc:
        # libpq quotes host, user and the DSN in its message, so only the class and SQLSTATE leave this function.
        raise SchemaParityError(
            f"could not connect with the DSN in the environment variable ({type(exc).__name__}, SQLSTATE {getattr(exc, 'pgcode', None)})"
        ) from None
    conn.set_session(readonly=True, autocommit=False)
    return conn


def refresh_snapshot(
    out_path: "str | Path",
    *,
    dsn_env: str,
    schemas: Sequence[str],
    source: str,
    with_indexes: bool = True,
    environ: "Mapping[str, str] | None" = None,
) -> Catalogue:
    """Write the snapshot of *schemas* read from the database named by environment variable *dsn_env* (read-only session)."""
    if not schemas:
        raise SchemaParityError("refresh needs at least one schema to read")
    if not source.strip():
        raise SchemaParityError("refresh needs a `source` note (where, when, which role) so a reader can judge the snapshot's age")
    dsn = (environ if environ is not None else os.environ).get(dsn_env, "")
    if not dsn.strip():
        raise SchemaParityError(f"environment variable {dsn_env} is not set; it must hold a READ-ONLY DSN for the database to snapshot")
    conn = connect_read_only(dsn)
    try:
        catalogue = read_catalogue(conn, schemas, with_indexes=with_indexes, snapshot_form=True)
    finally:
        conn.close()
    if not catalogue:
        raise SchemaParityError(f"no table found in schemas {list(schemas)}: the role cannot see them, or the names are wrong; nothing was written")
    body = {
        "schema_version": SCHEMA_VERSION,
        "_source": source,
        "tables": {t: {"columns": v["columns"], **({"indexes": v["indexes"]} if with_indexes else {})} for t, v in sorted(catalogue.items())},
    }
    atomic_write_text(out_path, dump_json(body))
    return catalogue


# ---------------------------------------------------------------------------------------------------------------- CLI


def _allowances(items: "Sequence[str]", flag: str) -> "dict[str, str]":
    out = {}
    for item in items:
        key, sep, reason = item.partition("=")
        if not sep or not key or not reason.strip():
            raise SchemaParityError(f"{flag} wants KEY=REASON (a reason is mandatory), got {item!r}")
        out[key] = reason
    return out


def _env_dsn(name: "Optional[str]") -> "Optional[str]":
    if name is None:
        return None
    value = os.environ.get(name, "").strip()
    if not value:
        raise SchemaParityError(f"environment variable {name} is not set; it must hold the DSN of a local throwaway server")
    return value


def main(argv: "Sequence[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.schema_snapshot_parity")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="provision SQL_FILE on a private server and compare with SNAPSHOT")
    c.add_argument("schema_sql")
    c.add_argument("snapshot")
    c.add_argument("--extension", action="append", default=[])
    c.add_argument("--schema", action="append", default=None)
    c.add_argument("--allow-extra", action="append", default=[], metavar="KEY=REASON")
    c.add_argument("--allow-missing", action="append", default=[], metavar="KEY=REASON")
    c.add_argument("--check-indexes", action="store_true")
    c.add_argument("--once", action="store_true", help="skip the second (idempotence) apply")
    c.add_argument("--pg-bin", default=None)
    c.add_argument("--server-dsn-env", default=None, help="environment variable holding the DSN of a LOCAL throwaway server to reuse instead of starting one")
    c.add_argument("--missing-exit", type=int, default=0, help="exit code when there is no server (default 0, printed loudly; use 3 in CI)")
    r = sub.add_parser("refresh", help="WRITE the snapshot from a live database through a read-only DSN in an environment variable")
    r.add_argument("out")
    r.add_argument("--dsn-env", required=True)
    r.add_argument("--schema", action="append", required=True)
    r.add_argument("--source", required=True, help="where/when/which role the read came from (stored in the file)")
    r.add_argument("--no-indexes", action="store_true")
    ns = parser.parse_args(argv)
    try:
        if ns.cmd == "refresh":
            cat = refresh_snapshot(ns.out, dsn_env=ns.dsn_env, schemas=ns.schema, source=ns.source, with_indexes=not ns.no_indexes)
            sys.stdout.write(f"wrote {ns.out}: {len(cat)} tables, {sum(len(t['columns']) for t in cat.values())} columns\n")
            return 0
        result = find_schema_snapshot_problems(
            ns.schema_sql,
            ns.snapshot,
            extensions=ns.extension,
            schemas=ns.schema,
            allowed_extra=_allowances(ns.allow_extra, "--allow-extra"),
            allowed_missing=_allowances(ns.allow_missing, "--allow-missing"),
            check_indexes=ns.check_indexes,
            run_twice=not ns.once,
            pg_bin=Path(ns.pg_bin) if ns.pg_bin else None,
            server_dsn=_env_dsn(ns.server_dsn_env),
        )
    except SchemaParityError as exc:
        sys.stderr.write(f"schema_snapshot_parity: {exc}\n")
        return 2
    if not result.checked:
        sys.stdout.write(f"NOT CHECKED: {result.reason}\n")
        return int(ns.missing_exit)
    for f in result.findings:
        sys.stdout.write(f.render() + "\n")
    sys.stdout.write(f"{len(result.findings)} findings\n")
    return 1 if result.findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
