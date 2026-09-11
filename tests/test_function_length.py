"""Unit tests for function_length: no new long function, and the long ones may not grow."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
