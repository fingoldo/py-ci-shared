"""The one finding record every gate reports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    """One violation at ``path:line``.

    *key* is what a baseline stores. By default it is ``rule::path::message`` WITHOUT the line number, so an edit
    above the finding does not invalidate the baseline entry; two identical findings in one file share a key and
    are told apart by COUNT (baselines are multisets), never collapsed.
    """

    path: str
    line: int
    rule: str
    message: str
    key: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.key:
            object.__setattr__(self, "key", f"{self.rule}::{self.path}::{self.message}")

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"

    def __str__(self) -> str:
        return self.render()


UNPARSED_RULE = "unparsed-file"
