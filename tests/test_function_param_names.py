"""One parameter-name helper for the scanners that used to carry copies of it (audit 52-F16)."""

from __future__ import annotations

import ast

import pytest

from py_ci_shared import db_transaction_completeness, gpu_timing_sync
from py_ci_shared._core.scopes import function_param_names


def _fn(src: str) -> ast.AST:
    return ast.parse(src).body[0]


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("def f(a, /, b, *c, d):\n    pass\n", {"a", "b", "c", "d"}),
        ("async def f(a, *, b):\n    pass\n", {"a", "b"}),
        ("def f(a, **kw):\n    pass\n", {"a"}),
        ("x = 1\n", set()),
    ],
)
def test_the_helper_names_the_parameters_a_function_binds(src, expected) -> None:
    assert function_param_names(ast.parse(src).body[0] if src.startswith("x") else _fn(src)) == expected


def test_both_scanners_use_the_one_helper() -> None:
    """Negative control (52-F16): before the change each scanner had its own function, so the identity check failed."""
    assert db_transaction_completeness._param_names is function_param_names
    assert gpu_timing_sync._param_names is function_param_names
