"""Process-global interpreter state a test can leave changed: the standard streams, the working directory, ``sys.path``
and the warning filters. The checks :mod:`py_ci_shared.resource_leak_guard` runs next to its process/thread/socket ones.

mlframe 1256fff4d: numba's ``ColorShell.__exit__`` called colorama ``deinit()``, which put back the ``sys.stdout`` colorama
had saved at import, and every later doctest printed to the console instead of the captured stream ("Expected: True,
Got nothing"). ``standard_stream_restore`` reads only the repo's own code, so a third-party ``deinit()`` is invisible to
it; this check compares the live objects. Each check snapshots BEFORE setup (so a function-scoped fixture must restore
what it changes too), compares at teardown, and the guard restores the snapshot after reporting.

* ``streams``: identity of ``sys.stdout``, ``sys.stderr`` and ``sys.stdin``. pytest's capture objects are the same
  object across a test's phases, so a capture in progress is not a change. A library that wraps a stream on its first
  import (colorama ``init()`` wraps ``sys.stdout`` in a ``StreamWrapper`` around it) is not reported when the new
  stream wraps the old one AND a library module was imported during the test; a REPLACED stream always is.
* ``cwd``: ``os.getcwd()``.
* ``sys_path``: ``sys.path``. Entries ADDED while a library was first imported (a plugin registering its directory) are
  that import's one-off configuration and are neither reported nor removed; a removed or reordered entry always is.
* ``warnings``: ``warnings.filters``. Filters added while a library was first imported (``filterwarnings("ignore", ...)``
  at module level is common) are not reported; any other change is. pytest's own ``catch_warnings`` around each test
  is outside this window, so its filters are not a change.

Allowlist entries (``leak_guard_allow``): ``stream:<glob>`` (``stdout``/``stderr``/``stdin``), ``cwd``,
``sys_path:<glob>`` (the entry) and ``warnings``.
"""

from __future__ import annotations

import fnmatch
import os
import sys
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["STATE_CHECKS", "StateSnapshot", "restore_state", "state_leaks", "take_state"]

STATE_CHECKS = ("streams", "cwd", "sys_path", "warnings")
_STREAMS = ("stdout", "stderr", "stdin")
#: Attributes through which a stream wrapper holds the stream it wraps (colorama's StreamWrapper, io wrappers, codecs).
_WRAPPED_ATTRS = ("_StreamWrapper__wrapped", "wrapped", "_wrapped", "stream", "_stream", "__wrapped__")
_RUNNER_MODULES = frozenset({"pytest", "_pytest", "pluggy", "py", "iniconfig", "packaging", "exceptiongroup", "tomli", "colorama"})


@dataclass
class StateSnapshot:
    """Interpreter state at one instant; ``None`` means the check is off."""

    streams: Optional[dict[str, Any]] = None
    cwd: Optional[str] = None
    sys_path: Optional[list[str]] = None
    warning_filters: Optional[list[Any]] = None
    modules: frozenset[str] = frozenset()


def take_state(checks: Iterable[str] = STATE_CHECKS) -> StateSnapshot:
    wanted = set(checks)
    snap = StateSnapshot(modules=frozenset(sys.modules))
    if "streams" in wanted:
        snap.streams = {name: getattr(sys, name, None) for name in _STREAMS}
    if "cwd" in wanted:
        snap.cwd = _cwd()
    if "sys_path" in wanted:
        snap.sys_path = list(sys.path)
    if "warnings" in wanted:
        snap.warning_filters = list(warnings.filters)
    return snap


def _cwd() -> str:
    try:
        return os.getcwd()
    except OSError:  # the directory was removed under us: that is a change too
        return "<deleted>"


def _permitted(kind: str, value: str, allow: Iterable[str]) -> bool:
    """``kind`` alone, or ``kind:<glob>`` matching *value*, is in *allow*."""
    for entry in allow:
        head, _, pattern = entry.partition(":")
        if head == kind and (not pattern or fnmatch.fnmatchcase(value, pattern)):
            return True
    return False


def _imported_library(before: StateSnapshot) -> bool:
    """A module outside the runner and the standard library was imported since *before*: a first import can configure."""
    stdlib: frozenset[str] = getattr(sys, "stdlib_module_names", frozenset())
    for module in set(sys.modules) - before.modules:
        top = module.split(".", 1)[0]
        if top in _RUNNER_MODULES or top.startswith("_pytest") or top.startswith("test_") or top == "conftest" or top in stdlib:
            continue
        return True
    return False


def _wraps(new: Any, old: Any) -> bool:
    """*new* is a wrapper whose wrapped stream (one or two hops) is *old*."""
    frontier = [new]
    for _ in range(2):
        nxt = []
        for obj in frontier:
            for attr in _WRAPPED_ATTRS:
                inner = getattr(obj, attr, None) if obj is not None else None
                if inner is old:
                    return True
                if inner is not None:
                    nxt.append(inner)
        frontier = nxt
    return False


def _stream_leaks(before: StateSnapshot, allow: list[str], imported: bool) -> list[str]:
    if before.streams is None:
        return []
    out = []
    for name, old in before.streams.items():
        new = getattr(sys, name, None)
        if new is old or _permitted("stream", name, allow) or (imported and _wraps(new, old)):
            continue
        out.append(f"sys.{name} was replaced ({type(old).__name__} -> {type(new).__name__}) and not restored")
    return out


def _path_leaks(before: StateSnapshot, allow: list[str], imported: bool) -> list[str]:
    if before.sys_path is None:
        return []
    now = list(sys.path)
    old = before.sys_path
    added = [p for p in now if p not in old]
    removed = [p for p in old if p not in now]
    out = [f"sys.path entry {p!r} was removed" for p in removed if not _permitted("sys_path", p, allow)]
    if not imported:
        out += [f"sys.path entry {p!r} was added" for p in added if not _permitted("sys_path", p, allow)]
    kept = [p for p in now if p in old]
    if not removed and kept != [p for p in old if p in now] and not _permitted("sys_path", "", allow):
        out.append("sys.path was reordered")
    return out


def _warning_leaks(before: StateSnapshot, allow: list[str], imported: bool) -> list[str]:
    if before.warning_filters is None or _permitted("warnings", "", allow):
        return []
    now = list(warnings.filters)
    old = before.warning_filters
    if now == old:
        return []
    if imported and len(now) > len(old) and now[len(now) - len(old) :] == old:
        return []  # only prepended while a library was first imported
    return [f"warnings.filters changed ({len(old)} -> {len(now)} filters; first now {now[0][:2] if now else None}) and not restored"]


def state_leaks(before: StateSnapshot, *, allow: Iterable[str] = ()) -> list[str]:
    """What changed since *before*, as one line per leak."""
    allow_list = list(allow)
    imported = _imported_library(before)
    out = _stream_leaks(before, allow_list, imported)
    if before.cwd is not None and _cwd() != before.cwd and not _permitted("cwd", "", allow_list):
        out.append(f"working directory changed to {_cwd()!r} (was {before.cwd!r})")
    return out + _path_leaks(before, allow_list, imported) + _warning_leaks(before, allow_list, imported)


def restore_state(before: StateSnapshot) -> None:
    """Put back what :func:`state_leaks` compares, keeping the import-time changes it does not report."""
    imported = _imported_library(before)
    if before.streams is not None:
        for name, old in before.streams.items():
            new = getattr(sys, name, None)
            if new is not old and not (imported and _wraps(new, old)):
                setattr(sys, name, old)
    if before.cwd is not None and before.cwd != "<deleted>" and _cwd() != before.cwd:
        try:
            os.chdir(before.cwd)
        except OSError:
            pass  # nosec B110 - the directory is gone; the leak was already reported
    if before.sys_path is not None:
        extra = [p for p in sys.path if p not in before.sys_path] if imported else []
        sys.path[:] = before.sys_path + extra
    if before.warning_filters is not None and list(warnings.filters) != before.warning_filters:
        now = list(warnings.filters)
        old = before.warning_filters
        prepended_on_import = imported and len(now) > len(old) and now[len(now) - len(old) :] == old
        if not prepended_on_import:
            warnings.filters[:] = old  # type: ignore[index]  # typeshed declares a Sequence; it is the module's list
            mutated = getattr(warnings, "_filters_mutated", None)  # invalidates the per-module "once" registries
            if callable(mutated):
                mutated()
