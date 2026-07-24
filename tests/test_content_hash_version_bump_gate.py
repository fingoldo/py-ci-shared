"""Unit tests for the content-hash/version-bump baseline meta-test harness.

Exercises assert_version_bumped_with_content() and register_refresh_option()
against a real (tiny, hand-written) scratch source tree -- same convention
as test_loc_budget.py/test_code_audit_meta.py: no mocking, the whole point
is verifying the seed/compare/self-certify-on-bump/refresh cycle around
real file I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import orjson
import pytest

from py_ci_shared.content_hash_version_bump_gate import (
    REFRESH_FLAG,
    assert_version_bumped_with_content,
    content_hash,
    register_refresh_option,
)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class TestAssertVersionBumpedWithContent:
    def test_first_run_seeds_baseline_and_skips(self, tmp_path):
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

        assert baseline.exists()
        seeded = orjson.loads(baseline.read_bytes())
        assert seeded["version"] == "v1"
        assert seeded["content_hash"] == content_hash([src])

    def test_unchanged_content_and_version_passes(self, tmp_path):
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

        # No pytest.fail/skip on the second call -- returning normally is the pass.
        assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

    def test_content_changed_without_bump_fails(self, tmp_path):
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

        _write(src, "def build(): return 'v1-but-different-structure'\n")
        with pytest.raises(pytest.fail.Exception, match="was NOT bumped"):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

    def test_content_changed_with_bump_is_self_certifying(self, tmp_path):
        """A version bump alongside the content change is accepted automatically --
        no separate refresh flag needed for the normal 'I bumped it' workflow."""
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

        _write(src, "def build(): return 'v2-new-structure'\n")
        assert_version_bumped_with_content(files=[src], version="v2", baseline_path=baseline)  # must not raise

        reseeded = orjson.loads(baseline.read_bytes())
        assert reseeded["version"] == "v2"
        assert reseeded["content_hash"] == content_hash([src])

    def test_bump_with_no_content_change_still_repins(self, tmp_path):
        """A version bump with NO content change (e.g. a deliberate no-op bump)
        is still accepted -- version mismatch alone is the self-certifying signal."""
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

        assert_version_bumped_with_content(files=[src], version="v2", baseline_path=baseline)
        assert orjson.loads(baseline.read_bytes())["version"] == "v2"

    def test_multiple_tracked_files_combined_into_one_hash(self, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        _write(a, "x = 1\n")
        _write(b, "y = 2\n")
        baseline = tmp_path / "_version_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[a, b], version="v1", baseline_path=baseline)

        # Changing EITHER tracked file without a bump must fail.
        _write(b, "y = 999\n")
        with pytest.raises(pytest.fail.Exception, match="was NOT bumped"):
            assert_version_bumped_with_content(files=[a, b], version="v1", baseline_path=baseline)

    def test_crlf_normalization_does_not_manufacture_a_diff(self, tmp_path):
        src = tmp_path / "prompt_builder.py"
        src.write_bytes(b"def build():\r\n    return 'v1'\r\n")
        baseline = tmp_path / "_version_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

        # Re-save with LF-only line endings, same logical content -- must NOT fail.
        src.write_bytes(b"def build():\n    return 'v1'\n")
        assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

    def test_refresh_flag_reseeds_even_with_existing_baseline(self, tmp_path, monkeypatch):
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v2'\n")
        baseline = tmp_path / "_version_baseline.json"
        baseline.write_text(orjson.dumps({"version": "stale", "content_hash": "deadbeef"}).decode("utf-8"), encoding="utf-8")

        monkeypatch.setattr(sys, "argv", [*sys.argv, REFRESH_FLAG])

        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[src], version="v2", baseline_path=baseline)

        reseeded = orjson.loads(baseline.read_bytes())
        assert reseeded["version"] == "v2"
        assert reseeded["content_hash"] == content_hash([src])


class TestContentHash:
    def test_stable_across_calls(self, tmp_path):
        src = tmp_path / "a.py"
        _write(src, "x = 1\n")
        assert content_hash([src]) == content_hash([src])

    def test_order_sensitive(self, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        _write(a, "x = 1\n")
        _write(b, "y = 2\n")
        assert content_hash([a, b]) != content_hash([b, a])


class TestRegisterRefreshOption:
    def _make_parser(self):
        from _pytest.config.argparsing import Parser

        return Parser()

    def test_registers_without_raising_on_fresh_parser(self):
        parser = self._make_parser()
        register_refresh_option(parser)
        args = parser.parse([REFRESH_FLAG])
        assert args.refresh_content_hash_version_baseline is True

    def test_double_registration_is_a_noop_not_a_crash(self):
        parser = self._make_parser()
        register_refresh_option(parser)
        register_refresh_option(parser)  # must not raise (pytest.Parser raises ValueError on conflict)
