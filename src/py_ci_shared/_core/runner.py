"""Run one enabled gate the same way from the CLI and from the pytest plugin: resolve, call, time, classify."""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import gate_cache
from .config import ConfigError, GateRun, RepoConfig, resolve_kwargs
from .refresh import ENV_VAR, GROW_ENV_VAR

logger = logging.getLogger(__name__)

__all__ = ["GateResult", "budget_verdict", "resolve_gate", "run_gate"]

PASSED, FAILED, SKIPPED, ERROR = "passed", "failed", "skipped", "error"


@dataclass(frozen=True)
class GateResult:
    """What one gate run produced."""

    name: str
    status: str
    message: str
    seconds: float
    budget_s: float

    @property
    def over_budget(self) -> bool:
        return self.seconds > self.budget_s


def resolve_gate(run: GateRun) -> tuple[Callable[..., Any], float]:
    """The entry function *run* names and its budget (table ``budget_s``, else the registry's)."""
    from .. import registry

    try:
        spec = registry.by_name(run.module)
    except KeyError as exc:
        raise ConfigError(str(exc.args[0])) from None
    entry = run.entry or spec.default_entry
    if entry is None:
        raise ConfigError(f"{spec.name} is a {spec.kind} module with no assert_* entry; it cannot be enabled as a gate")
    if spec.entries and entry not in spec.entries:
        raise ConfigError(f"{spec.name} has no entry {entry!r}; choose one of {list(spec.entries)}")
    module = importlib.import_module(spec.module)
    return getattr(module, entry), float(run.budget_s if run.budget_s is not None else spec.budget_s)


@contextmanager
def _refresh_env(refresh: bool, grow: bool = False) -> Iterator[None]:
    wanted = {ENV_VAR: "all"} if refresh else {}
    if refresh and grow:
        wanted[GROW_ENV_VAR] = "1"
    old = {k: os.environ.get(k) for k in wanted}
    os.environ.update(wanted)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _outcome_types() -> tuple[tuple[type[BaseException], ...], tuple[type[BaseException], ...]]:
    try:
        from _pytest.outcomes import Failed, Skipped
    except ImportError as exc:  # pytest absent: gates that need it fail with ImportError, reported as ERROR
        logger.debug("pytest outcome types unavailable, using AssertionError only: %s", type(exc).__name__)
        return (AssertionError,), ()
    return (AssertionError, Failed), (Skipped,)


def _cache_key(config: RepoConfig, run: GateRun, kwargs: dict, budget: Optional[float]) -> Optional[str]:
    """The key of this run's PASSED result, or None when the cache is off or this run must not be cached (see ``gate_cache``)."""
    if not gate_cache.enabled():
        return None
    return gate_cache.key_for(config.repo_root, run.module, run.entry, kwargs, budget, gate_cache.tree_digest(config.repo_root))


def run_gate(config: RepoConfig, run: GateRun, *, refresh: bool = False, grow: bool = False) -> GateResult:
    """Call *run*'s entry with its resolved kwargs from the repo root, never raising for a gate's own failure.

    ``refresh`` sets ``PY_CI_SHARED_REFRESH=all`` for the call and passes ``refresh=True`` when the entry takes it.
    ``grow`` (with ``refresh``) also sets ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1`` and passes ``grow=True`` when taken, so
    the refresh may add entries; without it a refresh only removes the ones that no longer fire.
    A config problem is an ERROR result, a gate's assertion a FAILED one; the returned message is the gate's text.
    """
    failed_types, skipped_types = _outcome_types()
    start = time.perf_counter()
    budget = 0.0
    try:
        func, budget = resolve_gate(run)
        kwargs = resolve_kwargs(func, dict(run.kwargs), config.repo_root)
        if refresh and "refresh" in inspect.signature(func).parameters:
            kwargs["refresh"] = True
        if refresh and grow and "grow" in inspect.signature(func).parameters:
            kwargs["grow"] = True
        cache_key = None if refresh else _cache_key(config, run, kwargs, budget)
        if cache_key is not None and gate_cache.lookup(config.repo_root, cache_key):
            logger.debug("gate %s passed on this tree before, not re-run", run.name)
            return GateResult(run.name, PASSED, "cached: nothing in the repository changed since it last passed", time.perf_counter() - start, budget)
        cwd = os.getcwd()
        os.chdir(config.repo_root)
        try:
            with _refresh_env(refresh, grow):
                func(**kwargs)
        finally:
            os.chdir(cwd)
        if cache_key is not None:
            gate_cache.record(config.repo_root, cache_key)
    except ConfigError as exc:
        logger.warning("gate configuration invalid, reporting ERROR: %s", type(exc).__name__)
        return GateResult(run.name, ERROR, f"configuration: {exc}", time.perf_counter() - start, budget)
    except failed_types as exc:
        logger.debug("gate failed, reporting FAILED: %s", type(exc).__name__)
        return GateResult(run.name, FAILED, str(exc), time.perf_counter() - start, budget)
    except skipped_types as exc:
        logger.debug("gate skipped, reporting SKIPPED: %s", type(exc).__name__)
        return GateResult(run.name, SKIPPED, str(exc), time.perf_counter() - start, budget)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # a crashing gate is reported as ERROR with its type, never swallowed
        logger.warning("gate raised, reporting ERROR: %s", type(exc).__name__)
        return GateResult(run.name, ERROR, f"{type(exc).__name__}: {exc}", time.perf_counter() - start, budget)
    return GateResult(run.name, PASSED, "", time.perf_counter() - start, budget)


def budget_verdict(result: GateResult, mode: str) -> Optional[str]:
    """A message when *result* ran past its budget and *mode* is not ``off``; the caller warns or fails on it.

    Never for an ERROR: a gate that failed to resolve has no budget yet (0), and "over its 0s budget" next to a
    configuration error sent people to the wrong fix (audit 2026-10-03 K-11)."""
    if mode == "off" or result.status == ERROR or not result.over_budget:
        return None
    return (
        f"{result.name} took {result.seconds:.1f}s, over its {result.budget_s:.0f}s budget. Narrow its inputs, raise "
        f'budget_s in [tool.py_ci_shared.gates.{result.name}], or set budget = "off".'
    )
