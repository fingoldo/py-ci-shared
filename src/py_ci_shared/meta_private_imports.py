"""A meta-test does not import a private name of the package it polices, unless a permitted entry says why.

The point of a meta-test is the public contract: one that imports ``_foo`` from production is testing an
implementation, and passes or fails with it. Some meta-tests have no public surface to reach -- a fail-closed gate,
a latched flag, a mixin's place in an MRO -- so each repo keeps a permitted set of ``"<test stem>::<dotted name>"``
entries, each with its reason.

mlframe, pyutilz and llm_bench each carried this check. None of them failed on a permitted entry that no longer
matched an import, so the set only grew: a test deleted or rewritten left its permission behind, ready to excuse
the next import of the same name. Here a stale entry fails like a new import does.

A name is private when its segment starts with one underscore; a dunder is not. By default only the LAST dotted
segment is judged, which is the mlframe and pyutilz rule: ``from pkg._impl import Public`` passes, ``from pkg.impl
import _helper`` does not. ``any_segment=True`` judges every segment after the package, which is llm_bench's rule
for ``import pkg._impl``. Relative imports are never judged: they are the test package's own modules.

Usage::

    from py_ci_shared.meta_private_imports import assert_no_private_meta_imports

    def test_meta_tests_dont_reach_private_internals():
        assert_no_private_meta_imports(META_DIR, ("mypkg",), permitted=_PERMITTED, exclude=(Path(__file__).name,))
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

__all__ = ["assert_no_private_meta_imports", "imported_names", "private_meta_imports"]


def imported_names(tree: ast.AST) -> list[str]:
    """Dotted names imported by ``import X`` and ``from X import Y`` (as ``X.Y``); relative imports are skipped."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            base = node.module or ""
            out.extend(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return out


def _is_private(segment: str) -> bool:
    return segment.startswith("_") and not segment.startswith("__")


def private_meta_imports(files: Iterable[Path], packages: Iterable[str], *, any_segment: bool = False) -> tuple[int, set[str]]:
    """``(files parsed, {"<stem>::<dotted name>", ...})`` for private names of *packages* the files import."""
    roots = set(packages)
    found: set[str] = set()
    parsed = 0
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        parsed += 1
        for name in imported_names(tree):
            parts = name.split(".")
            if parts[0] not in roots:
                continue
            judged = parts[1:] if any_segment else parts[-1:]
            if any(_is_private(p) for p in judged):
                found.add(f"{path.stem}::{name}")
    return parsed, found


def assert_no_private_meta_imports(
    meta_dir: Path,
    packages: Iterable[str],
    *,
    permitted: Iterable[str] = (),
    exclude: Iterable[str] = (),
    any_segment: bool = False,
    min_files: int = 1,
) -> None:
    """Fail on a private import no permitted entry covers, on a permitted entry nothing imports, or on an empty scan."""
    import pytest

    skip = set(exclude)
    files = sorted(p for p in meta_dir.glob("test_*.py") if p.name not in skip)
    parsed, found = private_meta_imports(files, packages, any_segment=any_segment)
    if parsed < min_files:
        pytest.fail(f"only {parsed} meta-test file(s) parsed under {meta_dir}; expected at least {min_files} -- Check meta_dir, the scan has lost its subject")
    allowed = set(permitted)
    new = sorted(found - allowed)
    stale = sorted(allowed - found)
    problems: list[str] = []
    if new:
        problems.append(
            f"{len(new)} meta-test import(s) of a private name. Either test the public contract instead, or add "
            "'<test stem>::<dotted name>' to the permitted set with the reason:\n  " + "\n  ".join(new)
        )
    if stale:
        problems.append(f"{len(stale)} permitted entr(ies) no longer match any import -- Remove them:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))
