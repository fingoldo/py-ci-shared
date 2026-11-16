"""An append-only data file may gain rows, never lose them: no first-parent commit may shrink one (audit 50-T12).

autopsia commit ``62721781`` overwrote a 2849-row ``article_verdicts_checkpoint.jsonl.gz`` with a stale 1542-row copy
from another session, in one plain commit (paid LLM answers, lost). ``merge_gz_jsonl_union --verify`` compares a MERGE
with its two parents and cannot see a plain commit; this gate walks the first-parent history of a revision and compares
every changed append-only file with its first parent.

A row is a line; for ``.gz`` the lines after gunzip. A commit whose message carries the trailer
``Append-Only-Shrink: <reason>`` (non-empty) waives its shrinks. A file deleted on the chain counts as shrunk to 0 rows,
and a ``.gz`` that cannot be decompressed in a commit is reported (its rows cannot be compared).

Git runs only through ``_core.git``. Blobs are read in one ``cat-file --batch`` per commit.
"""

from __future__ import annotations

import gzip
from collections.abc import Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import Finding
from ._core.git import git_output, git_text, read_blobs, run_git
from .commit_metadata import parse_trailers, read_commits

__all__ = ["DEFAULT_PATHS", "RULE", "TRAILER", "assert_append_only_log_shrink", "find_append_only_log_shrinks"]

RULE = "append-only-log-shrink"
TRAILER = "Append-Only-Shrink"
DEFAULT_PATHS: tuple[str, ...] = ("*.jsonl", "*.jsonl.gz")

PathLike = Union[str, Path]


def _rows(data: bytes, path: str) -> Optional[int]:
    """Lines of *data* (gunzipped for ``.gz``), or None when a gzip stream does not decompress."""
    if path.endswith(".gz"):
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError, ValueError):  # best-effort: the caller reports an unreadable side as a finding, never as a pass
            return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _commits(repo: Path, rev: str, paths: Sequence[str], max_commits: Optional[int]) -> list[tuple[str, str]]:
    """``(commit, first parent)`` for every first-parent commit that changes a path in *paths*, newest first."""
    args = ["log", "--first-parent", "--format=%H %P"]
    if max_commits is not None:
        args.append(f"--max-count={max_commits}")
    out = git_output(repo, *args, rev, "--", *paths)
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def _changed(repo: Path, parent: str, commit: str, paths: Sequence[str]) -> list[str]:
    out = git_text(run_git(repo, "diff", "--name-only", "--no-renames", "-z", parent, commit, "--", *paths, check=True).stdout)
    return [name for name in out.split("\0") if name]


def _waived(repo: Path, commit: str) -> bool:
    for c in read_commits(repo, f"{commit}^!"):
        if any(key.lower() == TRAILER.lower() and value.strip() for key, value in parse_trailers(c.message)):
            return True
    return False


def _subject(repo: Path, commit: str) -> str:
    for c in read_commits(repo, f"{commit}^!"):
        return c.message.strip().split("\n", 1)[0][:80]
    return ""


def find_append_only_log_shrinks(
    repo_root: PathLike, *, rev: str = "HEAD", paths: Sequence[str] = DEFAULT_PATHS, max_commits: Optional[int] = None
) -> list[Finding]:
    """Every first-parent commit of *rev* that leaves a file matching *paths* with fewer rows than its first parent.

    Raises ``CoreError`` (``GitError``) when *rev* does not resolve: an unknown revision is not a clean history."""
    repo = Path(repo_root)
    out: list[Finding] = []
    for commit, parent in _commits(repo, rev, paths, max_commits):
        if not parent:
            continue
        names = _changed(repo, parent, commit, paths)
        if not names:
            continue
        blobs = read_blobs(repo, [f"{parent}:{name}" for name in names] + [f"{commit}:{name}" for name in names])
        before, after = blobs[: len(names)], blobs[len(names) :]
        for name, old, new in zip(names, before, after):
            if old is None:
                continue
            rows_old = _rows(old, name)
            if rows_old is None:
                out.append(Finding(name, 1, RULE, f"{name} cannot be read (not gzip) in commit {commit[:12]}; its rows cannot be compared"))
                continue
            rows_new = 0 if new is None else _rows(new, name)
            if rows_new is None:
                out.append(Finding(name, 1, RULE, f"{name} cannot be read (not gzip) in commit {commit[:12]}; its rows cannot be compared"))
                continue
            if rows_new >= rows_old or _waived(repo, commit):
                continue
            out.append(
                Finding(
                    name,
                    1,
                    RULE,
                    f"{name} shrank from {rows_old} to {rows_new} rows in commit '{_subject(repo, commit)}'; append-only data must not lose rows "
                    f"(waive with a `{TRAILER}: <reason>` trailer in the commit message)",
                )
            )
    return out


def assert_append_only_log_shrink(repo_root: PathLike, *, rev: str = "HEAD", paths: Sequence[str] = DEFAULT_PATHS, max_commits: Optional[int] = None) -> None:
    """Fail on any shrink :func:`find_append_only_log_shrinks` reports."""
    found = find_append_only_log_shrinks(repo_root, rev=rev, paths=paths, max_commits=max_commits)
    if found:
        raise AssertionError(f"{len(found)} append-only file shrink(s) in the history of {rev}:\n  " + "\n  ".join(f.render() for f in found))
