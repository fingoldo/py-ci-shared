"""A mutable placeholder built once in an enclosing scope and handed out by reference from a function.

``_EMPTY = Rows()`` at module level, then ``def fetch(): return _EMPTY``: every caller receives the same object, so the first
caller that appends to it or fills it changes what every later caller sees. A fresh value per call (``return Rows()``, or a
local ``rows = Rows(); return rows``) does not have the defect, and neither does a copy on return (``return list(_EMPTY)``,
``return _EMPTY.copy()``, ``copy.copy(_EMPTY)``), since the return is then no longer the name itself.

A placeholder is a name assigned, directly in a module or function body, to a list, dict or set literal, to a builtin
collection constructor (``list()``, ``dict()``, ``set()``, ``deque()``, ``defaultdict()``, ``OrderedDict()``, ``Counter()``,
``bytearray()``), or to a call of a capitalised name (a class such as ``Rows()``), except a few immutable value types
(``Path``, ``Decimal``, ``frozenset``, ...). A ``return NAME`` is a finding when ``NAME`` is a placeholder of an enclosing
scope (module, or a function a nested function sits in), and the returning function does not rebind the name itself.

Not judged: a function that rebinds the name (a parameter, a local assignment, a ``global`` declaration does count as the
module's name and is judged), a return of anything other than the bare name, and a placeholder whose value is a factory call
with a lowercase name (``make_rows()``), since the gate cannot tell that it is shared.

Usage::

    from py_ci_shared.shared_mutable_placeholder import assert_no_shared_mutable_placeholders

    def test_no_shared_mutable_placeholders():
        assert_no_shared_mutable_placeholders(_py_files(REPO / "src"))
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, ParsedFile, scan_python
from ._core.node_index import nodes_of

__all__ = ["RULE", "assert_no_shared_mutable_placeholders", "find_shared_mutable_placeholders"]

RULE = "shared-mutable-placeholder"
_BUILTIN_COLLECTIONS = frozenset({"list", "dict", "set", "deque", "defaultdict", "OrderedDict", "Counter", "bytearray"})
_IMMUTABLE_CONSTRUCTORS = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath", "Decimal", "Fraction", "frozenset", "Enum", "Pattern"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_Scope = Union[ast.Module, ast.FunctionDef, ast.AsyncFunctionDef]


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def is_placeholder_value(value: ast.AST) -> bool:
    """True when the expression builds a mutable collection (see the module docstring)."""
    if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if not isinstance(value, ast.Call):
        return False
    name = _callee(value)
    if name in _BUILTIN_COLLECTIONS:
        return True
    return bool(name) and name[0].isupper() and name not in _IMMUTABLE_CONSTRUCTORS


def _direct_nodes(scope: _Scope) -> list[ast.AST]:
    """Nodes that execute in *scope*'s own body: nested function, class and lambda bodies are other scopes (the def is kept)."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(scope.body) if isinstance(scope, ast.Module) else [s for s in ast.iter_child_nodes(scope)]
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, _SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _placeholders_in(direct: list[ast.AST]) -> frozenset[str]:
    names: set[str] = set()
    for node in direct:
        if isinstance(node, ast.Assign) and is_placeholder_value(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None and is_placeholder_value(node.value) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def _bound_in(fn: _Scope, direct: list[ast.AST]) -> frozenset[str]:
    """Names *fn* binds itself (parameters, assignments, nested definitions), minus those it declares ``global``."""
    args = fn.args  # type: ignore[union-attr]
    params = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
    params += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
    bound: set[str] = set(params)
    declared_global: set[str] = set()
    for node in direct:
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Global):
            declared_global.update(node.names)
    return frozenset(bound - declared_global)


def _returns_in(fn: _Scope, direct: list[ast.AST], rel: str, visible: frozenset[str], scope_name: str) -> list[Finding]:
    return [
        Finding(
            rel,
            node.lineno,
            RULE,
            f"{node.value.id} is a mutable placeholder built once in {scope_name} and returned by reference from "
            f"{getattr(fn, 'name', '<function>')}: every caller shares one object; build the value inside the function or return a copy",
        )
        for node in direct
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name) and node.value.id in visible
    ]


def _findings_in(parsed: ParsedFile) -> list[Finding]:
    """One pass over the scopes: each scope's own nodes are collected once and serve the placeholder, binding and return checks."""
    if not any(isinstance(r.value, ast.Name) for r in nodes_of(parsed.tree, ast.Return)):
        return []  # a finding is a ``return NAME``; a module with none has nothing to judge
    out: list[Finding] = []
    module_direct = _direct_nodes(parsed.tree)
    # (scope node, its own nodes, names an inner function can see, name of the scope a finding is reported against)
    todo: list[tuple[ast.AST, list[ast.AST], frozenset[str], str]] = [(parsed.tree, module_direct, _placeholders_in(module_direct), "the module")]
    while todo:
        _, direct, enclosing, scope_name = todo.pop()
        for node in direct:
            if isinstance(node, _FUNCTIONS):
                fn_direct = _direct_nodes(node)
                visible = enclosing - _bound_in(node, fn_direct)
                out.extend(_returns_in(node, fn_direct, parsed.rel, visible, scope_name))
                todo.append((node, fn_direct, visible | _placeholders_in(fn_direct), node.name))
            elif isinstance(node, ast.ClassDef):
                todo.append((node, _direct_nodes(node), enclosing, scope_name))
    return out


def find_shared_mutable_placeholders(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every ``return NAME`` of an enclosing scope's mutable placeholder in *files*, sorted by path and line.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for an unreadable file (unless
    *allow_unparsed*).
    """
    scan = scan_python(list(files), min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_no_shared_mutable_placeholders(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any function that returns an enclosing scope's mutable placeholder by reference (see the module docstring)."""
    found = find_shared_mutable_placeholders(files, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(
            f"{len(found)} shared-mutable-placeholder finding(s): a shared object returned by reference is mutated by every caller:\n  "
            + "\n  ".join(f.render() for f in found)
        )
