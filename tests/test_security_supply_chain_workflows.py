"""Audit 2026-11-07 51-F2/F3/F4/F5/F6: workflow hygiene around pinned tools, refs and input interpolation."""

import os
import re
import subprocess
from pathlib import Path

import pytest
from _working_bash import working_bash
import yaml

from py_ci_shared import tool_versions

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
ACTION = ROOT / ".github" / "actions" / "install-pyutilz" / "action.yml"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(doc):
    jobs = doc["jobs"].values() if "jobs" in doc else []
    return [s for j in jobs for s in j.get("steps", [])] or doc["runs"]["steps"]


def test_lint_blocking_defaults_equal_tool_versions():
    inputs = _load(WF / "lint-blocking.yml")[True]["workflow_call"]["inputs"]
    expected = {
        "actionlint-version": tool_versions.ACTIONLINT_PY_VERSION,
        "zizmor-version": tool_versions.ZIZMOR_VERSION,
        "codespell-version": tool_versions.CODESPELL_VERSION,
        "yamllint-version": tool_versions.YAMLLINT_VERSION,
        "bandit-version": tool_versions.BANDIT_VERSION,
        "vulture-version": tool_versions.VULTURE_VERSION,
        "interrogate-version": tool_versions.INTERROGATE_VERSION,
        "deptry-version": tool_versions.DEPTRY_VERSION,
    }
    assert {k: inputs[k]["default"] for k in expected} == expected


def _unpinned_install_lines(workflow: Path) -> list[str]:
    """Install lines whose package is not pinned. A line may name a shell variable (``uvx "$INTERROGATE"``) only when that variable is
    assigned a pinned package (``INTERROGATE="interrogate==$PIN"``): the variable is what gets installed, so the assignment is the pin (44-F1)."""
    text = workflow.read_text(encoding="utf-8")
    bad: list[str] = []
    for step in _steps(_load(workflow)):
        for line in step.get("run", "").splitlines():
            stripped = line.strip()
            if not re.search(r"\buvx\b|\bpip install\b|\buv pip install\b|\buv tool\b|\bpipx\b", stripped) or "==" in stripped:
                continue
            pinned = False
            for var in re.findall(r"\$\{?([A-Z_]+)\}?", stripped):
                assigned = re.search(rf'^\s*{var}="([^"]*)"', text, re.MULTILINE)
                if assigned is not None and "==" in assigned.group(1):
                    pinned = True
            if not pinned:
                bad.append(stripped)
    return bad


def test_lint_blocking_has_no_unpinned_install_path():
    text = (WF / "lint-blocking.yml").read_text(encoding="utf-8")
    assert not re.search(r'if \[ -n "\$PIN" \]', text)
    assert _unpinned_install_lines(WF / "lint-blocking.yml") == []
    assert "Require pinned tool versions" in text


def test_a_variable_assigned_an_unpinned_package_is_reported(tmp_path) -> None:
    """Negative control (44-F1): the old exemption passed `uvx "$INTERROGATE"` whatever INTERROGATE held."""
    bad = tmp_path / "wf.yml"
    bad.write_text('jobs:\n  j:\n    steps:\n      - run: |\n          INTERROGATE="interrogate"\n          uvx "$INTERROGATE" -v src\n', encoding="utf-8")
    assert _unpinned_install_lines(bad) == ['uvx "$INTERROGATE" -v src']


@pytest.mark.parametrize("install", ["pip install ruff", "uv tool install ruff", "pipx install black", "uv pip install ruff"])
def test_an_unpinned_install_in_any_shape_is_reported(tmp_path, install) -> None:
    """Negative control (44-F2): the old pattern saw only uvx and uv pip install, so these never reached the pin check."""
    bad = tmp_path / "wf.yml"
    bad.write_text("jobs:\n  j:\n    steps:\n      - run: |\n          " + install + "\n", encoding="utf-8")
    assert _unpinned_install_lines(bad) == [install]


@pytest.mark.parametrize("name", ["docs.yml", "mypy-full.yml", "lint-blocking.yml"])
def test_install_command_inputs_never_interpolated_into_run(name):
    for step in _steps(_load(WF / name)):
        run = step.get("run", "")
        assert not re.search(r"\$\{\{\s*inputs\.[\w-]*install-command", run), step.get("name")


@pytest.mark.parametrize("path", [*sorted(WF.glob("*.yml")), ACTION])
def test_no_action_path_interpolated_into_run(path):
    steps = _steps(_load(path))
    assert steps
    for step in steps:
        assert "github.action_path" not in step.get("run", ""), (path.name, step.get("name"))


def _pin_script():
    for step in _steps(_load(ACTION)):
        if step.get("name") == "Pin pyutilz to a specific commit":
            return step["run"]
    raise AssertionError("pin step missing")


@pytest.mark.parametrize("ref", ["", "master", "--upload-pack=touch x", "abc123", "A" * 40, "a" * 39, "a" * 41])
def test_install_pyutilz_refuses_anything_but_a_full_sha(ref, tmp_path):
    proc = subprocess.run(
        [working_bash(), "-c", _pin_script()],
        cwd=tmp_path,
        env={"INPUTS_PYUTILZ_REF": ref, "PATH": "/usr/bin:/bin", **({"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {})},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "40-hex commit SHA" in proc.stdout
    assert not (tmp_path / "x").exists()


def test_install_pyutilz_script_uses_option_terminators():
    script = _pin_script()
    assert 'fetch --depth 1 origin -- "' in script
    assert "checkout --detach" in script
    assert "rev-parse HEAD" in script
