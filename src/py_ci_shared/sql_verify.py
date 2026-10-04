"""Shared harness: execute every SQL statement a project ships against a real PostgreSQL server.

WHY THIS EXISTS
---------------
A unit suite drives its loaders through a FAKE CURSOR. That is the right shape for unit tests --
fast, no database, and it proves the Python around a statement: the parameters, the row mapping,
the caching, the error handling. It proves NOTHING about whether PostgreSQL will accept the SQL,
because a fake cursor accepts any string at all.

That gap is not hypothetical, and it is not a syntax-only risk:

* An interpolated projection carried a trailing comma from the line it replaced, producing
  ``v.verdict AS verdict,`` immediately before ``FROM``. The application's single most important
  query was invalid; ~900 tests were green (upwork dashboard, round 14).
* A statement selected ``WHERE job_uid = %s`` from a table whose key column is ``uid``. Because it
  ran inside the transaction that recorded a proposal, PostgreSQL aborted the whole transaction and
  the write that mattered failed with "current transaction is aborted". 1,840 tests were green; the
  operator found it on the first live send (upwork dashboard, 2026-09-05).

Both classes are caught by one thing only: sending the statement to a server.

WHAT THIS MODULE IS
-------------------
The ~60 lines that are the same in every project -- resolving a DSN, running one labelled check,
running a loader instead of a copy of its SQL, and a runner that knows the difference between "the
statements passed", "a statement failed" and "there was no database to ask". The inventory of
statements is NOT here and should not be: it is the consuming project's, it is most of the code,
and it changes with that project's schema.

A consumer is then about ten lines::

    from py_ci_shared.sql_verify import check, run_checks

    def _checks(conn):
        ok = check(conn, "listing", LISTING_SQL, PARAMS)
        ok &= check(conn, "counts", COUNTS_SQL)
        return ok

    if __name__ == "__main__":
        raise SystemExit(run_checks(_checks, env_names=("DATABASE_JOBSTRACKER_URL", "DATABASE_URL")))

WHERE TO RUN IT: pre-PUSH, not pre-commit. It needs a reachable database and costs seconds, and a
checkout without one (CI, a fresh clone, a colleague's machine) must still be able to push -- which
is what ``--skip-without-db`` is for. What such a checkout must NOT do is push while believing the
statements were checked, hence the distinct `SKIPPED` exit code when the flag is absent.

COMPOSED STATEMENTS. ``FragmentMatrix`` / ``assert_fragment_matrix`` (bottom of this module) build every fragment
of a dict into every template it is formatted into, without a database, and check each result parses and names only
table aliases in scope where the fragment lands. A live run covers the combinations its inventory builds; the matrix
covers all of them. It needs sqlglot (the ``sql`` extra), imported inside the call.

NO DEPENDENCY IS ADDED. ``psycopg2`` is imported lazily, inside the call, so this module can live in
a dependency-free package and only a project that actually runs it needs the driver installed.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable, Optional

#: Distinct from 1 (a statement failed) so a CI job can tell "not verified" from "verified clean".
#: ``--skip-without-db`` maps it to 0, which is what a pre-push hook wants.
SKIPPED = 2

#: Read when the caller names none. The first non-empty one wins.
DEFAULT_ENV_NAMES = ("DATABASE_URL",)

#: ``postgresql+asyncpg://``, ``postgres+psycopg2://`` ...: the SQLAlchemy driver suffix psycopg2 cannot parse.
_DRIVER_SUFFIX = re.compile(r"^(postgres(?:ql)?)\+[A-Za-z0-9_]+://")

#: Connection errors that mean the server answered and refused THIS configuration: a wrong password, role or
#: database is a broken setup to fix, not a missing database to skip.
_CONFIG_ERROR_MARKERS = (
    "authentication failed",
    "password",
    "does not exist",
    "no pg_hba.conf entry",
    "invalid dsn",
    "invalid connection option",
    "invalid uri",
    "sslmode",
)


def dsn_from_env(env_names: Sequence[str] = DEFAULT_ENV_NAMES) -> str | None:
    """The database to verify against, or None when there is not one configured.

    None rather than a KeyError: "no database" is a state the caller decides how to treat, and for
    a pre-push hook the answer is to skip, not to crash.

    A SQLAlchemy-shaped DSN is normalised -- ``postgresql+asyncpg://`` is what an application config
    carries and what psycopg2 refuses to parse, and reusing the app's own variable is the point.
    """
    for name in env_names:
        raw = os.environ.get(name) or ""
        if raw.strip():
            dsn = raw.strip()
            return _DRIVER_SUFFIX.sub(r"\1://", dsn)
    return None


def check(conn, label: str, sql: str, params=None) -> bool:
    """Execute one statement, print a labelled OK/FAIL line, and never raise.

    Every failure is reported rather than the first one aborting the run: the value of a sweep is
    the full list. The connection is rolled back after EVERY statement, passed or failed: a verifier
    only asks the server whether it accepts the SQL, so an ``UPDATE``/``INSERT`` it runs must never
    persist, and after a failure the rollback also keeps one bad statement from failing every later
    one with "transaction is aborted".

    A statement that returns no result set (``INSERT`` without ``RETURNING``, DDL) is a pass with 0
    rows, not a "no results to fetch" failure. Without *params* the statement is executed with no
    parameter mapping at all, so a literal ``%`` in it is sent as written.
    """
    try:
        try:
            with conn.cursor() as cur:
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                rows = cur.fetchall() if getattr(cur, "description", None) is not None else []
        finally:
            conn.rollback()
        head = tuple(str(v)[:40] for v in rows[0]) if rows else None
        print(f"OK   {label}: {len(rows)} row(s), first={head}")
        return True
    except Exception as exc:
        print(f"FAIL {label}: {type(exc).__name__}: {str(exc).strip()[:300]}")
        return False


def _is_unreachable(exc: BaseException, psycopg2: object) -> bool:
    """True only for "there is no server to ask": a network/timeout error, not a refused configuration."""
    operational = getattr(psycopg2, "OperationalError", None)
    kinds: tuple[type, ...] = (OSError,) + ((operational,) if isinstance(operational, type) else ())
    if not isinstance(exc, kinds):
        return False
    text = str(exc).lower()
    return not any(marker in text for marker in _CONFIG_ERROR_MARKERS)


def check_loader(conn, label: str, call: Callable[[], object]) -> bool:
    """Run a LOADER against the live server instead of a copy of the SQL it issues.

    A `check()` entry holds a duplicate of a statement that lives in the application, and duplicates
    drift: one such copy still read ``CURRENT_DATE`` after the module had stopped saying it, so the
    verifier was verifying a statement the application no longer ran. Calling the loader cannot
    drift.

    A loader usually borrows its own connection from the application's pool, so *conn* is unused and
    is taken only to keep call sites uniform. A result object that carries a ``failed`` flag instead
    of raising is checked explicitly -- a loader that swallowed its error would otherwise report OK.
    """
    del conn  # the loader borrows its own connection from the configured pool
    try:
        rows = call()
    except Exception as exc:
        print(f"FAIL {label}: {type(exc).__name__}: {str(exc).strip()[:300]}")
        return False
    if getattr(rows, "failed", False):
        print(f"FAIL {label}: the loader returned a flagged-failed result -- see the log above")
        return False
    # A loader's return type is `object` by contract -- it may hand back a list of tuples, of dicts,
    # or a result object carrying `failed`. Narrowed here rather than promised in the signature: a
    # wider annotation would only move the guess to the call sites.
    items = list(rows) if isinstance(rows, (list, tuple)) else []
    first = None
    if items:
        row = items[0]
        values = list(row.values()) if isinstance(row, dict) else list(row)
        first = tuple(str(v)[:40] for v in values)
    print(f"OK   {label}: {len(items)} row(s), first={first}")
    return True


def run_checks(
    checks: Callable[[object], bool] | Iterable[Callable[[object], bool]],
    *,
    env_names: Sequence[str] = DEFAULT_ENV_NAMES,
    dsn: str | None = None,
    argv: Sequence[str] | None = None,
    connect_timeout: int = 5,
) -> int:
    """Open one connection, run *checks* against it, and return a process exit code.

    *checks* is one callable taking the connection and returning True when everything it ran
    passed, or an iterable of such callables (all are run, and the result is their conjunction, so
    a failure never hides the checks after it).

    Exit codes: 0 all passed, 1 something failed, `SKIPPED` there was no database -- unless
    ``--skip-without-db`` is in *argv*, which maps the last case to 0 so a pre-push hook lets the
    push through. Only an unreachable server counts as "no database": a server that refuses the
    credentials, or a DSN it cannot parse, is a broken configuration and exits 1 either way.
    """
    args = list(sys.argv if argv is None else argv)
    lenient = "--skip-without-db" in args

    if dsn is None:
        dsn = dsn_from_env(env_names)
    if dsn is None:
        print(f"SKIPPED: none of {', '.join(env_names)} is set -- nothing to verify the SQL against.")
        return 0 if lenient else SKIPPED
    dsn = _DRIVER_SUFFIX.sub(r"\1://", dsn)

    import psycopg2

    try:
        probe = psycopg2.connect(dsn, connect_timeout=connect_timeout)
        probe.close()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc).strip()[:120]}"
        if not _is_unreachable(exc, psycopg2):
            print(f"FAIL: the database refused the connection ({detail}); fix the configuration.")
            return 1
        print(f"SKIPPED: database unreachable ({detail}).")
        return 0 if lenient else SKIPPED

    runners = [checks] if callable(checks) else list(checks)
    ok = True
    # psycopg2's `with conn:` ends the transaction but leaves the connection open; closing() closes it.
    with contextlib.closing(psycopg2.connect(dsn, connect_timeout=connect_timeout)) as conn:
        try:
            for runner in runners:
                ok &= bool(runner(conn))
        finally:
            conn.rollback()

    print("\nALL OK" if ok else "\nSOMETHING FAILED")
    return 0 if ok else 1


# Fragment matrix: every fragment in every template it is formatted into.
#
# A statement composed with ``str.format`` (an ORDER BY fragment chosen by key, a wrapper around a full statement) is
# only verified in the combinations some test happens to build. The upwork dashboard's two-phase listing wrapped the
# full statement as ``SELECT ... FROM (<full>) core ORDER BY {outer_order_by}``; the "hire" ordering named the
# ``cd``/``cs`` joins of the INNER statement, so every narrow fetch under it failed on the server with ``missing
# FROM-clause entry for table "cd"`` while each ordering, alone, was valid and tested. The matrix builds EVERY key in
# EVERY template, parses each statement, and checks that every table alias a column names is in scope where it lands.

#: Qualifiers that are not FROM-clause aliases: ``excluded`` in ``ON CONFLICT DO UPDATE``, trigger ``new``/``old``.
DEFAULT_EXTRA_ALIASES: frozenset[str] = frozenset({"excluded", "new", "old"})

_PYFORMAT_PARAM = re.compile(r"%\((\w+)\)s|%s")
_DOTTED_REF = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")
_PLACEHOLDER = re.compile(r"\{[A-Za-z_]\w*\}")
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


@dataclasses.dataclass(frozen=True)
class FragmentMatrix:
    """One composition to verify exhaustively.

    Declarative form: *template* has a ``{slot}`` placeholder and each dict in *fragments* maps a key to a fragment
    formatted into it; *fill* gives the template's other placeholders. Every (fragment dict, key) pair is one
    statement. Use it where every fragment really may land in that template.

    Builder form: *build* takes a key and returns the statement the application would send, so the matrix follows
    the application's own choice of fragment per key (``_WRAPPED.get(key, _OUTER[key])``). *keys* lists the keys; by
    default the union of the keys of *fragments*.

    A string that looks like a dotted name (``"top_jobs_sql._FULL_SQL"``) is resolved by import, so the matrix reads
    the module's current value rather than a copy of it.
    """

    label: str
    template: Optional[str] = None
    slot: Optional[str] = None
    fragments: Sequence[Any] = ()
    fill: Mapping[str, str] = dataclasses.field(default_factory=dict)
    build: Optional[Callable[[str], str]] = None
    keys: Optional[Sequence[str]] = None
    extra_aliases: frozenset[str] = DEFAULT_EXTRA_ALIASES


def _resolved(value: Any) -> Any:
    if isinstance(value, str) and _DOTTED_REF.match(value):
        from .constant_relations import resolve_reference

        return resolve_reference(value)
    return value


def _fragment_dicts(matrix: FragmentMatrix) -> list[tuple[str, Mapping[str, str]]]:
    out = []
    for i, ref in enumerate(matrix.fragments):
        value = _resolved(ref)
        if not isinstance(value, Mapping):
            raise TypeError(f"fragments[{i}] ({ref!r}) is a {type(value).__name__}, not a dict of fragments")
        out.append((ref if isinstance(ref, str) else f"fragments[{i}]", value))
    return out


def _built(label: str, build: Callable[[str], str], key: str) -> tuple[str, Optional[str]]:
    try:
        return f"{label}[{key}]", build(key)
    except Exception as exc:
        return f"{label}[{key}]: the builder raised {type(exc).__name__}: {exc}", None


def matrix_statements(matrix: FragmentMatrix) -> list[tuple[str, Optional[str]]]:
    """``(label, statement)`` for every combination *matrix* declares. A combination that cannot be built has statement
    None and the reason in its label, which `find_fragment_matrix_problems` reports."""
    dicts = _fragment_dicts(matrix)
    out: list[tuple[str, Optional[str]]] = []
    if matrix.build is not None:
        keys = list(matrix.keys) if matrix.keys is not None else sorted({k for _, d in dicts for k in d})
        return [_built(matrix.label, matrix.build, key) for key in keys]
    template = _resolved(matrix.template)
    if not isinstance(template, str) or not matrix.slot:
        raise TypeError("the declarative form needs a template string and a slot name")
    fill = {name: _resolved(value) for name, value in matrix.fill.items()}
    for name, fragments in dicts:
        for key in sorted(fragments):
            label = f"{matrix.label}[{name}:{key}]"
            try:
                out.append((label, template.format(**{**fill, matrix.slot: fragments[key]})))
            except (KeyError, IndexError, ValueError) as exc:
                out.append((f"{label}: formatting the template failed ({type(exc).__name__}: {exc}); add it to `fill`", None))
    return out


def _parse_ready(sql: str, paramstyle: str) -> str:
    """The statement as the server would see it, each driver placeholder replaced by ``NULL``."""
    if paramstyle == "pyformat":
        return _PYFORMAT_PARAM.sub("NULL", sql).replace("%%", "%")
    return sql


def _prepare_ready(sql: str, paramstyle: str) -> str:
    """Driver placeholders as ``$n`` (one per distinct name), for ``PREPARE``."""
    if paramstyle != "pyformat":
        return sql
    numbers: dict[str, int] = {}

    def _number(m: "re.Match[str]") -> str:
        name = m.group(1) or f"#{len(numbers)}"
        numbers.setdefault(name, len(numbers) + 1)
        return f"${numbers[name]}"

    return _PYFORMAT_PARAM.sub(_number, sql).replace("%%", "%")


def alias_scope_problems(sql: str, *, extra_aliases: frozenset[str] = DEFAULT_EXTRA_ALIASES) -> list[str]:
    """Each qualified column whose qualifier is not a source of its scope or of an enclosing one (sqlglot, postgres
    dialect). Raises ``sqlglot.errors.SqlglotError`` on a statement it cannot parse."""
    import sqlglot
    from sqlglot.optimizer.scope import traverse_scope

    # One line per out-of-scope qualifier and visible set; a fragment that names `cs` ten times is one defect.
    found: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    seen: set[int] = set()
    for tree in sqlglot.parse(sql, read="postgres"):
        if tree is None:
            continue
        for scope in traverse_scope(tree):
            visible: set[str] = set()
            walker: Any = scope
            while walker is not None:
                visible |= {name.lower() for name in walker.sources}
                walker = walker.parent
            for column in scope.columns:
                qualifier = column.table
                if not qualifier or id(column) in seen:
                    continue
                seen.add(id(column))
                if qualifier.lower() not in visible and qualifier.lower() not in extra_aliases:
                    shown = tuple(sorted(v for v in visible if v))
                    found.setdefault((qualifier, shown), []).append(column.sql(dialect="postgres"))
    return [
        f"`{uses[0]}` names `{qualifier}`{f' ({len(uses)} references)' if len(uses) > 1 else ''}, but only {list(shown)} are in scope there"
        for (qualifier, shown), uses in found.items()
    ]


def _pglast_problem(sql: str) -> Optional[str]:
    try:
        from pglast import parse_sql  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        parse_sql(sql)
    except Exception as exc:
        return f"PostgreSQL's own parser (pglast) rejects it: {str(exc).strip()[:200]}"
    return None


def _prepare_problem(conn: Any, sql: str) -> Optional[str]:
    try:
        with conn.cursor() as cur:
            cur.execute(f"PREPARE py_ci_shared_fragment_matrix AS {sql}")
            cur.execute("DEALLOCATE py_ci_shared_fragment_matrix")
    except Exception as exc:
        return f"PREPARE failed: {type(exc).__name__}: {str(exc).strip()[:200]}"
    finally:
        conn.rollback()
    return None


def find_fragment_matrix_problems(
    matrices: Iterable[FragmentMatrix],
    *,
    conn: Any = None,
    paramstyle: str = "pyformat",
    min_statements: int = 1,
) -> list[str]:
    """``label: problem`` for every built statement that does not parse or names an alias out of scope.

    With pglast installed the statement is also parsed by PostgreSQL's own grammar; with *conn* (a DB-API
    connection, for example to ``embedded_postgres`` with the schema loaded) it is also ``PREPARE``d, which checks
    every name against the catalogue without running anything. *paramstyle* ``"pyformat"`` reads ``%(name)s``,
    ``%s`` and ``%%`` as the driver does; anything else leaves the text as it is. Fewer than *min_statements*
    statements in total is a finding: a matrix that builds nothing verifies nothing.
    """
    try:
        import sqlglot.errors
    except ImportError as exc:
        raise ImportError("the fragment matrix needs sqlglot: pip install 'py-ci-shared[sql]'") from exc
    problems: list[str] = []
    built = 0
    for matrix in matrices:
        try:
            statements = matrix_statements(matrix)
        except Exception as exc:
            problems.append(f"{matrix.label}: cannot build the matrix: {type(exc).__name__}: {exc}")
            continue
        for label, sql in statements:
            if sql is None:
                problems.append(label)
                continue
            built += 1
            text = _parse_ready(sql, paramstyle)
            # sqlglot parses a leftover `{slot}` without complaint, so a template filled with another raw template
            # would pass; a placeholder outside string literals is the composition being incomplete.
            leftover = sorted(set(_PLACEHOLDER.findall(_STRING_LITERAL.sub("''", text))))
            if leftover:
                problems.append(f"{label}: unfilled placeholder(s) {leftover}: the statement is a template, not SQL")
                continue
            try:
                problems.extend(f"{label}: {p}" for p in alias_scope_problems(text, extra_aliases=matrix.extra_aliases))
            except sqlglot.errors.SqlglotError as exc:
                problems.append(f"{label}: does not parse: {str(exc).strip()[:200]}")
                continue
            extras = [_pglast_problem(text)]
            if conn is not None:
                extras.append(_prepare_problem(conn, _prepare_ready(sql, paramstyle)))
            problems.extend(f"{label}: {extra}" for extra in extras if extra)
    if built < min_statements:
        problems.append(f"the fragment matrix built {built} statement(s), expected at least {min_statements}")
    return problems


def assert_fragment_matrix(matrices: Iterable[FragmentMatrix], **kwargs: Any) -> None:
    """Fail on any composed statement that does not parse or names an alias out of scope; see
    `find_fragment_matrix_problems`."""
    import pytest

    problems = find_fragment_matrix_problems(matrices, **kwargs)
    if problems:
        pytest.fail(f"{len(problems)} composed statement(s) are broken in some combination:\n  " + "\n  ".join(problems))
