"""A function that walks a repository root must prune agent worktrees.

Agent worktrees live under ``<repo>/.claude/worktrees`` and are full copies of the repository. A walk that enters them
multiplies its work: one consumer audit test took 19 to 30 minutes with about twenty of them present (pyutilz
test_no_unpruned_tree_walk).

The gate is narrow on purpose. It checks only a function whose docstring names one of its parameters as the repository
root (``repo root``, ``repository root`` or ``project root`` next to the parameter's name). Inside such a function, a walk
of that parameter (``p.rglob``, ``p.glob``, ``p.walk``, ``Path(p).rglob``, ``os.walk(p)``) must be pruned: the function
source must name ``worktrees`` (a filter) or call ``iter_files_pruned``. A test file is not scanned by default.

Accepted deliberately with ``# tree-walk-ok: <reason>`` on the walk's line or the line above; a marker with no reason is
a finding.

Usage::

    from py_ci_shared.unpruned_tree_walk import assert_unpruned_tree_walk

    def test_root_walks_prune_worktrees():
        assert_unpruned_tree_walk("mypackage", min_files=20)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, walk
from ._core.gate_contract import check_scan
from ._gate_report import line_has_marker, scan_tree, skip_set

__all__ = ["MARKER", "RULE", "assert_unpruned_tree_walk", "find_unpruned_tree_walk"]

RULE = "unpruned-tree-walk"
MARKER = "tree-walk-ok"
_WALK_ATTRS = frozenset({"rglob", "glob", "walk"})
_PRUNE_EVIDENCE = ("worktrees", "iter_files_pruned")


def _documented_roots(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Parameter names the docstring names as a repository root (``<name>`` near ``repo root``, ``repository root``, ``project root``)."""
    doc = ast.get_docstring(fn) or ""
    params = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    return {p for p in params if re.search(rf"\b{re.escape(p)}\b[^.\n]*\b(?:repo|repository|project) root\b|\b(?:repo|repository|project) root\b[^.\n]*\b{re.escape(p)}\b", doc)}


def _walked_name(expr: ast.expr) -> Optional[str]:
    """The parameter a walk receiver names: ``p``, ``Path(p)`` or ``os.fspath(p)``."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Call) and expr.args and isinstance(expr.args[0], ast.Name):
        return expr.args[0].id
    return None


def _walks(fn: ast.FunctionDef | ast.AsyncFunctionDef, roots: set[str]) -> list[tuple[str, int]]:
    """``(root, line)`` for each walk of a documented root inside *fn*."""
    out: list[tuple[str, int]] = []
    for node in walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _WALK_ATTRS:
            continue
        owner = node.func.value
        if node.func.attr == "walk" and isinstance(owner, ast.Name) and owner.id == "os" and node.args:
            name = _walked_name(node.args[0])
        else:
            name = _walked_name(owner)
        if name in roots:
            out.append((name, node.lineno))
    return out


def _marker_reason(lines: list[str], line: int) -> Optional[str]:
    for k in (line, line - 1):
        if line_has_marker(lines, k, MARKER):
            return lines[k - 1].split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def find_unpruned_tree_walk(
    root: Union[str, Path],
    *,
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every walk of a documented repository-root parameter that does not prune worktrees, sorted by path and line."""
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        lines = parsed.source.splitlines()
        for fn in walk(parsed.tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            roots = _documented_roots(fn)
            if not roots:
                continue
            body = "\n".join(parsed.source.splitlines()[fn.lineno - 1 : fn.end_lineno])
            if any(token in body for token in _PRUNE_EVIDENCE):
                continue
            for name, line in _walks(fn, roots):
                reason = _marker_reason(lines, line)
                if reason:
                    continue
                suffix = "; the # tree-walk-ok marker needs a reason after it" if reason == "" else ""
                out.append(
                    Finding(
                        parsed.rel,
                        line,
                        RULE,
                        f"`{fn.name}` walks the repository root `{name}` without pruning `.claude/worktrees`; filter those parts out or use iter_files_pruned{suffix}",
                    )
                )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_unpruned_tree_walk(
    root: Union[str, Path],
    *,
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any documented-root walk that does not prune agent worktrees."""
    found = find_unpruned_tree_walk(
        root,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    if found:
        raise AssertionError(f"{len(found)} {RULE} finding(s):\n  " + "\n  ".join(f.render() for f in found))
