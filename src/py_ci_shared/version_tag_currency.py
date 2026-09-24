"""Shared check: a package's declared version, its tags and its consumers' pins agree.

A library consumed by pin (git tag, not a registry) has three numbers that drift independently, and
every combination fails silently:

1. **Declared version vs newest tag.** ``pubspec.yaml``/``pyproject.toml`` says ``0.6.0`` but no
   ``v0.6.0`` tag exists: every consumer that pins a tag is still on the previous release while the
   repo believes it shipped. flutter_app_core C02-9 (2026-09-02).
2. **Tag exists but is not reachable from HEAD.** The tag was moved or cut on a branch that never
   merged, so ``ref: v0.6.0`` resolves to code nobody has reviewed on the default branch.
3. **Consumer pin age.** glossum P03-22: the app pinned ``v0.4.0`` while the sibling package had
   shipped through ``v0.5.9`` -- twelve releases of fixes, including security ones, that the
   product had simply never taken. Nothing failed; nobody looked.

Rules 1 and 2 are blocking (they are facts about one repo). Rule 3 is advisory by default, because
"pin is behind" is often a deliberate freeze -- but it should be a decision someone made this week,
not a number nobody has read since it was written.

``git`` via subprocess; manifests are parsed (TOML, or YAML via PyYAML), never searched with a regex.

Usage::

    from py_ci_shared.version_tag_currency import assert_version_is_tagged

    def test_declared_version_has_a_tag():
        assert_version_is_tagged(REPO, manifest="pubspec.yaml")
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from ._toml_compat import tomllib

_SEMVER_TAG_RE = re.compile(r"^v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z.-]+))?$")
_DEP_SECTIONS = ("dependencies", "dev_dependencies", "dependency_overrides")


class VersionCheckError(RuntimeError):
    """git could not answer a question the check depends on (not installed, not a repository, shallow history)."""


def _run_git(repo: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise VersionCheckError(f"git is not available to inspect {repo}: {exc}") from exc


def _git(repo: Path, *args: str) -> str:
    result = _run_git(repo, *args)
    if result.returncode != 0:
        raise VersionCheckError(f"git {' '.join(args)} failed in {repo} (exit {result.returncode}): {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def _strip_build(version: str) -> str:
    """Semver build metadata (``1.2.3+4``, pubspec's build number) is not part of the release a tag names."""
    return version.split("+", 1)[0].strip()


def declared_version(repo: Path, manifest: str) -> "str | None":
    """Return the version string declared in ``manifest``, or None.

    Parsed, not searched: a ``.toml`` manifest is ``[project].version`` (or ``[tool.poetry].version``), never the first
    ``version =`` of some other table; a YAML manifest (``pubspec.yaml``) is its top-level ``version``. Prerelease
    suffixes are kept (``0.6.0-beta.1`` is not ``0.6.0``); build metadata (``+4``) is dropped.
    """
    path = repo / manifest
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig")
    value: Any
    if path.suffix == ".toml":
        data = tomllib.loads(text)
        value = data.get("project", {}).get("version") or data.get("tool", {}).get("poetry", {}).get("version")
    else:
        import yaml

        data = yaml.safe_load(text) or {}
        value = data.get("version") if isinstance(data, dict) else None
    if value is None:
        return None
    return _strip_build(str(value)) or None


def _sort_key(tag: str) -> tuple:
    m = _SEMVER_TAG_RE.match(tag)
    if not m:
        return (0, 0, 0, 0, ())
    pre = m.group(4)
    # A prerelease sorts BEFORE its release (1.0.0-rc.1 < 1.0.0); numeric identifiers compare as numbers.
    pre_key = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")) if pre else ()
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre_key)


def semver_tags(repo: Path) -> list[str]:
    """Every semver-shaped tag in the repository (prereleases included), oldest first. Raises
    :class:`VersionCheckError` when git cannot list them."""
    tags = [t for t in _git(repo, "tag", "--list").splitlines() if _SEMVER_TAG_RE.match(t)]
    return sorted(tags, key=_sort_key)


def _is_ancestor(repo: Path, tag: str) -> "bool | None":
    """True/False from ``git merge-base --is-ancestor`` (exit 0 / exit 1); None when git could not decide."""
    result = _run_git(repo, "merge-base", "--is-ancestor", tag, "HEAD")
    if result.returncode in (0, 1):
        return result.returncode == 0
    return None


def find_version_tag_problems(repo: Path, manifest: str) -> list[str]:
    """Return one problem string per untagged declared version, unreachable tag, or question git could not answer."""
    version = declared_version(repo, manifest)
    if version is None:
        return [f"{manifest}: no version declared - nothing to check."]
    try:
        tags = semver_tags(repo)
    except VersionCheckError as exc:
        return [f"cannot determine whether {version} is tagged: {exc}"]
    matching = [t for t in tags if t[1:] == version or t == version]
    problems: list[str] = []
    if not matching:
        newest = tags[-1] if tags else "(none)"
        problems.append(
            f"{manifest} declares {version} and no matching tag exists (newest tag: {newest}). "
            f"Every consumer pinning a tag is still on the previous release while this repo "
            f"believes it shipped."
        )
        return problems
    tag = matching[-1]
    try:
        ancestor = _is_ancestor(repo, tag)
    except VersionCheckError as exc:
        return [f"cannot determine whether tag {tag} is on this branch: {exc}"]
    if ancestor is None:
        shallow = _run_git(repo, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
        hint = " This is a shallow clone: fetch full history (actions/checkout `fetch-depth: 0`)." if shallow else ""
        problems.append(f"cannot determine whether tag {tag} is an ancestor of HEAD (git merge-base failed).{hint}")
    elif not ancestor:
        problems.append(
            f"tag {tag} is not an ancestor of HEAD - it points at code that is not on this "
            f"branch, so a consumer pinning it gets something nobody reviewed here."
        )
    return problems


def pinned_ref(consumer_manifest: Path, package_name: str) -> "str | None":
    """The git ``ref`` *consumer_manifest* (a ``pubspec.yaml``) pins *package_name* to, read from its dependency maps
    (``dependencies``, ``dev_dependencies``, ``dependency_overrides``, the override winning). A path or hosted
    dependency has no ref. Quoted refs are read like any other YAML scalar."""
    import yaml

    data = yaml.safe_load(consumer_manifest.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(data, dict):
        return None
    ref = None
    for section in _DEP_SECTIONS:
        deps = data.get(section)
        spec = deps.get(package_name) if isinstance(deps, dict) else None
        git = spec.get("git") if isinstance(spec, dict) else None
        if isinstance(git, dict) and git.get("ref") is not None:
            ref = str(git["ref"])
        elif spec is not None and section == "dependency_overrides":
            ref = None  # an override to a path or hosted source replaces the git pin
    return ref


def find_stale_pin(consumer_manifest: Path, package_repo: Path, package_name: str) -> "str | None":
    """Advisory: report how many releases behind ``consumer_manifest``'s pin is.

    Returns None when the pin is current, the manifest has no git pin for the package, or the package repo has no
    tags.
    """
    if not consumer_manifest.is_file():
        return None
    pinned = pinned_ref(consumer_manifest, package_name)
    if pinned is None:
        return None
    tags = semver_tags(package_repo)
    if not tags or pinned not in tags:
        return None
    behind = len(tags) - 1 - tags.index(pinned)
    if behind <= 0:
        return None
    return (
        f"{consumer_manifest.name} pins {package_name} {pinned}, which is {behind} release(s) "
        f"behind {tags[-1]}. Every fix in between is one this product has not taken."
    )


def assert_version_is_tagged(repo: Path, manifest: str = "pubspec.yaml") -> None:
    """Fail when the declared version has no tag, the tag is not reachable from HEAD, or git cannot tell."""
    import pytest

    problems = find_version_tag_problems(repo, manifest)
    if problems:
        pytest.fail(f"{len(problems)} version/tag problem(s):\n  " + "\n  ".join(problems))
