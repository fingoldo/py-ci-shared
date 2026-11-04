"""Count file opens during a block of code and attribute them to the gate that caused them (audit 2026-11-04, 35-F3).

The self gate is I/O-bound, not parse-bound: what a gate costs is how many times it opens each file. ``count_opens`` patches ``io.open`` (which
``Path.read_text``/``read_bytes`` and ``open`` go through), records ``(path, gate)`` for every open, and names the gate as the innermost
``py_ci_shared`` module on the stack that is not a ``_core`` helper. ``python -m py_ci_shared._core.open_audit`` runs ``run-all`` under it and prints
the gates by opens, so the next hotspot is measured instead of guessed.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import pathlib
import os
import sys
import threading
from collections import Counter
from collections.abc import Iterator
from typing import Any, Optional

_PACKAGE = "py_ci_shared."
_HELPER_PREFIXES = ("py_ci_shared._core", __name__)


class OpenCounts:
    """What :func:`count_opens` saw: opens per file, and opens per ``(gate, file)``."""

    def __init__(self) -> None:
        self.by_file: "Counter[str]" = Counter()
        self.by_gate_file: "Counter[tuple[str, str]]" = Counter()
        self._lock = threading.Lock()

    def add(self, path: str, gate: str) -> None:
        with self._lock:
            self.by_file[path] += 1
            self.by_gate_file[(gate, path)] += 1

    def by_gate(self) -> "Counter[str]":
        """Opens per gate module name."""
        totals: "Counter[str]" = Counter()
        for (gate, _path), n in self.by_gate_file.items():
            totals[gate] += n
        return totals

    def max_per_file(self) -> "tuple[str, int]":
        """The most-opened file and its count (``("", 0)`` when nothing was opened)."""
        if not self.by_file:
            return "", 0
        return self.by_file.most_common(1)[0]

    def report(self, top: int = 15) -> str:
        total = sum(self.by_file.values())
        lines = [f"{total} opens over {len(self.by_file)} files; most-opened file: {self.max_per_file()}"]
        lines.extend(f"{n:>8}  {gate}" for gate, n in self.by_gate().most_common(top))
        return "\n".join(lines)


def _gate_of(frame: Any) -> str:
    while frame is not None:
        name = frame.f_globals.get("__name__", "")
        if name.startswith(_PACKAGE) and not name.startswith(_HELPER_PREFIXES):
            return str(name)
        frame = frame.f_back
    return "<outside the package>"


@contextlib.contextmanager
def count_opens(only_under: Optional[str] = None) -> Iterator[OpenCounts]:
    """Record every ``io.open`` of a path (not a file descriptor) in the block; *only_under* keeps just the paths below that directory."""
    counts = OpenCounts()
    real = io.open
    prefix = os.path.normcase(os.path.abspath(only_under)) if only_under else None

    def counting(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, (str, bytes, os.PathLike)):
            path = os.path.normcase(os.path.abspath(os.fsdecode(file)))
            if prefix is None or path.startswith(prefix):
                counts.add(path, _gate_of(sys._getframe(1)))
        return real(file, *args, **kwargs)

    # ``open`` and ``io.open`` are one function bound under two names; Python 3.9's ``Path`` calls it through its accessor, bound at import.
    accessor = getattr(pathlib, "_NormalAccessor", None)
    io.open = builtins.open = counting  # type: ignore[assignment]
    if accessor is not None:
        accessor.open = staticmethod(counting)
    try:
        yield counts
    finally:
        io.open = builtins.open = real  # type: ignore[assignment]
        if accessor is not None:
            accessor.open = staticmethod(real)


def main() -> int:
    from py_ci_shared import cli

    with count_opens(os.getcwd()) as counts:
        rc = cli.main(["run-all"])
    sys.stdout.write(counts.report() + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
