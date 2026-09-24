"""Tests for pickle_state_completeness: real objects through a real pickle, and seeded classes on disk for the static rules."""

from __future__ import annotations

import json
import textwrap
import threading
from pathlib import Path
from typing import Any, Optional

import pytest

from py_ci_shared.pickle_state_completeness import (
    RULE_NO_GETSTATE,
    RULE_NOT_EXCLUDED,
    assert_no_pickle_state_gaps,
    assert_pickle_round_trips,
    find_pickle_state_gaps,
    round_trip_failures,
)


class LazyLock:
    """Picklable until used: the lock appears on first call."""

    def __init__(self) -> None:
        self.n = 1

    def work(self) -> int:
        if not hasattr(self, "_lock"):
            self._lock = threading.Lock()
        with self._lock:
            return self.n


class DropsAndRebuilds(LazyLock):
    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state.pop("_lock", None)
        return state


class DropsButAssumesPresent:
    def __init__(self) -> None:
        self._lock: Optional[Any] = None

    def work(self) -> None:
        if self._lock is None:
            self._lock = threading.Lock()
        self._lock.acquire()
        self._lock.release()

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        del state["_lock"]  # dropped without a default: the clone has no attribute at all
        return state


class TestRuntime:
    def test_a_cache_created_on_use_fails_at_the_pickle_stage(self):
        failures = round_trip_failures({"lazy": LazyLock}, warm={"lazy": lambda o: o.work()})
        assert [(f.name, f.stage) for f in failures] == [("lazy", "pickle")]
        assert "lock" in str(failures[0].error)

    def test_without_the_warm_up_the_same_object_passes(self):
        assert round_trip_failures({"lazy": LazyLock}) == []

    def test_a_dropped_and_rebuilt_cache_passes(self):
        assert round_trip_failures({"ok": DropsAndRebuilds}, warm={"ok": lambda o: o.work()}) == []

    def test_a_dropped_attribute_the_clone_cannot_rebuild_fails_after_unpickle(self):
        failures = round_trip_failures({"bad": DropsButAssumesPresent}, warm={"bad": lambda o: o.work()})
        assert [(f.name, f.stage) for f in failures] == [("bad", "warm after unpickle")]

    def test_build_and_warm_failures_are_reported_by_stage(self):
        def boom() -> Any:
            raise RuntimeError("no fixture")

        failures = round_trip_failures({"b": boom, "w": LazyLock}, warm={"w": lambda o: 1 / 0})
        assert sorted((f.name, f.stage) for f in failures) == [("b", "build"), ("w", "warm")]

    def test_the_assert_fails_on_a_failure_on_no_factories_and_on_an_orphan_warm_up(self):
        with pytest.raises(pytest.fail.Exception, match="lazy: pickle failed"):
            assert_pickle_round_trips({"lazy": LazyLock}, warm={"lazy": lambda o: o.work()})
        with pytest.raises(pytest.fail.Exception, match="no factories"):
            assert_pickle_round_trips({})
        with pytest.raises(pytest.fail.Exception, match="name no factory"):
            assert_pickle_round_trips({"ok": DropsAndRebuilds}, warm={"typo": lambda o: None})
        assert_pickle_round_trips({"ok": DropsAndRebuilds}, warm={"ok": lambda o: o.work()})

    def test_custom_dumps_and_loads_are_used(self):
        calls: list[str] = []

        def dumps(obj: Any, protocol: int) -> bytes:
            calls.append("dumps")
            return b"x"

        def loads(blob: bytes) -> Any:
            calls.append("loads")
            return LazyLock()

        assert round_trip_failures({"a": LazyLock}, dumps=dumps, loads=loads) == []
        assert calls == ["dumps", "loads"]


def _static(tmp_path: Path, body: str) -> list[tuple[str, int]]:
    (tmp_path / "m.py").write_text(textwrap.dedent(body), encoding="utf-8")
    findings, _ = find_pickle_state_gaps(tmp_path, use_git=False)
    return [(f.rule, f.line) for f in findings]


class TestStateNotExcluded:
    def test_a_lazily_set_cache_copied_by_getstate_is_reported(self, tmp_path):
        body = """
            class C:
                def __getstate__(self):
                    state = self.__dict__.copy()
                    state.pop("_lock", None)
                    return state
                def run(self):
                    self._adjacency_cache = {}
                    self._lock = object()
        """
        assert _static(tmp_path, body) == [(RULE_NOT_EXCLUDED, 8)]

    @pytest.mark.parametrize("start", ["dict(self.__dict__)", "vars(self).copy()", "super().__getstate__()"])
    def test_every_copy_form_is_judged(self, tmp_path, start):
        body = f"class C:\n    def __getstate__(self):\n        return {start}\n    def run(self):\n        self._memo = {{}}\n"
        assert _static(tmp_path, body) == [(RULE_NOT_EXCLUDED, 5)]

    def test_an_explicit_key_getstate_is_not_judged(self, tmp_path):
        body = "class C:\n    def __getstate__(self):\n        return {'a': self.a}\n    def run(self):\n        self._memo = {}\n"
        assert _static(tmp_path, body) == []

    def test_names_listed_in_a_module_or_class_constant_count_as_excluded(self, tmp_path):
        body = """
            _MEMOS = ("_adjacency_cache",)
            _ALL = _MEMOS + ("_pos_cache",)
            class C:
                _POINTERS = ("_dmatrix_cache",)
                def __getstate__(self):
                    state = dict(self.__dict__)
                    for name in _ALL + self._POINTERS:
                        state.pop(name, None)
                    return state
                def run(self):
                    self._adjacency_cache = {}
                    self._pos_cache = {}
                    self._dmatrix_cache = None
                    self._other_cache = {}
        """
        assert _static(tmp_path, body) == [(RULE_NOT_EXCLUDED, 15)]

    def test_init_setstate_flags_and_modifier_names_are_not_runtime_state(self, tmp_path):
        body = """
            class C:
                def __init__(self):
                    self._cache = {}
                def __setstate__(self, state):
                    self._memo = {}
                def __getstate__(self):
                    return self.__dict__.copy()
                def run(self, corr):
                    self._caches_warmed = False
                    self._identity_cache_ycorr_ = corr
                    self._cached_lookups = {}
        """
        assert _static(tmp_path, body) == [(RULE_NOT_EXCLUDED, 12)]

    def test_one_finding_per_attribute(self, tmp_path):
        body = (
            "class C:\n    def __getstate__(self):\n        return self.__dict__.copy()\n"
            "    def a(self):\n        self._memo = 1 + 1\n    def b(self):\n        self._memo = {}\n"
        )
        assert _static(tmp_path, body) == [(RULE_NOT_EXCLUDED, 5)]


class TestUnpicklableWithoutGetstate:
    def test_a_lazily_created_lock_or_connection_is_reported_through_aliases(self, tmp_path):
        body = """
            import threading as th
            from sqlite3 import connect
            class C:
                def ensure(self):
                    self._conn = connect("x.db")
                    self._guard = th.RLock()
                    self._n = 0
        """
        assert _static(tmp_path, body) == [(RULE_NO_GETSTATE, 6), (RULE_NO_GETSTATE, 7)]

    @pytest.mark.parametrize("reducer", ["__getstate__", "__reduce__", "__reduce_ex__"])
    def test_a_class_with_any_reducer_is_not(self, tmp_path, reducer):
        body = f"import threading\nclass C:\n    def {reducer}(self, *a):\n        return {{}}\n    def ensure(self):\n        self._lock = threading.Lock()\n"
        assert _static(tmp_path, body) == []

    def test_inside_init_it_is_left_to_the_init_scanner(self, tmp_path):
        body = "import threading\nclass C:\n    def __init__(self):\n        self._lock = threading.Lock()\n"
        assert _static(tmp_path, body) == []


class TestCorpusAndBaseline:
    def test_bom_unparsable_and_empty(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "m.py").write_bytes(b"\xef\xbb\xbfimport threading\nclass C:\n    def f(self):\n        self._l = threading.Lock()\n")
        assert [f.line for f in find_pickle_state_gaps(tmp_path / "a", use_git=False)[0]] == [4]
        (tmp_path / "a" / "bad.py").write_text("class (:\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
            assert_no_pickle_state_gaps(tmp_path / "a", use_git=False)
        (tmp_path / "e").mkdir()
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_no_pickle_state_gaps(tmp_path / "e", use_git=False)

    def test_the_baseline_ratchet(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "m.py").write_text("import threading\nclass C:\n    def f(self):\n        self._l = threading.Lock()\n", encoding="utf-8")
        bl = tmp_path / "bl.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_pickle_state_gaps(src, baseline_path=bl, use_git=False)
        with pytest.raises(pytest.skip.Exception):
            assert_no_pickle_state_gaps(src, baseline_path=bl, refresh=True, use_git=False)
        assert len(json.loads(bl.read_text(encoding="utf-8"))["entries"]) == 1
        assert_no_pickle_state_gaps(src, baseline_path=bl, use_git=False)
        (src / "n.py").write_text("import threading\nclass D:\n    def f(self):\n        self._l = threading.Event()\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"threading.Event"):
            assert_no_pickle_state_gaps(src, baseline_path=bl, use_git=False)
