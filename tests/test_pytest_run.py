"""Tests for py_ci_shared.pytest_run: the mandatory flags, the plugin-dependent ones, the caller's own options, and a real run with its output in a file."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from py_ci_shared.pytest_run import build_command, main, run_pytest, summary_line


def _all(_module: str) -> bool:
    return True


def _none(_module: str) -> bool:
    return False


def test_the_mandatory_flags_are_present():
    """-s, --durations=200, --no-cov, -n 0 and the cache provider off follow the caller's paths."""
    command = build_command(["tests/test_a.py"], python="py", installed=_all)
    assert command[:4] == ["py", "-m", "pytest", "tests/test_a.py"]
    for flag in ("-s", "--durations=200", "--no-cov"):
        assert flag in command
    assert command[command.index("-n") + 1] == "0"
    assert command[command.index("-p") + 1] == "no:cacheprovider"


def test_options_of_plugins_that_are_not_installed_are_left_out():
    """pytest rejects --no-cov without pytest-cov and -n without xdist, so neither is added."""
    command = build_command(["tests/test_a.py"], python="py", installed=_none)
    assert "--no-cov" not in command and "-n" not in command
    assert "-s" in command and "--durations=200" in command


@pytest.mark.parametrize("own", [["-n", "3"], ["-n3"], ["--numprocesses=3"], ["--dist", "loadfile"]])
def test_a_workers_option_of_the_caller_replaces_the_single_process_default(own):
    """A project that wants xdist workers says so itself; the default ``-n 0`` is not added next to it."""
    command = build_command(["tests/test_a.py", *own], python="py", installed=_all)
    pairs = list(zip(command, command[1:]))
    assert ("-n", "0") not in pairs
    assert all(token in command for token in own)


def test_without_a_workers_option_the_single_process_default_is_added():
    """No -n of the caller: exactly one ``-n 0``."""
    command = build_command(["tests/test_a.py"], python="py", installed=_all)
    assert list(zip(command, command[1:])).count(("-n", "0")) == 1


def test_a_durations_option_of_the_caller_replaces_the_default():
    """--durations=20 is kept and the default --durations=200 is not added next to it."""
    command = build_command(["tests/test_a.py", "--durations=20"], python="py", installed=_all)
    assert "--durations=20" in command and "--durations=200" not in command


@pytest.mark.parametrize("quiet", ["-q", "-qq", "--quiet"])
def test_quiet_is_refused(quiet):
    """-q buffers the progress lines, so the command refuses it with the reason."""
    with pytest.raises(ValueError, match="buffers"):
        build_command(["tests/test_a.py", quiet], installed=_all)


def _write_tests(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "test_sample.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_passing_run_writes_everything_to_the_file_and_returns_zero(tmp_path):
    """The output file holds the progress, the durations and the last line exit=0; the summary line is found in it."""
    sample = _write_tests(tmp_path, "def test_ok():\n    print('visible because of -s')\n")
    out = tmp_path / "out" / "run.txt"
    assert run_pytest(out, [str(sample)], cwd=tmp_path) == 0
    text = out.read_text(encoding="utf-8")
    assert "visible because of -s" in text and "test_sample.py" in text
    assert text.rstrip().endswith("exit=0")
    assert "1 passed" in summary_line(out)


def test_a_failing_run_returns_pytests_code_and_records_it(tmp_path):
    """The exit code of a failing test is returned and written as the last line."""
    sample = _write_tests(tmp_path, "def test_bad():\n    assert 1 == 2\n")
    out = tmp_path / "run.txt"
    assert run_pytest(out, [str(sample)], cwd=tmp_path) == 1
    assert out.read_text(encoding="utf-8").rstrip().endswith("exit=1")
    assert "1 failed" in summary_line(out)


def test_main_prints_the_exit_code_the_summary_and_the_path(tmp_path, capsys, monkeypatch):
    """The command line reports where the output went and how the run ended, and returns pytest's code."""
    sample = _write_tests(tmp_path, "def test_ok():\n    pass\n")
    out = tmp_path / "run.txt"
    monkeypatch.chdir(tmp_path)
    assert main([str(out), str(sample)]) == 0
    shown = capsys.readouterr().out
    assert "exit=0" in shown and "1 passed" in shown and str(out) in shown


def test_main_without_arguments_is_a_usage_error(capsys):
    """No output file: usage on stdout and exit code 2; -q is a usage error as well."""
    assert main([]) == 2
    assert main(["out.txt", "-q"]) == 2
    assert "buffers" in capsys.readouterr().err


def test_summary_line_of_a_missing_file_is_empty(tmp_path):
    """A file that does not exist has no summary."""
    assert summary_line(tmp_path / "nope.txt") == ""


def test_the_interpreter_running_the_tool_is_the_one_that_runs_pytest():
    """No PATH lookup: the child is sys.executable."""
    assert build_command(["x"], installed=_none)[0] == sys.executable
