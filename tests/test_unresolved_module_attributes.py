"""Tests for py_ci_shared.unresolved_module_attributes."""

from __future__ import annotations

from pathlib import Path
from typing import Union

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.unresolved_module_attributes import (
    assert_no_unresolved_module_attributes,
    find_unresolved_module_attributes,
    scan_module_attributes,
)

BOM = b"\xef\xbb\xbf"


def _corpus(tmp_path: Path, files: "dict[str, Union[bytes, str]]") -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return tmp_path


def _report(tmp_path: Path, files: "dict[str, Union[bytes, str]]", **kw):
    root = _corpus(tmp_path, files)
    return scan_module_attributes(root, use_git=False, **kw)


def _hits(tmp_path: Path, files: "dict[str, Union[bytes, str]]", **kw) -> "list[str]":
    return [f"{f.path}:{f.line}:{f.message.split(':')[0]}" for f in _report(tmp_path, files, **kw).findings]


HELPERS = "def present():\n    return 1\n\nVALUE = 2\n"


def test_reports_the_seeded_violation(tmp_path):
    report = _report(tmp_path, {"helpers.py": HELPERS, "user.py": "import helpers\n\n\ndef run():\n    return helpers.removed_name()\n"})
    assert [(f.path, f.line, f.rule) for f in report.findings] == [("user.py", 5, "unresolved-module-attributes")]
    assert report.findings[0].message == "'helpers.removed_name': module 'helpers' (helpers.py) does not define 'removed_name'"
    assert report.checked_reads == 1


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    assert _hits(tmp_path, {"helpers.py": HELPERS, "user.py": "import helpers\n\n\ndef run():\n    return helpers.present() + helpers.VALUE\n"}) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _hits(tmp_path / "a", {"helpers.py": HELPERS, "user.py": "import helpers\nhelpers.gone\n"})
    bom = _hits(tmp_path / "b", {"helpers.py": BOM + HELPERS.encode(), "user.py": BOM + b"import helpers\nhelpers.gone\n"})
    assert plain == bom == ["user.py:2:'helpers.gone'"]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"helpers.py": HELPERS, "broken.py": "def broken(:\n", "user.py": "import helpers\nhelpers.gone\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_unresolved_module_attributes(root, use_git=False)
    assert len(find_unresolved_module_attributes(root, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_unresolved_module_attributes(tmp_path, use_git=False)
    root = _corpus(tmp_path / "one", {"a.py": "x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_unresolved_module_attributes(root, use_git=False, min_files=2)


# ---------------------------------------------------------------- what counts as a defined name


@pytest.mark.parametrize(
    "body",
    [
        "def NAME(): pass",
        "async def NAME(): pass",
        "class NAME: pass",
        "NAME = 1",
        "NAME: int = 1",
        "NAME: int",
        "OTHER = 0\nNAME = 1\nNAME += 1",
        "NAME, OTHER = 1, 2",
        "[OTHER, (NAME, *REST)] = 1, (2, 3)",
        "import NAME",
        "import os.path as NAME",
        "from os import path as NAME",
        "import json\nNAME = json",
        "if True:\n    NAME = 1",
        "try:\n    NAME = 1\nexcept ImportError:\n    NAME = None",
        "for NAME in range(3):\n    pass",
        "with open(__file__) as NAME:\n    pass",
        "while False:\n    NAME = 1",
        "if (NAME := 3):\n    pass",
        "__all__ = ['NAME']",
        "def f():\n    global NAME\n    NAME = 1",
    ],
)
def test_every_module_level_binding_shape_defines_the_name(tmp_path, body):
    files = {"target.py": body + "\n", "user.py": "import target\ntarget.NAME\n"}
    assert _hits(tmp_path, files) == []


@pytest.mark.parametrize(
    "body",
    [
        "def f():\n    NAME = 1",
        "class C:\n    NAME = 1",
        "def f(NAME):\n    pass",
        "# NAME = 1",
        "x = 'NAME'",
        "if True:\n    def f():\n        NAME = 1",
    ],
)
def test_a_local_or_class_attribute_does_not_define_a_module_name(tmp_path, body):
    files = {"target.py": body + "\n", "user.py": "import target\ntarget.NAME\n"}
    assert _hits(tmp_path, files) == ["user.py:2:'target.NAME'"]


def test_star_import_from_a_resolvable_module_defines_its_public_names_or_its_all(tmp_path):
    files = {
        "base.py": "PUBLIC = 1\n_private = 2\n",
        "limited.py": "A = 1\nB = 2\n__all__ = ['A']\n",
        "target.py": "from base import *\nfrom limited import *\n",
        "user.py": "import target\ntarget.PUBLIC\ntarget.A\ntarget._private\ntarget.B\n",
    }
    assert _hits(tmp_path, files) == ["user.py:4:'target._private'", "user.py:5:'target.B'"]


def test_a_package_defines_its_submodules(tmp_path):
    files = {"pkg/__init__.py": "X = 1\n", "pkg/sub.py": "Y = 1\n", "pkg/inner/__init__.py": "", "user.py": "import pkg\npkg.sub\npkg.inner\npkg.X\npkg.nope\n"}
    assert _hits(tmp_path, files) == ["user.py:5:'pkg.nope'"]


# ---------------------------------------------------------------- import forms


def test_dotted_import_binds_the_top_package_and_as_binds_the_leaf(tmp_path):
    files = {
        "pkg/__init__.py": "TOP = 1\n",
        "pkg/leaf.py": "LEAF = 1\n",
        "user.py": "import pkg.leaf\nimport pkg.leaf as lf\npkg.TOP\npkg.leaf\npkg.missing_top\nlf.LEAF\nlf.missing_leaf\n",
    }
    assert _hits(tmp_path, files) == ["user.py:5:'pkg.missing_top'", "user.py:7:'lf.missing_leaf'"]


def test_from_package_import_submodule_is_an_alias_with_or_without_as(tmp_path):
    files = {
        "pkg/__init__.py": "",
        "pkg/sub.py": "S = 1\n",
        "user.py": "from pkg import sub\nfrom pkg import sub as other\nsub.S\nsub.gone\nother.gone2\n",
    }
    assert _hits(tmp_path, files) == ["user.py:4:'sub.gone'", "user.py:5:'other.gone2'"]


def test_from_package_import_a_name_is_not_a_module_alias(tmp_path):
    # `from pkg import sub` yields the name pkg binds, so the same-named file is not what `sub` is.
    files = {
        "pkg/__init__.py": "from pkg.impl import sub\n",
        "pkg/impl.py": "def sub():\n    pass\n",
        "pkg/sub.py": "S = 1\n",
        "user.py": "from pkg import sub\nsub.anything\n",
    }
    assert _hits(tmp_path, files) == []


def test_a_function_scope_import_is_checked_and_does_not_leak_to_the_module(tmp_path):
    files = {
        "helpers.py": HELPERS,
        "user.py": "def f():\n    import helpers\n    return helpers.gone\n\n\ndef g(helpers):\n    return helpers.gone\n\n\nhelpers_other = 1\n",
    }
    assert _hits(tmp_path, files) == ["user.py:3:'helpers.gone'"]


def test_a_nested_function_sees_the_enclosing_alias_but_a_class_body_does_not_leak(tmp_path):
    files = {
        "helpers.py": HELPERS,
        "user.py": "import helpers\n\n\ndef outer():\n    def inner():\n        return helpers.gone\n    return inner\n\n\nclass K:\n    helpers = object()\n    def m(self):\n        return helpers.gone2\n",
    }
    assert _hits(tmp_path, files) == ["user.py:6:'helpers.gone'", "user.py:13:'helpers.gone2'"]


def test_relative_from_import_of_a_sibling_module(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/sib.py": "OK = 1\n", "pkg/user.py": "from . import sib\nsib.OK\nsib.gone\n"}
    assert _hits(tmp_path, files) == ["pkg/user.py:3:'sib.gone'"]


def test_a_non_first_party_module_is_not_judged_and_is_counted(tmp_path):
    report = _report(tmp_path, {"user.py": "import os\nimport numpy as np\nos.no_such_thing\nnp.whatever\n"})
    assert report.findings == [] and report.external_reads == 2 and report.checked_reads == 0


# ---------------------------------------------------------------- resolve_roots


def test_resolve_roots_find_a_sibling_package_the_tests_reach_through_sys_path(tmp_path):
    files = {
        "scrapers/queries.py": "Q = 1\n",
        "app/tests/test_x.py": "import sys\nimport queries\n\n\ndef test():\n    assert queries.Q_GONE\n",
    }
    root = _corpus(tmp_path, files)
    assert find_unresolved_module_attributes(root / "app", use_git=False) == []
    found = find_unresolved_module_attributes(root / "app", use_git=False, resolve_roots=[root / "scrapers"])
    assert [(f.path, f.line) for f in found] == [("tests/test_x.py", 6)]
    assert "queries.py" in found[0].message


def test_a_missing_resolve_root_is_an_error_not_a_silent_pass(tmp_path):
    root = _corpus(tmp_path, {"a.py": "x = 1\n"})
    with pytest.raises(FileNotFoundError, match="nope"):
        find_unresolved_module_attributes(root, use_git=False, resolve_roots=[tmp_path / "nope"])


def test_a_bare_name_found_in_several_directories_is_defined_when_any_defines_it(tmp_path):
    files = {"one/config.py": "A = 1\n", "two/config.py": "B = 1\n", "scan/t.py": "import config\nconfig.A\nconfig.B\nconfig.C\n"}
    root = _corpus(tmp_path, files)
    found = find_unresolved_module_attributes(root / "scan", use_git=False, resolve_roots=[root / "one", root / "two"])
    assert [f.line for f in found] == [4]


def test_several_scan_roots_are_all_scanned(tmp_path):
    root = _corpus(tmp_path, {"a/m.py": "X = 1\n", "b/t.py": "import m\nm.gone\n"})
    found = find_unresolved_module_attributes([root / "a", root / "b"], use_git=False, resolve_roots=[root / "a"])
    assert [f.path for f in found] == ["t.py"]


# ---------------------------------------------------------------- skipped, never silently passed


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("def __getattr__(name):\n    return name\n", "dynamic"),
        ("globals()['made'] = 1\n", "dynamic"),
        ("globals().update(made=1)\n", "dynamic"),
        ("exec('made = 1')\n", "dynamic"),
        ("import sys\nsetattr(sys.modules[__name__], 'made', 1)\n", "dynamic"),
        ("import sys\n\n\nclass _M(type(sys)):\n    pass\n\n\nsys.modules[__name__].__class__ = _M\n", "dynamic"),
        ("from not_a_real_module_xyz import *\n", "unresolvable"),
        ("def broken(:\n", "cannot be parsed"),
    ],
)
def test_a_module_whose_names_cannot_be_known_is_skipped_and_reported(tmp_path, body, reason):
    report = _report(tmp_path, {"target.py": body, "user.py": "import target\ntarget.made\n"}, allow_unparsed=True)
    assert report.findings == []
    assert [(s.path, s.line, s.alias, s.module) for s in report.skipped] == [("user.py", 2, "target", "target")]
    assert reason in report.skipped[0].reason


def test_a_star_import_of_a_dynamic_module_makes_the_importer_unjudgeable(tmp_path):
    files = {"lazy.py": "def __getattr__(n):\n    return n\n", "target.py": "from lazy import *\n", "user.py": "import target\ntarget.x\n"}
    report = _report(tmp_path, files)
    assert report.findings == [] and len(report.skipped) == 1


def test_a_namespace_package_is_skipped_and_reported(tmp_path):
    report = _report(tmp_path, {"ns/a.py": "X = 1\n", "user.py": "import ns\nns.a\n"})
    assert report.findings == [] and "namespace" in report.skipped[0].reason


def test_an_alias_bound_twice_in_one_scope_is_skipped_and_reported(tmp_path):
    files = {
        "helpers.py": HELPERS,
        "user.py": "try:\n    import helpers\nexcept ImportError:\n    helpers = None\nhelpers.gone\n",
    }
    report = _report(tmp_path, files)
    assert report.findings == [] and [s.reason for s in report.skipped] == ["alias rebound in the same scope"]


def test_two_different_modules_under_one_alias_name_are_skipped(tmp_path):
    files = {
        "a.py": "X = 1\n",
        "b.py": "Y = 1\n",
        "user.py": "def f(flag):\n    if flag:\n        import a as m\n    else:\n        import b as m\n    return m.X\n",
    }
    report = _report(tmp_path, files)
    assert report.findings == [] and len(report.skipped) == 1


def test_a_clean_module_is_not_recorded_as_skipped(tmp_path):
    report = _report(tmp_path, {"helpers.py": HELPERS, "user.py": "import helpers\nhelpers.present\n"})
    assert report.skipped == [] and report.checked_reads == 1


def test_an_alias_with_no_reads_is_not_recorded_as_skipped(tmp_path):
    report = _report(tmp_path, {"target.py": "def __getattr__(n):\n    return n\n", "user.py": "import target\n"})
    assert report.skipped == []


# ---------------------------------------------------------------- exemptions


def test_hasattr_guards_the_name_in_the_alias_scope_only(tmp_path):
    files = {
        "helpers.py": HELPERS,
        "user.py": (
            "import helpers\n\n\n"
            "def guarded():\n    if hasattr(helpers, 'maybe'):\n        return helpers.maybe\n\n\n"
            "def other():\n    return helpers.maybe\n"
        ),
    }
    # the module-scope alias is judged against hasattr calls anywhere in its scope subtree, which includes `guarded`
    assert _hits(tmp_path, files) == []
    files2 = {
        "helpers.py": HELPERS,
        "user.py": "def guarded():\n    import helpers\n    return hasattr(helpers, 'maybe') and helpers.maybe\n\n\ndef other():\n    import helpers\n    return helpers.maybe\n",
    }
    assert _hits(tmp_path / "second", files2) == ["user.py:8:'helpers.maybe'"]


def test_getattr_with_a_default_is_not_a_read_and_dunders_are_ignored(tmp_path):
    files = {"helpers.py": HELPERS, "user.py": "import helpers\ngetattr(helpers, 'maybe', None)\nhelpers.__version__\nhelpers.__file__\n"}
    report = _report(tmp_path, files)
    assert report.findings == [] and report.checked_reads == 0


def test_a_name_given_to_the_module_in_the_same_file_is_not_reported(tmp_path):
    files = {
        "helpers.py": HELPERS,
        "user.py": (
            "import helpers\nhelpers.assigned = 1\nsetattr(helpers, 'set_by_call', 2)\n"
            "def t(monkeypatch):\n    monkeypatch.setattr(helpers, 'patched', 3, raising=False)\n"
            "x = helpers.assigned, helpers.set_by_call, helpers.patched, helpers.never_given\n"
        ),
    }
    assert _hits(tmp_path, files) == ["user.py:6:'helpers.never_given'"]


def test_a_store_or_delete_is_not_a_read(tmp_path):
    files = {"helpers.py": HELPERS, "user.py": "import helpers\nhelpers.fresh = 1\ndel helpers.other\n"}
    assert _report(tmp_path, files).checked_reads == 0


def test_a_read_under_except_attribute_error_or_pytest_raises_is_deliberate(tmp_path):
    files = {
        "helpers.py": HELPERS,
        "user.py": (
            "import helpers\nimport pytest\n\n\n"
            "def a():\n    try:\n        return helpers.gone\n    except AttributeError:\n        return None\n\n\n"
            "def b():\n    with pytest.raises(AttributeError):\n        helpers.gone\n\n\n"
            "def c():\n    try:\n        return helpers.gone\n    except KeyError:\n        return None\n"
        ),
    }
    assert _hits(tmp_path, files) == ["user.py:19:'helpers.gone'"]


def test_a_comprehension_variable_shadowing_the_alias_is_not_the_module(tmp_path):
    files = {"helpers.py": HELPERS, "user.py": "import helpers\nrows = [helpers.gone for helpers in [object()]]\nbad = [helpers.gone2 for _ in range(2)]\n"}
    assert _hits(tmp_path, files) == ["user.py:3:'helpers.gone2'"]


def test_only_the_first_level_of_a_chain_is_checked(tmp_path):
    files = {"helpers.py": HELPERS, "user.py": "import helpers\nhelpers.VALUE.anything.at_all\nhelpers.present().x\nhelpers.gone.deeper\n"}
    assert _hits(tmp_path, files) == ["user.py:4:'helpers.gone'"]


# ---------------------------------------------------------------- the assert entry and its baseline


def test_the_assert_entry_lists_every_finding_and_hands_back_the_skipped(tmp_path):
    root = _corpus(
        tmp_path,
        {"helpers.py": HELPERS, "lazy.py": "def __getattr__(n):\n    return n\n", "user.py": "import helpers\nimport lazy\nhelpers.a\nhelpers.b\nlazy.c\n"},
    )
    skipped: list = []
    with pytest.raises(AssertionError) as info:
        assert_no_unresolved_module_attributes(root, use_git=False, skipped_report=skipped)
    text = str(info.value)
    assert "2 unresolved-module-attributes finding(s)" in text and "user.py:3" in text and "user.py:4" in text
    assert [s.alias for s in skipped] == ["lazy"]


def test_a_baseline_accepts_known_findings_and_rejects_new_ones(tmp_path):
    root = _corpus(tmp_path / "c", {"helpers.py": HELPERS, "user.py": "import helpers\nhelpers.old\n"})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):  # a refresh run skips by design after writing the file
        assert_no_unresolved_module_attributes(root, use_git=False, baseline_path=baseline, refresh=True, grow=True)
    assert_no_unresolved_module_attributes(root, use_git=False, baseline_path=baseline)
    (root / "user.py").write_text("import helpers\nhelpers.old\nhelpers.fresh\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_no_unresolved_module_attributes(root, use_git=False, baseline_path=baseline)
