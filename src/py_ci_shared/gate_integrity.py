"""Shared check: a gate that is DECLARED blocking must actually be able to block.

Across three audit waves of one consuming repo, the single highest-recurrence defect class
was not a code defect at all -- it was a check that existed, was called blocking, and did
not check. Three distinct mechanisms produced that outcome:

* **The gate never completed.** ``mypy src/pkg`` aborted with an ``INTERNAL ERROR`` inside a
  third-party stub and exited 2. The hook asserted an exit code and nothing else, so the
  set of errors mypy had managed to report before dying became a function of traversal
  order -- a "declared clean" gate whose findings were nondeterministic. An exit code is
  not evidence that a tool finished; only the tool's own success terminator is.
* **The gate ran a narrower scope than it claimed.** ``--ignore C901`` made 22 complexity
  findings permanently advisory; ``exclude = tests/`` hid 297 ruff findings. Both were
  deliberate. Neither was written down anywhere a reviewer or a check could see, so
  neither was ever revisited, and a NEW narrowing would have looked exactly the same.
* **The gate's threshold drifted below what it defended.** A ``--cov-fail-under`` sat 20
  points under actual coverage, so coverage could collapse by a fifth without the gate
  noticing.

This module is the general form of those three rules. It deliberately does NOT judge
whether a narrowing is correct -- only whether it was declared, with a reason, by a human.
That keeps the false-positive rate at zero at the cost of a one-time declaration pass, and
it means a newly-added narrowing fails the build on the commit that introduces it.

Usage (in a consuming repo's test suite)::

    from py_ci_shared.gate_integrity import assert_narrowings_declared

    assert_narrowings_declared(
        precommit_path=REPO_ROOT / ".pre-commit-config.yaml",
        workflows_dir=REPO_ROOT / ".github" / "workflows",
        declared={"pre-commit::ruff-real-bugs::--ignore=C901": "complexity is advisory; ..."},
    )

``pytest``/``yaml`` are imported lazily, matching this package's other modules.
"""

from __future__ import annotations

import re
from pathlib import Path

# Flags that NARROW what a gate inspects or lower the bar it enforces. Both the
# `--flag=value` and `--flag value` spellings are recognized; a bare short flag with no
# value (`-ll`) is captured as `-ll=` so it still needs a declaration.
_NARROWING_FLAGS = (
    "--ignore-glob",
    "--ignore",
    "--extend-select",
    "--select",
    "--deselect",
    "--exclude",
    "--extend-exclude",
    "--cov-fail-under",
    "--fail-under",
    "--min-confidence",
    "--skip",
    "--severity-level",
    "--confidence-level",
)
_NARROWING_FLAG_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(f) for f in _NARROWING_FLAGS) + r")(?:[=\s]+([^\s]+))?")
# Reusable-workflow inputs that narrow the called gate the same way a CLI flag would.
_NARROWING_INPUT_RE = re.compile(r"^\s*([\w-]*(?:ignore|select|exclude|fail-under|advisory)[\w-]*):\s*[\"']?(.*?)[\"']?\s*$", re.IGNORECASE)
_RUN_LINE_RE = re.compile(r"^\s*(?:-\s*)?run:\s*(.*)$")
_COV_FAIL_UNDER_RE = re.compile(r"--cov-fail-under[=\s]+([\d.]+)")


def _iter_precommit_hooks(precommit_path: Path):
    """Yield ``(hook_id, hook_mapping)`` for every hook in a pre-commit config."""
    import yaml

    config = yaml.safe_load(precommit_path.read_text(encoding="utf-8")) or {}
    for repo in config.get("repos", []) or []:
        for hook in repo.get("hooks", []) or []:
            yield str(hook.get("id", "<unnamed>")), hook


def _is_blocking(hook: dict) -> bool:
    """A hook is blocking unless it is manual-stage-only (opt-in, never runs on commit)."""
    stages = hook.get("stages")
    if not stages:
        return True
    return "manual" not in stages or len(stages) > 1


def _flag_narrowings(command: str) -> list[str]:
    return [f"{flag}={value or ''}" for flag, value in _NARROWING_FLAG_RE.findall(command)]


# Config keys that narrow a tool's scope or lower the bar it enforces, in a pyproject table.
_NARROWING_CONFIG_KEYS = ("exclude", "extend-exclude", "ignore", "extend-ignore", "select", "per-file-ignores", "fail_under", "fail-under", "ignore-init-module", "files")


def _precommit_narrowings(precommit_path: Path) -> dict[str, str]:
    """Narrowings on the argv and file-scoping keys of each BLOCKING pre-commit hook."""
    found: dict[str, str] = {}
    if not precommit_path.is_file():
        return found
    for hook_id, hook in _iter_precommit_hooks(precommit_path):
        if not _is_blocking(hook):
            continue
        entry = str(hook.get("entry", ""))
        for knob in _flag_narrowings(entry):
            found[f"pre-commit::{hook_id}::{knob}"] = entry.strip()
        for key in ("files", "exclude"):
            if hook.get(key):
                found[f"pre-commit::{hook_id}::{key}={hook[key]}"] = f"{key}: {hook[key]}"
    return found


def _workflow_narrowings(workflows_dir: Path) -> dict[str, str]:
    """Narrowings anywhere in a workflow file, including inside a ``run: |`` block scalar."""
    found: dict[str, str] = {}
    if not workflows_dir.is_dir():
        return found
    for workflow in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        # Fold shell line-continuations so a wrapped command is read as one invocation.
        text = re.sub(r"\\\s*\n\s*", " ", workflow.read_text(encoding="utf-8"))
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            # Every non-comment line, not just `run:` lines: a `run: |` block scalar puts the
            # actual command on the FOLLOWING lines, which is precisely where a
            # `--cov-fail-under` or an `--ignore` most often lives.
            for knob in _flag_narrowings(line):
                found[f"{workflow.name}::run::{knob}"] = line.strip()
            input_match = _NARROWING_INPUT_RE.match(line)
            # A `with:` input only narrows when it carries a value; `ignore: ""` is a no-op.
            if input_match and input_match.group(2) and input_match.group(2) not in ("false", "{}", "[]"):
                found[f"{workflow.name}::with::{input_match.group(1)}={input_match.group(2)}"] = line.strip()
    return found


def _pyproject_narrowings(pyproject_path: "Path | None", pyproject_tables: tuple[str, ...]) -> dict[str, str]:
    """Narrowings declared in the named pyproject tables -- the venue invisible from the other two."""
    found: dict[str, str] = {}
    if pyproject_path is None or not pyproject_path.is_file():
        return found
    import tomllib

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    for dotted in pyproject_tables:
        table = data
        for segment in dotted.split("."):
            table = table.get(segment, {}) if isinstance(table, dict) else {}
        if not isinstance(table, dict):
            continue
        for key in _NARROWING_CONFIG_KEYS:
            if key in table:
                found[f"pyproject::[{dotted}]::{key}"] = f"{key} = {table[key]!r}"
    return found


def find_narrowings(
    precommit_path: Path,
    workflows_dir: Path,
    pyproject_path: Path | None = None,
    pyproject_tables: tuple[str, ...] = (),
) -> dict[str, str]:
    """Return ``{narrowing_key: evidence}`` for every scope-narrowing or bar-lowering
    knob on a blocking gate, across all three venues where one can hide.

    Keys are ``"<venue>::<gate>::<knob>"`` -- stable across reformatting and line
    insertion (unlike a ``path:line`` anchor), so a declaration written once stays valid.
    Manual-stage-only pre-commit hooks are skipped: they are opt-in helpers, not gates.

    Args:
        precommit_path: the pre-commit config; each blocking hook's ``entry`` argv plus its
            ``files``/``exclude`` scoping keys are inspected.
        workflows_dir: GitHub Actions workflows; every non-comment line is inspected, since
            the narrowing that matters most often sits inside a ``run: |`` block scalar
            rather than on the ``run:`` line itself.
        pyproject_path: optional; the third venue, and the one that hid the largest
            narrowing in the motivating audit -- ``exclude = ["tests"]`` under ``[tool.ruff]``
            is invisible in both of the other two files while suppressing hundreds of findings.
        pyproject_tables: dotted table paths to inspect, e.g. ``("tool.ruff", "tool.mypy")``.
            Only the caller knows which tables configure a BLOCKING gate in its repo.
    """
    found: dict[str, str] = {}
    found.update(_precommit_narrowings(precommit_path))
    found.update(_workflow_narrowings(workflows_dir))
    found.update(_pyproject_narrowings(pyproject_path, pyproject_tables))
    return found


def find_undeclared_narrowings(
    precommit_path: Path,
    workflows_dir: Path,
    declared: dict[str, str],
    pyproject_path: Path | None = None,
    pyproject_tables: tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    """Return ``(undeclared, stale)``: narrowings present but not declared, and
    declarations naming a narrowing that no longer exists.

    Both directions matter. An undeclared narrowing is a gate quietly shrinking; a stale
    declaration is a reason nobody has re-read since the thing it justified was removed,
    which is how an allowlist becomes a place findings go to be forgotten.
    """
    present = find_narrowings(precommit_path, workflows_dir, pyproject_path, pyproject_tables)
    undeclared = sorted(f"{key}   <- {evidence}" for key, evidence in present.items() if key not in declared)
    stale = sorted(key for key in declared if key not in present)
    return undeclared, stale


def assert_narrowings_declared(
    precommit_path: Path,
    workflows_dir: Path,
    declared: dict[str, str],
    pyproject_path: Path | None = None,
    pyproject_tables: tuple[str, ...] = (),
) -> None:
    """Fail if any blocking gate narrows its scope without a written, in-repo reason."""
    import pytest

    undeclared, stale = find_undeclared_narrowings(precommit_path, workflows_dir, declared, pyproject_path, pyproject_tables)
    problems = []
    if undeclared:
        problems.append(
            f"{len(undeclared)} UNDECLARED narrowing(s) on a blocking gate -- add each key to the "
            f"declaration map with a one-line reason, or remove the narrowing:\n  " + "\n  ".join(undeclared)
        )
    if stale:
        problems.append(f"{len(stale)} STALE declaration(s) naming a narrowing that no longer exists -- delete them:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n\n".join(problems))


def find_gates_without_completion_assertion(precommit_path: Path, completion_wrapped_tools: dict[str, str]) -> list[str]:
    """Return one message per blocking hook that invokes a completion-ambiguous tool directly.

    Args:
        precommit_path: the pre-commit config to inspect.
        completion_wrapped_tools: ``{tool_invocation: required_wrapper_invocation}`` -- e.g.
            ``{"python -m mypy": "python -m py_ci_shared.mypy_gate"}``. A tool lands in this
            map when exit code 0 is not proof it finished: mypy exits non-zero on an internal
            crash but the crash can also truncate an otherwise-passing run, and any tool that
            can be silently short-circuited by a plugin/stub failure has the same shape.
    """
    violations: list[str] = []
    for hook_id, hook in _iter_precommit_hooks(precommit_path):
        if not _is_blocking(hook):
            continue
        entry = str(hook.get("entry", ""))
        for raw_invocation, wrapper in completion_wrapped_tools.items():
            if raw_invocation in entry and wrapper not in entry:
                violations.append(
                    f"pre-commit hook {hook_id!r} runs {raw_invocation!r} directly: exit code 0 alone does not "
                    f"prove the tool ran to completion (an internal error or an aborted traversal can leave the "
                    f"gate silently disarmed). Route it through {wrapper!r}, which requires the tool's own "
                    f"success terminator."
                )
    return violations


def assert_blocking_gates_assert_completion(precommit_path: Path, completion_wrapped_tools: dict[str, str]) -> None:
    """Fail if a blocking gate can pass without having run to completion."""
    import pytest

    violations = find_gates_without_completion_assertion(precommit_path, completion_wrapped_tools)
    if violations:
        pytest.fail(f"{len(violations)} blocking gate(s) assert only an exit code:\n  " + "\n  ".join(violations))


def find_coverage_gate_mismatches(pyproject_path: Path, workflows_dir: Path) -> list[str]:
    """Return a message per ``--cov-fail-under`` in CI that disagrees with pyproject's
    ``[tool.coverage.report] fail_under``.

    Two venues, one policy: when they desync, the lower one is the real gate and the higher
    one is decoration. Keeping them equal makes raising the ratchet a single deliberate edit.
    """
    import tomllib

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    declared = data.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under")
    if declared is None:
        return []
    violations: list[str] = []
    for workflow in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        violations.extend(
            f"{workflow.name}: --cov-fail-under={value} but pyproject [tool.coverage.report] fail_under={declared}"
            for value in _COV_FAIL_UNDER_RE.findall(workflow.read_text(encoding="utf-8"))
            if float(value) != float(declared)
        )
    return violations


def assert_coverage_gate_parity(pyproject_path: Path, workflows_dir: Path) -> None:
    """Fail if the CI coverage floor and the in-project coverage floor disagree."""
    import pytest

    violations = find_coverage_gate_mismatches(pyproject_path, workflows_dir)
    if violations:
        pytest.fail(f"{len(violations)} coverage-gate mismatch(es):\n  " + "\n  ".join(violations))
