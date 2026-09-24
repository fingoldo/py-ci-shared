"""Unit tests for function_length: no new long function, and the long ones may not grow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.function_length import assert_functions_do_not_grow, function_lengths, length_problems, write_length_baseline


def _module(tmp_path: Path, body_lines: int) -> Path:
    body = "\n".join("    x = 1" for _ in range(body_lines))
    p = tmp_path / "m.py"
    p.write_text(f"class K:\n    def meth(self):\n        pass\n\n\ndef long():\n{body}\n    def inner():\n        pass\n", encoding="utf-8")
    return p


def test_lengths_and_qualnames(tmp_path):
    p = _module(tmp_path, 4)
    assert function_lengths([p], tmp_path) == {"m.py::K.meth": 2, "m.py::long": 7, "m.py::long.<locals>.inner": 2}


class TestProblems:
    def test_a_new_function_over_the_limit(self):
        (p,) = length_problems({"m.py::f": 11}, {}, limit=10)
        assert "over the 10-line limit" in p

    def test_exactly_at_the_limit_is_allowed(self):
        assert length_problems({"m.py::f": 10}, {}, limit=10) == []

    def test_a_baselined_function_may_not_grow(self):
        (p,) = length_problems({"m.py::f": 21}, {"m.py::f": 20}, limit=10)
        assert "over its ceiling of 20" in p

    def test_a_shrink_is_locked_in(self):
        (p,) = length_problems({"m.py::f": 19}, {"m.py::f": 20}, limit=10)
        assert "refresh the baseline" in p

    def test_a_removed_function_leaves_the_baseline(self):
        (p,) = length_problems({}, {"m.py::gone": 20}, limit=10)
        assert "in the baseline but gone" in p


def test_baseline_round_trip_and_assert(tmp_path):
    p = _module(tmp_path, 12)
    baseline = tmp_path / "b.json"
    write_length_baseline(baseline, function_lengths([p], tmp_path), limit=10)
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"m.py::long": 15}
    assert_functions_do_not_grow([p], tmp_path, baseline, limit=10, min_functions=1)
    _module(tmp_path, 13)
    with pytest.raises(pytest.fail.Exception, match="over its ceiling of 15"):
        assert_functions_do_not_grow([p], tmp_path, baseline, limit=10, min_functions=1)


def test_assert_refuses_to_measure_nothing(tmp_path):
    with pytest.raises(pytest.fail.Exception, match="measured"):
        assert_functions_do_not_grow([], tmp_path, tmp_path / "b.json")


class TestAuditRegressions:
    def test_an_async_function_over_the_limit_is_measured_and_reported(self, tmp_path):
        body = "\n".join("    await x()" for _ in range(12))
        p = tmp_path / "a.py"
        p.write_text(f"async def fetch():\n{body}\n", encoding="utf-8")
        lengths = function_lengths([p], tmp_path)
        assert lengths == {"a.py::fetch": 13}
        (problem,) = length_problems(lengths, {}, limit=10)
        assert problem.startswith("a.py::fetch: 13 lines, over the 10-line limit")

    def test_the_baseline_write_excludes_a_function_exactly_at_the_limit(self, tmp_path):
        baseline = tmp_path / "b.json"
        write_length_baseline(baseline, {"m.py::at": 10, "m.py::over": 11, "m.py::under": 9}, limit=10)
        assert json.loads(baseline.read_text(encoding="utf-8")) == {"m.py::over": 11}

    def test_a_short_setter_does_not_hide_a_long_getter(self, tmp_path):
        getter = "\n".join("        x = 1" for _ in range(20))
        p = tmp_path / "p.py"
        p.write_text(
            f"class K:\n    @property\n    def v(self):\n{getter}\n        return x\n\n    @v.setter\n    def v(self, value):\n        self._v = value\n",
            encoding="utf-8",
        )
        assert function_lengths([p], tmp_path) == {"p.py::K.v": 22}

    def test_advice_for_a_baselined_function_now_within_the_limit_is_to_remove_it(self):
        (p,) = length_problems({"m.py::f": 8}, {"m.py::f": 20}, limit=10)
        assert "remove the entry" in p and "refresh the baseline to lock" not in p
        (q,) = length_problems({"m.py::f": 15}, {"m.py::f": 20}, limit=10)
        assert "refresh the baseline to lock the gain in" in q

    def test_an_unparsable_file_fails_the_gate(self, tmp_path):
        good = _module(tmp_path, 2)
        bad = tmp_path / "broken.py"
        bad.write_text("def f(:\n" + "    x = 1\n" * 600, encoding="utf-8")
        baseline = tmp_path / "b.json"
        baseline.write_text("{}", encoding="utf-8")
        assert_functions_do_not_grow([good], tmp_path, baseline, limit=10, min_functions=1)
        with pytest.raises(pytest.fail.Exception, match=r"broken.py:1: unparsable"):
            assert_functions_do_not_grow([good, bad], tmp_path, baseline, limit=10, min_functions=1)

    def test_a_missing_baseline_fails_and_a_refresh_creates_it(self, tmp_path):
        p = _module(tmp_path, 12)
        baseline = tmp_path / "b.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_functions_do_not_grow([p], tmp_path, baseline, limit=10, min_functions=1)
        with pytest.raises(pytest.skip.Exception):
            assert_functions_do_not_grow([p], tmp_path, baseline, limit=10, min_functions=1, refresh=True)
        assert json.loads(baseline.read_text(encoding="utf-8")) == {"m.py::long": 15}
        assert_functions_do_not_grow([p], tmp_path, baseline, limit=10, min_functions=1)

    def test_a_bom_file_is_measured(self, tmp_path):
        p = tmp_path / "bom.py"
        p.write_bytes(b"\xef\xbb\xbfdef f():\n    return 1\n")
        assert function_lengths([p], tmp_path) == {"bom.py::f": 2}
