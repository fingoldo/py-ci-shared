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


class ImportAliases:
    """``local name -> qualified dotted target`` for one module."""

    def __init__(self, mapping: Optional[dict[str, str]] = None, *, tree: Optional[ast.AST] = None, package: Optional[str] = None) -> None:
        self.mapping: dict[str, str] = dict(mapping or {})
        #: Set only by ``from_tree(..., scoped=True)``: resolution then follows Python's scoping for nodes of that tree.
        self._scoped_tree: Optional[ast.AST] = tree
        self._package = package
        self._parents: "Optional[dict[int, ast.AST]]" = None
        self._scope_bindings: "dict[int, dict[str, Optional[str]]]" = {}

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

    def _bindings_of(self, scope: ast.AST) -> "dict[str, Optional[str]]":
        """``name -> qualified target`` bound in *scope*'s own body; ``None`` marks a shadowing, non-resolvable binding."""
        cached = self._scope_bindings.get(id(scope))
        if cached is not None:
            return cached
        imports: dict[str, str] = {}
        others: "dict[str, list[Optional[ast.expr]]]" = {}

        def other(name: str, value: "Optional[ast.expr]") -> None:
            others.setdefault(name, []).append(value)

        def targets(t: ast.AST, value: "Optional[ast.expr]") -> None:
            if isinstance(t, ast.Name):
                other(t.id, value)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in t.elts:
                    targets(e, None)
            elif isinstance(t, ast.Starred):
                targets(t.value, None)

        stack: "list[ast.AST]"
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = scope.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []), *([a.kwarg] if a.kwarg else [])]:
                other(arg.arg, None)
        if isinstance(scope, ast.Lambda):
            stack = [scope.body]
        else:
            stack = list(scope.body)[::-1]  # type: ignore[attr-defined]
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                other(n.name, None)
                continue  # its body is another scope
            if isinstance(n, ast.Lambda):
                continue
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                imports.update(self._collect([n], self._package))
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    targets(t, n.value)
            elif isinstance(n, ast.AnnAssign):
                targets(n.target, n.value)
            elif isinstance(n, ast.AugAssign):
                targets(n.target, None)
            elif isinstance(n, (ast.For, ast.AsyncFor)):
                targets(n.target, None)
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                for item in n.items:
                    if item.optional_vars is not None:
                        targets(item.optional_vars, None)
            elif isinstance(n, ast.NamedExpr):
                targets(n.target, None)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                other(n.name, None)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                for g in n.names:
                    imports.setdefault(g, self.mapping.get(g, g))  # declared elsewhere: not a local shadow
            stack.extend(reversed(list(ast.iter_child_nodes(n))))
        out: "dict[str, Optional[str]]" = dict(imports)  # an import beats a plain rebind (`try: import x` / `except: x = None`)
        self._scope_bindings[id(scope)] = out  # registered before alias resolution, which may look names up in this scope
        for name, values in others.items():
            if name in imports:
                continue
            one = values[0] if len(values) == 1 else None
            resolved: Optional[str] = None
            if one is not None and isinstance(one, (ast.Name, ast.Attribute)):
                resolved = self._alias_of(one, name)
            out[name] = resolved
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
