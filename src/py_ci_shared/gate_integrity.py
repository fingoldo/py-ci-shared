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
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional

from ._core import read_source

# Flags that NARROW what a gate inspects or lower the bar it enforces. Both the
# `--flag=value` and `--flag value` spellings are recognized. A flag must end where its name ends:
# `--skip` is not `--skip-without-db`, and `--ignore` is not `--ignore-missing-imports`.
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
_NARROWING_FLAG_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(f) for f in _NARROWING_FLAGS) + r")(?![\w-])(?:[=\s]+([^\s]+))?")
# Short flags are only narrowings for the tool that defines them (`-m` is a module to python, a marker filter to
# pytest), so they are read per command, for the program that command runs. True = the flag takes a value; a bare
# short flag (`-ll`) is captured as `-ll=` so it still needs a declaration.
_SHORT_FLAGS: dict[str, dict[str, bool]] = {
    "bandit": {"-s": True, "-x": True, "-t": True, "-l": False, "-ll": False, "-lll": False, "-i": False, "-ii": False, "-iii": False},
    "pytest": {"-m": True, "-k": True},
}
_PROGRAM_ALIASES = {"py.test": "pytest"}
_RUNNERS = (("uv", "run"), ("poetry", "run"), ("pdm", "run"), ("hatch", "run"), ("pipx", "run"), ("uvx",))
# Reusable-workflow / action inputs that narrow the called gate the same way a CLI flag would.
_NARROWING_INPUT_RE = re.compile(r"^[\w-]*(?:ignore|select|exclude|fail-under|advisory|skip)[\w-]*$", re.IGNORECASE)
_COV_FAIL_UNDER_RE = re.compile(r"--cov-fail-under(?![\w-])[=\s]+(\S+)")
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")


def _load_yaml(path: Path) -> Any:
    import yaml

    return yaml.safe_load(read_source(path)) or {}


def _iter_precommit_hooks(precommit_path: Path) -> Iterator[tuple[str, dict]]:
    """Yield ``(hook_id, hook_mapping)`` for every hook in a pre-commit config."""
    config = _load_yaml(precommit_path)
    for repo in config.get("repos", []) or []:
        for hook in repo.get("hooks", []) or []:
            yield str(hook.get("id", "<unnamed>")), hook


def _is_blocking(hook: dict) -> bool:
    """A hook is blocking unless it is manual-stage-only (opt-in, never runs on commit)."""
    stages = hook.get("stages")
    if not stages:
        return True
    return "manual" not in stages or len(stages) > 1


def _hook_command(hook: dict) -> str:
    """The hook's full argv as one string: ``entry`` plus ``args``, which pre-commit appends to it."""
    args = hook.get("args") or []
    tail = " ".join(shlex.quote(str(a)) for a in args) if isinstance(args, list) else str(args)
    return f"{hook.get('entry', '')} {tail}".strip()


def _split(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _normalise_program(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?|py|pythonw", name):
        return "python"
    return _PROGRAM_ALIASES.get(name, name)


def _program_tokens(tokens: list[str]) -> list[str]:
    """*tokens* from the program onwards: env assignments and runners (``uv run``) dropped, ``python -m X`` read as ``X``."""
    i = 0
    while i < len(tokens) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[i]):
        i += 1
    for runner in _RUNNERS:
        if tuple(tokens[i : i + len(runner)]) == runner:
            i += len(runner)
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
            break
    rest = tokens[i:]
    if len(rest) >= 3 and _normalise_program(rest[0]) == "python" and rest[1] == "-m":
        return [_normalise_program(rest[2]), *rest[3:]]
    if rest:
        return [_normalise_program(rest[0]), *rest[1:]]
    return rest


def _short_flag_narrowings(command: str) -> list[str]:
    out: list[str] = []
    for segment in _SEGMENT_SPLIT_RE.split(command):
        tokens = _program_tokens(_split(segment))
        if not tokens:
            continue
        flags = _SHORT_FLAGS.get(tokens[0])
        if not flags:
            continue
        i = 1
        while i < len(tokens):
            token = tokens[i]
            head, eq, inline = token.partition("=")
            if head in flags:
                takes_value = flags[head]
                if eq:
                    value = inline
                elif takes_value and i + 1 < len(tokens):
                    value = tokens[i + 1]
                    i += 1
                else:
                    value = ""
                out.append(f"{head}={value}")
            i += 1
    return out


def _flag_narrowings(command: str) -> list[str]:
    return [f"{flag}={value or ''}" for flag, value in _NARROWING_FLAG_RE.findall(command)] + _short_flag_narrowings(command)


# Config keys that narrow a tool's scope or lower the bar it enforces, in a pyproject table: ruff/flake8 ignores and
# excludes, bandit's `skips`/`exclude_dirs`, mypy's `ignore_errors`/`disable_error_code`/`follow_imports`, coverage's
# `omit`, pytest's `norecursedirs`/`testpaths`.
_NARROWING_CONFIG_KEYS = (
    "exclude",
    "extend-exclude",
    "exclude_dirs",
    "exclude-dirs",
    "ignore",
    "extend-ignore",
    "select",
    "per-file-ignores",
    "extend-per-file-ignores",
    "fail_under",
    "fail-under",
    "ignore-init-module",
    "files",
    "skips",
    "tests",
    "ignore_errors",
    "ignore_missing_imports",
    "disable_error_code",
    "follow_imports",
    "omit",
    "norecursedirs",
    "testpaths",
)


def _precommit_narrowings(precommit_path: Path) -> dict[str, str]:
    """Narrowings on the argv (``entry`` + ``args``) and file-scoping keys of each BLOCKING pre-commit hook, and on
    the config's top-level ``files``/``exclude``, which scope every hook."""
    found: dict[str, str] = {}
    config = _load_yaml(precommit_path)
    for key in ("files", "exclude"):
        if isinstance(config, dict) and config.get(key):
            found[f"pre-commit::<top-level>::{key}={config[key]}"] = f"{key}: {config[key]}"
    for hook_id, hook in _iter_precommit_hooks(precommit_path):
        if not _is_blocking(hook):
            continue
        command = _hook_command(hook)
        for knob in _flag_narrowings(command):
            found[f"pre-commit::{hook_id}::{knob}"] = command
        for key in ("files", "exclude"):
            if hook.get(key):
                found[f"pre-commit::{hook_id}::{key}={hook[key]}"] = f"{key}: {hook[key]}"
    return found


def _command_lines(run: str) -> list[str]:
    """A ``run:`` script as commands: line continuations folded, comment lines dropped."""
    text = re.sub(r"\\\s*\n\s*", " ", run)
    return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _labels(steps: list[Any]) -> list[str]:
    """A label per step: its ``id`` or ``name``, else ``step<n>``; a repeated label gets ``#2``, ``#3``..."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for index, step in enumerate(steps, start=1):
        label = str(step.get("id") or step.get("name") or f"step{index}") if isinstance(step, dict) else f"step{index}"
        seen[label] = seen.get(label, 0) + 1
        out.append(label if seen[label] == 1 else f"{label}#{seen[label]}")
    return out


def _input_narrowings(prefix: str, inputs: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    if not isinstance(inputs, dict):
        return found
    for key, value in inputs.items():
        text = "" if value is None else str(value).strip()
        # An input only narrows when it carries a value; `ignore: ""` is a no-op.
        if _NARROWING_INPUT_RE.match(str(key)) and text and text.lower() not in ("false", "{}", "[]"):
            found[f"{prefix}::with::{key}={text}"] = f"{key}: {text}"
    return found


def _workflow_files(workflows_dir: Path) -> list[Path]:
    return sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))


def _workflow_narrowings(workflows_dir: Path) -> dict[str, str]:
    """Narrowings in each job's steps (``run:`` scripts, including ``run: |`` block scalars, and ``with:`` inputs) and in
    a reusable-workflow job's ``with:``. Keys carry the job id and the step label, so the same flag in two jobs is two
    narrowings, each declared on its own. Trigger filters (``on: paths-ignore``) are not scope narrowings of a gate."""
    found: dict[str, str] = {}
    for workflow in _workflow_files(workflows_dir):
        data = _load_yaml(workflow)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            job_prefix = f"{workflow.name}::{job_id}"
            found.update(_input_narrowings(job_prefix, job.get("with")))
            steps = job.get("steps") or []
            if not isinstance(steps, list):
                continue
            for label, step in zip(_labels(steps), steps):
                if not isinstance(step, dict):
                    continue
                prefix = f"{job_prefix}::{label}"
                for line in _command_lines(str(step.get("run") or "")):
                    for knob in _flag_narrowings(line):
                        found[f"{prefix}::run::{knob}"] = line.strip()
                found.update(_input_narrowings(prefix, step.get("with")))
    return found


def _pyproject_narrowings(pyproject_path: "Path | None", pyproject_tables: tuple[str, ...]) -> dict[str, str]:
    """Narrowings declared in the named pyproject tables -- the venue invisible from the other two. Flags inside a
    pytest ``addopts`` string are read like a command line."""
    found: dict[str, str] = {}
    if pyproject_path is None:
        return found
    from ._toml_compat import tomllib

    data = tomllib.loads(read_source(pyproject_path))
    for dotted in pyproject_tables:
        table: Any = data
        for segment in dotted.split("."):
            table = table.get(segment, {}) if isinstance(table, dict) else {}
        if not isinstance(table, dict):
            continue
        for key in _NARROWING_CONFIG_KEYS:
            if key in table:
                found[f"pyproject::[{dotted}]::{key}"] = f"{key} = {table[key]!r}"
        addopts = table.get("addopts")
        if addopts:
            command = "pytest " + (" ".join(map(str, addopts)) if isinstance(addopts, list) else str(addopts))
            for knob in _flag_narrowings(command):
                found[f"pyproject::[{dotted}]::addopts::{knob}"] = f"addopts = {addopts!r}"
    return found


def _require(path: Optional[Path], what: str, is_dir: bool = False) -> Optional[Path]:
    """*path*, or None when the caller passed None to say the venue does not exist. A path that is given but missing
    raises: a typo (``.github/workflow``) must not read as a repository with no narrowings."""
    if path is None:
        return None
    ok = Path(path).is_dir() if is_dir else Path(path).is_file()
    if not ok:
        raise FileNotFoundError(f"{what} {path} does not exist; pass None if this repository has none")
    return Path(path)


def find_narrowings(
    precommit_path: Optional[Path],
    workflows_dir: Optional[Path],
    pyproject_path: Path | None = None,
    pyproject_tables: tuple[str, ...] = (),
) -> dict[str, str]:
    """Return ``{narrowing_key: evidence}`` for every scope-narrowing or bar-lowering
    knob on a blocking gate, across all three venues where one can hide.

    Keys are ``"<venue>::<gate>::<knob>"`` -- stable across reformatting and line
    insertion (unlike a ``path:line`` anchor), so a declaration written once stays valid. Workflow keys are
    ``"<file>::<job>::<step>::run::<knob>"`` / ``"...::with::<input>=<value>"``.
    Manual-stage-only pre-commit hooks are skipped: they are opt-in helpers, not gates.

    Args:
        precommit_path: the pre-commit config; each blocking hook's ``entry`` + ``args`` argv, its
            ``files``/``exclude`` scoping keys and the config's top-level ones are inspected. None when the repository
            has no pre-commit config; a path that does not exist raises ``FileNotFoundError``.
        workflows_dir: GitHub Actions workflows; every step's ``run`` script (line by line, including a ``run: |``
            block scalar) and ``with:`` inputs are inspected. None when there are none; a missing path raises.
        pyproject_path: optional; the third venue, and the one that hid the largest
            narrowing in the motivating audit -- ``exclude = ["tests"]`` under ``[tool.ruff]``
            is invisible in both of the other two files while suppressing hundreds of findings. A given path that does
            not exist raises.
        pyproject_tables: dotted table paths to inspect, e.g. ``("tool.ruff", "tool.mypy")``.
            Only the caller knows which tables configure a BLOCKING gate in its repo.
    """
    precommit = _require(precommit_path, "pre-commit config")
    workflows = _require(workflows_dir, "workflows directory", is_dir=True)
    pyproject = _require(pyproject_path, "pyproject")
    found: dict[str, str] = {}
    if precommit is not None:
        found.update(_precommit_narrowings(precommit))
    if workflows is not None:
        found.update(_workflow_narrowings(workflows))
    found.update(_pyproject_narrowings(pyproject, pyproject_tables))
    return found


def find_undeclared_narrowings(
    precommit_path: Optional[Path],
    workflows_dir: Optional[Path],
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
    precommit_path: Optional[Path],
    workflows_dir: Optional[Path],
    declared: dict[str, str],
    pyproject_path: Path | None = None,
    pyproject_tables: tuple[str, ...] = (),
) -> None:
    """Fail if any blocking gate narrows its scope without a written, in-repo reason, or if a venue path is missing."""
    import pytest

    try:
        undeclared, stale = find_undeclared_narrowings(precommit_path, workflows_dir, declared, pyproject_path, pyproject_tables)
    except FileNotFoundError as exc:
        pytest.fail(str(exc))
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


def _invocation_in(invocation: str, command: str) -> bool:
    """Whether *invocation* (``python -m mypy``) is run by *command*, however the interpreter is spelled
    (``python3 -m mypy``, ``.venv/bin/python -m mypy``, ``mypy``, ``uv run mypy``): a token-level match per command."""
    wanted = _program_tokens(_split(invocation))
    if not wanted:
        return False
    for segment in _SEGMENT_SPLIT_RE.split(command):
        tokens = _program_tokens(_split(segment))
        if tokens[: len(wanted)] == wanted:
            return True
    return False


def find_gates_without_completion_assertion(precommit_path: Path, completion_wrapped_tools: dict[str, str]) -> list[str]:
    """Return one message per blocking hook that invokes a completion-ambiguous tool directly.

    Args:
        precommit_path: the pre-commit config to inspect.
        completion_wrapped_tools: ``{tool_invocation: required_wrapper_invocation}`` -- e.g.
            ``{"python -m mypy": "python -m py_ci_shared.mypy_gate"}``. A tool lands in this
            map when exit code 0 is not proof it finished: mypy exits non-zero on an internal
            crash but the crash can also truncate an otherwise-passing run, and any tool that
            can be silently short-circuited by a plugin/stub failure has the same shape. Both sides are matched by
            program, not by substring, so ``python3 -m mypy`` and ``mypy`` are the same tool.
    """
    violations: list[str] = []
    for hook_id, hook in _iter_precommit_hooks(Path(precommit_path)):
        if not _is_blocking(hook):
            continue
        command = _hook_command(hook)
        for raw_invocation, wrapper in completion_wrapped_tools.items():
            wrapped = _invocation_in(wrapper, command) or wrapper in command
            if _invocation_in(raw_invocation, command) and not wrapped:
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


def _strip_comment(line: str) -> str:
    """*line* without a YAML/shell comment (``#`` at the start or after whitespace, outside quotes)."""
    if line.lstrip().startswith("#"):
        return ""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and i > 0 and line[i - 1].isspace():
            return line[:i]
    return line


def find_coverage_gate_mismatches(pyproject_path: Path, workflows_dir: Path) -> list[str]:
    """Return a message per ``--cov-fail-under`` in CI that disagrees with pyproject's
    ``[tool.coverage.report] fail_under``, or whose value is not a literal number (``${{ env.MIN }}``), which cannot be
    compared. Comments are not read.

    Two venues, one policy: when they desync, the lower one is the real gate and the higher
    one is decoration. Keeping them equal makes raising the ratchet a single deliberate edit.
    """
    from ._toml_compat import tomllib

    data = tomllib.loads(read_source(pyproject_path))
    declared = data.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under")
    if declared is None:
        return []
    violations: list[str] = []
    for workflow in _workflow_files(Path(workflows_dir)):
        for line in read_source(workflow).splitlines():
            for raw in _COV_FAIL_UNDER_RE.findall(_strip_comment(line)):
                value = raw.strip("'\"")
                try:
                    number = float(value)
                except ValueError:
                    violations.append(
                        f"{workflow.name}: --cov-fail-under={value} is not a literal number, so it cannot be checked against fail_under={declared}"
                    )
                    continue
                if number != float(declared):
                    violations.append(f"{workflow.name}: --cov-fail-under={value} but pyproject [tool.coverage.report] fail_under={declared}")
    return violations


def assert_coverage_gate_parity(pyproject_path: Path, workflows_dir: Path) -> None:
    """Fail if the CI coverage floor and the in-project coverage floor disagree."""
    import pytest

    violations = find_coverage_gate_mismatches(pyproject_path, workflows_dir)
    if violations:
        pytest.fail(f"{len(violations)} coverage-gate mismatch(es):\n  " + "\n  ".join(violations))
