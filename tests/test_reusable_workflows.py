"""This repo's own workflows: least privilege, a timeout on every job, pinned actions and tools, pinned self-fetches."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from py_ci_shared.ci_workflow_gate import assert_continue_on_error_is_reviewed, find_continue_on_error_steps
from py_ci_shared.ci_workflow_timeout_gate import assert_all_jobs_have_timeout

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
ACTIONS = sorted((REPO / ".github" / "actions").glob("*/action.yml"))
REUSABLE = [p for p in WORKFLOWS if "workflow_call" in (yaml.safe_load(p.read_text(encoding="utf-8"))[True] or {})]
SHA_PIN = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")

# Every step lint-advisory.yml runs as advisory, by name. A new continue-on-error step there fails until it is added here.
LINT_ADVISORY_REVIEWED = {
    "Run ruff (full - advisory)",
    "Ruff mccabe complexity (advisory)",
    "pip-audit dependency vulnerability scan",
    "Import-linter (architectural boundaries, advisory)",
    "pydoclint (docstring-vs-signature consistency, advisory)",
    "Semgrep (custom org-specific rules, advisory)",
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_population_is_what_this_repo_ships():
    names = {p.name for p in WORKFLOWS}
    assert {"self-ci.yml", "release.yml", "ruff-blocking.yml", "lint-advisory.yml", "black-filtered.yml"} <= names
    assert len(REUSABLE) >= 7, [p.name for p in REUSABLE]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_declares_least_privilege_permissions(path: Path):
    data = _load(path)
    assert "permissions" in data, f"{path.name}: no top-level permissions:, so every job inherits the caller's token scope"
    top = data["permissions"]
    assert top in ({}, "read-all") or (
        isinstance(top, dict) and set(top.values()) <= {"read", "none"}
    ), f"{path.name}: top-level permissions {top!r} grant writes; grant them per job instead"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_has_a_timeout(path: Path):
    assert_all_jobs_have_timeout(path)


@pytest.mark.parametrize("path", WORKFLOWS + ACTIONS, ids=lambda p: p.parent.name + "/" + p.name)
def test_every_third_party_action_is_pinned_to_a_full_sha(path: Path):
    text = path.read_text(encoding="utf-8")
    refs = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", text, re.MULTILINE)
    loose = [r for r in refs if not r.startswith("./") and not SHA_PIN.match(r)]
    assert not loose, f"{path.name}: action refs not pinned to a 40-hex SHA: {loose}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_py_ci_shared_is_never_fetched_at_master_by_default(path: Path):
    """Configs and code come from the ref the caller pinned; master is only a logged fallback when that ref is gone."""
    for step_text in re.split(r"\n\s*- (?:name|uses):", path.read_text(encoding="utf-8")):
        if "fingoldo/py-ci-shared.git" not in step_text:
            continue
        assert "PCS_REF" in step_text, f"{path.name}: a step fetches py-ci-shared without inputs.py-ci-shared-ref:\n{step_text[:400]}"
        assert "git clone --depth 1 https://github.com/fingoldo/py-ci-shared.git" not in step_text


def test_lint_advisory_installs_every_tool_at_an_exact_version():
    text = (REPO / ".github" / "workflows" / "lint-advisory.yml").read_text(encoding="utf-8")
    for tool in ("pip-audit", "import-linter", "pydoclint", "semgrep"):
        installs = re.findall(rf"(?:uvx|uv pip install --system)\s+\"?{tool}\b[^\s\"]*", text)
        assert installs, f"no install of {tool} found; the check has lost its subject"
        assert all("==" in i for i in installs), f"{tool} is installed without an exact pin: {installs}"


def test_lint_advisory_advisory_steps_are_exactly_the_reviewed_set():
    path = REPO / ".github" / "workflows" / "lint-advisory.yml"
    assert set(find_continue_on_error_steps(path)) == LINT_ADVISORY_REVIEWED
    assert_continue_on_error_is_reviewed(path, LINT_ADVISORY_REVIEWED)


def test_self_ci_covers_the_python_floor_and_installs_dev_without_a_fallback():
    data = _load(REPO / ".github" / "workflows" / "self-ci.yml")
    job = data["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    assert {"3.9", "3.10", "3.11", "3.12", "3.13"} <= set(matrix["python"]), "requires-python >=3.9 must be tested on 3.9 upward"
    install = next(s for s in job["steps"] if s.get("name") == "Install package")
    assert "||" not in install["run"] and '".[dev]"' in install["run"], "a failed dev install must fail the job, not fall back"
    runs = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "py-ci-shared run-all" in runs, "the dogfood step is gone"
    assert "-ra" in runs and "--cov" in runs
    assert "PG_BIN=" in runs, "without PG_BIN the embedded-Postgres tests skip on the runners"


def test_release_moves_the_major_tag_only_after_verification():
    data = _load(REPO / ".github" / "workflows" / "release.yml")
    assert data[True]["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    publish = data["jobs"]["publish"]
    assert publish["needs"] == "verify" and publish["permissions"] == {"contents": "write"}
    assert "git push -f origin" in "\n".join(str(s.get("run", "")) for s in publish["steps"])
