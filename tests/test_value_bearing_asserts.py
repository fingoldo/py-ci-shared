"""`value_bearing_asserts` separates narrowing from value checks, and its ratchet only shrinks."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.value_bearing_asserts import assert_no_value_bearing_asserts, find_value_bearing_asserts, is_narrowing_assert


def _test(src: str) -> ast.expr:
    node = ast.parse(src).body[0]
    assert isinstance(node, ast.Assert)
    return node.test


class TestTheClassifier:
    @pytest.mark.parametrize("src", ["assert a is not None", "assert a is None", "assert x", "assert self.conn", "assert rows[0]", "assert a is not None and b is not None"])
    def test_narrowing_forms(self, src):
        assert is_narrowing_assert(_test(src))

    @pytest.mark.parametrize("src", ["assert a + b < 1.0", "assert n >= 1", "assert x in ('a', 'b')", "assert a == b", "assert f(x)", "assert a is not None and n > 0"])
    def test_value_checks(self, src):
        assert not is_narrowing_assert(_test(src))

    def test_isinstance_is_a_value_check_unless_allowed(self):
        test = _test("assert isinstance(x, int)")
        assert not is_narrowing_assert(test)
        assert is_narrowing_assert(test, allow_isinstance=True)

    def test_allowing_isinstance_does_not_allow_what_rides_with_it(self):
        assert not is_narrowing_assert(_test("assert isinstance(p, tuple) and len(p) in (2, 3)"), allow_isinstance=True)


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    (root / "tests").mkdir(parents=True)
    (root / "a.py").write_text("def f(x):\n    assert x is not None\n    assert x > 0\n    return x\n", encoding="utf-8")
    (root / "tests" / "test_a.py").write_text("def test_f():\n    assert f(1) == 1\n", encoding="utf-8")
    return root


class TestTheScan:
    def test_production_only_and_counted(self, tmp_path):
        offenders, seen = find_value_bearing_asserts(_package(tmp_path))
        assert offenders == ["a.py:3  x > 0"]
        assert seen == 2, "the tests/ assert must not be counted"


class TestTheRatchet:
    def test_a_value_assert_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match=re.escape("a.py:3")):
            assert_no_value_bearing_asserts(_package(tmp_path))

    def test_a_baseline_accepts_debt_and_a_stale_entry_fails(self, tmp_path):
        root = _package(tmp_path)
        baseline = tmp_path / "b.json"
        baseline.write_text(json.dumps(["a.py::x > 0"]), encoding="utf-8")
        assert_no_value_bearing_asserts(root, baseline_path=baseline)
        baseline.write_text(json.dumps(["a.py::x > 0", "gone.py::y < 1"]), encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=re.escape("gone.py")):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)

    def test_a_missing_baseline_is_written_and_skips(self, tmp_path):
        baseline = tmp_path / "b.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_value_bearing_asserts(_package(tmp_path), baseline_path=baseline)
        assert json.loads(baseline.read_text(encoding="utf-8")) == ["a.py::x > 0"]

    def test_the_floor(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="not reaching the code"):
            assert_no_value_bearing_asserts(_package(tmp_path), min_asserts_seen=10)
