"""``uv.lock`` agrees with ``pyproject.toml``: every declared requirement is locked with the same specifier, and nothing undeclared is still locked.

mlframe b5931c12b: ``uv.lock`` was out of step with ``pyproject.toml``, so the lint job's ``uv lock --check`` failed on every run until someone noticed. Nothing failed before CI, because the
pre-push gates never read the lock. This gate compares the two files as text, offline: the root package's ``[package.metadata]`` ``requires-dist`` / ``requires-dev`` in the lock
against ``[project] dependencies``, ``optional-dependencies`` and ``dependency-groups`` (and ``[tool.uv] dev-dependencies``) in the pyproject.

* ``uv-lock-missing-requirement``: declared in pyproject, absent from the lock's metadata (the lock was not regenerated after an addition).
* ``uv-lock-specifier-differs``: declared and locked with different specifiers or extras (compared as sets of clauses, so spacing and clause order do not matter).
* ``uv-lock-stale-requirement``: locked but no longer declared (a removal that was not locked).
* ``uv-lock-requires-python-differs``: the lock's ``requires-python`` is not the project's.
* ``uv-lock-no-root-package``: the lock has no editable/virtual package named like the project, or its metadata is absent (an unsupported lock format); the gate cannot vouch for it.

Markers are not compared (uv rewrites their spelling); the extra a requirement belongs to is. A requirement on the project itself (``pkg[all]``) and an ``include-group`` entry are skipped. A direct
reference (``name @ url``, git, path) compares by name only; a `tool.uv.sources` override is compared by its specifier. A repository without a ``uv.lock`` has nothing to compare: do not enable the gate there (``min_files=1`` fails an empty scan).

Usage in a consumer's meta test::

    from py_ci_shared.uv_lock_parity import assert_uv_lock_parity

    def test_uv_lock_matches_pyproject():
        assert_uv_lock_parity(".")

The gate does not run ``uv lock --check``; run that in CI where the network is available.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Baseline, Finding, SourceError, UnparsedFilesError, iter_files, read_source, relative_posix
from ._core.errors import EmptyScanError
from ._toml_compat import tomllib

__all__ = [
    "RULE_MISSING",
    "RULE_NO_ROOT",
    "RULE_PYTHON",
    "RULE_SPECIFIER",
    "RULE_STALE",
    "assert_uv_lock_parity",
    "find_uv_lock_parity",
]

RULE_MISSING = "uv-lock-missing-requirement"
RULE_SPECIFIER = "uv-lock-specifier-differs"
RULE_STALE = "uv-lock-stale-requirement"
RULE_PYTHON = "uv-lock-requires-python-differs"
RULE_NO_ROOT = "uv-lock-no-root-package"

_REQ = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<rest>.*)$")
_EXTRA_MARKER = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")
_DIRECT_KEYS = ("url", "git", "path", "directory", "editable", "virtual")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _specifier(text: str) -> str:
    """A specifier as a sorted, space-free clause list, so ``>=1, <2`` and ``<2,>=1`` agree."""
    return ",".join(sorted(p.strip().replace(" ", "") for p in text.split(",") if p.strip()))


def _parse_requirement(text: str) -> "Optional[tuple[str, str, str]]":
    """``(normalized name, extras, specifier)`` of a PEP 508 string; a direct reference has the specifier ``@``. ``None`` when it does not parse."""
    body = text.split(";", 1)[0]
    match = _REQ.match(body)
    if match is None:
        return None
    rest = match.group("rest").strip().strip("()")
    extras = ",".join(sorted(e.strip().lower() for e in (match.group("extras") or "").split(",") if e.strip()))
    spec = "@" if rest.startswith("@") else _specifier(rest)
    return _norm(match.group("name")), extras, spec


def _declared(project: "dict[str, Any]", tool_uv: "dict[str, Any]") -> "dict[tuple[str, str], list[tuple[str, str]]]":
    """``{(group, name): [(extras, specifier), ...]}`` from pyproject; groups are ``main``, ``extra:<e>`` and ``dev:<group>``."""
    out: "dict[tuple[str, str], list[tuple[str, str]]]" = defaultdict(list)
    own = _norm(str(project.get("name", "")))

    def add(group: str, requirement: object) -> None:
        parsed = _parse_requirement(requirement) if isinstance(requirement, str) else None
        if parsed is not None and parsed[0] != own:
            out[(group, parsed[0])].append((parsed[1], parsed[2]))

    for req in project.get("dependencies", []):
        add("main", req)
    for extra, reqs in project.get("optional-dependencies", {}).items():
        for req in reqs:
            add(f"extra:{_norm(extra)}", req)
    for group, reqs in (project.get("__groups__") or {}).items():
        for req in reqs:
            add(f"dev:{_norm(group)}", req)
    for req in tool_uv.get("dev-dependencies", []):
        add("dev:dev", req)
    return out


def _locked_spec(entry: "dict[str, Any]") -> "tuple[str, str]":
    extras = ",".join(sorted(str(e).lower() for e in entry.get("extras", [])))
    specifier = _specifier(str(entry.get("specifier", "")))
    if not specifier and any(k in entry for k in _DIRECT_KEYS):
        specifier = "@"  # `name @ url`; a `tool.uv.sources` override keeps its specifier and is compared by it
    return extras, specifier


def _locked(root_package: "dict[str, Any]", own: str) -> "dict[tuple[str, str], list[tuple[str, str]]]":
    out: "dict[tuple[str, str], list[tuple[str, str]]]" = defaultdict(list)
    metadata = root_package.get("metadata", {})
    for entry in metadata.get("requires-dist", []):
        if _norm(str(entry["name"])) == own:
            continue  # a self-extra (`pkg[all]`) is expanded by uv into the extra's members
        marker = _EXTRA_MARKER.search(str(entry.get("marker", "")))
        group = f"extra:{_norm(marker.group(1))}" if marker else "main"
        out[(group, _norm(str(entry["name"])))].append(_locked_spec(entry))
    for group, entries in metadata.get("requires-dev", {}).items():
        for entry in entries:
            out[(f"dev:{_norm(group)}", _norm(str(entry["name"])))].append(_locked_spec(entry))
    return out


def _root_package(lock: "dict[str, Any]", name: str) -> "Optional[dict[str, Any]]":
    for package in lock.get("package", []):
        source = package.get("source", {})
        if _norm(str(package.get("name", ""))) == name and (source.get("editable") == "." or source.get("virtual") == "."):
            return package if "metadata" in package else None
    return None


def _same(declared: "list[tuple[str, str]]", locked: "list[tuple[str, str]]") -> bool:
    """Equal clause sets; a locked direct source (`@`, a git/path override from `tool.uv.sources`) stands for whatever specifier the pyproject keeps."""
    if len(declared) != len(locked):
        return False
    pairs = zip(sorted(declared), sorted(locked))
    return all(d == k or (d[0] == k[0] and k[1] == "@") for d, k in pairs)


def _describe(group: str, name: str) -> str:
    return f"{name} ({'dependencies' if group == 'main' else group.replace(':', ' ')})"


def _compare(rel: str, declared: Any, locked: Any) -> "list[Finding]":
    out: "list[Finding]" = []
    for key in sorted(declared):
        what = _describe(*key)
        if key not in locked:
            out.append(Finding(rel, 1, RULE_MISSING, f"{what} is declared in pyproject.toml but missing from uv.lock: run `uv lock`"))
        elif not _same(declared[key], locked[key]):
            out.append(Finding(rel, 1, RULE_SPECIFIER, f"{what} is locked as {sorted(locked[key])} but declared as {sorted(declared[key])}: run `uv lock`"))
    for key in sorted(set(locked) - set(declared)):
        out.append(Finding(rel, 1, RULE_STALE, f"{_describe(*key)} is locked but no longer declared in pyproject.toml: run `uv lock`"))
    return out


def _groups(data: "dict[str, Any]") -> "dict[str, list[str]]":
    """``[dependency-groups]`` flattened to requirement strings (``include-group`` entries are dropped)."""
    return {g: [r for r in reqs if isinstance(r, str)] for g, reqs in data.get("dependency-groups", {}).items()}


def _findings_for(rel: str, pyproject: "dict[str, Any]", lock: "dict[str, Any]") -> "list[Finding]":
    project = dict(pyproject.get("project", {}))
    if not project.get("name"):
        return []
    project["__groups__"] = _groups(pyproject)
    root = _root_package(lock, _norm(str(project["name"])))
    if root is None:
        return [
            Finding(
                rel,
                1,
                RULE_NO_ROOT,
                f"uv.lock has no editable/virtual package `{project['name']}` with [package.metadata]: the gate cannot compare it; regenerate with a current uv",
            )
        ]
    out = _compare(rel, _declared(project, pyproject.get("tool", {}).get("uv", {})), _locked(root, _norm(str(project["name"]))))
    wanted = project.get("requires-python")
    if wanted is not None and _specifier(str(lock.get("requires-python", ""))) != _specifier(str(wanted)):
        out.append(
            Finding(rel, 1, RULE_PYTHON, f"uv.lock requires-python is {lock.get('requires-python')!r} but pyproject.toml says {wanted!r}: run `uv lock`")
        )
    return out


def _load(path: Path) -> "dict[str, Any]":
    data: "dict[str, Any]" = tomllib.loads(read_source(path))
    return data


def find_uv_lock_parity(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every disagreement between each ``uv.lock`` under *root* and the ``pyproject.toml`` beside it, sorted by path.

    Raises ``EmptyScanError`` when fewer than *min_files* lock files are found and ``UnparsedFilesError`` for a lock or pyproject that cannot be read or parsed (unless *allow_unparsed*): a gate
    that cannot read the files must not pass them. A lock with no pyproject beside it is skipped.
    """
    base = Path(root)
    locks = iter_files(base, ("uv.lock",), use_git=use_git)
    if len(locks) < min_files:
        raise EmptyScanError(f"uv_lock_parity found {len(locks)} uv.lock file(s) under {base}, below the floor of {min_files}")
    out: "list[Finding]" = []
    unparsed: "list[str]" = []
    for lock_path in locks:
        pyproject_path = lock_path.with_name("pyproject.toml")
        rel = relative_posix(lock_path, base)
        if not pyproject_path.is_file():
            continue
        try:
            out.extend(_findings_for(rel, _load(pyproject_path), _load(lock_path)))
        except (SourceError, ValueError) as exc:
            unparsed.append(f"{rel}: {exc}")
    if unparsed and not allow_unparsed:
        raise UnparsedFilesError(f"{len(unparsed)} file(s) could not be read or parsed:\n  " + "\n  ".join(unparsed))
    return sorted(out, key=lambda f: (f.path, f.rule, f.message))


def assert_uv_lock_parity(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any pyproject/lock disagreement, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_uv_lock_parity(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "run `uv lock` and commit uv.lock together with the pyproject.toml change"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="uv_lock_parity", refresh_command="PY_CI_SHARED_REFRESH=uv_lock_parity")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} uv.lock/pyproject.toml disagreement(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
