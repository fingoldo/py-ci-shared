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


_SPLIT_IN_CACHE = """
    import threading

    _guard = threading.Lock()
    _items = {}

    def remember(key, value):
        if key not in _items:
            with _guard:
                _items[key] = value
        return _items[key]
"""


def test_exempt_entry_drops_the_matching_finding(tmp_path):
    p = tmp_path / "mod.py"
    p.write_text(_module(_SPLIT_IN_CACHE), encoding="utf-8")
    assert find_check_then_act_outside_lock([p], min_files=1)
    assert not find_check_then_act_outside_lock([p], min_files=1, exempt={("mod.py", "remember"): "pure value, duplicate compute is harmless"})


def test_exempt_entry_matches_on_path_suffix_and_function_name(tmp_path):
    p = tmp_path / "mod.py"
    p.write_text(_module(_SPLIT_IN_CACHE), encoding="utf-8")
    assert find_check_then_act_outside_lock([p], min_files=1, exempt={("other.py", "remember"): "wrong file"})
    assert find_check_then_act_outside_lock([p], min_files=1, exempt={("mod.py", "forget"): "wrong function"})


def test_assert_fails_on_an_exemption_that_matches_nothing(tmp_path):
    p = tmp_path / "mod.py"
    p.write_text(_module("def fine():\n    return 1\n"), encoding="utf-8")
    with pytest.raises(AssertionError, match="match no finding"):
        assert_no_check_then_act_outside_lock([p], min_files=1, exempt={("mod.py", "remember"): "stale"})


def test_assert_passes_when_every_finding_is_exempt_and_every_exemption_is_live(tmp_path):
    p = tmp_path / "mod.py"
    p.write_text(_module(_SPLIT_IN_CACHE), encoding="utf-8")
    assert_no_check_then_act_outside_lock([p], min_files=1, exempt={("mod.py", "remember"): "pure value, duplicate compute is harmless"})


@pytest.mark.parametrize("reason", ["", "   "])
def test_exemption_without_a_reason_is_refused(tmp_path, reason):
    p = tmp_path / "mod.py"
    p.write_text(_module(_SPLIT_IN_CACHE), encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty reason"):
        find_check_then_act_outside_lock([p], min_files=1, exempt={("mod.py", "remember"): reason})


def test_a_test_and_write_under_a_guard_helper_that_takes_the_lock_is_not_a_finding(tmp_path):
    body = textwrap.dedent("""
        import threading
        from contextlib import contextmanager

        class Cache:
            def __init__(self):
                self._lock = threading.Lock()
                self._items = {}

            @contextmanager
            def _guard(self):
                with self._lock:
                    yield

            def put(self, key, value):
                with self._guard():
                    if key not in self._items:
                        self._items[key] = value
        """)
    (tmp_path / "m.py").write_text(body, encoding="utf-8")
    assert find_check_then_act_outside_lock([tmp_path / "m.py"], use_git=False) == []


@pytest.mark.parametrize("act", ["del _C[key]", "_C.pop(key)", "_C.update({key: 1})"])
def test_non_subscript_acts_after_an_unlocked_test_are_found(tmp_path, act):
    body = f"""
        _C = {{}}

        def f(key):
            if key in _C:
                {act}
    """
    assert len(_lines(tmp_path, _module(body))) == 1


def test_class_level_dict_through_cls_is_found(tmp_path):
    body = """
        class A:
            _c = {}

            @classmethod
            def put(cls, key):
                if key not in cls._c:
                    cls._c[key] = 1
    """
    assert len(_lines(tmp_path, _module(body))) == 1


def test_non_subscript_act_under_one_lock_is_clean(tmp_path):
    body = """
        _C = {}

        def f(key):
            with _guard:
                if key in _C:
                    del _C[key]
    """
    assert _lines(tmp_path, _module(body)) == []


def test_setdefault_after_a_test_is_atomic_and_not_an_act(tmp_path):
    body = """
        _C = {}

        def f(key):
            if key not in _C:
                return _C.setdefault(key, 1)
    """
    assert _lines(tmp_path, _module(body)) == []


def test_lock_table_removal_that_rechecks_under_its_own_lock_block_is_clean(tmp_path):
    body = """
        _locks = {}

        def release(key):
            with _guard:
                entry = _locks.get(key)
            try:
                work()
            finally:
                with _guard:
                    entry.refcount -= 1
                    if entry.refcount == 0:
                        del _locks[key]
    """
    assert _lines(tmp_path, _module(body)) == []


def test_idempotent_pop_with_a_default_is_not_an_act(tmp_path):
    body = """
        _C = {}

        def f(key):
            with _guard:
                last = _C.get(key, 0)
            with _guard:
                _C.pop(key, None)
    """
    assert _lines(tmp_path, _module(body)) == []


@pytest.mark.parametrize("name,doc", [("_is_banned_unlocked", "Check ban status."), ("_check", "Check it (caller holds the lock).")])
def test_helper_documented_as_called_under_the_lock_is_not_judged(tmp_path, name, doc):
    body = f"""
        _C = {{}}

        def {name}(key):
            '''{doc}'''
            e = _C.get(key)
            if e is None:
                return False
            del _C[key]
            return True
    """
    assert _lines(tmp_path, _module(body)) == []


def test_unlocked_test_then_guarded_removal_is_still_a_race(tmp_path):
    body = """
        _locks = {}

        def release(key):
            if key in _locks:
                with _guard:
                    if _locks[key].refcount == 0:
                        del _locks[key]
    """
    assert len(_lines(tmp_path, _module(body))) == 1


def test_removal_after_a_test_in_a_different_lock_block_without_recheck_is_a_race(tmp_path):
    body = """
        _C = {}

        def f(key):
            with _guard:
                present = key in _C
            with _guard:
                del _C[key]
    """
    assert len(_lines(tmp_path, _module(body))) == 1
