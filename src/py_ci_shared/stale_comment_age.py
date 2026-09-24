"""Shared check: a TODO or a commented-out call does not quietly become permanent.

A ``TODO`` is a promise with no due date, and commented-out code is a decision nobody finished
making. Both read as "in progress" forever. Two real examples from the 2026-09-02 glossum round:

* ``landing_renderer.dart:285`` carried ``// buildCta(context, onLaunchSurvey),`` for 37 days while
  the CTA it belonged to was already gone from the product (P05-17 / P07-3). Every renderer author
  after that read a hook that was not a hook.
* ``fcm_service.dart:112`` carried ``TODO: Call this AFTER user completes their first lesson`` for
  37 days while the service kept asking for the notification permission at app start -- the exact
  thing the TODO warned against (P07-14), and the reason the grant rate was what it was.

The rule is age, not existence: a TODO written this week is a note; one that has survived a release
cycle is a decision made by default. ``git blame`` gives the age, so the check needs no annotations
and cannot be gamed by re-indenting. An issue reference right after the marker (``TODO(#123)``,
``TODO(sourcemaps)``, ``TODO: #123``, a URL) exempts the comment -- that is a tracked promise, which is
the outcome this check wants. A gate that cannot date a line (shallow clone, blame failure, missing
scan directory) fails instead of passing on nothing.

Deliberately dependency-free (``git blame --line-porcelain`` via subprocess), language-agnostic:
``//``, ``#``, ``--`` and ``/*`` comment markers are all recognised, whole-line or trailing.

Usage::

    from py_ci_shared.stale_comment_age import assert_no_stale_todos

    def test_no_todo_older_than_a_release():
        assert_no_stale_todos(REPO, ["lib", "tool"], max_age_days=30)
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import time
import tokenize
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Optional

from ._core import DEFAULT_EXCLUDE, CorpusError, SourceReadError, iter_files, read_source

_MARKERS = r"(?:TODO|FIXME|HACK|XXX)"
# Applied to a comment's TEXT (marker stripped), so a trailing `x = 1  # TODO fix` is seen as well as a whole-line one.
_TODO_TEXT_RE = re.compile(rf"^\s*(?P<kw>{_MARKERS})\b(?P<ref>\([^)]*\))?(?P<after>.*)$", re.IGNORECASE | re.DOTALL)
# A tracked reference written right after the marker: `TODO: #12`, `TODO - https://...`, `FIXME ABC-12:`.
_LEADING_REF_RE = re.compile(r"^\s*[:\-]?\s*(?:#\d+\b|https?://\S+|(?P<key>[A-Z][A-Z0-9]+)-\d+\b)")
# Upper-case prefixes that form `WORD-123` without being an issue tracker key.
_NOT_ISSUE_KEYS = frozenset({"UTF", "UCS", "ISO", "SHA", "MD", "CP", "RFC", "PEP", "ECMA", "IEEE", "TLS", "SSL", "HTTP", "IPV", "CVE"})
# A commented-out statement: a call ending in `;` or `,`. In `#`-comment languages (Python, shell, YAML) a
# statement has no terminator, so a bare call is enough there.
_COMMENTED_CODE_RE = re.compile(rf"^\s*(?://+|#)\s*(?!{_MARKERS})[\w.]+\s*\([^;]*\)\s*[;,]\s*$", re.IGNORECASE)
_COMMENTED_HASH_CALL_RE = re.compile(rf"^\s*#\s*(?!{_MARKERS})[\w.]+\s*\([^;]*\)\s*[;,]?\s*$", re.IGNORECASE)
_HASH_SUFFIXES = frozenset({".py", ".sh", ".yaml", ".yml"})
_DEFAULT_SUFFIXES = (".dart", ".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".sql", ".yaml", ".yml")
#: Directory names never scanned, on top of the shared default set.
DEFAULT_SKIP_DIRS: frozenset[str] = DEFAULT_EXCLUDE | frozenset({".dart_tool"})
_BLAME_HEADER_RE = re.compile(r"^[0-9a-f]{7,64}\s+\d+\s+(\d+)")
#: Line ranges per `git blame` call: keeps the command line far below the Windows 32k limit.
_BLAME_BATCH = 200

# Words that carry an English sentence but essentially never stand alone as a token in code.
# Deliberately conservative: keywords and plausible identifiers (`is`, `in`, `and`, `or`, `for`,
# `as`, `from`, `to`, `not`, `if`, `with`, `this`) are absent, because dropping a real
# commented-out statement is the expensive mistake and keeping one prose line is the cheap one.
_PROSE_WORDS = (
    "the an but which because however rather instead whether than its their there " "would should does was were been we you they our your etc"
).split()
_PROSE_WORD_RE = re.compile(r"(?<![\w.])(?:" + "|".join(_PROSE_WORDS) + r"|e\.g\.|i\.e\.)(?![\w])", re.IGNORECASE)
# A lone "a" needs its own pattern: as a word it is prose, but `a` is also an ordinary variable
# name, so require an article's shape - "a" followed by a word.
_PROSE_ARTICLE_RE = re.compile(r"(?<![\w.])a\s+[a-z]{2,}", re.IGNORECASE)
_COMMENT_LINE_RE = re.compile(r"^\s*(?://+|#)\s?(?P<body>.*)$")
# A sentence-ending period, anchored so that `...`, a decimal and a dotted identifier do not count.
_SENTENCE_END_RE = re.compile(r"[\w)\"'\]]\.$")
_STRING_LITERAL_RE = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`")
# A heading with a parenthesised gloss: `# Upsert (handles race conditions)`, `# Cost (USD)`, `# mover (stem-changing)`.
# Code is written `name(args)`; a bare word, a space, then `(` is how English glosses a term. Only a single bare
# word counts, so a dotted `obj.method (x)` or a chained `.agg (...)` is still judged as code.
_GLOSSED_WORD_RE = re.compile(r"^\s*[^\W\d]\w*\s+\(", re.UNICODE)


class BlameError(RuntimeError):
    """``git blame`` could not date a line, so the gate cannot tell a fresh TODO from a stale one."""


def _comment_body(line: str) -> "str | None":
    """The text of ``line`` with its comment marker stripped, or None if it is not a comment."""
    m = _COMMENT_LINE_RE.match(line)
    return m.group("body") if m else None


def _body_is_code(body: str, python: bool) -> bool:
    """True when a comment body that matched the call shape is really a statement, not a glossed heading.

    For Python the body must also parse: `# Upsert (handles race conditions at DB level too)` has
    the regex's shape but is not a statement. A leading `.` (a commented-out continuation of a
    method chain, `# .group_by(...).agg(...)`) is given a receiver before parsing.
    """
    if _GLOSSED_WORD_RE.match(body):
        return False
    if not python:
        return True
    text = body.strip().rstrip(";,").strip()
    if text.startswith("."):
        text = "_" + text
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    if len(tree.body) != 1:
        return False
    stmt = tree.body[0]
    # A bare expression that is not a call computes nothing, so nobody commented it out:
    # `# rejected_invented(4) / rejected(7)` is a legend of status codes written in call syntax.
    # A tuple of calls (`# a(x), b(y),`) is a commented-out argument list and still counts.
    if not isinstance(stmt, ast.Expr):
        return True
    values = stmt.value.elts if isinstance(stmt.value, ast.Tuple) else [stmt.value]
    return bool(values) and all(isinstance(v, (ast.Call, ast.Await)) for v in values)


def _block_reads_as_prose(lines: Sequence[str], index: int) -> bool:
    """True when the contiguous comment block around ``lines[index]`` is English, not code.

    One line cannot answer this. ``# behavior_config.target_temporal_audit_unit ('s' / 'ms');``
    is indistinguishable from a call by structure alone, and so is
    ``# ErrorService.setUserId(null), AnalyticsService.setUserId(null),``. What separates them
    from real dead code is what surrounds them: commented-out code sits among more commented-out
    code, while a code-shaped sentence sits inside a paragraph.

    Measured across five repositories before this was added, the line-only rule matched 37
    comments, of which exactly one was actually commented-out code.
    """
    start = index
    while start > 0 and _comment_body(lines[start - 1]) is not None:
        start -= 1
    end = index
    while end + 1 < len(lines) and _comment_body(lines[end + 1]) is not None:
        end += 1
    for i in range(start, end + 1):
        body = (_comment_body(lines[i]) or "").strip()
        if not body:
            continue
        # Strip string literals first: `{ error: "email failed" }` is code whose payload happens
        # to be English, and that payload is not evidence about the line.
        stripped = _STRING_LITERAL_RE.sub("", body)
        if _SENTENCE_END_RE.search(stripped) or _PROSE_WORD_RE.search(stripped) or _PROSE_ARTICLE_RE.search(stripped):
            return True
    return False


def _markers_for(suffix: str) -> tuple[str, ...]:
    if suffix in _HASH_SUFFIXES:
        return ("#",)
    if suffix == ".sql":
        return ("--", "/*")
    return ("//", "/*")


def _line_comment(line: str, markers: Sequence[str]) -> Optional[str]:
    """The comment text on *line* (marker stripped), found outside quoted strings; a block-comment continuation
    line (leading ``*``) counts as a comment. ``None`` when the line carries no comment."""
    stripped = line.lstrip()
    if "/*" in markers and stripped.startswith("*") and not stripped.startswith("*/"):
        return stripped[1:]
    quote: Optional[str] = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        else:
            for marker in markers:
                if line.startswith(marker, i) and (marker != "#" or i == 0 or line[i - 1].isspace()):
                    text = line[i + len(marker) :]
                    return text.lstrip("/") if marker == "//" else text
        i += 1
    return None


def _python_comments(source: str) -> Optional[dict[int, str]]:
    """``{line: comment text}`` from the tokenizer (exact for Python), or ``None`` when it cannot tokenize."""
    out: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string[1:]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return out


def _comments(path: Path, source: str, lines: Sequence[str]) -> dict[int, str]:
    if path.suffix == ".py":
        found = _python_comments(source)
        if found is not None:
            return found
    markers = _markers_for(path.suffix)
    out: dict[int, str] = {}
    for i, line in enumerate(lines, start=1):
        text = _line_comment(line, markers)
        if text is not None:
            out[i] = text
    return out


def _has_issue_ref(match: "re.Match[str]") -> bool:
    if match.group("ref"):
        return True
    lead = _LEADING_REF_RE.match(match.group("after"))
    if lead is None:
        return False
    return lead.group("key") is None or lead.group("key") not in _NOT_ISSUE_KEYS


def _todo(comment: str) -> Optional["re.Match[str]"]:
    return _TODO_TEXT_RE.match(comment)


def _ranges(lines: Sequence[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for n in sorted(set(lines)):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def _git(repo_root: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def _blame_ages(repo_root: Path, rel_path: str, lines: Sequence[int]) -> dict[int, float]:
    """Return ``{line_number: age_in_days}`` for ``lines`` of ``rel_path``. Raises :class:`BlameError` when git fails."""
    if not lines:
        return {}
    ages: dict[int, float] = {}
    now = time.time()
    spans = _ranges(lines)
    for start in range(0, len(spans), _BLAME_BATCH):
        args = ["blame", "--line-porcelain"]
        for a, b in spans[start : start + _BLAME_BATCH]:
            args += ["-L", f"{a},{b}"]
        args += ["--", rel_path]
        try:
            proc = _git(repo_root, *args)
        except OSError as exc:
            raise BlameError(f"{rel_path}: cannot run git blame: {exc}") from exc
        if proc.returncode != 0:
            raise BlameError(f"{rel_path}: git blame failed: {proc.stderr.strip()[:300]}")
        current_line: "int | None" = None
        for line in proc.stdout.splitlines():
            m = _BLAME_HEADER_RE.match(line)
            if m:
                current_line = int(m.group(1))
                continue
            if line.startswith("author-time ") and current_line is not None:
                ages[current_line] = (now - int(line.split()[1])) / 86400.0
                current_line = None
    return ages


def _history_problem(repo_root: Path) -> Optional[str]:
    """Why ``git blame`` cannot give real ages here, or ``None``. A shallow clone attributes every line older than
    its boundary to the boundary commit, so every TODO looks as young as the newest commit and the gate passes."""
    try:
        proc = _git(repo_root, "rev-parse", "--is-shallow-repository")
    except OSError as exc:
        return f"{repo_root}: cannot run git ({exc}); comment ages are unknown, so nothing was checked."
    if proc.returncode != 0:
        return f"{repo_root}: not a git work tree ({proc.stderr.strip()[:200]}); comment ages are unknown, so nothing was checked."
    if proc.stdout.strip() == "true":
        return (
            f"{repo_root}: shallow clone. git blame dates every older line to the shallow boundary, so no TODO can "
            "look stale and this check would pass having checked nothing. Fetch full history (actions/checkout "
            "`fetch-depth: 0`, or `git fetch --unshallow`)."
        )
    return None


def _candidates(path: Path, require_issue_ref: bool) -> dict[int, tuple[str, str]]:
    try:
        source = read_source(path)
    except SourceReadError:
        source = path.read_bytes().decode("utf-8", errors="replace")  # comment text only; a lossy decode cannot hide a marker
    file_lines = source.splitlines()
    out: dict[int, tuple[str, str]] = {}
    hash_lang = path.suffix in _HASH_SUFFIXES
    for lineno, comment in sorted(_comments(path, source, file_lines).items()):
        line = file_lines[lineno - 1] if lineno - 1 < len(file_lines) else comment
        todo = _todo(comment)
        if todo:
            if require_issue_ref and _has_issue_ref(todo):
                continue
            out[lineno] = ("TODO", line.strip()[:100])
            continue
        code_re = _COMMENTED_HASH_CALL_RE if hash_lang else _COMMENTED_CODE_RE
        if (
            code_re.match(line)
            and _body_is_code(_comment_body(line) or "", python=path.suffix == ".py")
            and not _block_reads_as_prose(file_lines, lineno - 1)
        ):
            out[lineno] = ("commented-out code", line.strip()[:100])
    return out


def find_stale_comments(
    repo_root: Path,
    scan_dirs: Iterable[str],
    *,
    max_age_days: int = 30,
    suffixes: Sequence[str] = _DEFAULT_SUFFIXES,
    require_issue_ref: bool = True,
    skip_dirs: Iterable[str] = DEFAULT_SKIP_DIRS,
) -> list[str]:
    """Return one problem string per TODO/commented-out call older than ``max_age_days``.

    A TODO carrying an issue reference right after its marker (``TODO(#12)``, ``TODO(topic)``, ``TODO: #12``,
    ``TODO ABC-12``, a URL) is exempt when ``require_issue_ref`` is true: it is a tracked promise rather than a
    note to nobody. Trailing comments count. Only tracked files are scanned (an untracked file has no age).

    The gate reports, rather than passes, whatever stops it from dating a line: a scan directory that does not
    exist, a root that is not a git work tree, a shallow clone, and a ``git blame`` that fails.
    """
    problems: list[str] = []
    root = Path(repo_root)
    history = _history_problem(root)
    if history is not None:
        return [history]
    patterns = tuple(f"*{s}" for s in suffixes)
    for d in scan_dirs:
        base = root / d
        if not base.is_dir():
            problems.append(f"{d}: scan directory does not exist under {root}; nothing in it was checked. Fix scan_dirs.")
            continue
        try:
            files = iter_files(base, patterns, exclude=frozenset(skip_dirs), include_untracked=False, use_git=True)
        except CorpusError as exc:
            problems.append(f"{d}: {exc}")
            continue
        for path in files:
            rel = path.relative_to(root).as_posix()
            candidates = _candidates(path, require_issue_ref)
            if not candidates:
                continue
            try:
                ages = _blame_ages(root, rel, sorted(candidates))
            except BlameError as exc:
                problems.append(f"{exc}; its {len(candidates)} candidate comment(s) could not be dated.")
                continue
            for lineno, (kind, text) in sorted(candidates.items()):
                age = ages.get(lineno)
                if age is None:
                    problems.append(f"{rel}:{lineno}: git blame returned no date for this line - `{text}`.")
                    continue
                if age <= max_age_days:
                    continue
                problems.append(f"{rel}:{lineno}: {kind} {int(age)} days old - `{text}`. Do it, delete it, or " f"reference the issue that tracks it.")
    return problems


def assert_no_stale_todos(
    repo_root: Path,
    scan_dirs: Iterable[str],
    *,
    max_age_days: int = 30,
    require_issue_ref: bool = True,
    suffixes: Sequence[str] = _DEFAULT_SUFFIXES,
    skip_dirs: Iterable[str] = DEFAULT_SKIP_DIRS,
) -> None:
    """Fail on any TODO or commented-out call older than ``max_age_days``, and on anything that kept a line from
    being dated (see :func:`find_stale_comments`)."""
    import pytest

    problems = find_stale_comments(repo_root, scan_dirs, max_age_days=max_age_days, require_issue_ref=require_issue_ref, suffixes=suffixes, skip_dirs=skip_dirs)
    if problems:
        pytest.fail(f"{len(problems)} stale comment(s) older than {max_age_days} days:\n  " + "\n  ".join(problems))
