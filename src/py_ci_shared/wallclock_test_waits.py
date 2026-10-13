"""A test that waits on the wall clock is slow and flaky. Opt-in ratchet over the test files of a repo.

Flagged, one finding per line: Dart ``Future.delayed(const Duration(milliseconds`` and Python ``time.sleep(``. A test
that needs time to pass should inject the clock (a fake clock, ``fake_async``, a monkeypatched ``time.sleep``) or wait on
the condition it is really after. A real sleep makes the suite slower on every run and passes or fails with machine load.

Test files are the ones under a ``test``/``tests`` directory and the files named ``test_*.py``, ``*_test.py``
and ``*_test.dart``. Keys are ``<repo-relative path>: <normalized line>``, so re-indenting or moving a line does not
churn the baseline; two identical lines in one file share one key.

Enable it explicitly (NOT in the default set)::

    [tool.py_ci_shared]
    enable = ["wallclock_test_waits"]

Record the existing waits in ``tool/meta/baselines/wallclock_test_waits.json`` as ``{"accepted": {key: note}}``, one note
per entry saying why the wait is kept. Only a NEW wall-clock wait in a test fails. A ``*_dir`` keyword is resolved
against the repo root.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ._text_ratchet import DEFAULT_BASELINE_DIR, enforce_findings, is_test_path, normalize_line, text_files

__all__ = ["WAIT_PATTERNS", "find_wallclock_test_waits", "assert_no_new_wallclock_test_waits"]

WAIT_PATTERNS = (
    re.compile(r"Future\.delayed\(\s*const Duration\(milliseconds"),  # Dart
    re.compile(r"\btime\.sleep\("),  # Python
)


def find_wallclock_test_waits(root: Union[str, Path]) -> dict[str, str]:
    """``{key: description}`` for each line of a test file under *root* that waits on the wall clock."""
    found: dict[str, str] = {}
    for file in text_files(root, ("*.py", "*.dart")):
        if not is_test_path(file.rel):
            continue
        for number, line in enumerate(file.text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in WAIT_PATTERNS):
                found[f"{file.rel}: {normalize_line(line)}"] = f"line {number} waits on the wall clock inside a test"
    return found


def assert_no_new_wallclock_test_waits(repo_root: Union[str, Path], *, baseline_dir: Optional[str] = None) -> None:
    """Fail on a wall-clock wait in a test that the baseline does not accept (``tool/meta/baselines/wallclock_test_waits.json``)."""
    enforce_findings(
        "wallclock_test_waits",
        find_wallclock_test_waits(repo_root),
        repo_root=repo_root,
        baseline_dir=baseline_dir or DEFAULT_BASELINE_DIR,
        label="wallclock_test_waits",
        guidance="A test that sleeps on the wall clock is slow and flaky. Inject the clock or wait on the condition instead, or accept it with a note.",
    )
