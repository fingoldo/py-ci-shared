"""Measurement-only experiment: sweep wall time with child pytest startup trimmed. Not used by the tests."""

import os
import pathlib
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "src"))
from py_ci_shared import _mutation_runner as mr
from py_ci_shared import mutation_teeth as mt

mode = sys.argv[1]
if mode == "trimmed":
    base_env = mr._pytest_env

    def env():
        e = base_env()
        e["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        return e

    def flags():
        return ["-q", "--no-header", "-p", "no:randomly", "-p", "no:cacheprovider"]

    mr._pytest_env = env
    mt._pytest_env = env
    mr._pytest_flags = flags
    mt._pytest_flags = flags

d = pathlib.Path(tempfile.mkdtemp())
(d / "tests").mkdir()
(d / "m.py").write_text("def clamp(n, cap):\n    if n > cap:\n        return cap\n    return n\n\n\ndef unchecked(n):\n    return n * 2 + 1\n")
(d / "tests" / "test_m.py").write_text(
    "from m import clamp\n\n\ndef test_clamp():\n    assert clamp(5, 3) == 3\n    assert clamp(2, 3) == 2\n    assert clamp(3, 3) == 3\n"
)
for jobs in (1, 4):
    t = time.time()
    o = mt.find_surviving_mutants("m.py", ["tests/test_m.py"], d, limit=8, timeout=300, use_cache=False, jobs=jobs)
    print(  # noqa: T201 - a measurement script reports on stdout
        mode, "jobs", jobs, "wall", round(time.time() - t, 1), "run", o.mutants_run, "killed", o.killed, "surv", [m.line for m in o.survivors], flush=True
    )
