"""The per-tree node index returns exactly what a filtered ``ast.walk`` returns, and computes each memo once."""

from __future__ import annotations

import ast

from py_ci_shared._core import clear_parse_cache, nodes_of, parse_file, tree_memo

_SRC = """
import os
from a import b
def f():
    import sys as s
    from .c import d
    try:
        from e import g
    except ImportError:
        pass
class K:
    import json
"""


def _walk_filtered(tree: ast.AST, *types: type) -> list:
    return [n for n in ast.walk(tree) if isinstance(n, types)]


def test_one_type_matches_a_filtered_walk():
    tree = ast.parse(_SRC)
    assert nodes_of(tree, ast.ImportFrom) == _walk_filtered(tree, ast.ImportFrom)


def test_several_types_keep_walk_order():
    """Order matters: a later import rebinding a name must still come later."""
    tree = ast.parse(_SRC)
    got = nodes_of(tree, ast.Import, ast.ImportFrom, ast.Try)
    assert got == _walk_filtered(tree, ast.Import, ast.ImportFrom, ast.Try)
    assert len(got) == 7


def test_a_base_class_matches_its_subclasses_like_isinstance():
    tree = ast.parse(_SRC)
    assert nodes_of(tree, ast.stmt) == _walk_filtered(tree, ast.stmt)


def test_absent_types_give_an_empty_list():
    assert nodes_of(ast.parse("x = 1"), ast.ImportFrom) == []


def test_a_memo_is_computed_once_per_tree_and_key():
    tree = ast.parse(_SRC)
    calls = []

    def compute():
        calls.append(1)
        return len(nodes_of(tree, ast.Import))

    assert tree_memo(tree, "count", compute) == tree_memo(tree, "count", compute) == 3
    assert len(calls) == 1
    other = ast.parse(_SRC)
    tree_memo(other, "count", compute)
    assert len(calls) == 2, "a different tree object is a different entry"


def test_clearing_the_parse_cache_drops_memos(tmp_path):
    path = tmp_path / "m.py"
    path.write_text(_SRC, encoding="utf-8")
    tree = parse_file(path)
    calls = []
    tree_memo(tree, "k", lambda: calls.append(1))
    clear_parse_cache()
    tree_memo(tree, "k", lambda: calls.append(1))
    assert len(calls) == 2


def test_a_replaced_tree_is_freed_with_its_index_and_memos(tmp_path):
    """K-9: the index held every tree strongly, so each rewrite of a file pinned one more tree forever."""
    import gc
    import weakref

    from py_ci_shared._core import node_index

    clear_parse_cache()
    path = tmp_path / "m.py"
    refs = []
    for i in range(5):
        path.write_text(f"x = {i}\n", encoding="utf-8")
        tree = parse_file(path)
        nodes_of(tree, ast.Assign)
        tree_memo(tree, "k", lambda: 1)
        refs.append(weakref.ref(tree))
        del tree
    gc.collect()
    assert sum(r() is not None for r in refs) == 1, "only the cached (current) tree may stay alive"
    assert node_index.index_size() == 1
    plain = ast.parse("y = 1\n")
    nodes_of(plain, ast.Assign)
    ref = weakref.ref(plain)
    del plain
    gc.collect()
    assert ref() is None and node_index.index_size() == 1, "a tree built outside the parse cache is not pinned either"


def test_the_root_node_is_still_returned_in_walk_order():
    """The root is not stored in the index (it would keep its own key alive); nodes_of still returns it first."""
    tree = ast.parse(_SRC)
    assert nodes_of(tree, ast.Module) == [tree]
    assert nodes_of(tree, ast.AST) == list(ast.walk(tree))
    assert nodes_of(tree, ast.Module, ast.Import) == _walk_filtered(tree, ast.Module, ast.Import)
