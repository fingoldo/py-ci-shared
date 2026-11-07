"""A variable a tool's config reads (``extend = "$PY_CI_SHARED_DIR/configs/ruff-base.toml"``) must be defined in every CI job that runs the tool.

llm_bench d5990de: ``[tool.ruff] extend`` reads ``$PY_CI_SHARED_DIR``; the meta-tests job ran the ``timezone_honest`` gate (which shells out to ruff) and failed with "environment variable not found" until
a resolve step was copied into it. The lint job had the step, so the config looked fine and only the job that reached ruff indirectly broke.

The variables come from the string values of every ``[tool.<name>]`` table of ``pyproject.toml`` (``$VAR`` and ``${VAR}``; runner variables such as ``GITHUB_*``, ``RUNNER_*``, ``HOME``, ``PATH`` are
skipped). For each job of ``.github/workflows/*.yml``:

* ``tool-env-var-unset-in-job``: a ``run:`` step invokes ``<name>`` (``ruff check``, ``python -m ruff``, ``uvx ruff``, ``pre-commit run ruff``; install lines are not invocations) and the variable is not
  defined by the workflow ``env``, the job ``env``, that step's ``env``, an inline ``VAR=...`` / ``export VAR`` in the step, or an earlier step of the job writing ``VAR=`` to ``$GITHUB_ENV``.
* ``tool-env-var-unset-in-test-job``: the job runs ``pytest`` over a tree in which a test file shells out to the tool (a ``subprocess``/``run_git``-style call with ``"<name>"`` or ``-m <name>``), and the
  variable is not defined there. The pytest command's path arguments narrow the tree (``pytest tests/meta`` only sees ``tests/meta``).

Suppression: list the job in ``allow_jobs`` as ``"<workflow file>::<job>"`` (a job whose tool run is known to use a different config). Workflows are parsed with PyYAML (``use_git`` is accepted for signature parity; the workflow folder is read directly); one that does not parse is an
unparsed file.

Usage in a consumer's meta test::

    from py_ci_shared.tool_config_env_vars_set_in_every_tool_job import assert_tool_config_env_vars_set_in_every_tool_job

    def test_tool_config_variables_are_set_in_every_job():
        assert_tool_config_env_vars_set_in_every_tool_job(REPO)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from ._core import Baseline, Finding, SourceError, UnparsedFilesError, iter_files, read_source, relative_posix
from ._core.errors import EmptyScanError
from ._toml_compat import tomllib

__all__ = [
    "RULE_JOB",
    "RULE_TEST_JOB",
    "assert_tool_config_env_vars_set_in_every_tool_job",
    "find_tool_config_env_vars_set_in_every_tool_job",
]

RULE_JOB = "tool-env-var-unset-in-job"
RULE_TEST_JOB = "tool-env-var-unset-in-test-job"

_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_RUNNER_VARS = re.compile(r"^(?:GITHUB_|RUNNER_|CI$|HOME$|PATH$|USER$|USERPROFILE$|TMPDIR$|TEMP$|TMP$|PWD$|APPDATA$|LOCALAPPDATA$)")
_PYTEST = re.compile(r"(?<![\w.-])(?:python3?\s+-m\s+)?pytest\b([^\n;&|]*)")


def _vars_in(value: Any) -> "Iterator[str]":
    if isinstance(value, str):
        for match in _VAR.finditer(value):
            yield match.group(1) or match.group(2)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _vars_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _vars_in(item)


def _config_vars(pyproject: "dict[str, Any]") -> "dict[str, set[str]]":
    """``{tool name: variables its [tool.<name>] tables read}``."""
    out: "dict[str, set[str]]" = {}
    for tool, table in pyproject.get("tool", {}).items():
        names = {v for v in _vars_in(table) if not _RUNNER_VARS.match(v)}
        if names:
            out[tool] = names
    return out


def _invokes(run: str, tool: str) -> bool:
    pattern = re.compile(rf"(?<![\w./=-]){re.escape(tool)}(?![\w.=-])")
    for raw in run.splitlines():
        line = raw.split("#", 1)[0]
        if "install" in line or line.strip().startswith(("echo", "printf")):
            continue
        if pattern.search(line):
            return True
    return False


def _defines(var: str, run: str) -> bool:
    inline = re.search(rf"(?:^|[\s;&|]){re.escape(var)}=|export\s+{re.escape(var)}\b|\$env:{re.escape(var)}\s*=", run)
    return bool(inline)


def _writes_github_env(var: str, run: str) -> bool:
    return "GITHUB_ENV" in run and re.search(rf"{re.escape(var)}=", run) is not None


def _env_has(env: Any, var: str) -> bool:
    return isinstance(env, dict) and var in env


def _defined_before(var: str, workflow: "dict[str, Any]", job: "dict[str, Any]", steps: "list[Any]", upto: int) -> bool:
    """Whether *var* is set for step number *upto* of *job* (the step's own ``env`` and inline assignment count)."""
    if _env_has(workflow.get("env"), var) or _env_has(job.get("env"), var):
        return True
    for index, step in enumerate(steps[: upto + 1]):
        if not isinstance(step, dict):
            continue
        run = str(step.get("run", ""))
        if index == upto and (_env_has(step.get("env"), var) or _defines(var, run)):
            return True
        if index < upto and _writes_github_env(var, run):
            return True
    return False


def _line_of(text: str, job_name: str) -> int:
    match = re.search(rf"^ +{re.escape(str(job_name))}: *(?:#.*)?\s*$", text, re.MULTILINE)
    return text.count(chr(10), 0, match.start()) + 1 if match else 1


def _shells_out(tests_root: Path, tool: str) -> "list[Path]":
    """Test files under *tests_root* that run *tool* as a subprocess (the tool name quoted or after ``-m``)."""
    pattern = re.compile(rf"""['"]{re.escape(tool)}['"]|-m['",\s]+{re.escape(tool)}\b""")
    out: "list[Path]" = []
    for path in iter_files(tests_root, ("*.py",), use_git=False) if tests_root.is_dir() else []:
        try:
            text = read_source(path)
        except SourceError:
            continue
        if "subprocess" in text and pattern.search(text):
            out.append(path)
    return out


def _pytest_paths(run: str) -> "list[str]":
    """Path-like arguments of every ``pytest`` command in *run* (empty: the whole tree)."""
    paths: "list[str]" = []
    for match in _PYTEST.finditer(run):
        paths += [t.strip("'\"") for t in match.group(1).split() if not t.startswith("-") and ("/" in t or t.endswith(".py"))]
    return paths


def _test_job_exposes(root: Path, run_steps: "list[str]", tool: str, shell_cache: "dict[str, list[Path]]") -> bool:
    if not any(_PYTEST.search(r) for r in run_steps):
        return False
    scoped = [p for r in run_steps for p in _pytest_paths(r)]
    if tool not in shell_cache:
        shell_cache[tool] = _shells_out(root, tool)
    candidates = shell_cache[tool]
    if not scoped:
        return bool(candidates)
    prefixes = [root / p.rstrip("/") for p in scoped]
    return any(str(c).startswith(str(prefix)) for c in candidates for prefix in prefixes)


def _job_findings(
    rel: str,
    text: str,
    root: Path,
    workflow: "dict[str, Any]",
    job_name: str,
    job: "dict[str, Any]",
    config: "dict[str, set[str]]",
    shell_cache: "dict[str, list[Path]]",
) -> "list[Finding]":
    steps = job.get("steps") or []
    runs = [str(s.get("run", "")) if isinstance(s, dict) else "" for s in steps]
    out: "list[Finding]" = []
    line = _line_of(text, job_name)
    for tool, variables in sorted(config.items()):
        direct = [i for i, run in enumerate(runs) if _invokes(run, tool)]
        indirect = not direct and _test_job_exposes(root, runs, tool, shell_cache)
        positions = direct or ([max(i for i, r in enumerate(runs) if _PYTEST.search(r))] if indirect else [])
        for var in sorted(variables):
            if positions and not all(_defined_before(var, workflow, job, steps, i) for i in positions):
                rule = RULE_JOB if direct else RULE_TEST_JOB
                how = "runs" if direct else "runs pytest over a test that shells out to"
                out.append(
                    Finding(
                        rel,
                        line,
                        rule,
                        f"job `{job_name}` {how} `{tool}`, whose config in pyproject.toml reads ${var}, but the job never defines it: add the step or env that sets it",
                    )
                )
    return out


def _workflow_files(base: Path) -> "list[Path]":
    folder = base / ".github" / "workflows"
    return sorted(p for p in folder.glob("*.y*ml") if p.suffix in (".yml", ".yaml")) if folder.is_dir() else []


def _load_yaml(text: str) -> "dict[str, Any]":
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def find_tool_config_env_vars_set_in_every_tool_job(
    root: Union[str, Path],
    *,
    allow_jobs: Iterable[str] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every job under ``root/.github/workflows`` that runs a tool without a variable its pyproject config reads, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* workflow files are found and ``UnparsedFilesError`` for a workflow or pyproject that cannot be read or parsed (unless *allow_unparsed*): a gate
    that cannot read the files must not pass them.
    """
    base = Path(root)
    files = _workflow_files(base)
    if len(files) < min_files:
        raise EmptyScanError(f"tool_config_env_vars_set_in_every_tool_job found {len(files)} workflow file(s) under {base}, below the floor of {min_files}")
    allowed = set(allow_jobs)
    unparsed: "list[str]" = []
    out: "list[Finding]" = []
    config: "dict[str, set[str]]" = {}
    shell_cache: "dict[str, list[Path]]" = {}
    pyproject = base / "pyproject.toml"
    try:
        config = _config_vars(tomllib.loads(read_source(pyproject))) if pyproject.is_file() else {}
    except (SourceError, ValueError) as exc:
        unparsed.append(f"pyproject.toml: {exc}")
    for path in files if config else []:
        rel = relative_posix(path, base)
        try:
            text = read_source(path)
            workflow = _load_yaml(text)
        except (SourceError, ValueError, yaml.YAMLError) as exc:
            unparsed.append(f"{rel}: {exc}")
            continue
        for job_name, job in (workflow.get("jobs") or {}).items():
            if isinstance(job, dict) and f"{path.name}::{job_name}" not in allowed:
                out.extend(_job_findings(rel, text, base, workflow, job_name, job, config, shell_cache))
    if unparsed and not allow_unparsed:
        raise UnparsedFilesError(f"{len(unparsed)} file(s) could not be read or parsed:\n  " + "\n  ".join(unparsed))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule, f.message))


def assert_tool_config_env_vars_set_in_every_tool_job(
    root: Union[str, Path],
    *,
    allow_jobs: Iterable[str] = (),
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any job that runs a tool without a variable its config reads, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_tool_config_env_vars_set_in_every_tool_job(root, allow_jobs=allow_jobs, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "define the variable in the job (an env entry, or a step writing it to $GITHUB_ENV before the tool runs), or list the job in allow_jobs"
    if baseline_path is not None:
        baseline = Baseline(
            baseline_path, gate="tool_config_env_vars_set_in_every_tool_job", refresh_command="PY_CI_SHARED_REFRESH=tool_config_env_vars_set_in_every_tool_job"
        )
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} job(s) missing a variable a tool config reads; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
