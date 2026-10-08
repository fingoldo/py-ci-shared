"""Plain SQL files that change a live table bound their lock wait and add constraints and indexes without a blocking scan.

For hand-applied ``.sql`` migrations and runbooks (``psql -f``), not Alembic (``alembic_concurrently`` covers that). Each rule is a way a
statement that looks harmless queues every writer on a production table behind it:

1. ``ddl-lock-timeout``: a file with ``ALTER TABLE`` needs ``SET [LOCAL] lock_timeout`` before the first one. Even adding a column takes
   ACCESS EXCLUSIVE; waiting behind one long reader makes every later statement on the table, every INSERT included, wait behind this one.
   ``lock_timeout = 0`` and ``DEFAULT`` do not bound anything and do not count.
2. ``ddl-constraint-not-valid``: ``ADD CONSTRAINT ... CHECK`` / ``FOREIGN KEY`` (named or not) without ``NOT VALID`` validates under
   ACCESS EXCLUSIVE for the whole table scan. The two-step ``NOT VALID`` then ``VALIDATE CONSTRAINT`` does not block DML. A paste-ready
   command in a comment header (``--   ALTER TABLE ...``, two or more spaces after ``--``) counts as a command, because it is the first thing
   an operator copies.
3. ``ddl-index-concurrently``: ``CREATE [UNIQUE] INDEX`` without ``CONCURRENTLY`` holds a SHARE lock (no writes) for the whole build, unless the
   table is created by the same file. ``CREATE``/``DROP INDEX`` / ``REINDEX ... CONCURRENTLY`` between ``BEGIN`` and ``COMMIT`` is a finding
   too: PostgreSQL refuses it at run time ("cannot run inside a transaction block").
4. ``ddl-table-rewrite`` (``check_rewrites``, on by default): ``ADD COLUMN`` with a volatile ``DEFAULT`` (``random()``, ``gen_random_uuid()``,
   ``clock_timestamp()``, ``nextval()`` ...), a ``serial`` type or ``GENERATED ... STORED``, and ``ALTER COLUMN ... TYPE``, rewrite the table
   under ACCESS EXCLUSIVE (a binary-coercible type change does not, which only the author knows). A file carrying
   ``-- rewrite-ok: <reason>`` accepts them. An unknown function in a ``DEFAULT`` is not flagged: the scan cannot know its volatility
   (pass ``volatile_functions`` to name more).

*tiny_tables* maps ``table -> reason`` for tables small enough that a validating scan, a plain index build or a rewrite is instant (a bare name
matches that table in any schema); it never excuses rule 1 or the BEGIN rule. A blank reason is a ``ValueError``.

A small SQL lexer, no parser: comments, string literals and dollar-quoted bodies are blanked before any matching, so a ``CONCURRENTLY`` in a
comment or an ``ALTER TABLE`` inside a ``DO $$`` body counts for nothing, and psql ``\\`` meta-commands are ignored. A file that cannot be
read is an ``UnparsedFilesError`` (``allow_unparsed`` to skip), and fewer than *min_files* files is an ``EmptyScanError``. The gate is
opt-in: nothing runs it until a consumer calls it. A baseline lets an existing repo adopt it over the files it already has.

Usage::

    from py_ci_shared.ddl_lock_safety import assert_ddl_files_lock_safe

    def test_sql_files_are_lock_safe():
        assert_ddl_files_lock_safe(REPO_ROOT, sql_dirs=["sql"], tiny_tables={"kv_checkpoint": "a few dozen watermark rows"})
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import NamedTuple, Optional, Union

from ._core import Baseline, EmptyScanError, Finding, SourceReadError, UnparsedFilesError, iter_files, read_source, relative_posix

__all__ = ["RULE", "assert_ddl_files_lock_safe", "find_ddl_lock_findings"]

RULE = "ddl-lock-safety"
RULE_LOCK_TIMEOUT = "ddl-lock-timeout"
RULE_CONSTRAINT = "ddl-constraint-not-valid"
RULE_INDEX = "ddl-index-concurrently"
RULE_REWRITE = "ddl-table-rewrite"

#: Functions whose result differs per row or per call, so a column ``DEFAULT`` using one cannot be stored as catalogue metadata.
DEFAULT_VOLATILE_FUNCTIONS = (
    "random",
    "gen_random_uuid",
    "uuid_generate_v1",
    "uuid_generate_v1mc",
    "uuid_generate_v4",
    "clock_timestamp",
    "timeofday",
    "nextval",
    "setseed",
    "gen_random_bytes",
    "txid_current",
    "pg_sleep",
)

_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
_QUALIFIED = rf"(?:{_IDENT}\s*\.\s*)?{_IDENT}"
_PASTEABLE = re.compile(r"(?m)^--(?=[ \t]{2,}\S)")
_REWRITE_OK = re.compile(r"(?im)^[ \t]*--[ \t]*rewrite-ok:[ \t]*(\S.*)$")
_DOLLAR = re.compile(r"\$(?:[A-Za-z_]\w*)?\$")

_SET_LOCK_TIMEOUT = re.compile(r"(?is)^SET\s+(?:(?:LOCAL|SESSION)\s+)?lock_timeout\s*(?:=|\bTO\b)")
_ZERO_VALUE = re.compile(r"(?is)^\s*(?:DEFAULT\b|'?\s*0+\s*(?:ms|s|min|h|d)?\s*'?\s*$)")
_ALTER_TABLE = re.compile(rf"(?is)^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?P<table>{_QUALIFIED})(?P<rest>.*)$")
_CREATE_TABLE = re.compile(
    rf"(?is)^CREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:(?:TEMP|TEMPORARY|UNLOGGED)\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>{_QUALIFIED})"
)
_CREATE_INDEX = re.compile(
    rf"(?is)^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?P<conc>CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(?:(?!ON\b)(?P<name>{_IDENT})\s+)?ON\s+(?:ONLY\s+)?(?P<table>{_QUALIFIED})"
)
_CONCURRENT_OTHER = re.compile(r"(?is)^(?:DROP\s+INDEX|REINDEX\b.*?)\s+(?:\(.*?\)\s+)?(?:\w+\s+)?CONCURRENTLY\b")
_BEGIN = re.compile(r"(?is)^(?:BEGIN|START\s+TRANSACTION)\b")
_END = re.compile(r"(?is)^(?:COMMIT|END|ROLLBACK|ABORT)\b")
_ADD_CONSTRAINT = re.compile(rf"(?is)^ADD\s+(?:CONSTRAINT\s+(?P<name>{_IDENT})\s+)?(?P<kind>CHECK|FOREIGN\s+KEY)\b")
_ADD_COLUMN = re.compile(rf"(?is)^ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(?P<col>{_IDENT})\s+(?P<rest>.+)$")
_ALTER_TYPE = re.compile(rf"(?is)^ALTER\s+(?:COLUMN\s+)?(?P<col>{_IDENT})\s+(?:SET\s+DATA\s+)?TYPE\b")
_SERIAL = re.compile(r"(?i)^(?:small|big)?serial\b")
_STORED = re.compile(r"(?is)\bGENERATED\s+ALWAYS\s+AS\b.*\bSTORED\b")
_DEFAULT = re.compile(r"(?is)\bDEFAULT\b(?P<expr>.*?)(?=\b(?:NOT\s+NULL|NULL|CHECK|REFERENCES|CONSTRAINT|PRIMARY|UNIQUE|GENERATED|COLLATE)\b|$)")
_NOT_VALID = re.compile(r"(?i)\bNOT\s+VALID\b")


class _Stmt(NamedTuple):
    start: int
    flat: str


def _end_block_comment(src: str, i: int) -> int:
    depth, j = 1, i + 2
    while j < len(src) and depth:
        if src.startswith("/*", j):
            depth, j = depth + 1, j + 2
        elif src.startswith("*/", j):
            depth, j = depth - 1, j + 2
        else:
            j += 1
    return j


def _end_quoted(src: str, i: int, quote: str, escapes: bool = False) -> int:
    """Index of the closing *quote* of the literal opened at *i* (``len(src)`` when unterminated); a doubled quote is an escaped one."""
    j = i + 1
    while j < len(src):
        if escapes and src[j] == "\\":
            j += 2
        elif src[j] == quote and src.startswith(quote * 2, j):
            j += 2
        elif src[j] == quote:
            return j
        else:
            j += 1
    return len(src)


def _mask(src: str) -> str:
    """*src* with comments, string-literal contents and dollar-quoted bodies blanked; same length, newlines kept (so offsets map to lines)."""
    out = list(src)
    n, i = len(src), 0

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if src[k] != "\n":
                out[k] = " "

    while i < n:
        ch = src[i]
        if src.startswith("--", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif src.startswith("/*", i):
            j = _end_block_comment(src, i)
            blank(i, j)
            i = j
        elif ch == "'":
            escapes = i > 0 and src[i - 1] in "eE" and (i < 2 or not (src[i - 2].isalnum() or src[i - 2] == "_"))
            j = _end_quoted(src, i, "'", escapes)
            blank(i + 1, j)
            i = j + 1
        elif ch == '"':
            i = _end_quoted(src, i, '"') + 1
        elif ch == "$" and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")) and (m := _DOLLAR.match(src, i)):
            end = src.find(m.group(0), m.end())
            blank(m.end(), n if end < 0 else end)
            i = n if end < 0 else end + len(m.group(0))
        else:
            i += 1
    return "".join(out)


def _blank_meta_commands(masked: str) -> str:
    return re.sub(r"(?m)^[ \t]*\\.*$", lambda m: " " * len(m.group(0)), masked)


def _statements(masked: str) -> list[_Stmt]:
    out: list[_Stmt] = []
    pos = 0
    for piece in masked.split(";"):
        flat = " ".join(piece.split())
        if flat:
            out.append(_Stmt(pos + len(piece) - len(piece.lstrip()), flat))
        pos += len(piece) + 1
    return out


def _norm(name: str) -> str:
    """``"Sch"."T"`` / ``sch . t`` -> ``sch.t`` (unquoted identifiers lowercased, quoted ones kept as written)."""
    parts = [p.strip() for p in re.split(r"\.(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", name)]
    return ".".join(p[1:-1].replace('""', '"') if p.startswith('"') else p.lower() for p in parts)


def _top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _with_pasteable_commands(src: str) -> str:
    """*src* with each ``--   command`` comment line turned into code and the last line of every such block closed with ``;``, so a
    header that omits its terminator does not fuse with the live statement below it. Line numbers are unchanged."""
    lines = src.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        if _PASTEABLE.match(line):
            line = "  " + line[2:]
            if i + 1 >= len(lines) or not _PASTEABLE.match(lines[i + 1]):
                body = line.rstrip("\r")
                line = body + ";" + line[len(body) :]
        out.append(line)
    return "\n".join(out)


def _where(src: str, offset: int) -> tuple[int, int]:
    """``(line, column)`` of *offset*: statements of the two views are ordered by this, since only line numbers survive the pasteable rewrite."""
    return _line(src, offset), offset - (src.rfind("\n", 0, offset) + 1)


def _line(src: str, offset: int) -> int:
    return src.count("\n", 0, offset) + 1


def _tiny(table: str, tiny: Mapping[str, str]) -> bool:
    bare = table.rsplit(".", 1)[-1]
    return table in tiny or bare in tiny


def _bounded(stmt: _Stmt, cmd_src: str) -> bool:
    """A ``SET [LOCAL] lock_timeout`` that sets a real limit. The value is read from the unmasked text, where the quoted ``'5s'`` still exists."""
    if not _SET_LOCK_TIMEOUT.match(stmt.flat):
        return False
    segment = cmd_src[stmt.start :].split(";", 1)[0]
    value = re.split(r"(?i)\block_timeout\s*(?:=|\bTO\b)", segment, maxsplit=1)[-1]
    return not _ZERO_VALUE.match(value)


def _lock_timeout_findings(rel: str, src: str, live: list[_Stmt], cmd: list[_Stmt], cmd_src: str) -> list[Finding]:
    first_alter = next((s for s in live if _ALTER_TABLE.match(s.flat)), None)
    if first_alter is None:
        return []
    alter_at = _where(src, first_alter.start)
    if any(_where(cmd_src, s.start) < alter_at and _bounded(s, cmd_src) for s in cmd):
        return []
    table = _norm(_ALTER_TABLE.match(first_alter.flat).group("table"))  # type: ignore[union-attr]
    return [
        Finding(
            rel,
            _line(src, first_alter.start),
            RULE_LOCK_TIMEOUT,
            f"ALTER TABLE {table} has no SET lock_timeout before it, so an ACCESS EXCLUSIVE wait queues every writer on the table. "
            "Add `SET lock_timeout = '5s';` (or SET LOCAL inside the transaction) above the first ALTER TABLE; 0 and DEFAULT do not bound the wait",
        )
    ]


def _constraint_findings(rel: str, cmd: list[_Stmt], cmd_src: str, created: set[str], tiny: Mapping[str, str]) -> list[Finding]:
    out: list[Finding] = []
    for s in cmd:
        am = _ALTER_TABLE.match(s.flat)
        table = _norm(am.group("table")) if am else ""
        if not am or _tiny(table, tiny) or table in created:
            continue
        for clause in _top_level_commas(am.group("rest")):
            cm = _ADD_CONSTRAINT.match(clause)
            if cm and not _NOT_VALID.search(clause):
                label = " ".join(cm.group("kind").upper().split()) + (f" {_norm(cm.group('name'))}" if cm.group("name") else "")
                out.append(
                    Finding(
                        rel,
                        _line(cmd_src, s.start),
                        RULE_CONSTRAINT,
                        f"ADD CONSTRAINT {label} on {table} without NOT VALID scans the whole table under ACCESS EXCLUSIVE. "
                        f"Add it NOT VALID, then run `ALTER TABLE {table} VALIDATE CONSTRAINT ...;` as a second statement, or list {table} in tiny_tables with a reason",
                    )
                )
    return out


def _begin_block_findings(rel: str, src: str, live: list[_Stmt]) -> list[Finding]:
    out: list[Finding] = []
    in_txn = False
    for s in live:
        if _BEGIN.match(s.flat):
            in_txn = True
        elif _END.match(s.flat):
            in_txn = False
        elif in_txn and (_CONCURRENT_OTHER.match(s.flat) or ((m := _CREATE_INDEX.match(s.flat)) and m.group("conc"))):
            head = " ".join(s.flat.split()[:4]).upper()
            out.append(
                Finding(
                    rel,
                    _line(src, s.start),
                    RULE_INDEX,
                    f"`{head} ...` runs CONCURRENTLY inside an explicit BEGIN block, which PostgreSQL refuses with 'cannot run inside a transaction block'. "
                    "Move it out of the BEGIN ... COMMIT block (one CONCURRENTLY statement per file)",
                )
            )
    return out


def _plain_index_findings(rel: str, cmd: list[_Stmt], cmd_src: str, created: set[str], tiny: Mapping[str, str]) -> list[Finding]:
    out: list[Finding] = []
    for s in cmd:
        im = _CREATE_INDEX.match(s.flat)
        if not im or im.group("conc"):
            continue
        table = _norm(im.group("table"))
        if _tiny(table, tiny) or table in created:
            continue
        name = _norm(im.group("name")) if im.group("name") else "(unnamed)"
        out.append(
            Finding(
                rel,
                _line(cmd_src, s.start),
                RULE_INDEX,
                f"CREATE INDEX {name} on {table} without CONCURRENTLY blocks every write for the whole build. "
                f"Write CREATE INDEX CONCURRENTLY (outside any BEGIN block), or list {table} in tiny_tables with a reason",
            )
        )
    return out


def _file_findings(rel: str, src: str, tiny: Mapping[str, str], rewrites: bool, volatile: tuple[str, ...]) -> list[Finding]:
    live_masked = _blank_meta_commands(_mask(src))
    cmd_src = _with_pasteable_commands(src)
    cmd_masked = _blank_meta_commands(_mask(cmd_src))
    live, cmd = _statements(live_masked), _statements(cmd_masked)
    created = {_norm(m.group("table")) for s in cmd if (m := _CREATE_TABLE.match(s.flat))}
    out = _lock_timeout_findings(rel, src, live, cmd, cmd_src)
    out += _constraint_findings(rel, cmd, cmd_src, created, tiny)
    out += _begin_block_findings(rel, src, live)
    out += _plain_index_findings(rel, cmd, cmd_src, created, tiny)
    if rewrites and not _REWRITE_OK.search(src):
        out += _rewrite_findings(rel, src, live, tiny, volatile)
    return out


def _rewrite_reason(clause: str, call: "Optional[re.Pattern[str]]") -> Optional[tuple[str, str]]:
    """``(column, why)`` when one ALTER TABLE clause rewrites the table."""
    if tm := _ALTER_TYPE.match(clause):
        return _norm(tm.group("col")), "ALTER COLUMN ... TYPE (rewrites unless the change is binary-coercible)"
    cm = _ADD_COLUMN.match(clause)
    if not cm:
        return None
    col, rest = _norm(cm.group("col")), cm.group("rest")
    dm = _DEFAULT.search(rest)
    vm = call.search(dm.group("expr")) if call and dm else None
    if _SERIAL.match(rest):
        return col, "a serial column (its nextval() default)"
    if vm:
        return col, f"a volatile DEFAULT ({vm.group(1).lower()}())"
    if _STORED.search(rest):
        return col, "a GENERATED ... STORED column"
    return None


def _rewrite_findings(rel: str, src: str, live: list[_Stmt], tiny: Mapping[str, str], volatile: tuple[str, ...]) -> list[Finding]:
    call = re.compile(r"(?i)\b(" + "|".join(re.escape(f) for f in volatile) + r")\s*\(") if volatile else None
    out: list[Finding] = []
    for s in live:
        am = _ALTER_TABLE.match(s.flat)
        table = _norm(am.group("table")) if am else ""
        if not am or _tiny(table, tiny):
            continue
        for clause in _top_level_commas(am.group("rest")):
            found = _rewrite_reason(clause, call)
            if found:
                out.append(
                    Finding(
                        rel,
                        _line(src, s.start),
                        RULE_REWRITE,
                        f"ALTER TABLE {table} column {found[0]}: {found[1]} rewrites the whole table under ACCESS EXCLUSIVE. "
                        "Add the column without it and backfill in batches, or state why it is safe with a `-- rewrite-ok: <reason>` comment in the file",
                    )
                )
    return out


def find_ddl_lock_findings(
    root: Union[str, Path],
    *,
    sql_dirs: Iterable[Union[str, Path]],
    tiny_tables: Optional[Mapping[str, str]] = None,
    check_rewrites: bool = True,
    volatile_functions: Iterable[str] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding in the ``*.sql`` files under each of *sql_dirs* (relative to *root*), sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files were read and ``UnparsedFilesError`` for a file that cannot be read
    (unless *allow_unparsed*); a missing directory raises ``CorpusError``.
    """
    tiny = {_norm(k): v for k, v in (tiny_tables or {}).items()}
    blank = sorted(k for k, v in (tiny_tables or {}).items() if not str(v).strip())
    if blank:
        raise ValueError(f"tiny_tables entries need a reason saying why the table is small: {blank}")
    volatile = (*DEFAULT_VOLATILE_FUNCTIONS, *volatile_functions)
    base = Path(root)
    files: list[Path] = []
    for d in sql_dirs:
        files.extend(iter_files(base / d, ("*.sql",), use_git=use_git))
    out: list[Finding] = []
    problems: list[str] = []
    read = 0
    for path in sorted(set(files)):
        rel = relative_posix(path, base)
        try:
            src = read_source(path)
        except SourceReadError as exc:
            problems.append(f"{rel}:{exc.line or 1}: {exc.kind}: {exc.message}")
            continue
        read += 1
        out.extend(_file_findings(rel, src, tiny, check_rewrites, volatile))
    if read < min_files:
        extra = f" ({len(problems)} more could not be read)" if problems else ""
        raise EmptyScanError(
            f"only {read} .sql file(s) read under {list(map(str, sql_dirs))}{extra}; expected at least {min_files}. Point sql_dirs at the directories holding the DDL files"
        )
    if problems and not allow_unparsed:
        raise UnparsedFilesError(
            f"{len(problems)} SQL file(s) could not be read, so no gate can vouch for them. Re-save them as UTF-8:\n  " + "\n  ".join(problems)
        )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_ddl_files_lock_safe(
    root: Union[str, Path],
    *,
    sql_dirs: Iterable[Union[str, Path]],
    tiny_tables: Optional[Mapping[str, str]] = None,
    check_rewrites: bool = True,
    volatile_functions: Iterable[str] = (),
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    sql_dirs = list(sql_dirs)
    found = find_ddl_lock_findings(
        root,
        sql_dirs=sql_dirs,
        tiny_tables=tiny_tables,
        check_rewrites=check_rewrites,
        volatile_functions=volatile_functions,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "Fix each statement as its message says: bound the lock wait, add constraints NOT VALID, build indexes CONCURRENTLY, avoid table rewrites"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="ddl_lock_safety", refresh_command="PY_CI_SHARED_REFRESH=ddl_lock_safety")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} ddl-lock-safety finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
