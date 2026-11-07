"""A caller-supplied install command reaches a step through the environment, never expanded into the script text (zizmor template-injection)."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def test_no_run_step_expands_an_install_command_input_into_its_script():
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (data.get("jobs") or {}).items():
            for step in job.get("steps", []) or []:
                run = str(step.get("run", ""))
                if "${{" in run and "install-command" in run:
                    offenders.append(f"{path.name}:{job_name}: {step.get('name')}")
    assert offenders == [], f'pass the input through env (INSTALL_COMMAND) and run bash -eo pipefail -c "$INSTALL_COMMAND": {offenders}'
    mypy_full = yaml.safe_load((WORKFLOWS / "mypy-full.yml").read_text(encoding="utf-8"))
    runs = [str(s.get("run", "")) for j in mypy_full["jobs"].values() for s in j["steps"]]
    assert 'bash -eo pipefail -c "$INSTALL_COMMAND"' in runs
