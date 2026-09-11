"""Shared check: a tracker's summary tables and headings agree with the rows they summarise.

``audit_round_format`` makes each finding countable. It does not look at what the tracker SAYS the
count is, and on 2026-09-12 production_scrapers' tracker said, for one round, "0 RESOLVED / 100 open"
above a hundred rows that were every one of them dispositioned; another round's summary showed eight
DEFERRED that no row carried. A reader trusts the summary -- it is the reason to have one.

Two checks, both recomputed from the rows:

* **Summary tables.** A table whose header has a ``File`` column, a ``Findings`` column and one column
  per status word is a summary. Each of its rows names a file in backticks; the per-finding section
  for that file is the ``### `<file>` `` subsection. The row's ``Findings`` must equal the number of
  finding rows there, and each status column the number whose status cell opens with that word. A
  ``**total**`` row must equal the column sums.
* **Heading status suffixes.** A finding heading that ENDS with a status (``### SQL-13 (P3) -- ... --
  DEFERRED``) must name the same base status as its tracker row.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from py_ci_shared.audit_round_format import FINDING_ID_RE, TRACKER_ROW_RE, _table_cells, closing_word, round_files, without_fenced_blocks

DEFAULT_STATUSES: tuple[str, ...] = ("RESOLVED", "WON'T FIX", "DEFERRED", "NOT A DEFECT")
_SUBSECTION = re.compile(r"^###\s+`([^`]+)`\s*$")
_FILE_CELL = re.compile(r"`([^`]+\.md)`")
_HEADING = re.compile(r"^###\s+(.+?)\s*$", re.M)


def _status_of(cell: str, statuses: tuple[str, ...]) -> "str | None":
    return closing_word(cell, statuses)


def _finding_sections(lines: list[str]) -> dict[str, list[str]]:
    """``{file: [status cell of each row in its ### `<file>` subsection]}``."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in lines:
        m = _SUBSECTION.match(line.strip())
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
        elif line.startswith("## ") or line.startswith("### "):
            current = None
        elif current and line.lstrip().startswith("|"):
            cells = _table_cells(line)
            if cells and not all(set(c) <= set("-: ") for c in cells) and cells[0].lower() not in ("status", "disposition"):
                sections[current].append(cells[0])
    return sections


def _summary_columns(line: str, words: tuple[str, ...]) -> "tuple[int, dict[str, int]] | None":
    """``(findings column, {status: column})`` when *line* is a summary table's header, else None."""
    if not line.lstrip().startswith("|"):
        return None
    lowered = [h.strip("* ").lower() for h in _table_cells(line)]
    if "file" not in lowered or "findings" not in lowered or not any(w.lower() in lowered for w in words):
        return None
    return lowered.index("findings"), {w: lowered.index(w.lower()) for w in words if w.lower() in lowered}


def _int(cell: str) -> int:
    value = cell.strip("* ")
    return int(value) if value.isdigit() else 0


def _row_problems(name: str, cells: list[str], rows: list[str], findings_col: int, columns: dict[str, int], words: tuple[str, ...], where: str) -> list[str]:
    problems = []
    if cells[findings_col].strip("* ") != str(len(rows)):
        problems.append(f"{where}: `{name}` says {cells[findings_col].strip('* ')} findings, its section has {len(rows)} rows")
    for w, c in columns.items():
        counted = sum(1 for r in rows if _status_of(r, words) == w)
        said = cells[c].strip("* ") if c < len(cells) else ""
        if said != str(counted):
            problems.append(f"{where}: `{name}` says {said} {w}, its rows say {counted}")
    return problems


def _table_problems(lines: list[str], start: int, sections: dict[str, list[str]], words: tuple[str, ...], where: str) -> "tuple[list[str], int]":
    """Problems in the summary table whose header is ``lines[start]``, and the index just past it."""
    findings_col, columns = _summary_columns(lines[start], words)  # type: ignore[misc]
    problems: list[str] = []
    totals = {w: 0 for w in columns}
    total_findings = 0
    j = start + 2
    while j < len(lines) and lines[j].lstrip().startswith("|"):
        cells = _table_cells(lines[j])
        j += 1
        if "total" in cells[0].lower():
            said = {w: cells[c].strip("* ") for w, c in columns.items() if c < len(cells)}
            expect = {w: str(totals[w]) for w in columns}
            if said != expect or _int(cells[findings_col]) != total_findings:
                problems.append(
                    f"{where}: the total row reads {cells[findings_col].strip('* ')} findings {said}, the rows above sum to {total_findings} {expect}"
                )
            continue
        m = _FILE_CELL.search(cells[0])
        if m is None:
            continue
        name = m.group(1)
        if name not in sections:
            problems.append(f"{where}: summary row `{name}` has no ### `{name}` section to count")
            continue
        problems += _row_problems(name, cells, sections[name], findings_col, columns, words, where)
        for w, c in columns.items():
            totals[w] += _int(cells[c]) if c < len(cells) else 0
        total_findings += _int(cells[findings_col])
    return problems, j


def summary_problems(tracker: Path, *, statuses: Iterable[str] = DEFAULT_STATUSES) -> list[str]:
    words = tuple(statuses)
    lines = without_fenced_blocks(tracker.read_text(encoding="utf-8", errors="replace")).splitlines()
    sections = _finding_sections(lines)
    problems: list[str] = []
    i = 0
    while i < len(lines):
        if _summary_columns(lines[i], words) is None:
            i += 1
            continue
        found, i = _table_problems(lines, i, sections, words, tracker.name)
        problems += found
    return problems


def heading_status_problems(
    audits_dir: Path,
    tracker: Path,
    *,
    statuses: Iterable[str] = DEFAULT_STATUSES,
    finding_id_re: "re.Pattern[str]" = FINDING_ID_RE,
    tracker_row_re: "re.Pattern[str]" = TRACKER_ROW_RE,
) -> list[str]:
    words = tuple(statuses)
    suffix = re.compile(r"(?:--|—|-)\s*(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\s*$")
    tracked: dict[str, str] = {}
    for line in tracker.read_text(encoding="utf-8", errors="replace").splitlines():
        m = tracker_row_re.match(line)
        if m:
            status = _status_of(_table_cells(line)[0], words)
            if status:
                tracked[m.group(1)] = status
    problems: list[str] = []
    for path in round_files(audits_dir):
        for heading in _HEADING.findall(without_fenced_blocks(path.read_text(encoding="utf-8", errors="replace"))):
            fid = finding_id_re.match(heading)
            end = suffix.search(heading)
            if fid and end and fid.group(1) in tracked and tracked[fid.group(1)] != end.group(1):
                problems.append(f"{path.parent.name}/{path.name}: {fid.group(1)}'s heading ends {end.group(1)}, its tracker row says {tracked[fid.group(1)]}")
    return problems


def assert_tracker_summaries_agree(
    audits_dir: Path, tracker: Path, *, statuses: Iterable[str] = DEFAULT_STATUSES, known: Iterable[str] = (), min_summaries: int = 1
) -> None:
    import pytest

    text = tracker.read_text(encoding="utf-8", errors="replace").lower()
    if text.count("| findings |") < min_summaries:
        pytest.fail(f"fewer than {min_summaries} summary table(s) found in {tracker.name} -- the header format moved and this would check nothing")
    found = set(summary_problems(tracker, statuses=statuses) + heading_status_problems(audits_dir, tracker, statuses=statuses))
    new, stale = sorted(found - set(known)), sorted(set(known) - found)
    if new or stale:
        pytest.fail(
            (f"{len(new)} place(s) where the tracker's summary disagrees with its rows:\n  " + "\n  ".join(new) if new else "")
            + (f"\n{len(stale)} accepted entr(ies) no longer reproduce -- remove them:\n  " + "\n  ".join(stale) if stale else "")
        )
