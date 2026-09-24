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


class _RefreshConfig:
    def getoption(self, name, default=None):
        return name == REFRESH_FLAG


class _RefreshRequest:
    config = _RefreshConfig()


def _seed(files, version: str, baseline: Path) -> None:
    """The baseline is written only by an explicit refresh now."""
    with pytest.raises(pytest.skip.Exception):
        assert_version_bumped_with_content(files=files, version=version, baseline_path=baseline, request=_RefreshRequest())


@pytest.fixture(autouse=True)
def _not_in_ci(monkeypatch):
    """Bumps re-pin only outside CI; these tests model a developer machine unless they set CI themselves."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("PY_CI_SHARED_REFRESH", raising=False)


class TestAssertVersionBumpedWithContent:
    def test_a_missing_baseline_fails_and_a_refresh_seeds_it(self, tmp_path, monkeypatch):
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"
        monkeypatch.setattr(sys, "argv", ["pytest"])

        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)
        assert not baseline.exists()

        _seed([src], "v1", baseline)
        seeded = orjson.loads(baseline.read_bytes())
        assert seeded["version"] == "v1"
        assert seeded["content_hash"] == content_hash([src])

    def test_unchanged_content_and_version_passes(self, tmp_path):
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"

        _seed([src], "v1", baseline)

        # No pytest.fail/skip on the second call -- returning normally is the pass.
        assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

    def test_content_changed_without_bump_fails(self, tmp_path):
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"

        _seed([src], "v1", baseline)

        _write(src, "def build(): return 'v1-but-different-structure'\n")
        with pytest.raises(pytest.fail.Exception, match="was NOT bumped"):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)

    def test_content_changed_with_bump_is_self_certifying(self, tmp_path):
        """A version bump alongside the content change is accepted automatically --
        no separate refresh flag needed for the normal 'I bumped it' workflow."""
        src = tmp_path / "prompt_builder.py"
        _write(src, "def build(): return 'v1'\n")
        baseline = tmp_path / "_version_baseline.json"

        _seed([src], "v1", baseline)

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

        _seed([src], "v1", baseline)

        assert_version_bumped_with_content(files=[src], version="v2", baseline_path=baseline)
        assert orjson.loads(baseline.read_bytes())["version"] == "v2"

    def test_multiple_tracked_files_combined_into_one_hash(self, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        _write(a, "x = 1\n")
        _write(b, "y = 2\n")
        baseline = tmp_path / "_version_baseline.json"

        _seed([a, b], "v1", baseline)

        # Changing EITHER tracked file without a bump must fail.
        _write(b, "y = 999\n")
        with pytest.raises(pytest.fail.Exception, match="was NOT bumped"):
            assert_version_bumped_with_content(files=[a, b], version="v1", baseline_path=baseline)

    def test_crlf_normalization_does_not_manufacture_a_diff(self, tmp_path):
        src = tmp_path / "prompt_builder.py"
        src.write_bytes(b"def build():\r\n    return 'v1'\r\n")
        baseline = tmp_path / "_version_baseline.json"

        _seed([src], "v1", baseline)

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


class TestHashLayout:
    def test_moving_bytes_between_files_changes_the_hash(self, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        _write(a, "ab")
        _write(b, "c")
        first = content_hash([a, b])
        _write(a, "a")
        _write(b, "bc")
        assert content_hash([a, b]) != first
        _write(a, "ab")
        _write(b, "c")
        assert content_hash([a, b]) == first

    def test_the_hash_does_not_depend_on_where_the_checkout_lives(self, tmp_path):
        for base in ("one", "two/deeper"):
            d = tmp_path / base / "pkg"
            d.mkdir(parents=True)
            _write(d / "a.py", "x = 1\n")
            _write(d / "b.py", "y = 2\n")
        h1 = content_hash([tmp_path / "one" / "pkg" / "a.py", tmp_path / "one" / "pkg" / "b.py"])
        h2 = content_hash([tmp_path / "two" / "deeper" / "pkg" / "a.py", tmp_path / "two" / "deeper" / "pkg" / "b.py"])
        assert h1 == h2

    def test_an_empty_file_list_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="at least one file"):
            content_hash([])
        with pytest.raises(pytest.fail.Exception, match="no files"):
            assert_version_bumped_with_content(files=[], version="v1", baseline_path=tmp_path / "b.json")

    def test_a_legacy_baseline_still_gates(self, tmp_path):
        import hashlib

        src = tmp_path / "p.py"
        _write(src, "x = 1\n")
        legacy = hashlib.sha256(b"x = 1\n").hexdigest()[:16]
        baseline = tmp_path / "b.json"
        baseline.write_text(orjson.dumps({"version": "v1", "content_hash": legacy}).decode("utf-8"), encoding="utf-8")
        assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)
        _write(src, "x = 2\n")
        with pytest.raises(pytest.fail.Exception, match="was NOT bumped"):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)


class TestBumpDiscipline:
    def test_refresh_is_detected_from_the_env_under_xdist(self, tmp_path, monkeypatch):
        src = tmp_path / "p.py"
        _write(src, "x = 1\n")
        baseline = tmp_path / "b.json"
        monkeypatch.setattr(sys, "argv", ["-c"])
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", "content-hash-version")
        with pytest.raises(pytest.skip.Exception):
            assert_version_bumped_with_content(files=[src], version="v1", baseline_path=baseline)
        assert orjson.loads(baseline.read_bytes())["version"] == "v1"

    def test_a_bump_in_ci_is_not_written_and_fails_until_committed(self, tmp_path, monkeypatch):
        src = tmp_path / "p.py"
        _write(src, "x = 1\n")
        baseline = tmp_path / "b.json"
        _seed([src], "v1", baseline)
        before = baseline.read_bytes()
        monkeypatch.setenv("CI", "true")
        with pytest.raises(pytest.fail.Exception, match="never re-pinned"):
            assert_version_bumped_with_content(files=[src], version="v2", baseline_path=baseline)
        assert baseline.read_bytes() == before
        monkeypatch.setenv("CI", "false")
        assert_version_bumped_with_content(files=[src], version="v2", baseline_path=baseline)
        assert orjson.loads(baseline.read_bytes())["version"] == "v2"

    def test_reverting_to_an_old_version_with_new_content_fails(self, tmp_path):
        src = tmp_path / "p.py"
        _write(src, "x = 1\n")
        baseline = tmp_path / "b.json"
        _seed([src], "A", baseline)
        _write(src, "x = 2\n")
        assert_version_bumped_with_content(files=[src], version="B", baseline_path=baseline)
        with pytest.raises(pytest.fail.Exception, match="already pinned to different content"):
            assert_version_bumped_with_content(files=[src], version="A", baseline_path=baseline)
        _write(src, "x = 1\n")
        assert_version_bumped_with_content(files=[src], version="A", baseline_path=baseline)


def test_register_refresh_option_registers_the_generic_flag_too():
    from _pytest.config.argparsing import Parser

    parser = Parser()
    register_refresh_option(parser)
    args = parser.parse(["--py-ci-refresh", "content-hash-version"])
    assert args.py_ci_refresh == ["content-hash-version"]
