"""Unit tests for the optional-truthiness check. Real files on disk, no mocking."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.optional_truthiness import assert_optionals_test_for_none, find_truthiness_tests


def _module(tmp_path: Path, body: str, name: str = "m.py") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


class TestFindTruthinessTests:
    def test_an_optional_int_tested_for_truth_is_reported(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit and n >= limit:
                    return
        """,
        )

        findings = find_truthiness_tests(path)

        assert len(findings) == 1
        assert "`limit`" in findings[0]

    def test_is_not_none_is_accepted(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit is not None and n >= limit:
                    return
        """,
        )

        assert find_truthiness_tests(path) == []

    def test_an_optional_collection_is_left_alone(self, tmp_path):
        """An empty list read as absent is usually intentional, and reporting it drowns the
        signal. A dict[str, int] must not be mistaken for an optional int either."""
        path = _module(
            tmp_path,
            """
            def read(tags: list | None = None, mapping: dict[str, int] | None = None):
                if tags:
                    pass
                if mapping:
                    pass
        """,
        )

        assert find_truthiness_tests(path) == []

    def test_an_optional_string_is_left_alone(self, tmp_path):
        """Measured decision: including str produced 159 findings on one repo, nearly all
        of them an empty language code being read as absent, which is correct there."""
        path = _module(
            tmp_path,
            """
            def read(lang: str | None = None):
                if lang:
                    pass
        """,
        )

        assert find_truthiness_tests(path) == []

    def test_a_non_optional_number_is_left_alone(self, tmp_path):
        """Without None in the annotation there is no absent state to confuse 0 with."""
        path = _module(
            tmp_path,
            """
            def read(limit: int = 0):
                if limit:
                    pass
        """,
        )

        assert find_truthiness_tests(path) == []

    @pytest.mark.parametrize("annotation", ["Optional[int]", "Union[int, None]", "float | None"])
    def test_every_optional_spelling_is_recognised(self, tmp_path, annotation):
        path = _module(
            tmp_path,
            f"""
            def read(limit: {annotation} = None):
                if limit:
                    pass
        """,
        )

        assert len(find_truthiness_tests(path)) == 1

    def test_a_string_annotation_is_parsed_not_substring_matched(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: "int | None" = None):
                if limit:
                    pass
        """,
        )

        assert len(find_truthiness_tests(path)) == 1

    def test_it_reports_a_truthiness_test_inside_a_boolean_chain(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if ready and limit:
                    pass
        """,
        )

        assert len(find_truthiness_tests(path)) == 1


class TestAssertOptionalsTestForNone:
    def test_it_fails_on_a_new_finding(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit:
                    pass
        """,
        )

        with pytest.raises(pytest.fail.Exception, match="tested for truth"):
            assert_optionals_test_for_none(files=[path], repo_root=tmp_path)

    def test_a_baselined_finding_is_accepted(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit:
                    pass
        """,
        )
        known = find_truthiness_tests(path)

        assert_optionals_test_for_none(files=[path], repo_root=tmp_path, baseline=known)

    def test_it_fails_when_nothing_was_scanned(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_optionals_test_for_none(files=[], repo_root=tmp_path, min_subjects=1)


class TestAuditRegressions:
    def test_not_ifexp_while_assert_and_comprehension_filters_are_tests_for_truth(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None, items=()):
                if not limit:
                    pass
                size = 10 if limit else 0
                while limit:
                    break
                assert limit
                kept = [i for i in items if limit]
                if limit is None:
                    pass
        """,
        )

        lines = sorted(int(f.split(":")[1]) for f in find_truthiness_tests(path))

        assert lines == [3, 5, 6, 8, 9]

    def test_findings_are_repo_relative_and_duplicates_are_kept(self, tmp_path):
        body = """
            def read(limit: int | None = None):
                if limit:
                    pass
                if limit:
                    pass
        """
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = _module(tmp_path, body, "a/utils.py")
        b = _module(tmp_path, body, "b/utils.py")
        found_a = find_truthiness_tests(a, repo_root=tmp_path)
        assert [f.split(":")[0] for f in found_a] == ["a/utils.py", "a/utils.py"]
        with pytest.raises(pytest.fail.Exception, match=re.escape("b/utils.py")):
            assert_optionals_test_for_none(files=[a, b], repo_root=tmp_path, baseline=found_a)
        with pytest.raises(pytest.fail.Exception, match="1 optional"):
            assert_optionals_test_for_none(files=[a], repo_root=tmp_path, baseline=found_a[:1])
        assert_optionals_test_for_none(files=[a], repo_root=tmp_path, baseline=found_a)

    def test_a_baseline_entry_survives_a_line_shift_and_a_stale_one_fails(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit:
                    pass
        """,
        )
        known = find_truthiness_tests(path, repo_root=tmp_path)
        path.write_text("# a new comment\n# and another\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        assert find_truthiness_tests(path, repo_root=tmp_path) != known
        assert_optionals_test_for_none(files=[path], repo_root=tmp_path, baseline=known)
        path.write_text("def read(limit: int | None = None):\n    if limit is not None:\n        pass\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="no longer found"):
            assert_optionals_test_for_none(files=[path], repo_root=tmp_path, baseline=known)

    def test_a_nested_def_rebinding_the_name_is_judged_on_its_own_parameter(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def outer(limit: int | None = None):
                def g(limit: list):
                    if limit:
                        pass
                def h():
                    if limit:
                        pass
                return g, h
        """,
        )

        assert [int(f.split(":")[1]) for f in find_truthiness_tests(path)] == [7]

    def test_annotated_is_unwrapped(self, tmp_path):
        path = _module(
            tmp_path,
            """
            from typing import Annotated, Optional
            def read(a: Annotated[int | None, "doc"] = None, b: Optional[Annotated[float, "x"]] = None, c: Annotated[list | None, 1] = None):
                if a or b or c:
                    pass
        """,
        )

        assert sorted(f.split("`")[1] for f in find_truthiness_tests(path)) == ["a", "b"]

    def test_bom_and_unparsable_files(self, tmp_path):
        bom = tmp_path / "bom.py"
        bom.write_bytes(b"\xef\xbb\xbfdef read(limit: int | None = None):\n    if limit:\n        pass\n")
        bad = _module(tmp_path, "def (:\n", "bad.py")
        assert len(find_truthiness_tests(bom)) == 1
        (problem,) = find_truthiness_tests(bad)
        assert "unparsable" in problem
        with pytest.raises(pytest.fail.Exception, match="could not be parsed"):
            assert_optionals_test_for_none(files=[bad, bom], repo_root=tmp_path, baseline=find_truthiness_tests(bom, repo_root=tmp_path))
