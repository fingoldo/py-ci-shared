"""Tests for py_ci_shared.resource_leak_checks: the functions directly, and the checks wired through resource_leak_guard
in inner pytest sessions (subprocess), one seeded leak or control each."""

from __future__ import annotations

import io
import os
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from py_ci_shared.resource_leak_checks import STATE_CHECKS, restore_state, state_leaks, take_state
from py_ci_shared.resource_leak_guard import ALL_CHECKS

pytest_plugins = ("pytester",)
_SRC = str(Path(__file__).resolve().parents[1] / "src")


class _Wrapper:
    """A colorama-StreamWrapper-like object around a stream."""

    def __init__(self, wrapped: object) -> None:
        self._StreamWrapper__wrapped = wrapped


def test_the_guard_runs_the_state_checks_by_default() -> None:
    assert set(STATE_CHECKS) <= set(ALL_CHECKS)


class TestDirect:
    def test_a_replaced_stream_is_reported_and_restored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = take_state(["streams"])
        original = sys.stdout
        sys.stdout = io.StringIO()
        try:
            assert state_leaks(before) == [f"sys.stdout was replaced ({type(original).__name__} -> StringIO) and not restored"]
            assert state_leaks(before, allow=["stream:std*"]) == []
        finally:
            restore_state(before)
        assert sys.stdout is original

    def test_a_wrapper_of_the_old_stream_is_reported_unless_a_library_was_imported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = take_state(["streams"])
        original = sys.stderr
        sys.stderr = _Wrapper(original)  # type: ignore[assignment]
        try:
            assert [line.split(" (")[0] for line in state_leaks(before)] == ["sys.stderr was replaced"]
            monkeypatch.setitem(sys.modules, "seed_colour_lib", object())  # a library's first import happened
            assert state_leaks(before) == []
            restore_state(before)
            assert isinstance(sys.stderr, _Wrapper)  # the import-time wrapper is kept, like an import-time env var
        finally:
            sys.stderr = original

    def test_cwd_sys_path_and_warnings(self, tmp_path: Path) -> None:
        before = take_state()
        cwd, path, filters = os.getcwd(), list(sys.path), list(warnings.filters)
        try:
            os.chdir(tmp_path)
            sys.path.insert(0, "seed-entry")
            sys.path.remove(path[-1])
            warnings.simplefilter("ignore")
            leaks = state_leaks(before)
            assert f"working directory changed to {str(tmp_path)!r} (was {cwd!r})" in leaks
            assert "sys.path entry 'seed-entry' was added" in leaks and f"sys.path entry {path[-1]!r} was removed" in leaks
            assert any(line.startswith("warnings.filters changed") for line in leaks)
            assert state_leaks(before, allow=["cwd", "sys_path", "warnings"]) == []
            restore_state(before)
            assert (os.getcwd(), sys.path, list(warnings.filters)) == (cwd, path, filters)
        finally:
            os.chdir(cwd)
            sys.path[:] = path
            warnings.filters[:] = filters

    def test_a_reordered_sys_path_is_reported(self) -> None:
        before = take_state(["sys_path"])
        path = list(sys.path)
        try:
            sys.path[:] = path[::-1]
            assert state_leaks(before) == (["sys.path was reordered"] if len(set(path)) > 1 else [])
        finally:
            sys.path[:] = path

    def test_import_time_additions_are_not_reported_or_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = take_state(["sys_path", "warnings"])
        path, filters = list(sys.path), list(warnings.filters)
        try:
            monkeypatch.setitem(sys.modules, "seed_plugin_lib", object())
            sys.path.append("seed-plugin-dir")
            warnings.filterwarnings("ignore", message="seed")
            assert state_leaks(before) == []
            restore_state(before)
            assert "seed-plugin-dir" in sys.path and warnings.filters[0][1] is not None
        finally:
            sys.path[:] = path
            warnings.filters[:] = filters

    def test_a_check_that_is_off_reports_nothing(self, tmp_path: Path) -> None:
        before = take_state([])
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert state_leaks(before) == []
        finally:
            os.chdir(cwd)


def _inner(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, body: str, *args: str) -> pytest.RunResult:
    monkeypatch.setenv("PYTHONPATH", _SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    pytester.makepyfile(test_inner=textwrap.dedent(body))
    return pytester.runpytest_subprocess("-p", "py_ci_shared.resource_leak_guard", "-p", "no:cacheprovider", *args)


def _text(result: pytest.RunResult) -> str:
    return "\n".join(result.outlines)


class TestThroughTheGuard:
    @pytest.mark.parametrize("capture", ["-s", "--capture=fd"])
    def test_a_stream_swapped_and_left_errors_at_teardown_then_is_restored(self, pytester, monkeypatch, capture):
        result = _inner(
            pytester,
            monkeypatch,
            """
            import io, sys
            def test_leaks():
                sys.stdout = io.StringIO()
            def test_next_sees_the_real_stream():
                assert not isinstance(sys.stdout, io.StringIO)
            """,
            capture,
        )
        if capture == "-s":
            result.assert_outcomes(passed=2, errors=1)
            assert "sys.stdout was replaced" in _text(result)
        else:  # pytest's own capture puts sys.stdout back when the call phase ends: nothing is left to leak
            result.assert_outcomes(passed=2)

    def test_a_stream_swapped_in_teardown_of_a_fixture_is_seen_under_capture_too(self, pytester, monkeypatch):
        result = _inner(
            pytester,
            monkeypatch,
            """
            import io, sys, pytest
            @pytest.fixture
            def deinit():
                yield
                sys.stderr = io.StringIO()  # colorama deinit() in a finalizer
            def test_it(deinit):
                pass
            """,
        )
        result.assert_outcomes(passed=1, errors=1)
        assert "sys.stderr was replaced" in _text(result)

    def test_cwd_sys_path_and_warning_leaks_error(self, pytester, monkeypatch):
        result = _inner(
            pytester,
            monkeypatch,
            """
            import os, sys, warnings
            def test_cwd(tmp_path):
                os.chdir(tmp_path)
            def test_path():
                sys.path.insert(0, "seed-dir")
            def test_filters():
                warnings.simplefilter("ignore")
            def test_after():
                assert "seed-dir" not in sys.path
            """,
        )
        result.assert_outcomes(passed=4, errors=3)
        text = _text(result)
        assert "working directory changed" in text and "sys.path entry 'seed-dir' was added" in text and "warnings.filters changed" in text

    def test_restoring_tools_and_capture_fixtures_are_clean(self, pytester, monkeypatch):
        result = _inner(
            pytester,
            monkeypatch,
            """
            import io, sys, warnings, contextlib, pytest
            def test_monkeypatch(monkeypatch, tmp_path):
                monkeypatch.chdir(tmp_path)
                monkeypatch.syspath_prepend(str(tmp_path))
                monkeypatch.setattr(sys, "stdout", io.StringIO())
            def test_capsys(capsys):
                print("x")
                assert capsys.readouterr().out == "x\\n"
            def test_catch_warnings():
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                with pytest.warns(UserWarning):
                    warnings.warn("w", UserWarning)
            def test_redirect():
                with contextlib.redirect_stdout(io.StringIO()):
                    print("hidden")
            """,
            "-s",
        )
        result.assert_outcomes(passed=4)

    def test_allowlist_and_narrowed_checks(self, pytester, monkeypatch):
        result = _inner(
            pytester,
            monkeypatch,
            """
            import os, pytest
            @pytest.mark.leak_guard_allow("cwd")
            def test_allowed(tmp_path):
                os.chdir(tmp_path)
            """,
        )
        result.assert_outcomes(passed=1)
        pytester.makeini("[pytest]\nleak_guard_checks = env logging\n")
        result = _inner(pytester, monkeypatch, "import os\ndef test_unchecked(tmp_path):\n    os.chdir(tmp_path)\n")
        result.assert_outcomes(passed=1)


def test_adoption_matrix_counts_the_plugin_enabled_by_its_config_key(tmp_path: Path) -> None:
    """``[tool.py_ci_shared] resource_leak_guard = true`` enables the plugin without naming it in ``enable``."""
    import subprocess

    from py_ci_shared.adoption_matrix import find_module_usage

    (tmp_path / "pyproject.toml").write_text('[tool.py_ci_shared]\nresource_leak_guard = true\nenable = ["naive_utcnow"]\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "pyproject.toml"], cwd=tmp_path, check=True)
    used, _ = find_module_usage(tmp_path)
    assert used.get("resource_leak_guard") == ["pyproject.toml"] and used.get("naive_utcnow") == ["pyproject.toml"]
    (tmp_path / "pyproject.toml").write_text('[tool.py_ci_shared]\nresource_leak_guard = false\nenable = ["naive_utcnow"]\n')
    assert "resource_leak_guard" not in find_module_usage(tmp_path)[0]
