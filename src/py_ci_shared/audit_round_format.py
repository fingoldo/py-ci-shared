"""Shared checks: an audit tracker and its round files stay countable by machine.

A round of findings is only closed if it can be COUNTED as closed, and every failure here was a count
that read clean while missing part of the file (production_scrapers, 2026-09-07..09):

* **Two spellings of one status.** ``| **RESOLVED** |`` and ``| RESOLVED |`` both appeared; a status
  report keyed on the bold one said "148 RESOLVED / 11 DEFERRED" when the truth was 206 and 14. A
  QUALIFIED status (``RESOLVED (5/6)``) then fell between the spelling check and the word check.
* **An addendum at the finding level.** ``### TEST-3 addendum ...`` reads, to every tool, as a second
  finding called TEST-3 with no disposition.
* **One id, two findings.** ``CORR-1`` was defined by two rounds and the codebase's prose used it for both.
* **Tracker and findings drifting apart**, in either direction.
* **A claim asserting a string is ABSENT from a whole file**, in a disposition verifier. It went green four
  times for one reason: the file's own comment explaining the fix contained the banned string.
* **A round filed in the wrong place** (autopsia, 2026-09-12): a round whose every row was closed still sat in the
  open tree, and nothing noticed because the tracker kept its disposition in a named column, not the first one, so
  every check above read it as an older format and counted nothing. `assert_rounds_filed` finds the column by name.

Every format is a parameter, with production_scrapers' conventions as the defaults, and every check
has a floor on what it parsed: a check whose pattern stopped matching reports a clean tree otherwise.
"""

from __future__ import annotations

import ast
import collections
import re
from collections.abc import Iterable
from pathlib import Path

DEFAULT_STATUSES: tuple[str, ...] = ("RESOLVED", "WON'T FIX", "DEFERRED", "NOT A DEFECT")
#: A tracker row's status cell in the one spelling: ``| **WORD** |``, optionally followed by a qualifier.
BOLD_STATUS_RE = re.compile(r"^\|\s*\*\*([A-Z'’ ]+)\*\*[^|]*\|")
#: ``### SQL-1 (P2) -- ...``: a finding heading.
FINDING_ID_RE = re.compile(r"^([A-Z]+-\d+) \(")
#: Either ``**Disposition:** X`` or ``**Disposition: X**`` -- anchored on the bold-open and the word.
DISPOSITION_RE = re.compile(r"\*\*Disposition")
#: ``| STATUS | SEV | `ID` | ...``: a tracker row's own id, reached through the status and severity cells.
TRACKER_ROW_RE = re.compile(r"^\|\s*[^|]+\|\s*(?:P\d|Low|Med|High|Info)\s*\|\s*`([A-Z]+-\d+)`", re.MULTILINE)
_HEADING_RE = re.compile(r"^### (.+)$", re.MULTILINE)
_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}")


# ------------------------------------------------------------------ the tracker's status column
def tracker_rows(tracker: Path) -> list[str]:
    return [ln for ln in tracker.read_text(encoding="utf-8").splitlines() if ln.startswith("|")]


def status_problems(tracker: Path, statuses: Iterable[str] = DEFAULT_STATUSES) -> "tuple[list[str], list[str]]":
    """(problems, parsed status words). A cell that MENTIONS a status word must be the bold form of one."""
    words = tuple(statuses)
    mention = re.compile("|".join(re.escape(s) for s in words))
    problems, parsed = [], []
    for line in tracker_rows(tracker):
        if line.count("|") < 2:
            continue
        cell = line.split("|")[1].strip()
        if not mention.search(cell):
            continue
        m = BOLD_STATUS_RE.match(line)
        if not m:
            problems.append(f"not in the `**WORD**` form every count keys on: {line[:100]}")
            continue
        word = m.group(1).strip()
        parsed.append(word)
        if word not in words:
            problems.append(f"status word outside {list(words)}: {word!r} in {line[:80]}")
    return problems, parsed


def assert_tracker_statuses_countable(tracker: Path, *, statuses: Iterable[str] = DEFAULT_STATUSES, min_rows: int = 1) -> None:
    import pytest

    problems, parsed = status_problems(tracker, statuses)
    if len(parsed) < min_rows:
        pytest.fail(f"only {len(parsed)} status cells parsed from {tracker.name}; expected at least {min_rows} -- the format moved or the pattern broke")
    if problems:
        pytest.fail(f"{len(problems)} tracker status cell(s) every count would miss:\n  " + "\n  ".join(problems[:30]))


# ------------------------------------------------------------------ round files
def without_fenced_blocks(text: str) -> str:
    """*text* with fenced code blocks blanked line for line: a finding QUOTING a heading is not one."""
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else line)
    return "\n".join(out)


def round_files(audits_dir: Path) -> list[Path]:
    """The ``.md`` files of every dated round directory, open and ``implemented/``, README excluded."""
    files: list[Path] = []
    for parent in (audits_dir, audits_dir / "implemented"):
        if parent.is_dir():
            for d in sorted(parent.iterdir()):
                if d.is_dir() and _DATED.match(d.name):
                    files.extend(sorted(f for f in d.glob("*.md") if f.name != "README.md"))
    return files


def _text(path: Path) -> str:
    return without_fenced_blocks(path.read_text(encoding="utf-8", errors="replace"))


def finding_problems(
    path: Path,
    *,
    finding_id_re: "re.Pattern[str]" = FINDING_ID_RE,
    disposition_re: "re.Pattern[str]" = DISPOSITION_RE,
    not_findings: Iterable[str] = (),
) -> "list[str] | None":
    """Problems in one round file, or None when its round keeps dispositions elsewhere (a thematic older format)."""
    if not any(disposition_re.search(_text(f)) for f in path.parent.glob("*.md")):
        return None
    prose = set(not_findings)
    text = _text(path)
    problems = []
    for match in _HEADING_RE.finditer(text):
        heading = match.group(1).strip()
        if heading in prose:
            continue
        found = finding_id_re.match(heading)
        if not found:
            problems.append(f"{path.name}: `### {heading[:70]}` sits at the finding level but is not a finding id; continuations go at `####`")
            continue
        end = text.find("\n### ", match.end())
        if not disposition_re.search(text[match.end() : end if end != -1 else len(text)]):
            problems.append(f"{path.name}: finding {found.group(1)} has no disposition")
    return problems


def defined_ids(files: Iterable[Path], finding_id_re: "re.Pattern[str]" = FINDING_ID_RE) -> "dict[str, list[str]]":
    """``{finding id: [round/file that defines it]}`` from ``###`` headings."""
    defined: dict[str, list[str]] = collections.defaultdict(list)
    for path in files:
        for match in _HEADING_RE.finditer(_text(path)):
            found = finding_id_re.match(match.group(1).strip())
            if found:
                defined[found.group(1)].append(f"{path.parent.name}/{path.name}")
    return dict(defined)


def assert_rounds_countable(
    audits_dir: Path,
    *,
    tracker: "Path | None" = None,
    finding_id_re: "re.Pattern[str]" = FINDING_ID_RE,
    disposition_re: "re.Pattern[str]" = DISPOSITION_RE,
    tracker_row_re: "re.Pattern[str]" = TRACKER_ROW_RE,
    not_findings: Iterable[str] = (),
    min_files: int = 1,
    min_ids: int = 1,
) -> None:
    """Every finding heading is an id with a disposition; no id is defined twice; tracker rows and findings match."""
    import pytest

    files = round_files(audits_dir)
    if len(files) < min_files:
        pytest.fail(f"only {len(files)} round file(s) under {audits_dir}; expected at least {min_files}")
    problems: list[str] = []
    checked = 0
    for path in files:
        found = finding_problems(path, finding_id_re=finding_id_re, disposition_re=disposition_re, not_findings=not_findings)
        if found is not None:
            checked += 1
            problems.extend(found)
    if not checked:
        pytest.fail("no round keeps dispositions in its files, or the disposition pattern stopped matching; nothing was checked")
    ids = defined_ids(files, finding_id_re)
    if len(ids) < min_ids:
        pytest.fail(f"only {len(ids)} finding ids found; expected at least {min_ids}")
    problems.extend(f"{fid} is defined by more than one heading: {', '.join(where)}" for fid, where in sorted(ids.items()) if len(where) > 1)
    if tracker is not None:
        tracked = {m.group(1) for m in tracker_row_re.finditer(tracker.read_text(encoding="utf-8", errors="replace"))}
        problems.extend(f"{fid} has a finding section but no tracker row" for fid in sorted(set(ids) - tracked))
        problems.extend(f"tracker row {fid} names no finding section" for fid in sorted(tracked - set(ids)))
    if problems:
        pytest.fail(f"{len(problems)} problem(s) that keep the rounds from being counted:\n  " + "\n  ".join(problems[:40]))


# ------------------------------------------------------------------ closed rounds are filed
#: Words that close a tracker row. A repo passes its own; these are the union two trackers already use.
DEFAULT_CLOSING: tuple[str, ...] = ("RESOLVED", "DOCUMENTED", "FUTURE", "REJECTED", "WON'T FIX", "DEFERRED", "NOT A DEFECT")
#: Header names of the column a tracker keeps its disposition in, matched case-insensitively.
DEFAULT_STATUS_COLUMNS: tuple[str, ...] = ("disposition", "status")


_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _table_cells(line: str) -> list[str]:
    """Cells split on UNESCAPED pipes: a finding that quotes ``2 + \\|delta\\|`` otherwise shifts every column after it."""
    return [c.strip() for c in _UNESCAPED_PIPE.split(line.strip().strip("|"))]


def _is_totals_row(label: str, status: str) -> bool:
    """A bold first cell over an empty status is a summary line (``| **Total** | 9 | ... | |``), not an open row."""
    return label.startswith("**") and label.endswith("**") and not status.strip()


def tracker_status_cells(tracker: Path, status_columns: Iterable[str] = DEFAULT_STATUS_COLUMNS) -> "list[tuple[str, str]] | None":
    """``(row label, status cell)`` for the first table whose header names a status column; None when no table does.

    The column is found by its NAME, not its position: trackers written weeks apart put the disposition first, fifth
    or last, and `status_problems`' first-cell rule read every such tracker as having no status at all. The row label
    is the row's first cell, so a problem string stays stable when a line is inserted above it.
    """
    wanted = {c.lower() for c in status_columns}
    lines = _text(tracker).splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        header = _table_cells(lines[i])
        column = next((k for k, name in enumerate(header) if name.strip("*`_ ").lower() in wanted), None)
        j = i + 1
        block = []
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            block.append(_table_cells(lines[j]))
            j += 1
        if column is not None:
            return [
                (cells[0], cells[column])
                for cells in block
                if column < len(cells) and not all(set(c) <= set("-: ") for c in cells) and not _is_totals_row(cells[0], cells[column])
            ]
        i = j
    return None


def closing_word(cell: str, closing: Iterable[str] = DEFAULT_CLOSING) -> "str | None":
    """The closing word *cell* opens with - bold, backticks, case and a curly apostrophe ignored - or None."""
    lead = cell.strip().lstrip("*`_ ").upper().replace("’", "'")
    return next((word for word in sorted(closing, key=len, reverse=True) if lead.startswith(word.upper())), None)


def round_filing_problems(
    audits_dir: Path,
    *,
    closing: Iterable[str] = DEFAULT_CLOSING,
    status_columns: Iterable[str] = DEFAULT_STATUS_COLUMNS,
    implemented: str = "implemented",
    tracker_glob: str = "TRACKER*.md",
) -> list[str]:
    """Where a dated round sits must agree with its tracker. Line-number-free strings, so a baseline survives edits.

    * a round filed under ``implemented/`` whose tracker has a row that is not closed;
    * a round still in the open tree whose every tracker row IS closed - it should have been moved;
    * a tracker with no table carrying a status column, which nothing can count;
    * an open-tree round with no tracker at all, whose closure therefore cannot be shown either way.

    Older ``implemented/`` rounds without a tracker are not reported: they predate the tracker convention and were
    filed by hand; requiring one retroactively would only restate history.
    """
    words = tuple(closing)
    columns = tuple(status_columns)
    problems: list[str] = []
    for parent, filed in ((audits_dir, False), (audits_dir / implemented, True)):
        if not parent.is_dir():
            continue
        for rnd in sorted(d for d in parent.iterdir() if d.is_dir() and _DATED.match(d.name)):
            where = rnd.relative_to(audits_dir).as_posix()
            trackers = sorted(rnd.glob(tracker_glob))
            if not trackers and not filed:
                problems.append(f"{where}: open round with no {tracker_glob} - its closure cannot be counted")
            for tracker in trackers:
                rows = tracker_status_cells(tracker, columns)
                if not rows:
                    problems.append(f"{where}/{tracker.name}: no table with a {'/'.join(columns)} column and a row - nothing to count")
                    continue
                open_rows = [label for label, cell in rows if closing_word(cell, words) is None]
                if filed:
                    problems.extend(f"{where}/{tracker.name}: row {label!r} is not closed, yet the round is filed under {implemented}/" for label in open_rows)
                elif not open_rows:
                    problems.append(f"{where}: every row of {tracker.name} is closed - move the round to {implemented}/")
    return problems


def assert_rounds_filed(
    audits_dir: Path,
    *,
    known: Iterable[str] = (),
    min_trackers: int = 1,
    implemented: str = "implemented",
    tracker_glob: str = "TRACKER*.md",
    **kwargs: Iterable[str],
) -> None:
    """`round_filing_problems` as a shrink-only ratchet: a problem not in *known* fails, and so does a *known* entry
    that no longer reproduces. Fails too when fewer than *min_trackers* trackers exist, since zero parsed is clean."""
    import pytest

    count = sum(len(list(d.glob(tracker_glob))) for parent in (audits_dir, audits_dir / implemented) if parent.is_dir() for d in parent.iterdir() if d.is_dir())
    if count < min_trackers:
        pytest.fail(f"only {count} {tracker_glob} file(s) under {audits_dir}; expected at least {min_trackers} -- the layout moved or the glob broke")
    found = set(round_filing_problems(audits_dir, implemented=implemented, tracker_glob=tracker_glob, **kwargs))
    baseline = set(known)
    new, stale = sorted(found - baseline), sorted(baseline - found)
    if new or stale:
        pytest.fail(
            (f"{len(new)} audit round(s) filed against their own tracker - close the rows, or move the round:\n  " + "\n  ".join(new) if new else "")
            + (f"\n{len(stale)} baseline entr(ies) no longer reproduce - remove them:\n  " + "\n  ".join(stale) if stale else "")
        )


# ------------------------------------------------------------------ disposition verifiers
def absence_comparisons(source: str) -> list[tuple[int, str, str]]:
    """``(line, left, right)`` for every ``X not in Y`` comparison in *source*."""
    return [
        (node.lineno, ast.unparse(node.left), ast.unparse(node.comparators[0]))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare) and node.ops and isinstance(node.ops[0], ast.NotIn)
    ]


def is_whole_file_read(expr: str, readers: Iterable[str] = ("src", "sibling_src")) -> bool:
    """``src("x.py")`` is a whole file; ``src("x.py").split(...)[1][:200]`` is a slice a reader can audit."""
    stripped = expr.strip()
    return any(stripped.startswith(f"{r}(") for r in readers) and stripped.endswith(")") and "[" not in stripped


def assert_no_whole_file_absence_claims(verifier: Path, *, readers: Iterable[str] = ("src", "sibling_src")) -> None:
    """No ``"literal" not in src("file.py")``: a file that comments its fix contains the string it bans."""
    import pytest

    names = tuple(readers)
    offenders = [
        f"line {line}: {left[:40]} not in {right}"
        for line, left, right in absence_comparisons(verifier.read_text(encoding="utf-8"))
        if is_whole_file_read(right, names)
    ]
    if offenders:
        pytest.fail(
            "claim(s) asserting a literal is absent from a WHOLE file -- such a file comments its fix with the banned "
            "string, so the claim goes green for the wrong reason. Use a parsed helper or a slice:\n  " + "\n  ".join(offenders)
        )
