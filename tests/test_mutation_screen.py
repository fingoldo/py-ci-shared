"""The two-phase mutation sweep: a screen under its own environment and test set, then the full set for the survivors only.

The mutation harness itself is replaced by a fake that records its calls, so these tests pin the orchestration (what each phase runs, what the
environment is, what a survivor means) without running pytest.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from py_ci_shared import _mutation_screen as screen
from py_ci_shared._mutation_model import Mutant, MutationHarnessError, MutationRun


def _mutant(line: int, tag: str) -> Mutant:
    """A mutant on *line* whose key is distinct per *tag*."""
    return Mutant(path=Path("pkg/mod.py"), line=line, column=0, description=f"op-{tag}", original_span="a", mutated_span="b", context=tag)


def _run(survivors=(), killed=0, mutants_run=0, inconclusive=()) -> MutationRun:
    """A run with the given survivors, kill count and mutant count."""
    return MutationRun(
        survivors=list(survivors),
        mutants_run=mutants_run,
        killed=killed,
        truncated=False,
        candidates_total=mutants_run,
        inconclusive=list(inconclusive),
    )


class _FakeFinder:
    """Stands in for ``find_surviving_mutants``: answers from scripted runs and records each call and the environment it saw."""

    def __init__(self, *answers: MutationRun) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, path, test_paths, repo_root, **kwargs) -> MutationRun:
        """Record the call, with the NUMBA_DISABLE_JIT value visible at that moment, and return the next scripted run."""
        self.calls.append({"test_paths": list(test_paths), "kwargs": kwargs, "numba": os.environ.get("NUMBA_DISABLE_JIT")})
        return self.answers.pop(0)


@pytest.fixture
def fake(monkeypatch):
    """Patch the harness with a fake whose answers the test scripts; return it."""

    def install(*answers: MutationRun) -> _FakeFinder:
        """Install a fake returning *answers* in order."""
        finder = _FakeFinder(*answers)
        monkeypatch.setattr(screen, "find_surviving_mutants", finder)
        return finder

    return install


def test_screen_runs_under_its_environment_and_the_confirmation_does_not(fake, monkeypatch):
    """The screen sees the screen environment; the confirmation sees the environment the caller had, which is not the screen's."""
    monkeypatch.delenv("NUMBA_DISABLE_JIT", raising=False)
    finder = fake(_run(survivors=[_mutant(5, "a")], mutants_run=3, killed=2), _run(mutants_run=1))
    screen.find_surviving_mutants_screened(
        "pkg/mod.py", ["full/test_a.py", "full/test_b.py"], ".", screen_suites=["full/test_a.py"], screen_env={"NUMBA_DISABLE_JIT": "1"}
    )
    assert finder.calls[0]["numba"] == "1"
    assert finder.calls[1]["numba"] is None
    assert "NUMBA_DISABLE_JIT" not in os.environ


def test_environment_is_restored_even_when_the_screen_raises(fake, monkeypatch):
    """A failing screen must not leave the screen environment set for the rest of the process."""
    monkeypatch.setenv("NUMBA_DISABLE_JIT", "0")

    def boom(path, test_paths, repo_root, **kwargs):
        """Fail the same way the harness does when the unmutated baseline does not pass."""
        raise MutationHarnessError("baseline does not pass")

    monkeypatch.setattr(screen, "find_surviving_mutants", boom)
    with pytest.raises(MutationHarnessError):
        screen.find_surviving_mutants_screened("pkg/mod.py", ["t.py"], ".", screen_suites=["t.py"], screen_env={"NUMBA_DISABLE_JIT": "1"})
    assert os.environ["NUMBA_DISABLE_JIT"] == "0"


def test_confirmation_runs_on_the_full_set_and_only_the_survivor_lines(fake):
    """Only the screen's survivors are re-judged, on the lines they sit on, against the full test set."""
    finder = fake(_run(survivors=[_mutant(5, "a"), _mutant(9, "b")], mutants_run=4, killed=2), _run(survivors=[_mutant(5, "a")], mutants_run=2))
    screen.find_surviving_mutants_screened(
        "pkg/mod.py", ["full/test_a.py", "full/test_b.py"], ".", screen_suites=["full/test_a.py"], screen_env={"NUMBA_DISABLE_JIT": "1"}
    )
    assert finder.calls[0]["test_paths"] == ["full/test_a.py"]
    assert finder.calls[1]["test_paths"] == ["full/test_a.py", "full/test_b.py"]
    assert finder.calls[1]["kwargs"]["lines"] == [range(5, 6), range(9, 10)]


def test_a_screen_kill_stands_and_a_confirmed_survivor_is_reported(fake):
    """A screen survivor that the full set kills is a kill; the one that survives there is the reported survivor."""
    survivor_a = _mutant(5, "a")
    survivor_b = _mutant(9, "b")
    fake(_run(survivors=[survivor_a, survivor_b], mutants_run=10, killed=8), _run(survivors=[survivor_a], mutants_run=2))
    result = screen.find_surviving_mutants_screened("pkg/mod.py", ["full.py"], ".", screen_suites=["screen.py"], screen_env={"NUMBA_DISABLE_JIT": "1"})
    assert [m.key for m in result.survivors] == [survivor_a.key]
    assert result.killed == 8 + 1
    assert result.mutants_run == 10


def test_no_screen_survivor_means_no_confirmation(fake):
    """When the screen kills everything there is nothing to re-judge, so the full set is not run at all."""
    finder = fake(_run(killed=4, mutants_run=4))
    result = screen.find_surviving_mutants_screened("pkg/mod.py", ["full.py"], ".", screen_suites=["screen.py"], screen_env={"NUMBA_DISABLE_JIT": "1"})
    assert len(finder.calls) == 1
    assert result.survivors == []
    assert result.killed == 4


def test_a_confirmation_that_regenerates_too_few_mutants_is_refused(fake):
    """A candidate that the confirmation did not re-run cannot be called killed or surviving, so the sweep refuses to answer."""
    fake(_run(survivors=[_mutant(5, "a"), _mutant(5, "b")], mutants_run=5, killed=3), _run(mutants_run=1))
    with pytest.raises(MutationHarnessError, match="neither re-judged nor killed"):
        screen.find_surviving_mutants_screened("pkg/mod.py", ["full.py"], ".", screen_suites=["screen.py"], screen_env={"NUMBA_DISABLE_JIT": "1"})


def test_a_cache_is_refused_because_it_cannot_record_the_environment(fake):
    """A cache entry does not say which environment produced it, so replaying one could hand a screen verdict to a full-set run."""
    fake()
    with pytest.raises(ValueError, match="use_cache"):
        screen.find_surviving_mutants_screened("pkg/mod.py", ["full.py"], ".", screen_suites=["s.py"], screen_env={}, use_cache=True)


def test_each_phase_writes_its_own_checkpoint(fake):
    """The screen's and the confirmation's verdicts are not interchangeable, so they never share a checkpoint file."""
    finder = fake(_run(survivors=[_mutant(5, "a")], mutants_run=1), _run(mutants_run=1))
    screen.find_surviving_mutants_screened(
        "pkg/mod.py", ["full.py"], ".", screen_suites=["s.py"], screen_env={"NUMBA_DISABLE_JIT": "1"}, checkpoint_path="out/ckpt.json"
    )
    assert finder.calls[0]["kwargs"]["checkpoint_path"] == str(Path("out/ckpt.screen.json"))
    assert finder.calls[1]["kwargs"]["checkpoint_path"] == str(Path("out/ckpt.confirm.json"))
