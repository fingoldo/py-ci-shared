"""How long has each consumer repo's CI been red? A daily report over the GitHub Actions runs of every repo in
``configs/consumers.toml``.

For every active workflow of a repo, the completed runs on the consumer's CI branch (plus scheduled runs, which run
on the default branch) are read newest first; cancelled and skipped runs do not count. A workflow whose newest run
failed is *red*, and has been since the first failure after its last success: that is the "red for" figure. When
the window holds no success at all the figure is a lower bound (shown as ``>=``).

A failed run whose jobs never started because the account could not pay (the job annotation says "recent account
payments have failed" or names the "spending limit") says nothing about the code, so it is set aside like a cancelled
run. Failures are probed newest first until the first one that is not a billing refusal. A workflow is ``billing``
only when nothing but billing refusals is red since its last success; a code failure older than a billing-blocked run
keeps the workflow ``red``, counted from the first failure after the last success. A ``billing`` workflow is listed
separately and never fails the report. Everything else red for more than ``--max-red-days`` (default 2) is a finding.

Usage::

    python -m py_ci_shared.ci_health configs/consumers.toml [--max-red-days 2] [--output ci-health.md]
        [--only NAME ...] [--jobs 8] [--deadline 600] [--request-timeout 60]

Network: the GitHub REST API through ``urllib`` with the first token found in ``CONSUMER_READ_TOKEN``, ``GH_TOKEN``,
``GITHUB_TOKEN``; with none set, the ``gh`` CLI (its own login) when it is on PATH, else anonymous ``urllib``. Private
repos are read only with ``CONSUMER_READ_TOKEN`` (or through ``gh``); otherwise each is skipped with a GitHub
``::warning::``. The report goes to stdout and is appended to ``$GITHUB_STEP_SUMMARY`` when set. Exit 1 when a
non-billing workflow is red past the threshold or a repo that should be readable could not be read, else 0; exit 2
when ``--only`` names a consumer that is not in the file, or no consumer is left (a typo must not check nothing and pass).

Time: every request has a timeout (``--request-timeout``, for ``urllib`` and for the ``gh`` subprocess alike), the
consumers are read concurrently (``--jobs``), progress goes to stderr per consumer, and once ``--deadline`` seconds
have passed no new request is made: a repo not finished by then is an error, so the report always comes out, and
fails, inside the workflow's 15 minute budget. Measured 2026-10-03 on a workstation through ``gh``: 180 sequential
requests took 419 s with nothing printed until the end.

Every decision is a pure function over parsed API JSON (:func:`relevant_runs`, :func:`assess_workflow`,
:func:`is_billing_annotation`, :func:`render_markdown`, :func:`exit_code`); :func:`collect` is the only code that
talks to the network, through an injectable ``api`` callable.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ._consumers import TOKEN_ENV, Consumer, load_consumers

__all__ = [
    "BILLING_MARKERS",
    "ApiError",
    "DeadlineExceededError",
    "RepoHealth",
    "WorkflowHealth",
    "assess_repo",
    "assess_workflow",
    "collect",
    "default_api",
    "with_deadline",
    "exit_code",
    "is_billing_annotation",
    "main",
    "relevant_runs",
    "render_markdown",
]

#: GET ``path`` (relative to https://api.github.com, query string included) and return the parsed JSON.
Api = Callable[[str], Any]

#: Lower-cased fragments of the annotation GitHub puts on a job it refused to start for billing reasons.
BILLING_MARKERS = ("recent account payments have failed", "spending limit")
#: Conclusions that say nothing about the code: the run was stopped or never meant to run.
IGNORED_CONCLUSIONS = frozenset({"cancelled", "skipped", "stale"})
GREEN_CONCLUSIONS = frozenset({"success", "neutral"})
DEFAULT_MAX_RED_DAYS = 2.0
RUNS_PER_PAGE = 100
#: Pages of runs read per workflow while no success has been seen; past that the streak is a lower bound.
MAX_PAGES = 3
#: Jobs of one failed run whose annotations are read when deciding whether it is a billing failure.
MAX_JOBS_PROBED = 3
#: Failed runs of one workflow probed for billing, newest first. An unprobed failure counts as a code failure
#: (fail closed): on 2026-10-03 polyvocab_app/CI had 10 billing refusals on top of code failures that began 09-02.
MAX_BILLING_PROBES = 20
DEFAULT_JOBS = 8
#: Seconds after which no new request is made. The daily workflow has 15 minutes; this leaves room for setup.
DEFAULT_DEADLINE = 600.0
DEFAULT_REQUEST_TIMEOUT = 60.0


class ApiError(RuntimeError):
    """A GitHub API call failed; ``status`` is the HTTP status when there was one."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class DeadlineExceededError(ApiError):
    """The overall deadline passed before this request; no request is made after it."""


@dataclass
class WorkflowHealth:
    """One workflow's state. ``status``: ``green``, ``red`` or ``billing``. ``red_since`` is the first failure after
    the last success; ``lower_bound`` is true when the window held no success, so the real streak is longer.
    ``billing_runs`` counts the billing-refused failures since ``red_since``, which never count as code failures."""

    workflow: str
    path: str
    status: str
    red_since: Optional[datetime] = None
    last_success: Optional[datetime] = None
    lower_bound: bool = False
    red_runs: int = 0
    latest_url: str = ""
    days_red: float = 0.0
    billing_runs: int = 0


@dataclass
class RepoHealth:
    """One repo: its workflows, or why it has none to show (``skipped`` for want of a token, ``error``, no runs)."""

    name: str
    repo: str
    branch: str
    workflows: list[WorkflowHealth] = field(default_factory=list)
    skipped: str = ""
    error: str = ""


def _ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def relevant_runs(runs: Iterable[dict]) -> list[dict]:
    """Completed runs that say something about the code, newest first, one per run id."""
    seen: dict[int, dict] = {}
    for run in runs:
        if run.get("status") != "completed" or not run.get("conclusion") or run["conclusion"] in IGNORED_CONCLUSIONS:
            continue
        seen.setdefault(int(run["id"]), run)
    return sorted(seen.values(), key=lambda r: (r.get("created_at") or "", r["id"]), reverse=True)


def is_billing_annotation(texts: Iterable[str]) -> bool:
    """True when any annotation text is GitHub's "the job was not started because of billing" message."""
    return any(marker in (text or "").lower() for text in texts for marker in BILLING_MARKERS)


def _days(now: datetime, since: Optional[datetime]) -> float:
    return max((now - since).total_seconds() / 86400.0, 0.0) if since else 0.0


def assess_workflow(
    workflow: dict, runs: Iterable[dict], now: datetime, *, billing: bool = False, billing_run_ids: Iterable[int] = ()
) -> Optional[WorkflowHealth]:
    """The health of ``workflow`` (an item of ``/actions/workflows``) from its ``runs``; ``None`` when none counts.

    ``billing_run_ids`` names the failed runs GitHub refused to start for billing. They are set aside like cancelled
    runs: the workflow is ``billing`` only when nothing but such refusals is red since the last success, and ``red``
    (counted from the first code failure after the last success) otherwise. ``billing=True`` is the older form of
    "the newest run is such a refusal"."""
    ordered = relevant_runs(runs)
    if not ordered:
        return None
    blocked = {int(i) for i in billing_run_ids}
    if billing:
        blocked.add(int(ordered[0]["id"]))
    name, path = str(workflow.get("name") or ordered[0].get("name") or "?"), str(workflow.get("path") or "")
    latest_url = str(ordered[0].get("html_url") or "")
    is_refused = lambda r: int(r["id"]) in blocked and r["conclusion"] not in GREEN_CONCLUSIONS  # noqa: E731
    real = [r for r in ordered if not is_refused(r)]
    if not real or real[0]["conclusion"] in GREEN_CONCLUSIONS:
        last_ok = real[0] if real else None
        newer = [r for r in ordered if is_refused(r) and (last_ok is None or (r.get("created_at") or "") >= (last_ok.get("created_at") or ""))]
        last_success = _ts(last_ok.get("created_at")) if last_ok else None
        if not newer:
            return WorkflowHealth(name, path, "green", last_success=last_success, latest_url=latest_url)
        since = _ts(newer[-1].get("created_at"))
        return WorkflowHealth(
            name,
            path,
            "billing",
            red_since=since,
            last_success=last_success,
            lower_bound=last_ok is None,
            red_runs=len(newer),
            latest_url=latest_url,
            days_red=_days(now, since),
            billing_runs=len(newer),
        )
    streak: list[dict] = []
    last_success = None
    for run in real:
        if run["conclusion"] in GREEN_CONCLUSIONS:
            last_success = _ts(run.get("created_at"))
            break
        streak.append(run)
    cutoff = streak[-1].get("created_at") or ""
    red_since = _ts(cutoff)
    return WorkflowHealth(
        name,
        path,
        "red",
        red_since=red_since,
        last_success=last_success,
        lower_bound=last_success is None,
        red_runs=len(streak),
        latest_url=latest_url,
        days_red=_days(now, red_since),
        billing_runs=sum(1 for r in ordered if is_refused(r) and (r.get("created_at") or "") >= cutoff),
    )


def assess_repo(
    consumer: Consumer,
    workflows: Sequence[dict],
    runs_by_workflow: dict[int, list[dict]],
    now: datetime,
    *,
    billing_run_ids: Iterable[int] = (),
) -> RepoHealth:
    """Assemble a :class:`RepoHealth` from parsed API data. Only ``active`` workflows count; ``billing_run_ids`` names
    the failed runs whose jobs GitHub refused to start for billing reasons."""
    billing_ids = set(billing_run_ids)
    out = RepoHealth(consumer.name, consumer.repo, consumer.branch)
    for wf in workflows:
        if wf.get("state", "active") != "active":
            continue
        health = assess_workflow(wf, runs_by_workflow.get(int(wf["id"]), []), now, billing_run_ids=billing_ids)
        if health is not None:
            out.workflows.append(health)
    return out


def over_threshold(repo: RepoHealth, max_red_days: float) -> list[WorkflowHealth]:
    """Red (not billing) workflows red for more than ``max_red_days``."""
    return [w for w in repo.workflows if w.status == "red" and w.days_red > max_red_days]


def exit_code(repos: Sequence[RepoHealth], max_red_days: float) -> int:
    """1 when a non-billing workflow is red past the threshold or a readable repo errored, else 0."""
    return 1 if any(r.error or over_threshold(r, max_red_days) for r in repos) else 0


def _day(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d") if value else "-"


def _red_for(w: WorkflowHealth) -> str:
    if w.status == "green":
        return "-"
    text = f"{'>=' if w.lower_bound else ''}{w.days_red:.1f} d ({w.red_runs} run{'s' if w.red_runs != 1 else ''})"
    if w.status == "red" and w.billing_runs:
        text += f" + {w.billing_runs} billing-blocked"
        if w.billing_runs >= MAX_BILLING_PROBES:
            text += ", older failures not probed"
    return text


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


_ORDER = {"red": 0, "billing": 1, "green": 2}


def render_markdown(repos: Sequence[RepoHealth], now: datetime, max_red_days: float) -> str:
    """The report: one table row per workflow (worst first), then the repos that were skipped, errored or had no runs."""
    lines = [
        f"## Consumer CI health ({now.strftime('%Y-%m-%d %H:%M')} UTC, red threshold {max_red_days:g} days)",
        "",
        "| repo | workflow | status | red for | red since | last success | latest run |",
        "|---|---|---|---|---|---|---|",
    ]
    rows: list[tuple[tuple, str]] = []
    for r in repos:
        for w in r.workflows:
            flag = "RED" if w.status == "red" and w.days_red > max_red_days else w.status
            link = f"[run]({w.latest_url})" if w.latest_url else "-"
            row = f"| {r.name} | {_cell(w.workflow)} | {flag} | {_red_for(w)} | {_day(w.red_since)} | {_day(w.last_success)} | {link} |"
            rows.append(((_ORDER[w.status], -w.days_red, r.name, w.workflow), row))
    lines += [row for _, row in sorted(rows, key=lambda x: x[0])]
    notes = []
    for r in repos:
        if r.skipped:
            notes.append(f"- {r.name} (`{r.repo}`): skipped, {r.skipped}")
        elif r.error:
            notes.append(f"- {r.name} (`{r.repo}`): ERROR, {r.error}")
        elif not r.workflows:
            notes.append(f"- {r.name} (`{r.repo}`): no completed runs on `{r.branch}`")
    if notes:
        lines += ["", *notes]
    flagged = [(r, w) for r in repos for w in over_threshold(r, max_red_days)]
    billing = [(r, w) for r in repos for w in r.workflows if w.status == "billing"]
    lines.append("")
    lines.append(
        f"{len(flagged)} workflow(s) red for more than {max_red_days:g} days"
        + (": " + ", ".join(f"{r.name}/{w.workflow}" for r, w in flagged) if flagged else "")
        + f"; {len(billing)} blocked by billing"
        + (": " + ", ".join(f"{r.name}/{w.workflow}" for r, w in billing) if billing else "")
        + "."
    )
    return "\n".join(lines) + "\n"


# --- network -------------------------------------------------------------------------------------------------------


def urllib_api(token: Optional[str], *, base: str = "https://api.github.com", timeout: float = DEFAULT_REQUEST_TIMEOUT) -> Api:
    """An :data:`Api` over ``urllib``; ``token`` may be ``None`` (anonymous: public repos, 60 requests an hour)."""

    def get(path: str) -> Any:
        req = urllib.request.Request(f"{base}/{path.lstrip('/')}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ApiError(f"GET {path}: HTTP {exc.code} {exc.reason}", status=exc.code) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ApiError(f"GET {path}: {type(exc).__name__}: {exc}") from exc

    return get


def gh_api(executable: str = "gh", *, timeout: float = DEFAULT_REQUEST_TIMEOUT) -> Api:
    """An :data:`Api` over ``gh api`` (its own login); a call still running after ``timeout`` seconds is an :class:`ApiError`."""

    def get(path: str) -> Any:
        try:
            proc = subprocess.run(
                [executable, "api", path.lstrip("/")], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise ApiError(f"gh api {path}: no answer in {timeout:g} s") from exc
        if proc.returncode != 0:
            msg = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"exit {proc.returncode}"
            status = 404 if "HTTP 404" in proc.stderr else None
            raise ApiError(f"gh api {path}: {msg}", status=status)
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise ApiError(f"gh api {path}: not JSON ({exc})") from exc

    return get


def default_api(env: Optional[dict] = None, *, timeout: float = DEFAULT_REQUEST_TIMEOUT) -> tuple[Api, bool]:
    """The :data:`Api` to use and whether it may read private consumers (see the module docstring)."""
    env = dict(os.environ if env is None else env)
    consumer_token = (env.get(TOKEN_ENV) or "").strip()
    token = consumer_token or (env.get("GH_TOKEN") or "").strip() or (env.get("GITHUB_TOKEN") or "").strip()
    if token:
        return urllib_api(token, timeout=timeout), bool(consumer_token)
    gh = shutil.which("gh")
    if gh:
        return gh_api(gh, timeout=timeout), True
    return urllib_api(None, timeout=timeout), False


def with_deadline(api: Api, deadline: float, *, clock: Callable[[], float] = time.monotonic) -> Api:
    """``api`` that refuses (:class:`DeadlineExceededError`) every request started once ``clock()`` reaches ``deadline``."""

    def get(path: str) -> Any:
        if clock() >= deadline:
            raise DeadlineExceededError(f"GET {path}: not attempted, the overall deadline passed")
        return api(path)

    return get


def _q(**params: Any) -> str:
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def _billing_refusals(api: Api, repo: str, ordered: Sequence[dict]) -> list[int]:
    """Ids of the newest failures (at most :data:`MAX_BILLING_PROBES`) that were refused for billing, stopping at the
    first green run or the first failure that is not such a refusal."""
    out: list[int] = []
    for run in ordered[:MAX_BILLING_PROBES]:
        if run["conclusion"] in GREEN_CONCLUSIONS or not _run_is_billing(api, repo, run):
            break
        out.append(int(run["id"]))
    return out


def _run_is_billing(api: Api, repo: str, run: dict) -> bool:
    jobs = api(f"repos/{repo}/actions/runs/{run['id']}/jobs?{_q(per_page=100)}").get("jobs", [])
    # A job GitHub refused to start has no steps; probe those first, then any failed job.
    probe = sorted((j for j in jobs if j.get("conclusion") not in GREEN_CONCLUSIONS), key=lambda j: bool(j.get("steps")))[:MAX_JOBS_PROBED]
    for job in probe:
        annotations = api(f"repos/{repo}/check-runs/{job['id']}/annotations")
        if is_billing_annotation(str(a.get("message") or "") + " " + str(a.get("title") or "") for a in annotations):
            return True
    return False


def _paged_runs(api: Api, base: str, **filters: str) -> list[dict]:
    """Completed runs of one workflow, newest first, page by page until a success shows up (or :data:`MAX_PAGES`)."""
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        batch = api(f"{base}?{_q(**filters, status='completed', per_page=RUNS_PER_PAGE, page=page)}").get("workflow_runs", [])
        out += batch
        if len(batch) < RUNS_PER_PAGE or any(r.get("conclusion") in GREEN_CONCLUSIONS for r in batch):
            break
    return out


def collect(consumer: Consumer, api: Api, now: datetime, *, can_read_private: bool) -> RepoHealth:
    """Read one consumer's workflows and runs through ``api`` and assess them. API failures become ``error``."""
    if consumer.private and not can_read_private:
        return RepoHealth(consumer.name, consumer.repo, consumer.branch, skipped=f"private and {TOKEN_ENV} is not set; it was NOT checked")
    try:
        meta = api(f"repos/{consumer.repo}")
        default_branch = str(meta.get("default_branch") or consumer.branch)
        workflows = [w for w in api(f"repos/{consumer.repo}/actions/workflows?{_q(per_page=100)}").get("workflows", []) if w.get("state") == "active"]
        runs_by: dict[int, list[dict]] = {}
        billing_ids: list[int] = []
        for wf in workflows:
            base = f"repos/{consumer.repo}/actions/workflows/{wf['id']}/runs"
            runs = _paged_runs(api, base, branch=consumer.branch)
            if default_branch != consumer.branch:
                runs += _paged_runs(api, base, event="schedule")
            ordered = relevant_runs(runs)
            runs_by[int(wf["id"])] = ordered
            billing_ids += _billing_refusals(api, consumer.repo, ordered)
    except ApiError as exc:
        return RepoHealth(consumer.name, consumer.repo, consumer.branch, error=str(exc))
    return assess_repo(consumer, workflows, runs_by, now, billing_run_ids=billing_ids)


def _annotations(repos: Sequence[RepoHealth], max_red_days: float) -> list[str]:
    out: list[str] = []
    for r in repos:
        if r.skipped:
            out.append(f"::warning title=consumer skipped::{r.repo} is private and {TOKEN_ENV} is not set; its CI health was NOT checked")
        if r.error:
            out.append(f"::error title=ci_health::{r.repo}: {r.error}")
        out += [
            f"::error title=CI red for {w.days_red:.1f} days::{r.repo} {w.workflow} ({w.path}) has been red since {_day(w.red_since)}"
            for w in over_threshold(r, max_red_days)
        ]
        out += [
            f"::warning title=CI blocked by billing::{r.repo} {w.workflow}: jobs were not started (account payment / spending limit)"
            for w in r.workflows
            if w.status == "billing"
        ]
    return out


def main(argv: Optional[Sequence[str]] = None, *, api: Optional[Api] = None, now: Optional[datetime] = None) -> int:
    """Report every consumer's CI health; 1 when a non-billing workflow is red past ``--max-red-days``."""
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.ci_health", description=__doc__.split("\n", 1)[0])
    parser.add_argument("config", nargs="?", default="configs/consumers.toml", help="consumers.toml (default: configs/consumers.toml)")
    parser.add_argument("--max-red-days", type=float, default=DEFAULT_MAX_RED_DAYS, help="days a workflow may stay red (default 2)")
    parser.add_argument("--output", help="also write the markdown report to this file")
    parser.add_argument("--only", action="append", default=[], help="check only this consumer name (repeatable)")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS, help=f"consumers read at once (default {DEFAULT_JOBS})")
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE, help=f"seconds after which no request is made (default {DEFAULT_DEADLINE:g})")
    parser.add_argument("--request-timeout", type=float, default=DEFAULT_REQUEST_TIMEOUT, help=f"seconds per request (default {DEFAULT_REQUEST_TIMEOUT:g})")
    args = parser.parse_args(argv)
    consumers = load_consumers(Path(args.config))
    if args.only:
        known = {c.name for c in consumers}
        unknown = sorted(set(args.only) - known)
        if unknown:
            sys.stderr.write(f"ci_health: --only names no consumer in {args.config}: {', '.join(unknown)} (known: {', '.join(sorted(known))})\n")
            return 2
        consumers = [c for c in consumers if c.name in set(args.only)]
    if not consumers:
        sys.stderr.write(f"ci_health: {args.config} lists no consumer; nothing would be checked\n")
        return 2
    if api is None:
        api, can_read_private = default_api(timeout=args.request_timeout)
    else:
        can_read_private = True
    bounded = with_deadline(api, time.monotonic() + args.deadline)
    at = now or datetime.now(timezone.utc)
    lock = threading.Lock()
    started = time.monotonic()

    def one(consumer: Consumer) -> RepoHealth:
        result = collect(consumer, bounded, at, can_read_private=can_read_private)
        state = "skipped" if result.skipped else ("ERROR" if result.error else f"{len(result.workflows)} workflow(s)")
        with lock:
            sys.stderr.write(f"ci_health: {consumer.name}: {state} ({time.monotonic() - started:.0f} s)\n")
            sys.stderr.flush()
        return result

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        repos = list(pool.map(one, consumers))
    text = render_markdown(repos, at, args.max_red_days)
    sys.stdout.write(text)
    for line in _annotations(repos, args.max_red_days):
        sys.stdout.write(line + "\n")
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    code = exit_code(repos, args.max_red_days)
    if code == 0 and any(w.status == "billing" for r in repos for w in r.workflows):
        sys.stdout.write("ci_health: WARNING: some workflows are blocked by billing, not by the code; fix the account, not the repo\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
