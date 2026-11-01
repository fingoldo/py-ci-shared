"""A sweep killed midway resumes from its checkpoint, judging only the mutants it had not reached.

The resumed run must reach the verdicts a clean run reaches, in the same order, and must not spend a judgement on a
mutant the checkpoint already holds: those are replayed onto the current run's objects, which the merge needs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared import mutation_teeth


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    open(repo / "m.py", "w", encoding="utf-8", newline="").write(
        "def clamp(n, cap):\n" "    if n > cap:\n" "        return cap\n" "    return n\n" "\n" "\n" "def unchecked(n):\n" "    return n * 2 + 1\n"
    )
    open(repo / "tests" / "test_m.py", "w", encoding="utf-8", newline="").write(
        "from m import clamp\n" "\n" "\n" "def test_clamp():\n" "    assert clamp(5, 3) == 3\n" "    assert clamp(2, 3) == 2\n" "    assert clamp(3, 3) == 3\n"
    )
    return repo


def _sweep(repo: Path, checkpoint: Path | None):
    return mutation_teeth.find_surviving_mutants(
        "m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False, use_warm_worker=False, checkpoint_path=checkpoint
    )


def _count_judgements(monkeypatch, stop_after: int | None = None) -> list[int]:
    """Count calls to the judge; optionally raise on the call after *stop_after*, as a killed process would stop."""
    real = mutation_teeth._judge
    calls: list[int] = []

    def counting(*args, **kwargs):
        if stop_after is not None and len(calls) == stop_after:
            raise KeyboardInterrupt("simulated kill")
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(mutation_teeth, "_judge", counting)
    return calls


def test_a_resumed_sweep_reaches_the_clean_verdicts_in_order(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    clean_calls = _count_judgements(monkeypatch)
    clean = _sweep(repo, None)
    assert clean.survivors, "the fixture must leave a survivor, or resuming proves nothing"
    total = len(clean_calls)
    assert total >= 3, "the fixture must have more than two judgements, or the interruption lands on the last one"

    checkpoint = tmp_path / "checkpoint.jsonl"
    monkeypatch.undo()
    _count_judgements(monkeypatch, stop_after=2)
    with pytest.raises(KeyboardInterrupt):
        _sweep(repo, checkpoint)
    recorded = [json.loads(line) for line in checkpoint.read_text(encoding="utf-8").splitlines()]
    assert len(recorded) == 2

    monkeypatch.undo()
    resumed_calls = _count_judgements(monkeypatch)
    resumed = _sweep(repo, checkpoint)

    assert len(resumed_calls) == total - 2, "the two recorded mutants were judged again"
    assert resumed.killed == clean.killed
    assert resumed.mutants_run == clean.mutants_run
    assert [m.key for m in resumed.survivors] == [m.key for m in clean.survivors]


def test_a_checkpoint_from_other_code_is_ignored(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    checkpoint = tmp_path / "checkpoint.jsonl"
    _count_judgements(monkeypatch, stop_after=1)
    with pytest.raises(KeyboardInterrupt):
        _sweep(repo, checkpoint)

    (repo / "m.py").write_text((repo / "m.py").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    monkeypatch.undo()
    calls = _count_judgements(monkeypatch)
    _sweep(repo, checkpoint)
    assert calls, "a checkpoint written for different code must not be replayed"


def test_a_torn_last_line_is_skipped_not_fatal(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    checkpoint = tmp_path / "checkpoint.jsonl"
    _count_judgements(monkeypatch, stop_after=2)
    with pytest.raises(KeyboardInterrupt):
        _sweep(repo, checkpoint)
    with open(checkpoint, "a", encoding="utf-8") as handle:
        handle.write('{"key": "trunc')

    monkeypatch.undo()
    outcome = _sweep(repo, checkpoint)
    assert outcome.mutants_run > 0
