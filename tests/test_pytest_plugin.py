"""The ``pytest11`` plugin, run for real in a child pytest over a throwaway repo."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from py_ci_shared._core.refresh import ENV_VAR

SRC = Path(__file__).resolve().parents[1] / "src"
CLEAN = "SQL = 'SELECT 1'\n\n\ndef f(x):\n    return x == SQL\n"
DIRTY = "SQL = 'SELECT 1'\n\n\ndef f(x):\n    return x is SQL\n"
IDENTITY = '[tool.py_ci_shared.gates.identity_comparisons]\nfiles = ["pkg/**/*.py"]\n'
ENV_PROBE = "import os\n\n\ndef test_probe():\n    print('REFRESH=' + os.environ.get('PY_CI_SHARED_REFRESH', '<unset>'))\n"


def _repo(tmp_path: Path, table: str, module: str = CLEAN) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_bytes(("[project]\nname = 'demo'\nversion = '0.1.0'\n\n" + table).encode())
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_bytes(module.encode())
    (tmp_path / "test_probe.py").write_bytes(ENV_PROBE.encode())
    return tmp_path


def _pytest(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != ENV_VAR}
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"  # only the plugin under test, loaded once by -p
    cmd = [sys.executable, "-m", "pytest", "-p", "py_ci_shared.pytest_plugin", "-p", "no:cacheprovider", "-rA", "-s", *args]
    return subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=240)


def test_a_repo_without_the_table_sees_no_gate_items(tmp_path):
    proc = _pytest(_repo(tmp_path, "", DIRTY))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pyproject.toml::" not in proc.stdout and "1 passed" in proc.stdout


def test_a_bare_run_adds_one_item_per_gate_and_reports_the_gates_text(tmp_path):
    proc = _pytest(_repo(tmp_path, IDENTITY, DIRTY))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAILED pyproject.toml::identity_comparisons" in proc.stdout
    assert "m.py:5" in proc.stdout, "the gate's own finding text must reach the report"
    assert "1 failed, 1 passed" in proc.stdout


def test_selecting_paths_leaves_gates_out_unless_forced(tmp_path):
    repo = _repo(tmp_path, IDENTITY, DIRTY)
    selected = _pytest(repo, "test_probe.py")
    assert selected.returncode == 0 and "pyproject.toml::" not in selected.stdout, selected.stdout
    forced = _pytest(repo, "test_probe.py", "--py-ci-gates=on")
    assert forced.returncode == 1 and "FAILED pyproject.toml::identity_comparisons" in forced.stdout, forced.stdout
    off = _pytest(repo, "--py-ci-gates=off")
    assert off.returncode == 0 and "pyproject.toml::" not in off.stdout, off.stdout


def test_a_clean_gate_passes_and_carries_the_marker(tmp_path):
    proc = _pytest(_repo(tmp_path, IDENTITY), "-m", "py_ci_shared", "--py-ci-gates=on")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASSED pyproject.toml::identity_comparisons" in proc.stdout and "1 deselected" in proc.stdout


def test_refresh_writes_a_missing_baseline_and_reaches_every_test_through_the_env(tmp_path):
    repo = _repo(tmp_path, '[tool.py_ci_shared.gates.loc_budget]\nfiles = ["pkg/*.py"]\nroot = "."\nbaseline_path = "b.json"\n')
    missing = _pytest(repo)
    assert missing.returncode == 1 and "FAILED pyproject.toml::loc_budget" in missing.stdout, missing.stdout
    assert not (repo / "b.json").exists()
    refreshed = _pytest(repo, "--py-ci-refresh=loc_budget")
    assert (repo / "b.json").is_file(), refreshed.stdout
    assert "REFRESH=loc_budget" in refreshed.stdout, "the option must set the env var every test and xdist worker reads"
    after = _pytest(repo)
    assert after.returncode == 0 and "REFRESH=<unset>" in after.stdout, after.stdout
    bare = _pytest(repo, "test_probe.py", "--py-ci-refresh")
    assert "REFRESH=all" in bare.stdout, bare.stdout


def test_a_gate_over_budget_warns_and_fails_in_fail_mode(tmp_path):
    table = IDENTITY + "budget_s = 0.000001\n"
    warned = _pytest(_repo(tmp_path / "warn", table))
    assert warned.returncode == 0 and "over its 0s budget" in warned.stdout, warned.stdout
    failing = _pytest(_repo(tmp_path / "fail", '[tool.py_ci_shared]\nbudget = "fail"\n' + table))
    assert failing.returncode == 1 and "over its 0s budget" in failing.stdout, failing.stdout


def test_a_malformed_table_is_a_usage_error(tmp_path):
    proc = _pytest(_repo(tmp_path, '[tool.py_ci_shared]\nbudget = "loud"\n'))
    assert proc.returncode == 4 and "py-ci-shared" in proc.stderr, proc.stdout + proc.stderr


LEAKY_TEST = "import os\n\n\ndef test_leaks_env():\n    os.environ['PCS_LEAK_PROBE'] = '1'\n"


def test_the_table_loads_the_resource_leak_guard_only_when_asked(tmp_path):
    off = _repo(tmp_path / "off", "[tool.py_ci_shared]\nresource_leak_guard = false\n")
    (off / "test_leaky.py").write_bytes(LEAKY_TEST.encode())
    quiet = _pytest(off, "test_leaky.py")
    assert quiet.returncode == 0 and "1 passed" in quiet.stdout, quiet.stdout + quiet.stderr
    on = _repo(tmp_path / "on", "[tool.py_ci_shared]\nresource_leak_guard = true\n")
    (on / "test_leaky.py").write_bytes(LEAKY_TEST.encode())
    guarded = _pytest(on, "test_leaky.py")
    assert guarded.returncode == 1, guarded.stdout + guarded.stderr
    assert "leaked resources past its teardown" in guarded.stdout and "PCS_LEAK_PROBE" in guarded.stdout, guarded.stdout
    # -p and the table together load it once, not twice.
    both = _pytest(on, "-p", "py_ci_shared.resource_leak_guard", "test_leaky.py")
    assert both.returncode == 1 and both.stdout.count("leaked resources past its teardown") == 1, both.stdout + both.stderr


def test_a_non_boolean_resource_leak_guard_is_a_usage_error(tmp_path):
    proc = _pytest(_repo(tmp_path, '[tool.py_ci_shared]\nresource_leak_guard = "yes"\n'))
    assert proc.returncode == 4 and "resource_leak_guard" in proc.stderr, proc.stdout + proc.stderr
