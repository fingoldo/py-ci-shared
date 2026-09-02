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
and cannot be gamed by re-indenting. An issue reference (``TODO(#123)``, ``TODO(sourcemaps)``, a URL)
exempts a line -- that is a tracked promise, which is the outcome this check wants.

Deliberately dependency-free (``git blame --line-porcelain`` via subprocess), language-agnostic:
``//``, ``#``, ``--`` and ``/*`` comment markers are all recognised.

Usage::

    from py_ci_shared.stale_comment_age import assert_no_stale_todos

    def test_no_todo_older_than_a_release():
        assert_no_stale_todos(REPO, ["lib", "tool"], max_age_days=30)
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

_COMMENT_PREFIX = r"(?://+|#|--|\*|/\*)"
_TODO_RE = re.compile(rf"^\s*{_COMMENT_PREFIX}\s*(TODO|FIXME|HACK|XXX)\b(?P<ref>\([^)]*\))?", re.IGNORECASE)
# A commented-out statement: a comment whose content is a call or an assignment ending in `;` or `,`.
_COMMENTED_CODE_RE = re.compile(rf"^\s*(?://+|#)\s*(?!TODO|FIXME|HACK|XXX)[\w.]+\s*\([^;]*\)\s*[;,]\s*$", re.IGNORECASE)
_ISSUE_REF_RE = re.compile(r"#\d+|https?://|\b[A-Z]{2,}-\d+\b|\(\w[\w-]*\)")
_DEFAULT_SUFFIXES = (".dart", ".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".sql", ".yaml", ".yml")


def _blame_ages(repo_root: Path, rel_path: str, lines: Sequence[int]) -> dict[int, float]:
    """Return ``{line_number: age_in_days}`` for ``lines`` of ``rel_path``."""
    if not lines:
        return {}
    args = ["git", "blame", "--line-porcelain"]
    for n in lines:
        args += ["-L", f"{n},{n}"]
    args += ["--", rel_path]
    try:
        out = subprocess.run(args, cwd=repo_root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {}
    ages: dict[int, float] = {}
    now = time.time()
    current_line: "int | None" = None
    for line in out.splitlines():
        m = re.match(r"^[0-9a-f]{7,40}\s+\d+\s+(\d+)", line)
        if m:
            current_line = int(m.group(1))
            continue
        if line.startswith("author-time ") and current_line is not None:
            ages[current_line] = (now - int(line.split()[1])) / 86400.0
            current_line = None
    return ages


def find_stale_comments(
    repo_root: Path,
    scan_dirs: Iterable[str],
    *,
    max_age_days: int = 30,
    suffixes: Sequence[str] = _DEFAULT_SUFFIXES,
    require_issue_ref: bool = True,
) -> list[str]:
    """Return one problem string per TODO/commented-out call older than ``max_age_days``.

    A TODO carrying an issue reference, a URL or a parenthesised topic is exempt when
    ``require_issue_ref`` is true: it is a tracked promise rather than a note to nobody.
    """
    problems: list[str] = []
    for d in scan_dirs:
        base = repo_root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            rel = path.relative_to(repo_root).as_posix()
            if "/.dart_tool/" in f"/{rel}" or "/node_modules/" in f"/{rel}":
                continue
            candidates: dict[int, str] = {}
            for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                todo = _TODO_RE.match(line)
                if todo:
                    if require_issue_ref and _ISSUE_REF_RE.search(line):
                        continue
                    candidates[i] = line.strip()[:100]
                elif _COMMENTED_CODE_RE.match(line):
                    candidates[i] = line.strip()[:100]
            if not candidates:
                continue
            ages = _blame_ages(repo_root, rel, sorted(candidates))
            for lineno, text in sorted(candidates.items()):
                age = ages.get(lineno)
                if age is None or age <= max_age_days:
                    continue
                kind = "TODO" if _TODO_RE.match(text) else "commented-out code"
                problems.append(f"{rel}:{lineno}: {kind} {int(age)} days old - `{text}`. Do it, delete it, or " f"reference the issue that tracks it.")
    return problems


def assert_no_stale_todos(
    repo_root: Path,
    scan_dirs: Iterable[str],
    *,
    max_age_days: int = 30,
    require_issue_ref: bool = True,
) -> None:
    """Fail on any TODO or commented-out call older than ``max_age_days``."""
    import pytest

    problems = find_stale_comments(repo_root, scan_dirs, max_age_days=max_age_days, require_issue_ref=require_issue_ref)
    if problems:
        pytest.fail(f"{len(problems)} stale comment(s) older than {max_age_days} days:\n  " + "\n  ".join(problems))
