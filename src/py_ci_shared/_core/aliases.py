"""Resolve what a name in a module refers to, however it was imported.

Most gates matched a call by its spelling (``node.func.attr == "reload"``, ``Name("patch")``), so ``import
importlib as il; il.reload(m)``, ``from unittest import mock; mock.patch(...)`` or a relative import slipped past
(audit 2026-09-24 systemic item 6). :class:`ImportAliases` maps every name bound by an import in a module to its
fully qualified dotted target; :meth:`ImportAliases.qualified_name` turns a ``Name``/``Attribute`` chain into
that dotted path.

Scope: module-level and nested imports are all collected (a function-local ``import x as y`` binds ``y`` in that
function; collapsing scopes is the usual trade-off for a lint and errs toward finding). Names that no import binds
resolve to themselves, so builtins and locals come back as their own spelling.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional


def resolve_relative(module: Optional[str], level: int, package: Optional[str]) -> Optional[str]:
    """Absolute module for ``from <'.' * level><module> import ...`` inside *package*; ``None`` if it cannot resolve.

    *package* is the importing module's package (for ``pkg/sub/mod.py`` and ``pkg/sub/__init__.py`` alike it is
    ``pkg.sub``). ``level=0`` returns *module* unchanged.
    """
    if level == 0:
        return module
    if not package:
        return None
    parts = package.split(".")
    if level - 1 > len(parts) - 1:
        return None  # beyond the top-level package
    base = parts[: len(parts) - (level - 1)]
    return ".".join(base + ([module] if module else []))


def package_of(path: Path, src_root: Path, root_package: str) -> str:
    """Package of the module at *path* when *src_root* is the directory of *root_package*.

    ``src_root/a/b.py`` -> ``root_package.a``; ``src_root/a/__init__.py`` -> ``root_package.a``.
    """
    try:
        rel = path.parent.relative_to(src_root)
    except ValueError:
        rel = path.resolve().parent.relative_to(src_root.resolve())
    parts = [p for p in rel.parts if p not in ("", ".")]
    return ".".join([root_package, *parts]) if root_package else ".".join(parts)


def module_of(path: Path, src_root: Path, root_package: str) -> str:
    pkg = package_of(path, src_root, root_package)
    return pkg if path.stem == "__init__" else f"{pkg}.{path.stem}"


def _scope_roots(scope: ast.AST) -> "list[ast.AST]":
    """The statements (or the body expression of a lambda) that run in *scope*, last first so ``pop()`` walks in source order."""
    if isinstance(scope, ast.Lambda):
        return [scope.body]
    return list(scope.body)[::-1]  # type: ignore[attr-defined,no-any-return]


def _scope_params(scope: ast.AST) -> "list[str]":
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return []
    a = scope.args
    args = [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []), *([a.kwarg] if a.kwarg else [])]
    return [arg.arg for arg in args]


def _bound_targets(n: ast.AST) -> "list[tuple[ast.AST, Optional[ast.expr]]]":
    """``(target, assigned value or None)`` pairs the statement *n* binds (assignment, loop, ``with`` and walrus targets)."""
    if isinstance(n, ast.Assign):
        return [(t, n.value) for t in n.targets]
    if isinstance(n, ast.AnnAssign):
        return [(n.target, n.value)]
    if isinstance(n, (ast.AugAssign, ast.For, ast.AsyncFor, ast.NamedExpr)):
        return [(n.target, None)]
    if isinstance(n, (ast.With, ast.AsyncWith)):
        return [(item.optional_vars, None) for item in n.items if item.optional_vars is not None]
    if isinstance(n, ast.ExceptHandler) and n.name:
        return [(ast.Name(id=n.name, ctx=ast.Store()), None)]
    return []


def _target_names(target: ast.AST, value: "Optional[ast.expr]") -> "list[tuple[str, Optional[ast.expr]]]":
    """``(name, assigned value or None)`` for each name an assignment target binds (tuple and star targets carry no single value)."""
    if isinstance(target, ast.Name):
        return [(target.id, value)]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [pair for e in target.elts for pair in _target_names(e, None)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value, None)
    return []


class ImportAliases:
    """``local name -> qualified dotted target`` for one module."""

    def __init__(self, mapping: Optional[dict[str, str]] = None, *, tree: Optional[ast.AST] = None, package: Optional[str] = None) -> None:
        self.mapping: dict[str, str] = dict(mapping or {})
        #: Set only by ``from_tree(..., scoped=True)``: resolution then follows Python's scoping for nodes of that tree.
        self._scoped_tree: Optional[ast.AST] = tree
        self._package = package
        self._parents: "Optional[dict[int, ast.AST]]" = None
        self._scope_bindings: "dict[int, dict[str, Optional[str]]]" = {}
        self._chains: "dict[int, Optional[list[ast.AST]]]" = {}  # a chain depends only on the tree: computed once per node

    @classmethod
    def from_tree(cls, tree: ast.AST, *, package: Optional[str] = None, scoped: bool = False) -> "ImportAliases":
        """Collect every import in *tree*. *package* (the module's own package) resolves relative imports;
        without it a relative import is recorded with its leading dots (``.sub.x``) so it stays matchable.

        ``scoped=True`` makes :meth:`qualified_name` follow Python's scoping for nodes of *tree*: an import binds only the
        scope it is written in, and a parameter or a non-import assignment of the same name in a nearer scope SHADOWS the
        import (the name then resolves to ``None``, unless it is a one-hop alias such as ``p = pickle``). The flat default
        errs toward finding (a stray import anywhere binds the name everywhere) and is kept for existing callers."""
        from .node_index import nodes_of, tree_memo

        def build() -> "ImportAliases":
            return cls(cls._collect(nodes_of(tree, ast.Import, ast.ImportFrom), package), tree=tree if scoped else None, package=package)

        return tree_memo(tree, ("ImportAliases.from_tree", package, scoped), build)

    @staticmethod
    def _collect(imports: "list[ast.AST]", package: Optional[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for node in imports:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        out[alias.asname] = alias.name
                    else:
                        head = alias.name.split(".", 1)[0]
                        out[head] = head  # `import a.b.c` binds `a`; the attribute chain supplies `.b.c`
            elif isinstance(node, ast.ImportFrom):
                base = resolve_relative(node.module, node.level, package)
                if base is None:
                    base = "." * node.level + (node.module or "")
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    sep = "" if base.endswith(".") or not base else "."
                    out[alias.asname or alias.name] = f"{base}{sep}{alias.name}"
        return out

    def qualified_name(self, node: ast.AST) -> Optional[str]:
        """Dotted target of a ``Name``/``Attribute`` chain (``il.reload`` -> ``importlib.reload``); for a ``Call``, of
        its callee. ``None`` for anything else (a subscript, a call result in the middle of the chain, ...)."""
        if isinstance(node, ast.Call):
            node = node.func  # one level only: the callee of `f()()` is a call result, not a name
        attrs = []
        while isinstance(node, ast.Attribute):
            attrs.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        if self._scoped_tree is not None:
            scoped_head = self._resolve_scoped(node)
            if scoped_head is None:
                return None
            return ".".join([scoped_head, *attrs[::-1]])
        head = self.mapping.get(node.id, node.id)
        return ".".join([head, *attrs[::-1]])

    # ---- scope-aware resolution (opt-in, see from_tree(scoped=True)) ------------------------------------------------

    def _parent_map(self) -> "dict[int, ast.AST]":
        if self._parents is None:
            parents: "dict[int, ast.AST]" = {}
            assert self._scoped_tree is not None
            stack: "list[ast.AST]" = [self._scoped_tree]
            while stack:
                cur = stack.pop()
                for child in ast.iter_child_nodes(cur):
                    parents[id(child)] = cur
                    stack.append(child)
            self._parents = parents
        return self._parents

    def _enclosing_scopes(self, node: ast.AST) -> "Optional[list[ast.AST]]":
        key = id(node)
        if key not in self._chains:
            self._chains[key] = self._enclosing_chain(node)
        return self._chains[key]

    def _enclosing_chain(self, node: ast.AST) -> "Optional[list[ast.AST]]":
        """Scopes whose body holds *node*, innermost first, ending with the module; ``None`` when *node* is not in the tree.

        A decorator, default or annotation belongs to the scope AROUND the function, a body statement to the function."""
        parents = self._parent_map()
        if id(node) not in parents:
            return None
        chain: "list[ast.AST]" = []
        child: ast.AST = node
        cur: Optional[ast.AST] = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, ast.Lambda):
                if child is cur.body:
                    chain.append(cur)
            elif isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if any(child is stmt for stmt in cur.body):
                    chain.append(cur)
            elif isinstance(cur, ast.Module):
                chain.append(cur)
            child = cur
            cur = parents.get(id(cur))
        return chain

    def _scan_scope(self, scope: ast.AST) -> "tuple[dict[str, str], dict[str, list[Optional[ast.expr]]]]":
        """``(imports, other bindings)`` of *scope*'s own body: name -> target for imports, name -> assigned values for the rest."""
        imports: dict[str, str] = {}
        others: "dict[str, list[Optional[ast.expr]]]" = {}
        for arg in _scope_params(scope):
            others.setdefault(arg, []).append(None)
        stack: "list[ast.AST]" = _scope_roots(scope)
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                others.setdefault(n.name, []).append(None)
                continue  # its body is another scope
            if isinstance(n, ast.Lambda):
                continue
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                imports.update(self._collect([n], self._package))
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                for g in n.names:
                    imports.setdefault(g, self.mapping.get(g, g))  # declared elsewhere: not a local shadow
            else:
                for target, value in _bound_targets(n):
                    for name, bound in _target_names(target, value):
                        others.setdefault(name, []).append(bound)
            stack.extend(reversed(list(ast.iter_child_nodes(n))))
        return imports, others

    def _bindings_of(self, scope: ast.AST) -> "dict[str, Optional[str]]":
        """``name -> qualified target`` bound in *scope*'s own body; ``None`` marks a shadowing, non-resolvable binding."""
        cached = self._scope_bindings.get(id(scope))
        if cached is not None:
            return cached
        imports, others = self._scan_scope(scope)
        out: "dict[str, Optional[str]]" = dict(imports)  # an import beats a plain rebind (`try: import x` / `except: x = None`)
        self._scope_bindings[id(scope)] = out  # registered before alias resolution, which may look names up in this scope
        for name, values in others.items():
            if name in imports:
                continue
            one = values[0] if len(values) == 1 else None
            out[name] = self._alias_of(one, name) if isinstance(one, (ast.Name, ast.Attribute)) else None
        return out

    def _alias_of(self, value: ast.expr, own_name: str) -> Optional[str]:
        """One-hop alias: ``p = pickle`` / ``L = pickle.loads`` resolve to what the right-hand side resolves to."""
        base: ast.AST = value
        while isinstance(base, ast.Attribute):
            base = base.value
        if isinstance(base, ast.Name) and base.id == own_name:
            return None
        attrs: "list[str]" = []
        node: ast.AST = value
        while isinstance(node, ast.Attribute):
            attrs.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        head = self._resolve_scoped(node)
        return None if head is None else ".".join([head, *attrs[::-1]])

    def _resolve_scoped(self, name: ast.Name) -> Optional[str]:
        chain = self._enclosing_scopes(name)
        if chain is None:
            return self.mapping.get(name.id, name.id)
        for i, scope in enumerate(chain):
            if isinstance(scope, ast.ClassDef) and i > 0:
                continue  # a class body is invisible to the functions written inside it
            bound = self._bindings_of(scope)
            if name.id in bound:
                return bound[name.id]
        return name.id  # bound nowhere: a builtin (or a name from `import *`), spelled as itself

    def is_imported(self, local_name: str) -> bool:
        return local_name in self.mapping

    def __contains__(self, local_name: object) -> bool:
        return local_name in self.mapping

    def __repr__(self) -> str:
        return f"ImportAliases({self.mapping!r})"
