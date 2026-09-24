"""Shared fixtures for the py-ci-shared suite.

Every ``git`` a test runs, directly or through a gate, sees an isolated configuration: the developer's global and
system git config (``commit.gpgsign``, hooks, ``init.templateDir``, a default branch name) is hidden for the whole
session, and signing is off. Without this, the tests that commit in a tmp repo pass in CI and fail, or prompt for a
passphrase, on a machine with signing configured.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Callable

import pytest

from py_ci_shared.randomly_seed_guard import bound_randomly_reseeders

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

GIT_ISOLATION = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_COUNT": "5",
    "GIT_CONFIG_KEY_0": "commit.gpgsign",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "tag.gpgsign",
    "GIT_CONFIG_VALUE_1": "false",
    "GIT_CONFIG_KEY_2": "init.defaultBranch",
    "GIT_CONFIG_VALUE_2": "master",
    "GIT_CONFIG_KEY_3": "user.name",
    "GIT_CONFIG_VALUE_3": "py-ci-shared tests",
    "GIT_CONFIG_KEY_4": "user.email",
    "GIT_CONFIG_VALUE_4": "tests@py-ci-shared.invalid",
}


@pytest.fixture(autouse=True, scope="session")
def _isolated_git_config() -> Iterator[None]:
    """Hide the developer's global/system git config from every git subprocess of the session."""
    saved = {k: os.environ.get(k) for k in GIT_ISOLATION}
    os.environ.update(GIT_ISOLATION)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_git(repo: Path, *args: str) -> str:
    """Run git in *repo* under the isolated config and return stdout; raises on a non-zero exit."""
    env = {**os.environ, **GIT_ISOLATION}
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True, timeout=60).stdout


@pytest.fixture
def write() -> Callable[..., Path]:
    """``write(path, text)``: create parents and write UTF-8 with LF endings; returns the path."""

    def _write(path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    return _write


@pytest.fixture
def git_repo(tmp_path: Path) -> Callable[..., Path]:
    """``git_repo(files={rel: text}, tags=(...))``: an initialised repo with one commit holding *files*.

    Each call makes a new repo under ``tmp_path``; tags, when given, point at that commit.
    """
    counter = [0]

    def _make(files: "dict[str, str] | None" = None, tags: tuple[str, ...] = (), name: str = "") -> Path:
        counter[0] += 1
        repo = tmp_path / (name or f"repo{counter[0]}")
        repo.mkdir(parents=True)
        run_git(repo, "init", "-q")
        for rel, text in (files or {"README.md": "x\n"}).items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "initial")
        for tag in tags:
            run_git(repo, "tag", tag)
        return repo

    return _make


def pytest_configure(config: pytest.Config) -> None:
    """pytest-randomly passes thinc's reseeder a seed past 2**32; bound it (see randomly_seed_guard).

    The package's own pytest11 plugin does this too, but this suite must not depend on the package being installed
    with its entry point registered.
    """
    bound_randomly_reseeders()
