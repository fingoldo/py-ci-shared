"""Shared check: a test double reaching duck-typed production code must be spec-bound.

2026-09-05 glossum agent wave, findings 02-F1 through 02-F5. ``advisory_lock.claim_lock``
resolves the session it runs lock SQL against through ``getattr(target, "session", None)``
-- the indirection exists because one caller swaps its session mid-flight. A bare
``AsyncMock()`` AUTO-CREATES that attribute, so every lock statement went to a different
child mock whose ``scalar()`` was a truthy Mock: the lock always appeared acquired, the
conflict never raised, and a test named "exits early on lock conflict" never reached one.
Five tests across four files were affected, and one of them fell through into the batch
loop it was protecting and span for over two hours, growing to 82 GiB.

The production-side alternative was considered and rejected there: making the resolver
follow ``.session`` only when it is a real session would break legitimate hand-written
holder fakes, and no attribute test can distinguish a Mock from a holder. The rule belongs
on the test side, which is what this enforces.

SCOPE, deliberately narrow
--------------------------
``Mock()``/``AsyncMock()`` with no spec is the right tool for a commit coroutine, a context
manager or an HTTP client, and a file-wide ban flagged five files whose doubles never reach
a lock. Two shapes are reported instead:

* a spec-less mock bound to a name matching ``name_hints`` (e.g. anything with "session" in
  it), and
* a spec-less mock passed directly to one of ``entry_points`` -- the functions that take the
  duck-typed object.

Usage::

    from py_ci_shared.spec_bound_doubles import assert_doubles_are_spec_bound

    def test_no_bare_mock_reaches_the_lock():
        assert_doubles_are_spec_bound(
            files=lock_driving_tests,
            entry_points={"claim_lock", "try_claim_lock"},
            name_hints={"session"},
            repo_root=REPO,
        )
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

DEFAULT_MOCK_NAMES = frozenset({"Mock", "MagicMock", "AsyncMock", "NonCallableMock"})


def is_bare_mock(node: ast.AST, mock_names: frozenset[str] = DEFAULT_MOCK_NAMES) -> bool:
    """True for ``Mock()`` / ``AsyncMock()`` with no ``spec``/``spec_set`` and no args."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name not in mock_names:
        return False
    return not node.args and not any(kw.arg in ("spec", "spec_set") for kw in node.keywords)


def find_unbound_doubles(
    path: Path,
    *,
    entry_points: frozenset[str],
    name_hints: frozenset[str],
    mock_names: frozenset[str] = DEFAULT_MOCK_NAMES,
) -> list[int]:
    """Lines where a spec-less mock is used as the duck-typed object."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []

    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            names += [t.attr for t in targets if isinstance(t, ast.Attribute)]
            if any(h in n.lower() for n in names for h in name_hints) and is_bare_mock(node.value, mock_names):
                lines.add(node.value.lineno)
        if isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in entry_points:
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    if is_bare_mock(arg, mock_names):
                        lines.add(arg.lineno)
    return sorted(lines)


def files_driving(files: Iterable[Path], entry_points: Iterable[str]) -> list[Path]:
    """The test files that mention one of the duck-typed entry points."""
    wanted = tuple(entry_points)
    out = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(name in text for name in wanted):
            out.append(path)
    return out


def assert_doubles_are_spec_bound(
    *,
    files: Iterable[Path],
    entry_points: Iterable[str],
    name_hints: Iterable[str],
    repo_root: Path,
    min_subjects: int = 1,
) -> None:
    """Fail when a spec-less mock is used where production duck-types on an attribute."""
    import pytest

    entries = frozenset(entry_points)
    hints = frozenset(h.lower() for h in name_hints)
    subjects = files_driving(files, entries)

    if len(subjects) < min_subjects:
        pytest.fail(
            f"only {len(subjects)} test file(s) mention {sorted(entries)} -- expected at least "
            f"{min_subjects}. The scan lost its subject."
        )

    problems: list[str] = []
    for path in subjects:
        lines = find_unbound_doubles(path, entry_points=entries, name_hints=hints)
        if lines:
            rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
            problems.append(
                f"{rel} builds a spec-less mock at line(s) {lines} and hands it to duck-typed "
                f"code; a bare Mock auto-creates whatever attribute that code resolves through, "
                f"which silently routes the call to a different object"
            )

    if problems:
        pytest.fail(f"{len(problems)} file(s) with unbound doubles:\n  " + "\n  ".join(problems))
