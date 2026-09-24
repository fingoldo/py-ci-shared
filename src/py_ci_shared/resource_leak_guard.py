"""pytest plugin: a test that leaks a child process, a thread, a socket, ``logging.disable`` or an env var errors at teardown.

A leaked background process is invisible to a green suite: a scheduler test that did not stub its job spawned a real
rollup script against the live database on every run, fifty accumulated, and nothing failed. Raising inside the leaking
code does not help either, because schedulers catch every exception by design; the check has to run at TEARDOWN, outside
the code under test. The same holds for process-global state a test leaves behind: ``logging.disable(CRITICAL)`` or an
``os.environ`` edit silently changes every later test.

Enable with ``-p py_ci_shared.resource_leak_guard`` (or ``pytest_plugins`` in the root conftest). Per test it compares:

* ``processes``: live descendants of this process (psutil; without it, ``multiprocessing.active_children()``), after a
  short grace for ones already exiting;
* ``threads``: alive NON-daemon threads, after a short join grace (a daemon thread cannot block interpreter exit);
* ``sockets``: this process's inet connections (psutil only);
  for these three the baseline is taken AFTER setup, so what a module- or session-scoped fixture starts is not the
  test's leak;
* ``logging``: ``logging.root.manager.disable``, and ``env``: ``os.environ``, both from BEFORE setup, so a
  function-scoped fixture must restore them too. A variable ADDED while the test imported new modules is a library
  configuring itself on first import (``KMP_DUPLICATE_LIB_OK``, ``HF_HOME``), happens once per process, and is neither
  reported nor removed; a changed or removed variable always is. Both are restored after the report (``leak_guard_restore = false`` to
  keep the leaked value), so one leak does not cascade into every later test.

Allowlist: ini ``leak_guard_allow`` (one entry per line) or ``@pytest.mark.leak_guard_allow(...)`` with entries
``thread:<glob>`` (thread name), ``process:<glob>`` (process name or command line), ``socket:<glob>`` (``laddr->raddr``),
``env:<glob>`` (variable name) or ``logging``. ``PYTEST_*`` and ``COV_CORE_*`` variables are always allowed.
``@pytest.mark.no_leak_guard`` skips a test; ini ``leak_guard_checks`` narrows the checks.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Optional

import pytest

__all__ = ["ALL_CHECKS", "Snapshot", "leaks_between", "take_snapshot"]

ALL_CHECKS = ("processes", "threads", "sockets", "logging", "env")
_ALWAYS_ALLOWED_ENV = ("PYTEST_*", "COV_CORE_*")
#: Modules pytest itself imports lazily during a test's setup and call; they configure nothing.
_RUNNER_MODULES = frozenset({"pytest", "_pytest", "pluggy", "py", "iniconfig", "packaging", "exceptiongroup", "tomli", "colorama"})
_THREAD_GRACE_S = 0.5
_PROCESS_GRACE_S = 1.0
_BEFORE = pytest.StashKey["Snapshot"]()
_AFTER_SETUP = pytest.StashKey["Snapshot"]()


@dataclass
class Snapshot:
    """What a test could leak, at one instant. ``None`` means "not measured" (check off, or psutil missing)."""

    processes: Optional[dict[int, str]] = None
    threads: Optional[dict[int, tuple[str, threading.Thread]]] = None
    sockets: Optional[set[str]] = None
    logging_disable: Optional[int] = None
    env: Optional[dict[str, str]] = None
    modules: Optional[frozenset[str]] = None


def _psutil() -> Any:
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def _processes() -> dict[int, str]:
    psutil = _psutil()
    if psutil is None:
        import multiprocessing

        return {p.pid or 0: p.name for p in multiprocessing.active_children()}
    described = ((child.pid, _describe(psutil, child)) for child in psutil.Process().children(recursive=True))
    return {pid: text for pid, text in described if text is not None}


def _describe(psutil: Any, child: Any) -> Optional[str]:
    """The command line (or name) of a child; None when it exited between listing and reading, which is not a leak."""
    try:
        return " ".join(child.cmdline()) or str(child.name())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _sockets() -> Optional[set[str]]:
    psutil = _psutil()
    if psutil is None:
        return None
    proc = psutil.Process()
    getter = getattr(proc, "net_connections", None) or proc.connections
    out = set()
    for c in getter(kind="inet"):
        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
        out.add(f"{laddr}->{raddr}")
    return out


def take_snapshot(checks: Iterable[str] = ALL_CHECKS) -> Snapshot:
    wanted = set(checks)
    snap = Snapshot()
    if "processes" in wanted:
        snap.processes = _processes()
    if "threads" in wanted:
        snap.threads = {t.ident or 0: (t.name, t) for t in threading.enumerate() if not t.daemon}
    if "sockets" in wanted:
        snap.sockets = _sockets()
    if "logging" in wanted:
        snap.logging_disable = logging.root.manager.disable
    if "env" in wanted:
        snap.env = dict(os.environ)
        snap.modules = frozenset(sys.modules)
    return snap


def _allowed(kind: str, value: str, allow: Iterable[str]) -> bool:
    for entry in allow:
        k, _, pattern = entry.partition(":")
        if k == kind and (pattern == "" or fnmatch.fnmatchcase(value, pattern)):
            return True
    return False


def _thread_leaks(before: Snapshot, allow: list[str]) -> list[str]:
    if before.threads is None:
        return []
    deadline = time.monotonic() + _THREAD_GRACE_S
    out = []
    for t in threading.enumerate():
        if t.daemon or t is threading.current_thread() or (t.ident or 0) in before.threads:
            continue
        t.join(max(0.0, deadline - time.monotonic()))
        if t.is_alive() and not _allowed("thread", t.name, allow):
            out.append(f"thread {t.name!r} (non-daemon) is still running")
    return out


def _process_leaks(before: Snapshot, allow: list[str]) -> list[str]:
    if before.processes is None:
        return []
    deadline = time.monotonic() + _PROCESS_GRACE_S
    new = {pid: name for pid, name in _processes().items() if pid not in before.processes}
    while new and time.monotonic() < deadline:
        time.sleep(0.05)
        alive = _processes()
        new = {pid: name for pid, name in new.items() if pid in alive}
    return [f"child process {pid} ({name}) is still running" for pid, name in sorted(new.items()) if not _allowed("process", name, allow)]


def _socket_leaks(before: Snapshot, allow: list[str]) -> list[str]:
    if before.sockets is None:
        return []
    after = _sockets() or set()
    return [f"socket {s} is still open" for s in sorted(after - before.sockets) if not _allowed("socket", s, allow)]


def _import_time_keys(before: Snapshot) -> set[str]:
    """Variables ADDED while new modules were imported: a library configuring itself on first import (``KMP_*`` from
    an OpenMP runtime, ``HF_HOME`` from a lazily imported model loader) sets them once per process, not per test."""
    if before.env is None or before.modules is None or not any(_is_library(m) for m in set(sys.modules) - before.modules):
        return set()
    return {k for k in os.environ if k not in before.env}


def _is_library(module: str) -> bool:
    """A module outside the test runner and the standard library: importing one can configure a process."""
    top = module.split(".", 1)[0]
    if top in _RUNNER_MODULES or top.startswith("_pytest") or top.startswith("test_") or top == "conftest":
        return False
    return top not in getattr(sys, "stdlib_module_names", frozenset())


def _env_leaks(before: Snapshot, allow: list[str]) -> list[str]:
    if before.env is None:
        return []
    after = os.environ
    allow_env = [*allow, *(f"env:{p}" for p in _ALWAYS_ALLOWED_ENV)]
    import_time = _import_time_keys(before)
    out = []
    for key in sorted(set(before.env) | set(after)):
        if key in import_time or _allowed("env", key, allow_env):
            continue
        if key not in before.env:
            out.append(f"env var {key} was added")
        elif key not in after:
            out.append(f"env var {key} was removed")
        elif after[key] != before.env[key]:
            out.append(f"env var {key} was changed")
    return out


def leaks_between(before: Snapshot, global_before: Optional[Snapshot] = None, *, allow: Iterable[str] = ()) -> list[str]:
    """What is leaked NOW relative to *before* (processes, threads, sockets) and *global_before* (logging, env).

    *global_before* defaults to *before*.
    """
    allow_list = list(allow)
    glob = global_before or before
    out = _process_leaks(before, allow_list) + _thread_leaks(before, allow_list) + _socket_leaks(before, allow_list)
    if glob.logging_disable is not None and logging.root.manager.disable != glob.logging_disable and not _allowed("logging", "", allow_list):
        out.append(f"logging.disable({logging.root.manager.disable}) left on (was {glob.logging_disable})")
    return out + _env_leaks(glob, allow_list)


def _restore(before: Snapshot) -> None:
    if before.logging_disable is not None:
        logging.disable(before.logging_disable)
    if before.env is not None:
        keep = _import_time_keys(before)
        for key in [k for k in os.environ if k not in before.env and k not in keep]:
            del os.environ[key]
        os.environ.update(before.env)


# ------------------------------------------------------------------------------------------------------------ pytest hooks


def pytest_addoption(parser: Any) -> None:
    parser.addini("leak_guard_allow", "resource_leak_guard allowlist: kind:glob per line (thread/process/socket/env/logging)", type="linelist", default=[])
    parser.addini("leak_guard_checks", "resource_leak_guard checks to run (default: all)", type="args", default=list(ALL_CHECKS))
    parser.addini("leak_guard_restore", "restore leaked env vars and logging.disable after reporting", type="bool", default=True)


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "leak_guard_allow(*entries): allow these leaks (kind:glob) for this test")
    config.addinivalue_line("markers", "no_leak_guard: do not check this test for leaked resources")
    _psutil()  # imported now, not during the first test, where it would read as that test's import-time configuration
    unknown = sorted(set(config.getini("leak_guard_checks")) - set(ALL_CHECKS))
    if unknown:
        raise pytest.UsageError(f"leak_guard_checks: unknown check(s) {unknown}; known: {list(ALL_CHECKS)}")


def _checks(item: Any) -> list[str]:
    return list(item.config.getini("leak_guard_checks"))


def _allow(item: Any) -> list[str]:
    allow = list(item.config.getini("leak_guard_allow"))
    for mark in item.iter_markers("leak_guard_allow"):
        allow.extend(str(a) for a in mark.args)
    return allow


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: Any) -> None:
    if item.get_closest_marker("no_leak_guard") is None:
        item.stash[_BEFORE] = take_snapshot([c for c in _checks(item) if c in ("logging", "env")])


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: Any) -> None:
    """Every fixture is set up by now and the body has not started: this is the after-setup baseline."""
    if _BEFORE in pyfuncitem.stash:
        pyfuncitem.stash[_AFTER_SETUP] = take_snapshot([c for c in _checks(pyfuncitem) if c in ("processes", "threads", "sockets")])


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: Any, nextitem: Any) -> None:
    """Runs after pytest's own teardown implementation, so the test's function-scoped fixtures are finalized."""
    if _BEFORE not in item.stash:
        return
    before = item.stash[_BEFORE]
    after_setup = item.stash.get(_AFTER_SETUP, Snapshot())
    leaks = leaks_between(after_setup, before, allow=_allow(item))
    if leaks and item.config.getini("leak_guard_restore"):
        _restore(before)
    if leaks:
        raise pytest.fail.Exception(
            f"{item.nodeid} leaked resources past its teardown:\n    " + "\n    ".join(leaks) + "\n"
            "Stop/join/close them in the test or a fixture finalizer, use monkeypatch for env vars, or allow them "
            "with @pytest.mark.leak_guard_allow('<kind>:<glob>') and a reason.",
            pytrace=False,
        )
