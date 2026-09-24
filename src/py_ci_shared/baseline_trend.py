"""How each baselined rule's count has moved over time, read out of git history.

A ratchet only proves a rule is not getting WORSE. Whether it is getting better is a separate question,
and nothing was answering it: on 2026-09-03 one repository's accepted findings were counted by hand and
turned out to have gone from 506 to 271 in a day, while three rules had never moved at all since the day
they were adopted.

A rule whose count never falls is saying one of three things, and all three are worth knowing:

* the debt is real and nobody owns it - the ordinary case, and a decision waiting to be made;
* the rule reports something no one can act on, so every entry is noise wearing the costume of debt.
  Three of this toolkit's own Dart sweeps were in exactly this state for a month;
* the rule is satisfied and the entries are stale, which the ratchet's own prune check would catch on
  the next run - so this is the cheap way to notice nobody has run it.

Reads every shape a baseline is written in: ``{"accepted": {key: note}}`` (the Python ratchet),
``{"entries": [...]}`` or ``{"entries": {finding: note}}`` (the Dart sweeps), the ``_core.Baseline`` multiset
``{"entries": {key: {"count": n, "note": ..}}}`` (counts summed) and a bare ``{key: note}`` map whose keys read as
paths or finding keys. A baseline renamed in its history is followed across the rename, a rule counts as moved
when its count changed at any point (not only between the endpoints), and a git failure exits non-zero.

    python -m py_ci_shared.baseline_trend                     every baseline, default directories
    python -m py_ci_shared.baseline_trend --dir test/meta/baselines --since 2026-08-01
    python -m py_ci_shared.baseline_trend --unmoved            only rules that have not moved
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable, Sequence
from typing import Optional

DEFAULT_DIRECTORIES = ("tool/meta/baselines", "test/meta/baselines")

_KEY_CHARS = ("/", "\\", ".", ":")


class GitError(RuntimeError):
    """A git command the report depends on failed."""


def _weight(value: object) -> int:
    """How many accepted findings one mapping entry stands for: a ``{"count": n}`` record counts n, anything else 1."""
    if isinstance(value, dict) and isinstance(value.get("count"), int) and not isinstance(value.get("count"), bool):
        return max(int(value["count"]), 0)
    return 1


def _count_mapping(mapping: dict) -> int:
    return sum(_weight(v) for k, v in mapping.items() if not str(k).startswith("_"))


def count_entries(text: str) -> Optional[int]:
    """The number of accepted findings in a baseline file's text, or None when it is not one."""
    try:
        data = json.loads(text.lstrip("\ufeff"))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("accepted", "entries"):
        value = data.get(key)
        if isinstance(value, dict):
            return _count_mapping(value)
        if isinstance(value, list):
            return len(value)
    # `module_sizes.json` keeps its per-file ceilings under its own name.
    ceilings = data.get("ceilings")
    if isinstance(ceilings, dict):
        return len(ceilings)
    # A bare {key: note} baseline: counted when its keys read as paths or finding keys, so a stray config
    # file ({"something": "else"}) is still not reported as a rule.
    keys = [k for k in data if not k.startswith("_")]
    if keys and any(ch in k for k in keys for ch in _KEY_CHARS):
        return _count_mapping(data)
    return None


def _run_git(repo: str, *args: str) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise GitError(f"git {args[0]}: {exc}") from exc


def _git(repo: str, *args: str) -> str:
    """stdout of a git command; raises :class:`GitError` when it fails, so a broken repo is never an empty report."""
    proc = _run_git(repo, *args)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def baseline_files(repo: str, directories: Iterable[str]) -> list[str]:
    out: list[str] = []
    for directory in directories:
        listing = _git(repo, "ls-files", f"{directory}/*.json")
        out.extend(line for line in listing.splitlines() if line.strip())
    return sorted(out)


_COMMIT_MARK = "@@commit "


def _commits_with_paths(repo: str, path: str, since: "str | None") -> list[tuple[str, str, Optional[str]]]:
    """``(short sha, date, path at that commit or None when deleted there)``, newest first, following renames."""
    args = ["log", "--follow", "--name-status", f"--format={_COMMIT_MARK}%h %ad", "--date=short"]
    if since:
        args += [f"--since={since}"]
    args += ["--", path]
    out: list[tuple[str, str, Optional[str]]] = []
    for line in _git(repo, *args).splitlines():
        if line.startswith(_COMMIT_MARK):
            sha, date = line[len(_COMMIT_MARK) :].split(None, 1)
            out.append((sha, date.strip(), None))
        elif line.strip() and out and out[-1][2] is None:
            fields = line.split("\t")
            status = fields[0][:1]
            if status != "D":
                out[-1] = (out[-1][0], out[-1][1], fields[-1])
    return out


def history(repo: str, path: str, since: "str | None") -> list[tuple[str, str, int]]:
    """`(date, short sha, count)` per commit that touched [path], oldest first.

    Each commit is read at the path the file had IN that commit, so history before a rename is kept.
    """
    points: list[tuple[str, str, int]] = []
    for sha, date, at_path in reversed(_commits_with_paths(repo, path, since)):
        if at_path is None:
            continue
        count = count_entries(_git(repo, "show", f"{sha}:{at_path}"))
        if count is not None:
            points.append((date, sha, count))
    return points


def report(
    repo: str,
    directories: Sequence[str],
    since: str | None,
    stale_only: bool,
) -> list[str]:
    lines: list[str] = []
    for path in baseline_files(repo, directories):
        points = history(repo, path, since)
        if not points:
            continue
        first, last = points[0], points[-1]
        counts = [c for _, _, c in points]
        moved = min(counts) != max(counts)
        # A rule sitting at zero has nothing to decide about: it is a clean rule doing its job, not one
        # whose debt nobody owns. Listing it among the unmoved buries the three that matter.
        if stale_only and (moved or last[2] == 0):
            continue
        arrow = "->" if first[2] != last[2] else ("~~" if moved else "==")
        name = path.rsplit("/", 1)[-1].removesuffix(".json")
        lines.append(f"{last[2]:5d}  {name:34s} {first[2]:5d} {arrow} {last[2]:<5d}" f"  {first[0]} .. {last[0]}  ({len(points)} change(s))")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="repository root (default: here)")
    parser.add_argument(
        "--dir",
        dest="directories",
        action="append",
        help=f"a baseline directory; repeatable (default: {', '.join(DEFAULT_DIRECTORIES)})",
    )
    parser.add_argument("--since", help="only commits after this date, as git understands it")
    parser.add_argument(
        "--stale-days",
        type=int,
        help="deprecated spelling of --unmoved, kept so an existing invocation still works",
    )
    parser.add_argument(
        "--unmoved",
        action="store_true",
        help="only rules whose count is the same as the day they were adopted",
    )
    args = parser.parse_args(argv)

    try:
        lines = report(
            args.repo,
            args.directories or list(DEFAULT_DIRECTORIES),
            args.since,
            args.unmoved or args.stale_days is not None,
        )
    except GitError as exc:
        sys.stderr.write(f"baseline_trend: {exc}\n")
        return 2
    if not lines:
        sys.stdout.write("no baselines found\n")
        return 0
    sys.stdout.write("count  rule                               first .. last\n")
    for line in lines:
        sys.stdout.write(f"{line}\n")
    sys.stdout.write(
        "\n'~~' means the count moved and came back to where it started.\n"
        "A count that never moved is a rule to decide about: real unowned debt, a rule reporting\n"
        "something nobody can act on, or entries nobody has pruned.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
