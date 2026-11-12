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

A pin that lags further than ``--allow-behind`` can be accepted for a while with ``--waivers FILE`` (``[[waiver]]`` tables: ``name``, ``oldest``
(the oldest release tag the repo may pin), ``until`` (an ISO date) and ``reason``). Until that date the lag down to ``oldest`` is reported as a
``pin-waiver`` note and does not fail; from the day after, the repo fails as a ``stale-pin`` again and the finding says the waiver expired.
That separates accepted lag from neglect, so one standing red job does not hide a real moving or unresolvable pin.

A fixed pin followed by a ``# vX.Y.Z`` comment (``pip install ...@<sha>  # v1.23.8``) is also compared with that tag: with ``--resolve-in`` the
tag's commit must be the pinned commit, otherwise the pin is a ``ci-pin-comment-not-release`` finding (a comment that lies about the release;
write ``master <sha7>`` instead). It is report-only unless ``--strict-pin-comments`` is given. Without a checkout the comments are not checked.

A repos file holds ``repos = ["../mlframe", ...]`` (relative to the file) or ``[[repo]]`` tables with ``path`` and an
optional ``name``.
"""

from __future__ import annotations

import argparse
import ast
import logging
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from ._core import DEFAULT_EXCLUDE, CoreError, Finding, ImportAliases, iter_files, parse_text, read_source
from ._core.git import GitError, git_text, run_git
from ._core.node_index import walk as _fast_walk
from ._toml_compat import tomllib
from .version_tag_currency import is_release_tag
from ._adoption_pin_comments import comment_mismatches
from ._adoption_waivers import Waiver, load_waivers, preflight_problem, waived_allowance
from ._adoption_pins import (  # noqa: F401  (the pin vocabulary, re-exported from its module)
    Pin,
    classify_ref,
    _pin,
    _rel,
    _code_lines,
    _pin_from_requirement,
    _line_of,
    _dependency_strings,
    _pyproject_pins,
    _with_line,
    _source_table_pin,
    _requirements_pins,
    _uv_lock_pins,
    _precommit_pins,
    _yaml_install_pins,
    _workflow_pins,
    _safe,
    find_pins,
    MOVING_KINDS,
    _PRECOMMIT_REPO,
)
from ._silent_skip_scan import _loudly_checked_flags, _skips_in_tree

logger = logging.getLogger(__name__)

__all__ = [
    "MOVING_KINDS",
    "Pin",
    "RepoReport",
    "Waiver",
    "RefResolver",
    "classify_ref",
    "find_module_usage",
    "find_pins",
    "find_silent_skips",
    "has_gate_runner",
    "is_python_repo",
    "ranked_gaps",
    "known_modules",
    "load_waivers",
    "waived_allowance",
    "load_repo_list",
    "main",
    "render_markdown",
    "scan_repo",
]

PathLike = Union[str, Path]

#: Pin kinds that do not name one fixed commit.
SILENT_RULES = ("importorskip", "collect-ignore", "exit-0", "skip", "availability-flag", "swallowed")

_MODULE_REF = re.compile(r"(?<!tool\.)\bpy_ci_shared[./]([A-Za-z_][A-Za-z0-9_]*)")
_CLI_RUN = re.compile(r"\bpy-ci-shared\s+(?:run|refresh|tool)\s+([A-Za-z_][A-Za-z0-9_]*)")
_USAGE_PATTERNS = ("*.py", "*.pyi", "*.toml", "*.yml", "*.yaml", "*.cfg", "*.ini", "*.sh", "*.ps1", "*.bat", "*.txt", "*.json", "Makefile")
_USAGE_EXCLUDE = DEFAULT_EXCLUDE | {"audits"}


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
            proc = run_git(self.repo, *args)
        except GitError as exc:
            logger.debug("git command failed while resolving a ref: %s", type(exc).__name__)
            return None
        return git_text(proc.stdout) if proc.returncode == 0 else None

    def releases(self) -> list[str]:
        """The checkout's ``vX.Y.Z`` tags, oldest first by version (``v1`` and pre-releases are not releases)."""
        if self._releases is None:
            out = self._git("tag", "--list", "v*")
            tags = [t.strip() for t in (out or "").splitlines() if is_release_tag(t.strip())]
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
            out = (self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") or "").strip()
            self._cache[ref] = out or None
        return self._cache[ref]


def known_modules(package_dir: Optional[Path] = None) -> list[str]:
    """Every module name of this package, private ones included (a consumer can import ``_toml_compat``)."""
    here = package_dir or Path(__file__).resolve().parent
    names = {p.stem for p in here.glob("*.py") if p.stem not in ("__init__", "__main__")}
    names |= {p.name for p in here.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    return sorted(names)


def _config_modules(path: Path) -> list[str]:
    try:
        data = tomllib.loads(read_source(path))
    except tomllib.TOMLDecodeError as exc:
        logger.warning("config is not valid TOML, its modules are not checked: %s", type(exc).__name__)
        return []
    table = (data.get("tool", {}) or {}).get("py_ci_shared")
    if not isinstance(table, dict):
        return []
    out = [str(n) for n in table.get("enable", []) or []]
    if table.get("resource_leak_guard") is True:  # the plugin is enabled by a key of its own, not by `enable`
        out.append("resource_leak_guard")
    if table.get("stub_signature_guard") is True:
        out.append("stub_signature_guard")
    for name, section in (table.get("gates", {}) or {}).items():
        if isinstance(section, dict) and section.get("enabled", True) is not False:
            out.append(str(section.get("module", name)))
    return out


def _py_import_modules(text: str) -> list[str]:
    """Submodule names reached through ``from py_ci_shared import <name>`` style imports of a ``.py`` text."""
    try:
        tree = parse_text(text)
    except (SyntaxError, ValueError) as exc:
        logger.warning("adoption_matrix: a .py text did not parse, so its py_ci_shared imports were not counted: %s", exc)
        return []
    out: list[str] = []
    for target in ImportAliases.from_tree(tree).mapping.values():
        parts = target.split(".")
        if parts[0] == "py_ci_shared" and len(parts) > 1:
            out.append(parts[1])
    return out


_HOOK_ID = re.compile(r"^\s*-?\s*id:\s*['\"]?([A-Za-z0-9_.-]+)['\"]?\s*$")
_REPO_LINE = re.compile(r"^\s*-?\s*repo:")
_MIN_LISTED_GATES = 2
_PYTHON_REPO_MIN_FILES = 25


def _precommit_modules(text: str, known: set[str]) -> list[str]:
    """Hook ids of the ``repo:`` block that points at py-ci-shared, when an id is a module name (``id: repo_hygiene`` or ``id: repo-hygiene``)."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if _REPO_LINE.match(line):
            inside = bool(_PRECOMMIT_REPO.match(line))
        elif inside:
            m = _HOOK_ID.match(line)
            if m and m.group(1).replace("-", "_") in known:
                out.append(m.group(1).replace("-", "_"))
    return out


def _listed_gate_names(text: str, known: set[str]) -> list[str]:
    """Gate names a ``.py`` file lists as strings: a list, tuple or set literal holding at least two module names is a parametrized gate list
    (``@pytest.mark.parametrize("gate", ["repo_hygiene", "import_cycles"])``) that the import-based count cannot see (34-F12). One lone string is not counted, as a word such as
    ``registry`` is also an ordinary string."""
    try:
        tree = parse_text(text)
    except (SyntaxError, ValueError) as exc:
        logger.warning("_listed_gate_names: file not parsed, its listed gates are not counted: %s", exc)
        return []
    out: list[str] = []
    for node in _fast_walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            hits = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value in known]
            if len(hits) >= _MIN_LISTED_GATES:
                out += hits
    return out


def _usage_names(text: str, path: Path, known: set[str]) -> list[str]:
    """Every module name one file refers to, in the order its reference kinds are checked."""
    names = [m.group(1) for m in _MODULE_REF.finditer(text)]
    names += [m.group(1) for m in _CLI_RUN.finditer(text) if m.group(1) in known]
    if path.suffix == ".py":
        names += _py_import_modules(text)
        names += _listed_gate_names(text, known)
    if path.name == ".pre-commit-config.yaml":
        names += _precommit_modules(text, known)
    if path.name == "pyproject.toml":
        names += _config_modules(path)
    return names


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
        if name.startswith("__") and name.endswith("__"):  # an attribute of the package (``py_ci_shared.__file__``, ``__version__``), never a module
            return
        (used if name in known else unknown).setdefault(name, set()).add(rel)

    for path in iter_files(root, _USAGE_PATTERNS, exclude=_USAGE_EXCLUDE, include_untracked=False):
        rel = _rel(path, root)
        if not _mentions(path, (b"py_ci_shared", b"py-ci-shared")):
            continue
        try:
            text = read_source(path)
        except CoreError:
            continue
        for name in _usage_names(text, path, known):
            note(name, rel)
    return ({k: sorted(v) for k, v in sorted(used.items())}, {k: sorted(v) for k, v in sorted(unknown.items())})


def _mentions(path: Path, needles: Sequence[bytes]) -> bool:
    """A raw-bytes prefilter: decoding every large data file in a repo dominated the run."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("substituted a fallback for %s: %s", type(exc).__name__, exc)
        logger.debug("could not read a file while looking for a pin mention: %s", type(exc).__name__)
        return False
    return any(n in raw for n in needles)


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
            tree = parse_text(text)
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
    except CoreError as exc:
        logger.warning("local copy scan failed, no local copies reported: %s", type(exc).__name__)
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
    waiver_note: str = ""
    tag_commits: dict[str, Optional[str]] = field(default_factory=dict)  # tag named in a pin comment -> its commit (None: no such tag)
    known_tags: bool = False  # the resolver saw release tags, so a tag it cannot resolve really does not exist
    enforce_comments: bool = False

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
        # A major tag (@v1) is the documented consumer form, moved by release.yml; only branches and unpinned uses are unsafe.
        return [p for p in self.pins if p.moving and p.kind != "moving-tag"]

    @property
    def stale_pins(self) -> list[Pin]:
        """Fixed pins more than ``allow_behind`` releases behind the latest release tag."""
        return [p for p in self.pins if p.ref is not None and not p.moving and self.behind.get(p.ref, (0, ""))[0] > self.allow_behind]

    @property
    def unresolvable_pins(self) -> list[Pin]:
        """Fixed pins the resolver was asked about and could not find: a typo'd or deleted tag, an unknown SHA."""
        return [p for p in self.pins if p.ref is not None and not p.moving and p.ref in self.resolved and self.resolved[p.ref] is None]

    @property
    def comment_mismatches(self) -> list[tuple[Pin, str]]:
        """``(pin, why)`` for a fixed pin whose ``# vX.Y.Z`` comment names a tag whose commit is not the pinned commit."""
        return comment_mismatches(self.pins, self.resolved, self.tag_commits, self.known_tags)

    @property
    def failing(self) -> bool:
        return (
            (self.enforce_comments and bool(self.comment_mismatches))
            or not self.pins_agree
            or bool(self.moving_pins)
            or bool(self.silent_skips)
            or bool(self.stale_pins)
            or bool(self.unresolvable_pins)
        )

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
            out.append(Finding(first.path, first.line, "pins-disagree", f"{len(by_key)} different py-ci-shared refs: {detail}. Pin every consumer to one release tag"))
        out.extend(
            Finding(p.path, p.line, "moving-pin", f"{p.target} pinned to {p.label()} ({p.kind}): it changes without a commit here. Pin a release tag or a full SHA") for p in self.moving_pins
        )
        for p in self.stale_pins:
            n, latest = self.behind[p.ref or ""]
            out.append(Finding(p.path, p.line, "stale-pin", f"{p.target} pinned to {p.label()}: {n} release{'s' if n != 1 else ''} behind {latest}. Bump the pin, or add a dated waiver to configs/pin_waivers.toml"))
        out.extend(
            Finding(p.path, p.line, "unresolvable-pin", f"{p.target} pinned to {p.label()}, which does not exist upstream: its install fails. Push the commit, fix the tag name, or pin a SHA that exists")
            for p in self.unresolvable_pins
        )
        out.extend(Finding(p.path, p.line, "ci-pin-comment-not-release", f"{p.target} pinned to {p.label()}: {why}") for p, why in self.comment_mismatches)
        out.extend(self.silent_skips)
        if self.waiver_note and self.pins:
            out.append(Finding(self.pins[0].path, self.pins[0].line, "pin-waiver", self.waiver_note))
        return out


def scan_repo(
    repo_root: PathLike,
    *,
    name: Optional[str] = None,
    modules: Optional[Iterable[str]] = None,
    resolver: Optional[Callable[[str], Optional[str]]] = None,
    local_copies: bool = True,
    allow_behind: int = 0,
    enforce_comments: bool = False,
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
        enforce_comments=enforce_comments,
    )
    if resolver is not None and not (isinstance(resolver, RefResolver) and resolver.repo is None):
        report.resolved = {p.ref: resolver(p.ref) for p in report.pins if p.ref is not None}
    if isinstance(resolver, RefResolver):
        for p in report.pins:
            full = report.resolved.get(p.ref) if p.ref is not None else None
            if p.ref is not None and full and not p.moving:
                lag = resolver.releases_behind(full)
                if lag is not None:
                    report.behind[p.ref] = lag
        if resolver.repo is not None:
            report.known_tags = bool(resolver.releases())
            report.tag_commits = {t: resolver(t) for t in {p.comment_tag for p in report.pins if p.comment_tag}}
    return report


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _summary_lines(reports: Sequence[RepoReport]) -> list[str]:
    lines = [
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
    return lines


def _pin_lines(reports: Sequence[RepoReport]) -> list[str]:
    lines = ["", "## Pins", "", "| repo | where | location | target | ref | kind |", "|---|---|---|---|---|---|"]
    for r in reports:
        for p in r.pins:
            full = r.resolved.get(p.ref) if p.ref is not None else None
            ref = f"`{p.label()}`" + (f" (= `{full[:12]}`)" if full and p.ref is not None and not full.startswith(p.ref) else "")
            lines.append(f"| {r.name} | `{p.path}:{p.line}` | {p.location} | {_cell(p.target)} | {ref} | {p.kind}{' (moving)' if p.moving else ''} |")
    return lines


def _module_lines(reports: Sequence[RepoReport], modules: Optional[Iterable[str]]) -> list[str]:
    names = [r.name for r in reports]
    all_modules = sorted(set(modules or []) | {m for r in reports for m in r.modules})
    counts = {m: sum(m in r.modules for r in reports) for m in all_modules}
    lines = [
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
    return lines


def _workflow_lines(reports: Sequence[RepoReport]) -> list[str]:
    names = [r.name for r in reports]
    targets = sorted({p.target for r in reports for p in r.pins if p.target.startswith(("workflow:", "action:"))})
    if not targets:
        return []
    lines = ["", "## Shared workflows and actions", "", "| target | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for t in targets:
        cells = []
        for r in reports:
            labels = sorted({p.label() for p in r.pins if p.target == t})
            cells.append(", ".join(f"`{x[:12]}`" for x in labels) if labels else ".")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    return lines


def _finding_lines(reports: Sequence[RepoReport]) -> list[str]:
    lines = ["", "## Findings", ""]
    any_finding = False
    for r in reports:
        found = r.findings()
        extra = [f"- {r.name}: `{f.path}:{f.line}` local-copy: {f.message}" for f in r.local_copies]
        extra += [f"- {r.name}: unknown-module `py_ci_shared.{m}` in " + ", ".join(f"`{p}`" for p in v[:3]) for m, v in r.unknown_modules.items()]
        lines.extend(f"- {r.name}: `{f.path}:{f.line}` {f.rule}: {f.message}" for f in found)
        lines.extend(extra)
        any_finding = any_finding or bool(found) or bool(extra)
    if not any_finding:
        lines.append("None.")
    return lines


def is_python_repo(root: Path) -> bool:
    """Whether *root* is a Python project: at least ``_PYTHON_REPO_MIN_FILES`` ``.py`` files outside the excluded directories.

    A Flutter or docs repo carries a ``pyproject.toml`` and a script or two for its tooling; ranking its missing Python gates would bury the real gaps."""
    seen = 0
    for _dirpath, dirnames, filenames in os.walk(root):  # walk-ok: pruned in the next line by _USAGE_EXCLUDE, which contains .claude
        dirnames[:] = [d for d in dirnames if d not in _USAGE_EXCLUDE]
        seen += sum(name.endswith(".py") for name in filenames)
        if seen >= _PYTHON_REPO_MIN_FILES:
            return True
    return False


@dataclass(frozen=True)
class GapRow:
    """One module adopted by some Python consumers and missing in the others."""

    module: str
    adopters: tuple[str, ...]
    missing: tuple[str, ...]


def ranked_gaps(reports: Sequence[RepoReport], modules: Iterable[str], python_repos: Optional[Iterable[str]] = None) -> tuple[list[GapRow], list[str]]:
    """``(gaps, unadopted)`` over the Python consumers (34-F1, 34-F2).

    ``gaps`` holds every module at least one but not every Python repo uses, most-adopted first (then by name): the gates a repo is missing that its siblings already run.
    ``unadopted`` is every module no Python repo uses at all. Adoption is the whole of what :func:`find_module_usage` sees: imports, ``py_ci_shared.<module>`` and CLI runs in any text
    file, ``[tool.py_ci_shared]`` enable lists and gate tables, pre-commit hook ids, and gate names listed in a ``.py`` file.
    """
    chosen = set(python_repos) if python_repos is not None else {r.name for r in reports if is_python_repo(r.root)}
    repos = [r for r in reports if r.name in chosen]
    gaps: list[GapRow] = []
    unadopted: list[str] = []
    for m in sorted(set(modules)):
        adopters = tuple(r.name for r in repos if m in r.modules)
        if not adopters:
            unadopted.append(m)
        elif len(adopters) < len(repos):
            gaps.append(GapRow(m, adopters, tuple(r.name for r in repos if m not in r.modules)))
    gaps.sort(key=lambda g: (-len(g.adopters), g.module))
    return gaps, unadopted


def _gap_lines(reports: Sequence[RepoReport], modules: Optional[Iterable[str]]) -> list[str]:
    names = sorted(modules or [])
    if not names:
        return []
    python_repos = [r for r in reports if is_python_repo(r.root)]
    if len(python_repos) < 2:
        return []
    gaps, unadopted = ranked_gaps(reports, names)
    lines = [
        "",
        "## Ranked gaps",
        "",
        f"Python consumers: {len(python_repos)} ({', '.join(r.name for r in python_repos)}). Most-adopted modules first; the missing repos are where to enable it next.",
        "",
    ]
    lines += ["| module | adopters | missing in |", "|---|---|---|"]
    lines += [f"| `{g.module}` | {len(g.adopters)}/{len(python_repos)} | {', '.join(g.missing)} |" for g in gaps]
    lines += ["", f"## Enabled nowhere ({len(unadopted)})", "", ", ".join(f"`{m}`" for m in unadopted) or "None."]
    lines += _runner_and_workflow_lines(python_repos)
    return lines


def has_gate_runner(root: Path) -> bool:
    """Whether the repo's ``pyproject.toml`` enables gates in ``[tool.py_ci_shared]`` (``py-ci-shared run-all`` has something to run)."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return False
    return bool(_config_modules(path))


def _runner_and_workflow_lines(python_repos: Sequence[RepoReport]) -> list[str]:
    """Python consumers without a ``[tool.py_ci_shared]`` runner (34-F7) and the reusable workflows each lacks that a sibling calls (34-F6)."""
    lines = ["", "## Python consumers without a `[tool.py_ci_shared]` gate runner", ""]
    bare = [r.name for r in python_repos if not has_gate_runner(r.root)]
    lines.append(", ".join(bare) if bare else "None.")
    used = {r.name: {p.target for p in r.pins if p.target.startswith("workflow:")} for r in python_repos}
    everything = sorted(set().union(*used.values())) if used else []
    lines += ["", "## Reusable workflows a Python consumer does not call", ""]
    rows = [
        f"- {r.name}: " + ", ".join(f"`{t[len('workflow:'):]}`" for t in everything if t not in used[r.name])
        for r in python_repos
        if any(t not in used[r.name] for t in everything)
    ]
    lines += rows or ["None."]
    return lines


def render_markdown(reports: Sequence[RepoReport], *, modules: Optional[Iterable[str]] = None, title: str = "py-ci-shared adoption matrix") -> str:
    """The report: per-repo summary, pins, module matrix (modules x repos), ranked gaps, workflow matrix, findings."""
    lines = [f"# {title}", ""]
    lines += _summary_lines(reports)
    lines += _pin_lines(reports)
    lines += _module_lines(reports, modules)
    lines += _gap_lines(reports, modules)
    lines += _workflow_lines(reports)
    lines += _finding_lines(reports)
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
    parser.add_argument("--waivers", help="TOML file of dated per-consumer lag waivers (see the module docstring)")
    parser.add_argument(
        "--strict-pin-comments",
        action="store_true",
        help="fail (not just report) a pin whose `# vX.Y.Z` comment names a tag whose commit is not the pinned commit (needs --resolve-in)",
    )
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
    waivers = load_waivers(args.waivers) if args.waivers else {}
    releases = resolver.releases() if resolver is not None else []
    problem = preflight_problem(resolver is not None, releases, waivers, [name for name, _root in _unique_names(roots)], args.advisory)
    if problem is not None:
        sys.stderr.write(f"adoption_matrix: {problem}\n")
        return 1
    reports = []
    for name, root in _unique_names(roots):
        allow, note = waived_allowance(waivers.get(name), releases, args.allow_behind, datetime.now(timezone.utc).date())
        report = scan_repo(
            root,
            name=name,
            modules=modules,
            resolver=resolver,
            local_copies=not args.no_local_copies,
            allow_behind=allow,
            enforce_comments=args.strict_pin_comments,
        )
        report.waiver_note = note
        reports.append(report)
    if resolver is None:
        sys.stderr.write("adoption_matrix: WITHOUT --resolve-in the stale-pin, unresolvable-pin, pin-comment and waiver checks are OFF; the exit code says nothing about them. Give --resolve-in a py-ci-shared checkout with tags\n")
    text = render_markdown(reports, modules=[m for m in modules if not m.startswith("_")])
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    failing = [r.name for r in reports if r.failing]
    if failing:
        sys.stderr.write(f"\nadoption_matrix: {len(failing)} repo(s) with disagreeing, moving, stale or unresolvable pins, bad pin comments, or silent skips: {', '.join(failing)}\n")
    if resolver is None and not args.advisory:  # 47-F8: without --resolve-in those checks did not run; exit 0 would claim they passed
        return 1
    return 0 if args.advisory or not failing else 1


if __name__ == "__main__":
    raise SystemExit(main())
