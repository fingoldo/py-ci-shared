"""Unit tests for the runtime-registry-mutation check. Real files on disk, no mocking, matching this package's convention."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.runtime_registry_mutation import assert_writes_have_replay, find_runtime_registry_writes


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _found(tmp_path: Path, *files: Path) -> list[str]:
    return [f"{w.function}:{w.registry}" for w in find_runtime_registry_writes(list(files), tmp_path)]


def test_a_function_writing_an_imported_registry_is_found(tmp_path):
    reg = _write(tmp_path, "registry.py", "_TRANSFORMS_REGISTRY = {}\n")
    use = _write(tmp_path, "discover.py", """
        from registry import _TRANSFORMS_REGISTRY

        def discover(name, t):
            _TRANSFORMS_REGISTRY[name] = t
    """)
    assert _found(tmp_path, reg, use) == ["discover:_TRANSFORMS_REGISTRY"]


def test_every_mutating_shape_is_found(tmp_path):
    mod = _write(tmp_path, "m.py", """
        PLUGIN_REGISTRY = dict()

        def a(k, v):
            PLUGIN_REGISTRY.setdefault(k, v)

        def b(d):
            PLUGIN_REGISTRY.update(d)

        def c(k):
            PLUGIN_REGISTRY.pop(k)

        def d(k):
            del PLUGIN_REGISTRY[k]
    """)
    assert sorted(_found(tmp_path, mod)) == ["a:PLUGIN_REGISTRY", "b:PLUGIN_REGISTRY", "c:PLUGIN_REGISTRY", "d:PLUGIN_REGISTRY"]


def test_import_time_helpers_and_reads_are_not_flagged(tmp_path):
    mod = _write(tmp_path, "m.py", """
        _REGISTRY = {}

        def register(fn):
            _REGISTRY[fn.__name__] = fn
            return fn

        def add(name, fn):
            _REGISTRY[name] = fn

        @register
        def f():
            return _REGISTRY.get("f")

        add("g", len)
        _REGISTRY["h"] = abs
    """)
    assert _found(tmp_path, mod) == []


def test_a_non_registry_dict_and_a_local_shadow_of_another_name_are_ignored(tmp_path):
    mod = _write(tmp_path, "m.py", """
        CACHE = {}

        def put(k, v):
            CACHE[k] = v
            local = {}
            local[k] = v
    """)
    assert _found(tmp_path, mod) == []


def test_assert_passes_with_a_reasoned_replay_writer_and_rejects_the_rest(tmp_path):
    reg = _write(tmp_path, "r.py", "_REGISTRY = {}\n")
    use = _write(tmp_path, "u.py", """
        from r import _REGISTRY

        def replay(names):
            for n in names:
                _REGISTRY[n] = n

        def discover(n):
            _REGISTRY[n] = n
    """)
    with pytest.raises(AssertionError, match="discover"):
        assert_writes_have_replay([reg, use], tmp_path, {"replay": "called from __setstate__"})
    assert_writes_have_replay([reg, use], tmp_path, {"replay": "called from __setstate__", "discover": "replayed on load by replay()"})


def test_assert_rejects_an_empty_reason_and_a_stale_writer(tmp_path):
    reg = _write(tmp_path, "r.py", "_REGISTRY = {}\n")
    with pytest.raises(AssertionError, match="need a reason"):
        assert_writes_have_replay([reg], tmp_path, {"x": " "})
    with pytest.raises(AssertionError, match="no longer write"):
        assert_writes_have_replay([reg], tmp_path, {"gone": "was replayed"})
