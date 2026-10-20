"""Regression tests for the 2026-10-20 subprocess and git audit (F1, F2, F4, F7, F11, F12)."""

from __future__ import annotations

import os
import stat

import pytest

from py_ci_shared import _mutation_runner, black_filtered_apply, install_safe_hook, setup_env
from py_ci_shared._core import git as core_git
from py_ci_shared._core.git import GitError


def test_the_current_directory_is_not_searched_for_executables():
    """F1: on Windows a bare "git" would otherwise resolve to a git.exe planted in the checkout being gated."""
    assert os.environ["NoDefaultCurrentDirectoryInExePath"] == "1"


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "0", "-3"])
def test_a_non_finite_or_non_positive_git_timeout_is_a_git_error(monkeypatch, raw):
    """F7: inf reaches subprocess as OverflowError and nan as an instant timeout; both must stay inside the GitError contract."""
    monkeypatch.setenv(core_git.TIMEOUT_ENV_VAR, raw)

    with pytest.raises(GitError):
        core_git._timeout("default")


def test_a_finite_git_timeout_is_accepted(monkeypatch):
    monkeypatch.setenv(core_git.TIMEOUT_ENV_VAR, "12.5")

    assert core_git._timeout("default") == 12.5


def test_the_nested_pytest_environment_drops_the_hooks_repository_variables(monkeypatch):
    """F2: a mutation run started from a hook must not let a test's git act on the repository that is committing."""
    monkeypatch.setenv("GIT_DIR", "/the/committing/repo/.git")
    monkeypatch.setenv("GIT_INDEX_FILE", "/the/committing/repo/.git/index")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--cov-fail-under=100")

    env = _mutation_runner._pytest_env()

    assert "GIT_DIR" not in env and "GIT_INDEX_FILE" not in env
    assert env["PYTEST_ADDOPTS"] == "", "the consumer's coverage options must still be cleared"


def test_a_windows_black_command_keeps_its_backslashes():
    """F4: POSIX shlex treats a backslash as an escape and turns C:\\tools\\black.exe into C:toolsblack.exe."""
    command = "C:" + chr(92) + "tools" + chr(92) + "black.exe --fast"

    assert black_filtered_apply._split_command(command, windows=True) == ["C:" + chr(92) + "tools" + chr(92) + "black.exe", "--fast"]


def test_a_posix_black_command_still_honours_quotes():
    assert black_filtered_apply._split_command('"/opt/my tools/black" -q', windows=False) == ["/opt/my tools/black", "-q"]


@pytest.mark.parametrize("value", ["/home/me/a" + chr(10) + "b", "/home/me/a" + chr(13) + "b", "x" * 1024])
def test_a_value_no_target_can_hold_is_refused(value):
    """F11: a line break writes a second variable into environment.d; setx truncates at 1024 characters."""
    with pytest.raises(ValueError):
        setup_env._reject_unwritable(value)


def test_an_ordinary_path_is_accepted():
    setup_env._reject_unwritable("/home/me/py-ci-shared")


def test_the_patched_hook_is_written_atomically_and_keeps_its_mode(tmp_path):
    """F12: the patch goes to a sibling file and replaces the hook, so no truncated hook or temp file is left."""
    hook = tmp_path / "pre-commit"
    hook.write_bytes(install_safe_hook._TARGET.encode("utf-8") + bytes([10]))
    hook.chmod(0o755)

    assert install_safe_hook._patch_one(hook) == 0

    assert install_safe_hook._REPLACEMENT.encode("utf-8") in hook.read_bytes()
    assert [p.name for p in tmp_path.iterdir()] == ["pre-commit"], "the staging file must not be left behind"
    if os.name != "nt":
        assert stat.S_IMODE(hook.stat().st_mode) == 0o755
