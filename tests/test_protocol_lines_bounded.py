"""The warm worker's reply reader keeps every line, and a line over the cap is only its head, so the next reply still arrives."""

from __future__ import annotations

import io

from py_ci_shared._mutation_runner import _protocol_lines


def test_short_lines_come_through_unchanged():
    assert list(_protocol_lines(io.StringIO("one\ntwo\n"))) == ["one\n", "two\n"]


def test_a_line_over_the_cap_is_kept_as_its_head_and_the_next_reply_still_arrives():
    long_line = "x" * 300
    stream = io.StringIO("ok1\n" + long_line + "\n" + "ok2\n")
    lines = list(_protocol_lines(stream, cap=100))
    assert lines[0] == "ok1\n"
    assert lines[1] == "x" * 100
    assert lines[2] == "ok2\n"
    assert len(lines) == 3


def test_a_partial_last_line_is_not_lost():
    assert list(_protocol_lines(io.StringIO("done\ntail"), cap=100)) == ["done\n", "tail"]


def test_a_line_longer_than_one_read_piece_is_read_whole_up_to_the_cap():
    body = "y" * 2_500_000
    lines = list(_protocol_lines(io.StringIO(body + "\n"), cap=3_000_000))
    assert lines == [body + "\n"]
