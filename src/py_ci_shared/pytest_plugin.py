"""pytest plugin (``pytest11`` entry point ``py_ci_shared``): the gates of ``[tool.py_ci_shared]`` as test items.

Installed with the package and inert until the repo's ``pyproject.toml`` has a ``[tool.py_ci_shared]`` table (see
:mod:`py_ci_shared._core.config`). Then a full run (``pytest`` with no path arguments, or ``--py-ci-gates=on``) gets one
item per enabled gate, ``pyproject.toml::<gate>``, that calls the gate's entry function from the repo root.

Options:

- ``--py-ci-refresh[=<gate>[,<gate>]|all]``: rewrite baselines instead of comparing. Sets ``PY_CI_SHARED_REFRESH``
  for the whole session, so xdist workers, subprocesses and a consumer's own hand-written gate tests all see it.
- ``--py-ci-gates=auto|on|off``: whether to add the gate items (``auto``: only when pytest was given no paths).

``resource_leak_guard = true`` in the table also loads :mod:`py_ci_shared.resource_leak_guard`, the opt-in plugin that
fails a test leaking a process, thread, socket, env var or ``logging.disable`` (the same as ``-p py_ci_shared.resource_leak_guard``).

Every gate is timed. Past its ``budget_s`` (registry default, or ``budget_s`` in its table) the item warns, or fails
when the table sets ``budget = "fail"``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import pytest

from ._core.config import ConfigError, GateRun, RepoConfig, load_config
from ._core.refresh import ENV_VAR, GENERIC_OPTION, register_refresh_options
from ._core.runner import ERROR, FAILED, SKIPPED, budget_verdict, run_gate
from .randomly_seed_guard import bound_randomly_reseeders

_CONFIG_KEY = pytest.StashKey[Optional[RepoConfig]]()
LEAK_GUARD_PLUGIN = "py_ci_shared.resource_leak_guard"


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("py-ci-shared")
    register_refresh_options(group)
    group.addoption(
        "--py-ci-gates",
        choices=("auto", "on", "off"),
        default="auto",
        help="add one test item per gate enabled in [tool.py_ci_shared] (auto: only when pytest is given no paths)",
    )


def _refresh_tokens(config: Any) -> list[str]:
    try:
        values = config.getoption(GENERIC_OPTION) or []
    except ValueError:
        return []
    return [token for value in values for token in str(value).split(",") if token.strip()]


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "py_ci_shared: a gate item generated from [tool.py_ci_shared]")
    bound_randomly_reseeders()  # pytest-randomly hands thinc an out-of-range seed; see randomly_seed_guard
    tokens = _refresh_tokens(config)
    if tokens:
        before = os.environ.get(ENV_VAR)
        existing = [t for t in (before or "").split(",") if t]
        os.environ[ENV_VAR] = ",".join(dict.fromkeys(existing + tokens))

        def _restore() -> None:
            if before is None:
                os.environ.pop(ENV_VAR, None)
            else:
                os.environ[ENV_VAR] = before

        config.add_cleanup(_restore)
    try:
        repo_config = load_config(Path(str(config.rootpath)))
    except ConfigError as exc:
        raise pytest.UsageError(f"py-ci-shared: {exc}") from exc
    config.stash[_CONFIG_KEY] = repo_config
    if repo_config is not None and repo_config.resource_leak_guard:
        # Registered late, its historic pytest_addoption and pytest_configure are replayed; unknown ini keys are only
        # validated after collection. import_plugin is a no-op when -p already loaded it and honours -p no:<name>.
        config.pluginmanager.import_plugin(LEAK_GUARD_PLUGIN)


def _wanted(config: Any, repo_config: Optional[RepoConfig]) -> bool:
    if repo_config is None or not repo_config.gates:
        return False
    mode = config.getoption("--py-ci-gates")
    if mode != "auto":
        return bool(mode == "on")
    source = getattr(config, "args_source", None)
    if source is not None:  # pytest >= 7.2 says whether the positional arguments came from the command line
        return bool(source != pytest.Config.ArgsSource.ARGS)
    return [str(a) for a in config.args] == [str(config.invocation_params.dir)] or list(config.args) == list(config.getini("testpaths"))


class GateFile(pytest.File):
    """The repo's ``pyproject.toml`` as the parent of the gate items."""

    def collect(self) -> list[Any]:
        repo_config = self.config.stash[_CONFIG_KEY]
        assert repo_config is not None  # only built when a table exists
        return [GateItem.from_parent(self, name=run.name, run=run, repo_config=repo_config) for run in repo_config.gates]


class GateFindingsError(Exception):
    """A gate reported findings; its message is the gate's own text."""


class GateItem(pytest.Item):
    """One enabled gate."""

    def __init__(self, *, run: GateRun, repo_config: RepoConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.run = run
        self.repo_config = repo_config
        self.add_marker(pytest.mark.py_ci_shared)

    def runtest(self) -> None:
        refresh = _gate_refresh(self.config, self.run)
        result = run_gate(self.repo_config, self.run, refresh=refresh)
        over = budget_verdict(result, self.repo_config.budget)
        if result.status == SKIPPED:
            pytest.skip(result.message)
        if result.status in (FAILED, ERROR):
            prefix = "configuration error: " if result.status == ERROR else ""
            raise GateFindingsError(prefix + result.message + (f"\n{over}" if over else ""))
        if over:
            if self.repo_config.budget == "fail":
                raise GateFindingsError(over)
            self.warn(pytest.PytestWarning(over))

    def repr_failure(self, excinfo: Any, style: Any = None) -> Any:
        if isinstance(excinfo.value, GateFindingsError):
            return f"{self.run.name}: {excinfo.value}"
        return super().repr_failure(excinfo)

    def reportinfo(self) -> tuple[Any, Optional[int], str]:
        return self.path, 0, f"py-ci-shared gate {self.run.name} ({self.run.module})"


def _gate_refresh(config: Any, run: GateRun) -> bool:
    tokens = set(_refresh_tokens(config)) | {t for t in os.environ.get(ENV_VAR, "").split(",") if t}
    return bool(tokens & {"all", run.name, run.module, run.name.replace("_", "-"), run.module.replace("_", "-")})


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(session: Any, config: Any, items: list[Any]) -> None:
    repo_config = config.stash.get(_CONFIG_KEY, None)
    if not _wanted(config, repo_config):
        return
    assert repo_config is not None
    parent = GateFile.from_parent(session, path=repo_config.repo_root / "pyproject.toml")
    items.extend(parent.collect())
