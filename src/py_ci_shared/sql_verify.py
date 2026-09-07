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

NO DEPENDENCY IS ADDED. ``psycopg2`` is imported lazily, inside the call, so this module can live in
a dependency-free package and only a project that actually runs it needs the driver installed.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Iterable, Sequence

#: Distinct from 1 (a statement failed) so a CI job can tell "not verified" from "verified clean".
#: ``--skip-without-db`` maps it to 0, which is what a pre-push hook wants.
SKIPPED = 2

#: Read when the caller names none. The first non-empty one wins.
DEFAULT_ENV_NAMES = ("DATABASE_URL",)


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
            return dsn.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
    return None


def check(conn, label: str, sql: str, params=None) -> bool:
    """Execute one statement, print a labelled OK/FAIL line, and never raise.

    Every failure is reported rather than the first one aborting the run: the value of a sweep is
    the full list. The connection is rolled back after a failure so the remaining checks still run
    -- without that, one bad statement would fail every later one with "transaction is aborted" and
    the report would blame the wrong statements.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params if params is not None else {})
            rows = cur.fetchall()
        conn.commit()
        head = tuple(str(v)[:40] for v in rows[0]) if rows else None
        print(f"OK   {label}: {len(rows)} row(s), first={head}")
        return True
    except Exception as exc:  # noqa: BLE001 -- reporting every failure is the point
        conn.rollback()
        print(f"FAIL {label}: {type(exc).__name__}: {str(exc).strip()[:300]}")
        return False


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
    except Exception as exc:  # noqa: BLE001 -- reporting every failure is the point
        print(f"FAIL {label}: {type(exc).__name__}: {str(exc).strip()[:300]}")
        return False
    if getattr(rows, "failed", False):
        print(f"FAIL {label}: the loader returned a flagged-failed result -- see the log above")
        return False
    first = None
    if rows:
        row = rows[0]
        values = row.values() if hasattr(row, "values") else row
        first = tuple(str(v)[:40] for v in values)
    print(f"OK   {label}: {len(rows)} row(s), first={first}")
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
    push through.
    """
    args = list(sys.argv if argv is None else argv)
    lenient = "--skip-without-db" in args

    import psycopg2  # noqa: PLC0415 -- lazy, so this package stays dependency-free

    if dsn is None:
        dsn = dsn_from_env(env_names)
    if dsn is None:
        print(f"SKIPPED: none of {', '.join(env_names)} is set -- nothing to verify the SQL against.")
        return 0 if lenient else SKIPPED
    try:
        probe = psycopg2.connect(dsn, connect_timeout=connect_timeout)
        probe.close()
    except Exception as exc:  # noqa: BLE001 -- an unreachable server is a skip, not a failed statement
        print(f"SKIPPED: database unreachable ({type(exc).__name__}: {str(exc).strip()[:120]}).")
        return 0 if lenient else SKIPPED

    runners = [checks] if callable(checks) else list(checks)
    ok = True
    with psycopg2.connect(dsn) as conn:
        for runner in runners:
            ok &= bool(runner(conn))

    print("\nALL OK" if ok else "\nSOMETHING FAILED")
    return 0 if ok else 1
