"""49-F9: a directory passed to a warn hook by hand was ignored and the run exited 0 without a word."""

from __future__ import annotations

import sys

from py_ci_shared import bandit_warn, format_warn


def test_format_warn_says_a_directory_argument_was_not_checked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["format_warn", str(tmp_path)])
    format_warn.main()
    assert "is a directory, not a file: nothing was checked" in capsys.readouterr().err


def test_bandit_warn_says_a_directory_argument_was_not_scanned(tmp_path, capsys):
    rc = bandit_warn.main(["--src-path", "src", str(tmp_path)])
    assert rc == 0 and "is a directory, not a file: the hook scans staged .py files only" in capsys.readouterr().err


def test_a_commit_with_no_python_files_stays_quiet(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["format_warn", "README.md"])
    format_warn.main()
    assert capsys.readouterr().err == ""
