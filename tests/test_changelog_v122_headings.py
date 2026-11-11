"""Every v1.22 release tag has a CHANGELOG heading (audit 43-F10 / 43-F2): the round report and the tracker must not disagree again."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from py_ci_shared._core.git import git_env

REPO = Path(__file__).resolve().parents[1]


def _tags() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "tag", "-l", "v1.22*"], capture_output=True, text=True, check=False, env=git_env()).stdout
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def missing_headings(changelog: str, tags: list[str]) -> list[str]:
    headings = set(re.findall(r"^## (\S+)\s*$", changelog, re.MULTILINE))
    return [tag for tag in tags if tag[1:] not in headings]


def test_every_v122_tag_has_a_changelog_heading() -> None:
    tags = _tags()
    if not tags:
        pytest.skip("no v1.22 tags in this checkout (a shallow clone); the check needs them")
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert missing_headings(changelog, tags) == []


def test_a_missing_heading_is_named() -> None:
    """Negative control: a changelog without the 1.22.6 heading names that tag."""
    assert missing_headings("## 1.22.5\n\nx\n", ["v1.22.5", "v1.22.6"]) == ["v1.22.6"]
