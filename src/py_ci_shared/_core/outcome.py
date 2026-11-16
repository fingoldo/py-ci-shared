"""Outcome channels between a gate and the runner: a refresh that wrote, a skip that evaluated nothing, a PASS with a note.

A gate under pytest signals "I rewrote my baseline" with ``pytest.skip``. The CLI used to report that as ``SKIP`` and
``1 skipped``, indistinguishable from a gate that did not run (audit 2026-11-07 49-F5). :func:`refreshed_skip` raises the
same pytest ``Skipped`` (pytest behaviour is unchanged) tagged so :func:`~py_ci_shared._core.runner.run_gate` reports a
REFRESHED result. :func:`unevaluated_skip` tags a skip whose input made the gate unable to judge (49-F7), which
``skip = "fail"`` in ``[tool.py_ci_shared]`` turns into a failure. :func:`add_note` lets a green gate say something
(49-F11), shown on the PASS row. :func:`track_writes` records which files :func:`~py_ci_shared._core.baseline.atomic_write_text`
rewrote during a run so a refresh can report ``+N -M entries``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "CLI_ENV_VAR",
    "KIND_ATTR",
    "REFRESHED",
    "UNEVALUATED",
    "add_note",
    "collect_notes",
    "entry_delta",
    "kind_of",
    "record_write",
    "refresh_hint",
    "pass_note",
    "running_gate",
    "refreshed_skip",
    "track_writes",
    "unevaluated_skip",
]

KIND_ATTR = "py_ci_shared_kind"
REFRESHED, UNEVALUATED = "refreshed", "unevaluated"
#: Set to ``1`` by the CLI while it runs gates, so a message can name the CLI command instead of a pytest flag (49-F4).
CLI_ENV_VAR = "PY_CI_SHARED_CLI"

_notes: "list[list[str]]" = []
_writes: "list[list[Any]]" = []


def _skip(kind: str, message: str) -> NoReturn:
    import pytest

    exc = pytest.skip.Exception(message)
    setattr(exc, KIND_ATTR, kind)
    raise exc


def refreshed_skip(message: str) -> NoReturn:
    """Skip under pytest, as a refresh always did, but tagged so the CLI reports REFRESHED rather than SKIP."""
    _skip(REFRESHED, message)


def unevaluated_skip(message: str) -> NoReturn:
    """Skip because the gate's input made it unable to judge (no section, nothing triggering); ``skip = "fail"`` makes it a failure."""
    _skip(UNEVALUATED, message)


def kind_of(exc: BaseException) -> str:
    """The tag :func:`refreshed_skip` or :func:`unevaluated_skip` put on *exc*, else an empty string."""
    return str(getattr(exc, KIND_ATTR, ""))


@contextmanager
def collect_notes() -> Iterator["list[str]"]:
    """Collect the notes :func:`add_note` receives while the block runs."""
    bucket: "list[str]" = []
    _notes.append(bucket)
    try:
        yield bucket
    finally:
        _notes.remove(bucket)


def add_note(message: str) -> None:
    """Attach *message* to the PASS of the gate now running; ignored when nothing collects (a plain pytest run)."""
    if _notes:
        _notes[-1].append(message)


@contextmanager
def track_writes(bucket: "Optional[list[Any]]" = None) -> Iterator["list[Any]"]:
    """Collect ``(path, previous text or None, new text)`` for every atomic baseline write while the block runs (into *bucket* when given)."""
    bucket = [] if bucket is None else bucket
    _writes.append(bucket)
    try:
        yield bucket
    finally:
        _writes.remove(bucket)


def record_write(path: Path, text: str) -> None:
    """Called by ``atomic_write_text`` before it replaces *path*; a no-op unless :func:`track_writes` is active."""
    if not _writes:
        return
    try:
        previous: Optional[str] = path.read_text(encoding="utf-8") if path.is_file() else None
    except (OSError, UnicodeDecodeError) as exc:  # best-effort: only the +N -M display counts from empty
        logger.debug("previous baseline text unreadable, counting from empty: %s", type(exc).__name__)
        previous = None
    _writes[-1].append((path, previous, text))


def _entries(text: Optional[str]) -> "set[str]":
    if text is None:
        return set()
    try:
        data = json.loads(text)
    except ValueError as exc:
        logger.debug("baseline text is not JSON, no entry delta: %s", type(exc).__name__)
        return set()
    if isinstance(data, dict) and isinstance(data.get("entries"), (dict, list)):
        data = data["entries"]
    if isinstance(data, dict):
        return {f"{k}={json.dumps(v, sort_keys=True)}" for k, v in data.items()}
    if isinstance(data, list):
        return {json.dumps(v, sort_keys=True) for v in data}
    return set()


def entries_word(n: int, *, count: bool = False) -> str:
    """``entry``/``entries`` for *n* (49-T11: "1 entries" and "entr(ies)" read as a tool that did not check its own output)."""
    word = "entry" if n == 1 else "entries"
    return f"{n} {word}" if count else word


def entry_delta(writes: "list[Any]") -> Optional[str]:
    """``+A -R entries (file, ...)`` over the writes of one refresh (changed entries count as one removal and one addition); None for none."""
    if not writes:
        return None
    if all(before is None for _, before, _ in writes):
        # 49-F8: a missing baseline written for the first time has no "before"; "+0 -0" read as a no-op refresh.
        return "created " + ", ".join(f"{Path(path).name} with {entries_word(len(_entries(after)), count=True)}" for path, _, after in writes)
    added = removed = 0
    names = []
    for path, before, after in writes:
        old, new = _entries(before), _entries(after)
        added += len(new - old)
        removed += len(old - new)
        names.append(Path(path).name)
    return f"+{added} -{removed} entries ({', '.join(names)})"


def pass_note(*, files_read: int, accepted: int, baseline: str = "", unparsed_allowed: int = 0) -> str:
    """The PASS note of a gate (49-T5): what it read, what the baseline accepted, and any unparsed files it allowed to be skipped."""
    parts = [f"read {files_read} file(s)"]
    if baseline:
        parts.append(f"{accepted} finding(s) accepted by {baseline}")
    elif accepted:
        parts.append(f"{accepted} finding(s) accepted")
    else:
        parts.append("0 finding(s) accepted")
    if unparsed_allowed:
        parts.append(f"{unparsed_allowed} unparsed file(s) allowed")
    return "; ".join(parts)


_running: "list[str]" = []


@contextmanager
def running_gate(name: str) -> Iterator[None]:
    """Mark *name* (the registered gate name the CLI enabled) as the gate running, for :func:`refresh_hint` (49-T4(a))."""
    _running.append(name)
    try:
        yield
    finally:
        _running.pop()


def refresh_hint(flag: str, gate: str) -> str:
    """How to refresh *gate*'s baseline: the CLI command under ``py-ci-shared``, else the pytest flag; the env form works in both.

    *gate* is the baseline's internal label; under the CLI the command names the enabled gate (49-T4(a): the label is rejected)."""
    if os.environ.get(CLI_ENV_VAR) == "1":
        name = _running[-1] if _running else gate
        return f"`py-ci-shared refresh {name}` (or PY_CI_SHARED_REFRESH={name})"
    return f"pytest {flag} (or PY_CI_SHARED_REFRESH={gate})"
