"""Unit tests for the prose numeric-claim parity check."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.prose_numeric_claims import NumericClaim, find_stale_claims, find_undated_volatile_claims


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestFindStaleClaims:
    def test_a_stale_count_is_reported_with_both_numbers(self, tmp_path):
        """The archetype: 'the 24-entry alias map' against a map that holds 27."""
        doc = _write(tmp_path / "TESTING.md", "The 24-entry backward-compat module alias map imports cleanly.\n")
        problems = find_stale_claims([NumericClaim(doc, r"The (\d+)-entry backward-compat", lambda: 27, "alias count")])
        assert len(problems) == 1
        assert "states 24" in problems[0] and "repo has 27" in problems[0]

    def test_a_correct_count_passes(self, tmp_path):
        doc = _write(tmp_path / "TESTING.md", "The 27-entry backward-compat module alias map imports cleanly.\n")
        assert find_stale_claims([NumericClaim(doc, r"The (\d+)-entry backward-compat", lambda: 27, "alias count")]) == []

    def test_a_thousands_separator_is_understood(self, tmp_path):
        doc = _write(tmp_path / "README.md", "1,900+ tests\n")
        assert find_stale_claims([NumericClaim(doc, r"([\d,]+)\+? tests", lambda: 1900, "test count")]) == []

    def test_an_anchor_that_no_longer_matches_is_itself_a_finding(self, tmp_path):
        """A claim whose prose was reworded has stopped being checked; passing silently
        would make the whole registry decay into decoration."""
        doc = _write(tmp_path / "TESTING.md", "The alias map imports cleanly.\n")
        problems = find_stale_claims([NumericClaim(doc, r"The (\d+)-entry backward-compat", lambda: 27, "alias count")])
        assert len(problems) == 1 and "no longer matches its anchor" in problems[0]

    def test_every_repeated_occurrence_is_checked(self, tmp_path):
        doc = _write(tmp_path / "README.md", "across 7 providers\nlater: across 5 providers\n")
        problems = find_stale_claims([NumericClaim(doc, r"across (\d+) providers", lambda: 7, "provider count")])
        assert len(problems) == 1 and "states 5" in problems[0]

    def test_tolerance_allows_a_deliberately_rounded_number(self, tmp_path):
        doc = _write(tmp_path / "README.md", "about 200 modules\n")
        assert find_stale_claims([NumericClaim(doc, r"about (\d+) modules", lambda: 216, "module count", tolerance=20)]) == []


class TestFindUndatedVolatileClaims:
    def test_an_undated_runtime_is_reported(self, tmp_path):
        doc = _write(tmp_path / "TESTING.md", "The meta-test suite runs in ~30 s.\n")
        assert len(find_undated_volatile_claims([doc])) == 1

    def test_a_nearby_iso_date_qualifies_the_claim(self, tmp_path):
        doc = _write(tmp_path / "TESTING.md", "Measured on 2026-09-02:\n\nThe meta-test suite runs in ~30 s.\n")
        assert find_undated_volatile_claims([doc]) == []

    def test_a_reviewed_line_is_suppressed(self, tmp_path):
        doc = _write(tmp_path / "CONTRIBUTING.md", "Aim for >80% coverage for new code.\n")
        assert find_undated_volatile_claims([doc], covered_patterns=[r"Aim for >80% coverage"]) == []
