"""Pin discovery for the adoption matrix: what a consumer repo pins py-ci-shared to, and how each pin is classified.

The matrix imports every name from here, so ``adoption_matrix.Pin`` and friends keep working (the split is for module size, 1000-LOC budget)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional, Union
import logging

from ._core import CoreError, iter_files, read_source
from ._adoption_pin_comments import COMMENT_TAG, with_comments
from ._toml_compat import tomllib

logger = logging.getLogger(__name__)
PathLike = Union[str, Path]

MOVING_KINDS = frozenset({"branch", "moving-tag", "unpinned", "sibling"})
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_RELEASE = re.compile(r"^v?\d+\.\d+\.\d+([.-]?[0-9A-Za-z.]+)?$")
_MOVING_TAG = re.compile(r"^v?\d+(\.\d+)?$")
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


@dataclass(frozen=True)
class Pin:
    """One place a repo names a py-ci-shared version. ``ref`` is None for a bare name or a URL without a ref."""

    path: str
    line: int
    location: str  # pyproject, requirements, uv.lock, pre-commit, workflow
    target: str  # package, workflow:<file>, action:<name>, pre-commit, config-clone, checkout, ref-input, ruff-extend
    ref: Optional[str]
    kind: str
    comment: Optional[str] = None  # the trailing ``# ...`` text on the pin's line, e.g. ``v1.23.8``

    @property
    def comment_tag(self) -> Optional[str]:
        """The release tag a trailing comment starts with (``# v1.23.8`` or ``# v1.23.8 (note)``), else None."""
        m = COMMENT_TAG.match(self.comment or "")
        return m.group(1) if m else None

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
    except tomllib.TOMLDecodeError as exc:
        logger.warning("pyproject.toml is not valid TOML, its pins are not checked: %s", type(exc).__name__)
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
    return Pin(pin.path, line, pin.location, pin.target, pin.ref, pin.kind, pin.comment)


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
    except tomllib.TOMLDecodeError as exc:
        logger.warning("uv.lock is not valid TOML, its pins are not checked: %s", type(exc).__name__)
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
    except CoreError as exc:
        logger.warning("could not read a consumer file, treated as empty: %s", type(exc).__name__)
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
    return sorted(set(with_comments(pins, root)), key=lambda p: (p.path, p.line, p.target))
