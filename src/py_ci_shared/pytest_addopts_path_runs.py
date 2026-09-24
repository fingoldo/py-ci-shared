"""A hook or CI step that names test paths, and runs none of them because ``addopts`` still deselects them.

``addopts`` is prepended to EVERY pytest invocation, including a pre-commit hook or workflow step that names test files
by path. In a project whose ``addopts`` carries ``-m 'not integration'``, a hook written as
``pytest tests/integration/test_x.py`` selects nothing: the marker expression still applies, and only a ``-m`` on the
command line replaces it. The first fix written for the shipped case (naming four never-run integration tests in a
hook) would have looked like a fix and run nothing. pytest's exit status 5 does not save it when the same command
also names other paths that do select something.

This builds on :mod:`py_ci_shared.marker_runner_coverage` (its ``runners``/``marked_tests``/``expression_selects``/
``keyword_selects``, which parse commands and evaluate ``-m``/``-k`` with pytest's grammar) and on
:func:`py_ci_shared.gate_config_honesty.gate_commands` (every pre-commit hook and workflow ``run:`` step). For each
runner, each PATH it names is checked on its own: when the path reaches at least one test and the runner's effective
``-m``/``-k`` (its own, or the one inherited from ``addopts`` unless the command resets it with ``-o addopts=``)
deselects every one of them, that is a finding. A path that reaches no test at all is left to
``ci_test_dir_reachability``.

Usage::

    from py_ci_shared.pytest_addopts_path_runs import assert_path_runs_select_tests

    def test_path_naming_runners_select_something():
        assert_path_runs_select_tests(PACKAGE_ROOT, repo_root=REPO_ROOT)
"""

from __future__ import annotations

import ast
import configparser
import posixpath
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, SourceProblem, read_source, relative_posix, scan_python
from ._gate_report import report
from .marker_runner_coverage import Runner, expression_selects, keyword_selects, marked_tests, runners

__all__ = ["RULE", "REFRESH_FLAG", "read_addopts", "find_path_runs_selecting_nothing", "assert_path_runs_select_tests"]

RULE = "path-run-selects-nothing"
REFRESH_FLAG = "--refresh-addopts-path-runs-baseline"
_RESETS_ADDOPTS = re.compile(r"""(?:-o|--override-ini)[=\s]+['"]?addopts=""")
_IDENT = re.compile(r"[A-Za-z_][\w.\-\[\]]*")


def read_addopts(package_root: Union[str, Path]) -> str:
    """``addopts`` from the package's ``pyproject.toml`` (string or list), ``pytest.ini``, ``tox.ini`` or ``setup.cfg``."""
    from ._toml_compat import tomllib

    root = Path(package_root)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        options = tomllib.loads(read_source(pyproject)).get("tool", {}).get("pytest", {}).get("ini_options", {})
        if "addopts" in options:
            raw = options["addopts"]
            return " ".join(raw) if isinstance(raw, list) else str(raw)
    for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        path = root / name
        if path.is_file():
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(read_source(path))
            if parser.has_option(section, "addopts"):
                return " ".join(parser.get(section, "addopts").split())
    return ""


@dataclass(frozen=True)
class _Test:
    file: str
    name: str
    markers: frozenset[str]


def _test_names(body: Sequence[ast.stmt], prefix: str = "") -> list[str]:
    out: list[str] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out.append(prefix + node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            out += _test_names(node.body, f"{prefix}{node.name}::")
    return out


def _collect_tests(package_root: Path, tests_dirs: Sequence[str], identifiers: Iterable[str]) -> tuple[list[_Test], list[SourceProblem], int]:
    """Every test under *tests_dirs* with the markers among *identifiers* it carries (via ``marked_tests``)."""
    names: dict[tuple[str, str], set[str]] = {}
    problems: list[SourceProblem] = []
    parsed = 0
    dirs = [package_root / d for d in tests_dirs if (package_root / d).is_dir()]
    for tests_dir in dirs:
        scan = scan_python(tests_dir, min_files=0, patterns=("test_*.py", "*_test.py"))
        problems += [SourceProblem(p.path, relative_posix(p.path, package_root), p.line, p.kind, p.message) for p in scan.unparsed]
        parsed += scan.parsed_count
        for f in scan:
            rel = relative_posix(f.path, package_root)
            for name in _test_names(f.tree.body):
                names[(rel, name)] = set()
    if not problems:
        for marker in sorted(set(identifiers)):
            for tests_dir in dirs:
                for marked in marked_tests(tests_dir, package_root, marker=marker):
                    members = marked.members if marked.members else (marked.name,)
                    for member in members:
                        if (marked.file, member) in names:
                            names[(marked.file, member)].add(marker)
    return [_Test(f, n, frozenset(m)) for (f, n), m in sorted(names.items())], problems, parsed


def _reaches(path: str, test: _Test) -> bool:
    if "::" in path:
        node_file, node = path.split("::", 1)
        return node_file == test.file and (test.name == node or test.name.startswith(node + "::"))
    return path in (".", "") or test.file == path or test.file.startswith(path + "/")


def _package_relative(path: str, cwd: str, package_rel: str) -> Optional[str]:
    """*path* as the runner names it, relative to the package root; ``None`` when it points outside the package."""
    node = ""
    if "::" in path:
        path, node = path.split("::", 1)
    full = posixpath.normpath(posixpath.join(cwd, path)) if cwd else posixpath.normpath(path)
    if cwd and package_rel:
        if full == package_rel:
            full = "."
        elif full.startswith(package_rel + "/"):
            full = full[len(package_rel) + 1 :]
        else:
            return None
    return f"{full}::{node}" if node else full


def _runners_for(commands: Mapping[str, tuple[str, str]], addopts: str) -> list[tuple[Runner, bool]]:
    """``(runner, inherits_addopts)``; a command that resets addopts (``-o addopts=``) inherits nothing."""
    out: list[tuple[Runner, bool]] = []
    for label, (command, _) in commands.items():
        reset = bool(_RESETS_ADDOPTS.search(command))
        out += [(r, not reset) for r in runners([(label, command)], addopts="" if reset else addopts)]
    return out


def _source_of(label: str) -> str:
    if label.startswith("pre-commit::"):
        return ".pre-commit-config.yaml"
    return ".github/workflows/" + label.split("::", 1)[0]


def _findings(tests: list[_Test], pairs: list[tuple[Runner, bool]], package_rel: str, addopts_expr: Optional[str]) -> list[Finding]:
    out: list[Finding] = []
    for runner, inherits in pairs:
        for raw in runner.paths:
            path = _package_relative(raw, runner.cwd, package_rel)
            if path is None:
                continue
            reached = [t for t in tests if _reaches(path, t)]
            if not reached:
                continue
            try:
                selected = [
                    t
                    for t in reached
                    if expression_selects(runner.expression, t.markers)
                    and keyword_selects(runner.keyword, [*t.file.split("/"), *t.name.split("::"), *t.markers])
                ]
            except ValueError:
                continue  # an expression pytest rejects is reported by marker_runner_coverage
            if selected:
                continue
            origin = "inherited from addopts" if inherits and runner.expression == addopts_expr and addopts_expr else "its own"
            why = f"-m {runner.expression!r} ({origin})" if runner.expression else f"-k {runner.keyword!r}"
            out.append(Finding(_source_of(runner.label), 1, RULE, f"{runner.label}: `{raw}` reaches {len(reached)} test(s) and {why} deselects every one"))
    return out


def _collect(
    package_root: Union[str, Path],
    *,
    repo_root: Optional[Union[str, Path]],
    tests_dirs: Sequence[str],
    precommit_path: Optional[Union[str, Path]],
    workflows: Optional[Iterable[Union[str, Path]]],
    addopts: Optional[str],
    commands: Optional[Mapping[str, tuple[str, str]]],
) -> tuple[list[Finding], int, list[SourceProblem]]:
    from .gate_config_honesty import gate_commands

    pkg = Path(package_root)
    repo = Path(repo_root) if repo_root is not None else pkg
    package_rel = relative_posix(pkg, repo) if pkg != repo else ""
    package_rel = "" if package_rel == "." else package_rel
    opts = read_addopts(pkg) if addopts is None else addopts
    if commands is None:
        pc = Path(precommit_path) if precommit_path is not None else repo / ".pre-commit-config.yaml"
        wfs = [Path(w) for w in workflows] if workflows is not None else sorted((repo / ".github" / "workflows").glob("*.y*ml"))
        commands = dict(gate_commands(pc if pc.is_file() else None, [], scope=package_rel or None))
        for wf in wfs:
            # A workflow whose top-level `defaults.run.working-directory` is the package names it once, outside any
            # step; every step of such a workflow runs in the package, so the whole file is in scope.
            in_scope = not package_rel or package_rel in read_source(wf)
            commands.update(gate_commands(None, [wf], scope=None if in_scope else package_rel))
    pairs = _runners_for(commands, opts)
    addopts_expr = runners([("addopts", "pytest")], addopts=opts)[0].expression if opts else None
    identifiers = {i for r, _ in pairs for e in (r.expression, r.keyword) if e for i in _IDENT.findall(e)} - {"and", "or", "not"}
    tests, problems, parsed = _collect_tests(pkg, tests_dirs, identifiers)
    return sorted(_findings(tests, pairs, package_rel, addopts_expr), key=lambda f: f.message), parsed, problems


def find_path_runs_selecting_nothing(
    package_root: Union[str, Path],
    *,
    repo_root: Optional[Union[str, Path]] = None,
    tests_dirs: Sequence[str] = ("tests",),
    precommit_path: Optional[Union[str, Path]] = None,
    workflows: Optional[Iterable[Union[str, Path]]] = None,
    addopts: Optional[str] = None,
    commands: Optional[Mapping[str, tuple[str, str]]] = None,
) -> list[Finding]:
    """Every (runner, named path) whose tests are all deselected, plus one ``unparsed-file`` finding per test file
    that could not be parsed. *commands* (``{label: (command, name)}``) replaces the pre-commit/workflow discovery;
    *addopts* replaces reading the package config. In a monorepo pass the *repo_root* holding the hooks and workflows."""
    findings, _, problems = _collect(
        package_root, repo_root=repo_root, tests_dirs=tests_dirs, precommit_path=precommit_path, workflows=workflows, addopts=addopts, commands=commands
    )
    return findings + [p.to_finding() for p in problems]


def assert_path_runs_select_tests(
    package_root: Union[str, Path],
    *,
    repo_root: Optional[Union[str, Path]] = None,
    tests_dirs: Sequence[str] = ("tests",),
    precommit_path: Optional[Union[str, Path]] = None,
    workflows: Optional[Iterable[Union[str, Path]]] = None,
    addopts: Optional[str] = None,
    commands: Optional[Mapping[str, tuple[str, str]]] = None,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
) -> None:
    """Fail on a path-naming runner that selects none of the tests it names (new against *baseline_path* when given),
    on fewer than *min_files* parsed test files, and on any unparsable test file."""
    findings, parsed, problems = _collect(
        package_root, repo_root=repo_root, tests_dirs=tests_dirs, precommit_path=precommit_path, workflows=workflows, addopts=addopts, commands=commands
    )
    report(
        findings,
        gate="pytest-addopts-path-runs",
        flag=REFRESH_FLAG,
        guidance="a runner that wants a deselected tier must pass its own -m (the last -m wins) as well as the paths",
        parsed_count=parsed,
        problems=problems,
        min_files=min_files,
        root=package_root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
