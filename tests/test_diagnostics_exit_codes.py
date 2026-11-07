"""Exit-code contracts for hook/script entry points (audit 49: a gate that cannot fail, a missing tool read as a finding)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from py_ci_shared import advisory_warn, bandit_warn, format_warn, import_cycles, teeth_sweep

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run_module(module: str, *args: str, cwd: Path) -> "subprocess.CompletedProcess[str]":
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.run([sys.executable, "-m", module, *args], cwd=str(cwd), capture_output=True, text=True, env=env, check=False, timeout=120)


def _cycle_pkg(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg import b\n")
    (pkg / "b.py").write_text("from pkg import a\n")


def test_import_cycles_script_exits_1_on_a_cycle_with_repo_relative_paths(tmp_path):
    _cycle_pkg(tmp_path)
    proc = _run_module("py_ci_shared.import_cycles", "pkg", "pkg", cwd=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "pkg/a.py" in proc.stdout.replace("\\", "/")


def test_import_cycles_script_exits_0_on_a_clean_tree(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("X = 1\n")
    assert _run_module("py_ci_shared.import_cycles", "pkg", "pkg", cwd=tmp_path).returncode == 0


def test_import_cycles_script_exits_2_without_traceback_on_a_missing_root(tmp_path):
    proc = _run_module("py_ci_shared.import_cycles", "nonexistent", cwd=tmp_path)
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "import_cycles" in proc.stderr


def test_import_cycles_script_exits_1_on_unparsed_files(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bad.py").write_text("def (:\n")
    assert _run_module("py_ci_shared.import_cycles", "pkg", "pkg", cwd=tmp_path).returncode == 1


def test_import_cycles_main_is_callable_and_returns_int(tmp_path, capsys):
    _cycle_pkg(tmp_path)
    assert import_cycles.main([str(tmp_path / "pkg"), "pkg"]) == 1


def _stub_run(monkeypatch, module, returncode: int, calls: list):
    def fake(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, returncode)

    monkeypatch.setattr(module.subprocess, "run", fake)


def test_bandit_warn_reports_a_missing_tool_not_findings(monkeypatch, capsys):
    monkeypatch.setattr(bandit_warn, "_tool_installed", lambda name: False)
    monkeypatch.delenv("PY_CI_SHARED_SRC_PATH", raising=False)
    calls: list = []
    _stub_run(monkeypatch, bandit_warn, 1, calls)
    assert bandit_warn.main(["--src-path", "src", "src/a.py"]) == 0
    err = capsys.readouterr().err
    assert "not installed" in err
    assert "WARNINGS ONLY" not in err
    assert calls == []


def test_bandit_warn_still_warns_on_real_findings(monkeypatch, capsys):
    monkeypatch.setattr(bandit_warn, "_tool_installed", lambda name: True)
    monkeypatch.delenv("PY_CI_SHARED_SRC_PATH", raising=False)
    _stub_run(monkeypatch, bandit_warn, 1, [])
    assert bandit_warn.main(["--src-path", "src", "src/a.py"]) == 0
    assert "WARNINGS ONLY" in capsys.readouterr().err


def test_advisory_warn_reports_missing_tools_not_findings(monkeypatch, capsys):
    monkeypatch.setattr(advisory_warn, "_tool_installed", lambda name: False)
    monkeypatch.delenv("PY_CI_SHARED_SRC_PATH", raising=False)
    calls: list = []
    _stub_run(monkeypatch, advisory_warn, 1, calls)
    assert advisory_warn.main(["--src-path", "src", "src/a.py"]) == 0
    err = capsys.readouterr().err
    assert "ruff is not installed" in err and "pip_audit is not installed" in err
    assert "WARNINGS ONLY" not in err
    assert calls == []


def test_format_warn_reports_a_missing_tool_not_findings(monkeypatch, capsys):
    monkeypatch.setattr(format_warn, "_tool_installed", lambda name: False)
    calls: list = []
    _stub_run(monkeypatch, format_warn, 1, calls)
    monkeypatch.setattr(sys, "argv", ["format_warn", "a.py"])
    with pytest.raises(SystemExit) as exc:
        format_warn.main()
        sys.exit(0)
    assert exc.value.code == 0
    err = capsys.readouterr().err
    assert "ruff is not installed" in err
    assert "WARNINGS ONLY" not in err


def test_teeth_sweep_unreadable_cases_file_exits_2_not_the_toothless_code(tmp_path, capsys):
    rc = teeth_sweep.main(["--cases", str(tmp_path / "nope.json"), "--repo", str(tmp_path)])
    assert rc == 2
    assert "cannot read --cases" in capsys.readouterr().err


def test_teeth_sweep_malformed_cases_file_exits_2(tmp_path, capsys):
    bad = tmp_path / "c.json"
    bad.write_text("{not json")
    assert teeth_sweep.main(["--cases", str(bad), "--repo", str(tmp_path)]) == 2


def test_teeth_sweep_empty_case_list_exits_2_before_running_the_suite(tmp_path, monkeypatch, capsys):
    cases = tmp_path / "c.json"
    cases.write_text(json.dumps([]))

    def boom(*a, **k):
        raise AssertionError("the suite must not run for zero cases")

    monkeypatch.setattr(teeth_sweep, "_run_suite", boom)
    assert teeth_sweep.main(["--cases", str(cases), "--repo", str(tmp_path)]) == 2
    assert "no cases" in capsys.readouterr().err


def test_workflow_runner_labels_exits_2_on_a_repo_without_workflows_unless_allowed(tmp_path, capsys):
    from py_ci_shared import workflow_runner_labels

    assert workflow_runner_labels.main([str(tmp_path)]) == 2
    assert workflow_runner_labels.main([str(tmp_path), "--allow-empty"]) == 0
    assert "0 problem(s)" in capsys.readouterr().out
