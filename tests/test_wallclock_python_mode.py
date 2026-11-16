"""``python_mode`` on the wall-clock gate: the default is the ``"ast"`` rule (a real wait call only), ``"text"`` keeps the
rule every pre-1.23.12 baseline was recorded with, and an unparsable file is held to the text rule in both."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.wallclock_test_waits import find_wallclock_test_waits

LITERAL_ONLY = 'def test_x(tmp_path):\n    (tmp_path / "t.py").write_text("time.sleep(0.5)")\n'
REAL_CALL = "import time\n\n\ndef test_x():\n    time.sleep(0.5)\n"
UNPARSABLE = "def test_x(:\n    time.sleep(0.5)\n"


def _tree(tmp_path: Path, text: str) -> Path:
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_case.py").write_text(text, encoding="utf-8")
    return tmp_path


def test_the_text_mode_still_flags_a_wait_written_inside_a_string_literal(tmp_path: Path) -> None:
    assert find_wallclock_test_waits(_tree(tmp_path, LITERAL_ONLY), python_mode="text")


def test_the_default_does_not_flag_a_wait_written_inside_a_string_literal(tmp_path: Path) -> None:
    assert find_wallclock_test_waits(_tree(tmp_path, LITERAL_ONLY)) == {}


def test_ast_mode_does_not_flag_text_inside_a_string_literal(tmp_path: Path) -> None:
    assert find_wallclock_test_waits(_tree(tmp_path, LITERAL_ONLY), python_mode="ast") == {}


def test_ast_mode_flags_a_real_time_sleep_call(tmp_path: Path) -> None:
    found = find_wallclock_test_waits(_tree(tmp_path, REAL_CALL), python_mode="ast")
    assert len(found) == 1 and "time.sleep(0.5)" in next(iter(found))


def test_ast_mode_holds_an_unparsable_file_to_the_text_rule(tmp_path: Path) -> None:
    assert find_wallclock_test_waits(_tree(tmp_path, UNPARSABLE), python_mode="ast")


def test_an_unknown_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="python_mode must be"):
        find_wallclock_test_waits(_tree(tmp_path, REAL_CALL), python_mode="regex")
