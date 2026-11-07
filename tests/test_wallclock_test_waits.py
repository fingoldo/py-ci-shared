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


def _waits(tmp_path, rel, body, mode="ast"):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return [v.split("line ")[1].split(" ")[0] for v in find_wallclock_test_waits(tmp_path, python_mode=mode).values()]


@pytest.mark.parametrize(
    "body",
    [
        "from time import sleep\n\n\ndef t():\n    sleep(1)\n",
        "import time as t\n\n\ndef x():\n    t.sleep(1)\n",
        "import asyncio\n\n\nasync def x():\n    await asyncio.sleep(1)\n",
        "import trio\n\n\nasync def x():\n    await trio.sleep(1)\n",
    ],
)
def test_ast_mode_sees_renamed_imports_and_async_sleeps(tmp_path, body):
    assert _waits(tmp_path, "tests/test_a.py", body) == ["5"]


def test_a_yield_sleep_zero_and_an_unrelated_sleep_method_are_not_waits(tmp_path):
    body = "import asyncio\n\n\nasync def x(self):\n    await asyncio.sleep(0)\n    self.sleep(3)\n"
    assert _waits(tmp_path, "tests/test_a.py", body) == []


def test_dart_delay_without_const_is_a_wait(tmp_path):
    assert _waits(tmp_path, "test/a_test.dart", "void main() {\n  await Future.delayed(Duration(milliseconds: 50));\n}\n") == ["2"]


def test_text_mode_sees_spaced_time_dot_sleep(tmp_path):
    assert _waits(tmp_path, "tests/a.py", "import time\n\n\ndef t():\n    time . sleep (1)\n", mode="text") == ["5"]


def test_a_repo_without_a_baseline_directory_passes_when_nothing_is_found(tmp_path):
    # audit 37-F19: tool/meta/baselines is optional; with no findings an absent baseline must not fail the gate
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_clean.py").write_text("def test_a():\n    assert 1 == 1\n", encoding="utf-8")
    assert_no_new_wallclock_test_waits(tmp_path)


@pytest.mark.parametrize("call", ["asyncio.sleep(1)", "trio.sleep(2)", "anyio.sleep(0.1)"])
def test_text_mode_sees_async_sleeps_in_a_file_that_does_not_parse(tmp_path, call):
    assert _waits(tmp_path, "tests/test_a.py", f"async def t(:\n    await {call}\n", mode="text") == ["2"]


@pytest.mark.parametrize("call", ["asyncio.sleep(0)", "asyncio.sleep()", "trio.sleep( 0 )"])
def test_text_mode_exempts_the_zero_and_no_argument_yield(tmp_path, call):
    assert _waits(tmp_path, "tests/test_a.py", f"async def t(:\n    await {call}\n", mode="text") == []
