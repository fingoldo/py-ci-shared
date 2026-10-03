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


# The three reusable workflows that fetch or install py-ci-shared itself, and the step that does it.
SELF_FETCH = {
    "ruff-blocking.yml": "Resolve PY_CI_SHARED_DIR",
    "lint-advisory.yml": "Resolve PY_CI_SHARED_DIR",
    "black-filtered.yml": "Install py-ci-shared",
}


def test_py_ci_shared_is_fetched_only_at_the_pinned_ref_and_never_at_master():
    """Configs and code come from the ref the caller pinned. The master fallback (audit 2026-10-03 WF-4) is gone, and
    the step scan has a floor: it used to skip every step and pass if the URL spelling changed (WF-15)."""
    fetching = {}
    for path in WORKFLOWS:
        for step in (step for job in _load(path)["jobs"].values() for step in job.get("steps") or []):
            run = str(step.get("run") or "")
            if "github.com/fingoldo/py-ci-shared" in run:
                fetching[path.name] = step.get("name")
                assert "${PCS_REF}" in run, f"{path.name}: {step.get('name')} fetches py-ci-shared without inputs.py-ci-shared-ref"
                commands = "\n".join(line for line in run.splitlines() if not line.strip().startswith(("echo", "#")))
                assert not re.search(r"py-ci-shared\.git\"|\bmaster\b", commands), f"{path.name}: {step.get('name')} can fetch master"
                assert "exit 1" in run, f"{path.name}: {step.get('name')} does not fail when the ref cannot be fetched"
    assert fetching == SELF_FETCH


# --- the fetch steps, executed (audit 2026-10-03 WF-4/WF-5) --------------------------------------------------------


def _bash() -> str:
    """A POSIX bash: the runners' own on Linux/macOS, Git's on Windows (never WSL's System32 launcher)."""
    import shutil

    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    git = shutil.which("git")
    if git:
        for candidate in (Path(git).parents[1] / "bin" / "bash.exe", Path(git).parents[1] / "usr" / "bin" / "bash.exe"):
            if candidate.exists():
                return str(candidate)
    pytest.skip("no bash to run the workflow step with")
    raise AssertionError  # unreachable, for type checkers


def _step(workflow: str, name: str) -> dict:
    steps = [s for job in _load(REPO / ".github" / "workflows" / workflow)["jobs"].values() for s in job["steps"]]
    (step,) = [s for s in steps if s.get("name") == name]
    return step


# Stand-ins for git/uv/uvx/sleep: each records its argv; a command fails when its argv matches $FAIL_PATTERN.
FAKES = r"""
_fake() {
  echo "$*" >> "$CALLS"
  if [ -n "$FAIL_PATTERN" ] && echo "$*" | grep -Eq "$FAIL_PATTERN"; then
    if [ -z "${FAIL_ONCE:-}" ]; then return 1; fi
    if [ ! -e "$FAIL_ONCE" ]; then touch "$FAIL_ONCE"; return 1; fi
  fi
  return 0
}
git() { _fake git "$@"; }
uv() { _fake uv "$@"; }
uvx() { _fake uvx "$@"; }
sleep() { _fake sleep "$@"; }
"""


def _run_step(tmp_path: Path, workflow: str, name: str, *, fail: str = "", repo: str = "fingoldo/consumer", **env: str):
    import subprocess

    step = _step(workflow, name)
    script = tmp_path / "step.sh"
    script.write_text(FAKES + step["run"], encoding="utf-8", newline="\n")
    calls, github_env = tmp_path / "calls.txt", tmp_path / "github_env"
    calls.write_text("", encoding="utf-8")
    github_env.write_text("", encoding="utf-8")
    import os

    full_env = {
        **os.environ,
        "CALLS": str(calls),
        "FAIL_PATTERN": fail,
        "GITHUB_ENV": str(github_env),
        "GITHUB_REPOSITORY": repo,
        "GITHUB_WORKSPACE": str(tmp_path / "ws"),
        "RUNNER_TEMP": str(tmp_path / "rt"),
        "PCS_REF": "v1.20.0",
        "FORCE_REMOTE": "false",
        "FAIL_ONCE": "",
        **env,
    }
    proc = subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", str(script)], env=full_env, capture_output=True, text=True, timeout=60)
    lines = calls.read_text(encoding="utf-8").splitlines()
    return proc, lines, github_env.read_text(encoding="utf-8")


@pytest.mark.parametrize("workflow", ["ruff-blocking.yml", "lint-advisory.yml"])
def test_a_pinned_ref_that_cannot_be_fetched_fails_after_retries_and_never_fetches_master(tmp_path: Path, workflow: str):
    proc, calls, github_env = _run_step(tmp_path, workflow, SELF_FETCH[workflow], fail=r"^git .* fetch ")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fetches = [c for c in calls if " fetch " in c]
    assert len(fetches) == 3 and all(c.endswith(" v1.20.0") for c in fetches), calls
    assert not any("master" in c for c in calls), calls
    assert "::error::py-ci-shared ref v1.20.0 could not be fetched" in proc.stdout
    assert github_env == "", "no PY_CI_SHARED_DIR may be exported when the fetch failed"


@pytest.mark.parametrize("workflow", ["ruff-blocking.yml", "lint-advisory.yml"])
def test_a_transient_fetch_failure_is_retried_and_succeeds(tmp_path: Path, workflow: str):
    proc, calls, github_env = _run_step(
        tmp_path, workflow, SELF_FETCH[workflow], fail=r"^git .* fetch ", FAIL_ONCE=str(tmp_path / "failed_once"), PCS_REF="abc123"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fetches = [c for c in calls if " fetch " in c]
    assert len(fetches) == 2 and all(c.endswith(" abc123") for c in fetches), calls
    assert github_env.startswith("PY_CI_SHARED_DIR=") and "attempt 1 of 3" in proc.stdout


def test_black_filtered_fails_when_the_pinned_ref_cannot_be_installed(tmp_path: Path):
    proc, calls, _ = _run_step(tmp_path, "black-filtered.yml", "Install py-ci-shared", fail=r"^uv pip install")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    installs = [c for c in calls if c.startswith("uv pip install")]
    assert len(installs) == 3 and all(c.endswith("py-ci-shared.git@v1.20.0") for c in installs), calls
    assert "::error::py-ci-shared ref v1.20.0 could not be installed" in proc.stdout


@pytest.mark.parametrize("workflow", sorted(SELF_FETCH))
def test_inside_this_repo_the_local_checkout_is_used_unless_a_remote_fetch_is_forced(tmp_path: Path, workflow: str):
    proc, calls, _ = _run_step(tmp_path, workflow, SELF_FETCH[workflow], repo="fingoldo/py-ci-shared")
    assert proc.returncode == 0 and not any("github.com" in c for c in calls), calls
    forced, forced_calls, _ = _run_step(tmp_path, workflow, SELF_FETCH[workflow], repo="fingoldo/py-ci-shared", FORCE_REMOTE="true", PCS_REF="deadbeef")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert any("github.com/fingoldo/py-ci-shared" in c and "deadbeef" in c for c in forced_calls), forced_calls


@pytest.mark.parametrize("workflow", sorted(SELF_FETCH))
def test_self_ci_runs_the_consumer_fetch_path_of_each_self_fetching_workflow(workflow: str):
    jobs = _load(REPO / ".github" / "workflows" / "self-ci.yml")["jobs"]
    remote = [j for j in jobs.values() if j.get("uses") == f"./.github/workflows/{workflow}" and (j.get("with") or {}).get("force-remote-fetch") is True]
    assert remote, f"no self-ci job calls {workflow} with force-remote-fetch: true"
    assert all("github.sha" in str(j["with"]["py-ci-shared-ref"]) for j in remote), "the remote fetch must test this commit"
    inputs = _load(REPO / ".github" / "workflows" / workflow)[True]["workflow_call"]["inputs"]
    assert inputs["force-remote-fetch"] == {"description": inputs["force-remote-fetch"]["description"], "type": "boolean", "default": False}


# --- pip-audit audits the calling project (audit 2026-10-03 WF-1) --------------------------------------------------


def test_pip_audit_audits_the_calling_project_by_default(tmp_path: Path):
    proc, calls, _ = _run_step(tmp_path, "lint-advisory.yml", "pip-audit dependency vulnerability scan", PIP_AUDIT_VERSION="2.10.1", PIP_AUDIT_REQUIREMENTS="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (call,) = [c for c in calls if c.startswith("uvx ")]
    assert call.split()[1] == "pip-audit==2.10.1" and call.split()[-1] == ".", f"pip-audit has no project target: {call}"


def test_pip_audit_audits_the_given_requirements_files(tmp_path: Path):
    proc, calls, _ = _run_step(
        tmp_path, "lint-advisory.yml", "pip-audit dependency vulnerability scan", PIP_AUDIT_VERSION="2.10.1", PIP_AUDIT_REQUIREMENTS="req.txt dev.txt"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (call,) = [c for c in calls if c.startswith("uvx ")]
    assert call.endswith("-r req.txt -r dev.txt") and " . " not in f"{call} ", call


# --- workflow hygiene (audit 2026-10-03 WF-14) ---------------------------------------------------------------------

CONSTRAINTS = REPO / ".github" / "constraints" / "runtime.txt"


def test_the_constraints_file_pins_every_runtime_dependency_exactly():
    from py_ci_shared._toml_compat import tomllib

    deps = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    names = {re.split(r"[<>=!~;\[ ]", d, maxsplit=1)[0].lower() for d in deps}
    pinned = {}
    for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, rest = line.partition("==")
            pinned[name.strip().lower()] = rest.split(";")[0].strip()
    assert names and names <= set(pinned), f"runtime dependencies without an exact pin in {CONSTRAINTS.name}: {names - set(pinned)}"
    assert all(re.fullmatch(r"\d+(\.\d+)+", v) for v in pinned.values()), pinned


@pytest.mark.parametrize("name", ["ci-health.yml", "consumer-pins.yml", "config-drift-check.yml", "corpus-drift.yml", "release.yml"])
def test_scheduled_reports_and_the_release_install_with_the_constraints(name: str):
    runs = [str(s.get("run", "")) for job in _load(REPO / ".github" / "workflows" / name)["jobs"].values() for s in job.get("steps") or []]
    installs = [r for r in runs if re.search(r"pip install\b", r)]
    assert installs, f"{name}: no install step found; the check lost its subject"
    assert all("-c .github/constraints/runtime.txt" in r for r in installs), f"{name}: an install without the constraints file: {installs}"


@pytest.mark.parametrize("path", [p for p in WORKFLOWS if "schedule" in (yaml.safe_load(p.read_text(encoding="utf-8"))[True] or {})], ids=lambda p: p.name)
def test_every_scheduled_workflow_has_a_concurrency_group(path: Path):
    assert "concurrency" in _load(path), f"{path.name}: a slow scheduled run and a manual one can overlap"


def test_corpus_drift_takes_its_baseline_from_master_and_skips_expired_artifacts():
    run = str(_step("corpus-drift.yml", "Find the last successful run")["run"])
    assert "status=success&branch=master" in run, "a dispatch from another branch must not become master's baseline"
    assert "select(.expired == false)" in run, "an expired snapshot artifact failed the download every night"


def test_black_filtered_reads_the_black_version_from_tool_versions():
    text = (REPO / ".github" / "workflows" / "black-filtered.yml").read_text(encoding="utf-8")
    assert "from py_ci_shared.tool_versions import BLACK_VERSION" in text
    assert not re.search(r"black==\d", text), "a literal black version can drift from tool_versions.BLACK_VERSION"


# The only moving labels: self-ci's single Windows and macOS legs (see the comment on them in self-ci.yml).
ALLOWED_LATEST = {("self-ci.yml", "windows-latest"), ("self-ci.yml", "macos-latest")}


def test_moving_runner_labels_are_only_the_reviewed_ones():
    found = set()
    for path in WORKFLOWS:
        for job in _load(path)["jobs"].values():
            labels = [job.get("runs-on")]
            matrix = (job.get("strategy") or {}).get("matrix") or {}
            labels += list(matrix.get("os") or []) + [leg.get("os") for leg in matrix.get("include") or []]
            found |= {(path.name, str(label)) for label in labels if label and "latest" in str(label)}
    assert found == ALLOWED_LATEST


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
