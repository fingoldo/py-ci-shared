"""A warm pytest worker for :mod:`py_ci_shared.mutation_teeth`. Not a public API.

Measured on the repo this was written for: a mutant costs 15.8s as a fresh subprocess, of which
pytest reports **0.48s** as actual test execution. Collection alone -- ``--co``, running nothing --
is 15.2s. So roughly 97% of every mutant is the interpreter starting and the package under test
importing, identical every single time. At 40 mutants that is eleven minutes to evaluate nineteen
seconds of assertions.

Keeping the interpreter warm is therefore the whole game. Two obvious ways to do it are both
silently WRONG, and both were proved so by execution rather than argued:

* **Re-running ``pytest.main()`` without touching ``sys.modules``** leaves the ORIGINAL module
  object imported. The mutated file on disk is never read, the tests pass, and the harness reports
  a false SURVIVOR -- it accuses a test of being toothless against a mutation that was never
  applied.
* **``importlib.reload(target)``** is worse: a mutation to one function made an unrelated test fail,
  because the already-imported test module still held a reference to the pre-reload class. A false
  KILL, which is the direction that hides a real defect.

What works is purging every repo-local module -- production AND test -- atomically between runs,
while leaving the third-party stack (pytest, its plugins, the standard library) warm. Measured at
1.6-2.7s marginal per mutant, a 7-10x improvement, with no identity split because nothing local
survives the purge to hold a stale reference.

A residual risk remains and is handled by the caller rather than denied here: a third-party
registry (a plugin's cache, a metaclass registry inside an installed package) can retain state
across runs in a way a purge does not reach. :mod:`mutation_teeth` therefore re-verifies every
reported SURVIVOR in a cold subprocess before believing it -- survivors are rare, so the cost is
small, and a false survivor is the outcome that wastes a human's time.

Between runs the worker also restores the working directory, ``os.environ``, ``sys.path`` and
``sys.argv`` to what they were at startup: a test that changes one of them and does not undo it
would otherwise fail every later run for a reason unrelated to the mutant, a false kill.

Protocol: one JSON object per line on stdin, one per line on the protocol channel, which is the
process's original stdout. At startup the worker moves that channel to a private file descriptor
and points fd 1 at the null device, so nothing a test writes -- ``print``, ``os.write(1, ...)``, a
child process -- can land between replies. Every request carries an ``id`` that its reply echoes,
and the parent ignores any line that is not the reply to the request it sent.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import IO, Any, Optional

#: `__file__` -> is it under the sandbox root. `Path.resolve()` is a syscall per entry, and the
#: purge walked all ~3900 of `sys.modules` on EVERY mutant to find the ~84 local ones; measured
#: at 2.3-6.3s per mutant, roughly 29% of a sweep. A module's `__file__` does not change while
#: the worker lives, so the answer is a pure function of that string. Verified equivalent by
#: comparing the resulting name sets: symmetric difference empty.
_IS_LOCAL: dict[str, bool] = {}

#: The same memo for namespace packages, keyed on the root and their `__path__` entries.
_IS_LOCAL_NAMESPACE: dict[tuple[str, ...], bool] = {}

#: Exceptions that mean the mutant made the code CRASH rather than produce a wrong answer. A crash
#: mutant dies against any test that reaches the line, so it is killed for free and tells nobody
#: anything about test quality. Kept here, not in the parent, because the worker classifies its own
#: first failure and the parent classifies a cold run's output with the same list.
_CRASH_EXCEPTIONS = (
    "ValueError",
    "LookupError",
    "IndexError",
    "KeyError",
    "TypeError",
    "AttributeError",
    "ZeroDivisionError",
    "OverflowError",
    "RecursionError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    # The classes a MUTATION produces, as opposed to the ones application code raises. An emptied
    # string becomes an invalid name or an empty pattern; a deleted call leaves a name unbound; a
    # changed constant walks off the end of a sequence or opens a path that is not there.
    "NameError",
    "UnboundLocalError",
    "ImportError",
    "ModuleNotFoundError",
    "StopIteration",
    "ArithmeticError",
    "OSError",
    "FileNotFoundError",
    "NotADirectoryError",
    "IsADirectoryError",
    "PermissionError",
    "re.error",
    "json.JSONDecodeError",
)


def is_crash_message(payload: str) -> Optional[bool]:
    """``True`` for an exception from the code, ``False`` for a failed assertion, ``None`` for neither."""
    payload = payload.strip()
    if payload.startswith("assert") or payload.startswith("AssertionError"):
        return False
    if any(payload.startswith(name) for name in _CRASH_EXCEPTIONS):
        return True
    return None


class _FirstFailure:
    """Records the file of the first failing test, and whether that failure was a crash.

    A pytest plugin rather than output parsing: the worker discards pytest's report deliberately,
    and re-enabling it to scrape a filename would put the report back on the protocol channel that
    already broke this worker once.
    """

    def __init__(self) -> None:
        self.path: str | None = None
        self.crash: bool = False

    def pytest_runtest_logreport(self, report) -> None:
        if self.path is None and report.failed:
            self.path = str(report.nodeid).split("::", 1)[0]
            crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
            self.crash = bool(is_crash_message(str(getattr(crash, "message", "") or "")))

    def pytest_collectreport(self, report) -> None:
        # A collection error kills every test in the file at once and never reaches logreport.
        if self.path is None and report.failed:
            self.path = str(report.nodeid).split("::", 1)[0]
            self.crash = True


def _is_local_namespace(paths: list[Any], root: Path) -> bool:
    entries = tuple(str(p) for p in paths)
    key = (str(root), *entries)
    verdict = _IS_LOCAL_NAMESPACE.get(key)
    if verdict is None:
        try:
            verdict = any(Path(p).resolve().is_relative_to(root) for p in entries)
        except (OSError, ValueError):
            verdict = False
        _IS_LOCAL_NAMESPACE[key] = verdict
    return verdict


def _purge_local_modules(root: Path) -> int:
    """Drop every imported module whose file lives under *root*.

    Atomically, in one pass: purging production modules but leaving test modules imported is
    exactly the half-measure that produced the false-kill above, because the surviving test module
    holds references into the dropped one.
    """
    doomed = []
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if not file:
            # A namespace package has no `__file__` but does have a `__path__`, and leaving it
            # imported keeps the next mutant's submodule import resolving through a stale parent.
            try:
                paths = list(getattr(module, "__path__", None) or [])
            except TypeError:
                continue
            if paths and _is_local_namespace(paths, root):
                doomed.append(name)
            continue
        verdict = _IS_LOCAL.get(file)
        if verdict is None:
            try:
                verdict = Path(file).resolve().is_relative_to(root)
            except (OSError, ValueError):
                verdict = False
            _IS_LOCAL[file] = verdict
        if verdict:
            doomed.append(name)
    for name in doomed:
        sys.modules.pop(name, None)
    return len(doomed)


class _ProcessState:
    """The process-global state a test can change and leave changed: cwd, environment, sys.path, sys.argv."""

    def __init__(self) -> None:
        self.cwd = os.getcwd()
        self.environ = dict(os.environ)
        self.path = list(sys.path)
        self.argv = list(sys.argv)

    def restore(self) -> None:
        try:
            os.chdir(self.cwd)
        except OSError:
            pass
        if dict(os.environ) != self.environ:
            os.environ.clear()
            os.environ.update(self.environ)
        sys.path[:] = self.path
        sys.argv[:] = self.argv
        importlib.invalidate_caches()


def _module_origin(target: str, names: list[str]) -> dict[str, Any]:
    """Where the modules the mutated file could be imported as were ACTUALLY loaded from."""
    wanted = os.path.normcase(str(Path(target).resolve()))
    sandbox = False
    elsewhere: list[str] = []
    for name in names:
        module = sys.modules.get(name)
        file = getattr(module, "__file__", None)
        if not file:
            continue
        try:
            here = os.path.normcase(str(Path(file).resolve()))
        except (OSError, ValueError):
            continue
        if here == wanted:
            sandbox = True
        else:
            elsewhere.append(f"{name} from {file}")
    return {"sandbox": sandbox, "elsewhere": elsewhere}


def _claim_protocol_channel() -> IO[str]:
    """Move the protocol off fd 1 and send fd 1 to the null device.

    ``contextlib.redirect_stdout`` only swaps ``sys.stdout``; a test calling ``os.write(1, ...)``
    or a subprocess inheriting fd 1 still wrote straight into the reply stream.
    """
    sys.stdout.flush()
    channel = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
    finally:
        os.close(devnull)
    return os.fdopen(channel, "w", encoding="utf-8", newline="\n", buffering=1)


def _reply(channel: IO[str], payload: dict[str, Any]) -> None:
    channel.write(json.dumps(payload) + "\n")
    channel.flush()


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root))
    channel = _claim_protocol_channel()
    import pytest  # imported once, deliberately: this is the cost the worker exists to amortise

    state = _ProcessState()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            _reply(channel, {"error": f"bad request: {exc}"})
            continue
        if not isinstance(request, dict):
            _reply(channel, {"error": "bad request: not an object"})
            continue
        request_id = request.get("id")
        if request.get("cmd") == "stop":
            return 0
        try:
            state.restore()
            _t0 = time.perf_counter()
            purged = _purge_local_modules(root)
            _t_purge = time.perf_counter() - _t0
            # pytest.main writes its report to OUR stdout. fd 1 is the null device now, and this
            # redirect keeps the Python-level streams off it too. The report is discarded rather
            # than captured: the exit code is the whole answer here, and the caller re-runs any
            # survivor in a cold process where it does keep the output.
            buffer = io.StringIO()
            spy = _FirstFailure()
            _t1 = time.perf_counter()
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                code = int(pytest.main(list(request["args"]), plugins=[spy]))
            _t_pytest = time.perf_counter() - _t1
            # Which FILE killed this mutant, so the caller can try it first next time. With `-x` the
            # run stops at the first failure, so the earlier the killer sits the less is collected
            # and executed -- measured at 40.85 of 67 tests run on average, against 11.62 when the
            # previous killer leads. Advisory only: a reply without it is still a valid reply.
            # Timings travel in the reply the worker already sends. A parent-side profiler
            # cannot attribute any of this -- the work happens in another process -- and pytest's
            # own `--durations` is inert here because stdout is redirected into a discarded buffer.
            reply: dict[str, Any] = {
                "id": request_id,
                "rc": code,
                "purged": purged,
                "failed": spy.path,
                "crash": spy.crash,
                "t_purge": round(_t_purge, 4),
                "t_pytest": round(_t_pytest, 4),
                "t_total": round(time.perf_counter() - _t0, 4),
            }
            if request.get("target"):
                reply["origin"] = _module_origin(str(request["target"]), [str(n) for n in request.get("names", [])])
            _reply(channel, reply)
        except BaseException as exc:
            _reply(channel, {"id": request_id, "error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
