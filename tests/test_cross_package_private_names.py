"""Tests for py_ci_shared.cross_package_private_names."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from py_ci_shared._core import CorpusError, EmptyScanError, UnparsedFilesError
from py_ci_shared.cross_package_private_names import AMBIGUOUS_RULE, RULE, assert_cross_package_private_names, find_cross_package_private_names

BOM = b"\xef\xbb\xbf"

B_MOD = "def _helper():\n    return 1\n\n\ndef public():\n    return 2\n\n\nVALUE = 3\n_CACHE = {}\n"


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else str(data).encode("utf-8"))
    return tmp_path


def _pkgs(root: Path, *labels: str) -> dict:
    return {label: root / label for label in (labels or ("a", "b"))}


def _a_over_b(tmp_path: Path, a_source: str, b_source: str = B_MOD, extra: Optional[dict] = None) -> list:
    files = {"a/consumer.py": a_source, "b/b_mod.py": b_source}
    files.update(extra or {})
    root = _corpus(tmp_path, files)
    return find_cross_package_private_names(_pkgs(root), use_git=False)


def _keys(found: list) -> list:
    return [(f.line, f.message.split(": ", 1)[0]) for f in found]


def test_reports_the_seeded_violation(tmp_path):
    found = _a_over_b(tmp_path, '"""doc"""\nfrom b_mod import _helper\n')
    assert [(f.path, f.line, f.rule) for f in found] == [("consumer.py", 2, RULE)]
    assert found[0].message.startswith("a->b:b_mod._helper: a reaches a private name of b's module b_mod")


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    assert _a_over_b(tmp_path, "from b_mod import public\nimport b_mod\nx = b_mod.VALUE\n") == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _a_over_b(tmp_path / "p", "from b_mod import _helper\n")
    root = _corpus(tmp_path / "r", {"a/consumer.py": BOM + b"from b_mod import _helper\n", "b/b_mod.py": BOM + B_MOD.encode()})
    bom = find_cross_package_private_names(_pkgs(root), use_git=False)
    assert plain == bom
    assert len(bom) == 1


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "x = 1\n", "a/broken.py": "def broken(:\n", "b/b_mod.py": B_MOD})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_cross_package_private_names(_pkgs(root), use_git=False)
    assert find_cross_package_private_names(_pkgs(root), use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with pytest.raises(EmptyScanError):
        find_cross_package_private_names(_pkgs(tmp_path), use_git=False)


def test_the_floor_applies_to_every_package_not_to_the_sum(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "x = 1\n", "a/two.py": "y = 1\n", "b/b_mod.py": B_MOD})
    with pytest.raises(EmptyScanError):
        find_cross_package_private_names(_pkgs(root), use_git=False, min_files=2)


def test_a_missing_package_root_is_an_error_not_a_pass(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "x = 1\n"})
    with pytest.raises(CorpusError):
        find_cross_package_private_names(_pkgs(root), use_git=False)


def test_every_alias_shape_that_binds_the_module_is_followed(tmp_path):
    source = (
        "import b_mod as bm\n"
        "from b_mod import public\n"
        "bm._CACHE.clear()\n"  # the incident's `tq._counts_cache.clear()`
        "bm._CACHE = {}\n"  # write
        "y = bm._helper()\n"  # call
        "del bm._CACHE\n"  # delete
    )
    assert _keys(_a_over_b(tmp_path, source)) == [(3, "a->b:b_mod._CACHE"), (4, "a->b:b_mod._CACHE"), (5, "a->b:b_mod._helper"), (6, "a->b:b_mod._CACHE")]


def test_a_submodule_bound_by_from_import_or_by_dotted_import_is_followed(tmp_path):
    source = "from pkg import sub as s\nimport pkg\nimport pkg.sub\na = s._X\nb = pkg.sub._X\n"
    found = _a_over_b(tmp_path, source, extra={"b/pkg/__init__.py": "", "b/pkg/sub.py": "_X = 1\nX = _X\n"})
    assert _keys(found) == [(4, "a->b:pkg.sub._X"), (5, "a->b:pkg.sub._X")]


def test_a_private_attribute_of_a_class_or_object_is_not_a_module_reach(tmp_path):
    source = "from klass_mod import Klass\nz = Klass._x\nimport b_mod as bm\nq = bm.public._attr\n"
    assert _a_over_b(tmp_path, source, extra={"b/klass_mod.py": "class Klass:\n    _x = 1\n"}) == []


def test_an_unimported_local_name_is_not_a_module(tmp_path):
    assert _a_over_b(tmp_path, "b_mod = object()\nz = b_mod._helper\n") == []


def test_dunder_underscore_and_relative_imports_are_exempt(tmp_path):
    source = "import b_mod\nfrom b_mod import __version__, _\nfrom . import _sibling\nfrom .x import _y\nv = b_mod.__doc__\nw = b_mod._\n"
    assert _a_over_b(tmp_path, source, B_MOD + "__version__ = 1\n_ = 1\n") == []


def test_type_checking_blocks_never_bind_but_their_else_does(tmp_path):
    source = (
        "from typing import TYPE_CHECKING\n"
        "import typing\n"
        "if TYPE_CHECKING:\n"
        "    from b_mod import _helper\n"
        "    import b_mod as bm\n"
        "if typing.TYPE_CHECKING:\n"
        "    from b_mod import _CACHE\n"
        "else:\n"
        "    from b_mod import _helper as runtime_helper\n"
        "if not TYPE_CHECKING:\n"
        "    from b_mod import _CACHE as live\n"
        "x: 'bm._CACHE'\n"
    )
    assert _keys(_a_over_b(tmp_path, source)) == [(9, "a->b:b_mod._helper"), (11, "a->b:b_mod._CACHE")]


def test_a_public_alias_is_still_a_finding_whose_message_names_the_alias(tmp_path):
    b = "def _get_dsn():\n    return 1\n\n\nget_dsn = _get_dsn\n_other = 1\n"
    found = _a_over_b(tmp_path, "from b_mod import _get_dsn, _other\n", b)
    by_name = {f.message.split(": ", 1)[0]: f.message for f in found}
    assert "a public alias exists: use b_mod.get_dsn" in by_name["a->b:b_mod._get_dsn"]
    assert "public alias" not in by_name["a->b:b_mod._other"]


def test_a_public_alias_by_reexport_import_is_found_in_a_package_init(tmp_path):
    found = _a_over_b(
        tmp_path, "from pkg import _build\n", extra={"b/pkg/__init__.py": "from .impl import _build as build\n", "b/pkg/impl.py": "def _build():\n    pass\n"}
    )
    assert "a public alias exists: use pkg.build" in found[0].message


def test_a_package_does_not_reach_itself(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "from own import _x\nimport own\ny = own._x\n", "a/own.py": "_x = 1\n", "b/b_mod.py": B_MOD})
    assert find_cross_package_private_names(_pkgs(root), use_git=False) == []


def test_tests_are_skipped_unless_asked_for(tmp_path):
    files = {
        "a/consumer.py": "x = 1\n",
        "b/b_mod.py": B_MOD,
        "a/tests/test_x.py": "from b_mod import _helper\n",
        "a/test_y.py": "from b_mod import _helper\n",
        "a/conftest.py": "from b_mod import _helper\n",
        "a/y_test.py": "from b_mod import _helper\n",
    }
    root = _corpus(tmp_path, files)
    assert find_cross_package_private_names(_pkgs(root), use_git=False) == []
    found = find_cross_package_private_names(_pkgs(root), use_git=False, include_tests=True)
    assert sorted(f.path for f in found) == ["conftest.py", "test_y.py", "tests/test_x.py", "y_test.py"]


def test_a_name_in_several_packages_is_listed_as_skipped_not_passed(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "from db import _x\n", "b/db.py": "_x = 1\n", "c/db.py": "_x = 2\n", "c/other.py": "z = 1\n"})
    packages = _pkgs(root, "a", "b", "c")
    found = find_cross_package_private_names(packages, use_git=False)
    assert [(f.rule, f.line) for f in found] == [(AMBIGUOUS_RULE, 1)]
    assert "skipped" in found[0].message and "db._x" in found[0].message
    with pytest.raises(AssertionError, match="found in several packages"):
        assert_cross_package_private_names(packages, use_git=False)


def test_a_sys_path_resolves_the_name_first_hit_wins_and_own_modules_are_not_foreign(tmp_path):
    files = {
        "a/consumer.py": "from db import _x\n",
        "a/db.py": "_x = 0\n",
        "b/db.py": "_x = 1\n",
        "c/consumer.py": "from db import _x\n",
        "c/db.py": "_x = 2\n",
    }
    root = _corpus(tmp_path, files)
    specs = {
        "a": {"root": root / "a", "sys_path": ["a", "b"]},  # its own db first: not foreign
        "b": {"root": root / "b", "sys_path": ["b"]},
        "c": {"root": root / "c", "sys_path": ["b", "c"]},  # b's db wins over its own
    }
    found = find_cross_package_private_names(specs, use_git=False)
    assert [(f.path, f.message.split(": ", 1)[0]) for f in found] == [("consumer.py", "c->b:db._x")]


def test_a_bad_package_spec_is_an_error(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "x = 1\n"})
    with pytest.raises(ValueError, match="unknown package"):
        find_cross_package_private_names({"a": {"root": root / "a", "sys_path": ["nope"]}}, use_git=False)
    with pytest.raises(ValueError, match="'root'"):
        find_cross_package_private_names({"a": {"sys_path": []}}, use_git=False)


def test_allowed_accepts_a_reach_with_a_reason(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "from b_mod import _helper\n", "b/b_mod.py": B_MOD})
    assert_cross_package_private_names(_pkgs(root), allowed={"a->b:b_mod._helper": "pinned by a dedicated contract test"}, use_git=False)
    with pytest.raises(AssertionError, match=r"a->b:b_mod._helper"):
        assert_cross_package_private_names(_pkgs(root), allowed={"a->b:b_mod._other": "reason"}, use_git=False)


def test_allowed_entries_need_a_reason_and_may_only_shrink(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "from b_mod import _helper\n", "b/b_mod.py": B_MOD})
    with pytest.raises(AssertionError, match="without a reason"):
        assert_cross_package_private_names(_pkgs(root), allowed={"a->b:b_mod._helper": "  "}, use_git=False)
    clean = _corpus(tmp_path / "clean", {"a/consumer.py": "x = 1\n", "b/b_mod.py": B_MOD})
    with pytest.raises(AssertionError, match=r"no longer occur.*\n.*a->b:b_mod\._helper"):
        assert_cross_package_private_names(_pkgs(clean), allowed={"a->b:b_mod._helper": "was needed"}, use_git=False)


def test_the_assert_names_every_finding_with_its_location(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "import b_mod\nx = b_mod._helper\ny = b_mod._CACHE\n", "b/b_mod.py": B_MOD})
    with pytest.raises(AssertionError) as exc:
        assert_cross_package_private_names(_pkgs(root), use_git=False)
    text = str(exc.value)
    assert "2 cross-package private name finding(s)" in text and "consumer.py:2:" in text and "consumer.py:3:" in text


def test_the_assert_takes_the_contract_keywords(tmp_path):
    root = _corpus(tmp_path, {"a/consumer.py": "x = 1\n", "a/broken.py": "def x(:\n", "b/b_mod.py": B_MOD})
    assert_cross_package_private_names(_pkgs(root), use_git=False, allow_unparsed=True, min_files=1)
    with pytest.raises(UnparsedFilesError):
        assert_cross_package_private_names(_pkgs(root), use_git=False)
