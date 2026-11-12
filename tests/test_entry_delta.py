"""49-F8: a refresh that writes a baseline for the first time reports it as created, not as +0 -0 entries."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared._core.outcome import entry_delta


def test_a_first_time_write_is_reported_as_created_with_its_entry_count():
    assert entry_delta([(Path("/r/fl.json"), None, '{"a.py::f": 3, "a.py::g": 4}')]) == "created fl.json with 2 entries"


def test_an_empty_first_time_write_still_says_created():
    assert entry_delta([(Path("/r/fl.json"), None, "{}")]) == "created fl.json with 0 entries"


def test_a_rewrite_keeps_the_added_and_removed_counts():
    assert entry_delta([(Path("/r/fl.json"), '{"a": 1}', '{"a": 2, "b": 1}')]) == "+2 -1 entries (fl.json)"
    assert entry_delta([]) is None
