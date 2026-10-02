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


def test_the_coverage_floor_is_enforced_only_by_the_full_suite_run():
    """fail_under applies to every coverage report, so every --cov run in any workflow must be the whole suite."""
    from py_ci_shared._toml_compat import tomllib

    report = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]["report"]
    assert report.get("fail_under", 0) >= 89, "the measured floor (TOTAL 90% at 6a8e382) is gone or lowered"
    cov_lines = [line.strip() for wf in WORKFLOWS for line in wf.read_text(encoding="utf-8").splitlines() if "--cov" in line and "pytest" in line]
    assert cov_lines, "no workflow measures coverage, so the floor is never enforced"
    narrow = [line for line in cov_lines if "pytest tests/ " not in line or re.search(r"\s-[km]\s|::", line.split("pytest", 1)[1])]
    assert not narrow, f"a --cov run over a subset would fail the floor it cannot reach: {narrow}"


def test_release_moves_the_major_tag_only_after_verification():
    data = _load(REPO / ".github" / "workflows" / "release.yml")
    assert data[True]["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    publish = data["jobs"]["publish"]
    assert publish["needs"] == "verify" and publish["permissions"] == {"contents": "write"}
    assert "git push -f origin" in "\n".join(str(s.get("run", "")) for s in publish["steps"])


def test_lint_blocking_lints_a_subproject_where_it_lives_and_the_workflows_at_the_root():
    """A monorepo subproject: the project checks run in `working-directory`, the workflow-file checks at the root."""
    wf = _load(REPO / ".github" / "workflows" / "lint-blocking.yml")
    inputs = wf[True]["workflow_call"]["inputs"]
    assert inputs["working-directory"]["default"] == "." and inputs["codespell-toml"]["default"] == "pyproject.toml"
    assert inputs["bandit-config"]["default"] == "" and inputs["bandit-exclude"]["default"] == ""
    job = wf["jobs"]["lint-blocking"]
    assert job["defaults"]["run"]["working-directory"] == "${{ inputs.working-directory }}"
    steps = {s.get("name"): s for s in job["steps"]}
    at_root = {n for n, s in steps.items() if s.get("working-directory") == "."}
    assert at_root == {"Actionlint (workflow files)", "Zizmor (workflow security scan)", "yamllint workflow files"}
    assert '--toml "$CODESPELL_TOML"' in steps["Codespell"]["run"]
    assert '-c "$BANDIT_CONFIG"' in steps["Bandit security scan"]["run"] and '-x "$BANDIT_EXCLUDE"' in steps["Bandit security scan"]["run"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_job_that_runs_this_package_installs_it_first(path):
    # config-drift-check.yml ran `python -m py_ci_shared.config_drift_check | tee ...` with nothing installed: every
    # run printed "No module named 'py_ci_shared'" and went green because the pipe took tee's exit status.
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job_name, job in (workflow.get("jobs") or {}).items():
        installed = False
        for step in job.get("steps") or []:
            run = str(step.get("run") or "")
            if re.search(r"""pip install\b[^\n]*(-e\s+["']?\.|py-ci-shared)""", run):
                installed = True
            if "python -m py_ci_shared." in run:
                assert installed, f"{path.name}:{job_name}: step {step.get('name')!r} runs py_ci_shared before any step installs it"
                if "|" in run:
                    assert step.get("shell") == "bash" or "pipefail" in run, f"{path.name}:{job_name}: a pipe hides the exit status"


# The first major of each action whose runs.using is node24 (for a composite, whose nested actions are all node24).
# GitHub forces Node 20 actions onto Node 24 and then removes Node 20, so a pin below its floor here is a dated break.
NODE24_FLOOR = {
    "actions/checkout": 5,
    "actions/setup-python": 6,
    "astral-sh/setup-uv": 7,
    "actions/upload-artifact": 6,
    "actions/download-artifact": 7,
    "actions/upload-pages-artifact": 5,
    "actions/deploy-pages": 5,
    "codecov/codecov-action": 7,
}
USES_LINE = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)(?:/[\w./-]+)?@([0-9a-f]{40})\s+#\s*v(\d+)\.\d+\.\d+(?:\s+#\s*zizmor: ignore\[[\w,-]+\])?\s*$")


def _third_party_uses() -> list[tuple[Path, str, str, int]]:
    found = []
    for path in [*WORKFLOWS, *ACTIONS]:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().removeprefix("- ")
            if not stripped.startswith("uses:") or "uses: ./" in stripped or "fingoldo/py-ci-shared" in stripped:
                continue
            m = USES_LINE.search(stripped)
            assert m, f"{path.name}: `{stripped}` is not a 40-char SHA with a full `# vX.Y.Z` comment"
            found.append((path, m.group(1), m.group(2), int(m.group(3))))
    return found


def test_every_action_is_on_its_node24_major_and_pinned_to_one_sha_repo_wide():
    uses = _third_party_uses()
    assert len(uses) >= 30, "the uses: scan found too few lines to be the real population"
    shas: dict[str, set[str]] = {}
    for path, action, sha, major in uses:
        assert action in NODE24_FLOOR, f"{path.name}: {action} has no reviewed Node 24 floor; check its action.yml and add one"
        assert major >= NODE24_FLOOR[action], f"{path.name}: {action} v{major} runs on Node 20 (node24 from v{NODE24_FLOOR[action]})"
        shas.setdefault(action, set()).add(sha)
    assert {a: s for a, s in shas.items() if len(s) > 1} == {}, "one action pinned to different SHAs in different files"


def test_no_job_runs_on_a_moving_ubuntu_label():
    """ubuntu-latest moved 24.04 -> 26.04 (actions/runner-images#14748); 26.04 has no Python 3.9 build."""
    labels = []
    for path in WORKFLOWS:
        for name, job in _load(path)["jobs"].items():
            runs_on = job.get("runs-on")
            if runs_on is None:
                assert "uses" in job, f"{path.name}:{name} has neither runs-on nor uses"
                continue
            labels.append(runs_on)
            assert "latest" not in str(runs_on), f"{path.name}:{name} runs on a moving label: {runs_on}"
    assert "ubuntu-24.04" in labels
    matrix = _load(REPO / ".github" / "workflows" / "self-ci.yml")["jobs"]["test"]["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-24.04"], "the blocking Linux legs (including 3.9) must stay on 24.04"


def test_self_ci_previews_ubuntu_26_04_without_blocking():
    job = _load(REPO / ".github" / "workflows" / "self-ci.yml")["jobs"]["test"]
    previews = [leg for leg in job["strategy"]["matrix"]["include"] if leg["os"] == "ubuntu-26.04"]
    assert previews and all(leg.get("preview") is True for leg in previews), "a 26.04 leg without preview: true would block"
    assert all(leg["python"] != "3.9" for leg in previews), "actions/python-versions has no 3.9 build for 26.04"
    assert job["continue-on-error"] == "${{ matrix.preview == true }}"
    run_tests = next(s for s in job["steps"] if s.get("name") == "Run tests")
    assert "matrix.os == 'ubuntu-24.04'" in run_tests["env"]["COVERAGE"], "coverage must come from exactly one blocking leg"


def test_upload_codecov_forwards_plugins_and_keeps_the_stock_default():
    action = _load(REPO / ".github" / "actions" / "upload-codecov" / "action.yml")
    assert action["inputs"]["plugins"]["default"] == "" and action["inputs"]["plugins"]["required"] is False
    assert "noop" in action["inputs"]["plugins"]["description"]
    (step,) = [s for s in action["runs"]["steps"] if str(s.get("uses", "")).startswith("codecov/codecov-action@")]
    assert step["with"]["plugins"] == "${{ inputs.plugins }}"
    assert step["with"]["files"] == "${{ inputs.files }}"
