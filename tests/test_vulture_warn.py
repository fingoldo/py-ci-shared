"""Unit tests for the warn-only vulture hook: argument parsing, path scoping, and vulture's exit codes."""

from __future__ import annotations

import subprocess
import sys

import pytest

from py_ci_shared import vulture_warn


def _fake_run(returncode: int, calls: list):  # type: ignore[type-arg]
    def run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode)

    return run


class TestAuditRegressions:
    def test_importing_the_module_runs_nothing(self):
        """The old module parsed sys.argv at import time and raised IndexError on a trailing flag."""
        code = "import sys; sys.argv = ['x', '--src-path']; import py_ci_shared.vulture_warn"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("argv", [["--src-path", "src/pkg", "a.py"], ["--src-path=src/pkg", "a.py"]])
    def test_both_flag_spellings_are_parsed(self, argv):
        options, rest = vulture_warn.parse_args(argv)
        assert options.src_path == "src/pkg" and rest == ["a.py"]

    def test_a_trailing_flag_is_a_usage_error_not_an_index_error(self, capsys):
        with pytest.raises(SystemExit) as info:
            vulture_warn.parse_args(["a.py", "--src-path"])
        assert info.value.code == 2
        assert "--src-path" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("path", "src", "expected"),
        [
            ("src/mlframe/x.py", "src\\mlframe", True),
            ("src/mlframe/x.py", "./src/mlframe/", True),
            ("src\\mlframe\\x.py", "src/mlframe", True),
            ("src/mlframe_extra/x.py", "src/mlframe", False),
            ("tests/src/mlframe/x.py", "src/mlframe", False),
        ],
    )
    def test_scope_is_a_normalised_path_prefix_not_a_substring(self, path, src, expected):
        assert vulture_warn.in_scope(path, src) is expected

    def test_exit_3_is_findings_and_other_nonzero_is_a_scan_that_did_not_happen(self, monkeypatch, capsys):
        calls: list = []
        monkeypatch.setattr(vulture_warn.subprocess, "run", _fake_run(3, calls))
        assert vulture_warn.main(["--src-path", "src/pkg", "src/pkg/a.py", "other/b.py"]) == 0
        assert calls[-1][-1] == "src/pkg/a.py" and "other/b.py" not in calls[-1]
        err = capsys.readouterr().err
        assert "WARNINGS ONLY" in err and "did not scan" not in err

        monkeypatch.setattr(vulture_warn.subprocess, "run", _fake_run(1, calls))
        assert vulture_warn.main(["--src-path", "src/pkg", "src/pkg/a.py"]) == 0
        err = capsys.readouterr().err
        assert "did not scan (exit 1" in err and "WARNINGS ONLY" not in err

    def test_the_whitelist_is_appended_from_the_env(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(vulture_warn.subprocess, "run", _fake_run(0, calls))
        monkeypatch.setenv("PY_CI_SHARED_SRC_PATH", "src/pkg")
        monkeypatch.setenv("PY_CI_SHARED_VULTURE_WHITELIST", "scripts/vulture_whitelist.py")
        assert vulture_warn.main(["src/pkg/a.py"]) == 0
        assert calls[-1][-2:] == ["src/pkg/a.py", "scripts/vulture_whitelist.py"]
