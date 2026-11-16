"""The one way a gate or tool writes to the console: never a traceback on a codepage that lacks a character.

A finding about ``src/数据.py`` on a cp1251 or cp1252 console (the default of a Windows pre-commit hook) raised
``UnicodeEncodeError`` from a bare ``sys.stdout.write``: the finding, the rest of the output and the exit code were lost,
and the traceback's exit 1 read as "findings" (audit 2026-10-03 K-1, 49-T2). Characters the stream cannot encode are
written as backslash escapes instead.

``safe_stdio`` switches both streams to ``backslashreplace``. Every entry module calls it as the first statement of its
``if __name__ == "__main__":`` block (audit 45-T1).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Optional, TextIO

__all__ = ["capped", "safe_stdio", "write_text"]


def _reconfigure(stream: object) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="backslashreplace")
    except (ValueError, OSError):  # best-effort: a closed or detached stream has nothing it could print anyway
        return


def safe_stdio() -> None:
    """Write unencodable characters as backslash escapes on stdout and stderr instead of raising."""
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)


def capped(items: "Sequence[str]", limit: int) -> "list[str]":
    """The first *limit* items, then ``... and N more`` when there are more (49-T7: a list that stops at 20 must say so)."""
    shown = list(items[:limit])
    if len(items) > limit:
        shown.append(f"... and {len(items) - limit} more")
    return shown


def write_text(text: str, *, err: bool = False, stream: Optional[TextIO] = None) -> None:
    """Write *text* exactly as given (no newline is added) to stdout, or stderr with ``err=True``, or to *stream*."""
    target = stream if stream is not None else (sys.stderr if err else sys.stdout)
    try:
        target.write(text)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "ascii"
        target.write(text.encode(encoding, "backslashreplace").decode(encoding, "replace"))
