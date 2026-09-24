"""Unit tests for the naive-utcnow check. Real files on disk, same no-mocking convention."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared._core import CorpusError
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

    def test_an_unparseable_file_is_reported_and_does_not_stop_the_walk(self, tmp_path):
        """Re-framed 2026-09-24 (MP-2): the old test REQUIRED the broken file to vanish silently, which is how a
        file with syntax newer than the interpreter passed every AST gate. It is now a reported entry, and the
        parsable file next to it is still checked."""
        (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
        (tmp_path / "ok.py").write_text("from datetime import datetime\nx = datetime.utcnow()\n", encoding="utf-8")
        found = find_naive_utcnow(tmp_path)
        assert len(found) == 2
        assert found[0].startswith("broken.py:1: unparsable:")
        assert found[1].startswith("ok.py:2:")

    def test_an_unparseable_file_fails_the_entry_point(self, tmp_path):
        (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
        (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"broken\.py:1: unparsable"):
            assert_no_naive_utcnow(tmp_path)
        (tmp_path / "broken.py").write_text("def f():\n    pass\n", encoding="utf-8")
        assert_no_naive_utcnow(tmp_path)  # control: the same tree, repaired, passes

    def test_a_default_build_dir_inside_the_root_is_skipped(self, tmp_path):
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "copy.py").write_text("from datetime import datetime\nx = datetime.utcnow()\n", encoding="utf-8")
        (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
        assert find_naive_utcnow(tmp_path) == []
        (tmp_path / "m.py").write_text("from datetime import datetime\nx = datetime.utcnow()\n", encoding="utf-8")
        assert find_naive_utcnow(tmp_path) == ["m.py:2: datetime.utcnow()"]

    def test_a_root_living_under_a_dir_named_build_is_still_scanned(self, tmp_path):
        """Exclusion matches parts RELATIVE to the root: a checkout under ``build/`` must not be skipped whole."""
        root = tmp_path / "build" / "checkout"
        root.mkdir(parents=True)
        (root / "m.py").write_text("from datetime import datetime\nx = datetime.utcnow()\n", encoding="utf-8")
        assert find_naive_utcnow(root) == ["m.py:2: datetime.utcnow()"]
        (root / "m.py").write_text("x = 1\n", encoding="utf-8")
        assert find_naive_utcnow(root) == []


class TestAuditRegressions:
    def test_a_bom_file_is_checked_not_dropped(self, tmp_path):
        """MP-1: `ast.parse` rejects U+FEFF, and the old `except SyntaxError: continue` dropped the file."""
        (tmp_path / "m.py").write_bytes(b"\xef\xbb\xbffrom datetime import datetime\r\nx = datetime.utcnow()\r\n")
        assert find_naive_utcnow(tmp_path) == ["m.py:2: datetime.utcnow()"]
        (tmp_path / "m.py").write_bytes(b"\xef\xbb\xbfx = 1\r\n")
        assert find_naive_utcnow(tmp_path) == []

    def test_non_utf8_bytes_are_reported_as_unreadable(self, tmp_path):
        (tmp_path / "m.py").write_bytes(b"x = '\xff\xfe'\n")
        found = find_naive_utcnow(tmp_path)
        assert len(found) == 1 and found[0].startswith("m.py:1: unreadable:")

    @pytest.mark.parametrize(
        "body",
        [
            "from datetime import datetime\nfrom pydantic import Field\nf = Field(default_factory=datetime.utcnow)\n",
            "import datetime as dt\nts = dt.datetime.utcfromtimestamp(0)\n",
            "from datetime import datetime as D\nnow = D.utcnow\n",
        ],
    )
    def test_uncalled_references_and_utcfromtimestamp_are_caught(self, body, tmp_path):
        """MP-3: only `.utcnow()` CALLS were matched; the default_factory reference and utcfromtimestamp slipped."""
        found = find_naive_utcnow(_module(tmp_path, body))
        assert len(found) == 1, found

    def test_a_call_is_reported_once_not_twice(self, tmp_path):
        found = find_naive_utcnow(_module(tmp_path, "from datetime import datetime\nx = datetime.utcnow()\n"))
        assert found == ["m.py:2: datetime.utcnow()"]

    @pytest.mark.parametrize("body", ["import arrow\nx = arrow.utcnow()\n", "import pendulum as p\nx = p.utcnow()\n"])
    def test_aware_libraries_are_resolved_through_aliases_and_not_flagged(self, body, tmp_path):
        assert find_naive_utcnow(_module(tmp_path, body)) == []
        # control: the same alias spelling bound to datetime IS flagged
        assert find_naive_utcnow(_module(tmp_path, body.replace("arrow", "datetime.datetime").replace("pendulum", "datetime.datetime"))) != []

    def test_an_empty_root_fails_the_floor(self, tmp_path):
        """MP-4 / SUITE-20: a wrong root used to pass green."""
        with pytest.raises(pytest.fail.Exception, match="only 0 file"):
            assert_no_naive_utcnow(tmp_path)
        (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
        assert_no_naive_utcnow(tmp_path)
        with pytest.raises(pytest.fail.Exception, match="expected at least 2"):
            assert_no_naive_utcnow(tmp_path, min_files=2)

    def test_a_missing_root_raises(self, tmp_path):
        with pytest.raises(CorpusError):
            find_naive_utcnow(tmp_path / "nope")
        assert find_naive_utcnow(tmp_path) == []  # control: an existing (empty) root does not raise

    def test_line_numbers_are_read_plainly(self):
        """SUITE-21: the line attribute was spelled through chr() concatenation, which hid it from grep."""
        import py_ci_shared.naive_utcnow as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "chr(108)" not in source
        assert "node.lineno" in source


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
