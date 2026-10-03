"""Private helpers of :mod:`py_ci_shared.ci_install_covers_conftest`: what a job's install commands provide.

Environment markers, ``pyproject.toml`` projects and extras, PEP 735 dependency groups, requirement files and the
arguments of ``pip install``/``uv pip install``, read statically from the repo.
"""

from __future__ import annotations

import ast
import functools
import os
import re
import shlex
import sys
import sysconfig
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ._core import Finding, SourceError, read_source
from ._toml_compat import tomllib

RULE_UNPARSED = "unparsed-file"

#: pytest hooks that run on every session before or during collection: an import inside one fails the whole run.
SESSION_HOOKS = frozenset(
    {
        "pytest_addhooks",
        "pytest_addoption",
        "pytest_cmdline_main",
        "pytest_cmdline_parse",
        "pytest_cmdline_preparse",
        "pytest_collect_directory",
        "pytest_collect_file",
        "pytest_collection",
        "pytest_collection_finish",
        "pytest_collection_modifyitems",
        "pytest_configure",
        "pytest_ignore_collect",
        "pytest_load_initial_conftests",
        "pytest_plugin_registered",
        "pytest_pycollect_makemodule",
        "pytest_report_header",
        "pytest_sessionstart",
    }
)
_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})

# pip / uv options that take a value (the value is not a requirement).
VALUED_OPTS = frozenset(
    {
        "-c", "--constraint", "-i", "--index-url", "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix", "--root",
        "--python", "-p", "-P", "--upgrade-package", "--no-binary", "--only-binary", "--platform", "--python-version", "--implementation",
        "--abi", "--src", "--progress-bar", "--log", "--cache-dir", "--resolution", "--prerelease", "--index-strategy", "--keyring-provider",
        "--exclude-newer", "-C", "--config-settings", "--config-setting", "--index", "--default-index", "--override", "--build-constraint",
        "--python-platform", "--trusted-host", "--proxy", "--timeout", "--retries", "--report", "--root-user-action", "--link-mode",
        "--refresh-package", "--reinstall-package", "--no-build-isolation-package", "--torch-backend", "--only-group", "--no-group",
    }
)  # fmt: skip
PEP508 = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<rest>[^;]*?)\s*(?:;\s*(?P<marker>.*))?$")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# --------------------------------------------------------------------------------------------------------------------
# environment markers and Python versions


Version = Optional["tuple[int, int]"]


def parse_version(text: str) -> Optional[tuple[int, ...]]:
    m = re.match(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text))
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def compare_versions(left: tuple[int, ...], op: str, right: tuple[int, ...]) -> Optional[bool]:
    n = max(len(left), len(right))
    a, b = left + (0,) * (n - len(left)), right + (0,) * (n - len(right))
    if op == "~=":
        return a >= b and a[: len(right) - 1] == b[: len(right) - 1]
    table: dict[str, Callable[[], bool]] = {
        "==": lambda: a[: len(right)] == b[: len(right)],
        "===": lambda: a == b,
        "!=": lambda: a[: len(right)] != b[: len(right)],
        "<": lambda: a < b,
        "<=": lambda: a <= b,
        ">": lambda: a > b,
        ">=": lambda: a >= b,
    }
    fn = table.get(op)
    return fn() if fn else None


_MARKER_TOKEN = re.compile(r"\s*(?:(?P<str>'[^']*'|\"[^\"]*\")|(?P<op>===|==|!=|<=|>=|~=|<|>|not\s+in\b|in\b)|(?P<paren>[()])|(?P<word>[A-Za-z_.]+))")


def _marker_tokens(marker: str) -> Optional[list[tuple[str, str]]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = marker.strip()
    while pos < len(text):
        m = _MARKER_TOKEN.match(text, pos)
        if not m or m.end() == pos:
            return None
        pos = m.end()
        kind = m.lastgroup or ""
        value = re.sub(r"\s+", " ", m.group(kind))
        tokens.append(("bool" if kind == "word" and value in ("and", "or", "not") else kind, value))
    return tokens


def _kleene_and(a: Optional[bool], b: Optional[bool]) -> Optional[bool]:
    return False if a is False or b is False else (None if a is None or b is None else True)


def _kleene_or(a: Optional[bool], b: Optional[bool]) -> Optional[bool]:
    return True if a is True or b is True else (None if a is None or b is None else False)


def _version_atom(left: tuple[str, str], op: str, right: tuple[str, str]) -> Optional[bool]:
    lv, rv = parse_version(left[1]), parse_version(right[1])
    if right[0] == "ver":  # 'x' < python_version: flip so the version is on the left
        lv, rv, op = rv, lv, {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(op, op)
    if lv is None or rv is None or (len(rv) > 2 and rv[:2] == lv[:2]):
        return None  # only major.minor is known, so 3.11 against '3.11.2' is undecidable
    return compare_versions(lv, op, rv[:2] if op != "~=" else rv)


@dataclass
class _MarkerParser:
    tokens: list[tuple[str, str]]
    python: Version
    position: int = 0

    def peek(self) -> Optional[tuple[str, str]]:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        tok = self.tokens[self.position]
        self.position += 1
        return tok

    def operand(self, tok: tuple[str, str]) -> tuple[str, str]:
        kind, value = tok
        if kind == "str":
            return "str", value[1:-1]
        if value in ("python_version", "python_full_version") and self.python is not None:
            return "ver", f"{self.python[0]}.{self.python[1]}"
        return "var", value

    def atom(self) -> Optional[bool]:
        if self.peek() == ("paren", "("):
            self.take()
            value = self.disjunction()
            self.take()
            return value
        left = self.operand(self.take())
        op = self.take()[1]
        right = self.operand(self.take())
        if "var" in (left[0], right[0]):
            return None
        if op in ("in", "not in"):
            return (left[1] in right[1]) == (op == "in")
        if "ver" in (left[0], right[0]):
            return _version_atom(left, op, right)
        return (left[1] == right[1]) if op == "==" else (left[1] != right[1]) if op == "!=" else None

    def conjunction(self) -> Optional[bool]:
        value = self.atom()
        while self.peek() == ("bool", "and"):
            self.take()
            value = _kleene_and(value, self.atom())
        return value

    def disjunction(self) -> Optional[bool]:
        value = self.conjunction()
        while self.peek() == ("bool", "or"):
            self.take()
            value = _kleene_or(value, self.conjunction())
        return value


def eval_marker(marker: str, python: Version) -> Optional[bool]:
    """Kleene evaluation of a PEP 508 marker against a known (or None) ``(major, minor)``; None when undecidable."""
    tokens = _marker_tokens(marker)
    if not tokens:
        return None
    parser = _MarkerParser(tokens, python)
    try:
        result = parser.disjunction()
    except IndexError:
        return None
    return result if parser.position == len(tokens) else None


# --------------------------------------------------------------------------------------------------------------------
# the repo: pyproject, requirement files, locks


@dataclass
class Repo:
    root: Path
    problems: list[Finding] = field(default_factory=list)
    #: ``(workflow, job, line, command)`` of a job that runs its tests through make/tox/nox and calls no pytest itself
    delegated: list[tuple[str, str, int, str]] = field(default_factory=list)
    _toml: dict[Path, Optional[dict[str, Any]]] = field(default_factory=dict)

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root.resolve())
            return True
        except ValueError:
            return False

    def toml(self, path: Path) -> Optional[dict[str, Any]]:
        if path not in self._toml:
            data: Optional[dict[str, Any]] = None
            if path.is_file():
                try:
                    data = tomllib.loads(read_source(path))
                except SourceError as exc:
                    self.problems.append(Finding(self.rel(path), exc.line or 1, RULE_UNPARSED, f"{exc.kind}: {exc.message}"))
                except tomllib.TOMLDecodeError as exc:
                    self.problems.append(Finding(self.rel(path), getattr(exc, "lineno", 1) or 1, RULE_UNPARSED, f"unparsable: {exc}"))
            self._toml[path] = data
        return self._toml[path]

    def lock_names(self, cwd: Path) -> Optional[set[str]]:
        """Package names in the nearest ``uv.lock`` at or above *cwd* (within the repo), or None."""
        for d in [cwd, *cwd.parents]:
            data = self.toml(d / "uv.lock") if (d / "uv.lock").is_file() else None
            if data is not None:
                return {norm(p.get("name", "")) for p in data.get("package", []) if isinstance(p, dict) and p.get("name")}
            if d.resolve() == self.root.resolve():
                break
        return None


@dataclass
class Provided:
    """What a job's install commands have provided so far."""

    dists: dict[str, list[Optional[str]]] = field(default_factory=dict)  # normalised dist -> its markers (None = unconditional)
    unresolved: list[str] = field(default_factory=list)  # install forms this check cannot read
    notes: list[str] = field(default_factory=list)
    reading: set[str] = field(default_factory=set)  # dynamic requirement files being read, so a file naming its own project stops

    def add(self, name: str, marker: Optional[str] = None) -> None:
        self.dists.setdefault(norm(name), []).append(marker)

    def copy(self) -> Provided:
        return Provided({k: list(v) for k, v in self.dists.items()}, list(self.unresolved), list(self.notes))

    def available(self, python: Version) -> set[str]:
        return {d for d, markers in self.dists.items() if any(m is None or eval_marker(m, python) is not False for m in markers)}


def split_requirement(spec: str) -> Optional[tuple[str, list[str], Optional[str]]]:
    """``(name, extras, marker)`` of a PEP 508 line, a ``name @ url`` line or a ``git+...#egg=name`` URL; None when not one."""
    spec = spec.strip()
    marker: Optional[str] = None
    if " @ " in spec or re.match(r"^[A-Za-z0-9._-]+(\[[^\]]*\])?\s*@\s*\S", spec):
        head, _, tail = spec.partition("@")
        if ";" in tail:
            marker = tail.split(";", 1)[1].strip()
        m = PEP508.match(head)
        return (m.group("name"), split_extras(m.group("extras")), marker) if m else None
    if re.match(r"^(git|hg|svn|bzr)\+|^https?://", spec):
        egg = re.search(r"[#&]egg=([A-Za-z0-9._-]+)", spec)
        return (egg.group(1), [], None) if egg else None
    m = PEP508.match(spec)
    if not m or not re.fullmatch(r"(?:[<>=!~]=?[^,]*,?\s*)*|\(.*\)|", (m.group("rest") or "").strip()):
        return None
    return m.group("name"), split_extras(m.group("extras")), m.group("marker")


def split_extras(text: Optional[str]) -> list[str]:
    return [e.strip() for e in (text or "").split(",") if e.strip()]


def _extra_lines(own: str, optional: dict[str, Any], extras: Sequence[str], provided: Provided) -> list[str]:
    """Every requirement line of *extras* and of the extras they name through ``own[other]`` self-references."""
    wanted = list(dict.fromkeys(extras))
    seen: set[str] = set()
    lines: list[str] = []
    while wanted:
        extra = wanted.pop()
        if extra in seen:
            continue
        seen.add(extra)
        if extra not in optional:
            provided.notes.append(f"{own} has no extra {extra!r}")  # pip warns and continues
            continue
        for line in optional[extra]:
            parts = split_requirement(line)
            if parts is not None and norm(parts[0]) == own:
                wanted.extend(parts[1])
            else:
                lines.append(line)
    return lines


def _dynamic_files(repo: Repo, directory: Path, spec: Any, what: str, provided: Provided) -> None:
    """Read a ``[tool.setuptools.dynamic]`` entry's ``file =`` requirement files; anything else is unresolved."""
    files = spec.get("file") if isinstance(spec, dict) else None
    files = [files] if isinstance(files, str) else files
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        provided.unresolved.append(f"{repo.rel(directory / 'pyproject.toml')}: {what} are dynamic, and not a [tool.setuptools.dynamic] file = list")
        return
    for name in files:
        key = str((directory / name).resolve())
        if key in provided.reading:
            continue
        provided.reading.add(key)
        try:
            requirements_file(repo, directory / name, directory, provided)
        finally:
            provided.reading.discard(key)


def add_project(repo: Repo, directory: Path, extras: Sequence[str], provided: Provided, *, marker: Optional[str] = None) -> bool:
    """Add a local project, its dependencies and the named extras; False when *directory* has no readable ``[project]`` table.

    ``dynamic = ["dependencies"]`` (or ``"optional-dependencies"``) is read from ``[tool.setuptools.dynamic]``'s
    ``file =`` lists; a dynamic table this cannot read is recorded as unresolved, never as "no dependencies"."""
    data = repo.toml(directory / "pyproject.toml") or {}
    project = data.get("project")
    if not isinstance(project, dict) or not project.get("name"):
        return False
    own = norm(project["name"])
    provided.add(own, marker)
    dynamic = project.get("dynamic") or []
    tool = data.get("tool")
    setuptools = tool.get("setuptools") if isinstance(tool, dict) else None
    sdyn = setuptools.get("dynamic") if isinstance(setuptools, dict) else None
    sdyn = sdyn if isinstance(sdyn, dict) else {}
    if "dependencies" in dynamic:
        _dynamic_files(repo, directory, sdyn.get("dependencies"), "dependencies", provided)
    if "optional-dependencies" in dynamic:
        optional_dyn = sdyn.get("optional-dependencies")
        for extra in dict.fromkeys(extras):
            spec = optional_dyn.get(extra) if isinstance(optional_dyn, dict) else None
            _dynamic_files(repo, directory, spec, f"optional-dependencies[{extra}]", provided)
    lines = list(project.get("dependencies") or [])
    if "optional-dependencies" not in dynamic:
        lines += _extra_lines(own, project.get("optional-dependencies") or {}, extras, provided)
    for line in lines:
        parts = split_requirement(line)
        if parts is not None and norm(parts[0]) != own:
            provided.add(parts[0], and_markers(marker, parts[2]))
    return True


def and_markers(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if a and b:
        return f"({a}) and ({b})"
    return a or b


def dependency_group(repo: Repo, pyproject: Path, group: str, provided: Provided, seen: Optional[set[str]] = None) -> None:
    data = repo.toml(pyproject) or {}
    groups = data.get("dependency-groups", {}) or {}
    seen = seen if seen is not None else set()
    if group in seen:
        return
    seen.add(group)
    if group not in groups:
        provided.unresolved.append(f"--group {group}: no [dependency-groups].{group} in {repo.rel(pyproject)}")
        return
    for entry in groups[group]:
        if isinstance(entry, dict) and "include-group" in entry:
            dependency_group(repo, pyproject, entry["include-group"], provided, seen)
        elif isinstance(entry, str):
            parts = split_requirement(entry)
            if parts:
                provided.add(parts[0], parts[2])


_OWN_VALUED = ("-r", "--requirement", "-e", "--editable", "--group")


def _options(args: Sequence[str]) -> Iterator[tuple[str, Optional[str]]]:
    """``(option, value)`` pairs of pip/uv arguments, ``-rFILE`` and ``--opt=value`` split; a positional is ``("", arg)``."""
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg in _OWN_VALUED or arg in VALUED_OPTS:
            yield arg, (args[i] if i < len(args) else "")
            i += 1
        elif arg.startswith("--") and "=" in arg:
            yield tuple(arg.split("=", 1))  # type: ignore[misc]
        elif arg.startswith("-r") and len(arg) > 2 and not arg.startswith("--"):
            yield "-r", arg[2:]
        else:
            yield (arg, None) if arg.startswith("-") else ("", arg)


def _apply_option(repo: Repo, opt: str, value: str, cwd: Path, base: Path, provided: Provided, depth: int) -> None:
    """One install option: *base* is what ``-r``/``-e`` paths are relative to (the requirements file's directory inside one)."""
    if "\x00" in value and opt in ("-r", "--requirement", "--group"):
        provided.unresolved.append(f"{opt} {show_arg(value)}: a file named only at run time")
    elif opt in ("-r", "--requirement"):
        requirements_file(repo, base / value, cwd, provided, depth + 1)
    elif opt in ("-e", "--editable", ""):
        add_target(repo, value, base, provided)
    elif opt == "--group":
        file_part, _, group = value.rpartition(":")
        dependency_group(repo, (cwd / file_part) if file_part else cwd / "pyproject.toml", group, provided)


def requirements_file(repo: Repo, path: Path, cwd: Path, provided: Provided, depth: int = 0) -> None:
    if depth > 10:
        return
    if not path.is_file():
        provided.unresolved.append(f"-r {repo.rel(path)}: no such file in the repo")
        return
    try:
        text = read_source(path)
    except SourceError as exc:
        repo.problems.append(Finding(repo.rel(path), exc.line or 1, RULE_UNPARSED, f"{exc.kind}: {exc.message}"))
        return
    for raw in re.sub(r"\\\r?\n", " ", text).splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if line.startswith("-"):
            for opt, value in _options(_tokens(line)):
                if value is not None:
                    _apply_option(repo, opt, value, cwd, path.parent, provided, depth)
        elif line:
            add_target(repo, line, path.parent, provided)


def _tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, posix=True)
    except ValueError:
        return line.split()


_URLISH = re.compile(r"^\w+\+|^https?://|^[A-Za-z0-9._-]+(\[[^\]]*\])?\s*@")
_ARCHIVES = (".whl", ".tar.gz", ".zip")


_RUNTIME = re.compile(r"\x00[^\x00]*\x00")


def _unresolved_target(repo: Repo, spec: str, cwd: Path, provided: Provided) -> None:
    """A target holding a run-time value. What can still be read is: ``${{ runner.temp }}/pyutilz[x]`` (a path outside
    the checkout, named by its last component), ``.[all,${EXTRA}]`` (the known extras) and ``name==${VERSION}`` (the
    name); the run-time part is recorded as unresolved."""
    head = spec.split("\x00", 1)[0]
    tail = _RUNTIME.split(spec)[-1]
    outside = re.match(r"^/?(?:.*/)?(?P<name>[A-Za-z0-9._-]+)/?(?:\[[^\]]*\])?$", tail)
    if outside and "/" in tail and "[" not in head:
        provided.add(outside.group("name"))
        provided.notes.append(f"{show_arg(spec)}: a path outside the checkout, read as distribution {outside.group('name')!r}")
        return
    provided.unresolved.append(f"install argument {show_arg(spec)} depends on a value only known at run time")
    if "[" in head:
        m = re.match(r"^(?P<base>[^\[]*)\[(?P<extras>[^\]]*)\](?P<rest>.*)$", _RUNTIME.sub("", spec))
        if m:
            extras = ",".join(split_extras(m.group("extras")))
            add_target(repo, m.group("base") + (f"[{extras}]" if extras else "") + m.group("rest"), cwd, provided)
    elif re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*\s*(?:[<>=!~]=?|@)", head):
        provided.add(re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*", head).group(0))  # type: ignore[union-attr]


def _path_target(repo: Repo, path_part: str, marker: Optional[str], cwd: Path, provided: Provided) -> None:
    m = re.match(r"^(?P<path>.*?)(?:\[(?P<extras>[^\]]*)\])?$", path_part)
    raw_path = (m.group("path") if m else path_part).replace("file:", "")
    extras = split_extras(m.group("extras") if m else None)
    if raw_path.endswith(_ARCHIVES):
        # a built distribution: by name when it is spelled out, else the project being built in this directory
        stem = Path(raw_path).name.split("-")[0]
        if "*" not in stem:
            provided.add(stem, marker)
        elif not add_project(repo, cwd, extras, provided, marker=marker):
            provided.unresolved.append(f"install argument {path_part!r}: a wheel of a project this check cannot find")
        return
    directory = Path(raw_path) if Path(raw_path).is_absolute() else (cwd / raw_path)
    directory = directory.resolve()
    if repo.inside(directory) and directory.is_dir():
        if not add_project(repo, directory, extras, provided, marker=marker):
            provided.unresolved.append(f"install argument {path_part!r}: {repo.rel(directory) or '.'} has no [project] table to read")
        return
    name = directory.name or Path(raw_path.rstrip("/\\")).name
    provided.add(name, marker)
    provided.notes.append(f"{path_part}: a path outside the checkout, read as distribution {name!r}")


def add_target(repo: Repo, spec: str, cwd: Path, provided: Provided) -> None:
    """One requirement argument: a name, a URL, ``name @ url``, a local path (with extras) or a wheel."""
    spec = spec.strip()
    if not spec:
        return
    if "\x00" in spec:  # an unresolved ${{ }} or $VAR
        _unresolved_target(repo, spec, cwd, provided)
        return
    path_part, marker = spec, None
    if ";" in spec and not _URLISH.match(spec):
        path_part, marker = (s.strip() for s in spec.split(";", 1))
    is_path = path_part.startswith((".", "/", "~", "file:")) or (bool(re.search(r"[\\/]", path_part)) and not _URLISH.match(path_part))
    if is_path or path_part.endswith(_ARCHIVES):
        _path_target(repo, path_part, marker, cwd, provided)
        return
    parts = split_requirement(spec)
    if parts is None:
        provided.unresolved.append(f"install argument {spec!r} is not a requirement this check can read")
        return
    provided.add(parts[0], parts[2])


def show_arg(spec: str) -> str:
    return repr(re.sub(r"\x00([^\x00]*)\x00", r"\1", spec))


def pip_install(repo: Repo, args: Sequence[str], cwd: Path, provided: Provided) -> None:
    for opt, value in _options(args):
        if value is not None:
            _apply_option(repo, opt, value, cwd, cwd, provided, -1)


# --------------------------------------------------------------------------------------------------------------------
# conftest imports


@dataclass(frozen=True)
class ConftestImport:
    module: str  # the dotted name as written
    top: str  # its first component
    conftest: str
    line: int
    guard: Callable[[Version], Optional[bool]]


def always(_: Version) -> Optional[bool]:
    return True


Guard = Callable[[Version], Optional[bool]]


def negate_guard(g: Guard) -> Guard:
    return lambda v: None if g(v) is None else not g(v)


def _all_of(ps: Sequence[Guard]) -> Guard:
    return lambda v: False if any(p(v) is False for p in ps) else None if any(p(v) is None for p in ps) else True


def _any_of(ps: Sequence[Guard]) -> Guard:
    return lambda v: True if any(p(v) is True for p in ps) else None if any(p(v) is None for p in ps) else False


def _is_version_info(node: ast.expr) -> bool:
    return ast.unparse(node.value if isinstance(node, ast.Subscript) else node) in ("sys.version_info", "version_info")


_COMPARE_OPS = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}


def _compare_guard(test: ast.Compare) -> Optional[Guard]:
    left, right, sym = test.left, test.comparators[0], _COMPARE_OPS.get(type(test.ops[0]))
    if _is_version_info(right) and not _is_version_info(left):
        left, right = right, left
        sym = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(sym or "", sym)
    if sym is None or len(test.ops) != 1 or not _is_version_info(left):
        return None
    try:
        bound = tuple(int(x) for x in ast.literal_eval(right))
    except (ValueError, TypeError, SyntaxError):
        return None
    op = sym
    return lambda v: None if v is None else compare_versions(tuple(v), op, bound)


def version_guard(test: ast.expr) -> Optional[Guard]:
    """A predicate for ``sys.version_info <op> (x, y)`` (either order, ``[:2]``, ``not``/``and``/``or`` allowed); None when
    *test* is not one."""
    if isinstance(test, ast.BoolOp):
        parts = [version_guard(v) for v in test.values]
        ps = [p for p in parts if p is not None]
        if len(ps) != len(parts):
            return None
        return _all_of(ps) if isinstance(test.op, ast.And) else _any_of(ps)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = version_guard(test.operand)
        return None if inner is None else negate_guard(inner)
    return _compare_guard(test) if isinstance(test, ast.Compare) else None


def is_type_checking(test: ast.expr) -> bool:
    return ast.unparse(test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")


def catches_import_error(node: ast.Try) -> bool:
    for handler in node.handlers:
        if handler.type is None:
            return True
        names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        if any(ast.unparse(n).split(".")[-1] in _IMPORT_ERRORS for n in names):
            return True
    return False


@dataclass
class _ImportVisitor:
    """Collects the imports a conftest runs when pytest loads it: module level, class bodies and session hooks."""

    rel: str
    out: list[ConftestImport] = field(default_factory=list)

    def visit(self, body: Sequence[ast.stmt], guard: Guard, in_hook: bool) -> None:
        for node in body:
            self.node(node, guard, in_hook)

    def loaded_modules(self, node: ast.stmt, guard: Guard, in_hook: bool) -> bool:
        """Record what *node* imports (an import, or a module-level ``pytest_plugins``, which pytest imports when it
        loads the conftest); False when *node* is neither."""
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module] if node.level == 0 and node.module else []
        elif isinstance(node, ast.Assign) and not in_hook and any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in node.targets):
            values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            names = [v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value]
        else:
            return False
        self.out.extend(ConftestImport(name, name.split(".")[0], self.rel, node.lineno, guard) for name in names)
        return True

    def node(self, node: ast.stmt, guard: Guard, in_hook: bool) -> None:
        if self.loaded_modules(node, guard, in_hook):
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not in_hook and (isinstance(node, ast.ClassDef) or node.name in SESSION_HOOKS):
                self.visit(node.body, guard, not isinstance(node, ast.ClassDef))
        elif isinstance(node, ast.If):
            self.branch(node, guard, in_hook)
        elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            try_node: ast.Try = node  # type: ignore[assignment]
            if not catches_import_error(try_node):
                self.visit(try_node.body, guard, in_hook)
            self.visit([*try_node.orelse, *try_node.finalbody], guard, in_hook)
        elif isinstance(node, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
            self.visit([*node.body, *getattr(node, "orelse", [])], guard, in_hook)

    def branch(self, node: ast.If, guard: Guard, in_hook: bool) -> None:
        if is_type_checking(node.test):
            self.visit(node.orelse, guard, in_hook)
            return
        g = version_guard(node.test)
        self.visit(node.body, guard if g is None else combine_guards(guard, g), in_hook)
        self.visit(node.orelse, guard if g is None else combine_guards(guard, negate_guard(g)), in_hook)


def conftest_imports(tree: ast.Module, rel: str) -> list[ConftestImport]:
    visitor = _ImportVisitor(rel)
    visitor.visit(tree.body, always, False)
    return visitor.out


def combine_guards(a: Callable[[Version], Optional[bool]], b: Callable[[Version], Optional[bool]]) -> Callable[[Version], Optional[bool]]:
    return lambda v: False if a(v) is False or b(v) is False else (None if a(v) is None or b(v) is None else True)


@functools.cache
def is_stdlib(name: str) -> bool:
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return name in names or name == "__future__"
    if name in sys.builtin_module_names or name == "__future__":
        return True
    import importlib.util

    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    origin = getattr(spec, "origin", None) or ""
    stdlib = sysconfig.get_paths().get("stdlib", "")
    return bool(origin and stdlib and os.path.normcase(origin).startswith(os.path.normcase(stdlib)) and "site-packages" not in origin)


@functools.lru_cache(maxsize=1)
def installed_map() -> dict[str, tuple[str, ...]]:
    """Import name -> distributions, from the running interpreter's installed metadata."""
    from importlib import metadata

    getter = getattr(metadata, "packages_distributions", None)
    if getter is not None:
        return {k: tuple(norm(d) for d in v) for k, v in getter().items()}
    out: dict[str, set[str]] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        top = dist.read_text("top_level.txt") or ""
        tops = set(top.split()) or {str(f).split("/")[0].removesuffix(".py") for f in (dist.files or []) if "/" in str(f) or str(f).endswith(".py")}
        for t in tops:
            out.setdefault(t, set()).add(norm(name))
    return {k: tuple(sorted(v)) for k, v in out.items()}
