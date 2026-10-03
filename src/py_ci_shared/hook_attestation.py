"""Commits that never went through the local hooks: a ``commit-msg`` trailer, and a check over the pushed range.

The most frequent cause of "CI red on master" in September 2026 was a commit that skipped the pre-commit hooks:
mlframe 58715253b (another session's 10 commits never ran the gates: ruff, black x12, vulture x11, mypy, uv.lock),
autopsia 70c28afa ("the meta-test debt the --no-verify pushes left"), llm_bench 57e79a7 (a LOC overrun and a
sibling-floor skew, both of which its pre-commit meta tests would have stopped), mlframe 7a8fd1d15 (a ``SKIP=``).
``hook_hygiene`` checks that a hook is honest and ``safe_precommit`` that it survives concurrent sessions; nothing
noticed that a pushed commit never met them.

Two halves:

1. ``python -m py_ci_shared.hook_attestation install`` writes a ``commit-msg`` hook that appends the trailer
   ``Hooks-Verified: <first 12 hex of sha256(staged .pre-commit-config.yaml)>[; skipped=<ids from $SKIP>]``.
   git runs ``commit-msg`` only after ``pre-commit`` passed, and ``git commit --no-verify`` (``-n``) skips BOTH hooks,
   so a bypassed commit carries no trailer: its absence is the evidence, no other logic needed. The hook adds nothing
   when no ``pre-commit`` hook is installed, since then nothing was verified.
2. ``python -m py_ci_shared.hook_attestation check --range "$BEFORE..$AFTER"`` in a push-triggered job lists every
   non-merge commit in the range with no trailer, a trailer whose hash differs from the commit's own
   ``.pre-commit-config.yaml``, or a non-empty ``skipped=``. Bot authors (``*[bot]``) and ``--allow-author`` names are
   left out, and so is a commit whose tree has no ``.pre-commit-config.yaml`` (nothing to attest). ``--warn-only``
   prints GitHub ``::warning::`` lines and exits 0; without it a finding exits 1.

Known noise, which IS the signal: a commit made on a machine without the hook, a GitHub web-UI edit, a squash merge
that rewrote the message. Adopt warn-only first and count the flags against the "fix CI red" commits that follow.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from ._core import CorpusError, Finding
from ._core.git import GitError, git_text, run_git

__all__ = [
    "BOT_AUTHORS",
    "CONFIG",
    "RULE_SKIPPED",
    "RULE_STALE",
    "RULE_UNVERIFIED",
    "TRAILER",
    "Commit",
    "assert_hook_attestation",
    "attest",
    "commits_in",
    "config_hash",
    "find_hook_attestation",
    "hook_script",
    "install",
    "main",
    "stamp_message",
    "trailer_value",
]

TRAILER = "Hooks-Verified"
CONFIG = ".pre-commit-config.yaml"
RULE_UNVERIFIED = "hook-unverified"
RULE_STALE = "hook-stale-config"
RULE_SKIPPED = "hook-skipped"
BOT_AUTHORS = ("dependabot[bot]", "github-actions[bot]")
_MARKER = "# py_ci_shared.hook_attestation commit-msg hook"
_ZERO = re.compile(r"^0{40}$|^0{64}$")

PathLike = Union[str, Path]


def config_hash(data: bytes) -> str:
    """The first 12 hex digits of the sha256 of a pre-commit config's bytes."""
    return hashlib.sha256(data).hexdigest()[:12]


def trailer_value(data: bytes, skip: str = "") -> str:
    """``<hash>`` or ``<hash>; skipped=<ids>`` for the config bytes *data* and a ``$SKIP`` value."""
    ids = ",".join(sorted({s.strip() for s in skip.split(",") if s.strip()}))
    return config_hash(data) + (f"; skipped={ids}" if ids else "")


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    subject: str
    trailers: tuple[str, ...]  # every Hooks-Verified value the message carries


def attest(commits: Iterable[Commit], expected_hash: Callable[[str], Optional[str]], *, allow_authors: Iterable[str] = BOT_AUTHORS) -> list[Finding]:
    """The findings for *commits*. *expected_hash* maps a sha to the hash of its config, None when it has none."""
    allowed = set(allow_authors)
    out: list[Finding] = []
    for c in commits:
        if c.author in allowed or c.author.endswith("[bot]"):
            continue
        expected = expected_hash(c.sha)
        if expected is None:
            continue
        where, subject = c.sha[:12], c.subject[:80]
        if not c.trailers:
            message = f"{subject!r} has no {TRAILER} trailer: it was committed with --no-verify, or without the hooks installed"
            out.append(Finding(where, 1, RULE_UNVERIFIED, message))
            continue
        value = c.trailers[-1]
        stamped, _, rest = value.partition(";")
        if stamped.strip() != expected:
            message = f"{subject!r} was verified against config {stamped.strip()}, but it commits config {expected}: the hooks it met are not its own"
            out.append(Finding(where, 1, RULE_STALE, message))
        skipped = rest.strip()[len("skipped=") :] if rest.strip().startswith("skipped=") else ""
        if skipped:
            out.append(Finding(where, 1, RULE_SKIPPED, f"{subject!r} skipped hooks {skipped} (SKIP=)"))
    return out


def _git(repo: Path, *args: str) -> bytes:
    try:
        proc = run_git(repo, *args)
    except GitError as exc:
        raise CorpusError(f"cannot run git in {repo}: {exc}") from exc
    if proc.returncode != 0:
        raise CorpusError(f"git {' '.join(args[:2])} failed in {repo}: {git_text(proc.stderr).strip()}")
    return proc.stdout


def commits_in(repo: PathLike, rev_range: str) -> list[Commit]:
    """Non-merge commits of *rev_range*, newest first. A range from the all-zero sha (a new branch) checks its tip."""
    base, sep, tip = rev_range.partition("..")
    if sep and _ZERO.match(base):
        rev_args = [f"{tip}^!"]
    else:
        rev_args = [rev_range]
    fmt = f"%H%x00%an%x00%s%x00%(trailers:key={TRAILER},valueonly,separator=%x01)%x1e"
    raw = _git(Path(repo), "log", "--no-merges", f"--format={fmt}", *rev_args).decode("utf-8", "replace")
    out = []
    for record in raw.split("\x1e"):
        parts = record.strip("\n").split("\x00")
        if len(parts) != 4:
            continue
        sha, author, subject, trailers = parts
        out.append(Commit(sha, author, subject, tuple(t.strip() for t in trailers.split("\x01") if t.strip())))
    return out


def _hash_at(repo: Path) -> Callable[[str], Optional[str]]:
    def expected(sha: str) -> Optional[str]:
        try:
            proc = run_git(repo, "show", f"{sha}:{CONFIG}")
        except GitError:
            return None
        return config_hash(proc.stdout) if proc.returncode == 0 else None

    return expected


def find_hook_attestation(repo_root: PathLike, rev_range: str, *, allow_authors: Iterable[str] = BOT_AUTHORS) -> list[Finding]:
    """Every commit of *rev_range* in the repo at *repo_root* that does not attest a clean run of its own hooks."""
    repo = Path(repo_root)
    return attest(commits_in(repo, rev_range), _hash_at(repo), allow_authors=allow_authors)


def assert_hook_attestation(repo_root: PathLike, rev_range: str, *, allow_authors: Iterable[str] = BOT_AUTHORS) -> None:
    """Fail when a commit of *rev_range* bypassed, skipped or out-ran its pre-commit hooks."""
    found = find_hook_attestation(repo_root, rev_range, allow_authors=allow_authors)
    if found:
        raise AssertionError(
            f"{len(found)} commit(s) did not go through their pre-commit hooks; run the hooks on them "
            f"(pre-commit run --from-ref/--to-ref) and fix what they report:\n  " + "\n  ".join(f.render() for f in found)
        )


# --- the commit-msg side ----------------------------------------------------------------------------------------------


def _hooks_dir(repo: Path) -> Path:
    path = Path(_git(repo, "rev-parse", "--git-path", "hooks").decode().strip())
    return path if path.is_absolute() else repo / path


def _staged_config(repo: Path) -> Optional[bytes]:
    proc = run_git(repo, "cat-file", "blob", f":{CONFIG}")
    if proc.returncode == 0:
        return proc.stdout
    on_disk = repo / CONFIG
    return on_disk.read_bytes() if on_disk.is_file() else None


def stamp_message(message_file: PathLike, repo: PathLike = ".") -> Optional[str]:
    """Append (or replace) the trailer in *message_file*; the value written, or None when there is nothing to attest
    (no pre-commit config, or no ``pre-commit`` hook installed so nothing ran)."""
    root = Path(repo)
    data = _staged_config(root)
    if data is None or not (_hooks_dir(root) / "pre-commit").is_file():
        return None
    value = trailer_value(data, os.environ.get("SKIP", ""))
    _git(root, "interpret-trailers", "--in-place", "--if-exists", "replace", "--trailer", f"{TRAILER}: {value}", str(message_file))
    return value


def hook_script(python: str) -> str:
    """The ``commit-msg`` hook text: run this module with the interpreter that installed it."""
    return f'#!/bin/sh\n{_MARKER}\nexec "{python}" -m py_ci_shared.hook_attestation commit-msg "$1"\n'


def install(repo: PathLike = ".", *, python: Optional[str] = None) -> Path:
    """Write the ``commit-msg`` hook (idempotent). An existing ``commit-msg`` hook that is not this one is left alone and
    raises ``CorpusError``: chaining someone else's hook is a decision for its owner."""
    path = _hooks_dir(Path(repo)) / "commit-msg"
    text = hook_script((python or sys.executable).replace("\\", "/"))
    if path.is_file() and _MARKER not in path.read_text(encoding="utf-8", errors="replace"):
        raise CorpusError(f'{path} exists and is not the hook_attestation hook; call `python -m py_ci_shared.hook_attestation commit-msg "$1"` from it')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    path.chmod(0o755)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``install`` | ``commit-msg <file>`` | ``check --range A..B [--warn-only] [--allow-author NAME]``."""
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.hook_attestation", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_install = sub.add_parser("install", help="write the commit-msg hook into this clone")
    p_install.add_argument("--repo", default=".")
    p_msg = sub.add_parser("commit-msg", help="(the hook) stamp the trailer into a commit message file")
    p_msg.add_argument("message_file")
    p_check = sub.add_parser("check", help="list the commits of a range that did not go through their hooks")
    p_check.add_argument("--range", required=True, dest="rev_range", help="BEFORE..AFTER (an all-zero BEFORE checks AFTER alone)")
    p_check.add_argument("--repo", default=".")
    p_check.add_argument("--warn-only", action="store_true", help="print ::warning:: lines and exit 0")
    p_check.add_argument("--allow-author", action="append", default=[], help="an author name never flagged (bots always are not)")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            sys.stdout.write(f"installed {install(args.repo)}\n")
            return 0
        if args.command == "commit-msg":
            stamp_message(args.message_file)
            return 0  # a commit-msg hook that fails would block the commit it is only meant to label
        found = find_hook_attestation(args.repo, args.rev_range, allow_authors=[*BOT_AUTHORS, *args.allow_author])
    except CorpusError as exc:
        sys.stderr.write(f"hook_attestation: {exc}\n")
        return 0 if getattr(args, "command", "") == "commit-msg" else 2
    for f in found:
        line = f.render()
        sys.stdout.write((f"::warning title=hook attestation::{line}" if args.warn_only else line) + "\n")
    sys.stdout.write(f"hook_attestation: {len(found)} commit(s) without a clean hook run in {args.rev_range}\n")
    return 0 if args.warn_only or not found else 1


if __name__ == "__main__":
    raise SystemExit(main())
