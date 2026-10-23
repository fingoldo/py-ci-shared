"""Shared fixtures for the py-ci-shared suite.

Every ``git`` a test runs, directly or through a gate, sees an isolated configuration: the developer's global and
system git config (``commit.gpgsign``, hooks, ``init.templateDir``, a default branch name) is hidden for the whole
session, and signing is off. Without this, the tests that commit in a tmp repo pass in CI and fail, or prompt for a
passphrase, on a machine with signing configured.
"""

from __future__ import annotations

import dataclasses
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


@pytest.fixture(autouse=True)
def _no_inherited_coverage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that starts pytest in a subprocess must not inherit the coverage run that is measuring this suite.

    pytest-cov publishes COV_CORE_* and COVERAGE_* in the environment for its own child processes. A nested pytest
    started with them, but not with the parent's measurement mode, writes statement-mode data next to the parent's
    branch-mode data, and the parent then fails at combine time with INTERNALERROR (see _mutation_runner._pytest_env).
    """
    for key in list(os.environ):
        if key.startswith(("COV_CORE_", "COVERAGE_")):
            monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def _fresh_tree_digests(request: pytest.FixtureRequest) -> Iterator[None]:
    """A test that rewrites files must not leave a gate of a later test a digest of the old tree.

    Only the self-gate module shares one tree digest across its gates: it runs the gates over this checkout and writes nothing.
    Every other test starts and ends with the digests forgotten.
    """
    from py_ci_shared._core import gate_cache

    shares = request.node.path.name == "test_self_gates.py"
    if not shares:
        gate_cache.forget_tree_digests()
    yield
    if not shares:
        gate_cache.forget_tree_digests()


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


@pytest.fixture(scope="session")
def pg_cluster():
    """One throwaway Postgres server for the whole session.

    A start costs 24-32 s on the machine the suite is measured on, and each module that needed a server started its own. Tests that need an
    isolated schema take a :func:`fresh_database` on this server, so they still cannot see each other's objects.
    """
    from py_ci_shared.embedded_postgres import embedded_postgres, find_pg_bin

    bin_dir = find_pg_bin()
    if bin_dir is None:
        pytest.skip("no Postgres binaries here (PG_BIN / pgserver / PATH / `python -m py_ci_shared.embedded_postgres fetch`)")
    with embedded_postgres(bin_dir) as dsn:
        yield dsn


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> None:
    """Under xdist: run the tests marked ``xdist_group`` on one worker, and pick the worker count from worker_budget.

    Both have to be in place before xdist starts. xdist decides the mode in its own ``pytest_cmdline_main``, and it starts each worker from the
    controller's ``invocation_params.args`` (the original command line), so a mode set only on the controller's option object reaches neither the
    scheduler's choice of group nor the workers' test ids. The ``--dist loadgroup`` is therefore added to those arguments, unless the command line
    chose a distribution already. ``invocation_params`` is pytest's own record of the invocation and is replaced, not mutated (it is a frozen dataclass).
    """
    if hasattr(config, "workerinput"):
        return
    try:
        import xdist  # noqa: F401
    except ImportError:
        return
    args = tuple(config.invocation_params.args)
    if not any(a == "--dist" or a.startswith("--dist=") for a in args):
        config.invocation_params = dataclasses.replace(config.invocation_params, args=(*args, "--dist", "loadgroup"))
        config.option.dist = "loadgroup"
    if getattr(config.option, "numprocesses", None) is None:
        from py_ci_shared.worker_budget import available_workers

        # 2 GiB per worker: this suite loads the package and its test doubles, not the numeric libraries, so it needs less than the package default.
        workers = available_workers(ram_per_worker_bytes=2 * 2**30)
        # One worker means a serial run: leave xdist out, so pytest-cov never meets the xdist controller (see self-ci.yml).
        if workers > 1:
            config.option.numprocesses = workers


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """pytest-randomly passes thinc's reseeder a seed past 2**32; bound it (see randomly_seed_guard).

    The package's own pytest11 plugin does this too, but this suite must not depend on the package being installed
    with its entry point registered.
    """
    bound_randomly_reseeders(config)
