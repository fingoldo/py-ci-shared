"""Unit tests for the shared audit-wave filename check, on real scratch trees."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.audit_wave_filenames import assert_no_new_audit_wave_filenames, find_audit_wave_test_files, write_baseline


def _tests(tmp_path: Path, *names: str) -> Path:
    for name in names:
        p = tmp_path / "tests" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("def test_x():\n    pass\n", encoding="utf-8")
    return tmp_path / "tests"


class TestPatterns:
    @pytest.mark.parametrize(
        "name",
        [
            "test_audit_2026_09_05_q_final.py",
            "test_audit_round9_fixes.py",
            "test_round14_low_fixes.py",
            "test_wave97_reporting.py",
            "test_waves65_69_split.py",
            "test_w2_savepoint.py",
            "test_p2_coverage_batch3.py",
            "test_misc_fixes_a.py",
            "test_untested_b.py",
            "sub/test_wave3_deep.py",
        ],
    )
    def test_every_known_process_name_is_caught(self, tmp_path, name):
        tests = _tests(tmp_path, name)

        assert find_audit_wave_test_files(tests) == [name]

    @pytest.mark.parametrize("name", ["test_w2v_embeddings.py", "test_waveform.py", "test_roundtrip.py", "test_audit_log_writer.py"])
    def test_a_topic_that_merely_starts_alike_is_not(self, tmp_path, name):
        """`w2v`, `waveform`, `roundtrip` and `audit_log` are topics; a pattern that caught them would be ignored."""
        assert find_audit_wave_test_files(_tests(tmp_path, name)) == []

    def test_a_repo_specific_pattern_is_additive(self, tmp_path):
        tests = _tests(tmp_path, "test_jolly_wishing_deer_x.py")

        assert find_audit_wave_test_files(tests, extra_patterns=(r"^test_jolly_wishing_deer_",)) == ["test_jolly_wishing_deer_x.py"]


class TestBaseline:
    def test_a_grandfathered_name_passes_and_a_new_one_fails(self, tmp_path):
        tests = _tests(tmp_path, "test_wave1_old.py")
        baseline = tmp_path / "baseline.json"
        write_baseline(baseline, ["test_wave1_old.py"])
        assert_no_new_audit_wave_filenames(tests, baseline=baseline)

        _tests(tmp_path, "test_wave2_new.py")
        with pytest.raises(pytest.fail.Exception, match=re.escape("test_wave2_new.py")):
            assert_no_new_audit_wave_filenames(tests, baseline=baseline)

    def test_a_stale_baseline_entry_fails_unless_migrating(self, tmp_path):
        """A renamed file must leave the baseline, or the list only ever grows."""
        tests = _tests(tmp_path, "test_topic.py")
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(["test_wave1_renamed_since.py"]), encoding="utf-8")

        with pytest.raises(pytest.fail.Exception, match="no longer name an offender"):
            assert_no_new_audit_wave_filenames(tests, baseline=baseline)
        assert_no_new_audit_wave_filenames(tests, baseline=baseline, fail_on_stale=False)

    def test_no_baseline_means_no_offender_is_allowed(self, tmp_path):
        with pytest.raises(pytest.fail.Exception):
            assert_no_new_audit_wave_filenames(_tests(tmp_path, "test_round1_x.py"))
