"""Runtime writes to a module-level registry: an entry added after import is missing in every other process.

A registry filled at import time is rebuilt by every interpreter that imports the module. An entry added later, from inside
a function (a transform discovered at fit time, a plugin registered on first use), exists only in the process that ran that
function: a pickled object that names it fails to load anywhere else. The shipped instance was a composite model whose
auto-discovered chain transform was registered during discovery and raised ``UnknownTransformError`` in a new interpreter.

The check flags every function-scope write to a module-level dict whose name contains ``REGISTRY`` (``X[k] = v``,
``X.setdefault``, ``X.update``, ``X.pop``, ``del X[k]``), except inside

* a registration helper applied as a decorator, or called in a module-scope statement, somewhere in the scanned files
  (it runs at import), or
* a writer the caller lists in ``replay_writers`` with the reason it is replayed on load (reachable from ``__setstate__``
  or a loader), which is the fix for a genuine runtime registration.

Usage from a repository's meta tests::

    from py_ci_shared.runtime_registry_mutation import assert_writes_have_replay

    def test_runtime_registry_writes_have_a_replay():
        assert_writes_have_replay(files=..., repo_root=REPO_ROOT, replay_writers={"reregister_x": "called from __setstate__"})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

__all__ = ["RegistryWrite", "find_runtime_registry_writes", "assert_writes_have_replay"]

_MUTATORS = frozenset({"setdefault", "update", "pop", "popitem", "clear", "__setitem__", "__delitem__"})


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


def _module_registries(tree: ast.Module) -> set[str]:
    """Names bound at module scope to a dict (literal, ``dict(...)`` or annotated) whose name contains ``REGISTRY``."""
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        is_dict = isinstance(value, (ast.Dict, ast.DictComp)) or (isinstance(value, ast.Call) and getattr(value.func, "id", "") in {"dict", "OrderedDict", "defaultdict"})
        for t in targets:
            if isinstance(t, ast.Name) and "REGISTRY" in t.id.upper() and is_dict:
                names.add(t.id)
    return names


def _callee_name(node: ast.expr) -> str | None:
    """The called or referenced name of a decorator / call target (``f``, ``mod.f``, ``f(...)``)."""
    target = node.func if isinstance(node, ast.Call) else node
    return target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)


def _import_time_helpers(trees: Iterable[ast.Module]) -> set[str]:
    """Names used as decorators on module-scope definitions, or called in a module-scope statement: they run at import."""
    out: set[str] = set()
    for tree in trees:
        for node in tree.body:
            for deco in getattr(node, "decorator_list", ()):
                name = _callee_name(deco)
                if name:
                    out.add(name)
            # A bare module-scope call statement (``register_metric("rmse", ...)``) or loop of them registers at import.
            for sub in ast.walk(node) if isinstance(node, (ast.Expr, ast.For, ast.If, ast.With)) else ():
                if isinstance(sub, ast.Call):
                    name = _callee_name(sub)
                    if name:
                        out.add(name)
    return out


def _writes_in(func: ast.AST, registries: set[str]) -> list[tuple[int, str]]:
    """``(line, registry)`` for every write to one of ``registries`` inside ``func`` (not in nested defs)."""
    hits: list[tuple[int, str]] = []
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.value, ast.Name) and node.value.id in registries:
            hits.append((node.lineno, node.value.id))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATORS
              and isinstance(node.func.value, ast.Name) and node.func.value.id in registries):
            hits.append((node.lineno, node.func.value.id))
    return hits


def find_runtime_registry_writes(files: Iterable[Path], repo_root: Path) -> list[RegistryWrite]:
    """Every function-scope write to a module-level ``*REGISTRY*`` dict (defined in any scanned file), outside import-time decorators."""
    parsed: list[tuple[str, ast.Module]] = []
    for path in files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        parsed.append((Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix(), tree))
    import_time = _import_time_helpers(tree for _p, tree in parsed)
    # A registry is usually written through an import (``from .registry import _REGISTRY``), so every module-level
    # ``*REGISTRY*`` dict defined anywhere in the scanned files counts in every file.
    registries = set().union(*(_module_registries(tree) for _p, tree in parsed)) if parsed else set()
    writes: list[RegistryWrite] = []
    if not registries:
        return writes
    for rel, tree in parsed:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in import_time:
                writes += [RegistryWrite(rel, node.name, line, reg) for line, reg in _writes_in(node, registries)]
    return sorted(writes, key=lambda w: (w.path, w.lineno))


def assert_writes_have_replay(files: Iterable[Path], repo_root: Path, replay_writers: Mapping[str, str], min_files: int = 1) -> None:
    """Fail on a runtime registry write whose function is neither an import-time decorator nor a listed replay writer.

    ``replay_writers`` maps a function name to the reason its registrations are replayed on load; an empty reason is
    rejected, and a listed writer that no longer writes anything must be removed.
    """
    files = list(files)
    if len(files) < min_files:
        raise AssertionError(f"scanned only {len(files)} files (< {min_files}); the scan lost its subject")
    empty = sorted(k for k, v in replay_writers.items() if not str(v).strip())
    if empty:
        raise AssertionError(f"replay writers need a reason: {empty}")
    writes = find_runtime_registry_writes(files, repo_root)
    bad = [w for w in writes if w.function not in replay_writers]
    stale = sorted(set(replay_writers) - {w.function for w in writes})
    msgs = []
    if bad:
        msgs.append("runtime writes to a module registry with no load-time replay (the entry will be missing in any other "
                    "process that loads an object naming it): " + "; ".join(map(repr, bad)))
    if stale:
        msgs.append(f"listed replay writers that no longer write a registry: {stale}")
    if msgs:
        raise AssertionError("\n".join(msgs))
