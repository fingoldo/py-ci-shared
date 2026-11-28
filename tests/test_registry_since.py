"""``since`` in registry.toml is the first LIVE release that contains the module (the README catalogue renders it).

v1.22.0, v1.22.2 and v1.23.12 to v1.23.16 are dead tags (the first four of the 1.23 series were tagged on commits that ``release_guard prepare`` had not
prepared, v1.23.16 failed this test over them; none was published), so a module they carried says the next live release. Read from the tags themselves, so it needs a checkout with tag history and skips without one."""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path

import pytest

from py_ci_shared._core.git import git_env
from py_ci_shared.registry import load_gates
from py_ci_shared.version_tag_currency import semver_tags

REPO = Path(__file__).resolve().parents[1]
DEAD_TAGS = frozenset({"v1.22.0", "v1.22.2", "v1.23.12", "v1.23.13", "v1.23.14", "v1.23.15", "v1.23.16"})
FIRST_CHECKED = (1, 22, 3)  # older releases predate the layout this reads


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.lstrip("v").split("."))


@functools.cache
def _files(tag: str) -> frozenset[str]:
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", tag, "src/py_ci_shared"], cwd=REPO, capture_output=True, text=True, timeout=60, env=git_env())
    return frozenset(out.stdout.split()) if out.returncode == 0 else frozenset()


def _has(tag: str, name: str) -> bool:
    files = _files(tag)
    return f"src/py_ci_shared/{name}.py" in files or f"src/py_ci_shared/{name}/__init__.py" in files


def _live_tags() -> list[str]:
    try:
        tags = [t for t in semver_tags(REPO) if t not in DEAD_TAGS]
    except (OSError, subprocess.SubprocessError):
        tags = []
    if not tags or not _files(tags[-1]):
        pytest.skip("no tag history in this checkout")
    return tags


def test_each_since_is_the_first_live_tag_holding_the_module():
    tags = _live_tags()
    wrong = []
    for spec in load_gates():
        key = _key(spec.since)
        tag = f"v{spec.since}"
        if key < FIRST_CHECKED or tag not in tags:
            continue  # unreleased (the next release) or older than the checked range
        before = tags[tags.index(tag) - 1]
        if _has(before, spec.name) or not _has(tag, spec.name):
            wrong.append(f"{spec.name}: since {spec.since}, in {before}={_has(before, spec.name)}, in {tag}={_has(tag, spec.name)}")
    assert not wrong, "registry.toml `since` disagrees with the tags:\n" + "\n".join(wrong)
