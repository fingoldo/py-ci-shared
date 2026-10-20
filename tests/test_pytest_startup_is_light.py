"""A pytest session that does not use pytest-randomly does not import the numeric and GPU libraries its reseeders would pull in.

A session started with ``-p no:randomly`` (the mutation harness starts one per cold run) used to load every ``pytest_randomly.random_seeder``
entry point, so thinc, torch and cupy were imported before the first test: about 7 s per start on a machine with them installed. The guard
that prevents it runs in the test conftest and in the package's own pytest plugin; this test starts a real pytest process and checks what it
imported, so a call that drops the session config fails here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "_startup_probe_plugin.py"


def test_a_session_without_randomly_imports_no_heavy_library(tmp_path):
    out = tmp_path / "loaded.txt"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]), PCS_PROBE_OUT=str(out))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider", "-p", "_startup_probe_plugin", "--co", "-q", "--no-cov", "tests/test_worker_budget.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
    loaded = out.read_text(encoding="utf-8")
    assert loaded == "", f"a session with -p no:randomly imported {loaded}; the randomly reseeder guard must see the session config"
