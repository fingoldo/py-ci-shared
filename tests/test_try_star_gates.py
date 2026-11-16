"""Gates see ``try: ... except* X:`` (PEP 654, 3.11+) as a try statement too (audit 45-T2).

``ast.TryStar`` is a sibling of ``ast.Try``, not a subclass, so an ``isinstance(node, ast.Try)`` test skips every
exception-group handler. The shared tuple ``_core.TRY_NODES`` names both, and every ``isinstance`` test in ``src/`` that
names ``ast.Try`` must use it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from py_ci_shared._core.ast_try import TRY_NODES
from py_ci_shared.code_audit.broad_except import scan_broad_except_swallows
from py_ci_shared.code_audit.skip_masking_except import scan_except_skip_masks_call_under_test

_SRC = Path(__file__).resolve().parent.parent / "src" / "py_ci_shared"
_EXCEPT_STAR_AVAILABLE = hasattr(ast, "TryStar")


def test_try_nodes_names_both_statements() -> None:
    assert ast.Try in TRY_NODES
    if _EXCEPT_STAR_AVAILABLE:
        assert ast.TryStar in TRY_NODES


def _mentions_plain_try(node: ast.AST) -> bool:
    return any(isinstance(sub, ast.Attribute) and sub.attr == "Try" for sub in ast.walk(node))


def test_no_isinstance_test_names_plain_ast_try_outside_the_constant() -> None:
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        if path.name == "ast_try.py" and path.parent.name == "_core":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance":
                if len(node.args) == 2 and _mentions_plain_try(node.args[1]):
                    offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert not offenders, f"isinstance tests on ast.Try that skip ast.TryStar; use TRY_NODES: {offenders}"


@pytest.mark.skipif(not _EXCEPT_STAR_AVAILABLE, reason="except* needs Python 3.11+")
def test_broad_except_scanner_finds_a_swallow_in_an_except_star(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def h():\n    try:\n        g()\n    except* Exception:\n        pass\n",
        encoding="utf-8",
    )
    assert [f.line for f in scan_broad_except_swallows(tmp_path)] == [4]


@pytest.mark.skipif(not _EXCEPT_STAR_AVAILABLE, reason="except* needs Python 3.11+")
def test_skip_masking_scanner_finds_a_skip_in_an_except_star(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(
        "import pytest\nimport m\n\n\ndef test_f():\n    try:\n        m.f()\n    except* Exception:\n" "        pytest.skip('api moved')\n",
        encoding="utf-8",
    )
    assert scan_except_skip_masks_call_under_test(tmp_path)
