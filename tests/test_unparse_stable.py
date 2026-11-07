"""Cross-version stability of what feeds baseline keys: the node renderer, the stdlib predicate, the body fingerprint."""

from __future__ import annotations

import ast
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from py_ci_shared import resource_leak_checks
from py_ci_shared._core.unparse import unparse_stable
from py_ci_shared.alembic_concurrently import collect_unguarded_concurrently
from py_ci_shared.code_audit.duplicate_function_body import _normalized_body
from py_ci_shared.drifted_duplicate_functions import _canonical

_SRC = Path(__file__).resolve().parent.parent / "src" / "py_ci_shared"

# Statement -> the text 3.11+ ast.unparse gives it; 3.9/3.10 differ on the first four.
_GOLDEN = [
    ("lambda: 1", "lambda: 1"),
    ("f = lambda: (yield)", "f = lambda: (yield)"),
    ("lambda a, *b: 1", "lambda a, *b: 1"),
    ("for (a, b) in c:\n    pass", "for a, b in c:\n    pass"),
    ("[x for (a, b) in c]", "[x for a, b in c]"),
    ("(a, b) = c", "a, b = c"),
    ("for (a,) in c:\n    pass", "for a, in c:\n    pass"),
    ("for (a, b), c in d:\n    pass", "for (a, b), c in d:\n    pass"),
    ("for x in c:\n    pass\nelse:\n    y = 1", "for x in c:\n    pass\nelse:\n    y = 1"),
    ("async def f():\n    async for (a, b) in c:\n        pass", "async def f():\n    async for a, b in c:\n        pass"),
    ("x[a, b]", "x[a, b]"),
    ("() = c", "() = c"),
]


@pytest.mark.parametrize(("source", "expected"), _GOLDEN)
def test_unparse_stable_renders_the_311_shape(source: str, expected: str) -> None:
    assert unparse_stable(ast.parse(source).body[0]) == expected


def test_alembic_finding_key_is_the_311_shape_on_every_interpreter(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "001_x.py").write_text('from alembic import op\nop.execute(lambda: "CREATE INDEX CONCURRENTLY ix ON t (a)")\n', encoding="utf-8")
    findings, _scan = collect_unguarded_concurrently(versions)
    assert [f.key for f in findings] == ["alembic-concurrently::001_x.py::op.execute(lambda: 'CREATE INDEX CONCURRENTLY ix ON t (a)')"]


def _src_nodes() -> Iterator[tuple[str, ast.AST]]:
    for path in sorted(_SRC.rglob("*.py")):
        if path.name == "unparse.py" and path.parent.name == "_core":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            yield path.relative_to(_SRC).as_posix(), node


def _names_unparse(node: ast.AST) -> bool:
    """``ast.unparse``, ``getattr(ast, "unparse", ...)`` or ``hasattr(ast, "unparse")``."""
    if isinstance(node, ast.Attribute):
        return node.attr == "unparse" and isinstance(node.value, ast.Name) and node.value.id == "ast"
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"getattr", "hasattr"}
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "ast"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "unparse"
    )


def test_no_bare_ast_unparse_in_src() -> None:
    """Every node rendered in src goes through ``unparse_stable``; ``ast.unparse`` is not stable across interpreters."""
    offenders = [f"{rel}:{getattr(node, 'lineno', 0)}" for rel, node in _src_nodes() if _names_unparse(node)]
    assert not offenders, f"use py_ci_shared._core.unparse.unparse_stable instead of ast.unparse: {offenders}"


def _empty_default(node: ast.expr) -> bool:
    """``frozenset()``, ``()``, ``set()``, ``[]``: a default that makes a missing attribute look like an empty stdlib."""
    if isinstance(node, (ast.Tuple, ast.List)):
        return not node.elts
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"frozenset", "set", "tuple", "list"} and not node.args


def _empty_stdlib_fallback(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) == 3
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "stdlib_module_names"
        and _empty_default(node.args[2])
    )


def test_no_empty_fallback_for_stdlib_module_names_in_src() -> None:
    """``sys.stdlib_module_names`` exists from 3.10; an empty fallback silently turns every stdlib module into a library on 3.9."""
    offenders = [f"{rel}:{getattr(node, 'lineno', 0)}" for rel, node in _src_nodes() if _empty_stdlib_fallback(node)]
    assert not offenders, f"use _ci_install_parts.is_stdlib (it has a 3.9 path): {offenders}"


def test_imported_library_ignores_a_lazily_imported_stdlib_module() -> None:
    lazy = {"colorsys", "zoneinfo"}
    for name in lazy:
        sys.modules.pop(name, None)
    before = resource_leak_checks.take_state()
    assert not lazy & before.modules
    __import__("colorsys")
    __import__("zoneinfo")
    assert resource_leak_checks._imported_library(before) is False


def test_body_fingerprint_is_the_canonical_rendering_not_ast_dump() -> None:
    body = ast.parse(textwrap.dedent("x = f(a)\nreturn_ = x + 1\n")).body
    rendered = _normalized_body(body)
    assert rendered == _canonical(ast.Module(body=body, type_ignores=[]))
    # ast.dump prints the empty keywords list and the implicit Load context, which differ between 3.11 and 3.14
    assert "keywords=[]" not in rendered
    assert "Load()" not in rendered


def test_self_ci_runs_a_python_314_leg() -> None:
    """The newest interpreter has a leg (non-blocking until it has run green once): 3.14-only breaks were found locally, not by CI."""
    import yaml  # optional-import-ok: PyYAML is a runtime dependency of the package

    workflow = yaml.safe_load((_SRC.parent.parent / ".github" / "workflows" / "self-ci.yml").read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]
    legs = [leg for leg in matrix["include"] if leg["python"] == "3.14"]
    assert legs, "no Python 3.14 leg in the self-ci matrix"
    assert all(leg.get("preview") is True for leg in legs)


def test_no_stale_python_38_floor_claims_in_the_fallbacks() -> None:
    """requires-python is >=3.9 (audit 45-F7): the comments claiming a 3.8 floor above removed fallbacks stay gone."""
    stale = {
        "meta_test_utils.py": "this package supports 3.8",
        "code_audit/default_via_or.py": "supported 3.8 floor",
    }
    for rel, phrase in stale.items():
        assert phrase not in (_SRC / rel).read_text(encoding="utf-8"), f"{rel} still says {phrase!r}"
