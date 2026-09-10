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
import tomllib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "py_ci_shared"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _python_floor() -> tuple[int, int]:
    """The (major, minor) this package promises to run on, read from ``requires-python``."""
    spec = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["requires-python"]
    digits = spec.strip().lstrip(">=~^ ")
    major, _, minor = digits.partition(".")
    return int(major), int(minor.split(".")[0])


def test_the_floor_is_still_below_the_versions_these_rules_guard():
    """If the floor ever rises past 3.10 these checks become noise and should be deleted, not left lying."""
    assert _python_floor() < (3, 11), "the Python floor moved; revisit whether this gate still has a subject"


@pytest.mark.parametrize("path", sorted(SRC.glob("*.py")), ids=lambda p: p.name)
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


@pytest.mark.parametrize("path", sorted(SRC.glob("*.py")), ids=lambda p: p.name)
def test_no_module_imports_tomllib_directly(path: Path):
    """``tomllib`` is stdlib from 3.11; the compat shim is the only place allowed to reach for it."""
    if path.name == "_toml_compat.py":
        return
    source = path.read_text(encoding="utf-8")
    assert not [ln for ln in source.splitlines() if ln.strip() == "import tomllib"], (
        f"{path.name} imports tomllib directly; use ``from ._toml_compat import tomllib``"
    )
