"""Typed errors raised by ``py_ci_shared._core``.

Every error a gate can hit while reading its corpus is a subclass of :class:`CoreError`, so a gate never has to
catch ``(OSError, SyntaxError, UnicodeDecodeError, ValueError)`` and decide on its own whether to skip. The ones a
gate turns into a test failure also derive from ``AssertionError``: pytest then renders them as an ordinary failed
assertion, and an ``except Exception`` in gate code cannot quietly swallow a failed floor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class CoreError(Exception):
    """Base class for every ``_core`` error."""


class SourceError(CoreError):
    """A file could not be turned into source text or an AST. Carries where and why."""

    kind = "error"

    def __init__(self, path: Path, message: str, line: Optional[int] = None) -> None:
        self.path = Path(path)
        self.message = message
        self.line = line
        where = f"{self.path}:{line}" if line is not None else str(self.path)
        super().__init__(f"{where}: {self.kind}: {message}")


class SourceReadError(SourceError):
    """The bytes could not be read (OSError) or decoded (not valid in the declared encoding)."""

    kind = "unreadable"


class SourceParseError(SourceError):
    """The text decoded but is not valid Python for this interpreter (SyntaxError, or a NUL byte)."""

    kind = "unparsable"


class CorpusError(CoreError):
    """The corpus root does not exist or is not a directory. Never a silent empty scan."""


class EmptyScanError(CoreError, AssertionError):
    """Fewer files PARSED than the gate's floor: the walk is not reaching the code."""


class UnparsedFilesError(CoreError, AssertionError):
    """At least one file in the corpus could not be read or parsed, so the gate cannot vouch for it."""


class BaselineError(CoreError, AssertionError):
    """The baseline is missing, malformed, or rejects the current findings."""


class BaselineGrowthError(BaselineError):
    """A refresh would add baseline entries (or raise counts/ceilings) without the growth opt-in. ``grown`` names them."""

    def __init__(self, message: str, grown: "list[str]") -> None:
        super().__init__(message)
        self.grown = list(grown)
