"""tool_versions: the one place the shared ruff version lives, and the pins that must agree with it."""

from __future__ import annotations

import re
from pathlib import Path

from py_ci_shared import tool_versions
from py_ci_shared._toml_compat import tomllib
from py_ci_shared.pinned_tool_versions import TOOLS, pinned_version

REPO = Path(__file__).resolve().parent.parent


def test_ruff_version_is_an_exact_release() -> None:
    """A range or a prerelease cannot be pinned with `==` by every consumer."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", tool_versions.RUFF_VERSION)


def test_every_checked_tool_uses_the_shared_constant() -> None:
    assert TOOLS["ruff"][2] == tool_versions.RUFF_VERSION


def test_this_repos_pin_is_read_through_toml_not_text() -> None:
    """The dev pin the repo's own tests run must be the shared version, read from the parsed TOML."""
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [r for group in data["project"].get("optional-dependencies", {}).values() for r in group]
    assert any(pinned_version(f'x = ["{r}"]', "ruff") == tool_versions.RUFF_VERSION for r in requirements)
