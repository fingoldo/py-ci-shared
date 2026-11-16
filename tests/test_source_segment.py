"""``source_segment`` matches ``ast.get_source_segment`` and splits each source once (audit 2026-11-16 48-T1)."""

from __future__ import annotations

import ast
from pathlib import Path

from py_ci_shared._core.source import source_lines, source_segment

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "py_ci_shared"
OWNER = SRC_ROOT / "_core" / "source.py"


def _nodes_with_position(tree: ast.AST):
    return [node for node in ast.walk(tree) if getattr(node, "lineno", None) is not None]


def test_matches_ast_get_source_segment_on_every_node() -> None:
    texts = [
        "def f():\n    return 'é€'\n",
        "def f():\r\n    return 1\r\n",
        "def f():\r    return 2\r",
        "x = 'ü'; y = (\n  3)\nz = [\n  4,\r\n  5]\n",
        "class A:\n    def g(self):\n        return {'k': 'v'}  # trailing\n",
    ]
    for text in texts:
        nodes = list(_nodes_with_position(ast.parse(text)))
        assert nodes, text
        for node in nodes:
            assert source_segment(text, node) == ast.get_source_segment(text, node), (text, ast.dump(node))


def test_node_without_position_gives_none() -> None:
    node = ast.Name(id="x", ctx=ast.Load())
    assert source_segment("x\n", node) is None


def test_source_is_split_once_for_many_nodes() -> None:
    text = "".join(f"def f{i}():\n    return {i}\n" for i in range(60))
    source_lines.cache_clear()
    for node in _nodes_with_position(ast.parse(text)):
        source_segment(text, node)
    info = source_lines.cache_info()
    assert info.misses == 1
    assert info.hits > 100


def test_no_module_calls_ast_get_source_segment() -> None:
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path == OWNER:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Attribute) and node.attr == "get_source_segment":
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and any(alias.name == "get_source_segment" for alias in node.names):
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}")
    assert offenders == []
