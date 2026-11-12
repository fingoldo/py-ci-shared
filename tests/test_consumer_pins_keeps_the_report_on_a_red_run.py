"""The consumer-pins matrix step keeps its report on a red run (audit 47-F13).

A matrix failure used to abort the step before the "Not checked" section was appended, so a red run dropped that section from the
summary and the artifact. The step now records the matrix's exit code, appends the sections, and exits with that code last.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "consumer-pins.yml"


def _matrix_script() -> list[str]:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    steps = next(iter(jobs.values()))["steps"]
    (step,) = [s for s in steps if s.get("name") == "Adoption matrix"]
    return [line.strip() for line in step["run"].splitlines() if line.strip() and not line.strip().startswith("#")]


def test_the_matrix_exit_code_is_kept_and_returned_after_the_sections_are_appended() -> None:
    lines = _matrix_script()
    assert "set +e" in lines, "the matrix's exit code must be kept, so the step does not abort before the sections"
    matrix = next(i for i, line in enumerate(lines) if "adoption_matrix" in line)
    assert "rc=$?" in lines[matrix : matrix + 3], "the matrix's exit code must be saved right after its (continued) command"
    not_checked = next(i for i, line in enumerate(lines) if "not-checked" in line)
    assert matrix < not_checked, "the Not checked section must be appended after the matrix runs"
    assert lines[-1] == 'exit "$rc"', "the step must end by returning the matrix's own exit code"


def test_the_matrix_is_not_allowed_to_abort_the_step_before_the_sections() -> None:
    script = "\n".join(_matrix_script())
    assert "set +e" in script and "rc=$?" in script, "a bare matrix invocation aborts the step on a red run, before the sections are written"
