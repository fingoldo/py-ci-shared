"""One baseline: a multiset of accepted finding keys, each with a note; atomic, sorted, fail-when-missing.

What the dozen per-gate writers got wrong (audit 2026-09-24 ARCH-22, TZ-15, TZ-17, MT-1), fixed once here:

* **Multiset, not set.** Two identical ``assert n > 0`` in one file are two findings. A set-keyed baseline accepted
  the second one for free once the first was baselined; here each key carries a COUNT and a third copy is new.
* **Missing file fails.** Several gates wrote today's findings and skipped when the baseline was absent, so
  deleting the baseline (or a wrong path) turned the gate into a no-op that reported success. Here a missing
  baseline is a failure that names the refresh command; it is only written when a refresh is requested.
* **Unjustified entries fail.** A gate that wants every accepted entry reasoned passes
  ``new_note=UNJUSTIFIED_MARKER + ": ..."``, so a refresh writes the marker as each new entry's note; a normal run
  rejects any entry still carrying it (always, whatever the gate), so "refresh" cannot be used as "accept
  everything" (mutation_teeth's marker was previously never checked).
* **Refresh shrinks only.** A refresh drops entries that no longer fire and lowers counts; it never adds an entry or
  raises a count unless growth is opted into (``grow=True``, ``--py-ci-refresh-grow``, ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``).
  Without the opt-in it writes the shrunk baseline and fails naming what it refused, so "refresh" cannot silently
  accept a new violation. Seeding a missing baseline with findings is growth too; an empty one may always be written.
  :func:`write_ratchet` applies the same rule to the gates that keep their own file shapes.
* **Atomic, byte-stable writes.** ``mkstemp`` in the target directory + ``os.replace``; UTF-8, ``\\n`` newlines,
  ``sort_keys``, sorted entries, trailing newline. An interrupted write leaves the old file, never half of one.

On-disk format written::

    {"schema": 1, "gate": "<name>", "entries": {"<key>": {"count": 2, "note": "why"}}}

Readers also accept every format already committed in consumer repos: a JSON list of keys (duplicates count),
``{key: note}``, ``{"accepted": {key: note}}`` (baseline_ratchet), ``{key: count}``, and ``{"_comment": ...}``
metadata keys, which are ignored.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union
from collections.abc import Iterable, Mapping

from .errors import BaselineError, BaselineGrowthError
from .findings import Finding
from .refresh import grow_hint, grow_requested

PathLike = Union[str, "os.PathLike[str]"]

UNJUSTIFIED_MARKER = "NEEDS-JUSTIFICATION"
SCHEMA_VERSION = 1


def is_unjustified(note: object) -> bool:
    return isinstance(note, str) and note.lstrip().startswith(UNJUSTIFIED_MARKER)


def atomic_write_text(path: PathLike, text: str) -> None:
    """Write *text* to *path* atomically: temp file in the same directory, fsync, ``os.replace``. UTF-8, ``\\n``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dump_json(data: Any) -> str:
    """The canonical byte-stable JSON text: indent 2, sorted keys, ASCII, trailing newline."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def load_json(path: PathLike) -> Any:
    """Parse the JSON file at *path* (UTF-8, a BOM tolerated). Raises :class:`BaselineError` naming the file when it
    cannot be read or is not JSON; a missing file is the caller's to check first."""
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise BaselineError(f"baseline {p} is unreadable: {exc}") from exc


_META_KEYS = frozenset({"_comment", "_meta", "_schema", "_note"})


def _parse_list(data: list, path: Path) -> "Counter[str]":
    counts: Counter[str] = Counter()
    for item in data:
        if isinstance(item, str):
            counts[item] += 1
        elif isinstance(item, (list, tuple)) and item and all(isinstance(x, str) for x in item):
            counts["|".join(item)] += 1  # e.g. private_imports-style (path, module) pairs
        else:
            raise BaselineError(f"{path}: unsupported list entry {item!r}")
    return counts


def _entry(key: str, value: Any, path: Path) -> tuple[int, Optional[str]]:
    """``(count, note or None)`` for one mapping entry in any committed shape."""
    if isinstance(value, dict):
        note = value.get("note", "")
        return int(value.get("count", 1)), note if isinstance(note, str) else json.dumps(note)
    if isinstance(value, bool):
        raise BaselineError(f"{path}: entry {key!r} has a boolean value")
    if isinstance(value, int):
        return value, None
    if isinstance(value, str) or value is None:
        return 1, value or ""
    return 1, json.dumps(value, sort_keys=True)


def _parse_entries(data: Any, path: Path) -> tuple["Counter[str]", dict[str, str]]:
    if isinstance(data, list):
        return _parse_list(data, path), {}
    if not isinstance(data, dict):
        raise BaselineError(f"{path}: baseline must be a JSON list or object, got {type(data).__name__}")
    for wrapper in ("entries", "accepted"):
        if isinstance(data.get(wrapper), dict):
            data = data[wrapper]
            break
    counts: Counter[str] = Counter()
    notes: dict[str, str] = {}
    for key, value in data.items():
        if key in _META_KEYS:
            continue
        n, note = _entry(key, value, path)
        counts[key] += n
        if note is not None:
            notes[key] = note
    return counts, notes


def shrink_only(
    previous: Optional[Mapping[str, int]], current: Mapping[str, int], *, slack: int = 0
) -> tuple[dict[str, int], dict[str, tuple[Optional[int], int]]]:
    """``(kept, grown)`` for a refresh without the growth opt-in.

    *kept* holds each key present in both with the smaller value (a count or a ceiling), so an entry that no longer
    fires is dropped and a lowered value is locked in. *grown* maps every key the refresh refused to ``(old, new)``:
    a key not in *previous* (``old`` None) or one whose value rose by more than *slack*. ``previous=None`` means no
    baseline exists yet, so every current key is growth.
    """
    before = dict(previous or {})
    kept: dict[str, int] = {}
    grown: dict[str, tuple[Optional[int], int]] = {}
    for key, value in current.items():
        if value <= 0:
            continue
        if key not in before:
            grown[key] = (None, int(value))
            continue
        kept[key] = min(int(before[key]), int(value))
        if value > before[key] + slack:
            grown[key] = (int(before[key]), int(value))
    return kept, grown


def growth_message(gate: str, path: PathLike, grown: Mapping[str, tuple[Optional[int], int]], *, seeding: bool = False) -> str:
    """The failure a refused growing refresh reports: what it would have added, and the opt-in that allows it."""
    lines = "\n    ".join(f"{k}  (new: {new})" if old is None else f"{k}  ({old} -> {new})" for k, (old, new) in sorted(grown.items()))
    if seeding:
        head = f"{gate}: baseline {path} does not exist, and seeding it would accept {len(grown)} current finding(s)"
    else:
        head = f"{gate}: refresh wrote only the removals to {path}; it refused {len(grown)} addition(s) or increase(s)"
    return f"{head}:\n    {lines}\nFix them, or accept them deliberately: {grow_hint()}."


def write_ratchet(
    path: PathLike,
    current: Mapping[str, int],
    *,
    gate: str,
    previous: Optional[Mapping[str, int]],
    render: Callable[[dict[str, int]], str],
    grow: Optional[bool] = None,
    request: Any = None,
    slack: int = 0,
) -> dict[str, int]:
    """Refresh a baseline kept in a gate's own shape under the shrink-only rule; return what was written.

    *previous* is the committed ``{key: count or ceiling}`` (None when the file is missing), *render* turns the mapping
    to write into the file text. With growth allowed (*grow*, else :func:`grow_requested` on *request*) *current* is
    written as is. Otherwise the :func:`shrink_only` result is written, and :class:`BaselineGrowthError` is raised
    after the write when anything was refused, so the removals land and the additions are named. A missing baseline
    is written only when nothing was refused (an empty one), so a refused seeding leaves no file behind.
    """
    allow = grow_requested(request) if grow is None else grow
    grown: dict[str, tuple[Optional[int], int]]
    if allow:
        kept, grown = {k: int(v) for k, v in current.items() if v > 0}, {}
    else:
        kept, grown = shrink_only(previous, current, slack=slack)
    if previous is not None or not grown:
        atomic_write_text(path, render(kept))
    if grown:
        raise BaselineGrowthError(growth_message(gate, path, grown, seeding=previous is None), sorted(grown))
    return kept


@dataclass
class BaselineOutcome:
    """What one enforcement found. ``ok`` is False when the gate must fail."""

    new: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    unjustified: list[str] = field(default_factory=list)
    missing: bool = False
    refreshed: bool = False
    message: str = ""

    @property
    def ok(self) -> bool:
        return not (self.new or self.unjustified or self.missing)

    def raise_for_pytest(self, *, fail_on_stale: bool = True) -> None:
        """``pytest.skip`` after a refresh, ``pytest.fail`` on a problem, nothing when clean."""
        import pytest

        if self.refreshed:
            pytest.skip(self.message)
        if not self.ok or (fail_on_stale and self.stale):
            pytest.fail(self.message, pytrace=False)


class Baseline:
    """A committed multiset of accepted finding keys for one gate."""

    def __init__(self, path: PathLike, *, gate: str = "", refresh_command: str = "", new_note: str = "") -> None:
        self.path = Path(path)
        self.gate = gate or self.path.stem
        self.refresh_command = refresh_command or f"set PY_CI_SHARED_REFRESH={self.gate} (or pass its --refresh-* flag) and rerun"
        self.new_note = new_note

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> tuple["Counter[str]", dict[str, str]]:
        """``(counts, notes)``. Raises :class:`BaselineError` when the file is missing or malformed."""
        if not self.exists():
            raise BaselineError(f"baseline {self.path} does not exist. Create it with: {self.refresh_command}")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise BaselineError(f"baseline {self.path} is unreadable: {exc}") from exc
        return _parse_entries(data, self.path)

    def save(self, counts: Mapping[str, int], notes: Optional[Mapping[str, str]] = None) -> None:
        notes = notes or {}
        entries = {k: {"count": int(n), "note": notes.get(k, "")} for k, n in sorted(counts.items()) if n > 0}
        atomic_write_text(self.path, dump_json({"schema": SCHEMA_VERSION, "gate": self.gate, "entries": entries}))

    @staticmethod
    def count(found: Iterable[Union[Finding, str]]) -> "Counter[str]":
        return Counter(f.key if isinstance(f, Finding) else f for f in found)

    def regenerate(self, found: Iterable[Union[Finding, str]], *, grow: Optional[bool] = None, request: Any = None) -> "Counter[str]":
        """Write today's findings, keeping existing notes; new keys get ``self.new_note``.

        Shrink-only unless growth is allowed (*grow*, else :func:`grow_requested`): see :func:`write_ratchet`, whose
        :class:`BaselineGrowthError` this raises after writing the removals.
        """
        current = self.count(found)
        previous_counts: Optional[Counter[str]] = None
        notes: dict[str, str] = {}
        if self.exists():
            try:
                previous_counts, notes = self.load()
            except BaselineError:
                previous_counts = None

        def render(kept: dict[str, int]) -> str:
            entries = {k: {"count": n, "note": notes.get(k) or self.new_note} for k, n in sorted(kept.items())}
            return dump_json({"schema": SCHEMA_VERSION, "gate": self.gate, "entries": entries})

        kept = write_ratchet(self.path, current, gate=self.gate, previous=previous_counts, render=render, grow=grow, request=request)
        return Counter(kept)

    def _refresh(self, found: list[Union[Finding, str]], *, grow: Optional[bool], request: Any) -> BaselineOutcome:
        try:
            current = self.regenerate(found, grow=grow, request=request)
        except BaselineGrowthError as exc:
            return BaselineOutcome(new=exc.grown, message=str(exc))
        return BaselineOutcome(refreshed=True, message=f"{self.gate}: baseline rewritten, {sum(current.values())} entr(ies) in {self.path}")

    def enforce(
        self,
        found: Iterable[Union[Finding, str]],
        *,
        refresh: bool = False,
        describe: Optional[Mapping[str, str]] = None,
        guidance: str = "",
        grow: Optional[bool] = None,
        request: Any = None,
    ) -> BaselineOutcome:
        """Compare *found* against the baseline (or rewrite it when *refresh*). Never raises on a finding.

        *describe* maps a key to what the reader should see for it (e.g. ``path:line  expr``); defaults to the key.
        A refresh is shrink-only unless *grow* (or :func:`grow_requested` on *request*); a refused growth is a failed
        outcome whose ``new`` lists the refused keys.
        """
        found = list(found)
        describe = dict(describe or {})
        for f in found:
            if isinstance(f, Finding):
                describe.setdefault(f.key, f.render())
        if refresh:
            return self._refresh(found, grow=grow, request=request)
        if not self.exists():
            return BaselineOutcome(
                missing=True,
                message=f"{self.gate}: baseline {self.path} does not exist, so nothing is accepted and nothing was checked against it. Create it with: {self.refresh_command}",
            )
        accepted, notes = self.load()
        current = self.count(found)
        outcome = BaselineOutcome()
        for key in sorted(current):
            extra = current[key] - accepted.get(key, 0)
            outcome.new.extend([key] * max(extra, 0))
        for key in sorted(accepted):
            extra = accepted[key] - current.get(key, 0)
            outcome.stale.extend([key] * max(extra, 0))
        outcome.unjustified = sorted(k for k in accepted if is_unjustified(notes.get(k)))
        parts: list[str] = []
        if outcome.new:
            lines = "\n    ".join(describe.get(k, k) for k in outcome.new)
            parts.append(f"{len(outcome.new)} new finding(s) not in {self.path.name}{': ' + guidance if guidance else ''}\n    {lines}")
        if outcome.unjustified:
            parts.append(
                f"{len(outcome.unjustified)} baseline entr(ies) still marked {UNJUSTIFIED_MARKER!r}; replace the marker with the reason "
                "each is acceptable (a refresh records what was found, it does not decide it is fine):\n    " + "\n    ".join(outcome.unjustified)
            )
        if outcome.stale:
            parts.append(f"{len(outcome.stale)} baseline entr(ies) no longer found; prune with: {self.refresh_command}\n    " + "\n    ".join(outcome.stale))
        outcome.message = f"{self.gate}:\n  " + "\n  ".join(parts) if parts else f"{self.gate}: clean ({sum(accepted.values())} accepted)"
        return outcome
