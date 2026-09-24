from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.module_cache_thread_safety import RULE_CACHE, RULE_IMPORT, assert_thread_safe_module_caches, find_thread_unsafe_module_state


def _write(root: Path, rel: str, body: str, *, bom: bool = False) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + textwrap.dedent(body).encode("utf-8"))
    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree
    return path


def _found(root: Path, **kw: object) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.rule) for f in find_thread_unsafe_module_state(root, use_git=False, **kw)]  # type: ignore[arg-type]


RACY_BODIES = {
    "insert_then_evict": "_CACHE[k] = v\n    if len(_CACHE) > 8:\n        _CACHE.popitem()",
    "move_to_end_lru": "if k in _CACHE:\n        _CACHE.move_to_end(k)\n    _CACHE[k] = v",
    "del_and_setdefault": "_CACHE.setdefault(k, v)\n    del _CACHE[k]",
    "augmented_element": "_CACHE[k] += 1",
    "element_append": "_CACHE.setdefault(k, []).append(v)",
    "element_via_local": "sizes = _CACHE.setdefault(k, {})\n    sizes[v] = 1",
    "clear_then_fill": "_CACHE.clear()\n    _CACHE.update(v)",
}


@pytest.mark.parametrize("body", list(RACY_BODIES.values()), ids=list(RACY_BODIES))
def test_non_atomic_update_is_flagged(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "m.py", f"_CACHE = {{}}\ndef f(k, v):\n    {body}\n")
    assert _found(tmp_path) == [("m.py", 1, RULE_CACHE)]


SAFE_BODIES = {
    "memo_insert_only": "_CACHE[k] = v",
    "clear_only": "_CACHE.clear()",
    "read_only": "return _CACHE.get(k)",
    "local_shadow": "_CACHE = {}\n    _CACHE[k] = v\n    _CACHE.pop(k)",
}


@pytest.mark.parametrize("body", list(SAFE_BODIES.values()), ids=list(SAFE_BODIES))
def test_atomic_or_local_updates_are_not_flagged(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "m.py", f"_CACHE = {{}}\ndef f(k, v):\n    {body}\n")
    assert _found(tmp_path) == []


def test_strict_flags_a_lone_insert_but_not_a_lone_clear(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "_CACHE = {}\ndef f(k, v):\n    _CACHE[k] = v\ndef g():\n    _CACHE.clear()\n")
    assert _found(tmp_path, strict=True) == [("m.py", 1, RULE_CACHE)]
    _write(tmp_path, "m.py", "_CACHE = {}\ndef g():\n    _CACHE.clear()\n")
    assert _found(tmp_path, strict=True) == []


def test_a_lock_anywhere_in_the_module_exempts_it(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "import threading as t\n_LOCK = t.RLock()\n_CACHE = {}\ndef f(k, v):\n    _CACHE[k] = v\n    _CACHE.pop(k)\n")
    assert _found(tmp_path) == []


def test_import_time_writes_async_writers_and_non_cache_names_are_exempt(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        from collections import OrderedDict
        _CACHE = OrderedDict()
        _CACHE["a"] = 1
        _CACHE.pop("a")
        _REGISTRY = {}
        async def f(k, v):
            _CACHE[k] = v
            _CACHE.pop(k)
        def g(k, v):
            _REGISTRY[k] = v
            _REGISTRY.pop(k)
        """,
    )
    assert _found(tmp_path) == []


def test_global_rebind_plus_write_and_dict_factories(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        import collections
        _memo = collections.defaultdict(list)
        def f(k):
            global _memo
            _memo = {}
            _memo[k] = 1
        """,
    )
    assert _found(tmp_path) == [("m.py", 3, RULE_CACHE)]


def test_custom_name_pattern(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "_STORE = {}\ndef f(k):\n    _STORE[k] = 1\n    _STORE.pop(k)\n")
    assert _found(tmp_path) == []
    assert _found(tmp_path, cache_name_pattern="STORE") == [("m.py", 1, RULE_CACHE)]


def test_lazy_from_import_in_delayed_callee(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        from joblib import Parallel, delayed
        def work(x):
            import math
            from os import path
            def inner():
                from json import dumps
            return x
        def safe(x):
            from os import sep  # joblib-import-race-ok: os is loaded at startup
            return x
        def not_dispatched(x):
            from os import sep
        def run(xs):
            return Parallel()(delayed(work)(x) for x in xs) + Parallel()(delayed(safe)(x) for x in xs)
        """,
    )
    assert _found(tmp_path) == [("m.py", 5, RULE_IMPORT), ("m.py", 7, RULE_IMPORT)]


def test_delayed_through_module_attribute(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "import joblib\ndef work(x):\n    from os import sep\ndef run(xs):\n    return [joblib.delayed(work)(x) for x in xs]\n")
    assert _found(tmp_path) == [("m.py", 3, RULE_IMPORT)]


def test_tests_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "tests/h.py", "_CACHE = {}\ndef f(k):\n    _CACHE[k] = 1\n    _CACHE.pop(k)\n")
    assert _found(tmp_path) == []
    assert _found(tmp_path, include_tests=True) == [("tests/h.py", 1, RULE_CACHE)]


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "_CACHE = {}\ndef f(k):\n    _CACHE[k] += 1\n", bom=True)
    assert _found(tmp_path) == [("m.py", 1, RULE_CACHE)]


def test_unparsable_file_and_empty_corpus_fail(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_thread_safe_module_caches(tmp_path, use_git=False)
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "bad.py", "def (:\n")
    assert [f.rule for f in find_thread_unsafe_module_state(tmp_path, use_git=False)] == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_thread_safe_module_caches(tmp_path, use_git=False)


def test_assert_raw_and_baseline_ratchet(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", "_CACHE = {}\ndef f(k):\n    _CACHE[k] = 1\n")
    assert_thread_safe_module_caches(src, use_git=False)
    _write(src, "m.py", "_CACHE = {}\ndef f(k):\n    _CACHE[k] += 1\n")
    with pytest.raises(pytest.fail.Exception, match=r"\[unlocked-cache\]"):
        assert_thread_safe_module_caches(src, use_git=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_thread_safe_module_caches(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_thread_safe_module_caches(src, baseline_path=baseline, refresh=True, use_git=False)
    assert_thread_safe_module_caches(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "n.py", "_CACHE = {}\ndef f(k):\n    _CACHE[k] += 1\n")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_thread_safe_module_caches(src, baseline_path=baseline, refresh=False, use_git=False)
