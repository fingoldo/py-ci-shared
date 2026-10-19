"""Gates reach the tree through the shared traversal helpers, not through a walk of their own.

A gate that calls ``ast.walk`` / ``ast.iter_child_nodes``, subclasses ``NodeVisitor`` / ``NodeTransformer``, or parses source with ``ast.parse`` repeats
work every other gate already paid for: the parse cache hands all of them the same tree, ``nodes_of`` / ``tree_memo`` / ``iter_scopes`` keep what one
walk found next to it, and a hand-written walk per gate is what made a full meta-suite run take most of an hour (one gate alone: 180 s in three body walks
per function). This test counts those uses per module outside ``_core`` and fails when a module's count grows or a module that had none gains one;
``tests/baselines/own_traversal.json`` holds the recorded counts and only goes down, as modules move to the helpers.

To lower a count after a migration, write ``json.dumps(_current(), indent=1, sort_keys=True)`` to the baseline file.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "py_ci_shared"
BASELINE = Path(__file__).with_name("baselines") / "own_traversal.json"
_CALLS = frozenset({"walk", "iter_child_nodes", "parse"})
_BASES = frozenset({"NodeVisitor", "NodeTransformer"})


def _uses(tree: ast.AST) -> int:
    """Hand-rolled traversal or parse sites in one module: ``ast.<call>(...)``, a visitor base class, or the same names imported from ``ast``."""
    imported = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "ast" for alias in node.names}
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "ast" and f.attr in _CALLS:
                n += 1
            elif isinstance(f, ast.Name) and f.id in _CALLS and f.id in imported:
                n += 1
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
                if name in _BASES:
                    n += 1
    return n


def _current() -> "dict[str, int]":
    """Per-module counts for every module outside ``_core`` (the helpers themselves) that has at least one site."""
    out: "dict[str, int]" = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("_core/"):
            continue
        count = _uses(ast.parse(path.read_text(encoding="utf-8")))
        if count:
            out[rel] = count
    return out


def test_no_module_gains_a_walk_of_its_own():
    """A gate must use ``nodes_of`` / ``tree_memo`` / ``iter_scopes`` and the parse cache; a count above its baseline, or a new module with any, fails."""
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    grew = {rel: (base.get(rel, 0), n) for rel, n in _current().items() if n > base.get(rel, 0)}
    assert not grew, (
        f"hand-rolled AST traversal or parse grew in {grew} (baseline, now): use py_ci_shared._core nodes_of / tree_memo / iter_scopes / parse_file, "
        "which share one walk and one parse across every gate"
    )


def test_baseline_only_shrinks():
    """A module whose count dropped (or reached zero) is lowered in the baseline, so the recorded debt matches the code."""
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    now = _current()
    stale = {rel: (n, now.get(rel, 0)) for rel, n in base.items() if now.get(rel, 0) < n}
    assert not stale, f"baseline above the code (recorded, now): {stale}; rewrite tests/baselines/own_traversal.json from _current()"
