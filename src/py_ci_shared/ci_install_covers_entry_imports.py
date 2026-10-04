"""The extras a CI job installs must cover what the module it runs imports when it starts.

A job that runs ``python -m mypkg.nightly`` (or ``python scripts/run.py``, or ``pytest tests/x``) loads that module and
everything it imports at module level, before a single line of its own code runs. When one of those modules does a bare
``import pywt`` and the distribution (``pywavelets``) is only in a ``signal`` extra the job never installed, the job dies at
startup with ``ModuleNotFoundError``, and does so on every run. In the shipped case a nightly installed ``.[dev]``, its
entry module imported ``pywt`` at module level, and the package declares ``pywavelets`` only in the ``signal`` extra: the
nightly was red from its first scheduled run to the day someone read the log::

    - run: pip install -e ".[dev]"
    - run: python -m mypkg.nightly --dry-run    # mypkg/features.py: import pywt  (pywavelets: extra `signal` only)

For every job of ``.github/workflows/*.yml`` this reads the install commands in order (``pip install``, ``python -m pip``,
``uv pip install``, ``-e .[extras]``, ``-r file``, ``uv sync --extra a --extra b`` / ``--all-extras``, ``uv run --extra``,
local composite actions, ``name @ url``) with the machinery of :mod:`py_ci_shared.ci_install_covers_conftest`, and at each
entry command (``python -m module``, ``python script.py``, ``coverage run``/``uv run`` of those, and with
``include_pytest`` ``pytest <test paths>``, each test file being an entry) it follows the module-level imports of the
entry: first-party modules inside the repo (``import``, ``from . import``, parent packages' ``__init__``), skipping
``try/except ImportError``, ``TYPE_CHECKING``, availability-flag, ``find_spec`` and function-level imports and any line with
``# optional-import-ok: <reason>`` (the rules of :mod:`py_ci_shared.optional_imports_guarded`). A third-party import is
reported when its distribution is declared in ``pyproject.toml`` (a dependency or any extra) but nothing the job installed
before the command provides it; the finding names the extra that declares it and the import chain from the entry.

Not judged, never silently: an import whose distribution the project does not declare (the conftest gate and
``optional_imports_guarded`` own that), a job that does not install the project itself, and a job with an install form this
reader cannot evaluate (``poetry install``, ``-r "$FILE"``, a third-party action that may install packages). ``name_map``
(``{"cv2": "opencv-python"}``) maps an import name to its distribution like in ``optional_imports_guarded``; ``exclude`` lists
workflow file names or path fragments to skip.

The dynamic twin, :func:`assert_entry_imports_without_extras`, is for the test suite of the repo: it starts the entries in a
subprocess in which the import names of every distribution of the NOT installed extras raise ``ModuleNotFoundError``, so a
guarded import the static reader misjudged (or a lazy path executed at import) still shows up::

    def test_the_nightly_starts_without_the_signal_extra():
        assert_entry_imports_without_extras(REPO_ROOT, [["mypkg.nightly", "--dry-run"], "mypkg"], blocked_extras=["signal", "gpu"])
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._ci_install_parts import Provided, Repo, add_project, add_target, dependency_group, norm
from ._core import Baseline, EmptyScanError, Finding, SourceError, UnparsedFilesError, parse_source
from ._import_graph import (
    MORE_ALIASES,
    Declared,
    ModuleImport,
    ModuleIndex,
    chain,
    distributions,
    installed_top_levels,
    module_level_imports,
    read_declared,
    reach,
)
from .ci_install_covers_conftest import (
    BUILTIN_ALIASES,
    _COVERAGE_VALUED,
    _UV_RUN_VALUED,
    _Context,
    _JobWalk,
    _is_python,
    _matrix_values,
    _program,
    _pytest_call,
    _read_yaml,
    _skip_options,
    _strip_wrappers,
    _workflow_files,
    _working_directory,
)

__all__ = [
    "RULE",
    "assert_ci_install_covers_entry_imports",
    "assert_entry_imports_without_extras",
    "blocked_import_names",
    "find_ci_install_covers_entry_imports",
]

RULE = "ci-install-missing-entry-import"
RULE_UNPARSED = "unparsed-file"
_TEST_FILE = re.compile(r"(test_.*|.*_test)\.py")
_UV_FLAG_VALUED = _UV_RUN_VALUED | {"--no-install-package", "--only-group", "--no-group", "--default-index", "--index-url"}
_PYTHON_VALUED = frozenset({"-W", "-X", "-Q", "--check-hash-based-pycs"})


# --------------------------------------------------------------------------------------------------------------------
# the entries of a job


@dataclass
class _Entry:
    kind: str  # "module" | "script" | "pytest"
    target: str  # dotted module, script path, or the pytest arguments
    args: list[str]
    cwd: Path
    line: int
    provided: Provided
    command: str


def _flags(args: Sequence[str], valued: Iterable[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """``(option, value)`` pairs and the positionals of a command line; ``--opt=value`` is split."""
    options: list[tuple[str, str]] = []
    positional: list[str] = []
    takes = frozenset(valued)
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg.startswith("--") and "=" in arg:
            name, _, value = arg.partition("=")
            options.append((name, value))
        elif arg.startswith("-") and arg != "-":
            if arg in takes and i < len(args):
                options.append((arg, args[i]))
                i += 1
            else:
                options.append((arg, ""))
        else:
            positional.append(arg)
            positional.extend(args[i:])
            break
    return options, positional


@dataclass
class _EntryWalk(_JobWalk):
    """A job walk that also records every entry command with what had been installed when it ran."""

    entries: list[_Entry] = field(default_factory=list)

    def command(self, argv: list[str], ctx: _Context, cwd: Path, line: int, depth: int) -> None:
        if argv and _program(argv[0]) == "uv" and argv[1:2] == ["run"]:
            self.uv_run(argv, cwd, line)
            return
        entry = self.entry_of(argv, cwd, line, self.provided)
        if entry is not None:
            self.entries.append(entry)
        super().command(argv, ctx, cwd, line, depth)

    def uv(self, rest: list[str], cwd: Path) -> None:
        if rest[:1] == ["sync"]:
            options, _ = _flags(rest[1:], _UV_FLAG_VALUED)
            self.sync(self.provided, cwd, options)
            return
        super().uv(rest, cwd)

    def project_dir(self, cwd: Path) -> Optional[Path]:
        for directory in [cwd, *cwd.parents]:
            data = self.repo.toml(directory / "pyproject.toml") if (directory / "pyproject.toml").is_file() else None
            if data is not None and isinstance(data.get("project"), dict):
                return directory
            if directory.resolve() == self.repo.root.resolve():
                break
        return None

    def sync(self, provided: Provided, cwd: Path, options: Sequence[tuple[str, str]]) -> None:
        """What ``uv sync``/``uv run`` installs: the project, the ``--extra`` extras (every one with ``--all-extras``) and the
        default ``dev`` group unless ``--no-dev``."""
        names = {name for name, _ in options}
        directory = self.project_dir(cwd)
        if directory is None or not self.repo.inside(directory):
            provided.unresolved.append("`uv sync` of a project this check cannot find")
            return
        data = self.repo.toml(directory / "pyproject.toml") or {}
        raw_project = data.get("project")
        project: dict[str, Any] = raw_project if isinstance(raw_project, dict) else {}
        extras = [value for name, value in options if name == "--extra"]
        if "--all-extras" in names:
            extras = [str(e) for e in (project.get("optional-dependencies") or {})]
            tool = data.get("tool")
            dynamic = ((tool.get("setuptools") or {}).get("dynamic") or {}) if isinstance(tool, dict) else {}
            extras += [str(e) for e in (dynamic.get("optional-dependencies") or {})] if isinstance(dynamic, dict) else []
        add_project(self.repo, directory, extras, provided)
        groups = [value for name, value in options if name in ("--group", "--only-group")]
        if "--no-dev" not in names and "--no-default-groups" not in names and "--only-group" not in names and "dev" in (data.get("dependency-groups") or {}):
            groups.append("dev")
        for group in dict.fromkeys(groups):
            dependency_group(self.repo, directory / "pyproject.toml", group, provided)

    def uv_run(self, argv: list[str], cwd: Path, line: int) -> None:
        options, rest = _flags(argv[2:], _UV_RUN_VALUED)
        names = {name for name, _ in options}
        provided = self.provided.copy()
        if not names & {"--no-sync", "--frozen", "--no-project", "--isolated", "--script"}:
            self.sync(provided, cwd, options)
        for name, value in options:
            if name == "--with":
                add_target(self.repo, value, cwd, provided)
        entry = self.entry_of(_strip_wrappers(rest), cwd, line, provided)
        if entry is not None:
            entry.command = " ".join(argv[:6])
            self.entries.append(entry)

    def entry_of(self, argv: list[str], cwd: Path, line: int, provided: Provided) -> Optional[_Entry]:
        argv = _strip_wrappers(argv)
        if not argv:
            return None
        shown = " ".join(argv[:6])
        call = _pytest_call(argv)
        if call is not None:
            return _Entry("pytest", " ".join(call.args), list(call.args), cwd, line, provided.copy(), shown)
        target = _python_target(argv)
        return None if target is None else _Entry(target[0], target[1], target[2], cwd, line, provided.copy(), shown)


def _python_target(argv: list[str]) -> Optional[tuple[str, str, list[str]]]:
    """``(kind, target, arguments)`` of ``python -m module ...`` / ``python script.py ...`` (also through ``coverage run``
    and a bare ``script.py``); None for anything else, and for a target only known at run time."""
    program = _program(argv[0])
    if program == "coverage" and argv[1:2] == ["run"]:
        argv = ["python", *_skip_options(argv[2:], _COVERAGE_VALUED, stop="-m")]
    elif argv[0].endswith(".py"):
        argv = ["python", *argv]
    elif not _is_python(program):
        return None
    rest = argv[1:]
    while rest and rest[0].startswith("-") and rest[0] != "-m":
        if rest[0] == "-c":
            return None
        rest = rest[2:] if rest[0] in _PYTHON_VALUED else rest[1:]
    if not rest or "\x00" in rest[0] or (rest[0] == "-m" and (len(rest) < 2 or "\x00" in rest[1])):
        return None
    if rest[0] == "-m":
        return "module", rest[1], rest[2:]
    return ("script", rest[0], rest[1:]) if rest[0].endswith(".py") else None


def _job_entries(repo: Repo, path: Path) -> Optional[list[tuple[str, _Entry]]]:
    """``(job id, entry)`` for every entry command of one workflow; None when the file cannot be read."""
    loaded = _read_yaml(repo, path)
    if loaded is None:
        return None
    text, data = loaded
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        repo.problems.append(Finding(repo.rel(path), 1, RULE_UNPARSED, "unparsable: no `jobs:` mapping"))
        return None
    base = _Context(repo.root)
    base = base.child(base.literal_env(data.get("env")))
    out: list[tuple[str, _Entry]] = []
    for job_id, job in data["jobs"].items():
        if not isinstance(job, dict) or "steps" not in job:
            continue
        ctx = _Context(repo.root, dict(base.env), _matrix_values(job))
        ctx = ctx.child(ctx.literal_env(job.get("env")))
        walk = _EntryWalk(repo, repo.rel(path), str(job_id), [None], text=text)
        walk.steps(job["steps"], ctx, _working_directory(job.get("defaults")) or _working_directory(data.get("defaults")))
        out.extend((str(job_id), entry) for entry in walk.entries)
    return out


# --------------------------------------------------------------------------------------------------------------------
# what an entry loads


def _pytest_files(repo: Repo, entry: _Entry) -> list[Path]:
    """The test files a ``pytest <paths>`` command collects: each existing path, a directory's ``test_*.py``, minus ``--ignore``."""
    ignored: list[Path] = []
    candidates: list[str] = []
    args = iter(entry.args)
    for arg in args:
        if arg == "--ignore":
            ignored.append((entry.cwd / next(args, "")).resolve())
        elif arg.startswith("--ignore="):
            ignored.append((entry.cwd / arg.split("=", 1)[1]).resolve())
        elif not arg.startswith("-"):
            candidates.append(arg.split("::")[0])
    files: list[Path] = []
    for name in candidates:
        target = entry.cwd / name
        if not target.exists() or not repo.inside(target):
            continue
        found = [target] if target.is_file() else sorted(p for p in target.rglob("*.py") if _TEST_FILE.fullmatch(p.name))
        files.extend(p for p in found if not any(p.resolve() == i or i in p.resolve().parents for i in ignored))
    return files


def _start_files(index: ModuleIndex, repo: Repo, entry: _Entry, include_pytest: bool) -> list[Path]:
    if entry.kind == "module":
        files = index.resolve(entry.target)
        if files and files[-1].name == "__init__.py" and (files[-1].parent / "__main__.py").is_file():
            files.append(files[-1].parent / "__main__.py")
        return files
    if entry.kind == "script":
        script = entry.cwd / entry.target
        return [script] if script.is_file() and repo.inside(script) else []
    return _pytest_files(repo, entry) if include_pytest else []


@dataclass
class _Gap:
    workflow: str
    job: str
    line: int
    entry: _Entry
    dist: str
    extras: list[str]
    sites: list[tuple[str, int, str, str]] = field(default_factory=list)  # (file, line, import, chain)

    def finding(self) -> Finding:
        file, line, module, how = self.sites[0]
        more = len(self.sites) - 1
        extras = ", ".join(repr(e) for e in self.extras)
        message = (
            f"job {self.job!r} runs `{self.entry.command}`, which imports {module.split('.')[0]!r} at module level ({file}:{line}"
            f"{'; ' + how if ' -> ' in how else ''})"
            + (f", and {more} more import(s) of it" if more > 0 else "")
            + f", but its install steps never provide {self.dist!r}, which only the {extras} extra declares: ModuleNotFoundError when it starts. "
            "Install that extra in the job, or make the import lazy or guarded"
        )
        return Finding(self.workflow, self.line, RULE, message, key=f"{RULE}::{self.workflow}::{self.job}::{self.dist}")


@dataclass
class _Run:
    """What one scan shares between its entries: the project, the findings so far and the per-file import cache."""

    repo: Repo
    declared: Declared
    name_map: Mapping[str, Any]
    include_pytest: bool
    gaps: dict[tuple[str, str, str], _Gap] = field(default_factory=dict)
    sources: dict[Path, list[ModuleImport]] = field(default_factory=dict)

    def imports_of(self, path: Path) -> list[ModuleImport]:
        key = path.resolve()
        if key not in self.sources:
            try:
                source, tree = parse_source(key)
                self.sources[key] = module_level_imports(tree, source.splitlines())
            except SourceError as exc:
                self.repo.problems.append(Finding(self.repo.rel(key), exc.line or 1, RULE_UNPARSED, f"{exc.kind}: {exc.message}"))
                self.sources[key] = []
        return self.sources[key]

    def missing_distribution(self, module: str, available: frozenset[str]) -> Optional[str]:
        """The extra-only distribution of *module* that nothing the job installed provides, else None."""
        candidates = distributions(module, self.declared, self.name_map)
        if any(c in available or c in self.declared.core for c in candidates):
            return None
        return next((c for c in candidates if c in self.declared.every_extra), None)


def _judge(run: _Run, workflow: str, job: str, entry: _Entry) -> None:
    repo, declared, provided = run.repo, run.declared, entry.provided
    if provided.unresolved or declared.project not in provided.dists:
        return
    roots = [entry.cwd, entry.cwd / "src", repo.root, repo.root / "src"]
    starts = _start_files(ModuleIndex(roots), repo, entry, run.include_pytest)
    if not starts:
        return
    index = ModuleIndex([*([p.parent for p in starts] if entry.kind == "script" else []), *roots])
    parents, third = reach(index, starts, run.imports_of)
    available = frozenset(provided.available(None))
    missing = {module: run.missing_distribution(module, available) for module in {site.module for site in third}}
    for site in third:
        dist = missing[site.module]
        if dist is None:
            continue
        gap = run.gaps.setdefault((workflow, job, dist), _Gap(workflow, job, entry.line, entry, dist, declared.extras_of(dist)))
        if not any(known[:2] == (repo.rel(site.path), site.line) for known in gap.sites):
            gap.sites.append((repo.rel(site.path), site.line, site.module, chain(parents, site.path, index)))


def find_ci_install_covers_entry_imports(
    root: Union[str, Path],
    *,
    name_map: Optional[Mapping[str, Any]] = None,
    exclude: Iterable[str] = (),
    include_pytest: bool = True,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> list[Finding]:
    """Every job whose entry command imports, at module level, a package that only an extra it did not install provides.

    ``min_files`` is the floor on workflow files read (``EmptyScanError`` below it); an unreadable workflow, pyproject or
    imported module raises ``UnparsedFilesError`` unless *allow_unparsed*. The ``pyproject.toml`` at *root* is required.
    """
    base = Path(root)
    declared = read_declared(base, base / "pyproject.toml")
    repo = Repo(base.resolve())
    skipped = tuple(exclude)
    run = _Run(repo, declared, dict(name_map or {}), include_pytest)
    read = 0
    for path in _workflow_files(base):
        rel = path.relative_to(base).as_posix()
        if any(fragment in rel for fragment in skipped):
            continue
        entries = _job_entries(repo, path)
        if entries is None:
            continue
        read += 1
        for job, entry in entries:
            _judge(run, rel, job, entry)
    problems = list(repo.problems)
    if problems and not allow_unparsed:
        raise UnparsedFilesError(
            f"{len(problems)} file(s) could not be read or parsed, so they were not checked:\n  " + "\n  ".join(p.render() for p in problems)
        )
    if read < min_files:
        raise EmptyScanError(f"only {read} workflow file(s) read under {base / '.github' / 'workflows'}; expected at least {min_files}")
    return sorted((gap.finding() for gap in run.gaps.values()), key=lambda f: (f.path, f.line, f.message))


def assert_ci_install_covers_entry_imports(
    root: Union[str, Path],
    *,
    name_map: Optional[Mapping[str, Any]] = None,
    exclude: Iterable[str] = (),
    include_pytest: bool = True,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Fail on a job whose entry imports an extra the job does not install; with *baseline_path*, only on new ones."""
    found = find_ci_install_covers_entry_imports(
        root, name_map=name_map, exclude=exclude, include_pytest=include_pytest, min_files=min_files, allow_unparsed=allow_unparsed
    )
    guidance = "install the extra the entry needs in that job, or make the module-level import lazy or guarded"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="ci_install_covers_entry_imports", refresh_command="PY_CI_SHARED_REFRESH=ci_install_covers_entry_imports")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(
            f"{len(found)} CI job(s) run an entry that imports a package its install steps do not provide; {guidance}:\n  "
            + "\n  ".join(f.render() for f in found)
        )


# --------------------------------------------------------------------------------------------------------------------
# the dynamic twin


_BLOCKER = """
import importlib, importlib.abc, runpy, sys

BLOCKED = set(sys.argv[1].split(","))
KIND, TARGET = sys.argv[2], sys.argv[3]
sys.argv = [TARGET] + sys.argv[4:]


class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError("blocked by the test: " + fullname, name=fullname)
        return None


sys.meta_path.insert(0, Blocker())
if KIND == "import":
    importlib.import_module(TARGET)
else:
    runpy.run_module(TARGET, run_name="__main__", alter_sys=True)
"""


def blocked_import_names(root: Union[str, Path], blocked_extras: Iterable[str], name_map: Optional[Mapping[str, Any]] = None) -> list[str]:
    """Import names of every distribution the *blocked_extras* add beyond ``[project.dependencies]``.

    A name comes from *name_map* (inverted: ``{"pywt": "pywavelets"}``), the built-in alias table, the metadata of the
    distributions installed here, and the distribution name with ``-``/``.`` read as ``_``. Raises ``AssertionError`` for an
    extra the project does not declare and when nothing would be blocked (a test that blocks nothing proves nothing)."""
    base = Path(root)
    declared = read_declared(base, base / "pyproject.toml")
    wanted = list(dict.fromkeys(blocked_extras))
    unknown = [e for e in wanted if e not in declared.extras]
    if unknown:
        raise AssertionError(f"{base / 'pyproject.toml'} declares no extra named {unknown}; its extras are {sorted(declared.extras)}")
    dists = sorted(set().union(*(declared.extras[e] for e in wanted)) - declared.core) if wanted else []
    names: set[str] = set()
    tables: list[Mapping[str, Any]] = [dict(name_map or {}), BUILTIN_ALIASES, MORE_ALIASES]
    for dist in dists:
        names.add(re.sub(r"[-.]+", "_", dist))
        for table in tables:
            names.update(imp for imp, value in table.items() if dist in {norm(v) for v in ([value] if isinstance(value, str) else value)} and "." not in imp)
    names.update(installed_top_levels(tuple(dists)))
    if not names:
        raise AssertionError(f"extras {wanted} add no distribution beyond the core dependencies, so nothing would be blocked")
    return sorted(names)


def assert_entry_imports_without_extras(
    root: Union[str, Path],
    entries: Sequence[Union[str, Sequence[str]]],
    blocked_extras: Iterable[str],
    *,
    name_map: Optional[Mapping[str, Any]] = None,
    timeout: float = 600.0,
    python_path: Optional[Sequence[Union[str, Path]]] = None,
) -> None:
    """Start each entry in a subprocess where the import names of every distribution of *blocked_extras* are unimportable.

    An entry is a module name (``"mypkg"``: imported) or an argument list (``["mypkg.nightly", "--dry-run"]``: run like
    ``python -m mypkg.nightly --dry-run``). Raises ``AssertionError`` with the tail of the traceback for each entry that
    does not exit 0. ``python_path`` defaults to the repo root and its ``src``; the interpreter is the running one.
    """
    base = Path(root).resolve()
    names = blocked_import_names(base, blocked_extras, name_map)
    paths = [str(p) for p in (python_path if python_path is not None else [base / "src", base])]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([*paths, os.environ.get("PYTHONPATH", "")]).strip(os.pathsep), "PYTHONIOENCODING": "utf-8"}
    failures: list[str] = []
    for entry in entries:
        kind, target, args = ("import", entry, []) if isinstance(entry, str) else ("run", entry[0], list(entry[1:]))
        proc = subprocess.run(
            [sys.executable, "-c", _BLOCKER, ",".join(names), kind, target, *args],
            cwd=base,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            failures.append(f"{kind} {target} {' '.join(args)}".strip() + f" exited {proc.returncode}:\n{proc.stderr[-1500:]}")
    if failures:
        raise AssertionError(
            f"with {sorted(set(blocked_extras))} unimportable ({len(names)} names blocked), {len(failures)} entry(ies) fail to start:\n  "
            + "\n  ".join(failures)
        )
