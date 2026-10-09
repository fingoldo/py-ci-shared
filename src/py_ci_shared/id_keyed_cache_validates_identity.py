"""A module-level cache keyed by ``id(obj)`` must keep a weakref to ``obj`` or re-check identity when it reads.

CPython reuses an object's ``id`` as soon as the object is freed. A registry such as::

    _HOST_CODES: dict[int, DeviceCodes] = {}

    def register(host_array, codes):
        _HOST_CODES[id(host_array)] = codes

    def lookup(host_array):
        return _HOST_CODES.get(id(host_array))

serves array A's device codes to a new array B allocated at the same address after A was freed: wrong values, no
exception, and only on the runs where the allocator happens to hand the address back. That shipped in a handoff
registry between a host-side encoder and a GPU stage. The two safe shapes are:

* the module stores a weakref to the keyed object (``weakref.ref``, ``weakref.finalize``, ``weakref.proxy``,
  ``weakref.WeakMethod``), so the entry can be dropped, or detected as dead, when the object goes away;
* the function that READS the cache compares identity before trusting the entry (``entry.ref() is obj``,
  ``entry[0] is obj``, ``if cached_obj is not obj: miss``).

Matched: a dict-like module-level name (``{}``, ``dict()``, ``OrderedDict()``, ``WeakValueDictionary()``...) with at
least one access whose key contains ``id(...)``: directly, in a tuple, f-string or arithmetic, through a local name
bound from such an expression, or through a module function that returns one (``def _key(o): return (id(o), o.shape)``).
Accesses are subscripts, ``.get``/``.pop``/``.setdefault``/``__getitem__`` and ``in``. Each function that reads such a
cache is reported unless the module uses ``weakref`` as above or that function contains an identity comparison
(``is``/``is not`` against something other than ``None``/``True``/``False``).

Known false positives: a cache that stores the keyed object itself as the value and compares by ``==`` elsewhere, a
cache whose key also holds a value that makes address reuse harmless (a content hash next to the ``id``), and a cache
cleared on every call. Suppress with ``# id-key-ok: <reason>`` on the read, on the write that builds the key, or on the
cache's definition. A cache owned by an instance (``self._cache``) is not matched; only module-level names are.

Usage in a consumer's meta test::

    from py_ci_shared.id_keyed_cache_validates_identity import assert_id_keyed_cache_validates_identity

    def test_id_keyed_caches_validate_identity():
        assert_id_keyed_cache_validates_identity("src", min_files=50)
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from ._core.node_index import walk as _fast_walk
from ._gate_report import line_has_marker

__all__ = ["RULE", "MARKER", "assert_id_keyed_cache_validates_identity", "find_id_keyed_cache_validates_identity"]

RULE = "id-keyed-cache-validates-identity"
MARKER = "id-key-ok"
_DICT_FACTORIES = frozenset({"dict", "OrderedDict", "defaultdict", "WeakValueDictionary", "WeakKeyDictionary", "LRUCache", "TTLCache"})
_WEAKREF_NAMES = frozenset({"ref", "finalize", "proxy", "WeakMethod"})
_READ_METHODS = frozenset({"get", "pop", "setdefault", "__getitem__"})
_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
_Scope = Union[ast.Module, _FunctionNode]


def _module_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Module-scope statements, descending into ``if``/``try``/``with`` blocks but never into a def or class."""
    for node in body:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for name in ("body", "orelse", "finalbody"):
            yield from _module_statements(getattr(node, name, None) or [])
        for handler in getattr(node, "handlers", None) or []:
            yield from _module_statements(handler.body)


def _is_dict_value(value: ast.expr, aliases: ImportAliases) -> bool:
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return True
    if isinstance(value, ast.Call):
        return (aliases.qualified_name(value) or "").rsplit(".", 1)[-1] in _DICT_FACTORIES
    return False


def _module_caches(tree: ast.Module, aliases: ImportAliases) -> dict[str, int]:
    out: dict[str, int] = {}
    for node in _module_statements(tree.body):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if _is_dict_value(value, aliases):
            for target in targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, node.lineno)
    return out


def _uses_weakref(tree: ast.Module, aliases: ImportAliases) -> bool:
    for node in _fast_walk(tree):
        if isinstance(node, ast.Call):
            qualified = aliases.qualified_name(node) or ""
            head, _, tail = qualified.rpartition(".")
            if head.split(".")[0] == "weakref" and tail in _WEAKREF_NAMES:
                return True
    return False


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes of *scope* without descending into nested functions, classes or lambdas (judged on their own)."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _scopes(tree: ast.Module) -> list[_Scope]:
    return [tree] + [n for n in _fast_walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _has_id_call(expr: ast.AST, id_helpers: set[str], id_names: set[str], fixed: frozenset[str]) -> bool:
    """Does *expr* hold ``id(x)`` (``x`` not a module-level constant in *fixed*), a call to an id helper, or an id-bound local?"""
    for node in _fast_walk(expr):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "id" and not (len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id in fixed):
                return True
            if node.func.id in id_helpers:
                return True
        if isinstance(node, ast.Name) and node.id in id_names:
            return True
    return False


def _id_helpers(tree: ast.Module) -> set[str]:
    """Module functions returning an expression with ``id(...)``: ``def _key(o): return (id(o), o.shape)``."""
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            returns = [inner.value for inner in _own_nodes(node) if isinstance(inner, ast.Return) and inner.value is not None]
            if returns and all(_has_id_call(value, set(), set(), frozenset()) for value in returns):
                out.add(node.name)
    return out


def _module_names(tree: ast.Module) -> set[str]:
    """Names bound at module scope by assignment, def, class or import: objects that live as long as the process."""
    names: set[str] = set()
    for node in _module_statements(tree.body):
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


def _local_names(scope: ast.AST) -> set[str]:
    """Parameters and plain assignment targets of a function."""
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    args = scope.args
    bound = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, *(a for a in (args.vararg, args.kwarg) if a is not None)]}
    for node in _own_nodes(scope):
        if isinstance(node, ast.Assign):
            bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    return bound


def _id_names(scope: ast.AST, id_helpers: set[str], fixed: frozenset[str]) -> set[str]:
    """Local names bound from an expression containing ``id(...)`` (``key = (id(a), b)``), to a fixed point."""
    names: set[str] = set()
    assigns = [n for n in _own_nodes(scope) if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)]
    changed = True
    while changed:
        changed = False
        for node in assigns:
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                raise TypeError("only simple-name assignments are collected here")
            if target.id not in names and _has_id_call(node.value, id_helpers, names, fixed):
                names.add(target.id)
                changed = True
    return names


def _accesses(scope: ast.AST, caches: dict[str, int]) -> Iterator[tuple[str, ast.AST, ast.AST, bool]]:
    """``(cache, node, key expression, is_read)`` for every access of a module cache in this scope."""
    for node in _own_nodes(scope):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in caches:
            yield node.value.id, node, node.slice, isinstance(node.ctx, ast.Load)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in caches:
            if node.func.attr in _READ_METHODS and node.args:
                yield node.func.value.id, node, node.args[0], True
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
            right = node.comparators[0]
            if isinstance(right, ast.Name) and right.id in caches:
                yield right.id, node, node.left, True


def _checks_identity(scope: ast.AST) -> bool:
    """Does the scope compare something with ``is``/``is not`` other than against ``None``/``True``/``False``?"""
    for node in _own_nodes(scope):
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
            sides = [node.left, *node.comparators]
            if not any(isinstance(s, ast.Constant) and (s.value is None or isinstance(s.value, bool)) for s in sides):
                return True
    return False


def _shadowed(scope: ast.AST, caches: dict[str, int]) -> set[str]:
    """Cache names rebound locally in a function (a parameter or plain assignment, no ``global``): a different object."""
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    declared = {n for node in _own_nodes(scope) if isinstance(node, ast.Global) for n in node.names}
    args = scope.args
    bound = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    bound |= {t.id for node in _own_nodes(scope) if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)}
    return (bound & set(caches)) - declared


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, source: str) -> list[Finding]:
    """The findings in one parsed file."""
    caches = _module_caches(tree, aliases)
    if not caches or _uses_weakref(tree, aliases):
        return []
    lines = source.splitlines()
    helpers = _id_helpers(tree)
    module_names = _module_names(tree)
    per_scope = []
    id_keyed: dict[str, list[int]] = {}
    for scope in _scopes(tree):
        fixed = frozenset(module_names - _local_names(scope))
        names = _id_names(scope, helpers, fixed)
        live = {k: v for k, v in caches.items() if k not in _shadowed(scope, caches)}
        accesses = list(_accesses(scope, live))
        per_scope.append((scope, accesses))
        for cache, node, key, _ in accesses:
            if _has_id_call(key, helpers, names, fixed):
                id_keyed.setdefault(cache, []).append(getattr(node, "lineno", 1))
    out: list[Finding] = []
    for scope, accesses in per_scope:
        if _checks_identity(scope):
            continue
        reported: set[str] = set()
        for cache, node, _key, is_read in sorted(accesses, key=lambda a: getattr(a[1], "lineno", 0)):
            line = getattr(node, "lineno", 1)
            if not is_read or cache not in id_keyed or cache in reported:
                continue
            marked_at = [line, caches[cache], *id_keyed[cache]]
            if any(line_has_marker(lines, at, MARKER) for at in marked_at):
                continue
            reported.add(cache)
            where = getattr(scope, "name", "<module>")
            out.append(
                Finding(
                    rel,
                    line,
                    RULE,
                    f"module cache {cache} is keyed by id(obj) and {where}() reads it without checking that the entry's object is obj; "
                    "a new object allocated at a freed address gets the old entry",
                )
            )
    return out


def find_id_keyed_cache_validates_identity(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every read of an ``id()``-keyed module cache with no identity check, under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_id_keyed_cache_validates_identity(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_id_keyed_cache_validates_identity(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "store a weakref to the keyed object (weakref.ref/finalize) or compare `entry_obj is obj` on lookup; `# id-key-ok: <reason>` when address reuse is harmless"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="id_keyed_cache_validates_identity", refresh_command="PY_CI_SHARED_REFRESH=id_keyed_cache_validates_identity")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} id-keyed-cache-validates-identity finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
