"""A ``tool/check*`` guard that no test names is a guard nothing has ever run. Opt-in ratchet over a repo's guard scripts.

A guard is a file ``tool/check*.py`` or ``tool/check*.sh`` (directly under ``tool/``). It is covered when some test
file, under ``test/`` or ``tests/`` (``*.py`` or ``*.dart``), names it by its file name: a subprocess call, a path, a
string. A guard that no test names has no fixture that proves it still fails on bad input, so its own breakage goes
unseen until the day it is needed. The match is on the whole file name (``check_x.py`` is not matched by
``check_x.py.bak`` or ``my_check_x.py``); a name written only in a comment still counts.

Keys are the guard's repo-relative path. Enable it explicitly (NOT in the default set)::

    [tool.py_ci_shared]
    enable = ["guard_without_fixture"]

Record the existing unnamed guards in ``tool/meta/baselines/guard_without_fixture.json`` as ``{"accepted": {path: note}}``,
each note saying why the guard has no test yet. Only a NEW unnamed guard fails. A ``*_dir`` keyword is resolved
against the repo root.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ._text_ratchet import DEFAULT_BASELINE_DIR, enforce_findings, text_files

__all__ = ["GUARD_GLOBS", "find_guards_without_fixture", "assert_every_guard_named_by_a_test"]

GUARD_GLOBS = ("check*.py", "check*.sh")
_TEST_ROOTS = ("test", "tests")


def _is_direct_tool_guard(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) == 2 and parts[0] == "tool"


def find_guards_without_fixture(root: Union[str, Path]) -> dict[str, str]:
    """``{path: description}`` for each ``tool/check*`` guard that no test file under ``test/`` or ``tests/`` names."""
    guards = [f for f in text_files(root, GUARD_GLOBS, under=("tool",)) if _is_direct_tool_guard(f.rel)]
    tests = text_files(root, ("*.py", "*.dart"), under=_TEST_ROOTS)
    found: dict[str, str] = {}
    for guard in guards:
        name = guard.rel.rsplit("/", 1)[1]
        named = re.compile(rf"(?<![\w.-]){re.escape(name)}(?![\w-])")
        if not any(named.search(test.text) for test in tests):
            found[guard.rel] = f"no test file under test/ or tests/ names {name}"
    return found


def assert_every_guard_named_by_a_test(repo_root: Union[str, Path], *, baseline_dir: Optional[str] = None) -> None:
    """Fail on a ``tool/check*`` guard that no test names and the baseline does not accept (``tool/meta/baselines/guard_without_fixture.json``)."""
    enforce_findings(
        "guard_without_fixture",
        find_guards_without_fixture(repo_root),
        repo_root=repo_root,
        baseline_dir=baseline_dir or DEFAULT_BASELINE_DIR,
        label="guard_without_fixture",
        guidance="A tool/check* guard that no test names is never run against bad input. Add a test that invokes it, or accept it with a note.",
    )
