"""Tests for the resource_leak_guard plugin: inner pytest sessions in a subprocess, each seeding one leak or one control."""

from __future__ import annotations

import logging
import os
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.resource_leak_guard import leaks_between, take_snapshot

pytest_plugins = ("pytester",)
_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, body: str, ini: str = "") -> pytest.RunResult:
    monkeypatch.setenv("PYTHONPATH", _SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")  # the inner session sees only this plugin
    if ini:
        pytester.makeini("[pytest]\n" + textwrap.dedent(ini))
    pytester.makepyfile(test_inner=textwrap.dedent(body))
    return pytester.runpytest_subprocess("-p", "py_ci_shared.resource_leak_guard", "-p", "no:cacheprovider")


def _errors(result: pytest.RunResult) -> str:
    return "\n".join(result.outlines)


class TestThreads:
    def test_a_running_non_daemon_thread_errors_at_teardown(self, pytester, monkeypatch):
        result = _run(
            pytester,
            monkeypatch,
            """
            import threading
            def test_leaks():
                threading.Timer(2.0, lambda: None).start()
            """,
        )
        result.assert_outcomes(passed=1, errors=1)
        assert "leaked resources past its teardown" in _errors(result) and "(non-daemon) is still running" in _errors(result)

    def test_a_daemon_or_joined_thread_is_clean(self, pytester, monkeypatch):
        result = _run(
            pytester,
            monkeypatch,
            """
            import threading, time
            def test_daemon():
                threading.Thread(target=time.sleep, args=(2,), daemon=True).start()
            def test_joined():
                t = threading.Thread(target=time.sleep, args=(0.05,))
                t.start()
                t.join()
            def test_finishes_within_grace():
                threading.Thread(target=time.sleep, args=(0.1,)).start()
            """,
        )
        result.assert_outcomes(passed=3)

    def test_a_module_fixture_thread_is_not_the_tests_leak_but_an_allowlisted_name_is_needed_otherwise(self, pytester, monkeypatch):
        result = _run(
            pytester,
            monkeypatch,
            """
            import threading, pytest
            @pytest.fixture(scope="module")
            def server():
                t = threading.Timer(2.0, lambda: None)
                t.start()
                yield t
            def test_uses_it(server):
                assert server.is_alive()
            @pytest.mark.leak_guard_allow("thread:keeper*")
            def test_allowed():
                t = threading.Timer(2.0, lambda: None)
                t.name = "keeper-1"
                t.start()
            """,
        )
        result.assert_outcomes(passed=2)


class TestGlobalState:
    def test_env_edits_error_and_are_restored_for_the_next_test(self, pytester, monkeypatch):
        monkeypatch.setenv("LEAK_GUARD_EXISTING", "a")
        result = _run(
            pytester,
            monkeypatch,
            """
            import os
            def test_1_adds():
                os.environ["LEAK_GUARD_NEW"] = "1"
            def test_2_changes_and_removes():
                os.environ["LEAK_GUARD_EXISTING"] = "b"
            def test_3_sees_a_restored_environment():
                assert "LEAK_GUARD_NEW" not in os.environ
                assert os.environ["LEAK_GUARD_EXISTING"] == "a"
            def test_4_monkeypatch_is_clean(monkeypatch):
                monkeypatch.setenv("LEAK_GUARD_NEW", "x")
                monkeypatch.delenv("LEAK_GUARD_EXISTING")
            """,
        )
        result.assert_outcomes(passed=4, errors=2)
        out = _errors(result)
        assert "env var LEAK_GUARD_NEW was added" in out and "env var LEAK_GUARD_EXISTING was changed" in out

    def test_a_variable_a_first_import_adds_is_import_time_configuration_but_a_change_is_not(self, pytester, monkeypatch):
        monkeypatch.setenv("LEAK_GUARD_PRESET", "a")
        pytester.makepyfile(
            configures_itself="import os\nos.environ.setdefault('LEAK_GUARD_LIB_HOME', '/x')\n",
            reconfigures="import os\nos.environ['LEAK_GUARD_PRESET'] = 'b'\n",
        )
        result = _run(
            pytester,
            monkeypatch,
            """
            import os
            def test_1_first_import_configures():
                import configures_itself
            def test_2_it_is_kept():
                assert os.environ["LEAK_GUARD_LIB_HOME"] == "/x"
            def test_3_an_import_that_changes_a_variable_is_still_a_leak():
                import reconfigures
            """,
        )
        result.assert_outcomes(passed=3, errors=1)
        assert "LEAK_GUARD_PRESET was changed" in _errors(result) and "LEAK_GUARD_LIB_HOME" not in _errors(result)

    def test_a_function_fixture_that_does_not_restore_env_is_caught(self, pytester, monkeypatch):
        result = _run(
            pytester,
            monkeypatch,
            """
            import os, pytest
            @pytest.fixture
            def sets_env():
                os.environ["LEAK_GUARD_FIXTURE"] = "1"
                yield
            def test_it(sets_env):
                pass
            """,
        )
        result.assert_outcomes(passed=1, errors=1)
        assert "LEAK_GUARD_FIXTURE was added" in _errors(result)

    def test_logging_disable_errors_is_restored_and_can_be_allowed(self, pytester, monkeypatch):
        result = _run(
            pytester,
            monkeypatch,
            """
            import logging, pytest
            def test_1_disables():
                logging.disable(logging.CRITICAL)
            def test_2_restored():
                assert logging.root.manager.disable == logging.NOTSET
            @pytest.mark.leak_guard_allow("logging")
            def test_3_allowed():
                logging.disable(logging.CRITICAL)
            """,
            ini="leak_guard_restore = true\n",
        )
        result.assert_outcomes(passed=3, errors=1)
        assert "logging.disable(50) left on (was 0)" in _errors(result)

    def test_restore_can_be_turned_off(self, pytester, monkeypatch):
        result = _run(
            pytester,
            monkeypatch,
            """
            import os
            def test_1_adds():
                os.environ["LEAK_GUARD_KEEP"] = "1"
            def test_2_sees_it():
                assert os.environ.get("LEAK_GUARD_KEEP") == "1"
            """,
            ini="leak_guard_restore = false\nleak_guard_checks = env\n",
        )
        # test_2 passing proves the value was kept; it is in test_2's own baseline, so it is reported once
        result.assert_outcomes(passed=2, errors=1)


class TestProcessesAndSockets:
    def test_an_unwaited_child_process_errors_and_a_waited_one_does_not(self, pytester, monkeypatch):
        pytest.importorskip("psutil")
        result = _run(
            pytester,
            monkeypatch,
            """
            import subprocess, sys
            def test_leaks():
                subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"])
            def test_waits():
                subprocess.run([sys.executable, "-c", "pass"], check=True)
            """,
        )
        result.assert_outcomes(passed=2, errors=1)
        assert "child process" in _errors(result) and "time.sleep(4)" in _errors(result)

    def test_an_open_listening_socket_errors_and_a_closed_one_does_not(self, pytester, monkeypatch):
        pytest.importorskip("psutil")
        result = _run(
            pytester,
            monkeypatch,
            """
            import socket
            KEEP = []
            def test_leaks():
                s = socket.socket()
                s.bind(("127.0.0.1", 0))
                s.listen()
                KEEP.append(s)
            def test_closes():
                with socket.socket() as s:
                    s.bind(("127.0.0.1", 0))
                    s.listen()
            """,
        )
        result.assert_outcomes(passed=2, errors=1)
        assert "socket 127.0.0.1:" in _errors(result)


class TestConfiguration:
    def test_no_leak_guard_and_the_ini_allowlist(self, pytester, monkeypatch):
        result = _run(
            pytester,
            monkeypatch,
            """
            import os, pytest
            @pytest.mark.no_leak_guard
            def test_skipped_check():
                os.environ["LEAK_GUARD_A"] = "1"
            def test_allowed_by_ini():
                os.environ["LEAK_GUARD_ALLOWED_X"] = "1"
            """,
            ini="leak_guard_allow =\n    env:LEAK_GUARD_ALLOWED_*\n    env:LEAK_GUARD_A\n",
        )
        result.assert_outcomes(passed=2)

    def test_an_unknown_check_is_a_usage_error(self, pytester, monkeypatch):
        result = _run(pytester, monkeypatch, "def test_x():\n    pass\n", ini="leak_guard_checks = env sokcets\n")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        assert "sokcets" in "\n".join(result.errlines)

    def test_narrowed_checks_ignore_the_others(self, pytester, monkeypatch):
        result = _run(pytester, monkeypatch, "import os\ndef test_x():\n    os.environ['LEAK_GUARD_Z'] = '1'\n", ini="leak_guard_checks = threads\n")
        result.assert_outcomes(passed=1)


class TestSnapshotApi:
    def test_leaks_between_reports_env_and_logging_and_honours_the_always_allowed_names(self, monkeypatch):
        before = take_snapshot(["env", "logging"])
        monkeypatch.setenv("LEAK_GUARD_UNIT", "1")
        monkeypatch.setenv("PYTEST_SOMETHING_NEW", "1")
        logging.disable(logging.ERROR)
        try:
            assert leaks_between(before) == [f"logging.disable({logging.ERROR}) left on (was {before.logging_disable})", "env var LEAK_GUARD_UNIT was added"]
            assert leaks_between(before, allow=["env:LEAK_GUARD_*", "logging"]) == []
        finally:
            logging.disable(before.logging_disable or logging.NOTSET)

    def test_an_unmeasured_check_reports_nothing(self, monkeypatch):
        before = take_snapshot([])
        monkeypatch.setenv("LEAK_GUARD_UNIT2", "1")
        assert leaks_between(before) == []
