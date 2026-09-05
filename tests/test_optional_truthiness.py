"""Unit tests for the optional-truthiness check. Real files on disk, no mocking."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.optional_truthiness import assert_optionals_test_for_none, find_truthiness_tests


def _module(tmp_path: Path, body: str, name: str = "m.py") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


class TestFindTruthinessTests:
    def test_an_optional_int_tested_for_truth_is_reported(self, tmp_path):
        path = _module(tmp_path, """
            def read(limit: int | None = None):
                if limit and n >= limit:
                    return
        """)

        findings = find_truthiness_tests(path)

        assert len(findings) == 1
        assert "`limit`" in findings[0]

    def test_is_not_none_is_accepted(self, tmp_path):
        path = _module(tmp_path, """
            def read(limit: int | None = None):
                if limit is not None and n >= limit:
                    return
        """)

        assert find_truthiness_tests(path) == []

    def test_an_optional_collection_is_left_alone(self, tmp_path):
        """An empty list read as absent is usually intentional, and reporting it drowns the
        signal. A dict[str, int] must not be mistaken for an optional int either."""
        path = _module(tmp_path, """
            def read(tags: list | None = None, mapping: dict[str, int] | None = None):
                if tags:
                    pass
                if mapping:
                    pass
        """)

        assert find_truthiness_tests(path) == []

    def test_an_optional_string_is_left_alone(self, tmp_path):
        """Measured decision: including str produced 159 findings on one repo, nearly all
        of them an empty language code being read as absent, which is correct there."""
        path = _module(tmp_path, """
            def read(lang: str | None = None):
                if lang:
                    pass
        """)

        assert find_truthiness_tests(path) == []

    def test_a_non_optional_number_is_left_alone(self, tmp_path):
        """Without None in the annotation there is no absent state to confuse 0 with."""
        path = _module(tmp_path, """
            def read(limit: int = 0):
                if limit:
                    pass
        """)

        assert find_truthiness_tests(path) == []

    @pytest.mark.parametrize("annotation", ["Optional[int]", "Union[int, None]", "float | None"])
    def test_every_optional_spelling_is_recognised(self, tmp_path, annotation):
        path = _module(tmp_path, f"""
            def read(limit: {annotation} = None):
                if limit:
                    pass
        """)

        assert len(find_truthiness_tests(path)) == 1

    def test_a_string_annotation_is_parsed_not_substring_matched(self, tmp_path):
        path = _module(tmp_path, '''
            def read(limit: "int | None" = None):
                if limit:
                    pass
        ''')

        assert len(find_truthiness_tests(path)) == 1

    def test_it_reports_a_truthiness_test_inside_a_boolean_chain(self, tmp_path):
        path = _module(tmp_path, """
            def read(limit: int | None = None):
                if ready and limit:
                    pass
        """)

        assert len(find_truthiness_tests(path)) == 1


class TestAssertOptionalsTestForNone:
    def test_it_fails_on_a_new_finding(self, tmp_path):
        path = _module(tmp_path, """
            def read(limit: int | None = None):
                if limit:
                    pass
        """)

        with pytest.raises(pytest.fail.Exception, match="tested for truth"):
            assert_optionals_test_for_none(files=[path], repo_root=tmp_path)

    def test_a_baselined_finding_is_accepted(self, tmp_path):
        path = _module(tmp_path, """
            def read(limit: int | None = None):
                if limit:
                    pass
        """)
        known = find_truthiness_tests(path)

        assert_optionals_test_for_none(files=[path], repo_root=tmp_path, baseline=known)

    def test_it_fails_when_nothing_was_scanned(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_optionals_test_for_none(files=[], repo_root=tmp_path, min_subjects=1)
