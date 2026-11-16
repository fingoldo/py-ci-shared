"""44-T1: a nested mutant run imports the package from the sandbox's ``src``, not from the parent's checkout.

Without the sandbox ``src`` first on ``PYTHONPATH``, py_ci_shared mutating itself spawns a worker that loads the parent's
copy and the harness refuses the run (``MutationHarnessError``), so the gates' own tests could not be mutation-checked.
"""

from __future__ import annotations

import os

from py_ci_shared._mutation_runner import _pytest_env


def test_the_sandbox_src_is_first_on_the_child_pythonpath(tmp_path, monkeypatch):
    sandbox_src = tmp_path / "src"
    sandbox_src.mkdir()
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    env = _pytest_env(sandbox=tmp_path)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(sandbox_src)
    assert "/somewhere/else" in env["PYTHONPATH"].split(os.pathsep)


def test_a_sandbox_without_src_adds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    env = _pytest_env(sandbox=tmp_path)
    assert str(tmp_path / "src") not in env["PYTHONPATH"].split(os.pathsep)
