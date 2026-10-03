"""Every CI job that runs pytest installs what the repo's ``conftest.py`` files import when pytest starts.

A ``conftest.py`` is imported before a single test is collected, and so are the session hooks it defines
(``pytest_addoption``, ``pytest_configure``, ...). When one of them imports a third-party package that the job never
installed, the job dies at collection with ``ModuleNotFoundError`` and no test runs. Three regressions of exactly this
shape landed in four days (2026-09/10): pyutilz's ``numba-coverage.yml`` installed ``-e ".[...,dev]"`` but dropped the
``-r requirements-dev.txt`` that carries the py-ci-shared pin, while ``tests/conftest.py`` imports
``py_ci_shared.code_audit_meta`` inside ``pytest_addoption``; the nightly was red for five days before anyone looked.

The check is static. For every job of ``.github/workflows/*.yml`` with a ``run:`` step that invokes pytest
(``pytest``, ``python -m pytest``, ``uv run pytest``, ``coverage run -m pytest``, ...), it collects what the job's
earlier commands install:

* ``pip install`` / ``python -m pip install`` / ``uv pip install`` with requirement names (version specifiers, extras
  and environment markers parsed), ``-r file`` (recursive, relative to the working directory), ``--group``
  (PEP 735), ``-e .[extras]`` / ``.[extras]`` (the project itself, ``[project].dependencies`` and the named
  ``[project.optional-dependencies]``, self-references such as ``pkg[other]`` included), ``name @ git+https://...``,
  ``git+https://...#egg=name`` and a local path outside the checkout (read as the distribution its directory names);
* ``uv sync``, ``uv export`` and ``uv run`` (which syncs first) with a ``uv.lock`` present: every package in the
  lock is treated as provided, since the lock pins the full transitive set;
* local composite actions (``uses: ./.github/actions/x``, ``inputs`` substituted), py-ci-shared's own
  ``install-pyutilz`` action, and local shell scripts (``bash scripts/x.sh``), whose commands are read in place;
  ``cd`` inside a step moves the directory later relative paths resolve against.

What is REQUIRED is every third-party top-level import that runs when pytest loads each ``conftest.py`` the
invocation reaches (the conftests between its rootdir and its test paths; ``--ignore`` honoured): imports at module
level and in the session hooks, the modules a conftest's ``pytest_plugins`` names and every ``-p <plugin>`` on the
pytest command line, minus those inside a ``try`` that catches ``ImportError`` and those under
``if TYPE_CHECKING``. An import under ``if sys.version_info >= (3, 9):`` is required only on the job's Python
versions the guard admits, and a provided requirement with a ``; python_version ...`` marker counts only on the
versions it admits (the job's versions come from ``actions/setup-python`` and the matrix). Stdlib and first-party
names are dropped; an import name maps to its distribution through a built-in alias table (``yaml`` -> ``pyyaml``),
the caller's ``aliases`` and ``-``/``_``/case normalisation, and a name the repo declares or installs anywhere counts
as known. The running interpreter's installed metadata is NOT consulted for the verdict: it made the rule and the
baseline key depend on which interpreter ran the gate (``ci-install-missing::...::gitpython`` on a developer machine,
``ci-install-unmapped::...::git`` in CI); it only adds a hint to an unmapped finding's message. Distributions a
provided package pulls in transitively are not known, so a conftest import of one must be installed explicitly.

Three finding kinds, never a silent pass:

* ``ci-install-missing``: the job installs no distribution that provides the import;
* ``ci-install-unmapped``: the import's distribution is unknown (add it to ``aliases``);
* ``ci-install-unevaluated``: something is missing, but the job also installs through a form this check cannot
  read (``poetry install``, ``conda``, ``-r "$VAR"``, an extra built from a matrix value, a third-party action), or
  the job calls no pytest itself but runs ``tox``/``nox``/``make test`` (whose environments it does not read); a
  repo acknowledges that per job with ``acknowledge={"ci.yml::test": "why"}``.

Usage::

    from py_ci_shared.ci_install_covers_conftest import assert_ci_install_covers_conftest

    def test_ci_jobs_install_what_conftest_imports():
        assert_ci_install_covers_conftest(REPO_ROOT)

or ``[tool.py_ci_shared.gates.ci_install_covers_conftest]`` with ``aliases``/``first_party``/``acknowledge``/``baseline_path``.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from ._ci_install_parts import (
    ConftestImport,
    Provided,
    Repo,
    Version,
    add_project,
    conftest_imports,
    dependency_group,
    installed_map,
    is_stdlib,
    norm,
    parse_version,
    pip_install,
)
from ._core import Baseline, Finding, SourceError, read_source, refresh_requested, scan_python

__all__ = [
    "BUILTIN_ALIASES",
    "PytestRun",
    "assert_ci_install_covers_conftest",
    "collect_ci_install_gaps",
    "find_ci_install_gaps",
]

RULE_MISSING = "ci-install-missing"
RULE_UNMAPPED = "ci-install-unmapped"
RULE_UNEVALUATED = "ci-install-unevaluated"
RULE_UNPARSED = "unparsed-file"
REFRESH_FLAG = "--refresh-ci-install-covers-conftest-baseline"

#: import name -> distribution name, for the common packages whose two names differ.
BUILTIN_ALIASES: dict[str, str] = {
    "_pytest": "pytest",
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "fitz": "pymupdf",
    "google.protobuf": "protobuf",
    "jose": "python-jose",
    "jwt": "pyjwt",
    "magic": "python-magic",
    "multipart": "python-multipart",
    "OpenSSL": "pyopenssl",
    "PIL": "pillow",
    "pkg_resources": "setuptools",
    "psycopg2": "psycopg2-binary",
    "py_ci_shared": "py-ci-shared",
    "pytest_asyncio": "pytest-asyncio",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "slugify": "python-slugify",
    "telegram": "python-telegram-bot",
    "win32api": "pywin32",
    "win32con": "pywin32",
    "yaml": "pyyaml",
    "zmq": "pyzmq",
}

#: distributions that provide one import name under several names (any one of them satisfies it).
_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "psycopg2-binary": ("psycopg2-binary", "psycopg2"),
    "opencv-python": ("opencv-python", "opencv-python-headless", "opencv-contrib-python", "opencv-contrib-python-headless"),
    "pillow": ("pillow", "pillow-simd"),
}

#: remote composite actions whose install command is known: ``owner/repo/path`` -> the ``run:`` it executes.
#: tests/test_ci_install_covers_conftest.py holds the install-pyutilz entry equal to this repo's own action.yml.
KNOWN_ACTIONS: dict[str, tuple[str, dict[str, str]]] = {
    "fingoldo/py-ci-shared/.github/actions/install-pyutilz": (
        'uv pip install --system "./pyutilz[${INPUTS_PYUTILZ_EXTRAS}]" -e "${INPUTS_PROJECT_EXTRAS}"',
        {"INPUTS_PYUTILZ_EXTRAS": "pyutilz-extras", "INPUTS_PROJECT_EXTRAS": "project-extras"},
    ),
}

#: owners (or ``owner/repo/path``) of remote actions that install no Python package into the job's interpreter.
_QUIET_ACTIONS = frozenset(
    {
        "actions", "astral-sh", "github", "codecov", "docker", "pypa", "softprops", "peaceiris", "dorny", "step-security",
        "EnricoMi", "peter-evans", "mikepenz", "irongut", "marocchino", "reviewdog", "pre-commit", "zizmorcore", "rhysd",
        "crate-ci", "codespell-project", "dtolnay", "Swatinem", "subosito", "browser-actions", "ncipollo", "jlumbroso",
        "fingoldo/py-ci-shared/.github/actions/upload-codecov", "fingoldo/py-ci-shared/.github/workflows",
    }
)  # fmt: skip

_NOT_COLLECTED = frozenset({".git", ".hg", ".tox", ".nox", ".venv", "venv", "env", "node_modules", "build", "dist", "__pycache__", "site-packages"})
_OUTSIDE = Path("/__outside_the_checkout__")
_EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
_SHELL_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_UNKNOWN_INSTALLERS = (
    ("poetry", "install"), ("poetry", "add"), ("pipenv", "install"), ("pipenv", "sync"), ("conda", "install"), ("conda", "create"),
    ("conda", "env"), ("mamba", "install"), ("mamba", "create"), ("mamba", "env"), ("micromamba", "install"), ("micromamba", "create"),
    ("pdm", "install"), ("pdm", "sync"), ("hatch", "env"), ("pip-sync", ""), ("easy_install", ""), ("pixi", "install"),
)  # fmt: skip
_WRAPPERS = frozenset({"sudo", "time", "nice", "nohup", "exec", "command", "stdbuf", "xvfb-run", "env", "then", "do", "else", "if", "while", "until", "!", "{"})
_UV_RUN_VALUED = frozenset(
    {"--with", "--extra", "--group", "--python", "-p", "--directory", "--project", "--package", "--env-file", "--with-requirements", "--index"}
)
_COVERAGE_VALUED = frozenset({"--rcfile", "--source", "--include", "--omit", "--data-file", "--context"})


# --------------------------------------------------------------------------------------------------------------------
# YAML with line numbers


class _LineDict(dict):  # type: ignore[type-arg]
    line: int = 0
    key_lines: dict[str, int]


class _LineLoader(yaml.SafeLoader):
    """A SafeLoader whose mappings remember their own line and each key's line (1-based)."""


def _construct_mapping(loader: _LineLoader, node: yaml.MappingNode) -> _LineDict:
    loader.flatten_mapping(node)
    out = _LineDict()
    out.line = node.start_mark.line + 1
    out.key_lines = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        out[key] = loader.construct_object(value_node, deep=True)
        out.key_lines[str(key)] = key_node.start_mark.line + 1
    return out


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _load_yaml(path: Path) -> tuple[str, Any]:
    text = read_source(path)
    return text, yaml.load(text, Loader=_LineLoader)  # nosec B506 -- a SafeLoader subclass that only adds line numbers


def _yaml_problem(repo: Repo, path: Path, exc: yaml.YAMLError) -> Finding:
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    first = str(exc).splitlines()[0] if str(exc) else ""
    return Finding(repo.rel(path), (mark.line + 1) if mark is not None else 1, RULE_UNPARSED, f"unparsable: {type(exc).__name__}: {first}")


def _read_yaml(repo: Repo, path: Path) -> Optional[tuple[str, Any]]:
    """``(text, data)``, or None after recording the file as unreadable."""
    try:
        return _load_yaml(path)
    except SourceError as exc:
        repo.problems.append(Finding(repo.rel(path), exc.line or 1, RULE_UNPARSED, f"{exc.kind}: {exc.message}"))
    except yaml.YAMLError as exc:
        repo.problems.append(_yaml_problem(repo, path, exc))
    return None


# --------------------------------------------------------------------------------------------------------------------
# shell scripts


def _logical_lines(script: str) -> Iterator[tuple[int, str]]:
    """``(first line index, text)`` with ``\\`` and PowerShell backtick continuations joined. A heredoc body is yielded
    once, prefixed with ``\\x01``, so a ``"-m", "pytest"`` inside a Python heredoc is still seen but never run as shell."""
    lines = script.splitlines()
    i = 0
    while i < len(lines):
        start, text = i, lines[i]
        while text.rstrip().endswith(("\\", " `")) and i + 1 < len(lines):
            i += 1
            text = text.rstrip()[:-1] + " " + lines[i]
        i += 1
        yield start, text
        heredoc = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", text)
        if heredoc:
            end = next((j for j in range(i, len(lines)) if lines[j].strip() == heredoc.group(1)), len(lines))
            yield start, "\x01" + "\n".join(lines[i:end])
            i = end + 1


def _split_commands(text: str) -> list[str]:
    """Split one logical line on unquoted ``&&``, ``||``, ``;``, ``|``, ``&``, parentheses and backticks; drop a comment."""
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    for j, ch in enumerate(text):
        if quote:
            buf.append(ch)
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "#" and (not buf or buf[-1].isspace()):
            break
        elif ch in ";|()&`" and not (ch == "(" and text[j - 1 : j] == "$"):
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [c.strip() for c in out if c.strip()]


@dataclass
class _Context:
    """What a ``${{ }}`` expression or ``$VAR`` can be resolved against."""

    root: Path
    env: dict[str, Optional[str]] = field(default_factory=dict)
    matrix: dict[str, list[Any]] = field(default_factory=dict)
    inputs: dict[str, Optional[str]] = field(default_factory=dict)

    def expression(self, inner: str) -> Optional[str]:
        kind, _, name = inner.partition(".")
        if inner == "github.workspace":
            return self.root.as_posix()
        if kind == "matrix" and len(self.matrix.get(name, [])) == 1:
            return str(self.matrix[name][0])
        if kind == "inputs":
            return self.inputs.get(name)
        if kind == "env":
            return self.env.get(name)
        return None

    def substitute(self, text: str) -> str:
        """Replace what is known; an unknown expression or variable becomes ``\\x00name\\x00``."""

        def expr(m: re.Match[str]) -> str:
            value = self.expression(m.group(1))
            return value if value is not None else f"\x00{m.group(1)}\x00"

        def var(m: re.Match[str]) -> str:
            name = m.group(1) or m.group(2)
            value = self.root.as_posix() if name == "GITHUB_WORKSPACE" else self.env.get(name)
            return value if value is not None else f"\x00${name}\x00"

        return _SHELL_VAR.sub(var, _EXPR.sub(expr, text))

    def literal_env(self, mapping: Any) -> dict[str, Optional[str]]:
        out: dict[str, Optional[str]] = {}
        for key, value in (mapping or {}).items() if isinstance(mapping, dict) else ():
            resolved = self.substitute(str(value))
            out[str(key)] = None if "\x00" in resolved or "$(" in resolved else resolved
        return out

    def child(self, env: Mapping[str, Optional[str]], inputs: Optional[Mapping[str, Optional[str]]] = None) -> _Context:
        return _Context(self.root, {**self.env, **env}, self.matrix, dict(inputs) if inputs is not None else self.inputs)


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _strip_wrappers(argv: list[str]) -> list[str]:
    """Drop ``A=b`` assignments and wrappers (``env``, ``timeout 600``, ``xvfb-run -a``, ...) in front of a command."""
    i = 0
    while i < len(argv):
        word = argv[i]
        if re.match(r"^[A-Za-z_]\w*=", word):
            i += 1
        elif word in _WRAPPERS or word == "timeout":
            i += 1
            while i < len(argv) and (argv[i].startswith("-") or (word == "timeout" and argv[i][:1].isdigit())):
                i += 1
        else:
            break
    return argv[i:]


def _program(word: str) -> str:
    return re.split(r"[\\/]", word)[-1].lower().removesuffix(".exe")


def _is_python(word: str) -> bool:
    return bool(re.fullmatch(r"(python|python3|python3\.\d+|py|pypy3?)", _program(word)))


def _skip_options(rest: list[str], valued: frozenset[str], stop: str = "") -> list[str]:
    while rest and rest[0].startswith("-") and rest[0] != stop:
        rest = rest[2:] if rest[0] in valued else rest[1:]
    return rest


def _python_m(argv: list[str]) -> Optional[list[str]]:
    """``[module, *args]`` for ``python [opts] -m module ...`` / ``coverage run [opts] -m module ...``; else None."""
    rest = argv[1:]
    if _program(argv[0]) == "coverage":
        if rest[:1] != ["run"]:
            return None
        rest = rest[1:]
    rest = _skip_options(rest, _COVERAGE_VALUED, stop="-m")
    return rest[1:] if len(rest) >= 2 and rest[0] == "-m" else None


@dataclass(frozen=True)
class _PytestCall:
    args: list[str]
    uv_run: bool


def _pytest_call(argv: list[str]) -> Optional[_PytestCall]:
    """The pytest arguments when *argv* runs pytest in the job's environment (through any of the usual launchers)."""
    uv_run = False
    for _ in range(8):
        argv = _strip_wrappers(argv)
        prog = _program(argv[0]) if argv else ""
        if prog in ("pytest", "py.test"):
            return _PytestCall(argv[1:], uv_run)
        if _is_python(prog) or prog == "coverage":
            module = _python_m(argv)
            if module and module[0] == "pytest":
                return _PytestCall(module[1:], uv_run)
            if not module or module[0] != "coverage":
                return None
            argv = module
        elif prog == "uv" and argv[1:2] == ["run"]:
            uv_run = "--no-sync" not in argv
            argv = _skip_options(argv[2:], _UV_RUN_VALUED)
        elif prog in ("poetry", "pdm", "hatch", "pipenv", "rye") and argv[1:2] == ["run"]:
            argv = argv[2:]
        else:
            return None
    return None


# --------------------------------------------------------------------------------------------------------------------
# workflows and jobs


@dataclass
class PytestRun:
    """One pytest invocation in one job, with what the job has installed by then."""

    workflow: str
    job: str
    line: int
    cwd: Path
    args: list[str]
    provided: Provided
    pythons: list[Version]


def _matrix_values(job: Mapping[str, Any]) -> dict[str, list[Any]]:
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict):
        return {}
    out: dict[str, list[Any]] = {str(k): list(v) if isinstance(v, list) else [v] for k, v in matrix.items() if k not in ("include", "exclude")}
    for entry in matrix.get("include") or []:
        for key, value in entry.items() if isinstance(entry, dict) else ():
            values = out.setdefault(str(key), [])
            if value not in values:
                values.append(value)
    return out


def _versions(raw: Any, matrix: Mapping[str, list[Any]]) -> list[Version]:
    """The ``(major, minor)`` versions a ``python-version`` input names; ``[None]`` when they cannot be known."""
    values: list[Any] = []
    for item in raw if isinstance(raw, list) else str(raw).split("\n"):
        text = str(item).strip()
        m = _EXPR.fullmatch(text)
        key = re.fullmatch(r"matrix\.([\w-]+)", m.group(1)) if m else None
        if m and (key is None or key.group(1) not in matrix):
            return [None]
        values.extend(matrix[key.group(1)] if key else [text] if text else [])
    out: list[Version] = []
    for v in values:
        parsed = parse_version(str(v))
        version: Version = (parsed[0], parsed[1]) if parsed and len(parsed) >= 2 else None
        if version not in out:
            out.append(version)
    return out or [None]


def _action_key(uses: str) -> str:
    return uses.split("@", 1)[0].rstrip("/")


def _quiet_action(key: str) -> bool:
    owner = key.split("/", 1)[0]
    return owner in _QUIET_ACTIONS or any(key.startswith(q) for q in _QUIET_ACTIONS if "/" in q)


@dataclass
class _JobWalk:
    """Walks one job's steps in order, applying installs and recording pytest invocations."""

    repo: Repo
    workflow: str
    job_id: str
    pythons: list[Version]
    provided: Provided = field(default_factory=Provided)
    runs: list[PytestRun] = field(default_factory=list)
    text: str = ""
    delegated: list[tuple[int, str]] = field(default_factory=list)  # tox/nox/make-test calls: (line, command)

    def steps(self, steps: Any, ctx: _Context, workdir: str, depth: int = 0) -> None:
        for step in steps if isinstance(steps, list) else []:
            if isinstance(step, dict):
                self.step(step, ctx, workdir, depth)

    def step(self, step: dict[str, Any], ctx: _Context, workdir: str, depth: int) -> None:
        uses = str(step.get("uses", ""))
        raw_with = step.get("with")
        with_: dict[str, Any] = raw_with if isinstance(raw_with, dict) else {}
        if uses.startswith("./") and depth < 5:
            self.local_action(uses, ctx, with_, workdir, depth)
        elif uses:
            self.remote_action(uses, ctx, with_)
        run = step.get("run")
        if not isinstance(run, str):
            return
        step_ctx = ctx.child(ctx.literal_env(step.get("env")))
        cwd = self.directory(self.repo.root, step_ctx.substitute(str(step.get("working-directory") or workdir or ".")))
        run_line = getattr(step, "key_lines", {}).get("run", getattr(step, "line", 1))
        lines = self.text.splitlines()
        header = lines[run_line - 1] if 0 < run_line <= len(lines) else ""
        self.script(run, step_ctx, cwd, run_line + (1 if re.match(r"^\s*-?\s*run:\s*[|>]", header) else 0), depth)

    def remote_action(self, uses: str, ctx: _Context, with_: Mapping[str, Any]) -> None:
        key = _action_key(uses)
        if "setup-python" in key and "python-version" in with_:
            self.pythons = _versions(with_["python-version"], ctx.matrix)
        elif key in KNOWN_ACTIONS:
            command, inputs = KNOWN_ACTIONS[key]
            env = {var: (None if with_.get(name) is None else ctx.substitute(str(with_[name]))) for var, name in inputs.items()}
            env = {k: (None if v is None or "\x00" in v else v) for k, v in env.items()}
            self.script(command, ctx.child(env), self.repo.root, getattr(with_, "line", 1) - 1, 0)
        elif not _quiet_action(key) and not uses.startswith("docker://"):
            self.provided.unresolved.append(f"action {key} may install packages; its steps are not read")

    def local_action(self, uses: str, ctx: _Context, with_: Mapping[str, Any], workdir: str, depth: int) -> None:
        base = self.repo.root / uses
        action = next((p for p in (base / "action.yml", base / "action.yaml") if p.is_file()), None)
        loaded = self.repo_yaml(action) if action is not None else None
        if loaded is None:
            if action is None:
                self.provided.unresolved.append(f"local action {uses} has no action.yml")
            return
        text, data = loaded
        runs = data.get("runs") if isinstance(data, dict) else None
        if not isinstance(runs, dict) or runs.get("using") != "composite":
            return
        declared = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
        inputs: dict[str, Optional[str]] = {}
        for name, spec in declared.items():
            raw = with_.get(name, spec.get("default") if isinstance(spec, dict) else None)
            value = None if raw is None else ctx.substitute(str(raw))
            inputs[str(name)] = None if value is None or "\x00" in value else value
        saved, self.text = self.text, text
        self.steps(runs.get("steps"), ctx.child({}, inputs), workdir, depth + 1)
        self.text = saved

    def repo_yaml(self, path: Path) -> Optional[tuple[str, Any]]:
        return _read_yaml(self.repo, path)

    def directory(self, cwd: Path, target: str) -> Path:
        """Where ``cd target`` (or a ``working-directory``) lands; a directory named at run time is outside the checkout."""
        if "\x00" not in target:
            return cwd / target
        tail = re.split(r"\x00[^\x00]*\x00", target)[-1].strip("/\\")
        return _OUTSIDE / tail if tail else _OUTSIDE

    def script(self, run: str, ctx: _Context, cwd: Path, first_line: int, depth: int) -> None:
        local = ctx.child({})
        for index, logical in _logical_lines(run):
            if logical.startswith("\x01"):
                if re.search(r"[\"']-m[\"']\s*,\s*[\"']pytest[\"']", logical):
                    self.record(first_line + index, cwd, [])
                continue
            for command in _split_commands(logical):
                assignment = re.fullmatch(r"(?:export\s+|local\s+)?([A-Za-z_]\w*)=(\S*)", command)
                if assignment:
                    value = local.substitute(assignment.group(2).strip("'\""))
                    local.env[assignment.group(1)] = None if "\x00" in value or "$(" in value else value
                    continue
                argv = _strip_wrappers(_tokens(local.substitute(command)))
                if argv and argv[0] == "cd":
                    cwd = self.directory(cwd, argv[1]) if len(argv) > 1 else self.repo.root
                elif argv:
                    self.command(argv, local, cwd, first_line + index, depth)

    def command(self, argv: list[str], ctx: _Context, cwd: Path, line: int, depth: int) -> None:
        call = _pytest_call(argv)
        if call is not None:
            if call.uv_run:
                self.lock(cwd, "uv run")
            self.record(line, cwd, call.args)
            return
        prog, rest = _program(argv[0]), argv[1:]
        if _is_python(prog) and rest[:2] == ["-m", "pip"]:
            prog, rest = "pip", rest[2:]
        if re.fullmatch(r"pip3?(\.\d+)?", prog) and rest[:1] == ["install"]:
            pip_install(self.repo, rest[1:], cwd, self.provided)
        elif prog == "uv":
            self.uv(rest, cwd)
        elif prog in ("bash", "sh", "source", ".") or argv[0].endswith(".sh"):
            self.shell_script(argv, ctx, cwd, line, depth)
        elif prog in ("tox", "nox") or (_is_python(prog) and rest[:2] in (["-m", "tox"], ["-m", "nox"])):
            self.delegated.append((line, " ".join(argv[:3])))
        elif prog == "make" and any(re.search(r"test|check", a) for a in rest if not a.startswith("-")):
            self.delegated.append((line, " ".join(argv[:3])))
        else:
            self.unknown_installer(prog, rest)

    def uv(self, rest: list[str], cwd: Path) -> None:
        if rest[:2] == ["pip", "install"]:
            pip_install(self.repo, rest[2:], cwd, self.provided)
        elif rest[:2] == ["pip", "sync"]:
            pip_install(self.repo, [x for f in rest[2:] if not f.startswith("-") for x in ("-r", f)], cwd, self.provided)
        elif rest[:1] in (["sync"], ["export"]):
            self.lock(cwd, f"uv {rest[0]}")
        elif rest[:1] == ["add"]:
            pip_install(self.repo, rest[1:], cwd, self.provided)

    def shell_script(self, argv: list[str], ctx: _Context, cwd: Path, line: int, depth: int) -> None:
        script = argv[0] if argv[0].endswith(".sh") else next((a for a in argv[1:] if not a.startswith("-")), "")
        path = cwd / script
        if not script or "\x00" in script or depth >= 5 or not self.repo.inside(path) or not path.is_file():
            return
        try:
            text = read_source(path)
        except SourceError as exc:
            self.repo.problems.append(Finding(self.repo.rel(path), exc.line or 1, RULE_UNPARSED, f"{exc.kind}: {exc.message}"))
            return
        self.script(text, ctx, cwd, line, depth + 1)

    def unknown_installer(self, prog: str, rest: list[str]) -> None:
        for tool, sub in _UNKNOWN_INSTALLERS:
            if prog == tool and (not sub or rest[:1] == [sub]):
                self.provided.unresolved.append(f"`{' '.join([prog, *rest[:2]])}` installs through {tool}, which this check does not read")
        if _is_python(prog) and rest[:1] == ["setup.py"] and rest[1:2] in (["install"], ["develop"]):
            self.provided.unresolved.append("`python setup.py install` is not read")

    def lock(self, cwd: Path, how: str) -> None:
        names = self.repo.lock_names(cwd) if self.repo.inside(cwd) else None
        if names is None:
            self.provided.unresolved.append(f"`{how}` with no uv.lock in the repo")
            return
        for name in names:
            self.provided.add(name)
        self.provided.notes.append(f"`{how}`: every package in uv.lock counted as installed")

    def record(self, line: int, cwd: Path, args: list[str]) -> None:
        self.runs.append(PytestRun(self.workflow, self.job_id, line, cwd, list(args), self.provided.copy(), list(self.pythons)))


def _workflow_files(root: Path) -> list[Path]:
    wf = root / ".github" / "workflows"
    return sorted(p for p in wf.glob("*") if p.suffix in (".yml", ".yaml") and p.is_file()) if wf.is_dir() else []


def _working_directory(block: Any) -> str:
    run = block.get("run") if isinstance(block, dict) else None
    return str(run.get("working-directory") or "") if isinstance(run, dict) else ""


def _workflow_runs(repo: Repo, path: Path) -> Optional[list[PytestRun]]:
    """The pytest runs of one workflow file; None when it cannot be read."""
    loaded = _read_yaml(repo, path)
    if loaded is None:
        return None
    text, data = loaded
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        repo.problems.append(Finding(repo.rel(path), 1, RULE_UNPARSED, "unparsable: no `jobs:` mapping"))
        return None
    base = _Context(repo.root)
    base = base.child(base.literal_env(data.get("env")))
    runs: list[PytestRun] = []
    for job_id, job in data["jobs"].items():
        if not isinstance(job, dict) or "steps" not in job:
            continue
        ctx = _Context(repo.root, dict(base.env), _matrix_values(job))
        ctx = ctx.child(ctx.literal_env(job.get("env")))
        walk = _JobWalk(repo, repo.rel(path), str(job_id), [None], text=text)
        walk.steps(job["steps"], ctx, _working_directory(job.get("defaults")) or _working_directory(data.get("defaults")))
        runs.extend(walk.runs)
        if walk.delegated and not walk.runs:
            repo.delegated.append((repo.rel(path), str(job_id), *walk.delegated[0]))
    return runs


def _pytest_runs(repo: Repo) -> tuple[list[PytestRun], int]:
    """Every pytest invocation in every workflow job, and the number of workflow files read."""
    runs: list[PytestRun] = []
    read = 0
    for path in _workflow_files(repo.root):
        found = _workflow_runs(repo, path)
        if found is not None:
            read += 1
            runs.extend(found)
    return runs, read


# --------------------------------------------------------------------------------------------------------------------
# which conftests a run loads


def _ini_testpaths(repo: Repo, rootdir: Path) -> list[str]:
    data = repo.toml(rootdir / "pyproject.toml") or {}
    paths = (((data.get("tool") or {}).get("pytest") or {}).get("ini_options") or {}).get("testpaths")
    if isinstance(paths, list):
        return [str(p) for p in paths]
    import configparser

    for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        parser = configparser.ConfigParser()
        try:
            parser.read_string(read_source(rootdir / name) if (rootdir / name).is_file() else "")
        except (configparser.Error, SourceError):
            continue
        if parser.has_option(section, "testpaths"):
            return parser.get(section, "testpaths").split()
    return []


def _rootdir(repo: Repo, cwd: Path) -> Path:
    for d in [cwd, *cwd.parents]:
        if any((d / n).is_file() for n in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")):
            return d
        if d.resolve() == repo.root.resolve():
            break
    return cwd


def _under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _targets_and_ignores(repo: Repo, run: PytestRun) -> tuple[list[Path], list[Path]]:
    targets: list[Path] = []
    ignored: list[Path] = []
    args = iter(run.args)
    for a in args:
        if a in ("--ignore", "--deselect"):
            value = next(args, "")
            ignored.extend([(run.cwd / value).resolve()] if a == "--ignore" else [])
        elif a.startswith("--ignore="):
            ignored.append((run.cwd / a.split("=", 1)[1]).resolve())
        elif not a.startswith("-") and "\x00" not in a.split("::")[0]:
            p = run.cwd / a.split("::")[0]
            targets.extend([p.resolve()] if p.exists() and repo.inside(p) else [])
    return targets, ignored


def _conftests_for(repo: Repo, run: PytestRun, all_conftests: Sequence[Path]) -> list[Path]:
    """The conftests pytest loads for *run*: those between its rootdir and its test paths, and those below them."""
    if not repo.inside(run.cwd):
        return []
    root = _rootdir(repo, run.cwd).resolve()
    targets, ignored = _targets_and_ignores(repo, run)
    if not targets:
        targets = [(root / t).resolve() for t in _ini_testpaths(repo, root) if (root / t).exists()] or [run.cwd.resolve()]
    chosen: list[Path] = []
    for conftest in all_conftests:
        parent = conftest.parent
        if any(_under(conftest, ig) for ig in ignored):
            continue
        # a directory target loads the conftests below it; any target loads those of its parents up to the rootdir
        below = any(t.is_dir() and _under(parent, t) for t in targets)
        if below or any(_under(t, parent) and _under(parent, root) for t in targets):
            chosen.append(conftest)
    return chosen


def _first_party(repo: Repo, conftest: Path, extra: Iterable[str]) -> set[str]:
    """Top-level names importable from the checkout itself near *conftest*: its directory and every parent up to the
    repo root (and their ``src/``), plus each enclosing project's own import name."""
    names = set(extra)
    levels: list[Path] = []
    for d in [conftest.parent, *conftest.parent.parents]:
        levels.extend([d, d / "src"])
        project = (repo.toml(d / "pyproject.toml") or {}).get("project")
        if isinstance(project, dict) and project.get("name"):
            names.add(str(project["name"]).replace("-", "_"))
        if d.resolve() == repo.root.resolve():
            break
    for level in levels:
        children = list(level.iterdir()) if level.is_dir() else []
        names.update(c.stem for c in children if not c.name.startswith(".") and c.name not in _NOT_COLLECTED and (c.is_dir() or c.suffix == ".py"))
    return names


# --------------------------------------------------------------------------------------------------------------------
# the check


def _candidates(imp: ConftestImport, aliases: Mapping[str, Any]) -> tuple[list[str], bool]:
    """``(distribution names that provide the import, confident)``: confident when a table or metadata named them."""
    found: list[str] = []
    dotted = imp.module.split(".")
    for table in (aliases, BUILTIN_ALIASES):
        keys = [".".join(dotted[:depth]) for depth in range(len(dotted), 0, -1)]
        hit = next((table[k] for k in keys if k in table), None)
        if hit is not None:
            found.extend([hit] if isinstance(hit, str) else list(hit))
            break
    out: list[str] = []
    for name in found:
        out.extend(norm(alt) for alt in _ALTERNATIVES.get(norm(name), (name,)) if norm(alt) not in out)
    if out:
        return out, True
    return [norm(imp.top)], False


def _versions_text(versions: Sequence[Version]) -> str:
    known = sorted({f"{v[0]}.{v[1]}" for v in versions if v is not None}, key=lambda s: tuple(int(x) for x in s.split(".")))
    return f" (python {', '.join(known)})" if known else ""


@dataclass
class _Gap:
    rule: str
    run: PytestRun
    imp: ConftestImport
    dist: str
    versions: list[Version] = field(default_factory=list)
    sites: list[ConftestImport] = field(default_factory=list)

    def finding(self, job_key: str) -> Finding:
        others = len(self.sites) - 1
        what = f"{self.imp.conftest}:{self.imp.line} imports {self.imp.module!r}" + (f" (and {others} more import(s) of it)" if others > 0 else "")
        job = f"job {self.run.job!r}{_versions_text(self.versions)}"
        if self.rule == RULE_UNMAPPED:
            here = installed_map().get(self.imp.top, ())
            hint = f" (the interpreter running this check has it from {', '.join(map(repr, here))})" if here else ""
            message = (
                f"{job} runs pytest, and {what} at collection, but no distribution is known for {self.imp.top!r}{hint}: add "
                f"aliases={{{self.imp.top!r}: '<distribution>'}} so its install can be checked"
            )
        elif self.rule == RULE_UNEVALUATED:
            reasons = "; ".join(dict.fromkeys(self.run.provided.unresolved))
            message = (
                f"{job} runs pytest, {what} at collection, and no install step this check can read provides {self.dist!r}; "
                f"it could not evaluate: {reasons}. Install it explicitly, or acknowledge={{{job_key!r}: '<why>'}}"
            )
        else:
            message = f"{job} runs pytest, but its install steps never install {self.dist!r}, and {what} when pytest starts: ModuleNotFoundError at collection"
        key = f"{self.rule}::{self.run.workflow}::{self.run.job}::{self.dist}"
        return Finding(self.run.workflow, self.run.line, self.rule, message, key=key)


@dataclass
class _Checker:
    repo: Repo
    aliases: Mapping[str, Any]
    acknowledged: Mapping[str, str]
    provided_anywhere: set[str]
    gaps: dict[tuple[str, int, str, str], _Gap] = field(default_factory=dict)

    def check(self, run: PytestRun, imp: ConftestImport) -> None:
        dists, confident = _candidates(imp, self.aliases)
        confident = confident or any(d in self.provided_anywhere for d in dists)
        job_key = f"{Path(run.workflow).name}::{run.job}"
        for python in run.pythons:
            if imp.guard(python) is False or any(d in run.provided.available(python) for d in dists):
                continue
            rule = RULE_UNEVALUATED if run.provided.unresolved else RULE_MISSING if confident else RULE_UNMAPPED
            if rule == RULE_UNEVALUATED and job_key in self.acknowledged:
                continue
            gap = self.gaps.setdefault((run.workflow, run.line, run.job, dists[0]), _Gap(rule, run, imp, dists[0]))
            gap.versions.append(python)
            if imp not in gap.sites:
                gap.sites.append(imp)


def _declared(repo: Repo) -> set[str]:
    """Every distribution the root ``pyproject.toml`` names (dependencies, every extra, every dependency group)."""
    declared = Provided()
    data = repo.toml(repo.root / "pyproject.toml") or {}
    raw_project = data.get("project")
    project: dict[str, Any] = raw_project if isinstance(raw_project, dict) else {}
    tool = data.get("tool")
    setuptools = tool.get("setuptools") if isinstance(tool, dict) else None
    dynamic = setuptools.get("dynamic") if isinstance(setuptools, dict) else None
    dynamic_extras = dynamic.get("optional-dependencies") if isinstance(dynamic, dict) else None
    extras = [*(project.get("optional-dependencies") or {}), *(dynamic_extras if isinstance(dynamic_extras, dict) else {})]
    add_project(repo, repo.root, extras, declared)
    for group in data.get("dependency-groups") or {}:
        dependency_group(repo, repo.root / "pyproject.toml", str(group), declared)
    return set(declared.dists)


def collect_ci_install_gaps(
    root: Path,
    *,
    aliases: Optional[Mapping[str, Any]] = None,
    first_party: Iterable[str] = (),
    acknowledge: Optional[Mapping[str, str]] = None,
) -> tuple[list[Finding], list[Finding], int]:
    """``(findings, unreadable files, workflows read)`` for the repo at *root*.

    *aliases* maps an import name (a dotted prefix works too) to its distribution or a list of them; *first_party* names
    extra local top-level modules; *acknowledge* maps ``"<workflow file name>::<job id>"`` to the reason that job's
    unreadable install forms are accepted (it silences ``ci-install-unevaluated`` there, never ``ci-install-missing``).
    """
    repo = Repo(Path(root).resolve())
    runs, read = _pytest_runs(repo)
    acknowledged = {k: v for k, v in (acknowledge or {}).items() if str(v).strip()}
    if not runs:
        return _delegated_findings(repo, acknowledged), list(repo.problems), read
    scan = scan_python(repo.root, min_files=0, patterns=("conftest.py",))
    repo.problems.extend(problem.to_finding(RULE_UNPARSED) for problem in scan.unparsed)
    first = set(first_party)
    imports: dict[Path, list[ConftestImport]] = {}
    for f in scan.files:
        local = _first_party(repo, f.path.resolve(), first)
        imports[f.path.resolve()] = [i for i in conftest_imports(f.tree, f.rel) if not is_stdlib(i.top) and i.top not in local]
    checker = _Checker(repo, dict(aliases or {}), acknowledged, {d for r in runs for d in r.provided.dists} | _declared(repo))
    every = sorted(imports)
    for run in runs:
        for conftest in _conftests_for(repo, run, every):
            for imp in imports[conftest]:
                checker.check(run, imp)
        local = _first_party(repo, run.cwd / "conftest.py", first) if repo.inside(run.cwd) else first
        for imp in _plugin_options(run):
            if not is_stdlib(imp.top) and imp.top not in local:
                checker.check(run, imp)
    findings = [gap.finding(f"{Path(gap.run.workflow).name}::{gap.run.job}") for gap in checker.gaps.values()]
    findings += _delegated_findings(repo, acknowledged)
    return sorted(findings, key=lambda f: (f.path, f.line, f.message)), list(repo.problems), read


def _delegated_findings(repo: Repo, acknowledged: Mapping[str, str]) -> list[Finding]:
    """One ``ci-install-unevaluated`` per job that runs its tests only through tox/nox/``make test``."""
    findings: list[Finding] = []
    for workflow, job, line, command in repo.delegated:
        if f"{Path(workflow).name}::{job}" not in acknowledged:
            message = (
                f"job {job!r} runs its tests through `{command}`, whose environments this check does not read, so what the "
                f"conftests import was not checked. Run pytest in the job, or acknowledge={{{Path(workflow).name + '::' + job!r}: '<why>'}}"
            )
            findings.append(Finding(workflow, line, RULE_UNEVALUATED, message, key=f"{RULE_UNEVALUATED}::{workflow}::{job}::{command.split()[0]}"))
    return findings


def _plugin_options(run: PytestRun) -> list[ConftestImport]:
    """The plugins ``-p name`` / ``-pname`` load at startup (``-p no:x`` disables one and is skipped)."""
    out: list[ConftestImport] = []
    for i, a in enumerate(run.args):
        value = run.args[i + 1] if a == "-p" and i + 1 < len(run.args) else a[2:] if a.startswith("-p") and len(a) > 2 else ""
        if value and not value.startswith("no:") and "\x00" not in value:
            out.append(ConftestImport(value, value.split(".")[0], run.workflow, run.line, lambda _: True))
    return out


def find_ci_install_gaps(root: Path) -> list[str]:
    """Rendered findings (unreadable workflows and conftests first) for the repo at *root*, with no aliases or
    acknowledgements."""
    findings, problems, _ = collect_ci_install_gaps(root)
    return [f.render() for f in [*problems, *findings]]


def assert_ci_install_covers_conftest(
    root: Path,
    *,
    aliases: Optional[Mapping[str, Any]] = None,
    first_party: Iterable[str] = (),
    acknowledge: Optional[Mapping[str, str]] = None,
    baseline_path: Optional[Path] = None,
    min_workflows: int = 1,
    allow_unparsed: bool = False,
    request: Any = None,
) -> None:
    """Fail when a pytest job does not install what a conftest it loads imports at collection (see the module docstring).

    Fails too on fewer than *min_workflows* readable workflow files and, unless *allow_unparsed*, on a workflow, local
    action, requirements file, pyproject or conftest that cannot be read. With *baseline_path*, findings already in the
    baseline are accepted (shrink-only; refresh with ``--refresh-ci-install-covers-conftest-baseline`` or
    ``PY_CI_SHARED_REFRESH=ci-install-covers-conftest``).
    """
    import pytest

    findings, problems, read = collect_ci_install_gaps(Path(root), aliases=aliases, first_party=first_party, acknowledge=acknowledge)
    failures: list[str] = []
    if read < min_workflows:
        failures.append(f"only {read} workflow file(s) read under {Path(root) / '.github' / 'workflows'}; expected at least {min_workflows}")
    if problems and not allow_unparsed:
        failures.append("files that could not be read, so the check cannot vouch for them:\n    " + "\n    ".join(p.render() for p in problems))
    guidance = "CI jobs that run pytest without installing what conftest.py imports at collection"
    if baseline_path is None:
        failures.extend([guidance + ":\n    " + "\n    ".join(f.render() for f in findings)] if findings else [])
    else:
        outcome = Baseline(baseline_path, gate="ci-install-covers-conftest", refresh_command=f"pytest {REFRESH_FLAG}").enforce(
            findings, refresh=refresh_requested(REFRESH_FLAG, request), guidance=guidance
        )
        if outcome.refreshed and not failures:
            pytest.skip(outcome.message)
        if not outcome.ok or outcome.stale:
            failures.append(outcome.message)
    if failures:
        pytest.fail("\n  ".join(failures))
