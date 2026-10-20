"""Regression tests for the 2026-10-20 audit finding F5: a hung tool must not hang the hook, and F10: tools run in isolated mode."""

from __future__ import annotations

import subprocess

from py_ci_shared import advisory_warn, bandit_warn, mypy_gate


def _hang(*_args, **_kwargs):
    raise subprocess.TimeoutExpired(cmd="tool", timeout=1)


def test_a_hung_mypy_fails_the_gate_instead_of_hanging_it(monkeypatch, capsys):
    monkeypatch.setattr(mypy_gate.subprocess, "run", _hang)

    assert mypy_gate.main(["src"]) == 1

    assert "did not finish" in capsys.readouterr().out


def test_a_hung_warn_only_tool_is_skipped_and_never_blocks(monkeypatch, capsys):
    monkeypatch.setattr(bandit_warn.subprocess, "run", _hang)
    monkeypatch.setattr(bandit_warn, "resolve_src_files", lambda *_a, **_k: ["src/a.py"])

    assert bandit_warn.main(["src/a.py"]) == 0

    assert "skipped" in capsys.readouterr().err


def test_every_warn_only_call_carries_a_timeout(monkeypatch):
    seen: list[dict] = []

    def record(cmd, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(advisory_warn.subprocess, "run", record)
    monkeypatch.setattr(advisory_warn, "resolve_src_files", lambda *_a, **_k: ["src/a.py"], raising=False)

    advisory_warn.main(["src/a.py"])

    assert seen and all(kwargs.get("timeout") for kwargs in seen)


def test_the_child_interpreters_run_in_isolated_mode(monkeypatch):
    """F10: a repo-root directory named like the tool (ruff/, mypy/) must not shadow it when the gate runs with that repo as cwd."""
    commands: list[list[str]] = []

    def record(cmd, **kwargs):
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(advisory_warn.subprocess, "run", record)
    advisory_warn.main(["--src-path=src", "src/a.py"])

    assert commands and all(command[1] == "-I" for command in commands)
