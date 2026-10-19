"""``iter_scopes`` / ``scope_nodes``: each scope's own nodes, collected once per tree, parents before children, freed with the tree."""

from __future__ import annotations

import ast
import gc
import textwrap
import weakref

import pytest

from py_ci_shared._core.scopes import iter_scopes, scope_nodes

SRC = textwrap.dedent(
    """
    X = []

    class C:
        attr = {}

        def m(self):
            def g():
                return X
            return g

    def f(a):
        k = lambda: a
        return X
    """
)


def _names(scope) -> "set[str]":
    return {n.id for n in scope.nodes if isinstance(n, ast.Name)}


def _returned_names(scope) -> "list[str]":
    return [n.value.id for n in scope.nodes if isinstance(n, ast.Return) and isinstance(n.value, ast.Name)]


def test_each_scope_holds_only_its_own_body():
    """A nested definition's body is not part of the enclosing scope; the definition node itself is."""
    tree = ast.parse(SRC)
    by_name = {getattr(s.node, "name", None): s for s in iter_scopes(tree)}
    assert set(by_name) == {None, "C", "m", "g", "f"}
    assert _returned_names(by_name["g"]) == ["X"]
    assert _returned_names(by_name["m"]) == ["g"]  # g's own ``return X`` is not m's
    assert _returned_names(by_name["f"]) == ["X"]
    assert any(n is by_name["g"].node for n in by_name["m"].nodes)
    assert _returned_names(by_name[None]) == []


def test_parents_come_before_children_and_chain_up():
    """The enclosing scope chain of a nested function is its class, then the module."""
    scopes = iter_scopes(ast.parse(SRC))
    order = {id(s): i for i, s in enumerate(scopes)}
    children = [s for s in scopes if s.parent is not None]
    assert len(children) == 4  # every scope but the module has a parent
    assert all(order[id(s.parent)] < order[id(s)] for s in children)
    g = next(s for s in scopes if getattr(s.node, "name", None) == "g")
    assert [getattr(p.node, "name", None) for p in (g.parent, g.parent.parent, g.parent.parent.parent)] == ["m", "C", None]


def test_lambda_body_is_a_boundary_not_a_scope():
    """A lambda's body does not leak into the function that holds it, and a lambda opens no scope of its own."""
    tree = ast.parse(SRC)
    scopes = iter_scopes(tree)
    f = next(s for s in scopes if getattr(s.node, "name", None) == "f")
    assert any(isinstance(n, ast.Lambda) for n in f.nodes)
    assert "a" not in _names(f)  # ``a`` is read only inside the lambda body
    assert len(scopes) == 5


def test_computed_once_per_tree_and_freed_with_it():
    """The second call returns the memoised list; the memo does not keep the tree alive."""
    tree = ast.parse(SRC)
    first = iter_scopes(tree)
    assert iter_scopes(tree) is first
    ref = weakref.ref(tree)
    del tree, first
    gc.collect()
    assert ref() is None


def test_scope_nodes_lookup_and_unknown_opener():
    """``scope_nodes`` returns the memoised list for the module or a definition, and refuses a node from another tree."""
    tree = ast.parse(SRC)
    f = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    assert scope_nodes(tree, f) is next(s for s in iter_scopes(tree) if s.node is f).nodes
    assert scope_nodes(tree, tree) is iter_scopes(tree)[0].nodes
    other = ast.parse("def h():\n    pass\n").body[0]
    with pytest.raises(ValueError):
        scope_nodes(tree, other)
