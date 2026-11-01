"""A per-mutant checkpoint for a sweep, so an interrupted run resumes instead of starting over.

One JSON line per judged mutant, appended as its verdict lands. Each line records only which of the sweep's lists the
mutant was filed under and what it added to the budget: the mutant objects themselves are NOT stored, because the merge
orders verdicts by the identity of the objects this run generated, and a rebuilt object would not be in that order.
A resumed run therefore re-files the CURRENT objects under the recorded lists.

The file is keyed by the same fingerprint as the cache. Any change to the code under test, to the tests, or to the
scope changes the key, and the old lines are ignored rather than replayed. A torn last line (the process died while
writing it) is skipped; the mutant it described is simply judged again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

FILED_LISTS = ("survivors", "coverage_gaps", "inconclusive", "judged", "crashed")


class SweepCheckpoint:
    """The verdicts a previous run with this fingerprint already reached, and the file that records new ones."""

    def __init__(self, path: Optional[Union[Path, str]], key: str) -> None:
        self._path = Path(path) if path is not None else None
        self._key = key
        self.done: dict[str, dict[str, Any]] = self._load() if self._path is not None else {}

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._path is None or not self._path.is_file():
            return {}
        done: dict[str, dict[str, Any]] = {}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("key") == self._key and isinstance(entry.get("mutant"), str):
                done[entry["mutant"]] = entry
        return done

    def record(self, mutant_key: str, filed: dict[str, int], budget: dict[str, float]) -> None:
        """Append one judged mutant. *filed* counts the list names it was added to; *budget* is its time charge.

        One binary append per line: the sweep's partitions write from separate threads, and an append of a single line
        is not split with another writer's line, so no lock is held across the I/O.
        """
        if self._path is None:
            return
        entry = {"key": self._key, "mutant": mutant_key, "filed": filed, "budget": budget}
        line = (json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "ab") as handle:
            handle.write(line)


def filed_sizes(state: Any) -> dict[str, int]:
    """How long each list the verdicts are filed under is right now, so the next mutant's additions can be measured."""
    return {name: len(getattr(state, name)) for name in FILED_LISTS}


def sweep_delta(state: Any, before: dict[str, int], budget_before: dict[str, float]) -> tuple[dict[str, int], dict[str, float]]:
    """What one judged mutant added: the list names it was filed under (with counts) and its time charge."""
    filed = {name: len(getattr(state, name)) - before[name] for name in FILED_LISTS}
    charged = {name: state.budget[name] - budget_before[name] for name in state.budget}
    return {name: count for name, count in filed.items() if count}, charged


def replay(state: Any, mutant: Any, entry: dict[str, Any]) -> None:
    """File a mutant a previous run judged, under the current run's object, and charge the budget it recorded."""
    for name, count in entry.get("filed", {}).items():
        getattr(state, name).extend([mutant] * count)
    for name, amount in entry.get("budget", {}).items():
        state.budget[name] += amount
