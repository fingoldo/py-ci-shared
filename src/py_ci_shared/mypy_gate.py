"""Run mypy as a gate that requires COMPLETION, not merely a zero exit code.

``mypy`` prints ``Success: no issues found in N source files`` when, and only when, it
type-checked every file it was asked to and found nothing. Any other outcome -- real
errors, an ``INTERNAL ERROR`` inside a third-party stub, a crash partway through the
traversal -- produces no such line. A consuming repo hit exactly that: ``mypy src/pkg``
aborted inside a vendored ``transformers`` stub, so the errors it had managed to emit
before dying depended on file-traversal order, while the hook that called the gate
"declared clean" only ever looked at the exit code.

This wrapper asserts the terminator. It also enforces a minimum file count, because a
narrowed invocation (a mistyped path, an over-eager ``exclude``, a stale ``files=`` regex)
can produce a perfectly honest ``Success: no issues found in 3 source files`` while
skipping the other two hundred -- a green gate that checked almost nothing.

Usage, as a drop-in replacement for ``python -m mypy <args>``::

    python -m py_ci_shared.mypy_gate src/pkg
    python -m py_ci_shared.mypy_gate --min-files 200 src/pkg

Exit codes: 0 only on a completed, clean run; 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys

SUCCESS_RE = re.compile(r"Success: no issues found in (\d+) source files?")

#: mypy's OTHER terminator. A completed run that found problems ends with
#: ``Found 12 errors in 3 files (checked 165 source files)``, and that line is just as much proof of
#: completion as the success line is -- the file count is even in the same place.
#:
#: Without it this module reported "mypy did not print its completion line; a run that does not
#: finish cannot certify anything" for a run that finished perfectly well and found 366 errors. That
#: is the exact distinction the module exists to draw, drawn wrongly, and it would send someone
#: hunting a broken invocation instead of reading their type errors.
FOUND_RE = re.compile(r"Found (\d+) errors? in \d+ files? \(checked (\d+) source files?\)")


def check_mypy_output(output: str, returncode: int, min_files: int = 0) -> str | None:
    """Return a failure message, or ``None`` if the run completed cleanly.

    Args:
        output: mypy's combined stdout+stderr.
        returncode: mypy's exit status.
        min_files: the smallest file count a legitimate run of this scope can report --
            a lower count means the invocation silently narrowed. 0 disables the check.
    """
    match = SUCCESS_RE.search(output)
    found = FOUND_RE.search(output)
    if match is None and found is None:
        if "INTERNAL ERROR" in output:
            return "mypy aborted with an INTERNAL ERROR: it did not finish, so its findings are a function of traversal order, not of the code."
        return f"mypy did not print either completion line (exit {returncode}); a run that does not finish cannot certify anything."

    # The scope check applies to BOTH terminators. A run that narrowed to three files and then
    # found two errors in them is exactly as uninformative as one that narrowed and found none.
    checked = int(match.group(1)) if match else int(found.group(2))
    if min_files and checked < min_files:
        return f"mypy completed but checked only {checked} source files, below the declared minimum of {min_files} -- the invocation's scope has silently narrowed."

    if found is not None:
        return f"mypy completed over {checked} source files and found {found.group(1)} error(s). The run is sound; the findings are real."
    return None


def main(argv: list[str] | None = None) -> int:
    """Run mypy with the given arguments and gate on its completion line."""
    args = list(sys.argv[1:] if argv is None else argv)
    min_files = 0
    if "--min-files" in args:
        index = args.index("--min-files")
        min_files = int(args[index + 1])
        del args[index : index + 2]

    result = subprocess.run([sys.executable, "-m", "mypy", *args], capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    sys.stdout.write(output)
    problem = check_mypy_output(output, result.returncode, min_files=min_files)
    if problem is not None:
        sys.stdout.write(f"\nmypy_gate: FAIL -- {problem}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
