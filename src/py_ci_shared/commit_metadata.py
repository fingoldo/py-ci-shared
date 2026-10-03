"""Commit metadata policy: no subject that starts with a BOM or another invisible character, and no forbidden trailer.

pyutilz 086771d's subject begins with U+FEFF: a PowerShell 5 ``Set-Content -Encoding utf8`` wrote the message file
with a BOM and ``git commit -F`` kept it, so every ``git log --oneline`` and every grep for the subject misses it.
Trailers are policy: a repo owner may forbid ``Co-Authored-By`` (the agent harness adds it by default) or any other
key. The gate enforces the list the repo configures; the default list is EMPTY, so it decides nothing on its own.

Checks, per commit message:

* ``bom-subject``: the subject starts with U+FEFF, or contains one anywhere;
* ``invisible-subject``: the subject starts with a control, format (zero-width), private-use or whitespace character;
* ``forbidden-trailer``: a trailer whose key (case-insensitive) is in *forbidden_trailers*. An entry may carry a value
  glob, ``"Co-Authored-By: *Claude*"``, to forbid only matching values.

Usable three ways:

* a ``commit-msg`` hook (pre-commit ``stages: [commit-msg]``, it passes the message file)::

      python -m py_ci_shared.commit_metadata --message-file "$1" --forbid Co-Authored-By

* a CI step over the pushed range: ``python -m py_ci_shared.commit_metadata --range "$BEFORE..$AFTER"``;
* a gate: ``[tool.py_ci_shared.gates.commit_metadata]`` with ``forbidden_trailers = [...]`` (and optionally
  ``rev_range``); the CLI reads the same table, so ``--forbid`` is only needed without one. Merge commits and bot authors
  (``*[bot]``, a literal suffix) are skipped.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._core import CoreError, Finding, read_source
from ._toml_compat import tomllib
from ._core.git import git_output, run_git

__all__ = [
    "DEFAULT_SKIP_AUTHORS",
    "Commit",
    "assert_commit_metadata",
    "check_message",
    "find_commit_metadata_problems",
    "main",
    "parse_trailers",
    "read_commits",
]

DEFAULT_SKIP_AUTHORS = ("*[bot]",)
_FS, _RS = "\x1f", "\x1e"
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cn", "Zl", "Zp", "Zs"})

PathLike = Union[str, Path]


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    message: str
    parents: tuple[str, ...] = field(default=())

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0]


def read_commits(repo: PathLike, rev_range: str, *, paths: Sequence[str] = ()) -> list[Commit]:
    """Every commit in *rev_range* (oldest first), with its raw message. CoreError when git fails (a bad range is not empty)."""
    out = git_output(Path(repo), "log", "--reverse", f"--format=%H{_FS}%P{_FS}%an{_FS}%B{_RS}", rev_range, "--", *paths)
    commits = []
    for record in out.split(_RS):
        record = record.lstrip("\n")
        if not record:
            continue
        sha, parents, author, message = record.split(_FS, 3)
        commits.append(Commit(sha, author, message.rstrip("\n"), tuple(parents.split())))
    return commits


def parse_trailers(message: str) -> list[tuple[str, str]]:
    """``(key, value)`` pairs of the message's trailer block: its last paragraph, when every line there is ``Key: value``
    (continuation lines indented); none for a one-paragraph message, as in ``git interpret-trailers``."""
    paragraphs = [p for p in message.strip("\n").split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        return []
    out: list[tuple[str, str]] = []
    for line in paragraphs[-1].split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] = (out[-1][0], out[-1][1] + " " + line.strip())
            continue
        key, sep, value = line.partition(":")
        if not sep or not key or any(ch.isspace() for ch in key):
            return []
        out.append((key, value.strip()))
    return out


def _forbidden(key: str, value: str, forbidden: Iterable[str]) -> Optional[str]:
    for entry in forbidden:
        fkey, sep, pattern = entry.partition(":")
        if fkey.strip().lower() == key.lower() and (not sep or fnmatch.fnmatch(value.lower(), pattern.strip().lower())):
            return entry
    return None


def check_message(message: str, *, forbidden_trailers: Iterable[str] = ()) -> list[tuple[str, str]]:
    """``(rule, detail)`` for every policy breach in one commit message."""
    problems: list[tuple[str, str]] = []
    subject = message.split("\n", 1)[0]
    if "﻿" in subject:
        problems.append(("bom-subject", "the subject carries a byte-order mark (U+FEFF); rewrite the message file without a BOM"))
    elif subject and unicodedata.category(subject[0]) in _INVISIBLE_CATEGORIES:
        problems.append(("invisible-subject", f"the subject starts with the invisible character U+{ord(subject[0]):04X}"))
    forbidden = list(forbidden_trailers)
    for key, value in parse_trailers(message) if forbidden else []:
        entry = _forbidden(key, value, forbidden)
        if entry is not None:
            problems.append(("forbidden-trailer", f"trailer `{key}: {value}` is forbidden here ({entry!r})"))
    return problems


def _author_matches(author: str, entry: str) -> bool:
    """*entry* is an exact author name, or ``*<suffix>``: ``*[bot]`` is every bot account (a literal suffix, not a glob, so
    ``[bot]`` is not a character class that would match every name ending in b, o or t)."""
    return author.endswith(entry[1:]) if entry.startswith("*") else author == entry


def find_commit_metadata_problems(
    repo: PathLike,
    rev_range: str,
    *,
    forbidden_trailers: Iterable[str] = (),
    skip_authors: Iterable[str] = DEFAULT_SKIP_AUTHORS,
    include_merges: bool = False,
) -> list[Finding]:
    """One finding per breach per commit in *rev_range*; ``path`` is the short sha and ``line`` is 1."""
    forbidden, skip = list(forbidden_trailers), list(skip_authors)
    out: list[Finding] = []
    for commit in read_commits(repo, rev_range):
        if (len(commit.parents) > 1 and not include_merges) or any(_author_matches(commit.author, s) for s in skip):
            continue
        for rule, detail in check_message(commit.message, forbidden_trailers=forbidden):
            out.append(Finding(commit.sha[:12], 1, rule, f"{detail} -- subject {commit.subject.lstrip(chr(0xFEFF))[:80]!r}"))
    return out


def _default_range(repo: Path) -> str:
    """``@{upstream}..HEAD`` when the branch tracks one, else just ``HEAD``'s own commit."""
    proc = run_git(repo, "rev-parse", "--verify", "-q", "@{upstream}")
    return "@{upstream}..HEAD" if proc.returncode == 0 else "HEAD^!"


def assert_commit_metadata(
    repo_root: PathLike,
    *,
    rev_range: Optional[str] = None,
    forbidden_trailers: Iterable[str] = (),
    skip_authors: Iterable[str] = DEFAULT_SKIP_AUTHORS,
    include_merges: bool = False,
) -> None:
    """Fail on any breach in *rev_range* (default: the commits ahead of the upstream branch, else ``HEAD`` alone)."""
    repo = Path(repo_root)
    found = find_commit_metadata_problems(
        repo, rev_range or _default_range(repo), forbidden_trailers=forbidden_trailers, skip_authors=skip_authors, include_merges=include_merges
    )
    if found:
        raise AssertionError(
            f"{len(found)} commit metadata problem(s); reword the commit (git commit --amend / rebase) before pushing:\n  "
            + "\n  ".join(f.render() for f in found)
        )


def _configured(repo: Path) -> dict[str, Any]:
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    data = tomllib.loads(read_source(pyproject))
    table = data.get("tool", {}).get("py_ci_shared", {}).get("gates", {}).get("commit_metadata", {})
    return table if isinstance(table, dict) else {}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.commit_metadata", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repo", default=".", help="repository root (default: .)")
    what = parser.add_mutually_exclusive_group()
    what.add_argument("--message-file", help="check one message file (the commit-msg hook argument)")
    what.add_argument("--range", dest="rev_range", help="check every commit in this revision range, e.g. BEFORE..AFTER")
    parser.add_argument("--forbid", action="append", default=None, help="forbidden trailer key, or 'Key: <value glob>' (repeatable)")
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    try:
        config = _configured(repo)
        forbidden = args.forbid if args.forbid is not None else [str(x) for x in config.get("forbidden_trailers", [])]
        if args.message_file:
            text = Path(args.message_file).read_bytes().decode("utf-8", "replace")
            body = "\n".join(line for line in text.split("\n") if not line.startswith("#"))  # git strips comment lines
            problems = [Finding(args.message_file, 1, rule, detail) for rule, detail in check_message(body, forbidden_trailers=forbidden)]
        else:
            rev_range = args.rev_range or config.get("rev_range") or _default_range(repo)
            problems = find_commit_metadata_problems(
                repo, rev_range, forbidden_trailers=forbidden, skip_authors=config.get("skip_authors", DEFAULT_SKIP_AUTHORS)
            )
    except (CoreError, tomllib.TOMLDecodeError, OSError) as exc:
        sys.stderr.write(f"commit_metadata: {exc}" + "\n")
        return 1
    for f in problems:
        sys.stdout.write(f.render() + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
