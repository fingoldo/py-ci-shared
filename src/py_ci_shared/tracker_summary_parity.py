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
* **Finding states** (:func:`disposition_state_problems`). Every statement about one finding (a tracker row, a table row of a round file, the
  ``Disposition:`` line under its heading) must open with the same state word, and a RESOLVED cell must not say the finding was not
  reproduced.
* **Heading status suffixes.** A finding heading that ENDS with a status (``### SQL-13 (P3) -- ... --
  DEFERRED``) must name the same base status as its tracker row.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from py_ci_shared._core import read_source
from py_ci_shared.audit_disposition_parity import parse_disposition
from py_ci_shared.audit_round_format import FINDING_ID_RE, TRACKER_ROW_RE, closing_word, round_files, table_cells, without_fenced_blocks

DEFAULT_STATUSES: tuple[str, ...] = ("RESOLVED", "WON'T FIX", "DEFERRED", "NOT A DEFECT")
_SUBSECTION = re.compile(r"^###\s+`([^`]+)`\s*$")
_FILE_CELL = re.compile(r"`([^`]+\.md)`|(?<![\w./-])([\w./-]+\.md)\b")
_STATUS_HEADERS = ("status", "disposition", "state")
_HEADING = re.compile(r"^###\s+(.+?)\s*$", re.M)


def _status_of(cell: str, statuses: tuple[str, ...]) -> "str | None":
    return closing_word(cell, statuses)


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(c and set(c) <= set("-: ") for c in cells)


def _finding_sections(lines: list[str]) -> dict[str, list[str]]:
    """``{file: [status cell of each row in its ### `<file>` subsection]}``.

    A table row directly followed by a separator row is that table's HEADER, whatever its words, and names the
    status column (``Status`` / ``Disposition`` / ``State``, else the first); every other row is a finding.
    """
    sections: dict[str, list[str]] = {}
    current = None
    status_col = 0
    for index, line in enumerate(lines):
        m = _SUBSECTION.match(line.strip())
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            status_col = 0
        elif line.startswith("## ") or line.startswith("### "):
            current = None
        elif current and line.lstrip().startswith("|"):
            cells = table_cells(line)
            if not cells or _is_separator(cells):
                continue
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if following.lstrip().startswith("|") and _is_separator(table_cells(following)):
                lowered = [c.strip("* ").lower() for c in cells]
                status_col = next((i for i, c in enumerate(lowered) if c in _STATUS_HEADERS), 0)
                continue
            sections[current].append(cells[status_col] if status_col < len(cells) else "")
    return sections


def _summary_columns(line: str, words: tuple[str, ...]) -> "tuple[int, dict[str, int]] | None":
    """``(findings column, {status: column})`` when *line* is a summary table's header, else None."""
    if not line.lstrip().startswith("|"):
        return None
    lowered = [h.strip("* ").lower() for h in table_cells(line)]
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
    width = max([findings_col, *columns.values()]) + 1
    while j < len(lines) and lines[j].lstrip().startswith("|"):
        cells = table_cells(lines[j])
        j += 1
        if not cells or not any(cells):
            problems.append(f"{where}: an empty summary row at line {j}")
            continue
        if len(cells) < width:
            problems.append(f"{where}: summary row at line {j} has {len(cells)} cell(s), the header needs {width}: {lines[j - 1].strip()}")
            continue
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
            problems.append(f"{where}: summary row at line {j} names no round file, so it cannot be checked: {lines[j - 1].strip()}")
            continue
        name = m.group(1) or m.group(2)
        if name not in sections:
            problems.append(f"{where}: summary row `{name}` has no ### `{name}` section to count")
            continue
        problems += _row_problems(name, cells, sections[name], findings_col, columns, words, where)
        for w, c in columns.items():
            totals[w] += _int(cells[c]) if c < len(cells) else 0
        total_findings += _int(cells[findings_col])
    return problems, j


def summary_table_count(tracker: Path, *, statuses: Iterable[str] = DEFAULT_STATUSES) -> int:
    """Summary tables in *tracker* outside fenced blocks, recognised by the same header rule the check applies
    (``**Findings**`` included)."""
    words = tuple(statuses)
    lines = without_fenced_blocks(read_source(tracker)).splitlines()
    return sum(1 for line in lines if _summary_columns(line, words) is not None)


def summary_problems(tracker: Path, *, statuses: Iterable[str] = DEFAULT_STATUSES) -> list[str]:
    words = tuple(statuses)
    lines = without_fenced_blocks(read_source(tracker)).splitlines()
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


_ROUND_HEADINGS_CACHE: dict[Path, tuple[tuple[tuple[str, int, int], ...], tuple[tuple[Path, tuple[str, ...]], ...]]] = {}


def _round_headings(audits_dir: Path) -> tuple[tuple[Path, tuple[str, ...]], ...]:
    """Every round file's ``###`` headings outside fenced blocks, read once per change of the file set and its contents.

    The tracker check runs once per tracker, and each run used to re-read and re-blank every round file: on this repository that was
    the same work repeated once per tracker. The key is each file's path, mtime and size, so an edit invalidates the entry.
    """
    files = round_files(audits_dir)
    stamp = tuple((str(f), st.st_mtime_ns, st.st_size) for f in files for st in (f.stat(),))
    hit = _ROUND_HEADINGS_CACHE.get(audits_dir)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    value = tuple((f, tuple(_HEADING.findall(without_fenced_blocks(read_source(f))))) for f in files)
    _ROUND_HEADINGS_CACHE[audits_dir] = (stamp, value)
    return value


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
    for line in read_source(tracker).splitlines():
        m = tracker_row_re.match(line)
        if m:
            status = _status_of(table_cells(line)[0], words)
            if status:
                tracked[m.group(1)] = status
    problems: list[str] = []
    for path, headings in _round_headings(audits_dir):
        for heading in headings:
            fid = finding_id_re.match(heading)
            end = suffix.search(heading)
            if fid and end and fid.group(1) in tracked and tracked[fid.group(1)] != end.group(1):
                problems.append(f"{path.parent.name}/{path.name}: {fid.group(1)}'s heading ends {end.group(1)}, its tracker row says {tracked[fid.group(1)]}")
    return problems


def assert_tracker_summaries_agree(
    audits_dir: Path, tracker: Path, *, statuses: Iterable[str] = DEFAULT_STATUSES, known: Iterable[str] = (), min_summaries: int = 1, check_states: bool = False
) -> None:
    known = list(known)  # consumed on more than one path below; a generator would be empty the second time
    import pytest

    if summary_table_count(tracker, statuses=statuses) < min_summaries:
        pytest.fail(f"fewer than {min_summaries} summary table(s) found in {tracker.name} -- the header format moved and this would check nothing")
    found = set(summary_problems(tracker, statuses=statuses) + heading_status_problems(audits_dir, tracker, statuses=statuses))
    if check_states:  # opt-in: a consumer's tracker may carry old disagreements it has to fix first
        found.update(disposition_state_problems(audits_dir))
    new, stale = sorted(found - set(known)), sorted(set(known) - found)
    if new or stale:
        pytest.fail(
            (f"{len(new)} place(s) where the tracker's summary disagrees with its rows:\n  " + "\n  ".join(new) if new else "")
            + (f"\n{len(stale)} accepted entr(ies) no longer reproduce -- remove them:\n  " + "\n  ".join(stale) if stale else "")
        )


# ---------------------------------------------------------------------------------------------------------------------------------------------
# one finding, one state, wherever it is written (audit 41-F13)
# ---------------------------------------------------------------------------------------------------------------------------------------------

_ID_CELL = re.compile(r"^`?([A-Za-z0-9]+-[A-Za-z]*\d+)`?$")
_FINDING_HEADING = re.compile(r"^#{2,4}\s+`?([A-Za-z0-9]+-[A-Za-z]*\d+)`?(?=[\s(`:])")
_NOT_REPRODUCED = re.compile(r"not[ -]reproduced", re.I)


def _state_of(text: str) -> "str | None":
    """The state word a disposition cell or line opens with (``PARTIALLY RESOLVED`` is ``PARTIAL``), else None."""
    parsed = parse_disposition("Disposition: " + text.strip().lstrip("*`_ "))
    return parsed[0] if parsed else None


def _table_claims(lines: list[str], where: str) -> list[tuple[str, str, str, str]]:
    """``(id, state, where, cell)`` for each table row whose first cell is a finding id; the state cell is the header's
    Status/Disposition/State column, else the last cell."""
    claims = []
    column = -1
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = table_cells(line)
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if following.lstrip().startswith("|") and _is_separator(table_cells(following)):
            lowered = [c.strip("* ").lower() for c in cells]
            column = next((i for i, c in enumerate(lowered) if c in _STATUS_HEADERS), -1)
            continue
        m = _ID_CELL.match(cells[0]) if cells else None
        if m and len(cells) > 1:
            cell = cells[column] if 0 <= column < len(cells) else cells[-1]
            state = _state_of(cell)
            if state:
                claims.append((m.group(1), state, f"{where}:{index + 1}", cell))
    return claims


def _heading_claims(lines: list[str], where: str) -> list[tuple[str, str, str, str]]:
    claims = []
    current: "str | None" = None
    for index, line in enumerate(lines):
        if line.startswith("#"):
            m = _FINDING_HEADING.match(line)
            current = m.group(1) if m else None
        elif current:
            parsed = parse_disposition(line)
            if parsed:
                claims.append((current, parsed[0], f"{where}:{index + 1}", line.strip()))
                current = None
    return claims


def _claim_files(audits_dir: Path) -> list[Path]:
    files = round_files(audits_dir)
    for parent in (audits_dir, audits_dir / "implemented"):
        if parent.is_dir():
            files += sorted(p for p in parent.glob("**/TRACKER*.md") if p not in files)
    return files


def disposition_state_problems(audits_dir: Path) -> list[str]:
    """Findings whose state is written differently in two places (tracker row, round-file table row, ``Disposition:`` line), and
    RESOLVED cells that say the finding was not reproduced. The fix belongs in the tracker or finding text, never in an exemption."""
    claims: list[tuple[str, str, str, str]] = []
    for path in _claim_files(audits_dir):
        lines = without_fenced_blocks(read_source(path)).splitlines()
        where = path.relative_to(audits_dir).as_posix()
        claims += _table_claims(lines, where) + _heading_claims(lines, where)
    by_id: dict[str, list[tuple[str, str, str]]] = {}
    problems: list[str] = []
    for fid, state, where, cell in claims:
        by_id.setdefault(fid, []).append((state, where, cell))
        if state == "RESOLVED" and _NOT_REPRODUCED.search(cell):
            problems.append(f"{where}: {fid} is RESOLVED but its cell says it was not reproduced")
    for fid, entries in sorted(by_id.items()):
        if len({state for state, _, _ in entries}) > 1:
            said = "; ".join(f"{state} at {where}" for state, where, _ in entries)
            problems.append(f"{fid} is written with different states: {said}")
    return problems
