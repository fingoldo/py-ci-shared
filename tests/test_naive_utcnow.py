"""Unit tests for the naive-utcnow check. Real files on disk, same no-mocking convention."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.naive_utcnow import assert_no_naive_utcnow, find_naive_utcnow


def _module(tmp_path: Path, body: str, name: str = "m.py") -> Path:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


class TestItFindsRealCalls:
    def test_a_plain_call_is_found(self, tmp_path):
        _module(tmp_path, "from datetime import datetime\nx = datetime.utcnow()\n")
        found = find_naive_utcnow(tmp_path)
        assert len(found) == 1
        assert found[0].startswith("m.py:2:")

    @pytest.mark.parametrize(
        "body",
        [
            "import datetime as dt\nx = dt.datetime.utcnow()\n",
            "import datetime as _dt\nx = _dt.datetime.utcnow()\n",
            "from datetime import datetime\nx = datetime.utcnow().date()\n",
            "from datetime import datetime\nx = datetime.utcnow().isoformat()\n",
        ],
    )
    def test_every_import_spelling_is_caught(self, body, tmp_path):
        """Matched on the ATTRIBUTE, because these repos use all of these spellings and a check
        that enumerated them would miss the next one."""
        assert find_naive_utcnow(_module(tmp_path, body))

    def test_the_date_case_is_not_excused(self, tmp_path):
        """`utcnow().date()` and `now(UTC).date()` agree TODAY, which is exactly why a value-based
        test would not catch it. What is wrong is the frame living in a convention."""
        assert find_naive_utcnow(_module(tmp_path, "import datetime as dt\nd = dt.datetime.utcnow().date()\n"))


class TestItDoesNotFireOnProse:
    """The whole reason this is an AST walk. Both of these defeated a substring version in a real
    repo: production_scrapers abandoned its sweep over the comment case, and dashboard's version
    skips `startswith("#")` lines but still reads a docstring as code.
    """

    def test_a_comment_naming_it_is_not_a_call(self, tmp_path):
        body = "# utcnow() was replaced here; see the wave-17 note\nfrom datetime import UTC, datetime\nx = datetime.now(UTC)\n"
        assert find_naive_utcnow(_module(tmp_path, body)) == []

    def test_a_trailing_comment_naming_it_is_not_a_call(self, tmp_path):
        body = "from datetime import UTC, datetime\nx = datetime.now(UTC)  # not utcnow(), deliberately\n"
        assert find_naive_utcnow(_module(tmp_path, body)) == []

    def test_a_docstring_naming_it_is_not_a_call(self, tmp_path):
        body = '"""This module used to call datetime.utcnow() and no longer does."""\nx = 1\n'
        assert find_naive_utcnow(_module(tmp_path, body)) == []

    def test_the_correct_replacement_is_not_flagged(self, tmp_path):
        body = "from datetime import UTC, datetime\nx = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')\n"
        assert find_naive_utcnow(_module(tmp_path, body)) == []


class TestScoping:
    def test_skipped_directories_are_skipped(self, tmp_path):
        pkg = tmp_path / "tests"
        pkg.mkdir()
        (pkg / "t.py").write_text("from datetime import datetime\nx = datetime.utcnow()\n", encoding="utf-8")
        assert find_naive_utcnow(tmp_path, skip_dir_names={"tests"}) == []
        assert find_naive_utcnow(tmp_path) != []

    def test_an_unparseable_file_does_not_break_the_walk(self, tmp_path):
        (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
        (tmp_path / "ok.py").write_text("from datetime import datetime\nx = datetime.utcnow()\n", encoding="utf-8")
        found = find_naive_utcnow(tmp_path)
        assert len(found) == 1 and found[0].startswith("ok.py")


def test_the_entry_point_names_the_replacement(tmp_path):
    (tmp_path / "m.py").write_text("from datetime import datetime\nx = datetime.utcnow()\n", encoding="utf-8")
    # BaseException: `pytest.fail` raises `Failed`, which derives from it so that an
    # `except Exception` cannot swallow a failed assertion.
    with pytest.raises(BaseException) as excinfo:
        assert_no_naive_utcnow(tmp_path)
    message = str(excinfo.value)
    assert "datetime.now(datetime.UTC)" in message
    # The `Z`-suffix trap is in the message because it is the mistake the fix invites: an AWARE
    # `isoformat()` already emits `+00:00`.
    assert "+00:00Z" in message


def test_a_clean_tree_passes(tmp_path):
    (tmp_path / "m.py").write_text("from datetime import UTC, datetime\nx = datetime.now(UTC)\n", encoding="utf-8")
    assert_no_naive_utcnow(tmp_path)
