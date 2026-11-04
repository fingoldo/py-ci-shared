"""Text-ratchet gates: undecodable files are findings (33-F2) and identical lines are counted, not collapsed (33-F3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.git_child_env import find_unscrubbed_git_spawns
from py_ci_shared.guard_without_fixture import find_guards_without_fixture
from py_ci_shared.skip_as_pass import assert_no_new_skip_as_pass, find_skip_as_pass
from py_ci_shared.wallclock_test_waits import find_wallclock_test_waits

SKIP_BLOCK = b"echo SKIPPED\nexit 0\n"


def _put(root: Path, rel: str, data: bytes) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)


def test_skip_as_pass_reports_an_undecodable_file(tmp_path):
    _put(tmp_path, "tool/a.sh", b"# caf\xe9\n" + SKIP_BLOCK)
    assert list(find_skip_as_pass(tmp_path)) == ["tool/a.sh: unreadable"]


def test_wallclock_reports_an_undecodable_test_file_but_not_an_undecodable_non_test(tmp_path):
    _put(tmp_path, "tests/a.py", b"# caf\xe9\nimport time\ntime.sleep(1)\n")
    _put(tmp_path, "src/lib.py", b"# caf\xe9\n")
    assert list(find_wallclock_test_waits(tmp_path)) == ["tests/a.py: unreadable"]


def test_git_child_env_reports_an_undecodable_test_file(tmp_path):
    _put(tmp_path, "tests/test_a.py", b"# caf\xe9\n")
    assert list(find_unscrubbed_git_spawns(tmp_path)) == ["tests/test_a.py: unreadable"]


def test_guard_without_fixture_reports_an_undecodable_test_file(tmp_path):
    _put(tmp_path, "tool/check_x.sh", b"echo hi\n")
    _put(tmp_path, "tests/t.py", b"# caf\xe9\n")
    assert sorted(find_guards_without_fixture(tmp_path)) == ["tests/t.py: unreadable", "tool/check_x.sh"]


def test_identical_skip_blocks_are_counted(tmp_path):
    _put(tmp_path, "tool/a.sh", SKIP_BLOCK * 3)
    assert sorted(find_skip_as_pass(tmp_path)) == ["tool/a.sh: echo SKIPPED", "tool/a.sh: echo SKIPPED #2", "tool/a.sh: echo SKIPPED #3"]


def test_identical_sleeps_are_counted(tmp_path):
    _put(tmp_path, "tests/a.py", b"import time\n\n\ndef t():\n    time.sleep(1)\n    time.sleep(1)\n    time.sleep(1)\n")
    assert sorted(find_wallclock_test_waits(tmp_path)) == ["tests/a.py: time.sleep(1)", "tests/a.py: time.sleep(1) #2", "tests/a.py: time.sleep(1) #3"]


def test_a_baseline_accepting_one_block_does_not_hide_a_pasted_copy(tmp_path):
    _put(tmp_path, "tool/a.sh", SKIP_BLOCK)
    baseline = tmp_path / "tool/meta/baselines"
    baseline.mkdir(parents=True)
    (baseline / "skip_as_pass.json").write_text('{"accepted": {"tool/a.sh: echo SKIPPED": "legacy"}}', encoding="utf-8")
    assert_no_new_skip_as_pass(tmp_path)
    _put(tmp_path, "tool/a.sh", SKIP_BLOCK * 2)
    with pytest.raises(AssertionError, match="#2"):
        assert_no_new_skip_as_pass(tmp_path)
