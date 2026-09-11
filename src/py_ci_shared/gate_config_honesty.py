"""Shared check: a gate runs its tool the way the project configured it, and a "blocking" gate can block.

``gate_integrity`` asks whether a declared-blocking gate COMPLETES and whether its narrowings are
declared. This asks the two questions production_scrapers' bandit gate failed for weeks (2026-09-12):

* **The tool ran without the project's config.** The hook called ``bandit -ll`` with no ``-c
  pyproject.toml``, so the ``[tool.bandit]`` skips a July triage had written never applied there; it
  reported 79 "findings" that the project had already classified, and nobody read them. Under the
  config the count was zero. A tool whose config lives in a table it does not read by itself must be
  handed that table on every invocation.
* **The gate cannot fail.** The same hook ran through a ``*_warn`` wrapper that always exits 0, and a
  hook or step can equally be defeated by ``|| true``. That is a legitimate advisory gate only when it
  SAYS so in its id, alias or name; otherwise it is a blocking gate that never blocks.

Line-level: commands are read from every hook ``entry`` (plus ``args``) in a pre-commit config and
every ``run:`` in the workflows, optionally scoped to one project by a path fragment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

#: tool -> (pyproject table that holds its config, flags that pass it). Only tools that do NOT find
#: pyproject.toml by themselves belong here; ruff and mypy do.
DEFAULT_TOOLS: Mapping[str, tuple[str, tuple[str, ...]]] = {"bandit": ("bandit", ("-c", "--configfile"))}
_ADVISORY_WORDS = ("warn", "advisory", "report", "informational")
#: `--exit-zero` is the tool's own way of never failing (ruff, flake8, pylint): a gate carrying it in its
#: `args` passes whatever it finds, exactly like `|| true`, and reads as a blocking hook.
_ALWAYS_ZERO = re.compile(r"\|\|\s*true\b|;\s*exit\s+0\b|\bpy_ci_shared\.\w*_warn\b|(?<!\S)--exit-zero\b")
#: What makes a workflow step a GATE: a shell line in a workflow is often plumbing (`git fetch ... || true`),
#: so `|| true` there counts only on a line that runs one of these.
_GATE_TOOL = re.compile(r"\b(?:pytest|ruff|mypy|bandit|black|flake8|pylint|pre-commit|vulture|interrogate|codespell|py_ci_shared)\b")


def _invokes(tool: str, command: str) -> bool:
    """Does *command* RUN *tool* -- in command position, bare or via `python -m` / `uvx` -- rather than install or name it?"""
    run = re.compile(rf"(?:^|[\s;&|(]|&&)(?:\S*python\S*\s+-m\s+|uvx\s+(?:--from\s+\S+\s+)?)?{re.escape(tool)}(?=\s|$)")
    return any(run.search(line) and not re.search(r"\b(?:install|uninstall|add)\b", line) for line in command.splitlines())


def _mentions(scope: "str | None", *texts: str) -> bool:
    return scope is None or any(scope in t for t in texts)


def gate_commands(precommit_path: "Path | None", workflows: Iterable[Path], *, scope: "str | None" = None) -> dict[str, tuple[str, str]]:
    """``{label: (command, the gate's own name text)}`` for every hook and workflow step, within *scope*."""
    import yaml

    found: dict[str, tuple[str, str]] = {}
    if precommit_path is not None and precommit_path.is_file():
        data = yaml.safe_load(precommit_path.read_text(encoding="utf-8")) or {}
        for repo in data.get("repos", []) or []:
            for hook in repo.get("hooks", []) or []:
                if "manual" in (hook.get("stages") or []) and len(hook.get("stages") or []) == 1:
                    continue
                command = " ".join([str(hook.get("entry", "")), *map(str, hook.get("args", []) or [])]).strip()
                names = " ".join(str(hook.get(k, "")) for k in ("id", "alias", "name"))
                if command and _mentions(scope, str(hook.get("files", "")), command):
                    found[f"pre-commit::{hook.get('alias') or hook.get('id')}"] = (command, names)
    for workflow in workflows:
        data = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
        for job_id, job in (data.get("jobs") or {}).items():
            for step in (job or {}).get("steps", []) or []:
                run = str(step.get("run", "") or "")
                if not run.strip():
                    continue
                name = str(step.get("name", "") or run.splitlines()[0])
                advisory = " advisory" if step.get("continue-on-error") else ""
                found[f"{workflow.name}::{job_id}::{name}"] = (run, name + advisory)
    return found


def find_tools_run_without_their_config(
    commands: Mapping[str, tuple[str, str]], pyproject: Path, *, tools: Mapping[str, tuple[str, tuple[str, ...]]] = DEFAULT_TOOLS
) -> list[str]:
    """Commands that invoke a configured tool without passing its config."""
    from ._toml_compat import tomllib

    config = tomllib.loads(pyproject.read_text(encoding="utf-8")) if pyproject.is_file() else {}
    configured = {tool for tool, (table, _flags) in tools.items() if table in (config.get("tool") or {})}
    problems: list[str] = []
    for label, (command, _names) in sorted(commands.items()):
        for tool in sorted(configured):
            if not _invokes(tool, command):
                continue
            flags = tools[tool][1]
            if not any(re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", command) for f in flags):
                problems.append(f"{label}: runs {tool} without {'/'.join(flags)}, so [tool.{tools[tool][0]}] in {pyproject.name} does not apply")
    return problems


def find_gates_that_cannot_fail(commands: Mapping[str, tuple[str, str]], *, advisory_words: Iterable[str] = _ADVISORY_WORDS) -> list[str]:
    """Commands that always exit 0 while their gate's name does not say it is advisory."""
    words = tuple(w.lower() for w in advisory_words)
    problems: list[str] = []
    for label, (command, names) in sorted(commands.items()):
        is_hook = label.startswith("pre-commit::")
        zero = next(
            (m for line in command.splitlines() for m in [_ALWAYS_ZERO.search(line)] if m and (is_hook or _GATE_TOOL.search(line))),
            None,
        )
        if zero and not any(w in names.lower() or w in label.lower() for w in words):
            problems.append(f"{label}: always exits 0 ({zero.group(0)}) but is not named as advisory -- it reads as a gate and blocks nothing")
    return problems


def assert_gates_honest(
    precommit_path: "Path | None", workflows: Iterable[Path], pyproject: Path, *, scope: "str | None" = None, known: Iterable[str] = ()
) -> None:
    import pytest

    commands = gate_commands(precommit_path, list(workflows), scope=scope)
    if not commands:
        pytest.fail("no hook or workflow step was found in scope -- this would check nothing")
    problems = set(find_tools_run_without_their_config(commands, pyproject) + find_gates_that_cannot_fail(commands))
    new, stale = sorted(problems - set(known)), sorted(set(known) - problems)
    if new or stale:
        pytest.fail(
            (f"{len(new)} gate(s) that do not do what they say:\n  " + "\n  ".join(new) if new else "")
            + (f"\n{len(stale)} accepted entr(ies) no longer reproduce -- remove them:\n  " + "\n  ".join(stale) if stale else "")
        )
