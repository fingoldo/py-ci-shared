"""Tests for the mypy completion gate.

The gate exists to draw one distinction: a run that finished and certified the code, against a run
that never got there. Everything here is about keeping that distinction sharp -- including the case
where the gate itself got it wrong.
"""

from __future__ import annotations

import pytest

from py_ci_shared.mypy_gate import check_mypy_output

SUCCESS = "Success: no issues found in 317 source files"
WITH_ERRORS = "dashboard/db.py:12: error: Incompatible types\nFound 366 errors in 138 files (checked 165 source files)"


class TestACompletedCleanRun:
    def test_the_success_line_is_the_only_thing_that_certifies(self):
        assert check_mypy_output(SUCCESS, returncode=0) is None

    def test_a_narrowed_clean_run_is_refused(self):
        """`Success: no issues found in 3 source files` is honest and useless: the invocation
        collapsed and the two hundred files it should have covered went unchecked."""
        message = check_mypy_output("Success: no issues found in 3 source files", returncode=0, min_files=250)

        assert message is not None
        assert "silently narrowed" in message


class TestACompletedRunThatFoundErrors:
    """The regression this file was written for.

    `Found 366 errors in 138 files (checked 165 source files)` is a COMPLETION line -- the run
    finished, over a stated number of files, and the errors are real. Reporting it as "mypy did not
    print its completion line; a run that does not finish cannot certify anything" sends the reader
    hunting a broken invocation instead of reading their type errors, and that is precisely the
    confusion this module exists to prevent.
    """

    def test_it_is_not_reported_as_a_run_that_did_not_finish(self):
        message = check_mypy_output(WITH_ERRORS, returncode=1)

        assert message is not None, "errors are still a failure"
        assert "did not" not in message, message
        assert "does not finish" not in message, message

    def test_it_says_how_many_files_and_how_many_errors(self):
        message = check_mypy_output(WITH_ERRORS, returncode=1)

        assert "165 source files" in message
        assert "366 error" in message

    def test_the_scope_check_applies_to_it_too(self):
        """A run that narrowed to three files and then found two errors in them is exactly as
        uninformative as one that narrowed and found none."""
        message = check_mypy_output(
            "Found 2 errors in 1 file (checked 3 source files)",
            returncode=1,
            min_files=250,
        )

        assert "silently narrowed" in message


class TestARunThatNeverFinished:
    def test_no_terminator_at_all_is_refused(self):
        message = check_mypy_output("db.py: error: Source file found twice under different module names", returncode=2)

        assert "did not print either completion line" in message

    def test_an_internal_error_is_named_as_such(self):
        """Its findings depend on traversal order rather than on the code, which is a different
        problem from a narrowed scope and deserves its own sentence."""
        message = check_mypy_output("INTERNAL ERROR: mypy crashed", returncode=2)

        assert "INTERNAL ERROR" in message
        assert "traversal order" in message

    @pytest.mark.parametrize("returncode", [0, 1, 2])
    def test_the_exit_code_alone_never_decides(self, returncode):
        """The whole premise: an exit code cannot tell a clean run from a run that never happened,
        so the gate reads the output rather than the status."""
        assert check_mypy_output("", returncode=returncode) is not None
        assert check_mypy_output(SUCCESS, returncode=returncode) is None
