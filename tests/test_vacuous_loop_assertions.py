"""Unit tests for the vacuous-loop-assertion check. Real files on disk, no mocking, matching
this package's convention."""

from __future__ import annotations

import json
import re
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

    def test_moving_a_baselined_loop_down_the_file_does_not_fail(self, tmp_path):
        path = _module(tmp_path, "bad.py", _FLOORLESS_CONDITIONAL_ASSERT)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({find_floorless_loops([path], tmp_path)[0].key: "known"}), encoding="utf-8")
        path.write_text("# a line added above the test\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

        assert_no_new_floorless_loop(files=[path], repo_root=tmp_path, baseline_path=baseline)

    def test_a_line_numbered_baseline_key_is_still_honoured(self, tmp_path):
        path = _module(tmp_path, "bad.py", _FLOORLESS_CONDITIONAL_ASSERT)
        loop = find_floorless_loops([path], tmp_path)[0]
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({f"{loop.scope}::{loop.lineno + 40}": "written before keys were ordinal"}), encoding="utf-8")

        assert_no_new_floorless_loop(files=[path], repo_root=tmp_path, baseline_path=baseline)

    def test_a_second_floorless_loop_in_a_baselined_function_fails(self, tmp_path):
        path = _module(tmp_path, "bad.py", _FLOORLESS_CONDITIONAL_ASSERT)
        found = find_floorless_loops([path], tmp_path)
        assert len(found) == 1
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({found[0].key: "known"}), encoding="utf-8")
        text = path.read_text(encoding="utf-8")
        body_start = text.index("    for ", text.index(f"def {found[0].function}"))
        # Renamed so neither loop's assert reads as the other's floor (a floor may name the loop variable).
        loop_block = text[body_start:].split("\n\n", 1)[0].replace("line", "row") + "\n"
        path.write_text(text[:body_start] + loop_block + text[body_start:], encoding="utf-8")
        assert len(find_floorless_loops([path], tmp_path)) == 2

        with pytest.raises(AssertionError, match="zero matches"):
            assert_no_new_floorless_loop(files=[path], repo_root=tmp_path, baseline_path=baseline)

    def test_a_stale_baseline_entry_fails_too(self, tmp_path):
        _module(tmp_path, "good.py", _WITH_A_FLOOR_ON_THE_ITERABLE)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"good.py::test_no_active_generated_column::5": "stale"}), encoding="utf-8")

        with pytest.raises(AssertionError, match="no longer describe"):
            assert_no_new_floorless_loop(files=[tmp_path / "good.py"], repo_root=tmp_path, baseline_path=baseline)


_LITERAL_TUPLE_NEEDS_NO_FLOOR = """
    def test_every_id_is_mounted():
        ids = mounted_ids()
        for name in ("a", "b", "c"):
            assert name in ids
"""

_EMPTY_LITERAL_IS_STILL_THE_SHAPE = """
    def test_nothing_at_all():
        for name in []:
            assert name in mounted_ids()
"""

_LITERAL_THROUGH_A_WRAPPER = """
    def test_every_id_is_mounted():
        ids = mounted_ids()
        for i, name in enumerate(("a", "b")):
            assert name in ids
"""


class TestALiteralIterableNeedsNoFloor:
    """`for x in ("a", "b"): assert ...` cannot arrive empty, and the floor a reader would add is
    an assertion about the line directly above it."""

    def test_a_literal_tuple_is_not_flagged(self, tmp_path):
        path = _module(tmp_path, "ok.py", _LITERAL_TUPLE_NEEDS_NO_FLOOR)

        assert find_floorless_loops([path], tmp_path) == []

    def test_a_literal_through_enumerate_is_not_flagged(self, tmp_path):
        path = _module(tmp_path, "ok.py", _LITERAL_THROUGH_A_WRAPPER)

        assert find_floorless_loops([path], tmp_path) == []

    def test_an_EMPTY_literal_is_still_reported(self, tmp_path):
        """The exemption is for "a reader can count it", not for "it is written at the loop" -- a
        loop over `[]` is exactly the silent pass this check exists for."""
        path = _module(tmp_path, "bad.py", _EMPTY_LITERAL_IS_STILL_THE_SHAPE)

        assert len(find_floorless_loops([path], tmp_path)) == 1


class TestAuditRegressions:
    def test_an_assert_in_another_floorless_loop_is_not_a_floor(self, tmp_path):
        path = _module(
            tmp_path,
            "t.py",
            """
            def test_two_loops():
                for x in load_a():
                    assert x > 0
                for x in load_b():
                    assert x < 9
            """,
        )
        assert [loop.lineno for loop in find_floorless_loops([path], tmp_path)] == [3, 5]

    def test_a_floor_in_an_enclosing_loop_still_counts(self, tmp_path):
        path = _module(
            tmp_path,
            "t.py",
            """
            def test_nested():
                for case in cases():
                    rows = run(case)
                    assert rows
                    for row in rows:
                        assert row.ok
            """,
        )
        assert find_floorless_loops([path], tmp_path) == []

    def test_a_nested_function_s_loop_is_reported_once_under_its_owner(self, tmp_path):
        path = _module(
            tmp_path,
            "t.py",
            """
            def test_outer():
                def inner():
                    for x in load():
                        assert x
                inner()
            """,
        )
        found = find_floorless_loops([path], tmp_path)
        assert [(loop.function, loop.lineno) for loop in found] == [("inner", 4)]

    @pytest.mark.parametrize(
        "body",
        [
            "if not i:\n            continue\n        assert i > 0",
            "if i is None:\n            raise AssertionError('none')",
            "if i < 0:\n            pytest.fail('negative')\n        pass",
            "with subtests.test(i=i):\n            assert i > 0",
        ],
        ids=["continue", "raise", "pytest-fail", "subtests"],
    )
    def test_flow_control_raise_fail_and_subtests_bodies_are_assert_only(self, tmp_path, body):
        path = _module(tmp_path, "t.py", f"def test_x(subtests):\n    for i in load():\n        {body}\n")
        assert len(find_floorless_loops([path], tmp_path)) == 1

    def test_a_body_without_any_check_is_still_not_the_shape(self, tmp_path):
        path = _module(tmp_path, "t.py", "def test_x():\n    for i in load():\n        if not i:\n            continue\n        pass\n")
        assert find_floorless_loops([path], tmp_path) == []

    def test_an_unpacked_literal_is_not_a_non_empty_one(self, tmp_path):
        path = _module(tmp_path, "t.py", "def test_x(m):\n    for q in [*m]:\n        assert q\n    for r in [0, *m]:\n        assert r in m\n")
        assert [loop.lineno for loop in find_floorless_loops([path], tmp_path)] == [2]

    def test_keys_do_not_depend_on_the_working_directory(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / "tests").mkdir(parents=True)
        path = _module(repo / "tests", "t.py", "def test_x():\n    for i in load():\n        assert i\n")
        monkeypatch.chdir(repo)
        (from_repo,) = find_floorless_loops([Path("tests/t.py")], repo)
        monkeypatch.chdir(repo / "tests")
        (from_tests,) = find_floorless_loops([Path("t.py")], repo)
        assert from_repo.key == from_tests.key == "tests/t.py::test_x::#1"
        outside = _module(tmp_path, "t_out.py", "def test_y():\n    for i in load():\n        assert i\n")
        (loop,) = find_floorless_loops([outside], repo)
        assert loop.path.endswith("/t_out.py")
        assert path.exists()

    def test_bom_and_unparsable_files(self, tmp_path):
        bom = tmp_path / "t_bom.py"
        bom.write_bytes(b"\xef\xbb\xbfdef test_x():\n    for i in load():\n        assert i\n")
        assert len(find_floorless_loops([bom], tmp_path)) == 1
        bad = _module(tmp_path, "t_bad.py", "def (:\n")
        with pytest.raises(AssertionError, match=re.escape("t_bad.py")):
            find_floorless_loops([bom, bad], tmp_path)
        assert len(find_floorless_loops([bom, bad], tmp_path, allow_unparsed=True)) == 1
