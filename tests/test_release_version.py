"""One version: pyproject, ``__version__``, the reusable workflows' default ref and the release tags agree."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

import py_ci_shared
from py_ci_shared._toml_compat import tomllib
from py_ci_shared.version_consistency import assert_versions_agree
from py_ci_shared.version_tag_currency import semver_tags

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
VERSION = PYPROJECT["project"]["version"]


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.lstrip("v").split("."))


def test_pyproject_and_the_module_agree():
    assert_versions_agree(REPO, files=["src/py_ci_shared/__init__.py"], modules=["py_ci_shared"])
    assert py_ci_shared.__version__ == VERSION


def test_the_version_is_plain_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), f"{VERSION!r} is not X.Y.Z; release.yml only accepts vX.Y.Z tags"


def _tags() -> list[str]:
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO, capture_output=True, text=True, timeout=30)
    if inside.returncode != 0:
        pytest.skip("not a git checkout (an sdist or wheel): no tags to compare with")
    return semver_tags(REPO)


def test_the_version_is_at_least_the_newest_release_tag():
    """Between releases the declared version is the next one; it may never fall behind a tag that already shipped."""
    tags = _tags()
    assert tags, "no vX.Y.Z tags visible; CI must check out with fetch-depth: 0 so tags are fetched"
    newest = tags[-1]
    fix = "Bump version in pyproject.toml and src/py_ci_shared/__init__.py to the next release."
    assert _key(VERSION) >= _key(newest), f"pyproject declares {VERSION} but {newest} already shipped. {fix}"


def test_a_tagged_version_points_at_an_ancestor_of_head():
    tags = _tags()
    if f"v{VERSION}" not in tags:
        return  # not released yet: nothing to check
    code = subprocess.run(["git", "merge-base", "--is-ancestor", f"v{VERSION}", "HEAD"], cwd=REPO, capture_output=True, timeout=30).returncode
    assert code == 0, f"v{VERSION} exists but is not an ancestor of HEAD; bump the version for the next release"


@pytest.mark.parametrize("name", ["black-filtered.yml", "ruff-blocking.yml", "lint-advisory.yml"])
def test_each_reusable_workflow_defaults_to_its_own_release(name: str):
    """A caller pinned to a tag gets that tag's configs: the default ref is the version the file ships in."""
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    inputs = data[True]["workflow_call"]["inputs"]  # PyYAML reads the bare `on` key as True
    assert (
        inputs["py-ci-shared-ref"]["default"] == f"v{VERSION}"
    ), f"{name}: py-ci-shared-ref defaults to {inputs['py-ci-shared-ref']['default']!r}; set it to v{VERSION} with the version bump"


def test_the_install_pyutilz_action_defaults_to_the_pinned_commit():
    pin = next(d for d in PYPROJECT["project"]["optional-dependencies"]["dev"] if d.startswith("pyutilz"))
    sha = pin.rsplit("@", 1)[1]
    action = yaml.safe_load((REPO / ".github" / "actions" / "install-pyutilz" / "action.yml").read_text(encoding="utf-8"))
    assert action["inputs"]["pyutilz-ref"]["default"] == sha, "install-pyutilz's default ref and the [dev] pyutilz pin must be the same commit"
