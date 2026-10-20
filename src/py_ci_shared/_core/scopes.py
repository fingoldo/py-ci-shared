"""Scope-aware traversal, computed once per tree and shared by every gate.

A gate that judges what a function does (what it binds, returns, assigns) needs the nodes that execute in that function's OWN body:
nested function, class and lambda bodies are other scopes. Written by hand that is a recursive walk per gate, and per function it
re-walks the body again for every question asked of it; on a large tree it was the single most expensive thing a gate did
(``shared_mutable_placeholder``: 180 s of a 324 s run, three body walks per function). :func:`iter_scopes` walks each scope's own nodes
exactly once per tree and memoises the result next to the tree (:func:`~py_ci_shared._core.node_index.tree_memo`, weakly keyed, so it is
freed with the tree), and :func:`scope_nodes` hands a single scope's nodes out of that same memo.

The nodes of a scope are in no particular order. A nested definition node itself belongs to the scope it is written in (so ``bound``
names and decorators can be read), its body to the scope it opens. Class bodies are scopes of their own here, and a function defined in
a class is a child of that class's scope, so a gate can follow enclosing names down any nesting.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from typing import NamedTuple, Optional, Union

from .node_index import child_nodes, tree_memo

__all__ = ["SCOPE_TYPES", "Scope", "iter_scopes", "qualified_functions", "scope_nodes"]

#: Node types that open a scope of their own (a lambda opens one too, but holds no statements, so it is only a boundary).
SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_Opener = Union[ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]


class Scope(NamedTuple):
    """One scope of a tree: its definition node, the nodes that execute in its own body, and the scope it is written in.

    ``node`` and ``parent`` are ``None`` for the module scope: the memo lives next to the tree and a value holding its own key would keep it alive forever,
    so the caller, which has the tree, reads the module as ``tree``.
    """

    node: "Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]]"
    nodes: "list[ast.AST]"
    parent: "Optional[Scope]"


def _own_nodes(opener: _Opener) -> "list[ast.AST]":
    """Nodes that execute in *opener*'s own body; nested function, class and lambda bodies are other scopes (the definition node is kept)."""
    out: "list[ast.AST]" = []
    stack: "list[ast.AST]" = list(opener.body) if isinstance(opener, ast.Module) else list(ast.iter_child_nodes(opener))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, SCOPE_TYPES):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def own_nodes(roots: "Iterable[ast.AST]", stops: "tuple[type, ...]") -> "Iterator[ast.AST]":
    """Every node reachable from *roots* without entering a node of a *stops* type; that node itself is still yielded.

    Callers pass the children of a scope (``child_nodes(scope)``) or a list of statements, and the node types that
    are their own scope, so each statement is judged by the function it belongs to.
    """
    stack = list(roots)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, stops):
            stack.extend(child_nodes(node))


def iter_scopes(tree: ast.Module) -> "list[Scope]":
    """Every function, class and module scope of *tree*, parents before children, each scope's own nodes collected once.

    The result is shared (memoised on the tree): do not mutate it or the node lists in it.
    """

    def build() -> "list[Scope]":
        root = Scope(None, _own_nodes(tree), None)
        scopes = [root]
        todo = [root]
        while todo:
            current = todo.pop()
            for node in current.nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    child = Scope(node, _own_nodes(node), current)
                    scopes.append(child)
                    todo.append(child)
        return scopes

    return tree_memo(tree, "py_ci_shared.scopes", build)


def scope_nodes(tree: ast.Module, opener: _Opener) -> "list[ast.AST]":
    """The nodes that execute in *opener*'s own body (see :func:`iter_scopes`); *opener* must be the module or a definition inside *tree*."""
    for scope in iter_scopes(tree):
        if scope.node is opener or (scope.node is None and opener is tree):
            return scope.nodes
    raise ValueError("opener is not a scope of this tree")


def qualified_functions(tree: ast.Module, *, locals_marker: bool = True) -> "list[tuple[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]]]":
    """``(qualified name, definition)`` of every function in *tree*, in source order: methods as ``Class.method``, nested functions as ``outer.<locals>.inner``.

    With ``locals_marker=False`` a nested function is ``outer.inner`` (the spelling some gates match against). Computed once per tree from :func:`iter_scopes` and
    shared (do not mutate); many gates each carried their own copy of this recursion.
    """

    def build() -> "list[tuple[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]]]":
        inner = ".<locals>." if locals_marker else "."
        prefix: "dict[int, str]" = {}
        out: "list[tuple[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]]]" = []
        for scope in iter_scopes(tree):
            node = scope.node
            if node is None:
                prefix[id(scope)] = ""
                continue
            above = prefix[id(scope.parent)]  # type: ignore[index]  # parents come before children
            if isinstance(node, ast.ClassDef):
                prefix[id(scope)] = f"{above}{node.name}."
            else:
                qual = f"{above}{node.name}"
                out.append((qual, node))
                prefix[id(scope)] = f"{qual}{inner}"
        out.sort(key=lambda item: (item[1].lineno, item[1].col_offset))
        return out

    return tree_memo(tree, ("py_ci_shared.qualified_functions", locals_marker), build)
