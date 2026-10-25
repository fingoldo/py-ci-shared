"""Running pytest for :mod:`py_ci_shared.mutation_teeth`: cold subprocesses, the warm worker, and verdicts.

Split out of ``mutation_teeth`` so each part stays readable; everything here is re-exported from there,
and ``mutation_teeth`` calls these through its own namespace so a test can still patch them there.
"""

from __future__ import annotations

import functools
import importlib.util
import itertools
import json
from collections import deque
import logging
import os
import queue
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Sequence
from pathlib import Path
from collections.abc import Iterator
from typing import Any, Optional, Union

from ._core.git import git_env
from ._mutation_model import MutationHarnessError
from ._mutation_worker import is_crash_message

logger = logging.getLogger(__name__)


def _pytest_env() -> dict[str, str]:
    """Environment for a nested pytest run.

    ``PYTEST_ADDOPTS`` is cleared because a consuming repo's ``--cov-fail-under`` would fail every
    mutant run for the wrong reason and make every mutant look killed -- the same exit-code
    overloading this module was rewritten to abolish.
    """
    # git_env also drops GIT_DIR, GIT_INDEX_FILE and GIT_WORK_TREE: started from a hook, a test that spawns git would otherwise act on
    # the repository that is committing, and a mutant's verdict would depend on how the run was started.
    # The coverage keys belong to the parent's pytest-cov, which is measuring this run. Inherited, pytest-cov's
    # COV_CORE_DATAFILE and COVERAGE_FILE point the nested run at the parent's data file, and a nested run with
    # another measurement mode (branch or statement) writes into it: coverage then refuses to combine the two
    # ("Can't combine statement coverage data with branch data") and the whole session ends INTERNALERROR. Dropping
    # them leaves the nested run to measure nothing of the parent's.
    env = git_env(base={k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS" and not k.startswith(("COV_CORE_", "COVERAGE_"))})
    # The child inherits PYTHONPATH, never the parent's in-process `sys.path`. Without this, a
    # parent running from a checkout spawns a worker that loads the INSTALLED package instead --
    # measured: parent on a worktree, child on `C:\...\py-ci-shared\src`. Every worker-side change
    # is then untested by any run of the checkout, and silently so, because a reply missing the new
    # fields is still a valid reply. That is this module's own failure mode, applied to itself.
    inherited = env.get("PYTHONPATH", "")
    entries = [p for p in sys.path if p and p not in inherited.split(os.pathsep)]
    env["PYTHONPATH"] = os.pathsep.join([*entries, inherited]) if inherited else os.pathsep.join(entries)
    # Defence in depth, not a fix for a reproduced bug: bytecode caching keys on the source's size
    # and mtime-to-the-second, and an operator swap changes neither. Writing no bytecode at all
    # removes the question for the cost of a recompile that the warm worker already pays.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_ADDOPTS"] = ""
    return env


#: Characters of a child's output kept as text. The callers print ``[-2000:]`` of it; nothing else reads it.
_TAIL_CHARS = 256 * 1024
#: Longest piece read from a child's pipe in one call. A longer line arrives as several pieces.
_PIECE_CHARS = 1 << 20
#: Most FAILED/ERROR names kept. The verdict needs only whether there are any, and reports show a few.
_MAX_FAILED_NAMES = 10_000
#: Lines a warm worker's pump may hold unread. A reply is the newest line, so the oldest are dropped first.
_QUEUE_LINES = 64


class OutputScan:
    """The markers the classifiers read from a child's output, collected while the output streams.

    Fed the whole stream in order, this yields what the classifiers used to read from the full text:
    the first decisive ``E `` line (crash or assertion), the target of the first FAILED/ERROR line,
    the last summary line, and every FAILED/ERROR name. Only the names grow with the output, and
    they are capped at :data:`_MAX_FAILED_NAMES`.

    Pieces need not end at line breaks: an unterminated line is held until its end arrives, up to
    :data:`_PIECE_CHARS` characters. Past that cap a line is judged by its first :data:`_PIECE_CHARS`
    only, so a marker beyond the cap in a longer line is not seen. Call :meth:`finish` once the stream
    has ended, which judges a final unterminated line.
    """

    def __init__(self) -> None:
        self._partial: str | None = None
        self._crash_decided = False
        self._crash = False
        self._first_failing_decided = False
        self._first_failing: str | None = None
        self.summary: str | None = None
        self.failed: list[str] = []
        self.failed_total = 0

    def feed(self, text: str) -> None:
        for chunk in text.splitlines(keepends=True):
            body = chunk.splitlines()
            part = body[0] if body else ""
            held = self._partial or ""
            if part == chunk:  # no line break in this piece: the line continues in the next one
                self._partial = self._held(held, part)
                continue
            self._partial = None
            self._line(self._held(held, part) if held else part)

    def finish(self) -> None:
        """Judge the final line if the stream ended without a line break."""
        if self._partial is not None:
            line, self._partial = self._partial, None
            self._line(line)

    @staticmethod
    def _held(held: str, part: str) -> str:
        return held + part[: max(0, _PIECE_CHARS - len(held))]

    def _line(self, line: str) -> None:
        if " passed" in line or " failed" in line or " error" in line:
            self.summary = line.strip()
        stripped = line.strip()
        if line.startswith(("FAILED", "ERROR ")):
            self.failed_total += 1
            if len(self.failed) < _MAX_FAILED_NAMES:
                self.failed.append(stripped)
        if not self._first_failing_decided:
            for prefix in ("FAILED ", "ERROR "):
                if stripped.startswith(prefix):
                    self._first_failing_decided = True
                    target = stripped[len(prefix) :].split(" ", 1)[0]
                    self._first_failing = target.split("::", 1)[0] or None
                    break
        if not self._crash_decided and stripped.startswith("E "):
            verdict = is_crash_message(stripped[2:])
            if verdict is not None:
                self._crash_decided = True
                self._crash = verdict

    @property
    def killed_by_crash(self) -> bool:
        """True if the first decisive ``E`` line is an exception from the code, not a failed assertion."""
        return self._crash

    @property
    def first_failing_file(self) -> str | None:
        """The test file named by the first FAILED/ERROR line, or ``None``."""
        return self._first_failing


def _killed_by_crash(output: str) -> bool:
    """True if the failure looks like an exception from the mutated code, not a failed assertion.

    Read from pytest's own summary line rather than from the traceback body: a traceback can mention
    an exception name that a test deliberately asserted with ``pytest.raises``, and counting that as
    a crash would misreport a test doing its job.
    """
    scan = OutputScan()
    scan.feed(output)
    scan.finish()
    return scan.killed_by_crash


def _pytest_flags() -> list[str]:
    """Flags that isolate a nested run, built from what is actually installed.

    Disabling a plugin BY NAME is not safe: `-p no:xdist` made `pytest_progress` fail validation
    with `unknown hook 'pytest_xdist_node_collection_finished'`, pytest exited 3, and the run
    aborted. Parallelism and coverage are therefore switched off through their own options, which
    only exist when the plugin does -- so both are added conditionally rather than assumed.

    Coverage matters beyond speed: a consuming repo's `--cov-fail-under` would fail every mutant
    run for a reason unrelated to the mutation, making every mutant look killed.
    """
    flags = ["-q", "--no-header", "-p", "no:randomly", "-p", "no:cacheprovider"]
    if importlib.util.find_spec("xdist") is not None:
        flags += ["-n", "0"]
    if importlib.util.find_spec("pytest_cov") is not None:
        flags.append("--no-cov")
    return flags


# An underscore is part of a name (dash_duo), so the word is bounded by letters and digits only.
_DASH_FIXTURE_WORD = re.compile(r"(?<![A-Za-z0-9])dash(?![A-Za-z0-9])")


@functools.lru_cache(maxsize=1)
def _dash_plugin_installed() -> bool:
    """Whether pytest-dash is registered. Its plugin imports IPython at every interpreter start, which is most of a cold
    nested run's cost on machines that have it (about 7 s against 4.5 s without it, measured on a one-test repo)."""
    from importlib.metadata import entry_points

    return any(entry.name == "dash" for entry in entry_points(group="pytest11"))


@functools.lru_cache(maxsize=32)
def _tree_mentions_dash(root: str) -> bool:
    """Whether any Python file under *root* names ``dash``, the one place its plugin's fixtures can come from.

    Disabling a plugin changes a verdict only if some test can reach what it provides; a repo that never names it cannot."""
    for path in Path(root).rglob("*.py"):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            if _DASH_FIXTURE_WORD.search(path.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            return True  # unreadable means unknown, so the plugin stays on
    return False


def _plugin_isolation_flags(root: Path) -> list[str]:
    """``-p no:dash`` for a nested run whose tree never names dash, otherwise nothing.

    Only the cold nested run gets this. The warm worker is started once per sweep, so its startup is not paid per mutant."""
    if _dash_plugin_installed() and not _tree_mentions_dash(str(root)):
        return ["-p", "no:dash"]
    return []


def _first_failing_file(output: str) -> str | None:
    """The test FILE named by pytest's first failure line, or ``None``.

    Read from the short summary (``FAILED path::test - reason``) rather than from the traceback,
    for the same reason `_killed_by_crash` does: the summary line is a stable shape and a traceback
    is whatever the failing assertion happened to print. With `-x` there is at most one.
    """
    scan = OutputScan()
    scan.feed(output)
    scan.finish()
    return scan.first_failing_file


# ── process trees ───────────────────────────────────────────────────────────────────────────────
def _new_group_kwargs() -> dict[str, Any]:
    """Popen arguments that put the child at the head of its own process group, so the tree can be killed."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_tree(process: "subprocess.Popen[Any]") -> None:
    """Kill *process* and every process it started.

    ``Popen.kill`` ends the direct child only. A test that starts a server, or pytest-xdist's
    workers, survived it -- on Windows they kept the sandbox's files open, so its removal failed
    too. POSIX kills the child's session; Windows asks ``taskkill /T``, which walks the tree by
    parent pid and so must run before the child itself is gone.
    """
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
    else:
        try:
            getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
        except OSError:  # swallow-ok: the process may already have exited; killing it is best effort
            pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - a process that ignores SIGKILL
        pass


class _Tail:
    """The last :data:`_TAIL_CHARS` characters of a stream, held as pieces so the rest can be dropped."""

    def __init__(self) -> None:
        self._pieces: deque[str] = deque()
        self._size = 0

    def add(self, piece: str) -> None:
        self._pieces.append(piece)
        self._size += len(piece)
        while self._pieces and self._size - len(self._pieces[0]) >= _TAIL_CHARS:
            self._size -= len(self._pieces.popleft())

    def text(self) -> str:
        return "".join(self._pieces)[-_TAIL_CHARS:]


#: The longest protocol line the warm worker's reader keeps. Replies are the worker's own JSON, far below this; a longer line is
#: kept only as its head, which does not parse, so that request gets no reply and times out like a lost one, and the reader's memory stays bounded.
_PROTOCOL_LINE_CAP = 8 << 20


def _protocol_lines(stream: Any, cap: int = _PROTOCOL_LINE_CAP) -> Iterator[str]:
    """The lines of a worker's reply stream, read in bounded pieces; a line over *cap* is yielded as its first *cap* characters."""
    pieces: list[str] = []
    size = 0
    while True:
        piece = stream.readline(_PIECE_CHARS)
        if not piece:
            break
        if size < cap:
            kept = piece[: cap - size]
            pieces.append(kept)
            size += len(kept)
        if piece.endswith("\n"):
            yield "".join(pieces)
            pieces, size = [], 0
    if pieces:
        yield "".join(pieces)


def _drain(pipe: Any, tail: _Tail, scan: Optional[OutputScan]) -> None:
    """Read *pipe* to its end, keeping its tail and feeding each piece to *scan*.

    Reading continuously is what keeps a chatty child from blocking on a full pipe. A pipe closed by
    a kill ends the stream; what was read before that is kept.
    """
    try:
        while True:
            piece = pipe.readline(_PIECE_CHARS)
            if not piece:
                break
            tail.add(piece)
            if scan is not None:
                scan.feed(piece)
    except (OSError, ValueError):  # swallow-ok: a pipe closed by the kill ends the stream; what was read is kept
        pass


class CapturedRun(subprocess.CompletedProcess):  # type: ignore[type-arg]
    """A :class:`subprocess.CompletedProcess` whose ``stdout`` is only the tail of the output.

    ``stdout_scan`` holds the classifiers' view of the WHOLE stream, collected while it streamed.
    """

    def __init__(self, args: Any, returncode: int, stdout: str, stderr: str, stdout_scan: OutputScan) -> None:
        super().__init__(args, returncode, stdout, stderr)
        self.stdout_scan = stdout_scan


def _scan_of(run: Any) -> OutputScan:
    """The whole-stream scan of *run*: the one a :class:`CapturedRun` carries, or one made from its text."""
    if isinstance(run, CapturedRun):
        return run.stdout_scan
    scan = OutputScan()
    scan.feed(run.stdout)
    scan.finish()
    return scan


def _close_drained(process: "subprocess.Popen[Any]", readers: Sequence[threading.Thread]) -> None:
    """Close each pipe whose reader has finished; a reader still blocked on its pipe keeps it open."""
    for stream, reader in zip((process.stdout, process.stderr), readers):
        if stream is not None and not reader.is_alive():
            stream.close()


def run_with_deadline(cmd: Sequence[str], *, cwd: Union[Path, str], env: Optional[dict[str, str]], timeout: float) -> "Optional[CapturedRun]":
    """``subprocess.run(capture_output=True, text=True)`` whose timeout kills the whole process tree.

    The output is read as it streams. Memory is bounded: ``stdout`` and ``stderr`` keep only their
    last :data:`_TAIL_CHARS` characters, and the classifiers' markers are collected on the way.

    Returns ``None`` when *timeout* expired.
    """
    process = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
        env=env,
        **_new_group_kwargs(),
    )
    out_tail, err_tail, scan = _Tail(), _Tail(), OutputScan()
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, out_tail, scan), daemon=True, name="mutation-output-reader"),
        threading.Thread(target=_drain, args=(process.stderr, err_tail, None), daemon=True, name="mutation-stderr-reader"),
    ]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout)
        end = time.monotonic() + timeout
        for reader in readers:
            reader.join(max(0.0, end - time.monotonic()))
        if any(reader.is_alive() for reader in readers):
            raise subprocess.TimeoutExpired(list(cmd), timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        end = time.monotonic() + 10
        for reader in readers:
            reader.join(max(0.0, end - time.monotonic()))  # pipes may stay open under a grandchild that survived the kill
        _close_drained(process, readers)
        return None
    _close_drained(process, readers)
    scan.finish()
    return CapturedRun(list(cmd), process.returncode, out_tail.text(), err_tail.text(), scan)


def _remove_tree(path: Union[Path, str]) -> None:
    """``shutil.rmtree`` that clears read-only bits, retries briefly, and WARNS about what it could not remove.

    ``ignore_errors=True`` hid every failure, so a sandbox pinned by a leaked process stayed on
    disk, run after run, with nothing saying so.
    """
    target = Path(path)
    failures: list[str] = []

    def on_error(func: Any, name: str, _exc: Any) -> None:
        try:
            os.chmod(name, stat.S_IWRITE)
            func(name)
        except OSError as exc:
            failures.append(f"{name}: {exc}")

    for attempt in range(3):
        if not target.exists():
            return
        failures.clear()
        if sys.version_info >= (3, 12):
            shutil.rmtree(target, onexc=on_error)
        else:  # pragma: no cover - exercised on 3.9-3.11
            shutil.rmtree(target, onerror=on_error)
        if not target.exists():
            return
        time.sleep(0.2 * (attempt + 1))
    shown = "; ".join(failures[:3]) or "the directory still exists"
    warnings.warn(f"mutation teeth could not remove its sandbox {target}: {shown}", stacklevel=2)


def _run_pytest(test_paths: Sequence[Union[Path, str]], cwd: Path, timeout: float):
    """Run pytest, or return ``None`` when it did not finish inside *timeout*.

    The timeout is mandatory rather than optional: swapping ``<`` for ``<=`` produces
    non-terminating loops by construction, so an unbounded run hangs forever. On a timeout the
    whole process tree is killed, not only pytest.
    """
    return run_with_deadline(
        [sys.executable, "-m", "pytest", *[str(t) for t in test_paths], *_pytest_flags(), *_plugin_isolation_flags(cwd), "-x"],
        cwd=cwd,
        env=_pytest_env(),
        timeout=timeout,
    )


class _WarmRunner:
    """A persistent pytest process, reused across mutants.

    Measured: 15.8s per mutant as a fresh subprocess, of which pytest reports 0.48s as actual test
    execution -- roughly 97% is interpreter startup and importing the package under test. Warm, with
    a repo-local module purge between runs, the marginal cost measured 1.6-2.7s, a 7-10x
    improvement.

    The two obvious warm designs are silently wrong and were rejected by execution, not argument: no
    purge at all leaves the original module imported and reports a false SURVIVOR against a mutation
    never applied, and ``importlib.reload`` produces a false KILL because an already-imported test
    module still holds the pre-reload class. Only an atomic purge of every repo-local module -- test
    modules included -- is sound. See :mod:`py_ci_shared._mutation_worker`.

    A single worker is used rather than four. Four cold starts measured 39.3s against 19.2s for one:
    on Windows there is no ``fork``, so startup is half-serialised and disk-bound, and four workers
    would each need their own tree copy because they mutate the same file. The measured single-worker
    gain is already 6.9x; the parallel variant's extra 1.2-1.6x is not worth a per-worker copy and a
    crash-recovery protocol.

    The worker's replies are read by one pump thread per process into a queue, so a read with a
    deadline never leaves a thread blocked on a pipe that nothing will write to again.
    """

    def __init__(self, sandbox: Path, timeout: float, *, target: Optional[Path] = None, module_names: Sequence[str] = ()) -> None:
        self.sandbox = sandbox
        self.timeout = timeout
        #: The mutated file, and the dotted names it can be imported as: a run with
        #: ``check_origin=True`` reports where each of them was really loaded from.
        self.target = target
        self.module_names = list(module_names)
        self.process: Any = None
        #: The file whose failure ended the last run, if the worker reported one.
        self.last_failed: str | None = None
        #: The last run's `t_purge` / `t_pytest` / `t_total`, measured inside the worker.
        self.last_timings: dict[str, float] | None = None
        #: Whether the last run's first failure was an exception from the code rather than an assertion.
        self.last_crash: bool = False
        #: Whether the last run returned ``None`` because it overran the timeout (the worker is then stopped).
        self.last_timed_out: bool = False
        #: ``{"sandbox": bool, "elsewhere": [...]}`` from the last ``check_origin`` run.
        self.last_origin: dict[str, Any] | None = None
        self._ids = itertools.count(1)
        self._lines: "Optional[queue.Queue[Optional[str]]]" = None
        self._pumped: Any = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "py_ci_shared._mutation_worker", str(self.sandbox)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.sandbox),
            env=_pytest_env(),
            bufsize=1,
            **_new_group_kwargs(),
        )

    def restart(self) -> None:
        """A fresh worker, after a timeout or a death: the next mutant must not inherit a wedged process."""
        self.stop()
        self.start()

    def _reset(self) -> None:
        self.last_failed = None
        self.last_timings = None
        self.last_crash = False
        self.last_timed_out = False
        self.last_origin = None

    def run(self, test_paths: Sequence[Union[Path, str]], *, check_origin: bool = False) -> int | None:
        """Exit code, or ``None`` when the worker died, timed out or sent no usable reply.

        ``None`` deliberately does not mean "killed": a dead worker is a harness problem.
        ``last_timed_out`` says which kind of ``None`` it was, because the two need different
        handling -- a timeout re-run cold would only time out again.

        The timeout is enforced on the reply, which a single non-terminating mutant would otherwise
        withhold forever. A mutation that turns ``<`` into ``<=`` can produce exactly that by
        construction.
        """
        self._reset()
        if self.process is None or self.process.poll() is not None:
            return None
        request_id = next(self._ids)
        request: dict[str, Any] = {"cmd": "run", "id": request_id, "args": [str(t) for t in test_paths] + _pytest_flags() + ["-x"]}
        if check_origin and self.target is not None:
            request["target"] = str(self.target)
            request["names"] = self.module_names
        try:
            assert self.process.stdin is not None and self.process.stdout is not None
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            logger.debug("warm runner pipe failed, returned no result: %s", type(exc).__name__)
            return None
        reply = self._reply_to(request_id, time.monotonic() + self.timeout)
        if reply is None:
            return None
        # Advisory: which file failed first, so the caller can lead with it next time. A worker
        # that does not report it simply leaves the order alone.
        failed = reply.get("failed")
        self.last_failed = failed if isinstance(failed, str) else None
        self.last_crash = reply.get("crash") is True
        origin = reply.get("origin")
        self.last_origin = origin if isinstance(origin, dict) else None
        #: Per-mutant timings from inside the worker, where the cost actually is.
        self.last_timings = {k: reply[k] for k in ("t_purge", "t_pytest", "t_total") if k in reply} or None
        try:
            return int(reply["rc"])
        except (TypeError, ValueError) as exc:
            logger.debug("warm runner reply had no valid return code: %s", type(exc).__name__)
            return None

    def _reply_to(self, request_id: int, deadline: float) -> Optional[dict[str, Any]]:
        """The reply carrying *request_id*, skipping any other line; ``None`` on EOF or at the deadline."""
        while True:  # audit-exempt: busy_retry_loop -- each read waits at most the remaining deadline, so the loop is bounded in time
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.last_timed_out = True
                # Wedged on this mutant, not merely slow: stop it so the next request is not read
                # against a reply that belongs to this one.
                self.stop()
                return None
            line = self._next_line(remaining)
            if line is None:
                if self._lines is not None and self._eof:
                    return None
                continue
            try:
                reply = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Anything that is not the reply to THIS request -- stray test output that reached the
            # channel, or a late reply to an earlier one -- is skipped, never read as an exit code.
            if isinstance(reply, dict) and reply.get("id") == request_id and "rc" in reply:
                return reply
            if isinstance(reply, dict) and reply.get("id") == request_id:
                return None  # the worker answered this request with an error

    @property
    def _eof(self) -> bool:
        return self._pumped is not None and self._pumped.get("eof", False)

    def _ensure_pump(self) -> "queue.Queue[Optional[str]]":
        if self._lines is not None and self._pumped is not None and self._pumped.get("process") is self.process:
            return self._lines
        lines: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=_QUEUE_LINES)
        state: dict[str, Any] = {"process": self.process, "eof": False}
        stdout = self.process.stdout

        def pump() -> None:
            try:
                for line in _protocol_lines(stdout):
                    _put_latest(lines, line)
            except (OSError, ValueError):  # swallow-ok: a closed pipe is the end of the stream; the reader reports it through the None sentinel
                pass
            state["eof"] = True
            _put_latest(lines, None)

        threading.Thread(target=pump, daemon=True, name="mutation-worker-reader").start()
        self._lines, self._pumped = lines, state
        return lines

    def _next_line(self, wait: float) -> Optional[str]:
        lines = self._ensure_pump()
        try:
            return lines.get(timeout=max(0.0, wait))
        except queue.Empty as exc:
            logger.debug("warm runner produced no line in time: %s", type(exc).__name__)
            return None

    def _readline_within(self, deadline: float) -> str | None:
        """One line from the worker, or ``None`` if none arrives within *deadline* seconds or at EOF."""
        return self._next_line(deadline)

    def stop(self) -> None:
        process, self.process = self.process, None
        self._lines = None
        self._pumped = None
        if process is None:
            return
        try:
            if process.stdin is not None and process.poll() is None:
                process.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
                process.stdin.flush()
            process.wait(timeout=10)
        except Exception:
            _kill_tree(process)
        finally:
            for stream in (process.stdin, process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:  # noqa: PERF203 - each stream is closed on its own; one failing must not leave the next open
                    pass

    def __enter__(self) -> "_WarmRunner":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def _put_latest(lines: "queue.Queue[Optional[str]]", item: Optional[str]) -> None:
    """Queue *item*, dropping the oldest queued line when the queue is full.

    The reply a reader waits for is the newest line the worker writes, so dropping the oldest keeps
    it; stray output that reached the channel is skipped by the reader anyway.
    """
    if lines.full():
        try:
            lines.get_nowait()
        except queue.Empty:  # swallow-ok: the reader took the line first; there is room now
            pass
    lines.put_nowait(item)  # only the pump thread puts, so the slot freed above is still free


def _lead_with(paths: list[Union[Path, str]], first: str) -> list[Union[Path, str]]:
    """*paths* reordered so the entry matching *first* leads. Unknown names change nothing."""
    wanted = Path(first).as_posix()
    for i, p in enumerate(paths):
        if Path(p).as_posix() == wanted:
            return [paths[i], *paths[:i], *paths[i + 1 :]]
    return paths


#: Exit codes that mean "pytest did not get through the run": 2 interrupted, 3 internal error, 4
#: usage error, 5 nothing collected. On a MUTANT all four say the same thing, because the baseline
#: has already passed with the same paths, so only the mutation can have caused it.
#:
#: 4 and 5 were added after a measured case: emptying the string ``"_count"`` inside a ``__slots__``
#: tuple raises ``TypeError: __slots__ must be identifiers`` while the class body executes, which
#: happens during ``conftest`` import, so pytest exits 4. A refusal discards the result for the
#: WHOLE FILE, so that one obviously-killed mutant cost the other thirty-five in its file an answer.
_MUTATION_BROKE_THE_RUN = frozenset({2, 3, 4, 5})


def _classify_code(code: int, what: str, *, baseline_verified: bool = False) -> bool:
    """True if the tests PASSED. Raises when the exit code means neither pass nor fail.

    *baseline_verified* says the identical command was already observed to exit 0 on the unmutated
    tree, which is what licenses reading a "did not get through the run" code as a kill -- of the
    crash kind, which ``killed_by_crash`` keeps separate from a kill by assertion -- rather than as
    a refusal. On the baseline itself the same codes mean the caller passed paths pytest cannot use,
    and refusing is the only safe answer: the alternative reports every mutant killed and the run as
    a clean bill of health, the failure :mod:`gate_integrity` exists to prevent.

    It replaces a check that sniffed *what* for the words "the unmutated baseline". A guard that
    depends on the wording of a human-readable label is one rename away from silently classifying
    every baseline failure as a kill.
    """
    if code == 0:
        return True
    if code == 1:
        return False
    if baseline_verified and code in _MUTATION_BROKE_THE_RUN:
        return False
    raise MutationHarnessError(
        f"pytest exited {code} on {what}, which is neither pass (0) nor fail (1). "
        "4 = usage error, 5 = nothing collected, 2 = interrupted, 3 = internal error. "
        "Check `test_paths` and that the tests are reachable from `repo_root`."
    )


def _classify(result, what: str, *, baseline_verified: bool = False) -> bool:
    """True if the tests PASSED. Raises when the exit code means neither pass nor fail.

    Same rule as :func:`_classify_code`, for the cold-process path -- which had no mutation-caused
    branch at all, so a mutant that broke the run was a refusal whenever it was re-checked cold.
    """
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    if baseline_verified and result.returncode in _MUTATION_BROKE_THE_RUN:
        return False
    raise MutationHarnessError(
        f"pytest exited {result.returncode} on {what}, which is neither pass (0) nor fail (1). "
        "4 = usage error, 5 = nothing collected, 2 = interrupted, 3 = internal error.\n"
        "Check `test_paths` and that the tests are reachable from `repo_root`.\n"
        f"{result.stdout[-2000:]}"
    )
