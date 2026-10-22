"""Gates reach the tree through the shared traversal helpers, not through a walk of their own.

A gate that calls ``ast.walk`` / ``ast.iter_child_nodes``, subclasses ``NodeVisitor`` / ``NodeTransformer``, or parses a module with ``ast.parse`` repeats work every other gate already
paid for: the parse cache hands all of them the same tree, ``nodes_of`` / ``tree_memo`` / ``tree_cached`` / ``iter_scopes`` / ``qualified_functions`` / ``parent_map`` keep what one walk
found next to it, and a hand-written walk per gate is what made a full meta-suite run take most of an hour (one gate alone: 180 s in three body walks per function). No module outside
``_core`` may use them. Parsing a short expression string (``mode="eval"``, an annotation or a config check) is not a module parse and is not counted.

The one exemption is a module that must NOT share trees, with its reason: a mutation harness builds and edits private trees, so a shared, read-only one would be corrupted.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "py_ci_shared"
_CALLS = frozenset({"walk", "iter_child_nodes", "parse"})
_BASES = frozenset({"NodeVisitor", "NodeTransformer"})
EXEMPT = {
    "_mutation_operators.py": "parses and mutates private trees; a shared tree must never be edited",
}
# The code_audit package holds the scanners themselves: parsing source is their job, and the rule protects the gate modules.
SCANNER_PACKAGE = "code_audit/"
SCANNER_PACKAGE_REASON = "scanner package: its job is to parse source; gate modules are what the rule protects"


def _is_expression_parse(call: ast.Call) -> bool:
    """``ast.parse(text, mode="eval")``: an expression string, not a module."""
    if any(k.arg == "mode" and isinstance(k.value, ast.Constant) and k.value.value == "eval" for k in call.keywords):
        return True
    return len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) and call.args[1].value == "eval"


def _uses(tree: ast.AST) -> int:
    """Hand-rolled traversal or module-parse sites in one module: ``ast.<call>(...)``, a visitor base class, or the same names imported from ``ast``."""
    imported = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "ast" for alias in node.names}
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            via_attr = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "ast" and f.attr in _CALLS
            via_name = isinstance(f, ast.Name) and f.id in _CALLS and f.id in imported
            if (via_attr or via_name) and not _is_expression_parse(node):
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
        if rel.startswith("_core/") or rel.startswith(SCANNER_PACKAGE):
            continue
        count = _uses(ast.parse(path.read_text(encoding="utf-8")))
        if count:
            out[rel] = count
    return out


def test_no_module_has_a_walk_of_its_own():
    """A gate uses the shared helpers; any other module with a site fails, whatever it was before."""
    found = {rel: n for rel, n in _current().items() if rel not in EXEMPT}
    assert not found, (
        f"hand-rolled AST traversal or module parse in {found}: use py_ci_shared._core nodes_of / tree_cached / iter_scopes / qualified_functions / parent_map / TreeVisitor / "
        "parse_file / parse_text, which share one walk and one parse across every gate"
    )


def test_every_exemption_still_needs_its_reason():
    """An exempt module that no longer has a site, or a missing reason, is removed from the list."""
    now = _current()
    stale = sorted(rel for rel in EXEMPT if rel not in now)
    assert not stale, f"exempt modules without a site: {stale}"
    assert all(reason.strip() for reason in EXEMPT.values())
    assert SCANNER_PACKAGE_REASON.strip()
