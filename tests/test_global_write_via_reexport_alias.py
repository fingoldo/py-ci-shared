"""global_write_via_reexport_alias: `alias._NAME = v` where the alias module only re-exports _NAME rewrites the facade's copy, not the global."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError
from py_ci_shared.global_write_via_reexport_alias import (
    RULE,
    assert_global_write_via_reexport_alias,
    find_global_write_via_reexport_alias,
)

NL = chr(10)


def _pkg(tmp_path: Path, files: dict[str, list[str]]) -> Path:
    root = tmp_path / "pkg"
    for rel, lines in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(NL.join(lines) + NL, encoding="utf-8")
    (root / "__init__.py").touch()
    return root


INCIDENT = {
    "impl.py": ["_LIMIT = 10"],
    "facade.py": ["from .impl import _LIMIT"],
    "user.py": ["from . import facade as _f", "", "def set_limit(v):", "    _f._LIMIT = v"],
}


def test_a_write_through_a_reexporting_facade_is_reported_at_the_write(tmp_path):
    root = _pkg(tmp_path, INCIDENT)
    found = find_global_write_via_reexport_alias(root, use_git=False, min_files=1)
    assert [(f.path, f.line, f.rule) for f in found] == [("user.py", 4, RULE)]
    assert "_LIMIT" in found[0].message and "impl" in found[0].message


def test_a_write_through_the_owning_module_is_clean(tmp_path):
    files = dict(INCIDENT)
    files["user.py"] = ["from . import impl as _i", "", "def set_limit(v):", "    _i._LIMIT = v"]
    assert find_global_write_via_reexport_alias(_pkg(tmp_path, files), use_git=False, min_files=1) == []


def test_a_facade_that_defines_the_name_itself_is_not_a_re_export(tmp_path):
    files = dict(INCIDENT)
    files["facade.py"] = ["_LIMIT = 10"]
    assert find_global_write_via_reexport_alias(_pkg(tmp_path, files), use_git=False, min_files=1) == []


def test_a_facade_that_defines_the_name_after_an_import_is_not_a_re_export(tmp_path):
    files = dict(INCIDENT)
    files["facade.py"] = ["from .impl import _LIMIT", "_LIMIT = 11"]
    assert find_global_write_via_reexport_alias(_pkg(tmp_path, files), use_git=False, min_files=1) == []


def test_a_lowercase_or_plain_attribute_write_is_not_checked(tmp_path):
    files = dict(INCIDENT)
    files["user.py"] = ["from . import facade as _f", "", "def set_limit(v):", "    _f.limit = v"]
    assert find_global_write_via_reexport_alias(_pkg(tmp_path, files), use_git=False, min_files=1) == []


def test_an_unresolvable_facade_is_not_a_finding(tmp_path):
    files = {"user.py": ["from . import missing as _f", "_f._LIMIT = 1"]}
    assert find_global_write_via_reexport_alias(_pkg(tmp_path, files), use_git=False, min_files=1) == []


def test_a_reasoned_marker_accepts_the_write(tmp_path):
    files = dict(INCIDENT)
    files["user.py"] = ["from . import facade as _f", "", "def set_limit(v):", "    _f._LIMIT = v  # reexport-write-ok: the facade is the intended target, reviewed"]
    assert find_global_write_via_reexport_alias(_pkg(tmp_path, files), use_git=False, min_files=1) == []


def test_an_empty_tree_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        assert_global_write_via_reexport_alias(tmp_path, use_git=False)
