"""Run a command against a throwaway local Postgres, and find the main checkout's ``.env`` from a worktree.

Two gaps production_scrapers hit on 2026-09-11/12, both of the "a gate that silently checks nothing" kind:

* **The integration tier never ran.** It needs a real server; the fixture used Docker; the development
  machine had none and the CI runner was unavailable. Three integration files turned out never to have
  executed -- rows the schema rejects, a query that was empty by construction, a timing that could not
  hold. ``run`` starts a private server from plain Postgres binaries (``PG_BIN``, or the ``pgserver``
  wheel's, which ship for Windows, Linux and macOS), sets one DSN variable for the command, and stops
  the server afterwards. With no binaries it says so LOUDLY and exits with ``--missing-exit`` (0 by
  default for a pre-push hook) rather than passing in silence.
* **Live-database hooks passed silently in a worktree.** ``.env`` is untracked, so a git worktree has
  none; every ``--skip-without-db`` hook found no DSN and passed. ``main_checkout_file`` locates the
  same relative path in the main worktree via ``git rev-parse --git-common-dir``.

CLI::

    python -m py_ci_shared.embedded_postgres run --env PS_INTEGRATION_PG_DSN -- python -m pytest -m integration tests/integration
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

_EXE = ".exe" if os.name == "nt" else ""


def find_pg_bin(explicit: "str | None" = None) -> "Path | None":
    """A directory holding ``initdb`` and ``pg_ctl``: *explicit*, ``$PG_BIN``, an installed ``pgserver``, or PATH."""
    candidates = [Path(value) for value in (explicit, os.environ.get("PG_BIN")) if value]
    try:
        import pgserver  # type: ignore[import-not-found]

        candidates.append(Path(pgserver.__file__).resolve().parent / "pginstall" / "bin")
    except Exception:
        pass
    which = shutil.which("pg_ctl")
    if which:
        candidates.append(Path(which).parent)
    return next((c for c in candidates if (c / f"initdb{_EXE}").is_file() and (c / f"pg_ctl{_EXE}").is_file()), None)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _quiet(cmd: list[str], *, check: bool) -> None:
    """Run with DEVNULL on every stream: the server outlives pg_ctl, and an inherited pipe would hold the call open."""
    subprocess.run(cmd, check=check, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@contextlib.contextmanager
def embedded_postgres(bin_dir: Path, *, port: "int | None" = None, user: str = "postgres") -> Iterator[str]:
    """A running server in a temporary data directory, as a libpq DSN; stopped and deleted on exit."""
    port = port or free_port()
    data = Path(tempfile.mkdtemp(prefix="pg-embedded-"))
    pg_ctl = str(bin_dir / f"pg_ctl{_EXE}")
    try:
        _quiet([str(bin_dir / f"initdb{_EXE}"), "-D", str(data / "db"), "-U", user, "-A", "trust", "-E", "UTF8", "--no-locale"], check=True)
        _quiet([pg_ctl, "-D", str(data / "db"), "-o", f"-p {port} -c listen_addresses=127.0.0.1", "-l", str(data / "server.log"), "-w", "start"], check=True)
        try:
            yield f"host=127.0.0.1 port={port} user={user} dbname=postgres"
        finally:
            _quiet([pg_ctl, "-D", str(data / "db"), "-m", "fast", "-w", "stop"], check=False)
    finally:
        shutil.rmtree(data, ignore_errors=True)


def main_checkout_file(relative: "str | Path", *, cwd: "Path | None" = None) -> "Path | None":
    """*relative* (to the repository root) in the MAIN worktree, when *cwd* is inside a linked worktree."""
    cwd = cwd or Path.cwd()
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    main_root = Path(common).parent
    candidate = main_root / relative
    return candidate if candidate.is_file() else None


def run(argv: Sequence[str], *, env_var: str, bin_dir: "Path | None", missing_exit: int = 0) -> int:
    if bin_dir is None:
        sys.stderr.write(
            "\n" + "!" * 78 + "\n"
            f"!! SKIPPED: no Postgres binaries (set PG_BIN, or `pip install pgserver`). The command\n"
            f"!! below did NOT run, so nothing it guards was checked:\n!!   {' '.join(argv)}\n" + "!" * 78 + "\n\n"
        )
        return missing_exit
    with embedded_postgres(bin_dir) as dsn:
        return subprocess.run(list(argv), env={**os.environ, env_var: dsn}, check=False).returncode


def main(args: "Sequence[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.embedded_postgres")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a command with a throwaway server's DSN in an environment variable")
    r.add_argument("--env", required=True, help="the environment variable to set to the DSN")
    r.add_argument("--pg-bin", default=None)
    r.add_argument("--missing-exit", type=int, default=0, help="exit code when no binaries are found (default 0, printed loudly)")
    r.add_argument("command", nargs=argparse.REMAINDER)
    ns = parser.parse_args(args)
    command = [c for c in ns.command if c != "--"]
    if not command:
        parser.error("no command given after --")
    return run(command, env_var=ns.env, bin_dir=find_pg_bin(ns.pg_bin), missing_exit=ns.missing_exit)


if __name__ == "__main__":
    raise SystemExit(main())
