"""The sweep names a consumer baseline that a strict ratchet turns red, and leaves the consumer untouched (audit 42-F4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core.git import run_git
from py_ci_shared import stale_baseline_sweep
from py_ci_shared.stale_baseline_sweep import main, render_markdown, sweep_repo

_TEST = """
from py_ci_shared.baseline_ratchet import Baseline


def test_gate():
    b = Baseline("size", directory="baselines")
    assert b.enforce({"a.py": "x"}, label="size", guidance="g") == 0
"""


def _consumer(tmp_path: Path, accepted: dict) -> Path:
    repo = tmp_path / "consumer"
    (repo / "tests").mkdir(parents=True)
    (repo / "baselines").mkdir()
    (repo / "tests" / "test_gate.py").write_text(_TEST, encoding="utf-8")
    (repo / "baselines" / "size.json").write_text(json.dumps({"accepted": accepted}), encoding="utf-8")
    run_git(repo, "init", "-q", check=True)
    run_git(repo, "add", "-A", check=True)
    run_git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init", check=True)
    return repo


# A child pytest start-up costs seconds, so each consumer is swept once per module.


@pytest.fixture(scope="module")
def stale(tmp_path_factory):
    repo = _consumer(tmp_path_factory.mktemp("stale"), {"a.py": "n", "gone.py": "n", "gone2.py": "n"})
    return sweep_repo(repo)


def test_a_stale_entry_is_reported_with_its_count_and_key(stale):
    assert [(r.api, r.gate, r.stale) for r in stale.rows] == [("ratchet", "size", 2)]
    assert set(stale.rows[0].keys) == {"gone.py", "gone2.py"}
    assert stale.rows[0].baseline == "baselines/size.json"
    assert "| consumer |" in render_markdown([stale]) and "yes" in render_markdown([stale])


def test_a_clean_consumer_has_no_rows_and_the_checkout_is_not_written(tmp_path):
    repo = _consumer(tmp_path, {"a.py": "n"})
    before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.parts)
    result = sweep_repo(repo)
    assert result.rows == [] and result.test_files == 1 and "1 passed" in result.pytest_summary
    assert sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.parts) == before


def test_a_repo_that_is_not_a_checkout_is_not_measured_not_clean(tmp_path):
    result = sweep_repo(tmp_path)
    assert result.problem and "not measured" in render_markdown([result])


def test_the_cli_exit_code_follows_fail_on_stale(stale, monkeypatch, tmp_path):
    monkeypatch.setattr(stale_baseline_sweep, "sweep_repo", lambda *a, **k: stale)
    assert main([str(tmp_path)]) == 0
    assert main([str(tmp_path), "--fail-on-stale"]) == 1
