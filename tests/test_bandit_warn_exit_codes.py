"""bandit's exit codes: 1 is findings (the banner), any other failure is bandit failing and is named as such (audit 49-F12)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from py_ci_shared import bandit_warn


@pytest.fixture
def run_with(monkeypatch, tmp_path):
    source = tmp_path / "m.py"
    source.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("PY_CI_SHARED_SRC_PATH", str(tmp_path))
    monkeypatch.setattr(bandit_warn, "_tool_installed", lambda module: True)

    def _run(code: int) -> None:
        monkeypatch.setattr(bandit_warn.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code))

    return _run, str(source)


def test_findings_print_the_warnings_only_banner(run_with, capsys) -> None:
    run, source = run_with
    run(1)
    assert bandit_warn.main([source]) == 0
    err = capsys.readouterr().err
    assert "WARNINGS ONLY" in err and "bandit itself failed" not in err


def test_a_clean_run_prints_nothing(run_with, capsys) -> None:
    run, source = run_with
    run(0)
    assert bandit_warn.main([source]) == 0
    assert capsys.readouterr().err == ""


def test_a_bandit_failure_is_named_not_reported_as_findings(run_with, capsys) -> None:
    """Negative control (49-F12): exit 2 is bandit failing; the old code printed the findings banner for it."""
    run, source = run_with
    run(2)
    assert bandit_warn.main([source]) == 0
    err = capsys.readouterr().err
    assert "bandit itself failed (exit 2)" in err
    assert "WARNINGS ONLY" not in err
