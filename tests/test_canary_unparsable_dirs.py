"""CLAUDE.md asks for a ``tests/canary/<gate>/unparsable/`` directory; a gate that reads text by regex (``parses=False``) has no syntax to break.

A gate missing the directory without declaring ``parses=False`` is a gate nobody fed a broken file; a ``parses=False`` gate that has the directory is
fine. Text-corpus gates need an undecodable-bytes variant instead (see the bom/undecodable cases in their own tests)."""

from __future__ import annotations

from pathlib import Path

from test_gate_teeth import CANARIES

CANARY_ROOT = Path(__file__).resolve().parent / "canary"


def test_only_a_text_gate_may_lack_the_unparsable_canary():
    lacking = sorted(d.name for d in CANARY_ROOT.iterdir() if d.is_dir() and not (d / "unparsable").is_dir())
    assert len(lacking) > 5, "the canary tree was not read"
    undeclared = [n for n in lacking if n in CANARIES and CANARIES[n].parses]
    assert not undeclared, f"{undeclared} have no unparsable/ canary dir but are declared as gates that parse (parses=True): add the dir or set parses=False"
