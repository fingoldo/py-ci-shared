"""A stale baseline entry or surplus count fails every caller that tests ``BaselineOutcome.ok`` (audit 42-F4, 42-F5)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from py_ci_shared import mutation_teeth
from py_ci_shared._core import Baseline, Finding


def _f(msg: str) -> Finding:
    return Finding(rule="r", path="a.py", line=1, message=msg)


def test_ok_is_false_for_a_stale_entry_but_blocking_stays_false(tmp_path):
    b = Baseline(tmp_path / "b.json")
    b.enforce([_f("x"), _f("y")], refresh=True, grow=True)
    outcome = b.enforce([_f("x")])
    assert outcome.stale == ["r::a.py::y"]
    assert not outcome.ok and not outcome.blocking


def test_a_new_finding_is_blocking(tmp_path):
    b = Baseline(tmp_path / "b.json")
    b.enforce([_f("x")], refresh=True, grow=True)
    outcome = b.enforce([_f("x"), _f("z")])
    assert outcome.blocking and not outcome.ok


def test_raise_for_pytest_can_still_tolerate_stale_while_migrating(tmp_path):
    b = Baseline(tmp_path / "b.json")
    b.enforce([_f("x"), _f("y")], refresh=True, grow=True)
    outcome = b.enforce([_f("x")])
    outcome.raise_for_pytest(fail_on_stale=False)
    with pytest.raises(pytest.fail.Exception):
        outcome.raise_for_pytest()


def _fake_run(monkeypatch, survivors):
    fake = SimpleNamespace(survivors=survivors, inconclusive=[], coverage_gaps=[], truncated=False, from_cache=False, summary=lambda: "summary")
    monkeypatch.setattr(mutation_teeth, "find_surviving_mutants", lambda *a, **k: fake)


def test_mutation_teeth_fails_on_a_stale_survivor_entry(tmp_path, monkeypatch):
    b = Baseline(tmp_path / "b.json")
    b.enforce(["old-survivor"], refresh=True, grow=True)
    _fake_run(monkeypatch, [])
    with pytest.raises(AssertionError, match="old-survivor"):
        mutation_teeth.assert_no_new_surviving_mutant("m.py", [], tmp_path, b)


def test_mutation_teeth_passes_when_the_baseline_matches_exactly(tmp_path, monkeypatch):
    b = Baseline(tmp_path / "b.json")
    b.enforce([], refresh=True, grow=True)
    _fake_run(monkeypatch, [])
    mutation_teeth.assert_no_new_surviving_mutant("m.py", [], tmp_path, b)
