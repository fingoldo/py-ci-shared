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

from ._core import DEFAULT_EXCLUDE, ImportAliases, iter_files, scan_python
from ._core.node_index import walk as _fast_walk

__all__ = ["assert_no_private_meta_imports", "imported_names", "private_meta_imports"]

_DYNAMIC_IMPORTERS = frozenset({"importlib.import_module", "__import__", "builtins.__import__"})


def imported_names(tree: ast.AST) -> list[str]:
    """Dotted names imported by ``import X``, ``from X import Y`` (as ``X.Y``) and a literal
    ``importlib.import_module("X")`` / ``__import__("X")``; relative imports are skipped."""
    out: list[str] = []
    aliases = ImportAliases.from_tree(tree)
    for node in _fast_walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            base = node.module or ""
            out.extend(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and not node.args[0].value.startswith(".")
            and aliases.qualified_name(node) in _DYNAMIC_IMPORTERS
        ):
            out.append(node.args[0].value)
    return out


def _is_private(segment: str) -> bool:
    return segment.startswith("_") and not segment.startswith("__")


def private_meta_imports(files: Iterable[Path], packages: Iterable[str], *, any_segment: bool = False) -> tuple[int, set[str]]:
    """``(files parsed, {"<stem>::<dotted name>", ...})`` for private names of *packages* the files import.

    A file that cannot be read or parsed is not skipped: it is returned as ``"<stem>::<unparsed>"`` (and does not
    count as parsed), because nobody can say what it imports.
    """
    roots = set(packages)
    found: set[str] = set()
    scan = scan_python([Path(f) for f in files], min_files=0)
    for parsed in scan:
        for name in imported_names(parsed.tree):
            parts = name.split(".")
            if parts[0] not in roots:
                continue
            judged = parts[1:] if any_segment else parts[-1:]
            if any(_is_private(p) for p in judged):
                found.add(f"{parsed.path.stem}::{name}")
    found.update(f"{problem.path.stem}::<unparsed>" for problem in scan.unparsed)
    return scan.parsed_count, found


def assert_no_private_meta_imports(
    meta_dir: Path,
    packages: Iterable[str],
    *,
    permitted: Iterable[str] = (),
    exclude: Iterable[str] = (),
    any_segment: bool = False,
    min_files: int = 1,
    recursive: bool = True,
) -> None:
    """Fail on a private import no permitted entry covers, on a permitted entry nothing imports, on an unparsable
    meta-test, or on an empty scan. Meta-tests in subdirectories are included unless ``recursive=False``."""
    import pytest

    skip = set(exclude)
    candidates = iter_files(meta_dir, ("test_*.py",), exclude=DEFAULT_EXCLUDE) if recursive else sorted(meta_dir.glob("test_*.py"))
    files = [p for p in candidates if p.name not in skip]
    parsed, found = private_meta_imports(files, packages, any_segment=any_segment)
    if parsed < min_files:
        pytest.fail(f"only {parsed} meta-test file(s) parsed under {meta_dir}; expected at least {min_files} -- Check meta_dir, the scan has lost its subject")
    unparsed = sorted(f for f in found if f.endswith("::<unparsed>"))
    found -= set(unparsed)
    allowed = set(permitted)
    new = sorted(found - allowed)
    stale = sorted(allowed - found)
    problems: list[str] = []
    if unparsed:
        problems.append(f"{len(unparsed)} meta-test file(s) could not be parsed, so their imports are unknown:\n  " + "\n  ".join(unparsed))
    if new:
        problems.append(
            f"{len(new)} meta-test import(s) of a private name. Either test the public contract instead, or add "
            "'<test stem>::<dotted name>' to the permitted set with the reason:\n  " + "\n  ".join(new)
        )
    if stale:
        problems.append(f"{len(stale)} permitted entr(ies) no longer match any import -- Remove them:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))
