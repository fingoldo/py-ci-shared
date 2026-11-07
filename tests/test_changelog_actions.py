"""Every CHANGELOG entry that can turn a green consumer red or leave a stale baseline entry says what the consumer does about it.

CLAUDE.md: a change that alters what a gate reports gets an entry starting **Behaviour change** "with what to do". The entries of 1.23.x once
said only 'disappears' or 'reports new APIs', so a consumer whose baseline held a vanished finding went red on a stale entry with no instruction."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pytest

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
FIRST_ENFORCED = (1, 23, 0)
ACTION = re.compile(r"what to do|nothing to do", re.I)


def behaviour_change_units(text: str) -> list[tuple[str, int, str]]:
    """``(section heading, 1-based first line, unit text)`` for every run of lines (to the next blank line, bullet or heading) naming 'Behaviour change'."""
    lines = text.split("\n")
    out: list[tuple[str, int, str]] = []
    section, i = "", 0
    while i < len(lines):
        if lines[i].startswith("## "):
            section = lines[i][3:].strip()
        if "Behaviour change" not in lines[i]:
            i += 1
            continue
        end = i + 1
        while end < len(lines) and lines[end].strip() and not lines[end].startswith(("- ", "#")):
            end += 1
        out.append((section, i + 1, " ".join(lines[i:end])))
        i = end
    return out


def _section_key(section: str) -> Optional[tuple[int, ...]]:
    m = re.match(r"(?:Unreleased \(next: )?(\d+)\.(\d+)\.(\d+)", section)
    return tuple(int(g) for g in m.groups()) if m else None


def unenforced_free(units: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """The units of enforced sections (1.23.0 and later, including the Unreleased block) with no action for the consumer."""
    return [u for u in units if (_section_key(u[0]) or (0, 0, 0)) >= FIRST_ENFORCED and not ACTION.search(u[2])]


def test_every_behaviour_change_since_1_23_0_names_what_to_do():
    units = behaviour_change_units(CHANGELOG.read_text(encoding="utf-8"))
    assert len(units) >= 40, "the CHANGELOG was not read: the Behaviour change entries are gone"
    bare = unenforced_free(units)
    assert not bare, "Behaviour change entries with no 'What to do' / 'Nothing to do':\n" + "\n".join(f"  {s} line {n}: {t[:100]}" for s, n, t in bare)


@pytest.mark.parametrize(
    "text, bare",
    [
        ("## 1.23.5\n\n- **Behaviour change:** x now disappears.\n", 1),
        ("## 1.23.5\n\n- **Behaviour change:** x disappears. What to do: refresh the baseline.\n", 0),
        ("## 1.23.5\n\n- **Behaviour change:** x.\n  Nothing to do.\n", 0),
        ("## 1.22.5\n\n- **Behaviour change:** older entry, not enforced.\n", 0),
        ("## Unreleased (next: 1.23.10)\n\n- **Behaviour change:** y.\n- **Behaviour change:** z. What to do: nothing.\n", 1),
    ],
)
def test_the_checker_flags_exactly_the_entries_without_an_action(text: str, bare: int):
    assert len(unenforced_free(behaviour_change_units(text))) == bare
