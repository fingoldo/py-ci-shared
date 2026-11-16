"""49-T6: an unpinned git dependency is reported by name, ref and pyproject.toml line; the legacy keys stay the same."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared import git_dependency_pins as gdp

PYPROJECT = """\
[project]
name = "demo"
version = "0.1"
dependencies = [
    "seedextra @ git+https://github.com/o/seedextra.git@release-branch",
    "seednoref @ git+https://github.com/o/seednoref.git",
    "seedshort @ git+https://github.com/o/seedshort.git@abc1234",
]

[tool.uv.sources]
seeduv = { git = "https://github.com/o/seeduv.git", branch = "dev-branch" }
"""

LEGACY = ["release-branch", "<no ref>", "abc1234", "seeduv: branch=dev-branch"]


def _write(tmp_path: Path, text: str = PYPROJECT) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_legacy_return_values_are_unchanged(tmp_path: Path) -> None:
    assert gdp.find_unpinned_git_dependencies(_write(tmp_path)) == LEGACY


def test_legacy_allow_list_keys_still_exempt_a_url(tmp_path: Path) -> None:
    allowed = gdp.find_unpinned_git_dependencies(_write(tmp_path), allow_unpinned_url_prefixes=["git+https://github.com/o/seedextra"])
    assert "release-branch" not in allowed
    assert "<no ref>" in allowed


def test_failure_names_each_dependency_with_its_line(tmp_path: Path) -> None:
    path = _write(tmp_path)
    with pytest.raises(pytest.fail.Exception) as info:
        gdp.assert_all_git_dependencies_pinned(path)
    message = str(info.value)
    assert "seedextra: release-branch (pyproject.toml:5)" in message
    assert "seednoref: <no ref> (pyproject.toml:6)" in message
    assert "seedshort: abc1234 (pyproject.toml:7)" in message
    assert "seeduv: branch=dev-branch (pyproject.toml:11)" in message
    assert "<no ref>\n" not in message.replace("seednoref: <no ref>", "")
