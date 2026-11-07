"""Unit tests for tracker_summary_parity: summaries and headings agree with the rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.tracker_summary_parity import (
    assert_tracker_summaries_agree,
    disposition_state_problems,
    heading_status_problems,
    summary_problems,
    summary_table_count,
)

_TRACKER = """# Tracker

## Round 2026-01-01

| File | Findings | RESOLVED | WON'T FIX | DEFERRED |
| --- | ---: | ---: | ---: | ---: |
| `implemented/2026-01-01/01.md` | {n1} | {r1} | 0 | {d1} |
| `implemented/2026-01-01/02.md` | 1 | 0 | 1 | 0 |
| **total** | **{tn}** | **{tr}** | **1** | **{td}** |

### `implemented/2026-01-01/01.md`

| Status | Sev | Finding |
| --- | --- | --- |
| **RESOLVED** | P2 | `AA-1` one |
| **RESOLVED** (5/6) | P3 | `AA-2` two |
| **DEFERRED** | P3 | `AA-3` three |

### `implemented/2026-01-01/02.md`

| Status | Sev | Finding |
| --- | --- | --- |
| **WON'T FIX** | P2 | `AA-4` four |
"""


def _tracker(tmp_path: Path, **kw) -> Path:
    values = {"n1": 3, "r1": 2, "d1": 1, "tn": 4, "tr": 2, "td": 1, **kw}
    p = tmp_path / "TRACKER.md"
    p.write_text(_TRACKER.format(**values), encoding="utf-8")
    return p


def test_a_consistent_tracker_is_clean(tmp_path):
    assert summary_problems(_tracker(tmp_path)) == []


def test_a_stale_status_count_is_reported(tmp_path):
    """The production_scrapers case: a summary saying nothing was resolved above resolved rows."""
    problems = summary_problems(_tracker(tmp_path, r1=0, tr=0))
    assert "`implemented/2026-01-01/01.md` says 0 RESOLVED, its rows say 2" in problems[0]


def test_a_wrong_finding_count_and_a_wrong_total_are_reported(tmp_path):
    problems = summary_problems(_tracker(tmp_path, n1=4, tn=4))
    assert any("says 4 findings, its section has 3 rows" in p for p in problems)
    problems = summary_problems(_tracker(tmp_path, tn=5))
    assert any("total row reads 5 findings" in p for p in problems)


def test_a_summary_row_without_a_section_is_reported(tmp_path):
    p = _tracker(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("### `implemented/2026-01-01/02.md`", "### something else"), encoding="utf-8")
    assert any("has no ### `implemented/2026-01-01/02.md` section" in x for x in summary_problems(p))


def test_a_heading_status_must_match_the_row(tmp_path):
    tracker = _tracker(tmp_path)
    rnd = tmp_path / "implemented" / "2026-01-01"
    rnd.mkdir(parents=True)
    (rnd / "01.md").write_text("### AA-3 (P3) -- three -- RESOLVED\n\n**Disposition:** x\n\n### AA-1 (P2) -- one -- RESOLVED\n", encoding="utf-8")
    assert heading_status_problems(tmp_path, tracker) == ["2026-01-01/01.md: AA-3's heading ends RESOLVED, its tracker row says DEFERRED"]


def test_assert_refuses_a_tracker_with_no_summary(tmp_path):
    p = tmp_path / "TRACKER.md"
    p.write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="summary table"):
        assert_tracker_summaries_agree(tmp_path, p)


class TestAuditRegressions:
    def test_a_fenced_example_header_does_not_satisfy_the_floor_and_bold_headers_count(self, tmp_path):
        fenced = tmp_path / "fenced.md"
        fenced.write_text("# T\n\n```md\n| File | Findings | RESOLVED |\n| --- | --- | --- |\n```\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="fewer than 1 summary"):
            assert_tracker_summaries_agree(tmp_path, fenced)
        bold = _tracker(tmp_path).read_text(encoding="utf-8").replace("| File | Findings |", "| **File** | **Findings** |")
        (tmp_path / "bold.md").write_text(bold, encoding="utf-8")
        assert summary_table_count(tmp_path / "bold.md") == 1
        assert_tracker_summaries_agree(tmp_path, tmp_path / "bold.md")

    def test_a_row_without_backticks_is_checked_and_a_short_row_is_reported_not_a_crash(self, tmp_path):
        text = _tracker(tmp_path, n1=5).read_text(encoding="utf-8").replace("| `implemented/2026-01-01/01.md` |", "| implemented/2026-01-01/01.md |")
        (tmp_path / "plain.md").write_text(text, encoding="utf-8")
        assert any("says 5 findings" in p for p in summary_problems(tmp_path / "plain.md"))
        short = (
            _tracker(tmp_path).read_text(encoding="utf-8").replace("| `implemented/2026-01-01/02.md` | 1 | 0 | 1 | 0 |", "| `implemented/2026-01-01/02.md` |")
        )
        (tmp_path / "short.md").write_text(short, encoding="utf-8")
        problems = summary_problems(tmp_path / "short.md")
        assert any("has 1 cell(s)" in p for p in problems)
        unnamed = _tracker(tmp_path).read_text(encoding="utf-8").replace("| `implemented/2026-01-01/02.md` |", "| round two |")
        (tmp_path / "unnamed.md").write_text(unnamed, encoding="utf-8")
        assert any("names no round file" in p for p in summary_problems(tmp_path / "unnamed.md"))

    def test_the_status_column_is_found_by_header_and_any_header_row_is_skipped(self, tmp_path):
        text = _tracker(tmp_path).read_text(encoding="utf-8")
        text = text.replace(
            "| Status | Sev | Finding |\n| --- | --- | --- |\n| **RESOLVED** | P2 | `AA-1` one |\n| **RESOLVED** (5/6) | P3 | `AA-2` two |\n| **DEFERRED** | P3 | `AA-3` three |",
            "| ID | State | Finding |\n| --- | --- | --- |\n| `AA-1` | **RESOLVED** | one |\n| `AA-2` | **RESOLVED** (5/6) | two |\n| `AA-3` | **DEFERRED** | three |",
        )
        (tmp_path / "moved.md").write_text(text, encoding="utf-8")
        assert summary_problems(tmp_path / "moved.md") == []
        wrong = text.replace("| `AA-3` | **DEFERRED** | three |", "| `AA-3` | **RESOLVED** | three |")
        (tmp_path / "wrong.md").write_text(wrong, encoding="utf-8")
        assert any("says 1 DEFERRED, its rows say 0" in p for p in summary_problems(tmp_path / "wrong.md"))


class TestDispositionStates:
    """One finding, one state, wherever it is written (audit 41-F13)."""

    @staticmethod
    def _round(tmp_path: Path, tracker_cell: str, finding_line: str, *, table_cell: "str | None" = None) -> Path:
        audits = tmp_path / "audits"
        (audits / "2026-11-01").mkdir(parents=True)
        (audits / "TRACKER.md").write_text(f"| # | Sev | Finding | Disposition |\n|---|---|---|---|\n| 9-F1 | P2 | x | {tracker_cell} |\n", encoding="utf-8")
        body = f"## 9-F1 (P2) x\n\n- {finding_line}\n"
        if table_cell is not None:
            body += f"\n| # | Gate | Disposition |\n|---|---|---|\n| 9-F1 | g | {table_cell} |\n"
        (audits / "2026-11-01" / "09_x.md").write_text(body, encoding="utf-8")
        return audits

    def test_agreeing_states_pass_whatever_follows_the_state_word(self, tmp_path):
        audits = self._round(tmp_path, "RESOLVED", "Disposition: RESOLVED abc1234: did it", table_cell="**RESOLVED** abc1234")
        assert disposition_state_problems(audits) == []

    @pytest.mark.parametrize(
        ("tracker_cell", "finding_line", "table_cell", "fragment"),
        [
            (
                "OPEN",
                "Disposition: RESOLVED abc1234: did it",
                None,
                "9-F1 is written with different states: RESOLVED at 2026-11-01/09_x.md:3; OPEN at TRACKER.md:3",
            ),
            ("RESOLVED", "Disposition: DEFERRED: later", None, "different states"),
            ("RESOLVED", "Disposition: RESOLVED x", "WON'T FIX: no", "WON'T FIX at 2026-11-01/09_x.md:"),
            ("PARTIALLY RESOLVED (rest open)", "Disposition: RESOLVED x", None, "PARTIAL at TRACKER.md:3"),
            (
                "RESOLVED: the report is not reproduced here",
                "Disposition: RESOLVED x",
                None,
                "TRACKER.md:3: 9-F1 is RESOLVED but its cell says it was not reproduced",
            ),
        ],
    )
    def test_negative_controls_a_disagreement_is_reported(self, tmp_path, tracker_cell, finding_line, table_cell, fragment):
        audits = self._round(tmp_path, tracker_cell, finding_line, table_cell=table_cell)
        assert any(fragment in p for p in disposition_state_problems(audits)), disposition_state_problems(audits)

    def test_not_reproduced_is_fine_in_a_cell_that_is_not_resolved(self, tmp_path):
        audits = self._round(tmp_path, "WON'T FIX (cannot be reproduced)", "Disposition: WON'T FIX: not reproduced on abc")
        assert disposition_state_problems(audits) == []

    def test_a_fenced_example_is_not_a_claim(self, tmp_path):
        audits = self._round(tmp_path, "RESOLVED", "Disposition: RESOLVED x")
        with (audits / "2026-11-01" / "09_x.md").open("a", encoding="utf-8") as f:
            f.write("\n```\n| 9-F1 | g | OPEN |\n```\n")
        assert disposition_state_problems(audits) == []

    def test_this_repositorys_own_audits_agree(self):
        assert disposition_state_problems(Path(__file__).resolve().parents[1] / "audits") == []

    def test_assert_adds_the_state_check_only_on_request(self, tmp_path):
        audits = self._round(tmp_path, "OPEN", "Disposition: RESOLVED x")
        tracker = audits / "TRACKER.md"
        tracker.write_text(tracker.read_text(encoding="utf-8") + "\n| File | Findings | RESOLVED |\n|---|---|---|\n| `09_x.md` | 0 | 0 |\n\n### `09_x.md`\n", encoding="utf-8")
        assert_tracker_summaries_agree(audits, tracker)
        with pytest.raises(pytest.fail.Exception, match="different states"):
            assert_tracker_summaries_agree(audits, tracker, check_states=True)
