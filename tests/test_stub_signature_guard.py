"""Tests for the stub_signature_guard plugin: inner pytest sessions in a subprocess, each stubbing a real callable."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.stub_signature_guard import check_stub

pytest_plugins = ("pytester",)
SRC = Path(__file__).resolve().parents[1] / "src"

REAL = """
async def submit_approved(conn, submission_id, request, *, provider=None, expected_dry_run=None):
    return None

def missing_detail_transaction_ids(conn, ace_id, *, limit=200):
    return []

def _questions_line(job_uid, fl_cid, saved_answers=None):
    return ""

class Refresher:
    def refresh_once(self, *, full=False):
        return full

    @staticmethod
    def helper(a, b):
        return a
"""

BROKEN = """
import realmod

def test_submit(monkeypatch):
    async def _submit(conn, submission_id, request, provider=None):
        return None
    monkeypatch.setattr(realmod, "submit_approved", _submit)

def test_backfill(monkeypatch):
    monkeypatch.setattr("realmod.missing_detail_transaction_ids", lambda *_a: ["t1"])

def test_questions(monkeypatch):
    monkeypatch.setattr(realmod, "_questions_line", lambda job_uid, fl_cid: "questions")

def test_instance(monkeypatch):
    r = realmod.Refresher()
    monkeypatch.setattr(r, "refresh_once", lambda: None)
"""

FIXED = """
import realmod
from unittest import mock

def test_submit(monkeypatch):
    async def _submit(conn, submission_id, request, provider=None, expected_dry_run=None):
        return None
    monkeypatch.setattr(realmod, "submit_approved", _submit)

def test_backfill(monkeypatch):
    monkeypatch.setattr("realmod.missing_detail_transaction_ids", lambda *_a, **_k: ["t1"])

def test_questions(monkeypatch):
    monkeypatch.setattr(realmod, "_questions_line", lambda job_uid, fl_cid, saved_answers=None: "questions")

def test_not_checked(monkeypatch):
    monkeypatch.setattr(realmod, "_questions_line", mock.Mock())
    monkeypatch.setattr(realmod.Refresher, "helper", lambda: 1)  # staticmethod: not checked
    monkeypatch.setattr(realmod, "CONSTANT", 1, raising=False)
    monkeypatch.setenv("PCS_X", "1")
"""


def _run(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, body: str, ini: str = "") -> pytest.RunResult:
    monkeypatch.setenv("PYTHONPATH", str(SRC) + os.pathsep + str(pytester.path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    if ini:
        pytester.makeini("[pytest]\n" + textwrap.dedent(ini))
    pytester.makepyfile(realmod=REAL, test_inner=textwrap.dedent(body))
    return pytester.runpytest_subprocess("-p", "py_ci_shared.stub_signature_guard", "-p", "no:cacheprovider")


def test_the_three_dashboard_stubs_fail_at_their_setattr(pytester, monkeypatch):
    result = _run(pytester, monkeypatch, BROKEN)
    result.assert_outcomes(failed=4)
    out = "\n".join(result.outlines)
    assert "StubSignatureMismatchError" in out
    assert "test_inner.py::test_submit: stub test_submit.<locals>._submit" in out and "cannot accept expected_dry_run" in out
    assert "realmod.missing_detail_transaction_ids" in out
    assert out.count("cannot accept limit") >= 1 and out.count("cannot accept saved_answers") >= 1
    assert "Refresher.refresh_once" in out and "cannot accept full" in out


def test_the_fixed_stubs_and_unchecked_shapes_pass(pytester, monkeypatch):
    _run(pytester, monkeypatch, FIXED).assert_outcomes(passed=4)


def test_marker_and_ini_exemptions(pytester, monkeypatch):
    body = BROKEN.replace("def test_submit(", "import pytest\n\n@pytest.mark.stub_signature_allow('*submit_approved')\ndef test_submit(")
    body = body.replace("def test_questions(", "@pytest.mark.no_stub_signature_check\ndef test_questions(")
    result = _run(pytester, monkeypatch, body, ini="stub_signature_allow =\n    *missing_detail_transaction_ids\n")
    result.assert_outcomes(passed=3, failed=1)
    assert "cannot accept full" in "\n".join(result.outlines)


def test_a_second_setattr_is_checked_against_the_original(pytester, monkeypatch):
    body = """
import realmod

def test_twice(monkeypatch):
    monkeypatch.setattr(realmod, "_questions_line", lambda *a, **k: "ok")
    monkeypatch.setattr(realmod, "_questions_line", lambda job_uid, fl_cid: "short")
"""
    result = _run(pytester, monkeypatch, body)
    result.assert_outcomes(failed=1)
    assert "cannot accept saved_answers" in "\n".join(result.outlines)


def test_check_stub_directly():
    def real(a, *, k=1):
        return a

    assert check_stub(sys.modules[__name__], "real", real, lambda a: a) is not None
    assert check_stub(sys.modules[__name__], "real", real, lambda a, **kw: a) is None
    assert check_stub(sys.modules[__name__], "real", 5, lambda: 1) is None


def test_teeth_without_the_plugin_the_broken_stubs_pass(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(pytester.path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    pytester.makepyfile(realmod=REAL, test_inner=textwrap.dedent(BROKEN))
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=4)


def _repo(tmp_path: Path, table: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_bytes(("[project]\nname = 'demo'\nversion = '0.1.0'\n\n" + table).encode())
    (tmp_path / "realmod.py").write_bytes(REAL.encode())
    (tmp_path / "test_inner.py").write_bytes(BROKEN.encode())
    return tmp_path


def _pytest(repo: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PY_CI_SHARED_REFRESH")}
    env["PYTHONPATH"] = str(SRC) + os.pathsep + str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    cmd = [sys.executable, "-m", "pytest", "-p", "py_ci_shared.pytest_plugin", "-p", "no:cacheprovider", "-rA", "test_inner.py"]
    return subprocess.run(cmd, cwd=repo, env=env, capture_output=True, encoding="utf-8", timeout=240)


def test_the_table_loads_the_guard_only_when_asked(tmp_path):
    off = _pytest(_repo(tmp_path / "off", "[tool.py_ci_shared]\nstub_signature_guard = false\n"))
    assert off.returncode == 0 and "4 passed" in off.stdout, off.stdout + off.stderr
    on = _pytest(_repo(tmp_path / "on", "[tool.py_ci_shared]\nstub_signature_guard = true\n"))
    assert on.returncode == 1 and "4 failed" in on.stdout and "StubSignatureMismatchError" in on.stdout, on.stdout + on.stderr


def test_a_non_boolean_guard_key_is_a_usage_error(tmp_path):
    proc = _pytest(_repo(tmp_path, '[tool.py_ci_shared]\nstub_signature_guard = "yes"\n'))
    assert proc.returncode == 4 and "stub_signature_guard" in proc.stderr, proc.stdout + proc.stderr
