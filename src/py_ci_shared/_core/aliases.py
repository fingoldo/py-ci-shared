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

    def __init__(self, mapping: Optional[dict[str, str]] = None) -> None:
        self.mapping: dict[str, str] = dict(mapping or {})

    @classmethod
    def from_tree(cls, tree: ast.AST, *, package: Optional[str] = None) -> "ImportAliases":
        """Collect every import in *tree*. *package* (the module's own package) resolves relative imports;
        without it a relative import is recorded with its leading dots (``.sub.x``) so it stays matchable."""
        from .node_index import nodes_of, tree_memo

        return tree_memo(tree, ("ImportAliases.from_tree", package), lambda: cls(cls._collect(nodes_of(tree, ast.Import, ast.ImportFrom), package)))

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
        head = self.mapping.get(node.id, node.id)
        return ".".join([head, *attrs[::-1]])

    def is_imported(self, local_name: str) -> bool:
        return local_name in self.mapping

    def __contains__(self, local_name: object) -> bool:
        return local_name in self.mapping

    def __repr__(self) -> str:
        return f"ImportAliases({self.mapping!r})"
