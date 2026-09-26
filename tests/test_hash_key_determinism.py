"""Tests for hash_key_determinism: each sink shape, each exemption, and the order dependence itself."""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.hash_key_determinism import assert_hash_keys_are_deterministic, find_unsorted_hash_keys


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _lines(tmp_path: Path, body: str) -> list[int]:
    (tmp_path / "m.py").write_text(textwrap.dedent(body), encoding="utf-8")
    findings, _ = find_unsorted_hash_keys(tmp_path, use_git=False)
    return [f.line for f in findings]


def test_the_bug_class_is_real():
    a, b = {"x": 1, "y": 2}, {"y": 2, "x": 1}
    assert a == b
    assert hashlib.sha256(json.dumps(a).encode()).digest() != hashlib.sha256(json.dumps(b).encode()).digest()
    assert hashlib.sha256(json.dumps(a, sort_keys=True).encode()).digest() == hashlib.sha256(json.dumps(b, sort_keys=True).encode()).digest()


class TestSinks:
    @pytest.mark.parametrize(
        "expr",
        [
            "hashlib.sha256(json.dumps(cfg).encode()).hexdigest()",
            "hashlib.md5(json.dumps(cfg, indent=2).encode('utf-8'))",
            "h.update(json.dumps(cfg).encode())",
            "hash(json.dumps(cfg))",
            "zlib.crc32(json.dumps(cfg).encode())",
            "_stable_digest(json.dumps(cfg))",
            "CACHE[json.dumps(cfg)]",
            "CACHE.get(json.dumps(cfg))",
            "SEEN.add(json.dumps(cfg))",
        ],
    )
    def test_each_sink_is_reported(self, tmp_path, expr):
        body = f"import hashlib, json, zlib\nCACHE = {{}}\nSEEN = set()\ndef f(cfg, h, _stable_digest):\n    return {expr}\n"
        assert _lines(tmp_path, body) == [5]

    def test_through_a_local_name_and_through_a_key_variable(self, tmp_path):
        body = """
            import hashlib, json
            def f(cfg):
                text = json.dumps(cfg)
                return hashlib.sha1(text.encode()).hexdigest()
            def g(cfg, store):
                cache_key = json.dumps(cfg)
                return store[cache_key]
            def h(cfg):
                text = json.dumps(cfg)
                return text
        """
        assert _lines(tmp_path, body) == [4, 7]

    def test_orjson_and_an_aliased_import(self, tmp_path):
        body = """
            import hashlib
            import orjson
            from json import dumps as jd
            def f(cfg):
                return hashlib.sha256(orjson.dumps(cfg)).hexdigest(), hashlib.sha256(jd(cfg).encode()).hexdigest()
        """
        assert _lines(tmp_path, body) == [6, 6]


class TestNotReported:
    @pytest.mark.parametrize(
        "call",
        [
            "json.dumps(cfg, sort_keys=True)",
            "json.dumps(cfg, indent=1, sort_keys=True)",
            "orjson.dumps(cfg, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)",
            "json.dumps(cfg, **opts)",
            "json.dumps([a, b])",
            "json.dumps(sorted(items))",
            "json.dumps([x for x in items])",
            "json.dumps('literal')",
            "json.dumps(cfg)  # unsorted-ok: cfg is an OrderedDict built from a sorted list",
        ],
    )
    def test_sorted_or_order_is_value_or_marked(self, tmp_path, call):
        body = f"import hashlib, json, orjson\ndef f(cfg, a, b, items, opts):\n    blob = {call}\n    return hashlib.sha256(str(blob).encode())\n"
        assert _lines(tmp_path, body) == []

    def test_the_same_payload_unsorted_through_a_name_is_reported(self, tmp_path):
        body = "import hashlib, json\ndef f(cfg):\n    blob = json.dumps(cfg)\n    return hashlib.sha256(str(blob).encode())\n"
        assert _lines(tmp_path, body) == [3]

    def test_sort_keys_false_is_not_sorting_and_a_dict_inside_a_list_is_not_exempt(self, tmp_path):
        body = (
            "import hashlib, json\ndef f(cfg):\n"
            "    return hashlib.sha256(json.dumps(cfg, sort_keys=False).encode()), hashlib.sha256(json.dumps([{'a': 1}, cfg]).encode())\n"
        )
        assert _lines(tmp_path, body) == [3, 3]

    def test_a_membership_test_is_not_judged_and_tests_are_out_of_scope_by_default(self, tmp_path):
        body = "import hashlib, json\ndef f(pw, script):\n    return json.dumps(pw) in script\n"
        assert _lines(tmp_path, body) == []
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "t.py").write_text("import hashlib, json\ndef t(c):\n    return hashlib.sha1(json.dumps(c).encode())\n", encoding="utf-8")
        assert find_unsorted_hash_keys(tmp_path, use_git=False)[0] == []
        assert [f.path for f in find_unsorted_hash_keys(tmp_path, exclude_parts=(), use_git=False)[0]] == ["tests/t.py"]

    def test_a_dumps_that_is_written_or_logged_is_not_a_key(self, tmp_path):
        body = "import json\ndef f(cfg, path, log):\n    path.write_text(json.dumps(cfg))\n    log.info(json.dumps(cfg))\n    return json.dumps(cfg)\n"
        assert _lines(tmp_path, body) == []

    def test_a_nested_function_is_its_own_scope(self, tmp_path):
        body = """
            import hashlib, json
            def outer(cfg):
                text = json.dumps(cfg)
                def inner(text):
                    return hashlib.sha256(text.encode())
                return text
        """
        assert _lines(tmp_path, body) == []


class TestCorpusAndBaseline:
    def test_module_level_bom_unparsable_and_empty(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "m.py").write_bytes(b"\xef\xbb\xbfimport hashlib, json\nKEY = hashlib.sha1(json.dumps({'a': 1}).encode())\n")
        assert [f.line for f in find_unsorted_hash_keys(tmp_path / "a", use_git=False)[0]] == [2]
        (tmp_path / "a" / "bad.py").write_text("def (:\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
            assert_hash_keys_are_deterministic(tmp_path / "a", use_git=False)
        (tmp_path / "e").mkdir()
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_hash_keys_are_deterministic(tmp_path / "e", use_git=False)

    def test_the_baseline_ratchet(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "m.py").write_text("import hashlib, json\ndef f(c):\n    return hashlib.sha1(json.dumps(c).encode())\n", encoding="utf-8")
        bl = tmp_path / "bl.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_hash_keys_are_deterministic(src, baseline_path=bl, use_git=False)
        with pytest.raises(pytest.skip.Exception):
            assert_hash_keys_are_deterministic(src, baseline_path=bl, refresh=True, use_git=False)
        assert len(json.loads(bl.read_text(encoding="utf-8"))["entries"]) == 1
        assert_hash_keys_are_deterministic(src, baseline_path=bl, use_git=False)
        (src / "n.py").write_text("import hashlib, json\ndef g(c):\n    return hashlib.md5(json.dumps(c).encode())\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="unsorted-json-key"):
            assert_hash_keys_are_deterministic(src, baseline_path=bl, use_git=False)
