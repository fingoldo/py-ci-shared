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
