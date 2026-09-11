"""Unit tests for the vacuous-loop-assertion check. Real files on disk, no mocking, matching
this package's convention."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.vacuous_loop_assertions import (
    assert_no_new_floorless_loop,
    find_floorless_loops,
)


def _module(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# The 16b.6 shape: the real defect never satisfies the `if`, the loop body never runs.
_FLOORLESS_CONDITIONAL_ASSERT = """
    def test_no_active_generated_column():
        text = read_migration()
        for line in text.splitlines():
            if "is_dangerous" in line and "GENERATED ALWAYS" in line:
                assert line.lstrip().startswith("--")
"""

# The 16b.15 shape: nothing asserts the extractor yielded anything.
_FLOORLESS_EXTRACTOR_CONSUMER = """
    def test_no_bad_glyph_in_logs():
        for _lineno, value in extract_literals(module):
            assert "bad" not in value
"""

_WITH_A_FLOOR_ON_THE_ITERABLE = """
    def test_no_active_generated_column():
        text = read_migration()
        lines = text.splitlines()
        assert lines
        for line in lines:
            if "is_dangerous" in line and "GENERATED ALWAYS" in line:
                assert line.lstrip().startswith("--")
"""

_WITH_A_FLOOR_VIA_LIST_CAPTURE = """
    def test_no_bad_glyph_in_logs():
        found = list(extract_literals(module))
        assert found
        for _lineno, value in found:
            assert "bad" not in value
"""

_NOT_ASSERT_ONLY_HAS_A_SIDE_EFFECT = """
    def test_collects_and_checks(caplog):
        seen = []
        for _lineno, value in extract_literals(module):
            seen.append(value)
            assert "bad" not in value
        assert seen
"""

_EMPTY_LOOP_BODY_IS_NOT_THIS_SHAPE = """
    def test_iterates_only():
        for x in candidates():
            pass
"""


class TestFindFloorlessLoops:
    def test_the_conditional_assert_shape_is_reported(self, tmp_path):
        path = _module(tmp_path, "bad.py", _FLOORLESS_CONDITIONAL_ASSERT)

        found = find_floorless_loops([path], tmp_path)

        assert len(found) == 1
        assert found[0].function == "test_no_active_generated_column"

    def test_the_extractor_consumer_shape_is_reported(self, tmp_path):
        path = _module(tmp_path, "bad.py", _FLOORLESS_EXTRACTOR_CONSUMER)

        found = find_floorless_loops([path], tmp_path)

        assert len(found) == 1

    def test_an_assert_on_the_iterable_itself_clears_it(self, tmp_path):
        path = _module(tmp_path, "good.py", _WITH_A_FLOOR_ON_THE_ITERABLE)

        assert find_floorless_loops([path], tmp_path) == []

    def test_a_captured_list_asserted_afterward_clears_it(self, tmp_path):
        path = _module(tmp_path, "good.py", _WITH_A_FLOOR_VIA_LIST_CAPTURE)

        assert find_floorless_loops([path], tmp_path) == []

    def test_a_loop_with_a_real_side_effect_is_not_assert_only(self, tmp_path):
        """`.append(...)` is not an assert, so this loop body is not the assert-only shape at
        all -- it is reported for neither reason a real gap would be flagged for."""
        path = _module(tmp_path, "good.py", _NOT_ASSERT_ONLY_HAS_A_SIDE_EFFECT)

        assert find_floorless_loops([path], tmp_path) == []

    def test_a_bare_pass_body_is_not_this_shape(self, tmp_path):
        """An empty loop body is a no-op a reader can already see; it is not the "looks like it
        checks something but silently does not" shape this exists for."""
        path = _module(tmp_path, "irrelevant.py", _EMPTY_LOOP_BODY_IS_NOT_THIS_SHAPE)

        assert find_floorless_loops([path], tmp_path) == []


class TestAssertNoNewFloorlessLoop:
    def test_a_clean_tree_passes(self, tmp_path):
        _module(tmp_path, "good.py", _WITH_A_FLOOR_ON_THE_ITERABLE)
        baseline = tmp_path / "baseline.json"
        baseline.write_text("{}", encoding="utf-8")

        assert_no_new_floorless_loop(files=[tmp_path / "good.py"], repo_root=tmp_path, baseline_path=baseline)

    def test_a_new_floorless_loop_fails(self, tmp_path):
        _module(tmp_path, "bad.py", _FLOORLESS_CONDITIONAL_ASSERT)
        baseline = tmp_path / "baseline.json"
        baseline.write_text("{}", encoding="utf-8")

        with pytest.raises(AssertionError, match="zero matches"):
            assert_no_new_floorless_loop(files=[tmp_path / "bad.py"], repo_root=tmp_path, baseline_path=baseline)

    def test_a_baselined_floorless_loop_does_not_fail(self, tmp_path):
        path = _module(tmp_path, "bad.py", _FLOORLESS_CONDITIONAL_ASSERT)
        found = find_floorless_loops([path], tmp_path)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({found[0].key: "known, tracked"}), encoding="utf-8")

        assert_no_new_floorless_loop(files=[path], repo_root=tmp_path, baseline_path=baseline)

    def test_a_stale_baseline_entry_fails_too(self, tmp_path):
        _module(tmp_path, "good.py", _WITH_A_FLOOR_ON_THE_ITERABLE)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"good.py::test_no_active_generated_column::5": "stale"}), encoding="utf-8")

        with pytest.raises(AssertionError, match="no longer describe"):
            assert_no_new_floorless_loop(files=[tmp_path / "good.py"], repo_root=tmp_path, baseline_path=baseline)
