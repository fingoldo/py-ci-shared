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

Deliberately dependency-free (``git`` via subprocess, regex over the manifest).

Usage::

    from py_ci_shared.version_tag_currency import assert_version_is_tagged

    def test_declared_version_has_a_tag():
        assert_version_is_tagged(REPO, manifest="pubspec.yaml")
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_VERSION_RES = (
    re.compile(r"^version:\s*['\"]?([0-9]+\.[0-9]+\.[0-9]+)", re.MULTILINE),  # pubspec / yaml
    re.compile(r"^version\s*=\s*['\"]([0-9]+\.[0-9]+\.[0-9]+)", re.MULTILINE),  # pyproject
)
_SEMVER_TAG_RE = re.compile(r"^v?([0-9]+)\.([0-9]+)\.([0-9]+)$")


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def declared_version(repo: Path, manifest: str) -> "str | None":
    """Return the version string declared in ``manifest``, or None."""
    path = repo / manifest
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in _VERSION_RES:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def _sort_key(tag: str) -> tuple:
    m = _SEMVER_TAG_RE.match(tag)
    return tuple(int(g) for g in m.groups()) if m else (0, 0, 0)


def semver_tags(repo: Path) -> list[str]:
    """Every semver-shaped tag in the repository, oldest first."""
    tags = [t for t in _git(repo, "tag", "--list").splitlines() if _SEMVER_TAG_RE.match(t)]
    return sorted(tags, key=_sort_key)


def find_version_tag_problems(repo: Path, manifest: str) -> list[str]:
    """Return one problem string per untagged declared version or unreachable tag."""
    version = declared_version(repo, manifest)
    if version is None:
        return [f"{manifest}: no version declared - nothing to check."]
    tags = semver_tags(repo)
    matching = [t for t in tags if t.lstrip("v") == version]
    problems: list[str] = []
    if not matching:
        newest = tags[-1] if tags else "(none)"
        problems.append(
            f"{manifest} declares {version} and no matching tag exists (newest tag: {newest}). "
            f"Every consumer pinning a tag is still on the previous release while this repo "
            f"believes it shipped."
        )
    else:
        tag = matching[-1]
        reachable = _git(repo, "merge-base", "--is-ancestor", tag, "HEAD")
        code = subprocess.run(
            ["git", "merge-base", "--is-ancestor", tag, "HEAD"],
            cwd=repo,
            capture_output=True,
        ).returncode
        if code != 0:
            problems.append(
                f"tag {tag} is not an ancestor of HEAD - it points at code that is not on this "
                f"branch, so a consumer pinning it gets something nobody reviewed here."
            )
        del reachable
    return problems


def find_stale_pin(consumer_manifest: Path, package_repo: Path, package_name: str) -> "str | None":
    """Advisory: report how many releases behind ``consumer_manifest``'s pin is.

    Returns None when the pin is current, the manifest has no pin, or the package repo has no tags.
    """
    if not consumer_manifest.is_file():
        return None
    text = consumer_manifest.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"{re.escape(package_name)}:.*?ref:\s*(v?[0-9.]+)", text, re.DOTALL)
    if not m:
        return None
    pinned = m.group(1)
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
    """Fail when the declared version has no tag, or the tag is not reachable from HEAD."""
    import pytest

    problems = find_version_tag_problems(repo, manifest)
    if problems:
        pytest.fail(f"{len(problems)} version/tag problem(s):\n  " + "\n  ".join(problems))
