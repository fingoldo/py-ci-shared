"""Unit tests for the git-dependency-pin check (S-04 audit finding).

Real scratch pyproject.toml files, same no-mocking convention as this
package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.git_dependency_pins import assert_all_git_dependencies_pinned, find_unpinned_git_dependencies

_FULL_SHA = "1fd408df105f2a7d22d36a239d94a39ef888305a"


def _write_pyproject(tmp_path: Path, deps_line: str) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(
        "[project]\n"
        "name = \"scratch\"\n"
        "dependencies = [\n"
        '    "numpy>=1.0",\n'
        f"    {deps_line}\n"
        "]\n",
        encoding="utf-8",
    )
    return p


class TestFindUnpinnedGitDependencies:
    def test_full_sha_pin_is_clean(self, tmp_path):
        p = _write_pyproject(tmp_path, f'"mypkg @ git+https://github.com/org/mypkg.git@{_FULL_SHA}",')
        assert find_unpinned_git_dependencies(p) == []

    def test_branch_name_is_unpinned(self, tmp_path):
        p = _write_pyproject(tmp_path, '"mypkg @ git+https://github.com/org/mypkg.git@master",')
        assert find_unpinned_git_dependencies(p) == ["master"]

    def test_tag_is_unpinned(self, tmp_path):
        p = _write_pyproject(tmp_path, '"mypkg @ git+https://github.com/org/mypkg.git@v1.2.0",')
        assert find_unpinned_git_dependencies(p) == ["v1.2.0"]

    def test_short_sha_is_unpinned(self, tmp_path):
        p = _write_pyproject(tmp_path, '"mypkg @ git+https://github.com/org/mypkg.git@1fd408d",')
        assert find_unpinned_git_dependencies(p) == ["1fd408d"]

    def test_no_git_dependencies_is_clean(self, tmp_path):
        p = _write_pyproject(tmp_path, '"requests>=2.0",')
        assert find_unpinned_git_dependencies(p) == []

    def test_embedded_auth_at_sign_not_mistaken_for_ref_separator(self, tmp_path):
        """A URL like git+https://user@host/path@REF has two '@'s -- only
        the LAST one (before the ref) should be treated as the separator."""
        p = _write_pyproject(tmp_path, f'"mypkg @ git+https://token@github.com/org/mypkg.git@{_FULL_SHA}",')
        assert find_unpinned_git_dependencies(p) == []

    def test_multiple_git_dependencies_each_checked(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(
            "[project]\n"
            "dependencies = [\n"
            f'    "good @ git+https://github.com/org/good.git@{_FULL_SHA}",\n'
            '    "bad @ git+https://github.com/org/bad.git@main",\n'
            "]\n",
            encoding="utf-8",
        )
        assert find_unpinned_git_dependencies(p) == ["main"]


class TestAssertAllGitDependenciesPinned:
    def test_passes_silently_when_all_pinned(self, tmp_path):
        p = _write_pyproject(tmp_path, f'"mypkg @ git+https://github.com/org/mypkg.git@{_FULL_SHA}",')
        assert_all_git_dependencies_pinned(p)  # must not raise

    def test_fails_with_actionable_message_when_unpinned(self, tmp_path):
        p = _write_pyproject(tmp_path, '"mypkg @ git+https://github.com/org/mypkg.git@master",')
        with pytest.raises(pytest.fail.Exception, match="not pinned to a full commit SHA"):
            assert_all_git_dependencies_pinned(p)


def test_this_repos_own_pyproject_toml_is_clean():
    """py-ci-shared's own pyproject.toml, dogfooding the check."""
    own_pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert find_unpinned_git_dependencies(own_pyproject) == []
