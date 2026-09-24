"""Runtime writes to a module-level registry: an entry added after import is missing in every other process.

A registry filled at import time is rebuilt by every interpreter that imports the module. An entry added later, from inside
a function (a transform discovered at fit time, a plugin registered on first use), exists only in the process that ran that
function: a pickled object that names it fails to load anywhere else. The shipped instance was a composite model whose
auto-discovered chain transform was registered during discovery and raised ``UnknownTransformError`` in a new interpreter.

The check flags every function-scope write to a module-level dict whose name contains ``REGISTRY`` (``X[k] = v``,
``X.setdefault``, ``X.update``, ``X.pop``, ``del X[k]``, also through ``mod.X`` or an import alias), except inside

* a registration helper applied as a decorator, or called in a module-scope statement, somewhere in the scanned files
  (it runs at import), and any function nested in one (a decorator factory's inner ``deco``). The helper is matched
  to the module it is imported from, and a call under ``if __name__ == "__main__":`` does not count (it never runs at
  import), or
* a writer the caller lists in ``replay_writers`` with the reason it is replayed on load (reachable from ``__setstate__``
  or a loader), which is the fix for a genuine runtime registration.

Usage from a repository's meta tests::

    from py_ci_shared.runtime_registry_mutation import assert_writes_have_replay

    def test_runtime_registry_writes_have_a_replay():
        assert_writes_have_replay(files=..., repo_root=REPO_ROOT, replay_writers={"reregister_x": "called from __setstate__"})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import ImportAliases, ParsedFile, ScanResult, scan_python

__all__ = ["RegistryWrite", "find_runtime_registry_writes", "assert_writes_have_replay"]

_MUTATORS = frozenset({"setdefault", "update", "pop", "popitem", "clear", "__setitem__", "__delitem__"})
_DICT_FACTORIES = frozenset({"dict", "OrderedDict", "defaultdict", "Counter", "ChainMap", "WeakValueDictionary", "WeakKeyDictionary"})
_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


class RegistryWrite:
    """One function-scope write: where, which registry, and inside which function."""

    __slots__ = ("function", "lineno", "path", "registry")

    def __init__(self, path: str, function: str, lineno: int, registry: str) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.registry = registry

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.function}() writes {self.registry}"


def _module_level_statements(body: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
    """Module-scope statements, descending into ``if``/``try``/``with``/``for``/``while`` blocks but not into defs."""
    for node in body:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            yield from _module_level_statements(getattr(node, field, None) or [])
        for handler in getattr(node, "handlers", None) or []:
            yield from _module_level_statements(handler.body)


def _is_dict_value(value: ast.expr, aliases: ImportAliases) -> bool:
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return True
    if isinstance(value, ast.Call):
        names = {getattr(value.func, "attr", None) or getattr(value.func, "id", None)}
        qualified = aliases.qualified_name(value)
        if qualified:
            names.add(qualified.rsplit(".", 1)[-1])
        return bool(names & _DICT_FACTORIES)
    return False


def _module_registries(tree: ast.Module) -> set[str]:
    """Names bound at module scope (including inside a module-level ``if``/``try``) to a dict (literal, comprehension,
    ``dict``/``OrderedDict``/``defaultdict``/... however imported) whose name contains ``REGISTRY``."""
    aliases = ImportAliases.from_tree(tree)
    names: set[str] = set()
    for node in _module_level_statements(tree.body):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not _is_dict_value(value, aliases):
            continue
        for t in targets:
            if isinstance(t, ast.Name) and "REGISTRY" in t.id.upper():
                names.add(t.id)
    return names


def _callee_name(node: ast.expr) -> str | None:
    """The called or referenced name of a decorator / call target (``f``, ``mod.f``, ``f(...)``)."""
    target = node.func if isinstance(node, ast.Call) else node
    return target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)


def _is_main_guard(node: ast.stmt) -> bool:
    """``if __name__ == "__main__":`` -- its body runs when the file is executed as a script, never at import."""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    parts = [node.test.left, *node.test.comparators]
    names = {p.id for p in parts if isinstance(p, ast.Name)}
    consts = {p.value for p in parts if isinstance(p, ast.Constant)}
    return "__name__" in names and "__main__" in consts


def _module_path(rel: str) -> list[str]:
    parts = [p for p in rel.split("/") if p]
    if parts:
        parts[-1] = parts[-1].rsplit(".", 1)[0]
        if parts[-1] == "__init__":
            parts.pop()
    return parts


class _Helper:
    """A callable run at import: its bare name, and the module it resolves to (``None`` when it is not an import)."""

    __slots__ = ("method", "module", "name", "rel")

    def __init__(self, name: str, module: Optional[list[str]], rel: str, method: bool) -> None:
        self.name, self.module, self.rel, self.method = name, module, rel, method


def _helper_of(node: ast.expr, aliases: ImportAliases, rel: str) -> Optional[_Helper]:
    target = node.func if isinstance(node, ast.Call) else node
    name = _callee_name(target)
    if not name:
        return None
    head: ast.expr = target
    while isinstance(head, ast.Attribute):
        head = head.value
    if isinstance(target, ast.Name) and target.id not in aliases:
        return _Helper(name, None, rel, method=False)  # a function of this same module
    if isinstance(head, ast.Name) and head.id in aliases:
        qualified = aliases.qualified_name(target) or name
        module = [p for p in qualified.lstrip(".").split(".")[:-1] if p]
        return _Helper(name, module, rel, method=False)
    return _Helper(name, None, rel, method=True)  # `obj.register(...)` on a module-level object: a method


def _import_time_helpers(trees: Iterable[Union[ast.Module, tuple[str, ast.Module]]]) -> list[_Helper]:
    """Callables used as decorators on module-scope definitions, or called in a module-scope statement (a
    ``__main__`` guard excluded): they run at import."""
    out: list[_Helper] = []
    for item in trees:
        rel, tree = item if isinstance(item, tuple) else ("", item)
        aliases = ImportAliases.from_tree(tree)
        main_bodies: set[int] = set()
        for node in _module_level_statements(tree.body):
            if _is_main_guard(node):
                main_bodies.update(id(n) for n in ast.walk(node))
                continue
            if id(node) in main_bodies:
                continue
            for deco in getattr(node, "decorator_list", ()):
                helper = _helper_of(deco, aliases, rel)
                if helper:
                    out.append(helper)
            if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        helper = _helper_of(sub, aliases, rel)
                        if helper:
                            out.append(helper)
    return out


def _runs_at_import(func: _FunctionNode, rel: str, in_class: bool, helpers: list[_Helper]) -> bool:
    mine = _module_path(rel)
    for h in helpers:
        if h.name != func.name:
            continue
        if h.method:
            if in_class:
                return True
            continue
        if h.module is None:
            if h.rel == rel and not in_class:
                return True
            continue
        k = min(len(h.module), len(mine))
        if not in_class and k and h.module[-k:] == mine[-k:]:
            return True
    return False


def _registry_names(node: ast.expr, registries: set[str], local_aliases: Mapping[str, str]) -> Optional[str]:
    """The registry *node* refers to: ``_REGISTRY``, an import alias of it, or ``mod._REGISTRY``."""
    if isinstance(node, ast.Name):
        if node.id in registries:
            return node.id
        return local_aliases.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr in registries:
        return node.attr
    return None


def _writes_in(func: ast.AST, registries: set[str], local_aliases: Optional[Mapping[str, str]] = None) -> list[tuple[int, str]]:
    """``(line, registry)`` for every write to one of ``registries`` inside ``func`` (not in nested defs)."""
    aliases = local_aliases or {}
    hits: list[tuple[int, str]] = []
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            reg = _registry_names(node.value, registries, aliases)
            if reg:
                hits.append((node.lineno, reg))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATORS:
            reg = _registry_names(node.func.value, registries, aliases)
            if reg:
                hits.append((node.lineno, reg))
    return hits


def _import_aliases_of(tree: ast.Module, registries: set[str]) -> dict[str, str]:
    """``{"REG": "_REGISTRY"}`` for ``from x import _REGISTRY as REG``."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in registries and alias.asname:
                    out[alias.asname] = alias.name
    return out


def _functions(tree: ast.Module) -> Iterator[tuple[_FunctionNode, bool, list[_FunctionNode]]]:
    """``(function, defined_in_a_class, enclosing functions outermost first)`` for every function in *tree*."""

    def visit(node: ast.AST, in_class: bool, outer: list[_FunctionNode]) -> Iterator[tuple[_FunctionNode, bool, list[_FunctionNode]]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield child, in_class, outer
                yield from visit(child, False, [*outer, child])
            elif isinstance(child, ast.ClassDef):
                yield from visit(child, True, outer)
            else:
                yield from visit(child, in_class, outer)

    yield from visit(tree, False, [])


def _scan(files: Iterable[Path], repo_root: Path) -> ScanResult:
    return scan_python([Path(p) for p in files], root=repo_root)


def _writes(parsed: list[ParsedFile]) -> list[RegistryWrite]:
    helpers = _import_time_helpers((f.rel, f.tree) for f in parsed)
    # A registry is usually written through an import (``from .registry import _REGISTRY``), so every module-level
    # ``*REGISTRY*`` dict defined anywhere in the scanned files counts in every file.
    registries: set[str] = set().union(*(_module_registries(f.tree) for f in parsed)) if parsed else set()
    writes: list[RegistryWrite] = []
    if not registries:
        return writes
    for f in parsed:
        local_aliases = _import_aliases_of(f.tree, registries)
        exempt: set[int] = set()
        for func, in_class, outer in _functions(f.tree):
            # A decorator factory's inner function runs when the (import-time) factory applies it.
            if _runs_at_import(func, f.rel, in_class, helpers) or any(id(o) in exempt for o in outer):
                exempt.add(id(func))
                continue
            writes += [RegistryWrite(f.rel, func.name, line, reg) for line, reg in _writes_in(func, registries, local_aliases)]
    return sorted(writes, key=lambda w: (w.path, w.lineno))


def find_runtime_registry_writes(files: Iterable[Path], repo_root: Path, *, allow_unparsed: bool = False) -> list[RegistryWrite]:
    """Every function-scope write to a module-level ``*REGISTRY*`` dict (defined in any scanned file), outside
    import-time helpers and the functions nested in them. A file outside *repo_root* is keyed by its absolute path.
    Raises ``UnparsedFilesError`` (an ``AssertionError``) when a file cannot be parsed, unless *allow_unparsed*."""
    scan = _scan(files, repo_root)
    if not allow_unparsed:
        scan.check_unparsed()
    return _writes(scan.files)


def assert_writes_have_replay(files: Iterable[Path], repo_root: Path, replay_writers: Mapping[str, str], min_files: int = 1) -> None:
    """Fail on a runtime registry write whose function is neither an import-time helper nor a listed replay writer.

    ``replay_writers`` maps a function name to the reason its registrations are replayed on load; an empty reason is
    rejected, and a listed writer that no longer writes anything must be removed. Fewer than *min_files* PARSED files,
    or any file that cannot be parsed, fails too.
    """
    scan = _scan(files, repo_root)
    scan.min_files = min_files
    try:
        scan.check_floor()
    except AssertionError as exc:
        raise AssertionError(f"the scan lost its subject: {exc}") from exc
    scan.check_unparsed()
    empty = sorted(k for k, v in replay_writers.items() if not str(v).strip())
    if empty:
        raise AssertionError(f"replay writers need a reason: {empty}")
    writes = _writes(scan.files)
    bad = [w for w in writes if w.function not in replay_writers]
    stale = sorted(set(replay_writers) - {w.function for w in writes})
    msgs = []
    if bad:
        msgs.append(
            "runtime writes to a module registry with no load-time replay (the entry will be missing in any other "
            "process that loads an object naming it): " + "; ".join(map(repr, bad))
        )
    if stale:
        msgs.append(f"listed replay writers that no longer write a registry: {stale}")
    if msgs:
        raise AssertionError("\n".join(msgs))
