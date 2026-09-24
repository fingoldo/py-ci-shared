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

from ._core import DEFAULT_EXCLUDE, SourceError, iter_files, parse_file, read_source

# A paragraph opener, or a markdown table row with a `Disposition` cell (`| X-1 | Disposition: fixed, `test_y` |`).
_DISPOSITION_START = re.compile(r"^\s*(?:-\s*)?(?:\*\*Disposition|Disposition:)")
_TABLE_DISPOSITION = re.compile(r"^\s*\|(?:.*\|)?\s*(?:\*\*)?Disposition\b")
# `.py` must end the path (`tests/x.pyi` is not `tests/x.py`); a parametrised id (`::test_x[case]`) keeps its name.
_TEST_PATH = re.compile(r"(?<![\w/])((?:[\w.\-]+/)*tests/[\w./\-]+?\.py)(?![\w])(?:::(\w+)(?:\[[^\]`\s]*\])?)?(?:::(\w+)(?:\[[^\]`\s]*\])?)?")
_TEST_NAME = re.compile(r"`((?:Test[A-Z]\w*)|(?:test_\w+))(?:\[[^\]`]*\])?(?:::(\w+)(?:\[[^\]`]*\])?)?`")


class _Defined:
    """Top-level-or-nested def/class names of one file, and each class's own members."""

    def __init__(self, names: set[str], members: dict[str, set[str]]) -> None:
        self.names = names
        self.members = members


def _definitions(path: Path) -> _Defined:
    """Raises ``SourceError`` for a file that cannot be read or parsed (it is reported, never read as empty)."""
    tree = parse_file(path)
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    members: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            members.setdefault(node.name, set()).update(c.name for c in node.body if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    return _Defined(names, members)


def _defined_names(path: Path) -> set[str]:
    try:
        return _definitions(path).names
    except SourceError:
        return set()


def disposition_paragraphs(text: str) -> list[str]:
    """Every disposition paragraph in a round file, fenced blocks excluded. A table row with a ``Disposition``
    cell is a paragraph of its own. Windows path separators are normalised to ``/``."""
    out: list[str] = []
    current: list[str] = []
    fenced = False
    for line in text.replace("\\", "/").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if current:
            if line.strip() == "" or line.startswith("#") or line.lstrip().startswith("|"):
                out.append(" ".join(current))
                current = []
            else:
                current.append(line.strip())
                continue
        if _TABLE_DISPOSITION.match(line):
            out.append(line.strip())
        elif _DISPOSITION_START.match(line):
            current = [line.strip()]
    if current:
        out.append(" ".join(current))
    return out


def _member_problems(where: str, ref: str, first: str, second: str, defined: _Defined) -> set[str]:
    """``first`` must be defined; ``second`` (when given) must be a member of the CLASS ``first``."""
    if first not in defined.names:
        missing = [first] + ([second] if second and second not in defined.names else [])
        return {f"{where}: `{ref}::{part}`: not defined in that file" for part in missing}
    if second and second not in defined.members.get(first, set()):
        return {f"{where}: `{ref}::{second}`: not defined in that file"}
    return set()


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
        if not first:
            continue
        try:
            defined = _definitions(path)
        except SourceError as exc:
            problems.add(f"{where}: `{rel}`: cannot be parsed, so its tests cannot be confirmed ({exc.message})")
            continue
        problems |= _member_problems(where, rel, first, second, defined)
    return problems


def _name_reference_problems(where: str, para: str, all_names: set[str], tests_dir: str, class_members: "dict[str, set[str]] | None" = None) -> set[str]:
    """Backticked `TestX` / `test_x` (optionally `TestX::member`, member of that class) that no test file defines."""
    problems: set[str] = set()
    for name, member in _TEST_NAME.findall(para):
        if name not in all_names:
            missing: "str | None" = name
        elif member and (member not in class_members.get(name, set()) if class_members is not None else member not in all_names):
            missing = member
        else:
            missing = None
        if missing:
            ref = f"{name}::{member}" if member else name
            problems.add(f"{where}: `{ref}`: no test of that name in {tests_dir}/")
    return problems


def find_missing_test_references(audit_files: Iterable[Path], project_root: Path, *, tests_dir: str = "tests", other_roots: Iterable[Path] = ()) -> list[str]:
    """``<file>: <reference>: <why>`` for every named test that is not there.

    *other_roots* are sibling projects a disposition may cite: their test files resolve a
    `sibling/tests/x.py` path, and the names they define count for a bare `TestX` too -- a fix that
    landed in the sibling is tested there. A test file that cannot be parsed is itself a problem: the names
    it would define cannot be confirmed.
    """
    siblings = tuple(other_roots)
    test_files = [f for r in (project_root, *siblings) if (r / tests_dir).is_dir() for f in iter_files(r / tests_dir, ("*.py",), exclude=DEFAULT_EXCLUDE)]
    all_names: set[str] = set()
    class_members: dict[str, set[str]] = {}
    problems: set[str] = set()
    for f in test_files:
        try:
            defined = _definitions(f)
        except SourceError as exc:
            problems.add(f"{f.name}: cannot be parsed, so the tests it defines cannot be confirmed ({exc.message})")
            continue
        all_names |= defined.names
        for cls, members in defined.members.items():
            class_members.setdefault(cls, set()).update(members)
    for audit in audit_files:
        for para in disposition_paragraphs(read_source(audit)):
            problems |= _path_reference_problems(audit.name, para, project_root, tests_dir, siblings)
            problems |= _name_reference_problems(audit.name, para, all_names, tests_dir, class_members)
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
