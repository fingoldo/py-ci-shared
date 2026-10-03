"""Branch protection requires only status-check contexts that something actually produces.

pyutilz once required ``mypy-full / mypy-full`` while no workflow produced that check, so every merge would have waited
forever. The protection lives in GitHub settings, not in git, so no file gate can see it; this reads it through the API.

Per consumer in ``configs/consumers.toml``: the required contexts of its CI branch
(``branches/<branch>/protection/required_status_checks``: ``contexts`` and ``checks[].context``), against what the
newest five commits of that branch carry (check-run names and commit-status contexts; one commit is not enough while
its runs are still queued). A required context none of them carries is a finding. A repo whose protection cannot be read is reported LOUDLY and never passes silently:

* 404: the branch is not protected (nothing is required, so nothing can be missing): reported as ``unprotected``;
* 403 (private repos on a plan without protection rules for them, or a token without admin read): ``unreadable``, a
  GitHub ``::warning::`` line, and exit 1 under ``--strict``.

Usage (``ci-health.yml`` style; the API comes from :func:`py_ci_shared.ci_health.default_api`)::

    python -m py_ci_shared.required_check_contexts configs/consumers.toml [--strict]
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ._consumers import Consumer, load_consumers
from .ci_health import ApiError, default_api

__all__ = ["RepoChecks", "check_repo", "main"]

Api = Callable[[str], Any]


@dataclass
class RepoChecks:
    """One consumer: required contexts, what the newest commit produced, and the verdict."""

    name: str
    repo: str
    branch: str
    state: str = "ok"  # ok | missing | unprotected | unreadable | error
    required: list[str] = field(default_factory=list)
    produced: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    detail: str = ""


def _status_of(exc: ApiError) -> Optional[int]:
    if exc.status is not None:
        return exc.status
    text = str(exc)
    for code in (403, 404):
        if f"HTTP {code}" in text:
            return code
    return 403 if "Upgrade to GitHub Pro" in text else None


def _produced(api: Api, repo: str, branch: str, commits: int = 5) -> list[str]:
    """Check-run names and status contexts on the newest *commits* commits of *branch*: a run still queued on the head
    commit has not created its later jobs yet (mlframe's aggregate ``CI required checks`` job while CI was queued)."""
    shas = [str(c.get("sha")) for c in api(f"repos/{repo}/commits?sha={urllib.parse.quote(branch, safe='')}&per_page={commits}") or []]
    names: set[str] = set()
    for sha in shas:
        names.update(_produced_on(api, repo, sha))
    return sorted(names)


def _produced_on(api: Api, repo: str, ref: str) -> set[str]:
    names: set[str] = set()
    page = 1
    while True:
        data = api(f"repos/{repo}/commits/{ref}/check-runs?per_page=100&page={page}")
        runs = data.get("check_runs", []) if isinstance(data, dict) else []
        names.update(str(r.get("name", "")) for r in runs)
        if len(runs) < 100:
            break
        page += 1
    status = api(f"repos/{repo}/commits/{ref}/status")
    names.update(str(s.get("context", "")) for s in (status.get("statuses", []) if isinstance(status, dict) else []))
    return {n for n in names if n}


def check_repo(consumer: Consumer, api: Api) -> RepoChecks:
    out = RepoChecks(consumer.name, consumer.repo, consumer.branch)
    ref = urllib.parse.quote(consumer.branch, safe="")
    try:
        prot = api(f"repos/{consumer.repo}/branches/{ref}/protection/required_status_checks")
    except ApiError as exc:
        code = _status_of(exc)
        out.state, out.detail = ("unprotected", "branch not protected") if code == 404 else ("unreadable" if code == 403 else "error", str(exc))
        return out
    contexts = set(prot.get("contexts", []) or []) | {str(c.get("context", "")) for c in prot.get("checks", []) or []}
    out.required = sorted(c for c in contexts if c)
    try:
        out.produced = _produced(api, consumer.repo, consumer.branch)
    except ApiError as exc:
        out.state, out.detail = "error", f"cannot read the newest commit's checks: {exc}"
        return out
    out.missing = [c for c in out.required if c not in out.produced]
    out.state = "missing" if out.missing else "ok"
    return out


def main(argv: Optional[Sequence[str]] = None, *, api: Optional[Api] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.required_check_contexts", description=__doc__.split("\n", 1)[0])
    parser.add_argument("config", help="consumers.toml")
    parser.add_argument("--strict", action="store_true", help="also fail when a repo's protection cannot be read")
    args = parser.parse_args(argv)
    use_api = api if api is not None else default_api()[0]
    results = [check_repo(c, use_api) for c in load_consumers(Path(args.config))]
    for r in results:
        if r.state == "missing":
            sys.stdout.write(f"{r.repo}@{r.branch}: required context(s) no check on the 5 newest commits produced: {', '.join(r.missing)}" + "\n")
        elif r.state in ("unreadable", "error"):
            sys.stdout.write(f"::warning::{r.repo}@{r.branch}: branch protection {r.state}, required checks NOT verified ({r.detail})" + "\n")
        else:
            sys.stdout.write(f"{r.repo}@{r.branch}: {r.state} ({len(r.required)} required)" + "\n")
    bad = [r for r in results if r.state == "missing" or (r.state == "error") or (args.strict and r.state == "unreadable")]
    counts = {s: sum(r.state == s for r in results) for s in ("ok", "missing", "unprotected", "unreadable", "error")}
    sys.stdout.write("required_check_contexts: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
