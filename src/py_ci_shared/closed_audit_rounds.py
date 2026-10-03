"""A closed audit round is append-only: its files under ``audits/implemented/`` may gain lines, never lose or change one.

A bulk rewrite whose skip list named ``audits`` but not ``_audits`` rewrote five closed reports, and nothing noticed:
``audit_round_format`` checks that rounds are countable and that a filed round has no open row, and
``audit_disposition_parity`` that a RESOLVED disposition names real artefacts. Neither sees that the TEXT of a finding
in a closed round changed after it was closed, which is exactly what makes a record worthless.

Over a revision range (``merge-base(base, head)..head``) every file that existed under *closed_dir* at the merge base
may only have lines ADDED (new dispositions appended). Reported: a deleted or rewritten line (one finding per hunk,
with its line at the base), a deleted closed file, and a closed file moved out of *closed_dir*. Moving an OPEN round
into *closed_dir* is not an edit of a closed file: only files closed at the base are frozen. Line-ending-only changes
are ignored, and so are the lines a closed round keeps current by design: a ``Disposition`` line (revised when a fix
is reverted or re-done) and a row of a ``TRACKER*.md`` table (its status cell moves). Measured on the five local
consumers over 2026-09: without that exemption 9 of 9 flagged commits were disposition or status updates.

An intended edit (a typo that changes meaning, a broken link) is declared, not hidden: a commit in the range that
touches the file and carries the trailer ``Audit-Edit: <reason>`` exempts that file.

CI step (push or pull request)::

    python -m py_ci_shared.closed_audit_rounds --base "${{ github.event.before }}"   # or origin/master on a PR
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import CoreError, Finding
from ._core.git import git_output
from .commit_metadata import parse_trailers, read_commits

__all__ = ["DEFAULT_CLOSED_DIR", "DEFAULT_TRAILER", "RULE", "assert_closed_audit_rounds_append_only", "find_closed_round_edits", "main"]

RULE = "closed-audit-round-edit"
DEFAULT_CLOSED_DIR = "audits/implemented"
DEFAULT_TRAILER = "Audit-Edit"
_DISPOSITION = re.compile(r"^\s*(?:[-*]\s*)?\**Disposition\**\s*:?", re.IGNORECASE)
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")

PathLike = Union[str, Path]


def _under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _changed(repo: Path, base: str, head: str) -> list[tuple[str, str, Optional[str]]]:
    """``(status letter, old path, new path or None)`` for every file changed between *base* and *head*, renames found."""
    out = git_output(repo, "diff", "--name-status", "-z", "--find-renames", "--no-ext-diff", base, head)
    fields = out.split("\0")
    rows: list[tuple[str, str, Optional[str]]] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            rows.append((status[0], fields[i + 1], fields[i + 2]))
            i += 3
        else:
            rows.append((status[0], fields[i + 1], None))
            i += 2
    return rows


def _removed_hunks(repo: Path, base: str, head: str, old: str, new: str) -> list[tuple[int, int, str]]:
    """``(first line at base, count, first removed text)`` for every hunk that removes lines from *old*."""
    paths = [old] if old == new else [old, new]
    diff = git_output(repo, "diff", "-U0", "--no-color", "--no-ext-diff", "--find-renames", "--ignore-cr-at-eol", base, head, "--", *paths)
    hunks: list[tuple[int, int, str]] = []
    lines = diff.split("\n")
    for i, line in enumerate(lines):
        m = _HUNK.match(line)
        if not m:
            continue
        start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        if not count:
            continue
        removed = []
        for x in lines[i + 1 :]:
            if x.startswith("@@"):
                break
            if x.startswith("-"):
                removed.append(x[1:])
        if not all(_mutable(x, old) for x in removed):
            hunks.append((start, count, removed[0].strip() if removed else ""))
    return hunks


def _mutable(line: str, path: str) -> bool:
    """A line a closed round keeps up to date: a disposition, or a row of a tracker table (its status cell moves)."""
    if _DISPOSITION.search(line):
        return True
    return line.lstrip().startswith("|") and "tracker" in path.rsplit("/", 1)[-1].lower()


def _declared_edit(repo: Path, rev_range: str, paths: Sequence[str], trailer: str) -> bool:
    for commit in read_commits(repo, rev_range, paths=paths):
        if any(key.lower() == trailer.lower() and value.strip() for key, value in parse_trailers(commit.message)):
            return True
    return False


def find_closed_round_edits(
    repo_root: PathLike, base: str, *, head: str = "HEAD", closed_dir: str = DEFAULT_CLOSED_DIR, trailer: str = DEFAULT_TRAILER
) -> list[Finding]:
    """Every deletion or rewrite of a line of a file that was under *closed_dir* at ``merge-base(base, head)``.

    Raises ``CoreError`` (``GitError``) when *base* or *head* does not resolve: an unknown range is not a clean one."""
    repo = Path(repo_root)
    merge_base = git_output(repo, "merge-base", base, head).strip()
    rev_range = f"{merge_base}..{head}"
    out: list[Finding] = []
    for status, old, new in _changed(repo, merge_base, head):
        if not _under(old, closed_dir) or status in ("A", "C"):
            continue
        touched = [old] if new is None else [old, new]
        if _declared_edit(repo, rev_range, touched, trailer):
            continue
        if status == "D":
            out.append(Finding(old, 1, RULE, f"a closed audit file was deleted; closed rounds are append-only (or declare it with `{trailer}: <reason>`)"))
            continue
        if new is not None and not _under(new, closed_dir):
            out.append(Finding(old, 1, RULE, f"a closed audit file was moved out of {closed_dir}/ to {new}"))
        for start, count, text in _removed_hunks(repo, merge_base, head, old, new or old):
            span = f"line {start}" if count == 1 else f"lines {start}-{start + count - 1}"
            out.append(
                Finding(
                    old,
                    start,
                    RULE,
                    f"{span} of a closed round deleted or rewritten (was {text[:80]!r}); append instead, or declare it with `{trailer}: <reason>`",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_closed_audit_rounds_append_only(
    repo_root: PathLike, *, base: str, head: str = "HEAD", closed_dir: str = DEFAULT_CLOSED_DIR, trailer: str = DEFAULT_TRAILER
) -> None:
    """Fail on any edit :func:`find_closed_round_edits` reports."""
    found = find_closed_round_edits(repo_root, base, head=head, closed_dir=closed_dir, trailer=trailer)
    if found:
        raise AssertionError(f"{len(found)} edit(s) to closed audit rounds:\n  " + "\n  ".join(f.render() for f in found))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.closed_audit_rounds", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repo", default=".", help="repository root (default: .)")
    parser.add_argument("--base", required=True, help="the ref the range starts from (its merge base with --head)")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--closed-dir", default=DEFAULT_CLOSED_DIR)
    parser.add_argument("--trailer", default=DEFAULT_TRAILER)
    args = parser.parse_args(argv)
    try:
        found = find_closed_round_edits(args.repo, args.base, head=args.head, closed_dir=args.closed_dir, trailer=args.trailer)
    except CoreError as exc:
        sys.stderr.write(f"closed_audit_rounds: {exc}" + "\n")
        return 1
    for f in found:
        sys.stdout.write(f.render() + "\n")
    sys.stdout.write(f"closed_audit_rounds: {len(found)} edit(s) to files under {args.closed_dir}/ since {args.base}" + "\n")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
