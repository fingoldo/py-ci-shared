"""One ``ast.walk`` per tree per process, shared by every gate.

Each gate used to walk every tree itself, several times: on mlframe the unresolved-import check walked 18M nodes
and the marker check 7.7M, most of the 12 minutes a full meta-suite run took. The parse cache already hands every
caller the SAME tree object while the file is unchanged, so what a walk finds can be kept next to it:

* :func:`nodes_of` -- the nodes of the given types, in ``ast.walk`` order, from one walk of the tree;
* :func:`tree_memo` -- any per-tree derived value (a module's import aliases, a file's marker analysis), computed once.

Both are keyed on the tree object and hold a reference to it, so an ``id()`` is never reused for another tree while
its entry exists. Trees are shared and read-only (see :func:`py_ci_shared._core.source.parse_source`); a caller that
mutates one must not use these. :func:`clear` drops everything, and ``clear_parse_cache`` calls it.
"""

from __future__ import annotations

import ast
import heapq
import threading
from collections import deque
from collections.abc import Callable, Hashable, Iterator
from typing import Any, TypeVar, Union, overload

T = TypeVar("T")
_A = TypeVar("_A", bound=ast.AST)
_B = TypeVar("_B", bound=ast.AST)

# id(tree) -> (tree, {node type: ([walk positions], [nodes])})
_INDEX: "dict[int, tuple[ast.AST, dict[type, tuple[list[int], list[ast.AST]]]]]" = {}
# (id(tree), key) -> (tree, value)
_MEMO: "dict[tuple[int, Hashable], tuple[ast.AST, Any]]" = {}
_LOCK = threading.Lock()


_FIELDS: "dict[type, tuple[str, ...]]" = {}


def walk(node: ast.AST) -> "Iterator[ast.AST]":
    """``ast.walk`` with the same breadth-first order, about 1.5x faster: the stdlib version goes through two generator
    layers (``iter_child_nodes`` over ``iter_fields``) per node, and every gate here walks millions of nodes."""
    _ast, fields_of = ast.AST, _FIELDS
    todo = deque([node])
    pop, push, extend = todo.popleft, todo.append, todo.extend
    while todo:
        current = pop()
        fields = fields_of.get(type(current))
        if fields is None:
            fields = fields_of[type(current)] = current._fields
        for name in fields:
            value = getattr(current, name, None)
            if isinstance(value, _ast):
                push(value)
            elif isinstance(value, list):
                extend([item for item in value if isinstance(item, _ast)])
        yield current


def _index(tree: ast.AST) -> "dict[type, tuple[list[int], list[ast.AST]]]":
    with _LOCK:
        hit = _INDEX.get(id(tree))
    if hit is not None and hit[0] is tree:
        return hit[1]
    by_type: "dict[type, tuple[list[int], list[ast.AST]]]" = {}
    for pos, node in enumerate(walk(tree)):
        entry = by_type.get(type(node))
        if entry is None:
            entry = by_type[type(node)] = ([], [])
        entry[0].append(pos)
        entry[1].append(node)
    with _LOCK:
        _INDEX[id(tree)] = (tree, by_type)
    return by_type


@overload
def nodes_of(tree: ast.AST, node_type: "type[_A]", /) -> "list[_A]": ...


@overload
def nodes_of(tree: ast.AST, node_type: "type[_A]", other_type: "type[_B]", /) -> "list[Union[_A, _B]]": ...


@overload
def nodes_of(tree: ast.AST, *types: type) -> "list[ast.AST]": ...


def nodes_of(tree: ast.AST, *types: type) -> "list[Any]":
    """Every node of *tree* that is an instance of one of *types*, in ``ast.walk`` order. Subclasses count, as with
    ``isinstance``. The returned list is shared: do not mutate it."""
    by_type = _index(tree)
    hits = [entry for node_type, entry in by_type.items() if issubclass(node_type, types)]
    if not hits:
        return []
    if len(hits) == 1:
        return hits[0][1]
    return [node for _, node in heapq.merge(*(zip(pos, nodes) for pos, nodes in hits), key=lambda pair: pair[0])]


def tree_memo(tree: ast.AST, key: Hashable, compute: "Callable[[], T]") -> T:
    """``compute()`` for (*tree*, *key*), computed once while the tree is cached. *key* names what is derived, and
    must include every argument the value depends on besides the tree."""
    slot = (id(tree), key)
    with _LOCK:
        hit = _MEMO.get(slot)
    if hit is not None and hit[0] is tree:
        return hit[1]  # type: ignore[no-any-return]
    value = compute()
    with _LOCK:
        _MEMO[slot] = (tree, value)
    return value


def clear() -> None:
    """Drop every index and memo (frees memory; correctness never depends on it)."""
    with _LOCK:
        _INDEX.clear()
        _MEMO.clear()
