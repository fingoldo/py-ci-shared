"""Which consumer repos run which py_ci_shared modules, at which py-ci-shared version, and where a gate can skip itself.

Given consumer repo roots, it reports per repo:

* **Pins**: every place the repo names a py-ci-shared version: ``pyproject.toml`` dependency tables and
  ``[tool.uv.sources]``, ``requirements*.txt``/``*.in``, ``uv.lock``, the ``.pre-commit-config.yaml`` ``rev``, and in
  ``.github`` YAML the ``uses:`` refs of the shared workflows and actions, ``py-ci-shared-ref`` inputs, ``pip install``
  lines, ``git clone`` of the repo and ``actions/checkout`` of it, plus a ruff ``extend`` into a sibling checkout.
  A pin is *moving* when it is a branch, a major/minor tag (``v1``), a bare name or git URL without a ref, or a
  sibling path. Pins *agree* when they all name one commit (resolved through ``--resolve-in`` when given).
* **Release currency** (with ``--resolve-in``): a fixed pin whose commit lacks a release tag (``vX.Y.Z``) of that
  checkout is *behind*: the report says "N releases behind vX.Y.Z". ``--allow-behind N`` tolerates N.
* **Modules**: ``py_ci_shared.<module>`` in imports, ``python -m`` lines, ``importorskip`` strings and paths, plus the
  gates ``[tool.py_ci_shared]`` enables. Markdown files and ``audits/`` directories are not read.
* **Silent skips**: ``pytest.importorskip("py_ci_shared...")``, and a ``try`` importing py_ci_shared whose
  ``ImportError`` handler sets ``collect_ignore``, exits 0, calls ``pytest.skip``, sets a flag to ``False`` or just
  returns: a missing or too-old install then reads as a pass.
* **Local copies**: :func:`py_ci_shared.local_copy_report.find_local_copies` over each ``tests`` directory.

It only reads consumer repos. The exit status is 1 when a repo has disagreeing pins, moving pins, silent skips or a
pin behind the latest release; ``--advisory`` reports the same and exits 0.

Usage::

    python -m py_ci_shared.adoption_matrix ../mlframe ../pyutilz --output adoption.md
    python -m py_ci_shared.adoption_matrix --repos-file consumers.toml --resolve-in ../py-ci-shared --advisory

A repos file holds ``repos = ["../mlframe", ...]`` (relative to the file) or ``[[repo]]`` tables with ``path`` and an
optional ``name``.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ._core import DEFAULT_EXCLUDE, CoreError, Finding, ImportAliases, iter_files, read_source
from ._core.node_index import walk as _fast_walk
from ._toml_compat import tomllib

__all__ = [
    "MOVING_KINDS",
    "Pin",
    "RepoReport",
    "RefResolver",
    "classify_ref",
    "find_module_usage",
    "find_pins",
    "find_silent_skips",
    "known_modules",
    "load_repo_list",
    "main",
    "render_markdown",
    "scan_repo",
]

PathLike = Union[str, Path]

#: Pin kinds that do not name one fixed commit.
MOVING_KINDS = frozenset({"branch", "moving-tag", "unpinned", "sibling"})
SILENT_RULES = ("importorskip", "collect-ignore", "exit-0", "skip", "availability-flag", "swallowed")

_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_RELEASE = re.compile(r"^v?\d+\.\d+\.\d+([.-]?[0-9A-Za-z.]+)?$")
_MOVING_TAG = re.compile(r"^v?\d+(\.\d+)?$")
_FULL_RELEASE = re.compile(r"^v\d+\.\d+\.\d+$")
_PKG_NAME = re.compile(r"^\s*py[-_.]ci[-_.]shared\b", re.IGNORECASE)
_GIT_URL = re.compile(r"git\+(?:https?|ssh)://(?:git@)?github\.com[/:][\w.-]+/py-ci-shared(?:\.git)?(?:@([^\s\"'#;,\]\)]+))?", re.IGNORECASE)
_USES = re.compile(r"\buses:\s*['\"]?[\w.-]+/py-ci-shared(/[^@\s'\"]*)?@([^\s'\"#]+)")
_REF_INPUT = re.compile(r"\bpy-ci-shared-ref:\s*['\"]?([^\s'\"#]+)")
_CLONE = re.compile(r"\bgit\s+clone\b.*?[\w.-]+/py-ci-shared(?:\.git)?\b")
_CLONE_BRANCH = re.compile(r"(?:--branch|-b)[=\s]+['\"]?([^\s'\"]+)")
_CHECKOUT_REPO = re.compile(r"\brepository:\s*['\"]?[\w.-]+/py-ci-shared['\"]?\s*$")
_REF_KEY = re.compile(r"^\s*ref:\s*['\"]?([^\s'\"#]+)")
_SIBLING = re.compile(r"(?:^|[\s\"'=])(?:-e\s+|file:(?://)?)?(\.\.[/\\][^\s\"']*py-ci-shared)\b")
_PIP_LINE = re.compile(r"\b(pip|uv)\b.*\binstall\b")
_PRECOMMIT_REPO = re.compile(r"^\s*-?\s*repo:\s*['\"]?\S*/py-ci-shared(?:\.git)?['\"]?\s*$")
_REV = re.compile(r"^\s*rev:\s*['\"]?([^\s'\"#]+)")
_MODULE_REF = re.compile(r"(?<!tool\.)\bpy_ci_shared[./]([A-Za-z_][A-Za-z0-9_]*)")
_CLI_RUN = re.compile(r"\bpy-ci-shared\s+(?:run|refresh|tool)\s+([A-Za-z_][A-Za-z0-9_]*)")
_USAGE_PATTERNS = ("*.py", "*.pyi", "*.toml", "*.yml", "*.yaml", "*.cfg", "*.ini", "*.sh", "*.ps1", "*.bat", "*.txt", "*.json", "Makefile")
_USAGE_EXCLUDE = DEFAULT_EXCLUDE | {"audits"}
_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})
_EXIT_CALLS = frozenset({"sys.exit", "exit", "quit", "os._exit", "SystemExit"})


@dataclass(frozen=True)
class Pin:
    """One place a repo names a py-ci-shared version. ``ref`` is None for a bare name or a URL without a ref."""

    path: str
    line: int
    location: str  # pyproject, requirements, uv.lock, pre-commit, workflow
    target: str  # package, workflow:<file>, action:<name>, pre-commit, config-clone, checkout, ref-input, ruff-extend
    ref: Optional[str]
    kind: str

    @property
    def moving(self) -> bool:
        return self.kind in MOVING_KINDS

    def label(self) -> str:
        return self.ref if self.ref is not None else ("<sibling>" if self.kind == "sibling" else "<unpinned>")


def classify_ref(ref: Optional[str]) -> str:
    """``sha``, ``release`` (``v1.16.1``), ``moving-tag`` (``v1``), ``branch`` or ``unpinned`` (None)."""
    if ref is None or not ref.strip():
        return "unpinned"
    ref = ref.strip()
    if _SHA.match(ref.lower()):
        return "sha"
    if _RELEASE.match(ref):
        return "release"
    if _MOVING_TAG.match(ref):
        return "moving-tag"
    return "branch"


class RefResolver:
    """Resolve refs to full SHAs in a py-ci-shared git checkout (read-only ``git rev-parse``); None when unknown."""

    def __init__(self, repo: Optional[PathLike]) -> None:
        self.repo = Path(repo) if repo is not None else None
        self._cache: dict[str, Optional[str]] = {}
        self._releases: Optional[list[str]] = None

    def _git(self, *args: str) -> Optional[str]:
        if self.repo is None:
            return None
        try:
            proc = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=False)
        except OSError:
            return None
        return proc.stdout if proc.returncode == 0 else None

    def releases(self) -> list[str]:
        """The checkout's ``vX.Y.Z`` tags, oldest first by version (``v1`` and pre-releases are not releases)."""
        if self._releases is None:
            out = self._git("tag", "--list", "v*")
            tags = [t.strip() for t in (out or "").splitlines() if _FULL_RELEASE.match(t.strip())]
            self._releases = sorted(tags, key=lambda t: tuple(int(x) for x in t[1:].split(".")))
        return self._releases

    def releases_behind(self, sha: str) -> Optional[tuple[int, str]]:
        """``(n, latest)``: how many release tags the commit *sha* does not contain; None without releases or history."""
        releases = self.releases()
        if not releases:
            return None
        merged = self._git("tag", "--list", "v*", "--merged", sha)
        if merged is None:
            return None
        have = {t.strip() for t in merged.splitlines()}
        return sum(t not in have for t in releases), releases[-1]

    def __call__(self, ref: str) -> Optional[str]:
        if self.repo is None:
            return None
        if ref not in self._cache:
            try:
                proc = subprocess.run(
                    ["git", "-C", str(self.repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError:
                proc = None
            out = proc.stdout.strip() if proc is not None and proc.returncode == 0 else ""
            self._cache[ref] = out or None
        return self._cache[ref]


def _pin(path: str, line: int, location: str, target: str, ref: Optional[str], *, sibling: bool = False) -> Pin:
    return Pin(path, line, location, target, ref, "sibling" if sibling else classify_ref(ref))


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _code_lines(text: str) -> list[tuple[int, str]]:
    """``(lineno, line)`` for every line that is not a whole-line comment."""
    return [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if not ln.lstrip().startswith("#")]


def _pin_from_requirement(req: str, rel: str, line: int, location: str) -> Optional[Pin]:
    """A pin for a dependency string naming py-ci-shared (by name, git URL or sibling path), else None."""
    url = _GIT_URL.search(req)
    if url:
        return _pin(rel, line, location, "package", url.group(1))
    sib = _SIBLING.search(req)
    if sib and ("py-ci-shared" in req.lower() or _PKG_NAME.match(req)):
        return _pin(rel, line, location, "package", None, sibling=True)
    if _PKG_NAME.match(req):  # a bare name or a version specifier: py-ci-shared is not on an index, so nothing pins it
        return _pin(rel, line, location, "package", None)
    return None


def _line_of(lines: list[str], needle: str, used: set[int]) -> int:
    for i, ln in enumerate(lines, 1):
        if needle in ln and i not in used:
            used.add(i)
            return i
    return 1


def _dependency_strings(data: dict[str, Any]) -> list[str]:
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    out: list[str] = list(project.get("dependencies", []) or [])
    for group in (project.get("optional-dependencies", {}) or {}).values():
        out.extend(group or [])
    for group in (data.get("dependency-groups", {}) or {}).values():
        out.extend(g for g in (group or []) if isinstance(g, str))
    out.extend((data.get("build-system", {}) or {}).get("requires", []) or [])
    out.extend((tool.get("uv", {}) or {}).get("dev-dependencies", []) or [])
    return [s for s in out if isinstance(s, str)]


def _pyproject_pins(path: Path, root: Path) -> list[Pin]:
    rel = _rel(path, root)
    text = read_source(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    lines = text.splitlines()
    used: set[int] = set()
    pins: list[Pin] = []
    for req in _dependency_strings(data):
        pin = _pin_from_requirement(req, rel, 0, "pyproject")
        if pin is not None:
            pins.append(_with_line(pin, _line_of(lines, f'"{req}"', used) if f'"{req}"' in text else _line_of(lines, req, used)))
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry", {}) or {}
    for table in [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})] + [
        g.get("dependencies", {}) for g in (poetry.get("group", {}) or {}).values() if isinstance(g, dict)
    ]:
        for name, spec in (table or {}).items():
            if _PKG_NAME.match(name):
                pins.append(_with_line(_source_table_pin(spec, rel), _line_of(lines, name, used)))
    for name, spec in ((tool.get("uv", {}) or {}).get("sources", {}) or {}).items():
        if _PKG_NAME.match(name):
            pins.append(_with_line(_source_table_pin(spec, rel), _line_of(lines, name, used)))
    extend = (tool.get("ruff", {}) or {}).get("extend")
    if isinstance(extend, str) and "py-ci-shared" in extend and not extend.startswith("$"):
        pins.append(_pin(rel, _line_of(lines, extend, used), "pyproject", "ruff-extend", None, sibling=True))
    return pins


def _with_line(pin: Pin, line: int) -> Pin:
    return Pin(pin.path, line, pin.location, pin.target, pin.ref, pin.kind)


def _source_table_pin(spec: Any, rel: str) -> Pin:
    if isinstance(spec, dict):
        if "path" in spec:
            return _pin(rel, 0, "pyproject", "package", None, sibling=True)
        ref = spec.get("rev") or spec.get("tag") or spec.get("branch")
        if spec.get("branch") and not (spec.get("rev") or spec.get("tag")):
            return Pin(rel, 0, "pyproject", "package", str(ref), "branch")
        return _pin(rel, 0, "pyproject", "package", str(ref) if ref else None)
    return _pin(rel, 0, "pyproject", "package", None)


def _requirements_pins(path: Path, root: Path) -> list[Pin]:
    rel = _rel(path, root)
    pins = []
    for i, ln in _code_lines(read_source(path)):
        pin = _pin_from_requirement(ln.strip(), rel, i, "requirements")
        if pin is not None:
            pins.append(pin)
    return pins


def _uv_lock_pins(path: Path, root: Path) -> list[Pin]:
    rel = _rel(path, root)
    text = read_source(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    lines = text.splitlines()
    pins = []
    for pkg in data.get("package", []) or []:
        if not (isinstance(pkg, dict) and _PKG_NAME.match(str(pkg.get("name", "")))):
            continue
        src = pkg.get("source", {}) or {}
        want = f'name = "{pkg.get("name")}"'
        line = next((i for i, ln in enumerate(lines, 1) if ln.strip() == want), 1)
        if "git" in src:
            sha = str(src["git"]).rsplit("#", 1)[1] if "#" in str(src["git"]) else None
            pins.append(_pin(rel, line, "uv.lock", "package", sha))
        elif "editable" in src or "directory" in src or "path" in src:
            pins.append(_pin(rel, line, "uv.lock", "package", None, sibling=True))
    return pins


def _precommit_pins(path: Path, root: Path) -> list[Pin]:
    rel = _rel(path, root)
    lines = read_source(path).splitlines()
    pins = []
    for idx, ln in enumerate(lines):
        if _PRECOMMIT_REPO.match(ln):
            ref = None
            for nxt in lines[idx + 1 : idx + 6]:
                m = _REV.match(nxt)
                if m:
                    ref = m.group(1)
                    break
            pins.append(_pin(rel, idx + 1, "pre-commit", "pre-commit", ref))
    return pins + [p for p in _yaml_install_pins(path, root, "pre-commit")]


def _yaml_install_pins(path: Path, root: Path, location: str) -> list[Pin]:
    """``pip install`` of a py-ci-shared git URL or sibling path, and ``git clone`` of the repo, in a YAML file."""
    rel = _rel(path, root)
    pins = []
    for i, ln in _code_lines(read_source(path)):
        url = _GIT_URL.search(ln)
        if url:
            pins.append(_pin(rel, i, location, "package", url.group(1)))
        elif _PIP_LINE.search(ln) and _SIBLING.search(ln):
            pins.append(_pin(rel, i, location, "package", None, sibling=True))
        elif _CLONE.search(ln):
            branch = _CLONE_BRANCH.search(ln)
            pins.append(_pin(rel, i, location, "config-clone", branch.group(1) if branch else None))
    return pins


def _workflow_pins(path: Path, root: Path) -> list[Pin]:
    rel = _rel(path, root)
    raw = read_source(path).splitlines()
    pins = []
    for i, ln in _code_lines("\n".join(raw)):
        m = _USES.search(ln)
        if m:
            sub = (m.group(1) or "").strip("/")
            parts = sub.split("/")
            if len(parts) >= 3 and parts[1] == "workflows":
                target = f"workflow:{parts[-1]}"
            elif len(parts) >= 3 and parts[1] == "actions":
                target = f"action:{parts[2]}"
            else:
                target = f"action:{sub or 'root'}"
            pins.append(_pin(rel, i, "workflow", target, m.group(2)))
            continue
        m = _REF_INPUT.search(ln)
        if m and "inputs." not in ln and "${{" not in m.group(1):
            pins.append(_pin(rel, i, "workflow", "ref-input", m.group(1)))
            continue
        if _CHECKOUT_REPO.search(ln):
            indent = len(ln) - len(ln.lstrip())
            ref = None
            for nxt in raw[i : i + 6]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) < indent:
                    break
                r = _REF_KEY.match(nxt)
                if r:
                    ref = r.group(1)
                    break
            pins.append(_pin(rel, i, "workflow", "checkout", ref))
    return pins + _yaml_install_pins(path, root, "workflow")


def _safe(reader: Callable[[Path, Path], list[Pin]], path: Path, root: Path) -> list[Pin]:
    """*reader*'s pins, or none for a file that cannot be decoded (a pin in it could not be read either)."""
    try:
        return reader(path, root)
    except CoreError:
        return []


def find_pins(repo_root: PathLike) -> list[Pin]:
    """Every py-ci-shared pin in the repo's tracked files, sorted by path and line."""
    root = Path(repo_root)
    pins: list[Pin] = []
    for path in iter_files(root, ("pyproject.toml",), include_untracked=False):
        pins.extend(_safe(_pyproject_pins, path, root))
    for path in iter_files(root, ("requirements*.txt", "requirements*.in", "*requirements.txt", "constraints*.txt"), include_untracked=False):
        pins.extend(_safe(_requirements_pins, path, root))
    for path in iter_files(root, ("uv.lock",), include_untracked=False):
        pins.extend(_safe(_uv_lock_pins, path, root))
    for path in iter_files(root, (".pre-commit-config.yaml", ".pre-commit-config.yml"), include_untracked=False):
        pins.extend(_safe(_precommit_pins, path, root))
    for path in iter_files(root, (".github/*.yml", ".github/*.yaml"), include_untracked=False):
        pins.extend(_safe(_workflow_pins, path, root))
    return sorted(set(pins), key=lambda p: (p.path, p.line, p.target))


def known_modules(package_dir: Optional[Path] = None) -> list[str]:
    """Every module name of this package, private ones included (a consumer can import ``_toml_compat``)."""
    here = package_dir or Path(__file__).resolve().parent
    names = {p.stem for p in here.glob("*.py") if p.stem not in ("__init__", "__main__")}
    names |= {p.name for p in here.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    return sorted(names)


def _config_modules(path: Path) -> list[str]:
    try:
        data = tomllib.loads(read_source(path))
    except tomllib.TOMLDecodeError:
        return []
    table = (data.get("tool", {}) or {}).get("py_ci_shared")
    if not isinstance(table, dict):
        return []
    out = [str(n) for n in table.get("enable", []) or []]
    for name, section in (table.get("gates", {}) or {}).items():
        if isinstance(section, dict) and section.get("enabled", True) is not False:
            out.append(str(section.get("module", name)))
    return out


def find_module_usage(repo_root: PathLike, modules: Optional[Iterable[str]] = None) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(used, unknown)``: module -> files naming it; names after ``py_ci_shared.`` that are no module of this package.

    Reads tracked non-Markdown text files outside ``audits/``; ``.py`` files are also parsed for
    ``from py_ci_shared import <module>``.
    """
    root = Path(repo_root)
    known = set(modules if modules is not None else known_modules())
    used: dict[str, set[str]] = {}
    unknown: dict[str, set[str]] = {}

    def note(name: str, rel: str) -> None:
        (used if name in known else unknown).setdefault(name, set()).add(rel)

    for path in iter_files(root, _USAGE_PATTERNS, exclude=_USAGE_EXCLUDE, include_untracked=False):
        rel = _rel(path, root)
        if not _mentions(path, (b"py_ci_shared", b"py-ci-shared")):
            continue
        try:
            text = read_source(path)
        except CoreError:
            continue
        for m in _MODULE_REF.finditer(text):
            note(m.group(1), rel)
        for m in _CLI_RUN.finditer(text):
            if m.group(1) in known:
                note(m.group(1), rel)
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                tree = None
            if tree is not None:
                for target in ImportAliases.from_tree(tree).mapping.values():
                    parts = target.split(".")
                    if parts[0] == "py_ci_shared" and len(parts) > 1:
                        note(parts[1], rel)
        if path.name == "pyproject.toml":
            for name in _config_modules(path):
                note(name, rel)
    return ({k: sorted(v) for k, v in sorted(used.items())}, {k: sorted(v) for k, v in sorted(unknown.items())})


def _mentions(path: Path, needles: Sequence[bytes]) -> bool:
    """A raw-bytes prefilter: decoding every large data file in a repo dominated the run."""
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    return any(n in raw for n in needles)


def _imports_shared(stmts: Sequence[ast.stmt]) -> bool:
    for node in (n for s in stmts for n in _fast_walk(s)):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "py_ci_shared" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "py_ci_shared":
            return True
        if isinstance(node, ast.Call) and node.args and _str_arg_is_shared(node.args[0]):
            name = _call_name(node)
            if name.endswith(("import_module", "__import__")):
                return True
    return False


def _str_arg_is_shared(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.split(".")[0] == "py_ci_shared"


def _call_name(node: ast.Call) -> str:
    parts = []
    cur: ast.AST = node.func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any((t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", "")) in _IMPORT_ERRORS for t in types)


def _exit_code_zero(call: ast.Call) -> bool:
    if not call.args:
        return True
    arg = call.args[0]
    return isinstance(arg, ast.Constant) and arg.value in (0, None, False)


def _silent_shape(body: Sequence[ast.stmt]) -> Optional[str]:
    """The silent-skip rule a fallback body matches, or None when it fails loudly (raise, non-zero exit, pytest.fail)."""
    shapes: set[str] = set()
    for node in (n for s in body for n in _fast_walk(s)):
        if isinstance(node, ast.Raise):
            call = node.exc
            if isinstance(call, ast.Call) and _call_name(call) == "SystemExit" and _exit_code_zero(call):
                shapes.add("exit-0")
                continue
            return None
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _EXIT_CALLS or name.endswith(".exit"):
                if not _exit_code_zero(node):
                    return None
                shapes.add("exit-0")
            elif name.endswith("fail") and name.split(".")[0] in ("pytest", "fail"):
                return None
            elif name in ("pytest.skip", "skip", "pytest.xfail", "unittest.SkipTest", "SkipTest"):
                shapes.add("skip")
        elif isinstance(node, ast.Return) and node.value is not None:
            val = node.value
            if isinstance(val, ast.Constant) and isinstance(val.value, int) and not isinstance(val.value, bool) and val.value != 0:
                return None
            if isinstance(val, ast.Constant) and val.value in (0, None):
                shapes.add("exit-0" if val.value == 0 else "swallowed")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if any(n.startswith("collect_ignore") for n in names):
                shapes.add("collect-ignore")
            elif isinstance(node.value, ast.Constant) and node.value.value in (False, None) and names:
                shapes.add("availability-flag")
    for rule in ("collect-ignore", "exit-0", "skip", "availability-flag", "swallowed"):
        if rule in shapes:
            return rule
    return "swallowed"


_SHAPE_TEXT = {
    "collect-ignore": "sets collect_ignore when py_ci_shared does not import: those tests vanish from the run",
    "exit-0": "exits 0 when py_ci_shared does not import: a missing install reads as a pass",
    "skip": "skips when py_ci_shared does not import",
    "availability-flag": "sets a flag to False when py_ci_shared does not import: the checks behind it stop running",
    "swallowed": "swallows the ImportError of py_ci_shared and carries on",
}


def _is_find_spec_guard(test: ast.expr) -> bool:
    return any(isinstance(n, ast.Call) and _call_name(n).endswith("find_spec") and n.args and _str_arg_is_shared(n.args[0]) for n in _fast_walk(test))


def _flag_names(body: Sequence[ast.stmt]) -> list[str]:
    out: list[str] = []
    for node in (n for s in body for n in _fast_walk(s)):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value in (False, None):
            out.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def _loudly_checked_flags(tree: ast.AST) -> set[str]:
    """Names tested as ``if not NAME:`` (or ``NAME is False``/``is None``) where that branch fails loudly."""
    out: set[str] = set()
    for node in _fast_walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        name = None
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, (ast.Name, ast.Attribute)):
            name = test.operand.id if isinstance(test.operand, ast.Name) else test.operand.attr
        elif isinstance(test, ast.Compare) and isinstance(test.left, (ast.Name, ast.Attribute)) and isinstance(test.ops[0], (ast.Is, ast.Eq)):
            comp = test.comparators[0]
            if isinstance(comp, ast.Constant) and comp.value in (False, None):
                name = test.left.id if isinstance(test.left, ast.Name) else test.left.attr
        if name and _silent_shape(node.body) is None:
            out.add(name)
    return out


def _skips_in_tree(tree: ast.AST, rel: str) -> list[Finding]:
    out: list[Finding] = []
    for node in _fast_walk(tree):
        if isinstance(node, ast.Call) and _call_name(node).split(".")[-1] == "importorskip" and node.args and _str_arg_is_shared(node.args[0]):
            mod = node.args[0].value if isinstance(node.args[0], ast.Constant) else ""
            out.append(Finding(rel, node.lineno, "importorskip", f"importorskip({mod!r}): the gate turns SKIPPED when the install is missing or too old"))
        elif isinstance(node, ast.Try) and _imports_shared(node.body):
            for handler in node.handlers:
                if _catches_import_error(handler):
                    rule = _silent_shape(handler.body)
                    if rule == "availability-flag":
                        names = ", ".join(f"`{n}`" for n in _flag_names(handler.body))
                        out.append(Finding(rel, handler.lineno, rule, f"{names}: {_SHAPE_TEXT[rule]}"))
                    elif rule is not None:
                        out.append(Finding(rel, handler.lineno, rule, _SHAPE_TEXT[rule]))
        elif isinstance(node, ast.If) and _is_find_spec_guard(node.test):
            # `if find_spec("py_ci_shared") is None: <fallback>`: the fallback is the branch that does not import it.
            for branch in (node.body, node.orelse):
                if branch and not _imports_shared(branch):
                    rule = _silent_shape(branch)
                    if rule is not None and rule != "swallowed":
                        out.append(Finding(rel, branch[0].lineno, rule, _SHAPE_TEXT[rule]))
    return out


def find_silent_skips(repo_root: PathLike) -> list[Finding]:
    """Every place a missing py_ci_shared install makes a gate skip itself instead of failing."""
    root = Path(repo_root)
    out: list[Finding] = []
    checked: set[str] = set()
    for path in iter_files(root, ("*.py",), exclude=_USAGE_EXCLUDE, include_untracked=False):
        if not _mentions(path, (b"py_ci_shared", b"AVAILABLE", b"_available")):
            continue
        try:
            text = read_source(path)
        except CoreError:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        out.extend(_skips_in_tree(tree, _rel(path, root)))
        checked |= _loudly_checked_flags(tree)
    # A flag every reader checks and fails on (`if not SHARED_AVAILABLE: return 1`) is not a silent skip.
    out = [f for f in out if f.rule != "availability-flag" or not all(n.strip("`") in checked for n in f.message.split(":", 1)[0].split(", "))]
    return sorted(out, key=lambda f: (f.path, f.line))


def _test_dirs(root: Path) -> list[str]:
    """The shallowest ``tests`` directories holding tracked Python files (``social`` nests one per sub-project)."""
    dirs: set[str] = set()
    for path in iter_files(root, ("test_*.py",), exclude=_USAGE_EXCLUDE, include_untracked=False):
        parts = _rel(path, root).split("/")
        if "tests" in parts[:-1]:
            dirs.add("/".join(parts[: parts.index("tests") + 1]))
    return sorted(d for d in dirs if not any(d != o and d.startswith(o + "/") for o in dirs))


def _local_copies(root: Path) -> list[Finding]:
    from .local_copy_report import find_local_copies

    try:
        findings, _ = find_local_copies(root, test_dirs=_test_dirs(root), use_git=None)
    except CoreError:
        return []
    return findings


@dataclass
class RepoReport:
    """Everything the matrix knows about one consumer repo."""

    name: str
    root: Path
    pins: list[Pin] = field(default_factory=list)
    modules: dict[str, list[str]] = field(default_factory=dict)
    unknown_modules: dict[str, list[str]] = field(default_factory=dict)
    silent_skips: list[Finding] = field(default_factory=list)
    local_copies: list[Finding] = field(default_factory=list)
    resolved: dict[str, Optional[str]] = field(default_factory=dict)
    behind: dict[str, tuple[int, str]] = field(default_factory=dict)  # pin ref -> (releases behind, latest release)
    allow_behind: int = 0

    def pin_key(self, pin: Pin) -> str:
        """The identity two pins must share to agree: resolved SHA, else the SHA's 7-char prefix, else the label."""
        if pin.ref is None or pin.moving:
            return pin.label()
        full = self.resolved.get(pin.ref)
        if full:
            return full[:12]
        return pin.ref.lower()[:7] if pin.kind == "sha" else pin.ref

    @property
    def distinct_refs(self) -> list[str]:
        return sorted({self.pin_key(p) for p in self.pins})

    @property
    def pins_agree(self) -> bool:
        return len(self.distinct_refs) <= 1

    @property
    def moving_pins(self) -> list[Pin]:
        return [p for p in self.pins if p.moving]

    @property
    def stale_pins(self) -> list[Pin]:
        """Fixed pins more than ``allow_behind`` releases behind the latest release tag."""
        return [p for p in self.pins if p.ref is not None and not p.moving and self.behind.get(p.ref, (0, ""))[0] > self.allow_behind]

    @property
    def failing(self) -> bool:
        return not self.pins_agree or bool(self.moving_pins) or bool(self.silent_skips) or bool(self.stale_pins)

    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        if not self.pins_agree:
            by_key: dict[str, list[Pin]] = {}
            for p in self.pins:
                by_key.setdefault(self.pin_key(p), []).append(p)
            detail = "; ".join(
                f"{k}: " + ", ".join(f"{p.path}:{p.line}" for p in v[:4]) + (f" (+{len(v) - 4})" if len(v) > 4 else "") for k, v in sorted(by_key.items())
            )
            first = self.pins[0]
            out.append(Finding(first.path, first.line, "pins-disagree", f"{len(by_key)} different py-ci-shared refs: {detail}"))
        out.extend(
            Finding(p.path, p.line, "moving-pin", f"{p.target} pinned to {p.label()} ({p.kind}): it changes without a commit here") for p in self.moving_pins
        )
        for p in self.stale_pins:
            n, latest = self.behind[p.ref or ""]
            out.append(Finding(p.path, p.line, "stale-pin", f"{p.target} pinned to {p.label()}: {n} release{'s' if n != 1 else ''} behind {latest}"))
        out.extend(self.silent_skips)
        return out


def scan_repo(
    repo_root: PathLike,
    *,
    name: Optional[str] = None,
    modules: Optional[Iterable[str]] = None,
    resolver: Optional[Callable[[str], Optional[str]]] = None,
    local_copies: bool = True,
    allow_behind: int = 0,
) -> RepoReport:
    """Read one consumer repo. *resolver* maps a ref to a full SHA; a :class:`RefResolver` also dates each pin."""
    root = Path(repo_root)
    if not root.is_dir():
        raise CoreError(f"repo root is not a directory: {root}")
    used, unknown = find_module_usage(root, modules)
    report = RepoReport(
        name=name or root.name,
        root=root,
        pins=find_pins(root),
        modules=used,
        unknown_modules=unknown,
        silent_skips=find_silent_skips(root),
        local_copies=_local_copies(root) if local_copies else [],
        allow_behind=allow_behind,
    )
    if resolver is not None:
        report.resolved = {p.ref: resolver(p.ref) for p in report.pins if p.ref is not None}
    if isinstance(resolver, RefResolver):
        for p in report.pins:
            full = report.resolved.get(p.ref) if p.ref is not None else None
            if p.ref is not None and full and not p.moving:
                lag = resolver.releases_behind(full)
                if lag is not None:
                    report.behind[p.ref] = lag
    return report


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_markdown(reports: Sequence[RepoReport], *, modules: Optional[Iterable[str]] = None, title: str = "py-ci-shared adoption matrix") -> str:
    """The report: per-repo summary, pins, module matrix (modules x repos), workflow matrix, findings."""
    names = [r.name for r in reports]
    lines = [f"# {title}", ""]
    lines += [
        "## Summary",
        "",
        "| repo | pins | distinct refs | pins agree | moving pins | releases behind | silent skips | local copies | modules used |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        refs = ", ".join(f"`{k}`" for k in r.distinct_refs) or "none"
        agree = "yes" if r.pins_agree else "**no**"
        lag = max((n for n, _ in r.behind.values()), default=None)
        lag_cell = "?" if lag is None else (f"**{lag}**" if r.stale_pins else str(lag))
        lines.append(
            f"| {r.name} | {len(r.pins)} | {refs} | {agree} | {len(r.moving_pins)} | {lag_cell} | {len(r.silent_skips)} | {len(r.local_copies)} |"
            f" {len(r.modules)} |"
        )
    lines += ["", "## Pins", "", "| repo | where | location | target | ref | kind |", "|---|---|---|---|---|---|"]
    for r in reports:
        for p in r.pins:
            full = r.resolved.get(p.ref) if p.ref is not None else None
            ref = f"`{p.label()}`" + (f" (= `{full[:12]}`)" if full and p.ref is not None and not full.startswith(p.ref) else "")
            lines.append(f"| {r.name} | `{p.path}:{p.line}` | {p.location} | {_cell(p.target)} | {ref} | {p.kind}{' (moving)' if p.moving else ''} |")
    all_modules = sorted(set(modules or []) | {m for r in reports for m in r.modules})
    counts = {m: sum(m in r.modules for r in reports) for m in all_modules}
    lines += [
        "",
        "## Modules",
        "",
        "X = the repo's tracked non-Markdown files outside `audits/` name `py_ci_shared.<module>` or enable it in `[tool.py_ci_shared]`.",
        "",
    ]
    lines += ["| module | " + " | ".join(names) + " | n |", "|---|" + "---|" * len(names) + "---|"]
    for m in sorted(all_modules, key=lambda x: (counts[x], x)):
        lines.append(f"| `{m}` | " + " | ".join("X" if m in r.modules else "." for r in reports) + f" | {counts[m]} |")
    lines.append("| **modules used** | " + " | ".join(str(len(r.modules)) for r in reports) + " | |")
    targets = sorted({p.target for r in reports for p in r.pins if p.target.startswith(("workflow:", "action:"))})
    if targets:
        lines += ["", "## Shared workflows and actions", "", "| target | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        for t in targets:
            cells = []
            for r in reports:
                labels = sorted({p.label() for p in r.pins if p.target == t})
                cells.append(", ".join(f"`{x[:12]}`" for x in labels) if labels else ".")
            lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines += ["", "## Findings", ""]
    any_finding = False
    for r in reports:
        found = r.findings()
        extra = [f"- {r.name}: `{f.path}:{f.line}` local-copy: {f.message}" for f in r.local_copies]
        extra += [f"- {r.name}: unknown-module `py_ci_shared.{m}` in " + ", ".join(f"`{p}`" for p in v[:3]) for m, v in r.unknown_modules.items()]
        for f in found:
            lines.append(f"- {r.name}: `{f.path}:{f.line}` {f.rule}: {f.message}")
        lines.extend(extra)
        any_finding = any_finding or bool(found) or bool(extra)
    if not any_finding:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def load_repo_list(path: PathLike) -> list[tuple[str, Path]]:
    """``[(name, root)]`` from a TOML file: ``repos = [...]`` or ``[[repo]]`` tables; paths relative to the file."""
    p = Path(path)
    data = tomllib.loads(read_source(p))
    out: list[tuple[str, Path]] = []
    for item in data.get("repos", []) or []:
        root = (p.parent / str(item)).resolve()
        out.append((root.name, root))
    for table in data.get("repo", []) or []:
        root = (p.parent / str(table["path"])).resolve()
        out.append((str(table.get("name", root.name)), root))
    return out


def _unique_names(roots: Sequence[tuple[str, Path]]) -> list[tuple[str, Path]]:
    seen: dict[str, int] = {}
    for name, _ in roots:
        seen[name] = seen.get(name, 0) + 1
    return [((f"{root.parent.name}/{name}" if seen[name] > 1 else name), root) for name, root in roots]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Report the adoption matrix; 1 when a repo has disagreeing or moving pins or silent skips (0 with --advisory)."""
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.adoption_matrix", description=__doc__.split("\n", 1)[0])
    parser.add_argument("repos", nargs="*", help="consumer repo roots")
    parser.add_argument("--repos-file", help="TOML file listing repos (`repos = [...]` or `[[repo]]` with path/name)")
    parser.add_argument("--resolve-in", help="a py-ci-shared git checkout used to resolve tags and short SHAs to commits")
    parser.add_argument("--output", help="also write the markdown report to this file")
    parser.add_argument("--advisory", action="store_true", help="report only; exit 0 even when a repo fails")
    parser.add_argument("--no-local-copies", action="store_true", help="skip the local-copy scan")
    parser.add_argument("--allow-behind", type=int, default=0, help="releases a fixed pin may lag the latest tag (default 0; needs --resolve-in)")
    args = parser.parse_args(argv)
    roots: list[tuple[str, Path]] = [(Path(r).resolve().name, Path(r).resolve()) for r in args.repos]
    if args.repos_file:
        roots += load_repo_list(args.repos_file)
    if not roots:
        parser.error("give repo roots or --repos-file")
    missing = [str(r) for _, r in roots if not r.is_dir()]
    if missing:
        sys.stderr.write(f"adoption_matrix: not a directory: {', '.join(missing)}\n")
        return 2
    resolver = RefResolver(args.resolve_in) if args.resolve_in else None
    modules = known_modules()
    reports = [
        scan_repo(root, name=name, modules=modules, resolver=resolver, local_copies=not args.no_local_copies, allow_behind=args.allow_behind)
        for name, root in _unique_names(roots)
    ]
    text = render_markdown(reports, modules=[m for m in modules if not m.startswith("_")])
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    failing = [r.name for r in reports if r.failing]
    if failing:
        sys.stderr.write(f"\nadoption_matrix: {len(failing)} repo(s) with disagreeing/moving/stale pins or silent skips: {', '.join(failing)}\n")
    return 0 if args.advisory or not failing else 1


if __name__ == "__main__":
    raise SystemExit(main())
