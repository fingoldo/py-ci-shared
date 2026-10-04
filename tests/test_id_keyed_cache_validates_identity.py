"""Tests for py_ci_shared.id_keyed_cache_validates_identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.id_keyed_cache_validates_identity import RULE, assert_id_keyed_cache_validates_identity, find_id_keyed_cache_validates_identity

BOM = b"\xef\xbb\xbf"

VIOLATION = "_REG = {}\n\n\ndef register(arr, codes):\n    _REG[id(arr)] = codes\n\n\ndef lookup(arr):\n    return _REG.get(id(arr))\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _find(root, **kw):
    return find_id_keyed_cache_validates_identity(root, use_git=False, **kw)


def _lines(tmp_path: Path, source: str) -> list[int]:
    return [f.line for f in _find(_corpus(tmp_path, {"m.py": source.encode()}))]


def test_reports_the_seeded_violation(tmp_path):
    """The unchecked read is the finding, at the read's line; the write that builds the key is not."""
    found = _find(_corpus(tmp_path, {"m.py": VIOLATION.encode()}))
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 9, RULE)]
    assert "_REG" in found[0].message and "lookup()" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """Same registry, but the value carries the object and the reader compares it with `is`."""
    src = (
        "_REG = {}\n\n\ndef register(arr, codes):\n    _REG[id(arr)] = (arr, codes)\n\n\n"
        "def lookup(arr):\n    entry = _REG.get(id(arr))\n    if entry is not None and entry[0] is arr:\n        return entry[1]\n    return None\n"
    )
    assert _lines(tmp_path, src) == []


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("(id(arr), arr.shape)", id="tuple"),
        pytest.param('f"{id(arr)}:{arr.dtype}"', id="f-string"),
        pytest.param("id(arr) ^ 1", id="arithmetic"),
        pytest.param("_key(arr)", id="helper-returning-id"),
    ],
)
def test_keys_built_from_id_in_any_shape_are_caught(tmp_path, key):
    src = f"_REG = {{}}\n\ndef _key(o):\n    return (id(o), 1)\n\ndef lookup(arr):\n    return _REG.get({key})\n"
    assert _lines(tmp_path, src) == [7]


def test_a_key_held_in_a_local_name_is_followed(tmp_path):
    src = "_REG = {}\n\ndef lookup(arr):\n    key = (id(arr), 1)\n    k2 = key\n    return _REG[k2]\n"
    assert _lines(tmp_path, src) == [6]


@pytest.mark.parametrize(
    "read",
    ["_REG.get(id(a))", "_REG[id(a)]", "_REG.pop(id(a), None)", "_REG.setdefault(id(a), 1)", "(id(a) in _REG)"],
)
def test_every_read_shape_is_caught(tmp_path, read):
    assert _lines(tmp_path, f"_REG = {{}}\n\ndef f(a):\n    return {read}\n") == [4]


@pytest.mark.parametrize("factory", ["dict()", "OrderedDict()", "weakref.WeakValueDictionary()", "collections.OrderedDict()"])
def test_every_dict_like_factory_is_a_cache(tmp_path, factory):
    src = f"import collections, weakref\nfrom collections import OrderedDict\n_REG = {factory}\n\ndef f(a):\n    return _REG.get(id(a))\n"
    assert _lines(tmp_path, src) == [6]


def test_a_module_that_stores_a_weakref_is_exempt(tmp_path):
    src = VIOLATION.replace("_REG = {}", "import weakref\n_REG = {}").replace("    _REG[id(arr)] = codes", "    _REG[id(arr)] = (weakref.ref(arr), codes)")
    assert _lines(tmp_path, src) == []
    fin = "import weakref\n_REG = {}\ndef register(a):\n    _REG[id(a)] = 1\n    weakref.finalize(a, _REG.pop, id(a), None)\ndef g(a):\n    return _REG.get(id(a))\n"
    assert _lines(tmp_path / "fin", fin) == []


def test_importing_weakref_without_storing_one_is_not_an_exemption(tmp_path):
    assert _lines(tmp_path, "import weakref\n" + VIOLATION) == [10]


def test_an_identity_check_in_another_function_does_not_vouch_for_the_reader(tmp_path):
    src = VIOLATION + "\n\ndef other(a, b):\n    return a is b\n"
    assert _lines(tmp_path, src) == [9]


def test_a_comparison_with_none_is_not_an_identity_check(tmp_path):
    src = "_REG = {}\n\ndef f(a):\n    hit = _REG.get(id(a))\n    if hit is None:\n        return 0\n    return hit\n"
    assert _lines(tmp_path, src) == [4]


def test_a_cache_never_keyed_by_id_is_not_reported(tmp_path):
    src = "_REG = {}\n\ndef f(name):\n    return _REG.get(name)\n\ndef g(name):\n    return _REG.get(name.lower())\n"
    assert _lines(tmp_path, src) == []


def test_id_used_for_something_else_is_not_reported(tmp_path):
    src = "_REG = {}\n\ndef f(a, name):\n    log(id(a))\n    return _REG.get(name)\n"
    assert _lines(tmp_path, src) == []


def test_a_local_dict_with_the_same_name_is_not_the_module_cache(tmp_path):
    src = "_REG = {}\n\ndef f(a, _REG):\n    return _REG.get(id(a))\n\ndef g(a):\n    _REG = {}\n    return _REG.get(id(a))\n"
    assert _lines(tmp_path, src) == []


def test_each_reading_function_is_reported_once_per_cache(tmp_path):
    src = "_REG = {}\n\ndef f(a):\n    x = _REG.get(id(a))\n    return _REG[id(a)] or x\n\ndef g(a):\n    return _REG.get(id(a))\n"
    assert _lines(tmp_path, src) == [4, 8]


def test_the_marker_on_the_read_the_write_or_the_definition_suppresses_it(tmp_path):
    on_read = VIOLATION.replace("_REG.get(id(arr))", "_REG.get(id(arr))  # id-key-ok: value holds a strong ref")
    on_write = VIOLATION.replace("= codes", "= codes  # id-key-ok: the key is cleared on free")
    on_def = VIOLATION.replace("_REG = {}", "_REG = {}  # id-key-ok: process-lifetime objects")
    assert _lines(tmp_path / "r", on_read) == []
    assert _lines(tmp_path / "w", on_write) == []
    assert _lines(tmp_path / "d", on_def) == []
    assert _lines(tmp_path / "x", VIOLATION.replace("_REG.get(id(arr))", "_REG.get(id(arr))  # id-key-ok")) == []
    assert _lines(tmp_path / "n", VIOLATION.replace("_REG.get(id(arr))", "_REG.get(id(arr))  # other note")) == [9]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    a = _find(_corpus(tmp_path / "a", {"m.py": VIOLATION.encode()}))
    b = _find(_corpus(tmp_path / "b", {"m.py": BOM + VIOLATION.encode()}))
    assert [(f.line, f.message) for f in a] == [(f.line, f.message) for f in b] != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": VIOLATION.encode()})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert len(_find(root, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path)
    root = _corpus(tmp_path, {"a.py": b"x = 1\n"})
    with pytest.raises(EmptyScanError):
        _find(root, min_files=2)


def test_the_assert_names_the_site_and_passes_a_clean_tree(tmp_path):
    root = _corpus(tmp_path / "bad", {"m.py": VIOLATION.encode()})
    with pytest.raises(AssertionError, match=r"m\.py:9: \[id-keyed-cache-validates-identity\]"):
        assert_id_keyed_cache_validates_identity(root, use_git=False)
    assert_id_keyed_cache_validates_identity(_corpus(tmp_path / "ok", {"m.py": b"x = 1\n"}), use_git=False)


def test_id_of_a_module_level_constant_is_not_address_reuse(tmp_path):
    """`_C.get(id(_CONST))` keys on an object that lives as long as the process; a parameter or local of that name is not."""
    src = "_C = {}\n_CONST = (1, 2)\n\ndef f():\n    return _C.get(id(_CONST))\n"
    assert _lines(tmp_path / "a", src) == []
    shadowed = "_C = {}\n_CONST = (1, 2)\n\ndef g(_CONST):\n    return _C.get(id(_CONST))\n"
    assert _lines(tmp_path / "b", shadowed) == [5]


def test_a_helper_with_a_content_key_branch_is_not_an_id_helper(tmp_path):
    """A key function that returns `id` only for the non-array fallback and a content hash otherwise keys by content."""
    src = (
        "_C = {}\n\ndef _fp(p):\n    if not hasattr(p, 'shape'):\n        return ('obj', id(p))\n    return (p.shape, hash(p.tobytes()))\n\n"
        "def f(p):\n    return _C.get(_fp(p))\n"
    )
    assert _lines(tmp_path, src) == []
