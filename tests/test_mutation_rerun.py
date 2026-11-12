"""Behaviour of the mutation_rerun command line: survivor parsing, location keys and argument parsing.

The harness itself is not run here (it is slow and is covered by test_mutation_teeth); these tests pin
the parts that decide what gets re-run and recorded.
"""

from __future__ import annotations

import pytest

from py_ci_shared import mutation_rerun as mr

LOG = """\
mutation survivor: src/pyutilz/dev/logginglib.py:481:15  constant: emptied a string  '"results_log"' -> '""'
mutation survivor: src/pyutilz/dev/logginglib.py:481:29  comparison: not in becomes in  'not in' -> 'in'
mutation survivor: src/pyutilz/dev/other.py:12:3  constant: x
SURVIVOR src/pyutilz/dev/logginglib.py:485:12  deleted a statement-level call
unrelated line logginglib.py:999:1 is not a survivor
"""


class TestSurvivorLines:
    def test_returns_each_line_of_the_named_module_once_in_order(self):
        assert mr.survivor_lines(LOG, "logginglib.py") == [481, 485]

    def test_other_modules_are_not_included(self):
        assert 12 not in mr.survivor_lines(LOG, "logginglib.py")
        assert mr.survivor_lines(LOG, "other.py") == [12]

    def test_the_range_bounds_are_inclusive(self):
        assert mr.survivor_lines(LOG, "logginglib.py", 481, 481) == [481]
        assert mr.survivor_lines(LOG, "logginglib.py", 482, 484) == []

    def test_a_line_outside_the_range_is_excluded_on_both_sides(self):
        assert mr.survivor_lines(LOG, "logginglib.py", 482, 10**9) == [485]
        assert mr.survivor_lines(LOG, "logginglib.py", 1, 480) == []

    def test_a_non_survivor_line_is_ignored(self):
        assert 999 not in mr.survivor_lines(LOG, "logginglib.py")

    def test_no_survivors_gives_an_empty_list(self):
        assert mr.survivor_lines("", "logginglib.py") == []


class TestSurvivorLocation:
    def test_the_location_is_line_and_column(self):
        text = "src/pyutilz/dev/logginglib.py:481:15  constant: emptied a string"
        assert mr.survivor_location(text) == "481:15"

    def test_text_without_a_colon_has_no_location(self):
        assert mr.survivor_location("no location here") == ""

    def test_the_location_matches_the_notes_key_format(self):
        notes = {"481:15": "equivalent"}
        assert mr.survivor_location("a.py:481:15  x") in notes


class TestArgumentParsing:
    def test_lines_takes_the_module_tests_log_and_notes(self):
        args = mr._parser().parse_args(["lines", "m.py", "t.py", "log.txt", "notes.json"])
        assert (args.command, args.module, args.tests, args.log, args.notes) == ("lines", "m.py", "t.py", "log.txt", "notes.json")
        assert (args.min_line, args.max_line) == (1, 10**9)

    def test_lines_takes_an_optional_line_range(self):
        args = mr._parser().parse_args(["lines", "m.py", "t.py", "log.txt", "notes.json", "431", "470"])
        assert (args.min_line, args.max_line) == (431, 470)

    def test_the_survivors_journal_defaults_to_the_repo_convention(self):
        args = mr._parser().parse_args(["lines", "m.py", "t.py", "log.txt", "notes.json"])
        assert args.survivors == mr.DEFAULT_SURVIVORS == "tests/test_meta/mutation_survivors.json"

    def test_sweep_takes_the_module_tests_and_checkpoint(self):
        args = mr._parser().parse_args(["sweep", "m.py", "t.py", "ck.json"])
        assert (args.command, args.module, args.tests, args.checkpoint) == ("sweep", "m.py", "t.py", "ck.json")

    def test_repo_is_a_global_option_defaulting_to_the_working_directory(self):
        assert mr._parser().parse_args(["sweep", "m.py", "t.py", "ck.json"]).repo == "."
        assert mr._parser().parse_args(["--repo", "/r", "sweep", "m.py", "t.py", "ck.json"]).repo == "/r"

    def test_a_command_is_required(self):
        with pytest.raises(SystemExit):
            mr._parser().parse_args([])

    def test_an_unknown_command_is_refused(self):
        with pytest.raises(SystemExit):
            mr._parser().parse_args(["bogus", "m.py"])
