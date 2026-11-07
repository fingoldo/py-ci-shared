"""The shared callee-name helpers: Name gives its id, Attribute its last attribute, everything else a miss (audit 52-F2)."""

from __future__ import annotations

import ast

import pytest

from py_ci_shared._core.call_names import call_name, call_name_or_none, func_name, func_name_or_none

CASES = [
    ("f(1)", "f"),
    ("a.b.c(1)", "c"),
    ("f()(1)", None),
    ("x[0](1)", None),
    ("(lambda: 1)()", None),
    ("a.b().c(1)", "c"),
]


@pytest.mark.parametrize(("source", "expected"), CASES)
def test_call_shapes(source, expected):
    call = ast.parse(source, mode="eval").body
    assert call_name_or_none(call) == expected
    assert call_name(call) == (expected or "")
    assert func_name_or_none(call.func) == expected
    assert func_name(call.func) == (expected or "")


def test_a_node_without_a_callee_is_a_miss():
    node = ast.parse("a.b", mode="eval").body
    assert call_name_or_none(node) is None
    assert call_name(node) == ""
    assert func_name_or_none(None) is None
    assert func_name(None) == ""
