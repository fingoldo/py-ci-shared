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

Reads both shapes a baseline is written in: ``{"accepted": {key: note}}`` (the Python ratchet) and
``{"entries": [...]}`` or ``{"entries": {finding: note}}`` (the Dart sweeps).

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

DEFAULT_DIRECTORIES = ("tool/meta/baselines", "test/meta/baselines")


def count_entries(text: str) -> int | None:
    """The number of accepted findings in a baseline file's text, or None when it is not one."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("accepted", "entries"):
        value = data.get(key)
        if isinstance(value, (dict, list)):
            return len(value)
    # `module_sizes.json` keeps its per-file ceilings under its own name.
    ceilings = data.get("ceilings")
    return len(ceilings) if isinstance(ceilings, dict) else None


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    ).stdout


def baseline_files(repo: str, directories: Iterable[str]) -> list[str]:
    out: list[str] = []
    for directory in directories:
        listing = _git(repo, "ls-files", f"{directory}/*.json")
        out.extend(line for line in listing.splitlines() if line.strip())
    return sorted(out)


def history(repo: str, path: str, since: str | None) -> list[tuple[str, str, int]]:
    """`(date, short sha, count)` per commit that touched [path], oldest first."""
    args = ["log", "--follow", "--format=%h %ad", "--date=short"]
    if since:
        args += [f"--since={since}"]
    args += ["--", path]
    points: list[tuple[str, str, int]] = []
    for line in reversed(_git(repo, *args).splitlines()):
        if not line.strip():
            continue
        sha, date = line.split(None, 1)
        count = count_entries(_git(repo, "show", f"{sha}:{path}"))
        if count is not None:
            points.append((date.strip(), sha, count))
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
        moved = first[2] != last[2]
        # A rule sitting at zero has nothing to decide about: it is a clean rule doing its job, not one
        # whose debt nobody owns. Listing it among the unmoved buries the three that matter.
        if stale_only and (moved or last[2] == 0):
            continue
        arrow = "->" if moved else "=="
        name = path.rsplit("/", 1)[-1].removesuffix(".json")
        lines.append(
            f"{last[2]:5d}  {name:34s} {first[2]:5d} {arrow} {last[2]:<5d}"
            f"  {first[0]} .. {last[0]}  ({len(points)} change(s))"
        )
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

    lines = report(
        args.repo,
        args.directories or list(DEFAULT_DIRECTORIES),
        args.since,
        args.unmoved or args.stale_days is not None,
    )
    if not lines:
        sys.stdout.write("no baselines found\n")
        return 0
    sys.stdout.write("count  rule                               first .. last\n")
    for line in lines:
        sys.stdout.write(f"{line}\n")
    sys.stdout.write(
        "\nA count that never moved is a rule to decide about: real unowned debt, a rule reporting\n"
        "something nobody can act on, or entries nobody has pruned.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
