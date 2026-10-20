"""The on-disk cache of PASSED gate results: a repeat on an unchanged tree is not re-run, and nothing else is ever replayed."""

from __future__ import annotations

import pytest

from py_ci_shared._core import gate_cache, runner
from py_ci_shared._core.config import GateRun, RepoConfig


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with one source file, a private cache directory, and a gate that records each call."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.delenv(gate_cache.NO_CACHE_ENV, raising=False)
    calls: list[str] = []
    behaviour = {"outcome": "pass"}

    def fake_gate(**_kwargs):
        calls.append("call")
        if behaviour["outcome"] == "fail":
            raise AssertionError("seeded failure")

    monkeypatch.setattr(runner, "resolve_gate", lambda run: (fake_gate, 0.0))
    run = GateRun(name="fake", module="fake_module", entry=None, kwargs={})
    config = RepoConfig(repo_root=root, gates=(run,))
    return config, run, calls, behaviour


def test_a_repeat_on_an_unchanged_tree_is_not_re_run(repo):
    config, run, calls, _ = repo
    first = runner.run_gate(config, run)
    second = runner.run_gate(config, run)
    assert first.status == second.status == runner.PASSED
    assert calls == ["call"]
    assert "cached" in second.message


def test_a_changed_file_is_a_miss(repo):
    config, run, calls, _ = repo
    runner.run_gate(config, run)
    (config.repo_root / "module.py").write_text("X = 2\n", encoding="utf-8")
    runner.run_gate(config, run)
    assert calls == ["call", "call"]


def test_a_failure_is_never_replayed(repo):
    config, run, calls, behaviour = repo
    behaviour["outcome"] = "fail"
    assert runner.run_gate(config, run).status == runner.FAILED
    assert runner.run_gate(config, run).status == runner.FAILED
    assert calls == ["call", "call"]


def test_refresh_always_runs_the_gate(repo):
    config, run, calls, _ = repo
    runner.run_gate(config, run)
    runner.run_gate(config, run, refresh=True)
    assert calls == ["call", "call"]


def test_the_switch_turns_the_cache_off(repo, monkeypatch):
    config, run, calls, _ = repo
    monkeypatch.setenv(gate_cache.NO_CACHE_ENV, "1")
    runner.run_gate(config, run)
    runner.run_gate(config, run)
    assert calls == ["call", "call"]


def test_a_network_gate_is_never_cached(repo):
    config, _, calls, _ = repo
    run = GateRun(name="fake", module="fake_module", entry=None, kwargs={"network": True})
    runner.run_gate(config, run)
    runner.run_gate(config, run)
    assert calls == ["call", "call"]


def test_an_unwritable_cache_costs_only_the_next_run(repo, monkeypatch):
    config, run, calls, _ = repo

    blocker = config.repo_root / "not_a_directory"
    blocker.write_text("a file where the cache directory should be\n", encoding="utf-8")
    monkeypatch.setattr(gate_cache, "_store_path", lambda _root: blocker / "cache.json")
    assert runner.run_gate(config, run).status == runner.PASSED
    assert calls == ["call"]
