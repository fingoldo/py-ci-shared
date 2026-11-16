"""A threading.Thread must be daemonised, joined, or owned by a context manager.

A non-daemon thread keeps the interpreter alive at exit. pyutilz's ``UtilizationMonitor`` started a sampler thread that
hung exit whenever a caller forgot ``.stop()`` (2026-07-21 round 2).

Each ``threading.Thread(...)`` call is accepted when one of these holds:

* it passes ``daemon=True``;
* its object is joined, or has ``.daemon = True`` assigned, in the enclosing function (source files), or anywhere in the
  module for a test file (test helpers start a thread and the test joins it);
* it is created inside a class that defines both ``__enter__`` and ``__exit__``;
* it is assigned to ``self.<attr>`` and ``.<attr>.join()`` or ``.<attr>.daemon = True`` appears anywhere in the module.

Default scope is source files only: test directories are skipped unless ``include_tests=True``, in which case a test file
gets the module-wide join rule. Accepted deliberately with ``# thread-ok: <reason>`` on the call's line or the line above;
a marker with no reason is a finding.

Usage::

    from py_ci_shared.thread_lifecycle import assert_thread_lifecycle

    def test_threads_are_owned():
        assert_thread_lifecycle("mypackage", min_files=20)
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, ImportAliases, child_nodes, walk
from ._core.gate_contract import check_scan
from ._gate_report import _is_test_file, line_has_marker, scan_tree, skip_set

__all__ = ["MARKER", "RULE", "assert_thread_lifecycle", "find_thread_lifecycle"]

RULE = "thread-lifecycle"
MARKER = "thread-ok"


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in walk(tree) for child in child_nodes(parent)}


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST], kinds: tuple[type, ...]) -> Optional[ast.AST]:
    cur = parents.get(node)
    while cur is not None and not isinstance(cur, kinds):
        cur = parents.get(cur)
    return cur


def _is_daemon_kw(call: ast.Call) -> bool:
    return any(kw.arg == "daemon" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in call.keywords)


def _key(expr: ast.expr) -> Optional[tuple[str, ...]]:
    """A context-free identity for a Name (``("n", id)``) or a Name attribute (``("a", id, attr)``); None for anything else."""
    if isinstance(expr, ast.Name):
        return ("n", expr.id)
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        return ("a", expr.value.id, expr.attr)
    return None


def _join_or_daemon_for(scope: ast.AST, owner: ast.expr) -> bool:
    """True when *scope* calls ``<owner>.join(...)`` or assigns ``<owner>.daemon = True``, or joins each element of
    ``for v in <owner>:``. *owner* is a Name or self.attr."""
    key = _key(owner)
    for node in walk(scope):
        if isinstance(node, ast.For) and _key(node.iter) == key and isinstance(node.target, ast.Name):
            if _join_or_daemon_for(ast.Module(body=node.body, type_ignores=[]), node.target):
                return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
            if _key(node.func.value) == key:
                return True
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value is True:
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr == "daemon" and _key(t.value) == key:
                    return True
    return False


def _owner_of(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> Optional[ast.expr]:
    """The Name or ``self.<attr>`` the thread object is assigned to, or None when it is not assigned."""
    parent = parents.get(call)
    if isinstance(parent, ast.ListComp):  # threads = [threading.Thread(...) for ...]
        parent = parents.get(parent)
    if isinstance(parent, ast.Assign) and len(parent.targets) == 1:
        target = parent.targets[0]
        if isinstance(target, ast.Name):
            return target
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
            return target
    return None


def _in_context_manager_class(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    cls = _enclosing(call, parents, (ast.ClassDef,))
    if not isinstance(cls, ast.ClassDef):
        return False
    names = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return {"__enter__", "__exit__"} <= names


def _marker_reason(lines: list[str], line: int) -> Optional[str]:
    for k in (line, line - 1):
        if line_has_marker(lines, k, MARKER):
            return lines[k - 1].split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def _accepted(call: ast.Call, tree: ast.Module, parents: dict[ast.AST, ast.AST], test_file: bool) -> bool:
    if _is_daemon_kw(call) or _in_context_manager_class(call, parents):
        return True
    owner = _owner_of(call, parents)
    if owner is None:
        return False
    if test_file:
        return _join_or_daemon_for(tree, owner)
    if isinstance(owner, ast.Attribute):
        return _join_or_daemon_for(tree, owner)
    scope = _enclosing(call, parents, (ast.FunctionDef, ast.AsyncFunctionDef)) or tree
    return _join_or_daemon_for(scope, owner)


def find_thread_lifecycle(
    root: Union[str, Path],
    *,
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every ``threading.Thread`` under *root* with no daemon flag, join, or context-manager owner, sorted by path and line."""
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        parents = _parents(parsed.tree)
        lines = parsed.source.splitlines()
        test_file = _is_test_file(parsed.rel)
        for node in walk(parsed.tree):
            if not (isinstance(node, ast.Call) and aliases.qualified_name(node.func) == "threading.Thread"):
                continue
            if _accepted(node, parsed.tree, parents, test_file):
                continue
            reason = _marker_reason(lines, node.lineno)
            if reason:
                continue
            suffix = "; the # thread-ok marker needs a reason after it" if reason == "" else ""
            out.append(
                Finding(
                    parsed.rel,
                    node.lineno,
                    RULE,
                    f"non-daemon `threading.Thread` is never joined, daemonised, or owned by a context manager; it keeps the interpreter alive at exit{suffix}",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_thread_lifecycle(
    root: Union[str, Path],
    *,
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any unowned non-daemon thread."""
    found = find_thread_lifecycle(
        root,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    if found:
        raise AssertionError(f"{len(found)} {RULE} finding(s):\n  " + "\n  ".join(f.render() for f in found))
