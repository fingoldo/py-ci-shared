"""Shared ratchet for structural checks with a pre-existing backlog.

A check that a codebase already violates in thirty places cannot be introduced as a hard rule without a
thirty-place cleanup first, so in practice it does not get introduced at all. A baseline is the frozen
list of the violations that exist today: the check fails only on entries absent from it, so the rule
starts working against new code immediately while the backlog is paid down separately - or not at all, if
that never becomes worth it.

This is the mechanism glossum and flutter_app_core each grew their own copy of (``tool/meta/baseline.py``),
which is a third copy per repository and three places for a fix to miss. The RULES live in
``dart_scanners``/``arb_checks``/the repo's own scanners; this is the ratchet they all feed.

What the copies learned, kept here:

* **Every accepted entry carries a note.** JSON has no comments, so the note is the value. A bare list of
  paths tells a later reader nothing about whether an entry is a considered exception or an oversight.
  ``baseline_hygiene`` is the check that the notes stay human.
* **A key that is accepted but no longer found is reported, not enforced.** Failing a push for FIXING
  something would be perverse; a baseline nobody prunes stops meaning anything, so it is printed every run
  and pruned by a regeneration.
* **Regeneration preserves existing notes.** Otherwise the first regeneration silently replaces every
  human reason with the scanner's own sentence.
* **A scan that finds nothing while the baseline holds entries fails.** Every accepted entry going stale at once
  is far more often a scan that stopped matching (a moved glob, a renamed directory) than a backlog paid off in
  one commit; regenerating the baseline says which. ``min_found`` puts an explicit floor on a scan.
* **Regeneration shrinks only.** It drops entries no longer found; adding one needs ``regenerate(found, grow=True)``
  or ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``, else it writes the removals and raises ``BaselineGrowthError`` naming the
  additions, so a regeneration cannot quietly accept a new violation.
* **Keys are repo-relative.** An absolute path in a committed baseline matches on exactly one machine and
  silently accepts everything everywhere else.

Usage from a repo's own check script::

    from py_ci_shared.baseline_ratchet import Baseline

    baseline = Baseline("file-size", directory="tool/meta/baselines", refresh_command="python tool/meta/regen_baselines.py")
    exit_code = baseline.enforce(found, label="check: file size", guidance="Split it, or accept it deliberately.")

and from its regeneration script::

    baseline.regenerate(found)
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from typing import Optional

from ._core.baseline import UNJUSTIFIED_MARKER, atomic_write_text, dump_json, is_unjustified, write_ratchet

DEFAULT_DIRECTORY = os.path.join("tool", "meta", "baselines")
DEFAULT_REFRESH_COMMAND = "python tool/meta/regen_baselines.py"


class Baseline:
    """One check's frozen set of accepted violations, stored as ``{key: note}``."""

    def __init__(
        self,
        name: str,
        *,
        directory: str = DEFAULT_DIRECTORY,
        refresh_command: str = DEFAULT_REFRESH_COMMAND,
    ) -> None:
        self.name = name
        self.directory = directory
        self.refresh_command = refresh_command
        self.path = os.path.join(directory, f"{name}.json")

    # ---- storage ----

    def load(self) -> dict[str, str]:
        """The accepted entries, or an empty mapping when nothing is recorded yet.

        Accepts both shapes the existing repositories wrote: ``{"accepted": {...}}`` and a bare
        ``{key: note}`` object, so a repo can adopt this module without rewriting its files.
        """
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and "accepted" in data:
            accepted = data["accepted"]
            return dict(accepted) if isinstance(accepted, dict) else {}
        return {k: v for k, v in data.items() if not k.startswith("_")} if isinstance(data, dict) else {}

    def save(self, accepted: Mapping[str, str]) -> None:
        os.makedirs(self.directory, exist_ok=True)
        # Atomic: an interrupted regeneration leaves the previous file, never half of one.
        atomic_write_text(self.path, self._render(accepted))

    def _render(self, accepted: Mapping[str, str]) -> str:
        payload = {
            "_comment": (
                f"Accepted pre-existing violations for {self.name}. Managed by "
                "py_ci_shared.baseline_ratchet; regenerate with "
                f"{self.refresh_command}. Hand-edit only to write a note saying why an entry is kept."
            ),
            # Sorted so a regeneration produces a reviewable diff rather than a reshuffle.
            "accepted": dict(sorted(accepted.items())),
        }
        return dump_json(payload)

    # ---- use ----

    def enforce(self, found: Mapping[str, str], *, label: str, guidance: str, min_found: int = 0, fail_on_empty_scan: bool = True) -> int:
        """Fail on any entry in ``found`` the baseline does not accept; return a process exit code.

        ``found`` maps a stable key - a repo-relative path, or ``path:line`` - to a short description of
        the violation, which is what a reader sees when the check fails. Also fails when ``found`` has fewer
        than *min_found* entries, and when it is empty while the baseline accepts entries (every entry stale at
        once): both read as a scan that stopped reaching the code. A caller whose scan can legitimately clear everything
        (a mutation run that kills every survivor) passes ``fail_on_empty_scan=False``.
        """
        accepted = self.load()
        if len(found) < min_found:
            print(f"{label}: the scan found {len(found)} entr(ies), fewer than its floor of {min_found} - it is not reaching the code.", file=sys.stderr)
            return 1
        if fail_on_empty_scan and not found and accepted:
            print(
                f"{label}: the scan found nothing, yet {self.path} accepts {len(accepted)} entr(ies). Either the scan stopped "
                f"matching, or the whole backlog was paid off - then run {self.refresh_command} to record that.",
                file=sys.stderr,
            )
            return 1
        new = {key: value for key, value in found.items() if key not in accepted}
        stale = [key for key in accepted if key not in found]

        if new:
            print(f"{label}: {len(new)} new violation(s):", file=sys.stderr)
            for key in sorted(new):
                print(f"  {key} - {new[key]}", file=sys.stderr)
            print("", file=sys.stderr)
            print(guidance, file=sys.stderr)
            print("", file=sys.stderr)
            print(
                f"If this one is a considered exception, add it to {self.path} with a note saying why.",
                file=sys.stderr,
            )
            return 1

        unjustified = sorted(key for key, note in accepted.items() if is_unjustified(note))
        if unjustified:
            # A refresh writes the marker as the note; an entry still carrying it was recorded, not accepted.
            print(f"{label}: {len(unjustified)} baseline entry(ies) in {self.path} still marked {UNJUSTIFIED_MARKER!r}:", file=sys.stderr)
            for key in unjustified:
                print(f"  {key}", file=sys.stderr)
            print("Replace each marker with the reason the entry is acceptable.", file=sys.stderr)
            return 1

        if stale:
            print(f"{label}: {len(stale)} baseline entry(ies) no longer violate the rule - " f"run {self.refresh_command} to prune:")
            for key in sorted(stale):
                print(f"  {key}")

        print(f"{label}: no new violations ({len(accepted)} accepted, baselined)")
        return 0

    def regenerate(self, found: Mapping[str, str], *, grow: Optional[bool] = None) -> None:
        """Freeze the current findings, keeping the note already written for any entry that survives.

        Shrink-only unless *grow* (or ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``): see ``_core.write_ratchet``."""
        previous = self.load()
        before = dict.fromkeys(previous, 1) if os.path.exists(self.path) else None

        def render(kept: dict[str, int]) -> str:
            return self._render({key: previous.get(key, found[key]) for key in kept})

        write_ratchet(self.path, dict.fromkeys(found, 1), gate=self.name, previous=before, render=render, grow=grow)


def run_rules(
    scans: Mapping[str, object],
    rules: Mapping[str, tuple[str, str]],
    *,
    directory: str = DEFAULT_DIRECTORY,
    refresh_command: str = DEFAULT_REFRESH_COMMAND,
    min_found: Optional[Mapping[str, int]] = None,
) -> int:
    """Run every named scan against its baseline; return the worst exit code.

    ``scans`` maps a rule name to a zero-argument callable returning ``{key: description}``; ``rules`` maps
    the same names to ``(label, guidance)``. Every rule runs even after one fails or raises, so a push reports
    every problem it has rather than the first. A scan with no rule is reported and fails too: it would
    otherwise never be enforced. ``min_found`` gives per-rule floors (see :meth:`Baseline.enforce`).
    """
    floors = dict(min_found or {})
    worst = 0
    for name in sorted(set(scans) - set(rules)):
        print(f"{name}: scan registered with no rule - it is never enforced; add a (label, guidance) rule for it", file=sys.stderr)
        worst = 1
    for name, (label, guidance) in rules.items():
        scan = scans.get(name)
        if scan is None:
            print(f"{label}: SKIPPED - no scan registered under {name!r}", file=sys.stderr)
            worst = max(worst, 1)
            continue
        try:
            found = scan()  # type: ignore[operator]
        except Exception as exc:  # one broken scan must not hide the others; it still fails the run
            print(f"{label}: the scan raised {type(exc).__name__}: {exc}", file=sys.stderr)
            worst = max(worst, 1)
            continue
        code = Baseline(name, directory=directory, refresh_command=refresh_command).enforce(
            found, label=label, guidance=guidance, min_found=floors.get(name, 0)
        )
        worst = max(worst, code)
    return worst
