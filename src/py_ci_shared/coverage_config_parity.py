"""Coverage config that a CI run inherits without meaning to: a whole-suite ``fail_under`` on a narrow run, and njit bodies no run can see.

``[tool.coverage.report] fail_under`` is read by EVERY run that renders coverage: ``pytest --cov`` (pytest-cov takes
the config's ``fail_under`` when no ``--cov-fail-under`` is given) and ``coverage report|xml|html|json|lcov``. It was
written for the whole-suite gate, and it sinks every other run: a nightly that re-runs four suites under
``NUMBA_DISABLE_JIT=1`` purely to collect data reported 7.22% and exited 1 every night, starving the merged trend
workflow chained on its success, whose own ``coverage report`` then failed the same way. Two workflows sank at once.

Rules, over every ``run:`` step of ``.github/workflows/*.yml``:

* ``narrow-run-inherits-fail-under`` (only when the config sets ``fail_under > 0``): a coverage-rendering command on a
  NARROW run that neither points at its own config (``--cov-config=``, ``--rcfile=``, ``COVERAGE_RCFILE``) nor states
  its own floor (``--cov-fail-under``/``--fail-under``; whether that floor agrees with the config is
  ``gate_integrity.assert_coverage_gate_parity``'s job). Narrow means: named test paths other than ``testpaths``, a
  positive ``-m``/``-k`` selection (``-m integration``; ``-m "not gpu"`` is the whole suite minus a tier), a shard
  (``--splits``/``--group``, ``--shard-id``, ``--num-shards``), ``--lf``/``--deselect``, or a ``coverage`` report in a job
  with no whole-suite pytest of its own (a merge of downloaded data). A step with ``continue-on-error: true`` or a
  command ending ``|| true`` cannot fail the job and is skipped. The fix is to derive a config with ``fail_under``
  popped and pass it, keeping one source of truth, rather than a second hand-written floor.
* ``numba-blind-coverage``: the source imports numba, CI renders coverage, and no coverage-collecting run sets
  ``NUMBA_DISABLE_JIT=1`` (step, job or workflow ``env``, or inline): coverage.py cannot trace a compiled body, so every
  ``@njit`` function reads as uncovered however hard it is exercised.

Usage::

    from py_ci_shared.coverage_config_parity import assert_coverage_config_parity

    def test_coverage_config_parity():
        assert_coverage_config_parity(REPO)
"""

from __future__ import annotations

import configparser
import re
import shlex
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, SourceError, SourceProblem, iter_files, read_source, relative_posix
from ._gate_report import report, skip_set

__all__ = ["RULE_NARROW", "RULE_NUMBA", "REFRESH_FLAG", "coverage_fail_under", "find_coverage_config_violations", "assert_coverage_config_parity"]

RULE_NARROW = "narrow-run-inherits-fail-under"
RULE_NUMBA = "numba-blind-coverage"
REFRESH_FLAG = "--refresh-coverage-config-baseline"
_REPORT_COMMANDS = frozenset({"report", "xml", "html", "json", "lcov"})
_SHARD_FLAGS = ("--splits", "--group", "--shard-id", "--num-shards", "--lf", "--last-failed", "--deselect")
_OWN_CONFIG = ("--cov-config", "--rcfile", "--cov-fail-under", "--fail-under")
_SHELL_SPLIT = re.compile(r"&&|;|\|(?!\|)")
_NUMBA_IMPORT = re.compile(rb"^\s*(?:import\s+numba\b|from\s+numba\b)", re.MULTILINE)
_TRUTHY = re.compile(r"^\s*['\"]?(?:1|true|yes|on)['\"]?\s*$", re.IGNORECASE)


def coverage_fail_under(repo_root: Path) -> float:
    """``fail_under`` from ``pyproject.toml``, ``.coveragerc``, ``setup.cfg`` or ``tox.ini`` (first found), else 0."""
    from ._toml_compat import tomllib

    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(read_source(pyproject))
        value = data.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under")
        if value is not None:
            return float(value)
    for name, section in ((".coveragerc", "report"), ("setup.cfg", "coverage:report"), ("tox.ini", "coverage:report")):
        path = repo_root / name
        if path.is_file():
            parser = configparser.ConfigParser()
            parser.read_string(read_source(path))
            if parser.has_option(section, "fail_under"):
                return float(parser.get(section, "fail_under"))
    return 0.0


def _testpaths(repo_root: Path) -> set[str]:
    from ._toml_compat import tomllib

    pyproject = repo_root / "pyproject.toml"
    paths: set[str] = set()
    if pyproject.is_file():
        options = tomllib.loads(read_source(pyproject)).get("tool", {}).get("pytest", {}).get("ini_options", {})
        raw = options.get("testpaths", [])
        paths = {str(p).strip("/") for p in ([raw] if isinstance(raw, str) else raw)}
    return paths or {"tests", "test"}


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _commands(run: str) -> Iterator[tuple[str, bool]]:
    """``(command, may_fail)`` per shell command in a ``run:`` block; ``may_fail`` is False after ``|| true``."""
    folded = re.sub(r"\\\s*\n\s*", " ", run)
    for line in folded.splitlines():
        text = line.split(" #", 1)[0].strip()
        if not text or text.startswith("#"):
            continue
        swallowed = bool(re.search(r"\|\|\s*(?:true|:|exit\s+0)\b", text))
        for part in _SHELL_SPLIT.split(text.split("||", 1)[0]):
            if part.strip():
                yield part.strip(), not swallowed


@dataclass
class _Invocation:
    kind: str  # "pytest" or "coverage"
    words: list[str]
    text: str
    may_fail: bool
    env: dict[str, str] = field(default_factory=dict)


def _classify(command: str, may_fail: bool) -> Optional[_Invocation]:
    words = _tokens(command)
    inline_env = {}
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
        key, _, value = words.pop(0).partition("=")
        inline_env[key] = value
    for i, word in enumerate(words):
        base = word.rsplit("/", 1)[-1]
        rest = words[i + 1 :]
        if base in ("pytest", "py.test") or (base.startswith("python") and rest[:2] == ["-m", "pytest"]):
            args = rest[2:] if base.startswith("python") else rest
            return _Invocation("pytest", args, command, may_fail, inline_env)
        if base == "coverage" or (base.startswith("python") and rest[:2] == ["-m", "coverage"]):
            args = rest[2:] if base.startswith("python") else rest
            if args and args[0] in _REPORT_COMMANDS | {"run"}:
                return _Invocation("coverage", args, command, may_fail, inline_env)
            return None
    return None


def _collects(inv: _Invocation, run: str) -> bool:
    """A coverage command always renders or collects; a pytest run does when it names ``--cov``, or when its ``--cov``
    arrives through a shell variable or array set earlier in the same step (``"${cov_args[@]}"``)."""
    if inv.kind == "coverage":
        return True
    if any(a == "--cov" or a.startswith("--cov=") for a in inv.words):
        return True
    return "--cov" in run and any("$" in w for w in inv.words)


def _has_option(words: Sequence[str], names: Iterable[str]) -> bool:
    return any(w == n or w.startswith(n + "=") for w in words for n in names)


_VAR_REF = re.compile(r"\$\{?([A-Za-z_]\w*)")


def _shell_value(run: str, name: str) -> str:
    """Everything assigned to shell variable or array *name* in *run*: ``name=(...)`` (across lines), ``name+=(...)``,
    ``name="..."``, with ``export``/``local``/``declare`` prefixes."""
    pattern = rf"(?m)^\s*(?:(?:export|local|declare(?:\s+-\w+)*|readonly)\s+)?{re.escape(name)}\+?=(\([^)]*\)|[^\n]*)"
    return " ".join(m.group(1) for m in re.finditer(pattern, run))


def _passes_own_config(inv: _Invocation, run: str) -> bool:
    """The invocation names its own config: on its line, or in a shell variable or array it expands (``"${cov_args[@]}"``
    set to ``(--cov=src --cov-config=.coveragerc.narrow)`` earlier in the step)."""
    if _has_option(inv.words, _OWN_CONFIG):
        return True
    for word in inv.words:
        for name in _VAR_REF.findall(word):
            value = _shell_value(run, name)
            if value and _has_option([t for w in _tokens(value.strip("()")) for t in w.split()], _OWN_CONFIG):
                return True
    return False


def _narrow_reason(inv: _Invocation, testpaths: set[str]) -> Optional[str]:
    """Why a ``pytest --cov`` run is narrow, or ``None`` for a whole-suite run."""
    words = inv.words
    for flag in _SHARD_FLAGS:
        if _has_option(words, [flag]):
            return f"{flag} selects a subset"
    i = 0
    paths: list[str] = []
    while i < len(words):
        w = words[i]
        if w in ("-m", "-k") and i + 1 < len(words):
            if not words[i + 1].strip().startswith("not "):
                return f"{w} {words[i + 1]!r} selects a subset"
            i += 2
            continue
        if w.startswith("-"):
            i += (
                2 if w in ("-p", "-n", "-o", "-c", "-W", "-r", "--ignore", "--cov-report", "--timeout", "--durations", "--tb", "--maxfail", "--basetemp") else 1
            )
            continue
        paths.append(w.split("::", 1)[0].strip("./") or ".")
        i += 1
    named = [p for p in paths if p not in testpaths and p != "."]
    return f"names {', '.join(named[:3])}" if named else None


def _env_true(env: Mapping[str, Any], name: str) -> bool:
    return name in env and bool(_TRUTHY.match(str(env[name])))


@dataclass
class _Step:
    workflow: str
    job: str
    name: str
    line: int
    invocations: list[_Invocation]
    env: dict[str, Any]
    continue_on_error: bool
    run: str = ""


def _line_of(text: str, needle: str) -> int:
    first = needle.strip().splitlines()[0].strip() if needle.strip() else ""
    index = text.find(first) if first else -1
    return text.count("\n", 0, index) + 1 if index >= 0 else 1


def _steps(workflow: Path, rel: str) -> list[_Step]:
    import yaml

    text = read_source(workflow)
    data = yaml.safe_load(text) or {}
    top_env = dict(data.get("env") or {}) if isinstance(data, dict) else {}
    out: list[_Step] = []
    for job_id, job in ((data.get("jobs") or {}) if isinstance(data, dict) else {}).items():
        job = job or {}
        job_env = {**top_env, **dict(job.get("env") or {})}
        for step in job.get("steps") or []:
            run = str((step or {}).get("run") or "")
            if not run.strip():
                continue
            invocations = [inv for inv in (_classify(cmd, may_fail) for cmd, may_fail in _commands(run)) if inv is not None and _collects(inv, run)]
            if not invocations:
                continue
            env = {**job_env, **dict(step.get("env") or {})}
            name = str(step.get("name") or run.strip().splitlines()[0])
            coe = str(step.get("continue-on-error", job.get("continue-on-error", False))).lower() == "true"
            out.append(_Step(rel, str(job_id), name, _line_of(text, run), invocations, env, coe, run))
    return out


def _narrow_findings(steps: list[_Step], fail_under: float, testpaths: set[str]) -> list[Finding]:
    out: list[Finding] = []
    whole_suite_jobs = {(s.workflow, s.job) for s in steps for inv in s.invocations if inv.kind == "pytest" and _narrow_reason(inv, testpaths) is None}
    for s in steps:
        if s.continue_on_error or "COVERAGE_RCFILE" in s.env:
            continue
        for inv in s.invocations:
            if not inv.may_fail or _passes_own_config(inv, s.run):
                continue
            if inv.kind == "pytest":
                why = _narrow_reason(inv, testpaths)
            elif inv.words[0] in _REPORT_COMMANDS and (s.workflow, s.job) not in whole_suite_jobs:
                why = f"`coverage {inv.words[0]}` in a job with no whole-suite pytest run of its own"
            else:
                why = None
            if why:
                head = " ".join(inv.text.split()[:3])
                out.append(
                    Finding(
                        s.workflow,
                        s.line,
                        RULE_NARROW,
                        f"{s.job}::{s.name}: `{head} ...` {why} but inherits fail_under={fail_under:g}; pass a derived --cov-config/--rcfile",
                    )
                )
    return out


def _numba_finding(steps: list[_Step], rel_config: str) -> list[Finding]:
    collecting = [(s, inv) for s in steps for inv in s.invocations if inv.kind == "pytest" or inv.words[0] == "run"]
    if not collecting:
        return []
    if any(_env_true(s.env, "NUMBA_DISABLE_JIT") or _env_true(inv.env, "NUMBA_DISABLE_JIT") for s, inv in collecting):
        return []
    return [
        Finding(rel_config, 1, RULE_NUMBA, "the source imports numba but no CI coverage run sets NUMBA_DISABLE_JIT=1, so every @njit body reads as uncovered")
    ]


def _uses_numba(repo_root: Path, source_dirs: Sequence[Union[str, Path]], skip: frozenset[str]) -> bool:
    for base in source_dirs or [repo_root]:
        root = Path(base) if Path(base).is_absolute() else repo_root / base
        if not root.is_dir():
            continue
        if any(_NUMBA_IMPORT.search(_raw(path)) for path in iter_files(root, ("*.py",), exclude=skip) if not path.name.startswith("test_")):
            return True
    return False


def _raw(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _collect(
    repo_root: Union[str, Path], *, workflows_dir: Optional[Union[str, Path]], source_dirs: Sequence[Union[str, Path]], check_numba: bool
) -> tuple[list[Finding], int, list[SourceProblem]]:
    root = Path(repo_root)
    wf_dir = Path(workflows_dir) if workflows_dir is not None else root / ".github" / "workflows"
    files = [p for p in sorted(wf_dir.glob("*.y*ml")) if p.suffix in (".yml", ".yaml")] if wf_dir.is_dir() else []
    steps: list[_Step] = []
    problems: list[SourceProblem] = []
    parsed = 0
    for path in files:
        rel = relative_posix(path, root)
        try:
            steps += _steps(path, rel)
            parsed += 1
        except SourceError as exc:
            problems.append(SourceProblem(path, rel, exc.line or 1, exc.kind, exc.message))
        except Exception as exc:  # yaml.YAMLError and malformed structures: the workflow cannot be checked
            mark = getattr(exc, "problem_mark", None)
            problems.append(SourceProblem(path, rel, (mark.line + 1) if mark is not None else 1, "unparsable", str(exc).splitlines()[0]))
    findings: list[Finding] = []
    fail_under = coverage_fail_under(root)
    if fail_under > 0:
        findings += _narrow_findings(steps, fail_under, _testpaths(root))
    if check_numba and _uses_numba(root, source_dirs, skip_set(include_tests=False)):
        findings += _numba_finding(steps, "pyproject.toml")
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule)), parsed, problems


def find_coverage_config_violations(
    repo_root: Union[str, Path],
    *,
    workflows_dir: Optional[Union[str, Path]] = None,
    source_dirs: Sequence[Union[str, Path]] = (),
    check_numba: bool = True,
) -> list[Finding]:
    """Every narrow coverage run inheriting ``fail_under`` and, when the source imports numba, a missing
    ``NUMBA_DISABLE_JIT=1`` coverage run; plus one ``unparsed-file`` finding per unreadable workflow."""
    findings, _, problems = _collect(repo_root, workflows_dir=workflows_dir, source_dirs=source_dirs, check_numba=check_numba)
    return findings + [p.to_finding() for p in problems]


def assert_coverage_config_parity(
    repo_root: Union[str, Path],
    *,
    workflows_dir: Optional[Union[str, Path]] = None,
    source_dirs: Sequence[Union[str, Path]] = (),
    check_numba: bool = True,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
) -> None:
    """Fail on a finding (new against *baseline_path* when given), on fewer than *min_files* parsed workflows, and on
    any unparsable workflow. Refresh with ``--refresh-coverage-config-baseline``."""
    findings, parsed, problems = _collect(repo_root, workflows_dir=workflows_dir, source_dirs=source_dirs, check_numba=check_numba)
    report(
        findings,
        gate="coverage-config-parity",
        flag=REFRESH_FLAG,
        guidance="generate a coverage config with fail_under popped from pyproject and pass it (--cov-config= / --rcfile=)",
        parsed_count=parsed,
        problems=problems,
        min_files=min_files,
        root=repo_root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
