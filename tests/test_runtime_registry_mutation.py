"""Unit tests for the runtime-registry-mutation check. Real files on disk, no mocking, matching this package's convention."""

from __future__ import annotations

import textwrap
from pathlib import Path

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
    use = _write(
        tmp_path,
        "discover.py",
        """
        from registry import _TRANSFORMS_REGISTRY

        def discover(name, t):
            _TRANSFORMS_REGISTRY[name] = t
    """,
    )
    assert _found(tmp_path, reg, use) == ["discover:_TRANSFORMS_REGISTRY"]


def test_every_mutating_shape_is_found(tmp_path):
    mod = _write(
        tmp_path,
        "m.py",
        """
        PLUGIN_REGISTRY = dict()

        def a(k, v):
            PLUGIN_REGISTRY.setdefault(k, v)

        def b(d):
            PLUGIN_REGISTRY.update(d)

        def c(k):
            PLUGIN_REGISTRY.pop(k)

        def d(k):
            del PLUGIN_REGISTRY[k]
    """,
    )
    assert sorted(_found(tmp_path, mod)) == ["a:PLUGIN_REGISTRY", "b:PLUGIN_REGISTRY", "c:PLUGIN_REGISTRY", "d:PLUGIN_REGISTRY"]


def test_import_time_helpers_and_reads_are_not_flagged(tmp_path):
    mod = _write(
        tmp_path,
        "m.py",
        """
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
    """,
    )
    assert _found(tmp_path, mod) == []


def test_a_non_registry_dict_and_a_local_shadow_of_another_name_are_ignored(tmp_path):
    mod = _write(
        tmp_path,
        "m.py",
        """
        CACHE = {}

        def put(k, v):
            CACHE[k] = v
            local = {}
            local[k] = v
    """,
    )
    assert _found(tmp_path, mod) == []


def test_assert_passes_with_a_reasoned_replay_writer_and_rejects_the_rest(tmp_path):
    reg = _write(tmp_path, "r.py", "_REGISTRY = {}\n")
    use = _write(
        tmp_path,
        "u.py",
        """
        from r import _REGISTRY

        def replay(names):
            for n in names:
                _REGISTRY[n] = n

        def discover(n):
            _REGISTRY[n] = n
    """,
    )
    with pytest.raises(AssertionError, match="discover"):
        assert_writes_have_replay([reg, use], tmp_path, {"replay": "called from __setstate__"})
    assert_writes_have_replay([reg, use], tmp_path, {"replay": "called from __setstate__", "discover": "replayed on load by replay()"})


def test_assert_rejects_an_empty_reason_and_a_stale_writer(tmp_path):
    reg = _write(tmp_path, "r.py", "_REGISTRY = {}\n")
    with pytest.raises(AssertionError, match="need a reason"):
        assert_writes_have_replay([reg], tmp_path, {"x": " "})
    with pytest.raises(AssertionError, match="no longer write"):
        assert_writes_have_replay([reg], tmp_path, {"gone": "was replayed"})


def test_a_decorator_factory_inner_function_is_import_time(tmp_path):
    mod = _write(
        tmp_path,
        "m.py",
        """
        _REGISTRY = {}

        def register(name):
            def deco(fn):
                _REGISTRY[name] = fn
                return fn
            return deco

        def runtime(name):
            def deco(fn):
                _REGISTRY[name] = fn
            return deco

        @register("f")
        def f():
            return 1
    """,
    )
    assert _found(tmp_path, mod) == ["deco:_REGISTRY"]
    assert [w.lineno for w in find_runtime_registry_writes([mod], tmp_path)] == [12]


def test_a_main_guard_call_is_not_import_time(tmp_path):
    mod = _write(
        tmp_path,
        "m.py",
        """
        _REGISTRY = {}

        def main():
            _REGISTRY["x"] = 1

        def setup():
            _REGISTRY["y"] = 1

        setup()

        if __name__ == "__main__":
            main()
    """,
    )
    assert _found(tmp_path, mod) == ["main:_REGISTRY"]


def test_a_helper_is_matched_to_the_module_it_is_imported_from(tmp_path):
    reg = _write(
        tmp_path,
        "reg.py",
        """
        _REGISTRY = {}

        def register(fn):
            _REGISTRY[fn.__name__] = fn
            return fn
    """,
    )
    other = _write(
        tmp_path,
        "other.py",
        """
        from reg import _REGISTRY

        def register(fn):
            _REGISTRY["late"] = fn
    """,
    )
    use = _write(
        tmp_path,
        "use.py",
        """
        from reg import register

        @register
        def f():
            return 1
    """,
    )
    assert _found(tmp_path, reg, other, use) == ["register:_REGISTRY"]
    assert find_runtime_registry_writes([reg, other, use], tmp_path)[0].path == "other.py"


def test_collections_dict_factories_are_registries(tmp_path):
    mod = _write(
        tmp_path,
        "m.py",
        """
        import collections
        from collections import OrderedDict as OD

        A_REGISTRY = collections.defaultdict(list)
        B_REGISTRY = collections.OrderedDict()
        C_REGISTRY = OD()
        NOT_A_REGISTRY_LIST = []

        def w(k):
            A_REGISTRY[k] = 1
            B_REGISTRY[k] = 1
            C_REGISTRY[k] = 1
    """,
    )
    assert sorted(_found(tmp_path, mod)) == ["w:A_REGISTRY", "w:B_REGISTRY", "w:C_REGISTRY"]


def test_registries_under_try_and_writes_through_a_module_or_alias(tmp_path):
    reg = _write(
        tmp_path,
        "reg.py",
        """
        try:
            import fast
        except ImportError:
            _REGISTRY = {}
        if True:
            OTHER_REGISTRY = dict()
    """,
    )
    use = _write(
        tmp_path,
        "use.py",
        """
        import reg
        from reg import OTHER_REGISTRY as R

        def a(k, v):
            reg._REGISTRY[k] = v

        def b(k, v):
            R.setdefault(k, v)

        def c(k):
            local = {}
            local[k] = 1
    """,
    )
    assert sorted(_found(tmp_path, reg, use)) == ["a:_REGISTRY", "b:OTHER_REGISTRY"]


def test_a_file_outside_the_root_and_a_bom_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "x.py"
    outside.write_bytes(b"\xef\xbb\xbf_REGISTRY = {}\n\ndef w(k):\n    _REGISTRY[k] = 1\n")
    writes = find_runtime_registry_writes([outside], root)
    assert [(w.function, w.lineno) for w in writes] == [("w", 4)] and writes[0].path.endswith("/x.py")


def test_an_unparsable_file_fails_and_the_floor_counts_parsed_files(tmp_path):
    bad = _write(tmp_path, "bad.py", "_REGISTRY = {\n")
    with pytest.raises(AssertionError, match=r"bad\.py"):
        find_runtime_registry_writes([bad], tmp_path)
    assert find_runtime_registry_writes([bad], tmp_path, allow_unparsed=True) == []
    with pytest.raises(AssertionError, match="lost its subject"):
        assert_writes_have_replay([bad], tmp_path, {}, min_files=1)
    good = _write(tmp_path, "good.py", "_REGISTRY = {}\n")
    with pytest.raises(AssertionError, match=r"bad\.py"):
        assert_writes_have_replay([bad, good], tmp_path, {}, min_files=1)
    assert_writes_have_replay([good], tmp_path, {}, min_files=1)
