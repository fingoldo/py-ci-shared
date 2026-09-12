"""Shared check: a test an audit disposition names must exist.

``audit_disposition_parity`` checks the FILES a disposition names. The commonest claim in these
dispositions is a different one -- "covered by `TestX`", "test: `tests/test_y.py::test_z`" -- and it
was the claim that went unchecked. On 2026-09-12 a disposition recorded that a guard in
production_scrapers' ``scan_loop`` was tested; no test tripped it, and it was found only because a
synthesis agent looked. A named test that does not exist is the cheapest false RESOLVED there is.

What counts as a claim, inside a disposition paragraph (from ``**Disposition`` / ``Disposition:`` to
the next blank line):

* ``tests/<path>.py`` -- the file must exist; with ``::Name`` (or ``::Class::name``) each part must be
  defined in that file.
* a backticked ``TestSomething`` or ``test_something`` -- must be defined in some test file of the
  project.

Problem strings carry no line numbers, so a baseline of historical references (renamed since) survives
edits to the round files.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

_DISPOSITION_START = re.compile(r"^\s*(?:-\s*)?(?:\*\*Disposition|Disposition:)")
_TEST_PATH = re.compile(r"(?<![\w/])((?:[\w.\-]+/)*tests/[\w./\-]+?\.py)(?:::(\w+))?(?:::(\w+))?")
_TEST_NAME = re.compile(r"`((?:Test[A-Z]\w*)|(?:test_\w+))(?:::(\w+))?`")


def _defined_names(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def disposition_paragraphs(text: str) -> list[str]:
    """Every disposition paragraph in a round file, fenced blocks excluded."""
    out: list[str] = []
    current: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if current:
            if line.strip() == "" or line.startswith("#"):
                out.append(" ".join(current))
                current = []
            else:
                current.append(line.strip())
                continue
        if _DISPOSITION_START.match(line):
            current = [line.strip()]
    if current:
        out.append(" ".join(current))
    return out


def _path_reference_problems(where: str, para: str, project_root: Path, tests_dir: str, other_roots: "tuple[Path, ...]" = ()) -> set[str]:
    """`tests/<file>.py[::A[::b]]` references in one paragraph whose file or members are missing."""
    problems: set[str] = set()
    for raw, first, second in _TEST_PATH.findall(para):
        rel = raw.rstrip(".")
        # `tests/x.py`, or with this project's own prefix (`pkg/tests/x.py`), or a SIBLING project's
        # (`other_pkg/tests/x.py`), resolved against that sibling.
        if rel.startswith(tests_dir + "/"):
            # A bare `tests/x.py` in a SIBLING's round: the fix landed there and the round names the
            # package in its prose, not inside the backticks. A dashboard disposition citing
            # `tests/test_check_indexes_reconciliation.py` (realtime_applications') read as missing here.
            candidates = [project_root / rel, *(r / rel for r in other_roots)]
        else:
            candidates = [project_root / rel, project_root / rel.split("/", 1)[-1]]
        candidates += [r.parent / rel for r in other_roots if rel.startswith(r.name + "/")]
        path = next((c for c in candidates if c.is_file()), None)
        if path is None:
            problems.add(f"{where}: `{rel}`: no such test file")
            continue
        names = _defined_names(path)
        problems |= {f"{where}: `{rel}::{part}`: not defined in that file" for part in (first, second) if part and part not in names}
    return problems


def _name_reference_problems(where: str, para: str, all_names: set[str], tests_dir: str) -> set[str]:
    """Backticked `TestX` / `test_x` (optionally `::member`) in one paragraph that no test file defines."""
    problems: set[str] = set()
    for name, member in _TEST_NAME.findall(para):
        missing = name if name not in all_names else member if member and member not in all_names else None
        if missing:
            ref = f"{name}::{member}" if member else name
            problems.add(f"{where}: `{ref}`: no test of that name in {tests_dir}/")
    return problems


def find_missing_test_references(audit_files: Iterable[Path], project_root: Path, *, tests_dir: str = "tests", other_roots: Iterable[Path] = ()) -> list[str]:
    """``<file>: <reference>: <why>`` for every named test that is not there.

    *other_roots* are sibling projects a disposition may cite: their test files resolve a
    `sibling/tests/x.py` path, and the names they define count for a bare `TestX` too -- a fix that
    landed in the sibling is tested there.
    """
    siblings = tuple(other_roots)
    test_files = [f for r in (project_root, *siblings) if (r / tests_dir).is_dir() for f in sorted((r / tests_dir).rglob("*.py"))]
    all_names: set[str] = set().union(*(_defined_names(f) for f in test_files)) if test_files else set()
    problems: set[str] = set()
    for audit in audit_files:
        for para in disposition_paragraphs(audit.read_text(encoding="utf-8", errors="replace")):
            problems |= _path_reference_problems(audit.name, para, project_root, tests_dir, siblings)
            problems |= _name_reference_problems(audit.name, para, all_names, tests_dir)
    return sorted(problems)


def assert_disposition_tests_exist(
    audit_files: Iterable[Path],
    project_root: Path,
    *,
    known: Iterable[str] = (),
    min_files: int = 1,
    tests_dir: str = "tests",
    other_roots: Iterable[Path] = (),
) -> None:
    """Shrink-only: a missing reference not in *known* fails, and so does a *known* one that resolves again."""
    import pytest

    files = list(audit_files)
    if len(files) < min_files:
        pytest.fail(f"only {len(files)} audit file(s) given; expected at least {min_files} -- this would check nothing")
    found = set(find_missing_test_references(files, project_root, tests_dir=tests_dir, other_roots=other_roots))
    new, stale = sorted(found - set(known)), sorted(set(known) - found)
    if new or stale:
        pytest.fail(
            (f"{len(new)} disposition(s) name a test that does not exist:\n  " + "\n  ".join(new) if new else "")
            + (f"\n{len(stale)} accepted entr(ies) now resolve -- remove them:\n  " + "\n  ".join(stale) if stale else "")
        )
