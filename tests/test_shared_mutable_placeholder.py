"""Unit tests for shared_mutable_placeholder: a mutable value built once in an enclosing scope and returned by reference."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared.shared_mutable_placeholder import assert_no_shared_mutable_placeholders, find_shared_mutable_placeholders


def _lines(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "mod.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return [f.message for f in find_shared_mutable_placeholders([p], min_files=1)]


@pytest.mark.parametrize("value", ["[]", "{}", "set()", "dict()", "Rows()"])
def test_module_placeholder_returned_by_reference_is_found(tmp_path, value):
    body = f"""
        class Rows:
            pass

        _EMPTY = {value}

        def fetch():
            return _EMPTY
    """
    lines = _lines(tmp_path, body)
    assert len(lines) == 1 and "_EMPTY" in lines[0]


def test_placeholder_of_an_enclosing_function_is_found(tmp_path):
    body = """
        def make_reader():
            cache = {}

            def read(key):
                return cache

            return read
    """
    assert len(_lines(tmp_path, body)) == 1


def test_fresh_value_inside_the_function_is_not_a_finding(tmp_path):
    body = """
        class Rows:
            pass

        def fetch():
            rows = Rows()
            return rows

        def fetch_direct():
            return Rows()
    """
    assert _lines(tmp_path, body) == []


@pytest.mark.parametrize("ret", ["list(_EMPTY)", "_EMPTY.copy()", "tuple(_EMPTY)", "_EMPTY[:]"])
def test_returned_copy_is_not_a_finding(tmp_path, ret):
    body = f"""
        _EMPTY = []

        def fetch():
            return {ret}
    """
    assert _lines(tmp_path, body) == []


def test_local_rebinding_of_the_name_is_not_the_placeholder(tmp_path):
    body = """
        _EMPTY = []

        def fetch(_EMPTY):
            return _EMPTY
    """
    assert _lines(tmp_path, body) == []


def test_immutable_constructor_is_not_a_placeholder(tmp_path):
    body = """
        from pathlib import Path

        _ROOT = Path(".")

        def root():
            return _ROOT
    """
    assert _lines(tmp_path, body) == []


def test_assert_raises_on_a_shared_placeholder_and_passes_when_clean(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("_E = []\n\ndef f():\n    return _E\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="shared-mutable-placeholder"):
        assert_no_shared_mutable_placeholders([bad])
    good = tmp_path / "good.py"
    good.write_text("def f():\n    return []\n", encoding="utf-8")
    assert_no_shared_mutable_placeholders([good])
