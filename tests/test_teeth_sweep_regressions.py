"""Regression tests for ``teeth_sweep``: a run that did not happen is never a verdict, and every case gets one."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from py_ci_shared import teeth_sweep
from py_ci_shared.teeth_sweep import _apply, outcome_of


class TestTheExitCodeDecides:
    def test_a_usage_error_is_not_a_green_suite(self):
        summary, failed, ran = outcome_of(4, "", "ERROR: usage: pytest [options]\npytest: error: unrecognized arguments: -n")
        assert ran is False and failed and "DID NOT RUN" in summary

    @pytest.mark.parametrize("code", [2, 3, 5])
    def test_every_non_verdict_code_is_not_a_run(self, code):
        assert outcome_of(code, "no tests ran in 0.01s")[2] is False

    def test_a_clean_run_is_green(self):
        assert outcome_of(0, "3 passed in 0.10s") == ("3 passed in 0.10s", [], True)

    def test_exit_1_without_a_named_test_still_fails(self):
        _summary, failed, ran = outcome_of(1, "1 failed in 0.10s")
        assert ran is True and len(failed) == 1

    def test_named_failures_are_kept(self):
        _summary, failed, ran = outcome_of(1, "FAILED tests/test_x.py::test_y - assert 0\n1 failed in 0.1s")
        assert ran is True and failed == ["FAILED tests/test_x.py::test_y - assert 0"]


class TestPluginFlagsOnlyWhenInstalled:
    def test_no_plugin_no_flag(self, monkeypatch):
        monkeypatch.setattr(teeth_sweep.importlib.util, "find_spec", lambda name: None)
        cmd = teeth_sweep._suite_command(4)
        assert "-n" not in cmd and "--no-cov" not in cmd and "no:anyio" not in cmd

    def test_installed_plugins_get_their_flags(self, monkeypatch):
        monkeypatch.setattr(teeth_sweep.importlib.util, "find_spec", lambda name: object())
        cmd = teeth_sweep._suite_command(4)
        assert cmd[cmd.index("-n") + 1] == "4" and "--no-cov" in cmd and "no:anyio" in cmd

    def test_a_real_run_without_xdist_is_not_read_as_green(self, tmp_path, monkeypatch):
        """End to end against a real pytest: an unknown option exits 4, and the sweep must say so."""
        (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        monkeypatch.setattr(teeth_sweep, "_suite_command", lambda jobs: [sys.executable, "-m", "pytest", "-q", "--definitely-not-an-option"])
        summary, failed = teeth_sweep._run_suite(tmp_path, 1)
        assert "DID NOT RUN" in summary and teeth_sweep._did_not_run(failed)


class TestTimeoutsKillTheTree:
    def test_the_suite_runs_under_a_tree_killing_deadline(self, tmp_path, monkeypatch):
        seen = {}

        def fake(cmd, *, cwd, env, timeout):
            seen["timeout"] = timeout
            return None

        monkeypatch.setattr(teeth_sweep, "run_with_deadline", fake)
        summary, failed = teeth_sweep._run_suite(tmp_path, 2)
        assert seen["timeout"] == 900 and failed == ["<suite timed out>"] and "TIMED OUT" in summary


def _cases(tmp_path: pathlib.Path, cases: list) -> pathlib.Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


class TestOneBadCaseDoesNotAbortTheSweep:
    def test_the_next_case_still_runs(self, tmp_path, monkeypatch, capsys):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("X = 1\n", encoding="utf-8")
        calls = []

        def fake_suite(repo_, jobs):
            calls.append((repo_ / "a.py").read_text(encoding="utf-8"))
            if len(calls) == 1:
                return "1 passed", []
            return "1 failed", ["FAILED t.py::test"]

        monkeypatch.setattr(teeth_sweep, "_run_suite", fake_suite)
        cases = _cases(
            tmp_path, [{"id": "BAD", "what": "no file key", "old": "x", "new": "y"}, {"id": "OK", "what": "w", "file": "a.py", "old": "X = 1", "new": "X = 2"}]
        )
        code = teeth_sweep.main(["--repo", str(repo), "--cases", str(cases)])
        out = capsys.readouterr().out

        assert code == 1
        assert calls == ["X = 1\n", "X = 2\n"], "the good case never ran"
        assert "ERRORED" in out and "BAD" in out
        assert (repo / "a.py").read_text(encoding="utf-8") == "X = 1\n"

    def test_a_case_whose_suite_did_not_run_is_errored_not_toothless(self, tmp_path, monkeypatch, capsys):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("X = 1\n", encoding="utf-8")
        results = iter([("1 passed", []), ("PYTEST DID NOT RUN (exit 4)", ["<pytest did not run (exit 4)>"])])
        monkeypatch.setattr(teeth_sweep, "_run_suite", lambda repo_, jobs: next(results))
        cases = _cases(tmp_path, [{"id": "C1", "what": "w", "file": "a.py", "old": "X = 1", "new": "X = 2"}])
        assert teeth_sweep.main(["--repo", str(repo), "--cases", str(cases)]) == 1
        out = capsys.readouterr().out
        assert "NO TEETH" not in out and "ERRORED" in out

    def test_a_latin_1_target_is_not_applied_rather_than_crashing(self, tmp_path):
        target = tmp_path / "latin.py"
        target.write_bytes("x = 'café'\n".encode("latin-1"))
        why = _apply(target, "x", "y")
        assert why is not None and "cannot read" in why
        assert target.read_bytes() == "x = 'café'\n".encode("latin-1")


class TestTheReadBackIsExact:
    def test_an_empty_replacement_is_verified(self, tmp_path):
        p = tmp_path / "m.py"
        p.write_bytes(b"x = 1\ny = 2\n")
        assert _apply(p, "y = 2\n", "") is None
        assert p.read_bytes() == b"x = 1\n"

    def test_a_write_that_did_not_land_is_caught_even_when_the_replacement_already_exists(self, tmp_path, monkeypatch):
        p = tmp_path / "m.py"
        p.write_bytes(b"x = 1\nx = 2\n")
        monkeypatch.setattr(pathlib.Path, "write_bytes", lambda self, data: len(data))
        assert _apply(p, "x = 2", "x = 1") == "substitution did not survive the write"

    def test_a_no_op_substitution_is_refused(self, tmp_path):
        p = tmp_path / "m.py"
        p.write_bytes(b"x = 1\n")
        assert "changes nothing" in (_apply(p, "x = 1", "x = 1") or "")


class TestCrlfCases:
    def test_a_crlf_needle_matches_a_crlf_file(self, tmp_path):
        p = tmp_path / "crlf.py"
        p.write_bytes(b"def f():\r\n    return 1\r\n")
        assert _apply(p, "def f():\r\n    return 1", "def f():\r\n    return 2") is None
        assert p.read_bytes() == b"def f():\r\n    return 2\r\n"

    def test_a_crlf_needle_matches_an_lf_file(self, tmp_path):
        p = tmp_path / "lf.py"
        p.write_bytes(b"def f():\n    return 1\n")
        assert _apply(p, "def f():\r\n    return 1", "def f():\r\n    return 2") is None
        assert p.read_bytes() == b"def f():\n    return 2\n"


def test_the_module_runs_as_a_script(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "py_ci_shared.teeth_sweep", "--help"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0 and "--cases" in proc.stdout
