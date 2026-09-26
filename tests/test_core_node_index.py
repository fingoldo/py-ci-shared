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
