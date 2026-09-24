"""Unit tests for the warn-only bandit pre-commit wrapper: argument parsing, path scoping, never blocking."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from py_ci_shared import bandit_warn
from py_ci_shared.bandit_warn import pop_option, select_src_files

SRC = str(Path(__file__).resolve().parents[1] / "src")


class _Recorder:
    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(bandit_warn.subprocess, "run", rec)
    monkeypatch.delenv("PY_CI_SHARED_SRC_PATH", raising=False)
    return rec


def test_a_trailing_src_path_does_not_crash_the_hook():
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop("PY_CI_SHARED_SRC_PATH", None)
    proc = subprocess.run([sys.executable, "-m", "py_ci_shared.bandit_warn", "a.py", "--src-path"], capture_output=True, text=True, env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "--src-path was given without a value" in proc.stderr
    assert "IndexError" not in proc.stderr


def test_pop_option_reads_both_forms_and_a_missing_value():
    assert pop_option(["a.py", "--src-path", "src/p", "b.py"], "--src-path") == ("src/p", ["a.py", "b.py"])
    assert pop_option(["a.py", "--src-path=src/p"], "--src-path") == ("src/p", ["a.py"])
    assert pop_option(["a.py", "--src-path"], "--src-path") == (None, ["a.py"])


def test_equals_form_scopes_the_scan(recorder):
    assert bandit_warn.main(["src/pkg/a.py", "tests/t.py", "--src-path=src/pkg"]) == 0
    assert recorder.calls and recorder.calls[0][-1:] == ["src/pkg/a.py"]


def test_a_backslash_src_path_matches_and_a_sibling_prefix_does_not():
    assert select_src_files(["src\\pkg\\a.py", "src/pkg/b.py"], "src\\pkg") == ["src\\pkg\\a.py", "src/pkg/b.py"]
    assert select_src_files(["src/pkg_other/a.py", "src/pkg/x.txt"], "src/pkg") == []
    assert select_src_files(["./src/pkg/a.py"], "src/pkg/") == ["./src/pkg/a.py"]
    assert select_src_files(["a.py", "sub/b.py"], ".") == ["a.py", "sub/b.py"]


def test_a_windows_src_path_runs_bandit(recorder):
    bandit_warn.main(["--src-path", "src\\pkg", "src\\pkg\\a.py"])
    assert len(recorder.calls) == 1 and recorder.calls[0][-1] == "src\\pkg\\a.py"


def test_staged_files_outside_the_src_path_warn_that_nothing_was_scanned(recorder, capsys):
    assert bandit_warn.main(["--src-path", "src/pkg", "src/pkg_other/a.py"]) == 0
    assert recorder.calls == []
    assert "none under --src-path" in capsys.readouterr().err
    assert bandit_warn.main(["--src-path", "src/pkg", "README.md"]) == 0
    assert "none under" not in capsys.readouterr().err


def test_findings_warn_but_never_block(monkeypatch, capsys):
    monkeypatch.setattr(bandit_warn.subprocess, "run", _Recorder(returncode=1))
    assert bandit_warn.main(["--src-path", "src", "src/a.py"]) == 0
    assert "WARNINGS ONLY" in capsys.readouterr().err
