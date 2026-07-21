"""Unit tests for the LOC-budget baseline meta-test harness.

Exercises assert_no_new_oversized_file() and register_refresh_option()
against a real (tiny, hand-written) scratch source tree -- same convention
as test_code_audit_meta.py: no mocking, the whole point is verifying the
seed/compare/refresh/report cycle around real file I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import orjson
import pytest

from py_ci_shared.loc_budget import (
    REFRESH_FLAG,
    assert_no_new_oversized_file,
    register_refresh_option,
)


def _write_lines(path: Path, n: int) -> None:
    path.write_text("\n".join(f"x{i} = {i}" for i in range(n)) + "\n", encoding="utf-8")


class TestAssertNoNewOversizedFile:
    def test_first_run_seeds_baseline_and_skips(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        big = src / "big.py"
        _write_lines(big, 20)
        baseline = tmp_path / "_loc_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10)

        assert baseline.exists()
        seeded = orjson.loads(baseline.read_bytes())
        assert seeded == {"big.py": 20}

    def test_file_under_limit_never_flagged(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        small = src / "small.py"
        _write_lines(small, 5)
        baseline = tmp_path / "_loc_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_oversized_file(files=[small], root=src, baseline_path=baseline, limit=10)
        assert orjson.loads(baseline.read_bytes()) == {}

    def test_unchanged_tree_passes_after_seeding(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        big = src / "big.py"
        _write_lines(big, 20)
        baseline = tmp_path / "_loc_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10)

        # No pytest.fail/skip on the second call -- returning normally is the pass.
        assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10)

    def test_new_oversized_file_fails(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        baseline = tmp_path / "_loc_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_oversized_file(files=[], root=src, baseline_path=baseline, limit=10)

        big = src / "big.py"
        _write_lines(big, 20)
        with pytest.raises(pytest.fail.Exception, match="NEW oversized"):
            assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10)

    def test_grandfathered_file_within_slack_passes(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        big = src / "big.py"
        _write_lines(big, 20)
        baseline = tmp_path / "_loc_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10, growth_slack=5)

        _write_lines(big, 24)  # +4, within the 5-line slack
        assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10, growth_slack=5)

    def test_grandfathered_file_beyond_slack_fails(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        big = src / "big.py"
        _write_lines(big, 20)
        baseline = tmp_path / "_loc_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10, growth_slack=5)

        _write_lines(big, 30)  # +10, beyond the 5-line slack
        with pytest.raises(pytest.fail.Exception, match="GREW"):
            assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10, growth_slack=5)

    def test_shrunk_file_does_not_fail(self, tmp_path):
        """A grandfathered file that got smaller (partially fixed) must
        not fail -- only growth beyond the slack is gated."""
        src = tmp_path / "src"
        src.mkdir()
        big = src / "big.py"
        _write_lines(big, 30)
        baseline = tmp_path / "_loc_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10)

        _write_lines(big, 15)  # still over the limit, but shrunk from baseline
        assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10)

    def test_refresh_flag_reseeds_even_with_existing_baseline(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        big = src / "big.py"
        _write_lines(big, 20)
        baseline = tmp_path / "_loc_baseline.json"
        baseline.write_text("{}", encoding="utf-8")

        monkeypatch.setattr(sys, "argv", [*sys.argv, REFRESH_FLAG])

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_oversized_file(files=[big], root=src, baseline_path=baseline, limit=10)

        assert orjson.loads(baseline.read_bytes()) == {"big.py": 20}


class TestRegisterRefreshOption:
    def _make_parser(self):
        from _pytest.config.argparsing import Parser

        return Parser()

    def test_registers_without_raising_on_fresh_parser(self):
        parser = self._make_parser()
        register_refresh_option(parser)
        args = parser.parse([REFRESH_FLAG])
        assert args.refresh_loc_budget_baseline is True

    def test_double_registration_is_a_noop_not_a_crash(self):
        parser = self._make_parser()
        register_refresh_option(parser)
        register_refresh_option(parser)  # must not raise (pytest.Parser raises ValueError on conflict)
