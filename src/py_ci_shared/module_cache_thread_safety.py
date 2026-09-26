"""Module-level caches mutated with no lock, and lazy ``from X import`` inside joblib-dispatched workers.

Two concurrency defects that recur in code run under ``joblib`` with ``backend="threading"``:

* ``unlocked-cache``: a module-level dict whose name says it is a cache (``_CACHE``, ``_memo``...) is updated by a
  NON-ATOMIC sequence inside a synchronous function, and the module never constructs a ``Lock``/``RLock``/
  ``Semaphore``/``Condition``. Non-atomic means an insert together with an eviction (``pop``/``popitem``/``del``/
  ``move_to_end``/``clear``) in one function, or a read-modify-write of a value held in the cache (``C[k] += 1``,
  ``C.setdefault(k, []).append(x)``). Two threads doing get-or-compute-or-evict lose updates, raise ``KeyError`` from
  ``popitem`` on an emptied dict, or on Windows double-close a cached handle; the same shape was fixed three separate
  times in one codebase before a check existed. A lone memo insert (an idempotent value, one atomic store under the
  GIL), a lone ``clear()`` reset hook, writes at import, and ``async def`` writers (one event loop) are not findings;
  ``strict=True`` flags every function-scope write instead. A module that constructs any lock is exempt: whether that
  lock covers the whole sequence is left to review.
* ``lazy-import-in-delayed``: a function passed to ``delayed(...)`` (joblib, dask) runs ``from X import name`` in its
  body or in a nested function. Two threads importing a partially initialised module can leave the name unbound;
  the shipped instance raised ``NameError`` inside a fold, the caller's ``except Exception`` swallowed it, and the
  fold silently returned NaN. ``import X`` is not flagged. A line carrying ``# joblib-import-race-ok`` is an
  explicit opt-out (the import touches an already-loaded module, or the backend is processes).

Usage::

    from py_ci_shared.module_cache_thread_safety import assert_thread_safe_module_caches

    def test_module_caches_are_thread_safe():
        assert_thread_safe_module_caches(REPO / "src", baseline_path=HERE / "_module_cache_baseline.json")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._core.node_index import walk as _fast_walk
from ._gate_report import line_has_marker, report, scan_tree, skip_set

__all__ = ["RULE_CACHE", "RULE_IMPORT", "REFRESH_FLAG", "find_thread_unsafe_module_state", "assert_thread_safe_module_caches"]

RULE_CACHE = "unlocked-cache"
RULE_IMPORT = "lazy-import-in-delayed"
REFRESH_FLAG = "--refresh-module-cache-baseline"
IMPORT_MARKER = "joblib-import-race-ok"
DEFAULT_CACHE_NAME = r"cache|memo"
_DICT_FACTORIES = frozenset({"dict", "OrderedDict", "defaultdict", "Counter", "WeakValueDictionary", "WeakKeyDictionary", "LRUCache", "TTLCache"})
_MUTATORS = frozenset({"setdefault", "update", "pop", "popitem", "clear", "__setitem__", "__delitem__", "move_to_end"})
_LOCKS = frozenset({"Lock", "RLock", "Semaphore", "BoundedSemaphore", "Condition"})


def _module_statements(body: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
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
        qualified = aliases.qualified_name(value) or ""
        return qualified.rsplit(".", 1)[-1] in _DICT_FACTORIES
    return False


def _module_caches(tree: ast.Module, aliases: ImportAliases, name_re: re.Pattern[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for node in _module_statements(tree.body):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not _is_dict_value(value, aliases):
            continue
        for t in targets:
            if isinstance(t, ast.Name) and name_re.search(t.id):
                out.setdefault(t.id, node.lineno)
    return out


def _constructs_lock(tree: ast.Module, aliases: ImportAliases) -> bool:
    for node in _fast_walk(tree):
        if isinstance(node, ast.Call):
            qualified = aliases.qualified_name(node) or ""
            if qualified.rsplit(".", 1)[-1] in _LOCKS:
                return True
    return False


def _functions(tree: ast.Module) -> Iterator[Union[ast.FunctionDef, ast.AsyncFunctionDef]]:
    for node in _fast_walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _locally_bound(func: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> set[str]:
    """Names the function binds locally (parameters and plain assignments) that are not declared ``global``."""
    declared_global = {n for node in _fast_walk(func) if isinstance(node, ast.Global) for n in node.names}
    args = func.args
    params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    params |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    assigned = {t.id for node in _fast_walk(func) if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)}
    return (params | assigned) - declared_global


_INSERTS = frozenset({"setdefault", "update", "__setitem__"})
_REMOVES = frozenset({"pop", "popitem", "__delitem__", "move_to_end"})
_ELEMENT_MUTATORS = frozenset({"append", "extend", "add", "update", "insert", "remove", "discard", "pop", "setdefault", "clear", "__setitem__"})


def _own_nodes(func: ast.AST) -> Iterator[ast.AST]:
    """Nodes of *func* without descending into nested functions, classes or lambdas (they are judged on their own)."""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _cache_of(node: ast.expr, caches: dict[str, int]) -> Optional[str]:
    """The cache *node* names directly (``C``), or ``None``."""
    return node.id if isinstance(node, ast.Name) and node.id in caches else None


def _element_of(node: ast.expr, caches: dict[str, int], elements: dict[str, str]) -> Optional[str]:
    """The cache whose ELEMENT *node* is: ``C[k]``, ``C.setdefault(k, x)``, ``C.get(k)``, or a local bound to one."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in caches:
        return node.value.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("setdefault", "get"):
        value = node.func.value
        if isinstance(value, ast.Name) and value.id in caches:
            return value.id
    if isinstance(node, ast.Name):
        return elements.get(node.id)
    return None


def _operations(func: ast.AST, caches: dict[str, int], declared_global: set[str]) -> dict[str, set[str]]:
    """``{cache: {kinds}}`` written by *func*: ``insert``, ``remove``, ``clear``, ``element`` (read-modify-write of a
    value held in the cache) and ``rebind`` (``global C; C = ...``)."""
    ops: dict[str, set[str]] = {}
    elements: dict[str, str] = {}
    nodes = list(_own_nodes(func))
    for node in nodes:  # locals bound to an element: `sizes = C.setdefault(label, {})`
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            owner = _element_of(node.value, caches, {})
            if owner is not None:
                elements[node.targets[0].id] = owner
    for node in nodes:
        owner, kind = _classify(node, caches, elements, declared_global)
        if owner is not None and kind is not None:
            ops.setdefault(owner, set()).add(kind)
    return ops


def _classify(node: ast.AST, caches: dict[str, int], elements: dict[str, str], declared_global: set[str]) -> tuple[Optional[str], Optional[str]]:
    """``(cache, kind)`` of one write node, or ``(None, None)``."""
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
        direct = _cache_of(node.value, caches)
        if direct is not None:
            return direct, "insert" if isinstance(node.ctx, ast.Store) else "remove"
        return _element_of(node.value, caches, elements), "element"
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Subscript):
        return _element_of(node.target, caches, elements), "element"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        attr, value = node.func.attr, node.func.value
        direct = _cache_of(value, caches)
        if direct is not None:
            return direct, "insert" if attr in _INSERTS else "remove" if attr in _REMOVES else "clear" if attr == "clear" else None
        if attr in _ELEMENT_MUTATORS:
            return _element_of(value, caches, elements), "element"
        return None, None
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        hit = [t.id for t in targets if isinstance(t, ast.Name) and t.id in declared_global and t.id in caches]
        if hit:
            return hit[0], "rebind"
    return None, None


def _is_racy(kinds: set[str], strict: bool) -> bool:
    """A single insert (a memo filled with an idempotent value) or a lone ``clear()`` (a reset hook) is one atomic
    operation under the GIL; a sequence (insert plus evict) or a read-modify-write of a held value is not."""
    if strict:
        return bool(kinds - {"clear"})
    return "element" in kinds or ("insert" in kinds and bool(kinds & {"remove", "clear"})) or ("rebind" in kinds and len(kinds) > 1)


def _writers(tree: ast.Module, caches: dict[str, int], strict: bool) -> dict[str, list[str]]:
    """``{cache: [functions whose writes to it race]}``. ``async def`` functions are skipped: an event loop runs
    one coroutine step at a time, so their writes cannot interleave with each other mid-statement."""
    out: dict[str, list[str]] = {}
    for func in _functions(tree):
        if isinstance(func, ast.AsyncFunctionDef):
            continue
        declared_global = {n for node in _fast_walk(func) if isinstance(node, ast.Global) for n in node.names}
        shadowed = _locally_bound(func)
        live = {name: line for name, line in caches.items() if name not in shadowed or name in declared_global}
        for name, kinds in _operations(func, live, declared_global).items():
            if _is_racy(kinds, strict) and func.name not in out.get(name, []):
                out.setdefault(name, []).append(func.name)
    return out


def _cache_findings(parsed: ParsedFile, aliases: ImportAliases, name_re: re.Pattern[str], strict: bool) -> list[Finding]:
    caches = _module_caches(parsed.tree, aliases, name_re)
    if not caches or _constructs_lock(parsed.tree, aliases):
        return []
    out: list[Finding] = []
    for name, funcs in sorted(_writers(parsed.tree, caches, strict).items()):
        shown = ", ".join(sorted(funcs)[:4]) + (", ..." if len(funcs) > 4 else "")
        out.append(
            Finding(parsed.rel, caches[name], RULE_CACHE, f"module-level cache {name} is updated non-atomically in {shown}() and the module has no Lock")
        )
    return out


def _delayed_callees(tree: ast.Module, aliases: ImportAliases) -> set[str]:
    out: set[str] = set()
    for node in _fast_walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        qualified = aliases.qualified_name(node) or ""
        if qualified.rsplit(".", 1)[-1] not in ("delayed", "_delayed"):
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            out.add(first.id)
        elif isinstance(first, ast.Attribute):
            out.add(first.attr)
    return out


def _import_findings(parsed: ParsedFile, aliases: ImportAliases) -> list[Finding]:
    callees = _delayed_callees(parsed.tree, aliases)
    if not callees:
        return []
    lines = parsed.source.splitlines()
    out: list[Finding] = []
    seen: set[int] = set()
    for func in _functions(parsed.tree):
        if func.name not in callees:
            continue
        for node in _fast_walk(func):
            if not isinstance(node, ast.ImportFrom) or node.module == "__future__" or id(node) in seen:
                continue
            seen.add(id(node))
            if line_has_marker(lines, node.lineno, IMPORT_MARKER):
                continue
            names = ", ".join(a.name for a in node.names)
            module = "." * node.level + (node.module or "")
            out.append(Finding(parsed.rel, node.lineno, RULE_IMPORT, f"{func.name}() is passed to delayed() and runs `from {module} import {names}` per task"))
    return out


def _collect(
    root: Union[str, Path], *, cache_name_pattern: str, strict: bool, skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]
) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    name_re = re.compile(cache_name_pattern, re.IGNORECASE)
    findings: list[Finding] = []
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        findings += _cache_findings(parsed, aliases, name_re, strict) + _import_findings(parsed, aliases)
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule)), scan


def find_thread_unsafe_module_state(
    root: Union[str, Path],
    *,
    cache_name_pattern: str = DEFAULT_CACHE_NAME,
    strict: bool = False,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every unlocked module cache written from a function and every ``from X import`` inside a ``delayed()`` callee
    under *root*, plus one ``unparsed-file`` finding per file that could not be parsed."""
    findings, scan = _collect(
        root, cache_name_pattern=cache_name_pattern, strict=strict, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git
    )
    return findings + scan.unparsed_findings()


def assert_thread_safe_module_caches(
    root: Union[str, Path],
    *,
    cache_name_pattern: str = DEFAULT_CACHE_NAME,
    strict: bool = False,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a finding (new against *baseline_path* when given), on fewer than *min_files* parsed files, and on
    any unparsable file. Refresh with ``--refresh-module-cache-baseline``."""
    findings, scan = _collect(
        root, cache_name_pattern=cache_name_pattern, strict=strict, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git
    )
    report(
        findings,
        gate="module-cache-thread-safety",
        flag=REFRESH_FLAG,
        guidance="guard the whole get-or-compute-or-evict sequence with a threading.Lock, and hoist `from X import` to module scope",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
