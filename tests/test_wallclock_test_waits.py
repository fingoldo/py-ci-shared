"""wallclock_test_waits: a wall-clock wait inside a test file is found; the same line outside a test, or a fake clock, is not."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.wallclock_test_waits import assert_no_new_wallclock_test_waits, find_wallclock_test_waits


def _tree(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return root


def test_python_sleep_in_a_test_is_found_by_normalized_text(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/test_poll.py", "import time\n\ndef test_x():\n        time.sleep(0.5)\n")
    assert list(find_wallclock_test_waits(tmp_path)) == ["tests/test_poll.py: time.sleep(0.5)"]


def test_dart_delayed_in_a_test_is_found(tmp_path: Path) -> None:
    _tree(tmp_path, "test/widget_test.dart", "void main() async {\n  await Future.delayed(const Duration(milliseconds: 300));\n}\n")
    assert list(find_wallclock_test_waits(tmp_path)) == ["test/widget_test.dart: await Future.delayed(const Duration(milliseconds: 300));"]


def test_a_sleep_in_library_code_and_a_fake_clock_in_a_test_are_not_found(tmp_path: Path) -> None:
    _tree(tmp_path, "src/pkg/retry.py", "import time\ntime.sleep(1)\n")
    _tree(tmp_path, "tests/test_retry.py", "def test_x(fake_clock):\n    fake_clock.advance(0.5)\n")
    assert find_wallclock_test_waits(tmp_path) == {}


def test_a_recorded_wait_passes_and_a_new_one_fails(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/test_poll.py", "import time\ntime.sleep(0.5)\n")
    with pytest.raises(AssertionError):
        assert_no_new_wallclock_test_waits(tmp_path)
    accepted = {"tests/test_poll.py: time.sleep(0.5)": "polls a real subprocess; no clock seam"}
    _tree(tmp_path, "tool/meta/baselines/wallclock_test_waits.json", json.dumps({"accepted": accepted}))
    assert_no_new_wallclock_test_waits(tmp_path)
    _tree(tmp_path, "tests/test_other.py", "import time\ntime.sleep(2)\n")
    with pytest.raises(AssertionError):
        assert_no_new_wallclock_test_waits(tmp_path)


def test_a_test_named_file_outside_a_test_directory_is_not_scanned(tmp_path: Path) -> None:
    _tree(tmp_path, "scripts/test_probe.py", "import time\ntime.sleep(1)\n")
    _tree(tmp_path, "tools/poll_test.py", "import time\ntime.sleep(1)\n")
    assert find_wallclock_test_waits(tmp_path) == {}


def test_a_sleep_in_a_conftest_is_a_finding(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/conftest.py", "import time\n\ntime.sleep(0.2)\n")
    assert list(find_wallclock_test_waits(tmp_path)) == ["tests/conftest.py: time.sleep(0.2)"]


def test_a_sleep_in_a_dart_test_helper_is_a_finding(tmp_path: Path) -> None:
    _tree(tmp_path, "test/support/helper.dart", "Future<void> settle() => Future.delayed(const Duration(milliseconds: 50));\n")
    assert list(find_wallclock_test_waits(tmp_path)) == ["test/support/helper.dart: Future<void> settle() => Future.delayed(const Duration(milliseconds: 50));"]


def test_a_comment_line_with_sleep_is_not_a_finding(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/test_poll.py", "import time\n# time.sleep(5) was removed\n    # time.sleep(2)\n")
    assert find_wallclock_test_waits(tmp_path) == {}


def test_a_real_sleep_inside_tests_is_still_a_finding(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/test_poll.py", "import time\n\ndef test_x():\n    time.sleep(0.5)  # wait for the child\n")
    assert list(find_wallclock_test_waits(tmp_path)) == ["tests/test_poll.py: time.sleep(0.5) # wait for the child"]


def test_a_sleep_inside_a_docstring_is_not_a_finding(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/test_poll.py", 'def test_x():\n    """Do not time.sleep(1) here."""\n    assert True\n')
    assert find_wallclock_test_waits(tmp_path) == {}
