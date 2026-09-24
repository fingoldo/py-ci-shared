"""Tests for ``py_ci_shared._mutation_worker``, the warm pytest process behind ``mutation_teeth``.

The end-to-end cases drive a real worker through ``_WarmRunner``; each costs one interpreter start.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from py_ci_shared import _mutation_worker
from py_ci_shared.mutation_teeth import _WarmRunner


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


class TestTheProtocolChannelIsPrivate:
    def test_a_raw_fd_1_write_cannot_forge_a_reply(self, tmp_path):
        _write(
            tmp_path / "test_noisy.py",
            "import os, subprocess, sys\n\n\n"
            "def test_writes_to_fd_1():\n"
            "    os.write(1, b'{\"rc\": 999}\\n')\n"
            '    os.write(1, b\'{"id": 2, "rc": 998}\\n\')\n'
            "    subprocess.run([sys.executable, '-c', 'print(\"{}\")'], check=True)\n"
            "    assert True\n",
        )
        with _WarmRunner(tmp_path, timeout=120) as warm:
            codes = [warm.run(["test_noisy.py"]) for _ in range(3)]
        assert codes == [0, 0, 0], codes

    def test_a_failing_run_still_reports_its_own_code(self, tmp_path):
        _write(tmp_path / "test_fails.py", "import os\n\n\ndef test_f():\n    os.write(1, b'{\"rc\": 0}\\n')\n    assert 1 == 2\n")
        with _WarmRunner(tmp_path, timeout=120) as warm:
            assert warm.run(["test_fails.py"]) == 1


class TestProcessStateIsRestoredBetweenRuns:
    def test_cwd_env_path_and_argv_do_not_leak_into_the_next_run(self, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        _write(
            tmp_path / "test_leaks.py",
            "import os, sys\n\n\n"
            "def test_leaves_a_mess():\n"
            f"    os.chdir({str(elsewhere)!r})\n"
            "    os.environ['MUTATION_WORKER_LEAK'] = '1'\n"
            "    sys.path.insert(0, '/nonexistent-leak')\n"
            "    sys.argv.append('--leak')\n",
        )
        _write(
            tmp_path / "test_clean.py",
            "import os, sys\n\n\n"
            "def test_sees_a_clean_process():\n"
            f"    assert os.path.samefile(os.getcwd(), {str(tmp_path)!r})\n"
            "    assert 'MUTATION_WORKER_LEAK' not in os.environ\n"
            "    assert '/nonexistent-leak' not in sys.path\n"
            "    assert '--leak' not in sys.argv\n",
        )
        with _WarmRunner(tmp_path, timeout=120) as warm:
            assert warm.run(["test_clean.py"]) == 0, "the control must pass on a fresh worker"
            assert warm.run(["test_leaks.py"]) == 0
            assert warm.run(["test_clean.py"]) == 0, "state left by the previous run reached this one"

    def test_restore_undoes_each_change(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "path", list(sys.path))
        monkeypatch.setattr(sys, "argv", list(sys.argv))
        state = _mutation_worker._ProcessState()
        other = tmp_path / "o"
        other.mkdir()
        os.chdir(other)
        monkeypatch.setenv("MUTATION_WORKER_RESTORE", "x")
        sys.path.append("/leak")
        sys.argv.append("--leak")
        state.restore()
        assert Path.cwd() == tmp_path
        assert "MUTATION_WORKER_RESTORE" not in os.environ
        assert "/leak" not in sys.path and "--leak" not in sys.argv


class TestCrashesAreToldApart:
    def test_a_type_error_is_a_crash_and_an_assertion_is_not(self, tmp_path):
        _write(tmp_path / "test_crash.py", "def test_c():\n    len(5)\n")
        _write(tmp_path / "test_assert.py", "def test_a():\n    assert 1 == 2\n")
        with _WarmRunner(tmp_path, timeout=120) as warm:
            assert warm.run(["test_crash.py"]) == 1
            assert warm.last_crash is True
            assert warm.run(["test_assert.py"]) == 1
            assert warm.last_crash is False

    def test_the_plugin_reads_the_crash_line(self):
        spy = _mutation_worker._FirstFailure()
        report = types.SimpleNamespace(
            failed=True, nodeid="tests/t.py::test_x", longrepr=types.SimpleNamespace(reprcrash=types.SimpleNamespace(message="KeyError: 'k'"))
        )
        spy.pytest_runtest_logreport(report)
        assert spy.path == "tests/t.py" and spy.crash is True
        spy = _mutation_worker._FirstFailure()
        report.longrepr.reprcrash.message = "assert 1 == 2"
        spy.pytest_runtest_logreport(report)
        assert spy.crash is False


class TestModuleOrigin:
    def test_a_module_loaded_from_the_target_is_the_sandbox(self, tmp_path, monkeypatch):
        target = tmp_path / "pkg" / "mod.py"
        target.parent.mkdir()
        _write(target, "X = 1\n")
        monkeypatch.setitem(sys.modules, "pkg_origin_test.mod", types.SimpleNamespace(__file__=str(target)))
        assert _mutation_worker._module_origin(str(target), ["pkg_origin_test.mod"]) == {"sandbox": True, "elsewhere": []}

    def test_a_module_loaded_from_elsewhere_is_named(self, tmp_path, monkeypatch):
        target = tmp_path / "pkg" / "mod.py"
        other = tmp_path / "other" / "mod.py"
        for f in (target, other):
            f.parent.mkdir()
            _write(f, "X = 1\n")
        monkeypatch.setitem(sys.modules, "pkg_origin_test.mod", types.SimpleNamespace(__file__=str(other)))
        got = _mutation_worker._module_origin(str(target), ["pkg_origin_test.mod"])
        assert got["sandbox"] is False and len(got["elsewhere"]) == 1 and "other" in got["elsewhere"][0]


class TestNamespaceVerdictsAreCached:
    def test_a_namespace_package_is_resolved_once(self, tmp_path, monkeypatch):
        ns_dir = tmp_path / "ns"
        ns_dir.mkdir()
        module = types.ModuleType("mutation_worker_ns_test")
        module.__path__ = [str(ns_dir)]  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mutation_worker_ns_test", module)
        monkeypatch.setattr(_mutation_worker, "_IS_LOCAL_NAMESPACE", {})
        resolved: list[str] = []
        real_resolve = Path.resolve

        def counting(self, *args, **kwargs):
            if str(self) == str(ns_dir):
                resolved.append(str(self))
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", counting)
        other_root = tmp_path / "elsewhere"
        for _ in range(3):
            _mutation_worker._purge_local_modules(other_root)
        assert len(resolved) == 1
        assert "mutation_worker_ns_test" in sys.modules, "a namespace outside the root must not be purged"
        _mutation_worker._purge_local_modules(tmp_path)
        assert "mutation_worker_ns_test" not in sys.modules, "a namespace under the root must be purged"


@pytest.mark.parametrize("payload, expected", [("TypeError: x", True), ("assert False", False), ("AssertionError: y", False), ("Failed: z", None)])
def test_is_crash_message(payload, expected):
    assert _mutation_worker.is_crash_message(payload) is expected
