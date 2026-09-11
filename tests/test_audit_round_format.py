"""Unit tests for the shared audit-round format checks, on real scratch audit trees."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.audit_round_format import (
    absence_comparisons,
    assert_no_whole_file_absence_claims,
    assert_rounds_countable,
    assert_rounds_filed,
    assert_tracker_statuses_countable,
    closing_word,
    finding_problems,
    is_whole_file_read,
    round_filing_problems,
    status_problems,
    tracker_status_cells,
)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


class TestTrackerStatuses:
    def test_a_plain_and_a_qualified_status_both_count_only_in_the_bold_form(self, tmp_path):
        tracker = _write(
            tmp_path / "TRACKER.md",
            """
            | Status | Sev | Finding |
            | **RESOLVED** | P1 | `A-1` x |
            | **RESOLVED** (5/6) | P2 | `A-2` y |
            | RESOLVED | P2 | `A-3` plain spelling |
            | **RESOLVED (mechanism) / DEFERRED (policy)** | P3 | `A-4` z |
            """,
        )

        problems, parsed = status_problems(tracker)

        assert parsed.count("RESOLVED") == 2
        assert len(problems) == 2, problems  # the plain spelling, and the fused word nothing counts

    def test_a_fifth_word_fails_and_a_parse_of_nothing_fails(self, tmp_path):
        tracker = _write(tmp_path / "TRACKER.md", "| **DONE** (RESOLVED) | P1 | `A-1` x |\n")
        with pytest.raises(pytest.fail.Exception, match="outside"):
            assert_tracker_statuses_countable(tracker)
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_tracker_statuses_countable(_write(tmp_path / "EMPTY.md", "| a | b |\n"))


class TestRoundFiles:
    def test_an_addendum_at_the_finding_level_and_a_missing_disposition_are_reported(self, tmp_path):
        rnd = tmp_path / "audits" / "2026-09-05"
        _write(
            rnd / "01_sql.md",
            """
            ### SQL-1 (P2) -- ok
            **Disposition:** RESOLVED.

            ### SQL-2 (P3) -- no disposition here

            ### SQL-1 addendum, 2026-09-07: more
            text
            """,
        )

        problems = finding_problems(rnd / "01_sql.md")

        assert any("SQL-2 has no disposition" in p for p in problems)
        assert any("addendum" in p for p in problems)

    def test_a_quoted_heading_inside_a_fence_is_not_a_finding(self, tmp_path):
        rnd = tmp_path / "audits" / "2026-09-05"
        _write(rnd / "01.md", "### A-1 (P1) -- quotes a heading\n```\n### not a finding\n```\n**Disposition:** RESOLVED.\n")

        assert finding_problems(rnd / "01.md") == []

    def test_a_round_that_keeps_dispositions_elsewhere_is_skipped(self, tmp_path):
        rnd = tmp_path / "audits" / "2026-07-21"
        _write(rnd / "01.md", "### Dead code\nthematic section\n")

        assert finding_problems(rnd / "01.md") is None

    def test_the_whole_round_check_finds_duplicates_and_tracker_drift(self, tmp_path):
        audits = tmp_path / "audits"
        _write(audits / "2026-09-05" / "01.md", "### CORR-1 (P1) -- a\n**Disposition:** RESOLVED.\n")
        _write(
            audits / "implemented" / "2026-09-07" / "02.md",
            "### CORR-1 (P2) -- b\n**Disposition:** DEFERRED.\n### CORR-2 (P2) -- c\n**Disposition:** RESOLVED.\n",
        )
        tracker = _write(audits / "TRACKER.md", "| **RESOLVED** | P1 | `CORR-1` a |\n| **RESOLVED** | P2 | `CORR-9` orphan |\n")

        with pytest.raises(pytest.fail.Exception) as info:
            assert_rounds_countable(audits, tracker=tracker)

        message = str(info.value)
        assert "CORR-1 is defined by more than one heading" in message
        assert "CORR-2 has a finding section but no tracker row" in message
        assert "tracker row CORR-9 names no finding section" in message

    def test_a_clean_tree_passes_and_an_empty_one_does_not(self, tmp_path):
        audits = tmp_path / "audits"
        _write(audits / "2026-09-05" / "01.md", "### A-1 (P1) -- a\n**Disposition:** RESOLVED.\n")
        tracker = _write(audits / "TRACKER.md", "| **RESOLVED** | P1 | `A-1` a |\n")
        assert_rounds_countable(audits, tracker=tracker)

        with pytest.raises(pytest.fail.Exception):
            assert_rounds_countable(tmp_path / "nowhere")


class TestRoundFiling:
    _CLOSED = "| # | Lane | Severity | Disposition | Title |\n|---|---|---|---|---|\n| 1 | a | P1 | RESOLVED - `x.py` | t |\n| 2 | a | P2 | **FUTURE** (owner) | u |\n"

    def test_the_status_column_is_found_by_name_wherever_it_sits(self, tmp_path):
        tracker = _write(tmp_path / "TRACKER.md", "intro\n\n| id | severity | finding | status |\n|---|---|---|---|\n| A-1 | P1 | x | rejected - no |\n")

        assert tracker_status_cells(tracker) == [("A-1", "rejected - no")]
        assert closing_word("rejected - no") == "REJECTED"
        assert closing_word("**Won’t fix**") == "WON'T FIX"
        assert closing_word("OPEN - waiting") is None

    def test_an_escaped_pipe_does_not_shift_the_column_and_a_totals_row_is_not_a_row(self, tmp_path):
        tracker = _write(
            tmp_path / "TRACKER.md",
            "| # | Lane | Finding | Disposition |\n|---|---|---|---|\n"
            "| 18 | sec | `/x` runs `2 + \\|delta\\|` ablations | RESOLVED - capped |\n"
            "| **Total** | | | |\n",
        )

        assert tracker_status_cells(tracker) == [("18", "RESOLVED - capped")]

    def test_a_closed_round_in_the_open_tree_and_an_open_row_in_implemented_are_reported(self, tmp_path):
        audits = tmp_path / "audits"
        _write(audits / "2026-09-11" / "TRACKER.md", self._CLOSED)
        _write(audits / "implemented" / "2026-09-06" / "TRACKER.md", self._CLOSED.replace("**FUTURE** (owner)", "OPEN"))

        problems = round_filing_problems(audits)

        assert "2026-09-11: every row of TRACKER.md is closed - move the round to implemented/" in problems
        assert "implemented/2026-09-06/TRACKER.md: row '2' is not closed, yet the round is filed under implemented/" in problems
        assert len(problems) == 2, problems

    def test_an_uncountable_tracker_and_an_open_round_without_one_are_reported(self, tmp_path):
        audits = tmp_path / "audits"
        _write(audits / "2026-09-01" / "TRACKER.md", "| Report | Findings | Done |\n|---|---|---|\n| a.md | 3 | 3 |\n")
        _write(audits / "2026-09-02" / "report.md", "# prose only\n")
        _write(audits / "implemented" / "2026-07-01" / "report.md", "# filed before trackers existed\n")

        problems = round_filing_problems(audits)

        assert any(p.startswith("2026-09-01/TRACKER.md: no table") for p in problems), problems
        assert "2026-09-02: open round with no TRACKER*.md - its closure cannot be counted" in problems
        assert not any("2026-07-01" in p for p in problems), problems

    def test_the_ratchet_fails_on_a_new_and_on_a_stale_entry_and_passes_when_they_agree(self, tmp_path):
        audits = tmp_path / "audits"
        _write(audits / "2026-09-11" / "TRACKER.md", self._CLOSED)
        known = round_filing_problems(audits)

        assert_rounds_filed(audits, known=known)
        with pytest.raises(pytest.fail.Exception, match="move the round"):
            assert_rounds_filed(audits, known=())
        with pytest.raises(pytest.fail.Exception, match="no longer reproduce"):
            assert_rounds_filed(audits, known=[*known, "2026-01-01: gone"])
        with pytest.raises(pytest.fail.Exception, match="expected at least"):
            assert_rounds_filed(tmp_path / "nowhere")


class TestAbsenceClaims:
    def test_a_whole_file_absence_is_found_and_a_slice_is_allowed(self):
        assert is_whole_file_read('src("x.py")') and is_whole_file_read('sibling_src("x.py")')
        assert not is_whole_file_read('src("x.py").split("M")[1][:200]')
        found = absence_comparisons('ok = "needle" not in src("f.py")\nfine = "needle" in src("f.py")\n')
        assert [(left, right) for _, left, right in found] == [("'needle'", "src('f.py')")]

    def test_the_assertion_fails_on_one(self, tmp_path):
        verifier = _write(tmp_path / "verify.py", 'CHECKS = [("X-1", "gone", lambda: "proxy_url(0)" not in src("a.py"))]\n')
        with pytest.raises(pytest.fail.Exception, match="WHOLE file"):
            assert_no_whole_file_absence_claims(verifier)
