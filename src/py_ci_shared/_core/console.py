"""Make the standard streams of a gate's ``__main__`` entry unable to crash on a non-encodable character (audit 45-T1).

A finding that names a non-ASCII path, written to a legacy-codepage pipe or console, raised ``UnicodeEncodeError`` from a raw
``sys.stdout.write``: the finding was lost and the exit code became the traceback's 1. ``safe_stdio`` switches both streams to
``backslashreplace``. Every entry module calls it as the first statement of its ``if __name__ == "__main__":`` block.
"""

from __future__ import annotations

import sys


def _reconfigure(stream: object) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="backslashreplace")
    except (ValueError, OSError):
        return  # a closed or detached stream: nothing it could print anyway


def safe_stdio() -> None:
    """Write unencodable characters as backslash escapes on stdout and stderr instead of raising."""
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)
