"""Run pytest the way a developer machine needs it: one process, unbuffered, ``-s``, ``--durations``, no coverage, the whole output in a file.

Each default has a failure behind it:

* ``PYTHONUNBUFFERED=1`` and ``-s``: without ``-s`` pytest captures stdout and prints the tracebacks only when the session ends, so a run killed by a timeout leaves nothing to read.
* ``--durations=200``: the slow tests are the first thing to look at in a long run; the default prints ten.
* the output goes to a file, never to a pipe: ``| tail`` and ``| grep`` hold every line until the process exits, so the file of a live run stays empty. Read the file after the run.
* no ``-q``: it buffers the progress lines until the end.
* ``--no-cov`` (when ``pytest-cov`` is installed): on Windows the coverage teardown can die with ``PermissionError`` on a locked ``.coverage`` file after the tests passed, which ``-x``
  then turns into a failed run.
* ``-n 0`` (when ``pytest-xdist`` is installed): parallel workers each import the project and exhaust the RAM of a small machine; a project that really wants workers passes its own ``-n``.
* ``-p no:cacheprovider``: no ``.pytest_cache`` writes into a checkout that other runs read.

Usage::

    py-ci-shared tool pytest_run OUTFILE tests/test_a.py tests/test_b.py -k name
    python -m py_ci_shared.pytest_run OUTFILE tests/test_a.py

The file gets pytest's output and a last line ``exit=<code>``; the command prints the exit code, the pytest summary line and the file's path once the run is over. A pytest option of
your own (``-n 3``, ``--durations=20``, ``-x``) replaces the matching default. ``-q`` / ``--quiet`` is refused.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Optional, Union

__all__ = ["DEFAULT_DURATIONS", "build_command", "main", "run_pytest", "summary_line"]

DEFAULT_DURATIONS = 200
_QUIET = ("-q", "-qq", "-qqq", "--quiet")
_WORKERS = ("-n", "--numprocesses", "--dist")
_SUMMARY = re.compile(r"^=+ .*\b(passed|failed|error|errors|skipped|xfailed|xpassed|deselected)\b.* =+$")


def _has_option(args: Sequence[str], names: Sequence[str]) -> bool:
    """Whether *args* carries any of *names* as ``-n 3`` / ``-n3`` / ``--numprocesses=3``."""
    for arg in args:
        for name in names:
            if arg == name or arg.startswith(name + "=") or (len(name) == 2 and arg.startswith(name) and arg != name and not arg.startswith("--")):
                return True
    return False


def build_command(
    args: Sequence[str],
    *,
    durations: int = DEFAULT_DURATIONS,
    python: Optional[str] = None,
    installed: Callable[[str], bool] = lambda module: importlib.util.find_spec(module) is not None,
) -> list[str]:
    """The pytest command line: *args* (paths and the caller's own options) after the mandatory defaults.

    ``--no-cov`` and ``-n 0`` are added only when ``pytest_cov`` / ``xdist`` are importable (*installed*), since pytest rejects an option whose plugin is missing; a caller's own ``-n`` /
    ``--durations`` wins. Raises ``ValueError`` for ``-q`` / ``--quiet``.
    """
    given = list(args)
    if any(a in _QUIET or re.fullmatch(r"-q+", a) for a in given):
        raise ValueError("-q / --quiet buffers pytest's progress until the end of the run; leave it out")
    command = [python or sys.executable, "-m", "pytest", *given, "-s"]
    if not any(a.startswith("--durations") for a in given):
        command.append(f"--durations={int(durations)}")
    if installed("pytest_cov") and "--no-cov" not in given and not any(a.startswith("--cov") for a in given):
        command.append("--no-cov")
    if installed("xdist") and not _has_option(given, _WORKERS):
        command.extend(["-n", "0"])
    if "no:cacheprovider" not in " ".join(given):
        command.extend(["-p", "no:cacheprovider"])
    return command


def summary_line(out_file: Union[str, Path]) -> str:
    """pytest's last summary line (``== 3 passed, 1 skipped in 2.1s ==``) of the finished run in *out_file*, or an empty string."""
    try:
        lines = Path(out_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if _SUMMARY.match(line.strip()):
            return line.strip()
    return ""


def run_pytest(
    out_file: Union[str, Path],
    args: Sequence[str],
    *,
    durations: int = DEFAULT_DURATIONS,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[dict] = None,
) -> int:
    """Run pytest on *args* with the mandatory flags, all of its output in *out_file*, and return its exit code (also written as the file's last line, ``exit=<code>``)."""
    command = build_command(args, durations=durations)
    child_env = dict(os.environ if env is None else env)
    child_env["PYTHONUNBUFFERED"] = "1"
    path = Path(out_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        code = subprocess.run(
            command, stdout=handle, stderr=subprocess.STDOUT, env=child_env, cwd=None if cwd is None else str(cwd), check=False
        ).returncode  # nosec B603 - the interpreter running this
        handle.write(f"\nexit={code}\n")
    return code


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``pytest_run OUTFILE [pytest args...]``: exit code 0 when the tests passed, pytest's code otherwise, 2 for a usage error."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write((__doc__ or "").split("Usage::")[-1].split("The file gets")[0].strip() + "\n")
        return 0 if args else 2
    out_file, pytest_args = args[0], args[1:]
    try:
        code = run_pytest(out_file, pytest_args)
    except ValueError as exc:
        sys.stderr.write(f"pytest_run: {exc}\n")
        return 2
    sys.stdout.write(f"exit={code}\n{summary_line(out_file)}\noutput: {out_file}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
