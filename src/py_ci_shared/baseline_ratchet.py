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
        with open(self.path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and "accepted" in data:
            accepted = data["accepted"]
            return dict(accepted) if isinstance(accepted, dict) else {}
        return {k: v for k, v in data.items() if not k.startswith("_")} if isinstance(data, dict) else {}

    def save(self, accepted: Mapping[str, str]) -> None:
        os.makedirs(self.directory, exist_ok=True)
        payload = {
            "_comment": (
                f"Accepted pre-existing violations for {self.name}. Managed by "
                "py_ci_shared.baseline_ratchet; regenerate with "
                f"{self.refresh_command}. Hand-edit only to write a note saying why an entry is kept."
            ),
            # Sorted so a regeneration produces a reviewable diff rather than a reshuffle.
            "accepted": dict(sorted(accepted.items())),
        }
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")

    # ---- use ----

    def enforce(self, found: Mapping[str, str], *, label: str, guidance: str) -> int:
        """Fail on any entry in ``found`` the baseline does not accept; return a process exit code.

        ``found`` maps a stable key - a repo-relative path, or ``path:line`` - to a short description of
        the violation, which is what a reader sees when the check fails.
        """
        accepted = self.load()
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

        if stale:
            print(
                f"{label}: {len(stale)} baseline entry(ies) no longer violate the rule - "
                f"run {self.refresh_command} to prune:"
            )
            for key in sorted(stale):
                print(f"  {key}")

        print(f"{label}: no new violations ({len(accepted)} accepted, baselined)")
        return 0

    def regenerate(self, found: Mapping[str, str]) -> None:
        """Freeze the current findings, keeping the note already written for any entry that survives."""
        previous = self.load()
        self.save({key: previous.get(key, value) for key, value in found.items()})


def run_rules(
    scans: Mapping[str, object],
    rules: Mapping[str, tuple[str, str]],
    *,
    directory: str = DEFAULT_DIRECTORY,
    refresh_command: str = DEFAULT_REFRESH_COMMAND,
) -> int:
    """Run every named scan against its baseline; return the worst exit code.

    ``scans`` maps a rule name to a zero-argument callable returning ``{key: description}``; ``rules`` maps
    the same names to ``(label, guidance)``. Every rule runs even after one fails, so a push reports every
    problem it has rather than the first.
    """
    worst = 0
    for name, (label, guidance) in rules.items():
        scan = scans.get(name)
        if scan is None:
            print(f"{label}: SKIPPED - no scan registered under {name!r}", file=sys.stderr)
            worst = max(worst, 1)
            continue
        found = scan()  # type: ignore[operator]
        code = Baseline(name, directory=directory, refresh_command=refresh_command).enforce(
            found, label=label, guidance=guidance
        )
        worst = max(worst, code)
    return worst
