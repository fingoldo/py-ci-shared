"""A first-party sibling's version floor in ``pyproject.toml`` against the revision the CI workflows install of it.

llm_bench 57e79a7 raised ``pyutilz>=1.1`` in ``pyproject.toml`` while ``ci.yml`` (5 sites) and ``mypy-full.yml`` (1)
still ran ``git -C pyutilz checkout 8ffd7e6``, which is pyutilz 1.0.0. uv could not resolve, both workflows failed at
install, and only on CI: the author had an editable pyutilz 1.1 locally (fixed in d19b2b0). The neighbouring gates do
not see this: ``git_dependency_pins`` wants a git URL pinned to a SHA, ``adoption_matrix`` follows py-ci-shared pins
only, and ``checkout_resolution`` tests the checkout itself.

The gate collects every floor a sibling has in ``pyproject.toml`` (``dependencies``, ``optional-dependencies``,
``dependency-groups``; ``>=``, ``~=`` and ``==`` all set a floor), then every place a workflow (or ``[tool.uv.sources]``)
installs that sibling at a fixed ref:

* ``git -C <sibling> checkout <ref>`` (llm_bench's shape), ``git clone ... --branch <ref> .../<sibling>``;
* a ``github.com/<owner>/<sibling>.git@<ref>`` URL (pip, uv, a ``name @ git+...`` requirement);
* ``uses: <owner>/<sibling>/...@<ref>``;
* ``actions/checkout`` with ``repository: <owner>/<sibling>`` and ``ref: <ref>``;
* ``[tool.uv.sources] <sibling> = { git = ..., rev | tag | branch = <ref> }``.

Each ref is resolved to the sibling's version at that ref: a release tag (``v1.2.0``) is its own version; anything
else is read from ``pyproject.toml`` at that ref in a local clone of the sibling (``resolve_in``), else from GitHub
(``raw.githubusercontent.com``, with ``GITHUB_TOKEN`` or ``CONSUMER_READ_TOKEN`` when set). A site below the floor is a
finding, and so is a ref that cannot be resolved: an unknown version is not a pass. A ref built from an expression
(``${{ ... }}``) is not a fixed ref and is skipped.

Usage in a consumer's meta test::

    from py_ci_shared.sibling_floor_skew import assert_sibling_floor_skew

    def test_ci_installs_the_sibling_versions_pyproject_requires():
        assert_sibling_floor_skew(REPO_ROOT, resolve_in={"pyutilz": REPO_ROOT.parent / "pyutilz"})

or from CI: ``python -m py_ci_shared.sibling_floor_skew . --resolve-in pyutilz=../pyutilz``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ._core import Baseline, CoreError, EmptyScanError, Finding, SourceError, UnparsedFilesError, read_source
from ._core.git import GitError, run_git
from ._toml_compat import tomllib

__all__ = [
    "DEFAULT_OWNER",
    "DEFAULT_SIBLINGS",
    "RULE",
    "RULE_UNRESOLVED",
    "Floor",
    "InstallSite",
    "assert_sibling_floor_skew",
    "find_floors",
    "find_install_sites",
    "find_sibling_floor_skew",
    "main",
    "version_at",
]

RULE = "sibling-floor-skew"
RULE_UNRESOLVED = "sibling-ref-unresolved"
DEFAULT_OWNER = "fingoldo"
#: The first-party repos of ``configs/consumers.toml`` plus py-ci-shared itself (compared with ``-``/``_``/``.`` folded).
DEFAULT_SIBLINGS = (
    "algopacksimple",
    "autopsia",
    "claude-usage-notifier",
    "dash_app_core",
    "glossum_backend_scripts",
    "llm_bench",
    "mlframe",
    "noema_app",
    "py-ci-shared",
    "pyutilz",
    "social",
)

PathLike = Union[str, Path]
Resolver = Callable[[str, str, str], Optional[str]]  # (sibling, owner, ref) -> version or None

_RELEASE_TAG = re.compile(r"^v?(\d+\.\d+(?:\.\d+)?)$")
_VERSION = re.compile(r"^\s*version\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
_FLOOR = re.compile(r"(>=|~=|==)\s*([0-9][0-9A-Za-z.+!-]*)")
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$", re.DOTALL)
_REF = r"([^\s'\"#;,)\]]+)"
_REF_KEY = re.compile(r"^\s*-?\s*ref:\s*['\"]?" + _REF)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _name_pattern(sibling: str) -> str:
    return "[-_.]".join(re.escape(p) for p in re.split(r"[-_.]+", sibling))


@dataclass(frozen=True)
class Floor:
    """``<sibling> >= version`` declared at ``path:line``."""

    sibling: str
    version: str
    path: str
    line: int


@dataclass(frozen=True)
class InstallSite:
    """A place that installs *sibling* at the fixed *ref*; *owner* is the GitHub owner the URL names (or the default)."""

    sibling: str
    ref: str
    owner: str
    path: str
    line: int
    shape: str


def _vtuple(version: str) -> tuple[int, ...]:
    """``1.10.0rc1`` -> ``(1, 10, 0)``; the release segment only, padded so ``1.1`` equals ``1.1.0``."""
    nums = [int(x) for x in re.findall(r"\d+", version.split("+")[0].split("!")[-1])[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def _line_of(text: str, needle: str, start: int = 0) -> int:
    at = text.find(needle, start)
    return text.count("\n", 0, at) + 1 if at >= 0 else 1


def _requirements(data: Mapping[str, Any]) -> list[str]:
    project = data.get("project", {}) or {}
    out = [str(r) for r in project.get("dependencies", []) or []]
    for reqs in (project.get("optional-dependencies", {}) or {}).values():
        out += [str(r) for r in reqs or []]
    for reqs in (data.get("dependency-groups", {}) or {}).values():
        out += [r for r in reqs or [] if isinstance(r, str)]  # an {include-group = ...} table is not a requirement
    return out


def _load_pyproject(path: Path) -> tuple[str, dict[str, Any]]:
    """The text and the parsed table; a file that is not TOML raises ``SourceError`` like an unparsable source."""
    text = read_source(path)
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        from ._core import SourceParseError

        raise SourceParseError(path, f"not valid TOML: {exc}", getattr(exc, "lineno", None)) from exc


def find_floors(pyproject: PathLike, siblings: Iterable[str] = DEFAULT_SIBLINGS, *, rel: str = "pyproject.toml") -> list[Floor]:
    """Every ``>=``/``~=``/``==`` floor *pyproject* declares on one of *siblings* (``name @ url`` sets none)."""
    text, data = _load_pyproject(Path(pyproject))
    wanted = {_norm(s): s for s in siblings}
    out: list[Floor] = []
    for req in _requirements(data):
        m = _REQ_NAME.match(req.split(";", 1)[0])
        if not m or _norm(m.group(1)) not in wanted or m.group(3).lstrip().startswith("@"):
            continue
        for _, version in _FLOOR.findall(m.group(3)):
            out.append(Floor(wanted[_norm(m.group(1))], version, rel, _line_of(text, req)))
    return out


def _uv_source_sites(text: str, data: Mapping[str, Any], wanted: Mapping[str, str], owner: str, rel: str) -> list[InstallSite]:
    sources = ((data.get("tool", {}) or {}).get("uv", {}) or {}).get("sources", {}) or {}
    out = []
    for name, spec in sources.items():
        if _norm(name) not in wanted or not isinstance(spec, dict) or "git" not in spec:
            continue
        ref = spec.get("rev") or spec.get("tag") or spec.get("branch")
        if ref:
            m = re.search(r"github\.com[/:]([\w.-]+)/", str(spec["git"]))
            out.append(InstallSite(wanted[_norm(name)], str(ref), m.group(1) if m else owner, rel, _line_of(text, f"{name}"), "uv-source"))
    return out


def _line_sites(ln: str, i: int, lines: "list[str]", sibling: str, default_owner: str) -> "list[tuple[str, str, str]]":
    """``(ref, owner, shape)`` for every fixed-ref install of *sibling* on line *i* (``ln``) of a workflow."""
    name = _name_pattern(sibling)
    checkout = r"\bgit\s+-C\s+['\"]?(?:[^\s'\"]*/)?" + name + r"['\"]?\s+checkout\s+(?:-\S+\s+)*" + _REF
    found = [(m.group(1), default_owner, "git-checkout") for m in re.finditer(checkout, ln, re.IGNORECASE)]
    found += [(m.group(2), m.group(1), "git-url") for m in re.finditer(r"github\.com[/:]([\w.-]+)/" + name + r"(?:\.git)?@" + _REF, ln, re.IGNORECASE)]
    uses = r"\buses:\s*['\"]?([\w.-]+)/" + name + r"(?:/[^@\s'\"]*)?@" + _REF
    found += [(m.group(2), m.group(1), "uses") for m in re.finditer(uses, ln, re.IGNORECASE)]
    clone = re.search(r"\bgit\s+clone\b.*github\.com[/:]([\w.-]+)/" + name + r"(?:\.git)?\b", ln, re.IGNORECASE)
    branch = re.search(r"(?:--branch|-b)[=\s]+['\"]?" + _REF, ln) if clone else None
    if clone and branch:
        found.append((branch.group(1), clone.group(1), "git-clone"))
    repo = re.search(r"\brepository:\s*['\"]?([\w.-]+)/" + name + r"['\"]?\s*$", ln, re.IGNORECASE)
    if repo:
        found += _checkout_ref(lines, i, len(ln) - len(ln.lstrip(" -")), repo.group(1))
    return [f for f in found if "$" not in f[0]]  # ``${{ inputs.ref }}`` is not a fixed ref


def _checkout_ref(lines: "list[str]", i: int, indent: int, owner: str) -> "list[tuple[str, str, str]]":
    """The ``ref:`` key next to an ``actions/checkout`` ``repository:`` line, at about its indent."""
    for nxt in lines[max(0, i - 6) : i + 6]:
        r = _REF_KEY.match(nxt)
        if r and abs((len(nxt) - len(nxt.lstrip(" -"))) - indent) <= 2:
            return [(r.group(1), owner, "actions-checkout")]
    return []


def _yaml_sites(text: str, rel: str, wanted: Mapping[str, str], owner: str) -> list[InstallSite]:
    lines = text.splitlines()
    clone_owner = {}
    for sibling in wanted.values():
        m = re.search(r"github\.com[/:]([\w.-]+)/" + _name_pattern(sibling) + r"\b", text, re.IGNORECASE)
        clone_owner[sibling] = m.group(1) if m else owner
    return [
        InstallSite(sibling, ref, who, rel, i, shape)
        for i, ln in enumerate(lines, 1)
        if not ln.lstrip().startswith("#")
        for sibling in wanted.values()
        for ref, who, shape in _line_sites(ln, i, lines, sibling, clone_owner[sibling])
    ]


def find_install_sites(repo_root: PathLike, siblings: Iterable[str] = DEFAULT_SIBLINGS, *, owner: str = DEFAULT_OWNER) -> list[InstallSite]:
    """Every fixed-ref install of one of *siblings* in ``.github/workflows/*.y*ml`` and ``[tool.uv.sources]``."""
    root = Path(repo_root)
    wanted = {_norm(s): s for s in siblings}
    out: list[InstallSite] = []
    wf_dir = root / ".github" / "workflows"
    for path in sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]) if wf_dir.is_dir() else []:
        out += _yaml_sites(read_source(path), path.relative_to(root).as_posix(), wanted, owner)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text, data = _load_pyproject(pyproject)
        out += _uv_source_sites(text, data, wanted, owner, "pyproject.toml")
        for req in _requirements(data):  # `name @ git+https://github.com/o/name@ref` in pyproject
            m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?@" + _REF, req)
            if m and _norm(m.group(2)) in wanted:
                out.append(InstallSite(wanted[_norm(m.group(2))], m.group(3), m.group(1), "pyproject.toml", _line_of(text, req), "requirement-url"))
    return out


def _git_show_version(clone: Path, ref: str) -> Optional[str]:
    try:
        proc = run_git(clone, "show", f"{ref}:pyproject.toml")
    except GitError:
        return None
    if proc.returncode != 0:
        return None
    m = _VERSION.search(proc.stdout.decode("utf-8", "replace"))
    return m.group(1) if m else None


def _github_version(sibling: str, owner: str, ref: str) -> Optional[str]:
    url = f"https://raw.githubusercontent.com/{owner}/{sibling}/{ref}/pyproject.toml"
    request = urllib.request.Request(url)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("CONSUMER_READ_TOKEN")
    if token:
        request.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # fixed https host
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    m = _VERSION.search(body)
    return m.group(1) if m else None


def version_at(sibling: str, owner: str, ref: str, *, resolve_in: Optional[Mapping[str, PathLike]] = None, network: bool = True) -> Optional[str]:
    """The sibling's version at *ref*: a release tag names it; else ``pyproject.toml`` at *ref* in the local clone, else
    on GitHub (when *network*). None when nothing can say."""
    tag = _RELEASE_TAG.match(ref)
    if tag:
        return tag.group(1)
    clones = {_norm(k): Path(v) for k, v in (resolve_in or {}).items()}
    clone = clones.get(_norm(sibling))
    if clone is not None:
        found = _git_show_version(clone, ref)
        if found is not None:
            return found
    return _github_version(sibling, owner, ref) if network else None


def _site_finding(site: InstallSite, top: Floor, version: Optional[str]) -> Optional[Finding]:
    where = f"{top.sibling}>={top.version} ({top.path}:{top.line})"
    if version is None:
        message = (
            f"installs {site.sibling}@{site.ref} ({site.shape}), whose version cannot be resolved, against the floor {where}. "
            f"Pass resolve_in={{'{site.sibling}': <local clone>}} (--resolve-in) or a token that can read {site.owner}/{site.sibling}"
        )
        return Finding(site.path, site.line, RULE_UNRESOLVED, message, key=f"{RULE_UNRESOLVED}::{site.path}::{site.sibling}@{site.ref}")
    if _vtuple(version) < _vtuple(top.version):
        message = (
            f"installs {site.sibling}@{site.ref} ({site.shape}) = {version}, below the floor {where}: the install fails "
            f"on CI only. Move the ref to a {site.sibling} revision at or above {top.version}"
        )
        return Finding(site.path, site.line, RULE, message, key=f"{RULE}::{site.path}::{site.sibling}@{site.ref}<{top.version}")
    return None


def find_sibling_floor_skew(
    repo_root: PathLike,
    *,
    siblings: Iterable[str] = DEFAULT_SIBLINGS,
    owner: str = DEFAULT_OWNER,
    resolve_in: Optional[Mapping[str, PathLike]] = None,
    network: bool = True,
    resolver: Optional[Resolver] = None,
    allow_unparsed: bool = False,
) -> list[Finding]:
    """Every install site of a sibling whose version is below a floor ``pyproject.toml`` declares, or cannot be resolved.

    Raises ``EmptyScanError`` when *repo_root* has no ``pyproject.toml`` (there is nothing to compare) and
    ``UnparsedFilesError`` when it, or a workflow, cannot be read (unless *allow_unparsed*, which then reports nothing
    for that file). *resolver* replaces :func:`version_at` (tests; a custom source of versions)."""
    root = Path(repo_root)
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        raise EmptyScanError(f"no pyproject.toml under {root}: no sibling floors to compare the workflows against")
    names = list(siblings)
    try:
        floors = find_floors(pyproject, names)
        sites = find_install_sites(root, names, owner=owner) if floors else []
    except SourceError as exc:
        if allow_unparsed:
            return []
        raise UnparsedFilesError(f"cannot check sibling floors: {exc}") from exc
    resolve: Resolver = resolver or (lambda s, o, r: version_at(s, o, r, resolve_in=resolve_in, network=network))
    highest: dict[str, Floor] = {}
    for floor in floors:
        if floor.sibling not in highest or _vtuple(floor.version) > _vtuple(highest[floor.sibling].version):
            highest[floor.sibling] = floor
    cache: dict[tuple[str, str, str], Optional[str]] = {}
    out: list[Finding] = []
    for site in sites:
        top = highest.get(site.sibling)
        if top is None:
            continue
        key = (site.sibling, site.owner, site.ref)
        if key not in cache:
            cache[key] = resolve(*key)
        finding = _site_finding(site, top, cache[key])
        if finding is not None:
            out.append(finding)
    return sorted(out, key=lambda f: (f.path, f.line, f.message))


def assert_sibling_floor_skew(
    repo_root: PathLike,
    *,
    siblings: Iterable[str] = DEFAULT_SIBLINGS,
    owner: str = DEFAULT_OWNER,
    resolve_in: Optional[Mapping[str, PathLike]] = None,
    network: bool = True,
    resolver: Optional[Resolver] = None,
    baseline_path: Optional[PathLike] = None,
    refresh: bool = False,
    allow_unparsed: bool = False,
) -> None:
    """Fail on any skewed or unresolvable install site, or with *baseline_path* on one the (shrink-only) baseline does
    not accept."""
    found = find_sibling_floor_skew(
        repo_root, siblings=siblings, owner=owner, resolve_in=resolve_in, network=network, resolver=resolver, allow_unparsed=allow_unparsed
    )
    guidance = "move each install ref to a sibling revision that satisfies the pyproject floor (or lower the floor)"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="sibling_floor_skew", refresh_command="PY_CI_SHARED_REFRESH=sibling_floor_skew")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} sibling install site(s) below the pyproject floor; {guidance}:\n  " + "\n  ".join(f.render() for f in found))


def _resolve_in_arg(values: Sequence[str]) -> dict[str, Path]:
    out = {}
    for value in values:
        name, sep, path = value.partition("=")
        if not sep or not name or not path:
            raise CoreError(f"--resolve-in takes <sibling>=<path>, got {value!r}")
        out[name] = Path(path)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: print every finding; exit 1 when there is one, 2 on a usage or read error."""
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.sibling_floor_skew", description=__doc__.split("\n", 1)[0])
    parser.add_argument("repo", nargs="?", default=".", help="the repo whose pyproject floors and workflows are compared")
    parser.add_argument("--resolve-in", action="append", default=[], metavar="SIBLING=PATH", help="a local clone to read sibling versions from")
    parser.add_argument("--sibling", action="append", default=[], help="a first-party package name (default: the consumer list)")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="GitHub owner for refs whose URL names none")
    parser.add_argument("--no-network", action="store_true", help="never ask GitHub; an unresolvable ref stays a finding")
    args = parser.parse_args(argv)
    try:
        found = find_sibling_floor_skew(
            args.repo,
            siblings=args.sibling or DEFAULT_SIBLINGS,
            owner=args.owner,
            resolve_in=_resolve_in_arg(args.resolve_in),
            network=not args.no_network,
        )
    except (CoreError, AssertionError) as exc:
        sys.stderr.write(f"sibling_floor_skew: {exc}\n")
        return 2
    for finding in found:
        sys.stdout.write(finding.render() + "\n")
    sys.stdout.write(f"sibling_floor_skew: {len(found)} finding(s)\n")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
