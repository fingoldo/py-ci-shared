"""`fail_message_quality`: which messages count as actionable, which calls are audited, and what a broken file does."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.fail_message_quality import ACTIONABLE_RE, assert_fail_messages_actionable, fail_message_problems


def _meta(tmp_path: Path, body: str, name: str = "test_meta.py") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


class TestThePattern:
    @pytest.mark.parametrize(
        "text",
        ["value was wrong or missing", "the set of keys differs", "you would see it in the log", "no use for this entry", "3 entries OR fewer"],
    )
    def test_nouns_and_conjunctions_are_not_instructions(self, text):
        assert not ACTIONABLE_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Set PY_CI_SHARED_REFRESH",
            "set the flag and rerun",
            "mismatch; run pytest --refresh",
            "baseline drifted -- update it",
            "expected <name>",
            "See docs/x.md",
        ],
    )
    def test_instructions_are(self, text):
        assert ACTIONABLE_RE.search(text)


class TestTheScan:
    def test_an_imported_fail_is_audited(self, tmp_path):
        f = _meta(tmp_path, "from pytest import fail\n\ndef test_a():\n    fail('x')\n")
        audited, bad = fail_message_problems([f])
        assert audited == 1 and bad == ["test_meta.py:4 -> 'x'"]

    def test_an_aliased_module_is_audited(self, tmp_path):
        f = _meta(tmp_path, "import pytest as pt\n\ndef test_a():\n    pt.fail('x')\n    pt.fail('Fix x')\n")
        audited, bad = fail_message_problems([f])
        assert audited == 2 and len(bad) == 1

    def test_a_reason_keyword_is_read(self, tmp_path):
        f = _meta(tmp_path, "import pytest\n\ndef test_a():\n    pytest.fail(reason='thing broke')\n    pytest.fail(reason='Refresh it')\n")
        audited, bad = fail_message_problems([f])
        assert audited == 2 and bad == ["test_meta.py:4 -> 'thing broke'"]

    def test_an_unrelated_fail_is_not_audited(self, tmp_path):
        f = _meta(tmp_path, "def fail(x):\n    pass\n\ndef test_a():\n    fail('x')\n    other.fail('y')\n")
        assert fail_message_problems([f]) == (0, [])

    def test_a_parse_failure_is_reported_with_its_relative_path(self, tmp_path):
        sub = tmp_path / "meta"
        sub.mkdir()
        good = _meta(sub, "import pytest\n\ndef test_a():\n    pytest.fail('Fix it')\n")
        broken = _meta(sub, "def test_(:\n", name="test_broken.py")
        audited, bad = fail_message_problems([good, broken], root=tmp_path)
        assert audited == 1 and len(bad) == 1 and bad[0].startswith("meta/test_broken.py:1 (unparsable")

    def test_a_bom_file_is_read(self, tmp_path):
        f = tmp_path / "test_bom.py"
        f.write_bytes(b"\xef\xbb\xbfimport pytest\n\ndef test_a():\n    pytest.fail('x')\n")
        assert fail_message_problems([f]) == (1, ["test_bom.py:4 -> 'x'"])


class TestTheAssert:
    def test_an_actionable_directory_passes_and_a_bad_message_fails(self, tmp_path):
        _meta(tmp_path, "import pytest\n\ndef test_a():\n    pytest.fail('Refresh the baseline')\n")
        assert_fail_messages_actionable(tmp_path)
        _meta(tmp_path, "import pytest\n\ndef test_b():\n    pytest.fail('value was wrong or missing')\n", name="test_b.py")
        with pytest.raises(pytest.fail.Exception, match=r"test_b.py:4"):
            assert_fail_messages_actionable(tmp_path)

    def test_an_empty_directory_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="only 0"):
            assert_fail_messages_actionable(tmp_path)
