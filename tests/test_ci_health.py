"""ci_health: pure assessment over recorded GitHub API JSON, plus the CLI and the workflow that runs it.

The fixtures have the shape of GitHub REST responses (``/actions/workflows``, ``/actions/workflows/{id}/runs``,
``/actions/runs/{id}/jobs``, ``/check-runs/{id}/annotations``), reduced to the fields the module reads. The nightly's
timestamps and the code-failure annotations are copied from the consumer repos' real runs of 2026-09-28..10-02.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from py_ci_shared import ci_health
from py_ci_shared._consumers import Consumer
from py_ci_shared.ci_health import ApiError, assess_workflow, exit_code, is_billing_annotation, relevant_runs, render_markdown

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

BILLING_TEXT = (
    "The job was not started because recent account payments have failed or your spending limit needs to be increased. "
    "Please check the 'Billing & plans' section in your settings"
)


def _run(run_id: int, created: str, conclusion: str, *, status: str = "completed", event: str = "push", name: str = "CI") -> dict:
    return {
        "id": run_id,
        "name": name,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
    }


GREEN_WF = {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}
GREEN_RUNS = [
    _run(103, "2026-10-03T08:00:00Z", "success"),
    _run(102, "2026-10-02T08:00:00Z", "failure"),
    _run(101, "2026-10-01T08:00:00Z", "success"),
]

# glossum_backend_scripts' nightly: red every night since 2026-09-28 after a green 2026-09-27.
NIGHTLY_WF = {"id": 2, "name": "Nightly thorough property tests", "path": ".github/workflows/nightly-property.yml", "state": "active"}
NIGHTLY_RUNS = [
    _run(210, "2026-10-03T09:40:00Z", "cancelled", event="schedule"),
    _run(209, "2026-10-02T09:43:15Z", "failure", event="schedule"),
    _run(208, "2026-10-01T10:05:19Z", "failure", event="schedule"),
    _run(207, "2026-09-30T09:39:32Z", "failure", event="schedule"),
    _run(206, "2026-09-29T09:46:58Z", "failure", event="schedule"),
    _run(205, "2026-09-28T09:41:02Z", "failure", event="schedule"),
    _run(204, "2026-09-27T09:40:11Z", "success", event="schedule"),
]

# A billing-blocked repo: the jobs were created but never started (no steps), with GitHub's billing annotation.
BILLING_WF = {"id": 3, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}
BILLING_RUNS = [_run(302, "2026-10-02T18:35:35Z", "failure"), _run(301, "2026-09-25T10:00:00Z", "failure"), _run(300, "2026-09-20T10:00:00Z", "success")]
BILLING_JOBS = {"jobs": [{"id": 9001, "name": "test (3.11)", "conclusion": "failure", "steps": []}]}
BILLING_ANNOTATIONS = [{"annotation_level": "failure", "title": "", "message": BILLING_TEXT}]

# A real code failure: the jobs ran and the annotation is a process exit code.
CODE_JOBS = {"jobs": [{"id": 9002, "name": "test (3.11)", "conclusion": "failure", "steps": [{"name": "Run tests", "conclusion": "failure"}]}]}
CODE_ANNOTATIONS = [
    {"annotation_level": "warning", "title": "", "message": "Node.js 20 is deprecated."},
    {"annotation_level": "failure", "title": "", "message": "Process completed with exit code 2."},
]


def _consumer(name: str, *, private: bool = False, branch: str = "main") -> Consumer:
    return Consumer(name=name, repo=f"fingoldo/{name}", branch=branch, private=private)


class FakeApi:
    """Routes ``api(path)`` by path prefix (query string ignored) to recorded JSON; records every call."""

    def __init__(self, routes: dict[str, Any]):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, path: str) -> Any:
        self.calls.append(path)
        key = path.split("?", 1)[0]
        if key not in self.routes:
            raise ApiError(f"GET {path}: HTTP 404 Not Found", status=404)
        value = self.routes[key]
        if isinstance(value, Exception):
            raise value
        return value


def _routes(name: str, workflows: list[dict], runs: dict[int, list[dict]], *, default_branch: str = "main", extra: Any = None) -> dict[str, Any]:
    repo = f"repos/fingoldo/{name}"
    out: dict[str, Any] = {repo: {"default_branch": default_branch}, f"{repo}/actions/workflows": {"workflows": workflows}}
    for wf_id, wf_runs in runs.items():
        out[f"{repo}/actions/workflows/{wf_id}/runs"] = {"workflow_runs": wf_runs}
    out.update(extra or {})
    return out


# --- pure functions ---------------------------------------------------------------------------------------------


def test_relevant_runs_drop_cancelled_skipped_and_unfinished_and_sort_newest_first():
    runs = [
        _run(1, "2026-10-01T00:00:00Z", "success"),
        _run(2, "2026-10-03T00:00:00Z", "cancelled"),
        _run(3, "2026-10-02T00:00:00Z", "skipped"),
        _run(4, "2026-10-04T00:00:00Z", "", status="in_progress"),
        _run(5, "2026-10-02T12:00:00Z", "failure"),
        _run(5, "2026-10-02T12:00:00Z", "failure"),
    ]
    assert [r["id"] for r in relevant_runs(runs)] == [5, 1]


def test_a_green_workflow_is_green_even_after_an_earlier_failure():
    w = assess_workflow(GREEN_WF, GREEN_RUNS, NOW)
    assert w is not None and w.status == "green" and w.days_red == 0.0 and w.red_runs == 0
    assert w.last_success == datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)


def test_a_nightly_red_for_five_days_counts_from_its_first_failure_after_the_last_success():
    w = assess_workflow(NIGHTLY_WF, NIGHTLY_RUNS, NOW)
    assert w is not None and w.status == "red"
    assert w.red_since == datetime(2026, 9, 28, 9, 41, 2, tzinfo=timezone.utc)
    assert w.last_success == datetime(2026, 9, 27, 9, 40, 11, tzinfo=timezone.utc)
    assert w.red_runs == 5 and not w.lower_bound
    assert w.days_red == pytest.approx(5.0965, abs=1e-3)
    assert w.latest_url.endswith("/209"), "the cancelled run 210 must not count as the latest"


def test_no_success_in_the_window_makes_the_streak_a_lower_bound():
    w = assess_workflow(NIGHTLY_WF, NIGHTLY_RUNS[:-1], NOW)
    assert w is not None and w.lower_bound and w.last_success is None and w.red_runs == 5


def test_a_workflow_with_only_cancelled_runs_has_no_health():
    assert assess_workflow(GREEN_WF, [_run(1, "2026-10-01T00:00:00Z", "cancelled")], NOW) is None


@pytest.mark.parametrize(
    "texts, expected",
    [
        ([BILLING_TEXT], True),
        (["Your spending limit was reached"], True),
        (["Process completed with exit code 2.", "Node.js 20 is deprecated."], False),
        ([], False),
    ],
)
def test_billing_annotation_detection(texts, expected):
    assert is_billing_annotation(texts) is expected


# --- collect over recorded API JSON -----------------------------------------------------------------------------


def test_collect_green_and_red_nightly_repo_exits_1_on_the_red_nightly():
    routes = _routes(
        "glossum_backend_scripts",
        [GREEN_WF, NIGHTLY_WF, {"id": 9, "name": "Old", "path": ".github/workflows/old.yml", "state": "disabled_manually"}],
        {1: GREEN_RUNS, 2: NIGHTLY_RUNS},
        extra={
            "repos/fingoldo/glossum_backend_scripts/actions/runs/209/jobs": CODE_JOBS,
            "repos/fingoldo/glossum_backend_scripts/check-runs/9002/annotations": CODE_ANNOTATIONS,
        },
    )
    api = FakeApi(routes)
    repo = ci_health.collect(_consumer("glossum_backend_scripts", private=True), api, NOW, can_read_private=True)
    assert not repo.error and not repo.skipped
    assert {w.workflow: w.status for w in repo.workflows} == {"CI": "green", "Nightly thorough property tests": "red"}
    assert not any("/workflows/9/" in c for c in api.calls), "a disabled workflow is not read"
    assert exit_code([repo], 2) == 1
    assert exit_code([repo], 6) == 0
    text = render_markdown([repo], NOW, 2)
    lines = text.splitlines()
    nightly = next(i for i, line in enumerate(lines) if "Nightly" in line)
    ci = next(i for i, line in enumerate(lines) if "| CI |" in line)
    assert nightly < ci, "the worst workflow comes first"
    assert "| RED | 5.1 d (5 runs) | 2026-09-28 | 2026-09-27 |" in lines[nightly]
    assert "1 workflow(s) red for more than 2 days: glossum_backend_scripts/Nightly thorough property tests; 0 blocked by billing." in text


def test_collect_billing_blocked_repo_reports_billing_and_exits_0(capsys, tmp_path, monkeypatch):
    routes = _routes(
        "autopsia",
        [BILLING_WF],
        {3: BILLING_RUNS},
        default_branch="master",
        extra={"repos/fingoldo/autopsia/actions/runs/302/jobs": BILLING_JOBS, "repos/fingoldo/autopsia/check-runs/9001/annotations": BILLING_ANNOTATIONS},
    )
    repo = ci_health.collect(_consumer("autopsia", private=True, branch="master"), FakeApi(routes), NOW, can_read_private=True)
    (w,) = repo.workflows
    assert w.status == "billing" and w.days_red > 2
    assert exit_code([repo], 2) == 0
    assert "0 workflow(s) red for more than 2 days; 1 blocked by billing: autopsia/CI." in render_markdown([repo], NOW, 2)


def test_a_code_failure_is_not_billing():
    routes = _routes(
        "autopsia",
        [BILLING_WF],
        {3: BILLING_RUNS},
        default_branch="master",
        extra={"repos/fingoldo/autopsia/actions/runs/302/jobs": CODE_JOBS, "repos/fingoldo/autopsia/check-runs/9002/annotations": CODE_ANNOTATIONS},
    )
    repo = ci_health.collect(_consumer("autopsia", branch="master"), FakeApi(routes), NOW, can_read_private=True)
    assert repo.workflows[0].status == "red" and exit_code([repo], 2) == 1


def test_a_repo_with_no_runs_is_listed_and_passes():
    routes = _routes("llm_bench", [GREEN_WF], {1: []})
    repo = ci_health.collect(_consumer("llm_bench"), FakeApi(routes), NOW, can_read_private=False)
    assert repo.workflows == [] and not repo.error
    assert exit_code([repo], 2) == 0
    assert "- llm_bench (`fingoldo/llm_bench`): no completed runs on `main`" in render_markdown([repo], NOW, 2)


def test_a_private_repo_without_a_token_is_skipped_without_a_call():
    api = FakeApi({})
    repo = ci_health.collect(_consumer("social", private=True), api, NOW, can_read_private=False)
    assert repo.skipped and api.calls == [] and exit_code([repo], 2) == 0


def test_an_unreadable_repo_is_an_error_and_fails():
    repo = ci_health.collect(_consumer("gone"), FakeApi({}), NOW, can_read_private=True)
    assert "HTTP 404" in repo.error and exit_code([repo], 2) == 1


def test_scheduled_runs_are_read_when_the_ci_branch_is_not_the_default_one():
    api = FakeApi(_routes("polyvocab_app", [NIGHTLY_WF], {2: NIGHTLY_RUNS}, default_branch="main"))
    ci_health.collect(_consumer("polyvocab_app", branch="staging"), api, NOW, can_read_private=True)
    run_calls = [c for c in api.calls if c.split("?")[0].endswith("/runs") and "/workflows/" in c]
    assert any("branch=staging" in c for c in run_calls) and any("event=schedule" in c for c in run_calls)


# --- CLI ---------------------------------------------------------------------------------------------------------


def _config(tmp_path: Path) -> Path:
    p = tmp_path / "consumers.toml"
    p.write_text(
        '[[consumer]]\nname = "glossum_backend_scripts"\nrepo = "fingoldo/glossum_backend_scripts"\nbranch = "main"\nprivate = false\n\n'
        '[[consumer]]\nname = "autopsia"\nrepo = "fingoldo/autopsia"\nbranch = "master"\nprivate = false\n',
        encoding="utf-8",
    )
    return p


def _all_routes(nightly_conclusion: str) -> dict[str, Any]:
    nightly = [dict(r, conclusion=nightly_conclusion) if r["id"] == 209 else r for r in NIGHTLY_RUNS]
    routes = _routes(
        "glossum_backend_scripts",
        [NIGHTLY_WF],
        {2: nightly},
        extra={
            "repos/fingoldo/glossum_backend_scripts/actions/runs/209/jobs": CODE_JOBS,
            "repos/fingoldo/glossum_backend_scripts/check-runs/9002/annotations": CODE_ANNOTATIONS,
        },
    )
    routes.update(
        _routes(
            "autopsia",
            [BILLING_WF],
            {3: BILLING_RUNS},
            default_branch="master",
            extra={"repos/fingoldo/autopsia/actions/runs/302/jobs": BILLING_JOBS, "repos/fingoldo/autopsia/check-runs/9001/annotations": BILLING_ANNOTATIONS},
        )
    )
    return routes


def test_main_writes_the_step_summary_and_fails_on_the_red_nightly(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = ci_health.main([str(_config(tmp_path)), "--output", str(tmp_path / "out.md")], api=FakeApi(_all_routes("failure")), now=NOW)
    out = capsys.readouterr().out
    assert code == 1
    assert "::error title=CI red for 5.1 days::fingoldo/glossum_backend_scripts Nightly thorough property tests" in out
    assert "::warning title=CI blocked by billing::fingoldo/autopsia CI" in out
    assert summary.read_text(encoding="utf-8") == (tmp_path / "out.md").read_text(encoding="utf-8")
    assert "## Consumer CI health (2026-10-03 12:00 UTC, red threshold 2 days)" in summary.read_text(encoding="utf-8")


def test_main_billing_only_exits_0_with_a_warning_line(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    code = ci_health.main([str(_config(tmp_path))], api=FakeApi(_all_routes("success")), now=NOW)
    out = capsys.readouterr().out
    assert code == 0
    assert "ci_health: WARNING: some workflows are blocked by billing" in out


def test_main_threshold_flag_is_honoured(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert ci_health.main([str(_config(tmp_path)), "--max-red-days", "6"], api=FakeApi(_all_routes("failure")), now=NOW) == 0


@pytest.mark.parametrize(
    "env, private_ok, kind",
    [
        ({"CONSUMER_READ_TOKEN": "x", "GITHUB_TOKEN": "y"}, True, "urllib"),
        ({"GITHUB_TOKEN": "y"}, False, "urllib"),
        ({"CONSUMER_READ_TOKEN": " ", "GH_TOKEN": "y"}, False, "urllib"),
    ],
)
def test_default_api_reads_private_repos_only_with_the_consumer_token(env, private_ok, kind):
    api, can_read_private = ci_health.default_api(env)
    assert can_read_private is private_ok and api.__qualname__.startswith(f"{kind}_api")


def test_default_api_falls_back_to_gh_then_anonymous(monkeypatch):
    monkeypatch.setattr(ci_health.shutil, "which", lambda name: "/usr/bin/gh")
    api, ok = ci_health.default_api({})
    assert ok and api.__qualname__.startswith("gh_api")
    monkeypatch.setattr(ci_health.shutil, "which", lambda name: None)
    api, ok = ci_health.default_api({})
    assert not ok and api.__qualname__.startswith("urllib_api")


# --- the workflow ------------------------------------------------------------------------------------------------


def test_the_ci_health_workflow_runs_daily_read_only_with_the_optional_consumer_token():
    path = REPO / ".github" / "workflows" / "ci-health.yml"
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert wf[True]["schedule"] and "workflow_dispatch" in wf[True]
    assert wf["permissions"] == {"contents": "read"}
    (job,) = wf["jobs"].values()
    step = next(s for s in job["steps"] if "py_ci_shared.ci_health" in str(s.get("run", "")))
    assert step["env"]["CONSUMER_READ_TOKEN"] == "${{ secrets.CONSUMER_READ_TOKEN }}"
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert "--max-red-days 2" in step["run"] and 'if [ -z "$CONSUMER_READ_TOKEN" ]' in step["run"]


def test_runs_are_paged_until_a_success_shows_up(monkeypatch):
    monkeypatch.setattr(ci_health, "RUNS_PER_PAGE", 2)
    pages = {1: NIGHTLY_RUNS[1:3], 2: NIGHTLY_RUNS[3:5], 3: NIGHTLY_RUNS[5:7]}
    calls: list[str] = []

    def api(path: str) -> Any:
        calls.append(path)
        page = int(path.rsplit("page=", 1)[1])
        return {"workflow_runs": pages.get(page, [])}

    runs = ci_health._paged_runs(api, "repos/o/r/actions/workflows/2/runs", branch="main")
    assert [r["id"] for r in runs] == [209, 208, 207, 206, 205, 204] and len(calls) == 3
    w = assess_workflow(NIGHTLY_WF, runs, NOW)
    assert w is not None and not w.lower_bound and w.red_runs == 5
