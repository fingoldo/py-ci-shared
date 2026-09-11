"""`deferred_drift` fails on growth, on a new list, on a shrink and on a vanished list, and not otherwise."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.deferred_drift import assert_deferred_lists_not_grown, drift_problems


class TestTheRules:
    def test_unchanged_is_silent(self):
        assert drift_problems({"a::_X": 3}, {"a::_X": 3}) == []

    def test_growth_and_a_new_list_fail(self):
        problems = drift_problems({"a::_X": 3}, {"a::_X": 4, "b::_Y": 1})
        assert any(p.startswith("GREW a::_X") for p in problems)
        assert any(p.startswith("NEW list b::_Y") for p in problems)

    def test_a_shrink_and_a_vanished_list_fail_by_default(self):
        problems = drift_problems({"a::_X": 3, "b::_Y": 2}, {"a::_X": 1})
        assert any(p.startswith("SHRANK a::_X") for p in problems)
        assert any(p.startswith("GONE b::_Y") for p in problems)

    def test_the_old_lenient_behaviour_is_available(self):
        assert drift_problems({"a::_X": 3, "b::_Y": 2}, {"a::_X": 1}, fail_on_shrink=False) == []


def _meta(tmp_path: Path, entries: int) -> Path:
    meta = tmp_path / "test_meta"
    meta.mkdir(exist_ok=True)
    items = ", ".join(f'"e{i}"' for i in range(entries))
    (meta / "test_thing.py").write_text(f"_USER_DEFERRED_THINGS = {{{items}}}\n\n\ndef test_x():\n    pass\n", encoding="utf-8")
    return meta


class TestTheRatchet:
    def test_a_missing_baseline_is_written_and_skips(self, tmp_path):
        meta = _meta(tmp_path, 2)
        baseline = tmp_path / "b.json"
        with pytest.raises(pytest.skip.Exception):
            assert_deferred_lists_not_grown(meta, baseline)
        assert json.loads(baseline.read_text(encoding="utf-8")) == {"test_thing::_USER_DEFERRED_THINGS": 2}

    def test_growth_fails_and_a_matching_baseline_passes(self, tmp_path):
        baseline = tmp_path / "b.json"
        baseline.write_text(json.dumps({"test_thing::_USER_DEFERRED_THINGS": 2}), encoding="utf-8")
        assert_deferred_lists_not_grown(_meta(tmp_path, 2), baseline)
        with pytest.raises(pytest.fail.Exception, match="GREW"):
            assert_deferred_lists_not_grown(_meta(tmp_path, 3), baseline)

    def test_a_shrink_fails_until_the_baseline_follows(self, tmp_path):
        baseline = tmp_path / "b.json"
        baseline.write_text(json.dumps({"test_thing::_USER_DEFERRED_THINGS": 2}), encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="SHRANK"):
            assert_deferred_lists_not_grown(_meta(tmp_path, 1), baseline)

    def test_the_floor(self, tmp_path):
        meta = tmp_path / "test_meta"
        meta.mkdir()
        (meta / "test_empty.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
        baseline = tmp_path / "b.json"
        baseline.write_text("{}", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_deferred_lists_not_grown(meta, baseline)
