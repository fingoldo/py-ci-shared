"""Unit tests for check_then_act_outside_lock: a test and a write on one shared dict with no lock across both."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared.check_then_act_outside_lock import assert_no_check_then_act_outside_lock, find_check_then_act_outside_lock

_LOCK_LINES = "import threading\n_guard = threading.Lock()\n"


def _lines(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "mod.py"
    p.write_text(body, encoding="utf-8")
    return [f.message for f in find_check_then_act_outside_lock([p], min_files=1)]


def _module(body: str) -> str:
    """A module that constructs a lock (so it is judged), then *body* dedented."""
    return _LOCK_LINES + textwrap.dedent(body)


@pytest.mark.parametrize(
    "check",
    [
        "if key not in self._items:",
        "if self._items.get(key) is None:",
    ],
)
def test_test_outside_lock_then_write_inside_lock_is_found(tmp_path, check):
    body = f"""
        import threading

        class Store:
            def __init__(self):
                self._lock = threading.Lock()
                self._items = {{}}

            def put(self, key, value):
                {check}
                    with self._lock:
                        self._items[key] = value
    """
    assert len(_lines(tmp_path, textwrap.dedent(body))) == 1


def test_unlocked_test_and_write_on_a_module_dict_is_found(tmp_path):
    body = """
        _CACHE = {}

        def remember(key, value):
            if key in _CACHE:
                return _CACHE[key]
            _CACHE[key] = value
            return value
    """
    lines = _lines(tmp_path, _module(body))
    assert len(lines) == 1 and "_CACHE" in lines[0]


def test_test_and_write_inside_one_with_block_is_not_a_finding(tmp_path):
    body = """
        _CACHE = {}

        def remember(key, value):
            with _guard:
                if key not in _CACHE:
                    _CACHE[key] = value
                return _CACHE[key]
    """
    assert _lines(tmp_path, _module(body)) == []


def test_two_separate_with_blocks_on_one_lock_are_still_a_split(tmp_path):
    body = """
        import threading

        class Store:
            def __init__(self):
                self._lock = threading.Lock()
                self._items = {}

            def put(self, key, value):
                with self._lock:
                    present = key in self._items
                if not present:
                    with self._lock:
                        self._items[key] = value
    """
    assert len(_lines(tmp_path, textwrap.dedent(body))) == 1


def test_no_lock_anywhere_in_the_module_is_not_judged(tmp_path):
    body = """
        _CACHE = {}

        def remember(key, value):
            if key not in _CACHE:
                _CACHE[key] = value
    """
    assert _lines(tmp_path, textwrap.dedent(body)) == []


def test_write_without_a_prior_test_is_not_a_finding(tmp_path):
    body = """
        _CACHE = {}

        def store(key, value):
            _CACHE[key] = value
    """
    assert _lines(tmp_path, _module(body)) == []


def test_assert_raises_on_a_split_and_passes_on_a_joint_block(tmp_path):
    split = tmp_path / "split.py"
    split.write_text(_module("_CACHE = {}\n\ndef f(k, v):\n    if k in _CACHE:\n        pass\n    _CACHE[k] = v\n"), encoding="utf-8")
    with pytest.raises(AssertionError, match="check-then-act-outside-lock"):
        assert_no_check_then_act_outside_lock([split])
    joint = tmp_path / "joint.py"
    joint.write_text(_module("_CACHE = {}\n\ndef f(k, v):\n    with _guard:\n        if k not in _CACHE:\n            _CACHE[k] = v\n"), encoding="utf-8")
    assert_no_check_then_act_outside_lock([joint])
