"""Unit tests for tracker_summary_parity: summaries and headings agree with the rows."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.tracker_summary_parity import assert_tracker_summaries_agree, heading_status_problems, summary_problems

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
