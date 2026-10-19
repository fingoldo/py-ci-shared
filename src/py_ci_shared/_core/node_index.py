"""One ``ast.walk`` per tree per process, shared by every gate.

Each gate used to walk every tree itself, several times: on mlframe the unresolved-import check walked 18M nodes
and the marker check 7.7M, most of the 12 minutes a full meta-suite run took. The parse cache already hands every
caller the SAME tree object while the file is unchanged, so what a walk finds can be kept next to it:

* :func:`nodes_of` -- the nodes of the given types, in ``ast.walk`` order, from one walk of the tree;
* :func:`tree_memo` -- any per-tree derived value (a module's import aliases, a file's marker analysis), computed once.

Both are keyed WEAKLY on the tree object: an entry lives exactly as long as its tree, so a tree the parse cache replaced
(or one a caller built with a plain ``ast.parse`` and dropped) is freed with everything derived from it. Holding the
tree strongly, as this module first did, pinned every superseded tree of a long-lived process (audit 2026-10-03 K-9).
The index never stores the root node itself (a value referencing its key would keep the key alive); a memo value
must not hold its tree for the same reason. Trees are shared and read-only (see :func:`py_ci_shared._core.source.parse_source`); a caller that
mutates one must not use these. :func:`clear` drops everything, and ``clear_parse_cache`` calls it.
"""

from __future__ import annotations

import ast
import functools
import heapq
import itertools
import threading
import weakref
from collections import deque
from collections.abc import Callable, Hashable, Iterator
from typing import Any, Optional, TypeVar, Union, overload

T = TypeVar("T")
_A = TypeVar("_A", bound=ast.AST)
_B = TypeVar("_B", bound=ast.AST)

# tree -> {node type: ([walk positions], [nodes])}, the root itself left out (position 0, see nodes_of)
_INDEX: "weakref.WeakKeyDictionary[ast.AST, dict[type, tuple[list[int], list[ast.AST]]]]" = weakref.WeakKeyDictionary()
# tree -> {key: value}
_MEMO: "weakref.WeakKeyDictionary[ast.AST, dict[Hashable, Any]]" = weakref.WeakKeyDictionary()
# module tree -> every node below it in ``ast.walk`` order, the root itself left out (a value referencing its key would keep the tree alive)
_DESCENDANTS: "weakref.WeakKeyDictionary[ast.AST, list[ast.AST]]" = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()


def _all_node_types() -> "list[type]":
    found: "list[type]" = []
    todo = [ast.AST]
    while todo:
        cls = todo.pop()
        found.append(cls)
        todo.extend(cls.__subclasses__())
    return found


# every ``ast`` node type with its field names, filled once at import and only read afterwards (no lock needed); a node of an unknown subclass reads ``_fields`` directly
_FIELDS: "dict[type, tuple[str, ...]]" = {cls: cls._fields for cls in _all_node_types()}


def _walk(node: ast.AST) -> "Iterator[ast.AST]":
    """The breadth-first walk itself (no caching); see :func:`walk`."""
    _ast, fields_of = ast.AST, _FIELDS
    todo = deque([node])
    pop, push, extend = todo.popleft, todo.append, todo.extend
    while todo:
        current = pop()
        fields = fields_of.get(type(current)) or current._fields
        for name in fields:
            value = getattr(current, name, None)
            if isinstance(value, _ast):
                push(value)
            elif isinstance(value, list):
                extend([item for item in value if isinstance(item, _ast)])
        yield current


def walk(node: ast.AST) -> "Iterator[ast.AST]":
    """``ast.walk`` with the same breadth-first order, about 1.5x faster: the stdlib version goes through two generator
    layers (``iter_child_nodes`` over ``iter_fields``) per node, and every gate here walks millions of nodes.

    A module tree is walked once per process: the node list is kept next to the tree (weakly keyed, freed with it) and every later
    ``walk`` of that tree replays it. On the mlframe suite gates called ``walk`` on the same trees 221 million times, almost all of them
    repeats. Trees are shared and read-only (see :func:`py_ci_shared._core.source.parse_source`); a caller that mutates one must not use this.
    """
    if not isinstance(node, ast.Module):
        return _walk(node)
    return itertools.chain((node,), _descendants(node))


def _descendants(tree: ast.Module) -> "list[ast.AST]":
    with _LOCK:
        hit = _DESCENDANTS.get(tree)
    if hit is not None:
        return hit
    nodes = _walk(tree)
    next(nodes)  # the root
    rest = list(nodes)
    with _LOCK:
        _DESCENDANTS[tree] = rest
    return rest


def child_nodes(node: ast.AST) -> "list[ast.AST]":
    """The direct children of *node*, in the order ``ast.iter_child_nodes`` yields them, as a list.

    The stdlib generator goes through ``iter_fields`` and a second generator layer per node; a gate that recurses over a tree calls it millions of times.
    This reads the node's field names once per node type and returns a plain list (about 2x faster); callers that only iterate need no change.
    """
    fields = _FIELDS.get(type(node)) or node._fields
    out: "list[ast.AST]" = []
    for name in fields:
        value = getattr(node, name, None)
        if isinstance(value, ast.AST):
            out.append(value)
        elif isinstance(value, list):
            out.extend([item for item in value if isinstance(item, ast.AST)])
    return out


def parent_map(tree: ast.Module) -> "dict[int, Optional[ast.AST]]":
    """``{id(node): its parent node}`` for every node below *tree*, built once per tree and shared (do not mutate).

    A child of the module itself maps to ``None`` (the memo lives next to the tree and must not hold it); :func:`parent_of` turns that back into the tree. The expression
    contexts (``Load``, ``Store``, ``Del``) and the operator nodes (``Add``, ``Eq``, ``And``, ``Not``, ...) are one shared instance each in CPython, so they have no single parent and
    their entry is whichever node was seen last.
    """

    def build() -> "dict[int, Optional[ast.AST]]":
        out: "dict[int, Optional[ast.AST]]" = {}
        for node in _descendants(tree):
            for child in child_nodes(node):
                out[id(child)] = node
        for child in child_nodes(tree):
            out[id(child)] = None
        return out

    return tree_memo(tree, "py_ci_shared.parent_map", build)


def parent_of(tree: ast.Module, node: ast.AST) -> "Optional[ast.AST]":
    """The parent of *node* inside *tree* (the tree itself for a top-level statement), or ``None`` for the tree or a node not in it."""
    parents = parent_map(tree)
    if id(node) not in parents:
        return None
    return parents[id(node)] or tree


def _index(tree: ast.AST) -> "dict[type, tuple[list[int], list[ast.AST]]]":
    with _LOCK:
        hit = _INDEX.get(tree)
    if hit is not None:
        return hit
    by_type: "dict[type, tuple[list[int], list[ast.AST]]]" = {}
    nodes = _descendants(tree) if isinstance(tree, ast.Module) else list(itertools.islice(_walk(tree), 1, None))
    for pos, node in enumerate(nodes, start=1):  # position 0 is the root: see nodes_of
        entry = by_type.get(type(node))
        if entry is None:
            entry = by_type[type(node)] = ([], [])
        entry[0].append(pos)
        entry[1].append(node)
    with _LOCK:
        _INDEX[tree] = by_type
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
    if isinstance(tree, types):  # the root is walk position 0 and is not stored in the index
        hits.insert(0, ([0], [tree]))
    if not hits:
        return []
    if len(hits) == 1:
        return hits[0][1]
    return [node for _, node in heapq.merge(*(zip(pos, nodes) for pos, nodes in hits), key=lambda pair: pair[0])]


def tree_memo(tree: ast.AST, key: Hashable, compute: "Callable[[], T]") -> T:
    """``compute()`` for (*tree*, *key*), computed once while the tree is cached. *key* names what is derived, and
    must include every argument the value depends on besides the tree."""
    missing = object()
    with _LOCK:
        hit = _MEMO.get(tree, {}).get(key, missing)
    if hit is not missing:
        return hit  # type: ignore[no-any-return]
    value = compute()
    with _LOCK:
        _MEMO.setdefault(tree, {})[key] = value
    return value


def tree_cached(fn: "Callable[[ast.Module], T]") -> "Callable[[ast.Module], T]":
    """Decorator: compute ``fn(tree)`` once per tree and return the same (shared, read-only) result afterwards.

    For a gate helper that is a pure function of one module tree (a scope map, a per-function table). The result must not hold the tree itself (see :func:`tree_memo`):
    keying by ``id(node)`` or holding descendants is fine, a list containing the module node is not.
    """
    key = f"{fn.__module__}.{fn.__qualname__}"

    @functools.wraps(fn)
    def wrapper(tree: ast.Module) -> T:
        return tree_memo(tree, key, lambda: fn(tree))

    return wrapper


class TreeVisitor:
    """``ast.NodeVisitor`` over :func:`child_nodes`: ``visit`` dispatches to ``visit_<NodeType>`` and ``generic_visit`` visits the children in field order.

    For a gate whose traversal carries state down the tree (a scope stack, a "inside a finally" counter) and so cannot be a flat :func:`nodes_of` query. It gives the same
    order and dispatch as the stdlib class, about twice as fast, and keeps hand-written visitors off ``ast.NodeVisitor`` (see ``tests/test_gates_use_shared_traversal.py``).
    """

    def visit(self, node: ast.AST) -> Any:
        """Call ``visit_<NodeType>`` when the subclass defines it, else :meth:`generic_visit`."""
        method = getattr(self, "visit_" + type(node).__name__, None)
        if method is None:
            return self.generic_visit(node)
        return method(node)

    def generic_visit(self, node: ast.AST) -> None:
        """Visit every child of *node*."""
        for child in child_nodes(node):
            self.visit(child)


def index_size() -> int:
    """How many trees have an index or a memo (both shrink as their trees are freed)."""
    with _LOCK:
        return len(set(_INDEX.keys()) | set(_MEMO.keys()))


def clear() -> None:
    """Drop every index and memo (frees memory; correctness never depends on it)."""
    with _LOCK:
        _INDEX.clear()
        _MEMO.clear()
        _DESCENDANTS.clear()
