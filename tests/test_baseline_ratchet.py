"""Behavioural tests for the shared ratchet: it must fail on new debt, report paid debt, and never lose
the human note that says why an entry is kept."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.baseline_ratchet import Baseline, run_rules


def make(tmp_path: Path, name: str = "rule") -> Baseline:
    return Baseline(name, directory=str(tmp_path), refresh_command="python tool/meta/regen_baselines.py")


def test_no_baseline_accepts_nothing_and_a_finding_fails(tmp_path: Path, capsys) -> None:
    code = make(tmp_path).enforce({"lib/a.dart": "1528 lines"}, label="check: size", guidance="Split it.")
    err = capsys.readouterr().err
    assert code == 1
    assert "lib/a.dart - 1528 lines" in err
    assert "Split it." in err
    assert str(tmp_path) in err, "the message must name the file to edit for a considered exception"


def test_an_accepted_finding_passes_and_the_count_is_reported(tmp_path: Path, capsys) -> None:
    baseline = make(tmp_path)
    baseline.save({"lib/a.dart": "deliberate: one screen, split would spread its state"})
    code = baseline.enforce({"lib/a.dart": "1528 lines"}, label="check: size", guidance="Split it.")
    assert code == 0
    assert "no new violations (1 accepted, baselined)" in capsys.readouterr().out


def test_paid_debt_is_reported_but_does_not_fail(tmp_path: Path, capsys) -> None:
    baseline = make(tmp_path)
    baseline.save({"lib/gone.dart": "deliberate: for now"})
    code = baseline.enforce({}, label="check: size", guidance="Split it.")
    out = capsys.readouterr().out
    assert code == 0, "failing a push for fixing something would be perverse"
    assert "lib/gone.dart" in out
    assert "regen_baselines.py to prune" in out


def test_regeneration_keeps_the_note_and_drops_what_is_fixed(tmp_path: Path) -> None:
    baseline = make(tmp_path)
    baseline.save({"lib/a.dart": "deliberate: the reason someone wrote", "lib/gone.dart": "old"})
    baseline.regenerate({"lib/a.dart": "1528 lines", "lib/b.dart": "900 lines"})
    accepted = baseline.load()
    assert accepted["lib/a.dart"] == "deliberate: the reason someone wrote"
    assert accepted["lib/b.dart"] == "900 lines"
    assert "lib/gone.dart" not in accepted


def test_the_file_is_sorted_and_carries_its_own_instructions(tmp_path: Path) -> None:
    baseline = make(tmp_path)
    baseline.regenerate({"z.dart": "z", "a.dart": "a"})
    payload = json.loads((tmp_path / "rule.json").read_text(encoding="utf-8"))
    assert list(payload["accepted"]) == ["a.dart", "z.dart"], "sorted, so a regeneration diffs reviewably"
    assert "regen_baselines.py" in payload["_comment"]


def test_a_bare_mapping_is_read_too_so_a_repo_can_adopt_without_rewriting(tmp_path: Path) -> None:
    (tmp_path / "rule.json").write_text(
        json.dumps({"_comment": "older shape", "lib/a.dart": "deliberate: kept"}), encoding="utf-8"
    )
    assert make(tmp_path).load() == {"lib/a.dart": "deliberate: kept"}


def test_run_rules_runs_every_rule_and_returns_the_worst_code(tmp_path: Path, capsys) -> None:
    scans = {
        "clean": lambda: {},
        "dirty": lambda: {"lib/b.dart": "new"},
    }
    rules = {
        "clean": ("check: clean", "guidance"),
        "dirty": ("check: dirty", "guidance"),
    }
    code = run_rules(scans, rules, directory=str(tmp_path))
    captured = capsys.readouterr()
    assert code == 1
    assert "check: clean: no new violations" in captured.out, "a later failure must not hide an earlier pass"
    assert "lib/b.dart" in captured.err


def test_a_rule_with_no_scan_fails_rather_than_reading_as_a_pass(tmp_path: Path, capsys) -> None:
    code = run_rules({}, {"missing": ("check: missing", "guidance")}, directory=str(tmp_path))
    assert code == 1
    assert "SKIPPED" in capsys.readouterr().err


@pytest.mark.parametrize("absolute", [r"C:\Users\someone\repo\lib\a.dart", "/home/someone/repo/lib/a.dart"])
def test_an_absolute_key_is_storable_but_visible(tmp_path: Path, absolute: str) -> None:
    """The ratchet does not police key shape - `baseline_hygiene` does - but the key must survive a
    round trip, or that check would never see it."""
    baseline = make(tmp_path)
    baseline.regenerate({absolute: "matched on one machine only"})
    assert absolute in baseline.load()
