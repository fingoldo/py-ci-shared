"""Unit tests for the spec-bound-double check. Real files on disk, no mocking."""

from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.spec_bound_doubles import (
    assert_doubles_are_spec_bound,
    find_unbound_doubles,
    is_bare_mock,
)

_ENTRY = frozenset({"claim_lock"})
_HINTS = frozenset({"session"})


def _test_file(tmp_path: Path, body: str, name: str = "test_x.py") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


class TestIsBareMock:
    @pytest.mark.parametrize("src", ["AsyncMock()", "Mock()", "MagicMock()", "mock.AsyncMock()"])
    def test_a_spec_less_mock_is_bare(self, src):
        assert is_bare_mock(ast.parse(src, mode="eval").body)

    @pytest.mark.parametrize("src", ["AsyncMock(spec=AsyncSession)", "MagicMock(spec_set=X)", "AsyncMock(SomeClass)"])
    def test_a_bound_mock_is_not(self, src):
        assert not is_bare_mock(ast.parse(src, mode="eval").body)

    def test_an_unrelated_call_is_not_a_mock(self):
        assert not is_bare_mock(ast.parse("dict()", mode="eval").body)


class TestFindUnboundDoubles:
    def test_a_mock_named_like_the_duck_typed_object_is_reported(self, tmp_path):
        path = _test_file(tmp_path, """
            def test_a():
                session = AsyncMock()
                claim_lock(session, "k")
        """)

        assert find_unbound_doubles(path, entry_points=_ENTRY, name_hints=_HINTS) == [3]

    def test_a_mock_passed_straight_to_the_entry_point_is_reported(self, tmp_path):
        path = _test_file(tmp_path, """
            def test_a():
                claim_lock(AsyncMock(), "k")
        """)

        assert find_unbound_doubles(path, entry_points=_ENTRY, name_hints=_HINTS) == [3]

    def test_an_unrelated_mock_is_left_alone(self, tmp_path):
        """The narrow scope is the point: a bare mock is the right tool for a commit
        coroutine or an HTTP client, and a file-wide ban flags those wrongly."""
        path = _test_file(tmp_path, """
            def test_a():
                client = AsyncMock()
                session = AsyncMock(spec=AsyncSession)
                claim_lock(session, "k")
        """)

        assert find_unbound_doubles(path, entry_points=_ENTRY, name_hints=_HINTS) == []

    def test_an_unparsable_file_is_skipped(self, tmp_path):
        path = _test_file(tmp_path, "def (:\n")

        assert find_unbound_doubles(path, entry_points=_ENTRY, name_hints=_HINTS) == []


class TestAssertDoublesAreSpecBound:
    def test_it_fails_on_an_unbound_session_double(self, tmp_path):
        path = _test_file(tmp_path, """
            def test_a():
                session = AsyncMock()
                claim_lock(session, "k")
        """)

        with pytest.raises(pytest.fail.Exception, match="auto-creates"):
            assert_doubles_are_spec_bound(
                files=[path], entry_points=_ENTRY, name_hints=_HINTS, repo_root=tmp_path,
            )

    def test_it_passes_when_every_double_is_bound(self, tmp_path):
        path = _test_file(tmp_path, """
            def test_a():
                session = AsyncMock(spec=AsyncSession)
                claim_lock(session, "k")
        """)

        assert_doubles_are_spec_bound(
            files=[path], entry_points=_ENTRY, name_hints=_HINTS, repo_root=tmp_path,
        )

    def test_it_fails_when_no_file_drives_the_entry_point(self, tmp_path):
        path = _test_file(tmp_path, "def test_a():\n    assert True\n")

        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_doubles_are_spec_bound(
                files=[path], entry_points=_ENTRY, name_hints=_HINTS, repo_root=tmp_path, min_subjects=1,
            )
