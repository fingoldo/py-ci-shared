"""Nothing in this package may use syntax newer than the Python floor it advertises.

``requires-python`` is ``>=3.9``, and two separate bugs shipped that broke exactly that promise: five
checkers imported ``tomllib`` (stdlib from 3.11), and ``effect_assertion_parity`` called
``isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)`` -- PEP 604 unions between two classes are a
RUNTIME expression, supported from 3.10. Both are invisible to a developer on a modern interpreter and to
this suite when it runs on one: they surface only in a consumer's CI matrix, as a red shard whose message
has nothing to do with what the checker checks. mlframe's 3.9 and 3.10 shards found both.

``from __future__ import annotations`` does NOT cover the isinstance case: it defers ANNOTATIONS, and this
is an argument being evaluated.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from py_ci_shared._toml_compat import tomllib

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "py_ci_shared"
PYPROJECT = REPO / "pyproject.toml"
MODULES = sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)
TEST_FILES = sorted((REPO / "tests").glob("*.py"))


def _python_floor() -> tuple[int, int]:
    """The (major, minor) this package promises to run on, read from ``requires-python``."""
    spec = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["requires-python"]
    digits = spec.strip().lstrip(">=~^ ")
    major, _, minor = digits.partition(".")
    return int(major), int(minor.split(".")[0])


def test_the_floor_is_still_below_the_versions_these_rules_guard():
    """If the floor ever rises past 3.10 these checks become noise and should be deleted, not left lying."""
    assert _python_floor() < (3, 11), "the Python floor moved; revisit whether this gate still has a subject"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.relative_to(SRC).as_posix())
def test_no_module_uses_a_runtime_union_between_classes(path: Path):
    """``A | B`` as a VALUE (isinstance, issubclass, a cast target) needs 3.10."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"isinstance", "issubclass"}:
            continue
        offenders.extend(f"{path.name}:{node.lineno}" for arg in node.args[1:] if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.BitOr))
    assert not offenders, f"PEP 604 union evaluated at runtime, unsupported on 3.9: {offenders}. Use a tuple."


def _guarded(chain: list[ast.AST]) -> bool:
    """True when an import sits under ``if sys.version_info ...`` or in a ``try`` that handles ImportError."""
    for node in chain:
        if isinstance(node, ast.If) and "version_info" in ast.unparse(node.test):
            return True
        if isinstance(node, ast.Try) and any(h.type is None or "ImportError" in ast.unparse(h.type) for h in node.handlers):
            return True
    return False


def unguarded_tomllib_imports(source: str) -> list[int]:
    """Line numbers of ``import tomllib`` / ``import tomllib as t`` / ``from tomllib import ...`` with no version guard."""
    lines: list[int] = []

    def visit(node: ast.AST, chain: list[ast.AST]) -> None:
        hit = (isinstance(node, ast.Import) and any(a.name.split(".")[0] == "tomllib" for a in node.names)) or (
            isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "tomllib"
        )
        if hit and not _guarded(chain):
            lines.append(getattr(node, "lineno", 0))
        for child in ast.iter_child_nodes(node):
            visit(child, [*chain, node])

    visit(ast.parse(source), [])
    return lines


def _src(*lines: str) -> str:
    return chr(10).join(lines) + chr(10)


def test_the_tomllib_ban_sees_every_import_spelling():
    """Aliased and ``from`` imports are caught; a version-guarded or ImportError-guarded import is not."""
    assert unguarded_tomllib_imports("import tomllib as t") == [1]
    assert unguarded_tomllib_imports("from tomllib import loads") == [1]
    assert unguarded_tomllib_imports(_src("def f():", "    import tomllib")) == [2]
    assert unguarded_tomllib_imports(_src("import sys", "if sys.version_info >= (3, 11):", "    import tomllib")) == []
    assert unguarded_tomllib_imports(_src("try:", "    import tomllib", "except ImportError:", "    tomllib = None")) == []
    assert unguarded_tomllib_imports("import tomli as tomllib") == []


@pytest.mark.parametrize("path", MODULES + TEST_FILES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_file_imports_tomllib_without_a_version_guard(path: Path):
    """``tomllib`` is stdlib from 3.11; package and test code reach it through ``py_ci_shared._toml_compat``."""
    if path.name == "_toml_compat.py":
        return
    offenders = unguarded_tomllib_imports(path.read_text(encoding="utf-8-sig"))
    assert not offenders, (
        f"{path.relative_to(REPO).as_posix()} imports tomllib at line(s) {offenders} with no version guard, which fails on "
        f"3.9/3.10; use ``from py_ci_shared._toml_compat import tomllib``"
    )
