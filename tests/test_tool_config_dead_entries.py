"""tool_config_dead_entries: a pyproject ignore, omit or override that names nothing that exists (deptry ignores never expire)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError
from py_ci_shared.tool_config_dead_entries import (
    DEFAULT_EXEMPTIONS,
    RULE,
    assert_tool_config_dead_entries,
    find_tool_config_dead_entries,
    glob_matches,
)

NL = chr(10)


def _repo(tmp_path: Path, pyproject: list, files: tuple[str, ...] = ("src/pkg/__init__.py", "legacy_keep/x.py")) -> Path:
    (tmp_path / "pyproject.toml").write_text(NL.join(pyproject) + NL, encoding="utf-8")
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1" + NL, encoding="utf-8")
    return tmp_path


def _find(root: Path, **kwargs):
    return find_tool_config_dead_entries(root, use_git=False, **kwargs)


def test_a_per_file_ignore_matching_no_file_is_reported(tmp_path):
    root = _repo(
        tmp_path,
        ["[tool.ruff.lint.per-file-ignores]", '"legacy/**" = ["T201"]'],
    )
    found = _find(root)
    assert [(f.path, f.rule) for f in found] == [("pyproject.toml", RULE)]
    assert "legacy/**" in found[0].message


def test_a_per_file_ignore_that_matches_a_file_is_clean(tmp_path):
    root = _repo(
        tmp_path,
        ["[tool.ruff.lint.per-file-ignores]", '"legacy_keep/**" = ["T201"]'],
    )
    assert _find(root) == []


def test_a_deptry_ignore_naming_no_declared_dependency_is_reported(tmp_path):
    root = _repo(
        tmp_path,
        [
            "[project]",
            'dependencies = ["numpy>=1.20"]',
            "[tool.deptry.per_rule_ignores]",
            'DEP002 = ["gone-package"]',
        ],
    )
    found = _find(root)
    assert [f.rule for f in found] == [RULE]
    assert "gone-package" in found[0].message


def test_a_deptry_ignore_for_a_declared_dependency_is_clean_with_normalised_names(tmp_path):
    root = _repo(
        tmp_path,
        [
            "[project]",
            'dependencies = ["Some_Package[extra]>=2; python_version >= \\"3.9\\""]',
            "[tool.deptry.per_rule_ignores]",
            'DEP002 = ["some-package"]',
        ],
    )
    assert _find(root) == []


def test_the_cupy_entry_is_an_explicit_reasoned_exemption(tmp_path):
    root = _repo(
        tmp_path,
        [
            "[project]",
            'dependencies = ["cupy-cuda12x"]',
            "[tool.deptry.per_rule_ignores]",
            'DEP002 = ["cupy"]',
        ],
    )
    assert "cupy" in DEFAULT_EXEMPTIONS and len(DEFAULT_EXEMPTIONS["cupy"]) >= 20
    assert _find(root) == []
    assert [f.message for f in _find(root, exemptions={})] != []


def test_double_star_matches_zero_or_more_directories():
    assert glob_matches("legacy/**", "legacy/a.py")
    assert glob_matches("src/**/benchmarks/**", "src/benchmarks/x.py")
    assert glob_matches("src/**/benchmarks/**", "src/pkg/benchmarks/x.py")
    assert glob_matches("**/conftest.py", "conftest.py")
    assert not glob_matches("legacy/*", "legacy/sub/a.py")


def test_boilerplate_patterns_are_not_checked(tmp_path):
    root = _repo(
        tmp_path,
        ["[tool.coverage.run]", 'omit = ["*/__pycache__/*", ".venv/*", "*/site-packages/*"]'],
    )
    assert _find(root) == []


def test_a_coverage_omit_matching_no_file_is_reported(tmp_path):
    root = _repo(tmp_path, ["[tool.coverage.run]", 'omit = ["legacy/*"]'])
    assert [f.rule for f in _find(root)] == [RULE]


def test_an_empty_tree_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        assert_tool_config_dead_entries(tmp_path, use_git=False, min_files=1)
