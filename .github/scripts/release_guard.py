"""Release decisions for ``.github/workflows/release.yml``, as pure functions with a small command line.

* ``major-move TAG``: may pushing ``TAG`` move its major tag (``v1``)? Only when ``TAG`` is the highest release of that
  major. A back-port tag (``v1.17.1`` after ``v1.19.0``) must not move every ``@v1`` consumer backwards.
* ``rollback TAG``: ``TAG`` is an existing release, so the emergency ``workflow_dispatch`` may point its major at it.
* ``ci-green --repo R --sha S``: the ``self-ci.yml`` run for commit ``S`` succeeded; waits (``--wait``) while it is
  still running, fails when it failed or never ran.
* ``version`` (used by ``tests/test_release_version.py``): the declared version is ahead of every release tag, or
  is exactly the release whose tagged commit is ``HEAD``.

Stdlib only: the workflow runs it before (and independently of) installing the package. Exit 0 on yes, 1 with a
``::error::`` line on no, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Callable, Optional

RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
GREEN = frozenset({"success"})


def release_key(tag: str) -> Optional[tuple[int, int, int]]:
    """``(major, minor, patch)`` of a ``vX.Y.Z`` tag, else ``None`` (``v1``, ``v1.2``, ``v1.2.3-rc1`` are not releases)."""
    m = RELEASE_TAG.match(tag.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def release_tags(tags: Iterable[str]) -> list[str]:
    """The ``vX.Y.Z`` tags among ``tags``, oldest version first (numeric, so ``v1.10.0`` sorts after ``v1.9.0``)."""
    keyed = {t.strip(): release_key(t) for t in tags}
    return sorted((t for t, k in keyed.items() if k is not None), key=lambda t: keyed[t] or (0, 0, 0))


def highest(tags: Iterable[str], major: Optional[int] = None) -> Optional[str]:
    """The highest release tag, optionally only among ``vMAJOR.*.*``."""
    found = [t for t in release_tags(tags) if major is None or (release_key(t) or (-1,))[0] == major]
    return found[-1] if found else None


def major_move_problem(tag: str, tags: Iterable[str]) -> Optional[str]:
    """Why pushing ``tag`` may not move its major tag, or ``None`` when it may."""
    key = release_key(tag)
    if key is None:
        return f"{tag} is not a vX.Y.Z release tag"
    tags = list(tags)
    if tag not in tags:
        return f"{tag} is not among the repository's tags"
    top = highest(tags, key[0])
    if top != tag:
        return (
            f"{tag} is not the newest v{key[0]} release ({top} is): moving v{key[0]} to it would move every @v{key[0]} "
            f"consumer backwards. A back-port is published without moving v{key[0]}; to roll v{key[0]} back on purpose "
            "use the release workflow's workflow_dispatch (see CLAUDE.md, Rolling back a release)"
        )
    return None


def rollback_problem(target: str, tags: Iterable[str]) -> Optional[str]:
    """Why the major tag may not be pointed at ``target`` by the emergency dispatch, or ``None``."""
    if release_key(target) is None:
        return f"{target!r} is not a vX.Y.Z release tag"
    if target not in set(tags):
        return f"{target} is not an existing tag; roll back only to a release that already shipped"
    return None


def version_problem(version: str, tags: Iterable[str], head: str, tag_commit: Optional[str]) -> Optional[str]:
    """Why the declared ``version`` is wrong for a tree at commit ``head``, or ``None``.

    ``tag_commit`` is the commit ``v{version}`` points at (``None`` when that tag does not exist). Between releases the
    version must be strictly ahead of every release tag; it may equal a tag only on that tag's own commit."""
    key = release_key(f"v{version}")
    if key is None:
        return f"{version!r} is not X.Y.Z"
    top = highest(tags)
    fix = "bump version in pyproject.toml and src/py_ci_shared/__init__.py to the next release"
    if top is not None and key < (release_key(top) or (0, 0, 0)):
        return f"the declared version {version} is behind the newest release {top}; {fix}"
    if tag_commit is not None and tag_commit != head:
        return f"v{version} already shipped at {tag_commit[:12]} and HEAD ({head[:12]}) is past it; {fix}"
    return None


def ci_verdict(runs: Sequence[dict]) -> tuple[str, str]:
    """``("green" | "pending" | "red", why)`` from the ``self-ci.yml`` runs of one commit (any event)."""
    if any(r.get("conclusion") in GREEN for r in runs):
        ok = next(r for r in runs if r.get("conclusion") in GREEN)
        return "green", f"self-ci succeeded: {ok.get('html_url', '')}"
    if any(r.get("status") != "completed" for r in runs):
        return "pending", "self-ci is still running for this commit"
    if not runs:
        return "red", "self-ci never ran for this commit (tag a commit that was pushed to master)"
    worst = ", ".join(f"{r.get('conclusion')} {r.get('html_url', '')}" for r in runs)
    return "red", f"self-ci did not succeed for this commit: {worst}"


def _git_tags(cwd: Path) -> list[str]:
    out = subprocess.run(["git", "tag", "--list", "v*"], cwd=cwd, capture_output=True, text=True, check=True, timeout=60)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _github(token: str) -> Callable[[str], Any]:
    def get(path: str) -> Any:
        req = urllib.request.Request(f"https://api.github.com/{path}")
        req.add_header("Accept", "application/vnd.github+json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return get


def wait_for_ci(
    api: Callable[[str], Any],
    repo: str,
    sha: str,
    *,
    workflow: str = "self-ci.yml",
    wait: float = 0.0,
    poll: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, str]:
    """Poll ``workflow``'s runs for ``sha`` until green or red, or ``wait`` seconds pass (then the verdict is pending)."""
    end = clock() + wait
    while True:
        runs = api(f"repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=50").get("workflow_runs", [])
        verdict = ci_verdict(runs)
        if verdict[0] != "pending" or clock() >= end:
            return verdict
        sleep(poll)


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="release_guard.py", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("major-move", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("tag")
        p.add_argument("--repo-dir", default=".")
    ci = sub.add_parser("ci-green")
    ci.add_argument("--repo", required=True)
    ci.add_argument("--sha", required=True)
    ci.add_argument("--workflow", default="self-ci.yml")
    ci.add_argument("--wait", type=float, default=0.0, help="seconds to keep polling while the run is in progress")
    ci.add_argument("--poll", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.cmd == "ci-green":
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        state, why = wait_for_ci(_github(token), args.repo, args.sha, workflow=args.workflow, wait=args.wait, poll=args.poll)
        _say(why if state == "green" else f"::error::{why}")
        return 0 if state == "green" else 1
    tags = _git_tags(Path(args.repo_dir))
    problem = major_move_problem(args.tag, tags) if args.cmd == "major-move" else rollback_problem(args.tag, tags)
    if problem:
        _say(f"::error::{problem}")
        return 1
    _say(f"{args.tag}: ok to move v{(release_key(args.tag) or (0,))[0]} to it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
