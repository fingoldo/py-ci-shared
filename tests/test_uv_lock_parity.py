"""Tests for py_ci_shared.uv_lock_parity."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.uv_lock_parity import (
    RULE_MISSING,
    RULE_NO_ROOT,
    RULE_PYTHON,
    RULE_SPECIFIER,
    RULE_STALE,
    assert_uv_lock_parity,
    find_uv_lock_parity,
)

BOM = b"\xef\xbb\xbf"

PYPROJECT = """[project]
name = "My_Pkg"
version = "0"
requires-python = ">=3.9"
dependencies = ["requests >= 2.0, < 3", "numpy", "my_pkg[all]"]

[project.optional-dependencies]
test = ["pytest>=7"]

[dependency-groups]
dev = ["black", {include-group = "other"}]
other = ["ruff"]
"""

LOCK = """version = 1
requires-python = ">=3.9"

[[package]]
name = "my-pkg"
version = "0"
source = { editable = "." }

[package.metadata]
requires-dist = [
    { name = "numpy" },
    { name = "pytest", marker = "extra == 'test'", specifier = ">=7" },
    { name = "requests", specifier = "<3,>=2.0" },
]

[package.metadata.requires-dev]
dev = [{ name = "black" }]
other = [{ name = "ruff" }]
"""


def _find(root, **kw):
    return find_uv_lock_parity(root, use_git=False, **kw)


def _repo(tmp_path: Path, pyproject: str = PYPROJECT, lock: str = LOCK) -> Path:
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(lock, encoding="utf-8")
    return tmp_path


def _rules(tmp_path: Path, **edits):
    pyproject = edits.get("pyproject", PYPROJECT)
    lock = LOCK
    for old, new in edits.get("lock", []):
        assert old in lock
        lock = lock.replace(old, new)
    return [f.rule for f in _find(_repo(tmp_path, pyproject, lock))]


def test_negative_control_names_spacing_and_clause_order_agree(tmp_path):
    """`My_Pkg` vs `my-pkg`, `>= 2.0, < 3` vs `<3,>=2.0`, a self-extra and an include-group are all fine."""
    assert _rules(tmp_path) == []


def test_a_requirement_added_without_relocking_is_missing(tmp_path):
    """mlframe b5931c12b: the dependency edit that left the lock behind."""
    assert _rules(tmp_path, lock=[('    { name = "numpy" },\n', "")]) == [RULE_MISSING]


def test_a_changed_specifier_differs(tmp_path):
    assert _rules(tmp_path, lock=[('"<3,>=2.0"', '">=2.0"')]) == [RULE_SPECIFIER]


def test_a_removed_requirement_is_stale(tmp_path):
    assert _rules(tmp_path, lock=[('    { name = "numpy" },\n', '    { name = "numpy" },\n    { name = "flask" },\n')]) == [RULE_STALE]


def test_an_extra_moved_to_main_is_reported_both_ways(tmp_path):
    rules = _rules(tmp_path, lock=[(", marker = \"extra == 'test'\"", "")])
    assert sorted(rules) == sorted([RULE_MISSING, RULE_STALE])


def test_a_dev_group_change_is_reported(tmp_path):
    assert _rules(tmp_path, lock=[('dev = [{ name = "black" }]', 'dev = [{ name = "black" }, { name = "mypy" }]')]) == [RULE_STALE]


def test_requires_python_mismatch(tmp_path):
    assert _rules(tmp_path, lock=[('requires-python = ">=3.9"', 'requires-python = ">=3.8"')]) == [RULE_PYTHON]


def test_a_lock_without_the_root_package_cannot_be_vouched_for(tmp_path):
    root = _repo(tmp_path, lock='version = 1\nrequires-python = ">=3.9"\n')
    assert [f.rule for f in _find(root)] == [RULE_NO_ROOT]


def test_a_direct_reference_compares_by_name_only(tmp_path):
    pyproject = PYPROJECT.replace('"numpy"', '"numpy @ git+https://example.org/numpy.git"')
    lock = LOCK.replace('{ name = "numpy" }', '{ name = "numpy", git = "https://example.org/numpy.git" }')
    assert [f.rule for f in _find(_repo(tmp_path, pyproject, lock))] == []


def test_a_tool_uv_sources_override_keeps_its_declared_specifier(tmp_path):
    """mlframe: `pyutilz>=1.0.0` pinned to a git source in the lock has no specifier there."""
    pyproject = PYPROJECT.replace('"numpy"', '"numpy>=1"')
    lock = LOCK.replace('{ name = "numpy" }', '{ name = "numpy", git = "https://example.org/numpy.git" }')
    assert [f.rule for f in _find(_repo(tmp_path, pyproject, lock))] == []


def test_a_pyproject_without_a_project_table_is_skipped(tmp_path):
    assert _find(_repo(tmp_path, "[tool.x]\na = 1\n")) == []


def test_findings_are_located_by_the_lock_path_in_a_subdirectory(tmp_path):
    sub = tmp_path / "svc"
    sub.mkdir()
    _repo(sub, lock=LOCK.replace('"<3,>=2.0"', '">=9"'))
    assert [(f.path, f.rule) for f in _find(tmp_path)] == [("svc/uv.lock", RULE_SPECIFIER)]


def test_a_bom_prefixed_lock_is_read_like_the_plain_one(tmp_path):
    root = _repo(tmp_path, lock=LOCK.replace('"<3,>=2.0"', '">=9"'))
    (root / "uv.lock").write_bytes(BOM + (root / "uv.lock").read_bytes())
    assert [f.rule for f in _find(root)] == [RULE_SPECIFIER]


def test_an_unparsable_lock_fails_by_name_unless_allowed(tmp_path):
    root = _repo(tmp_path, lock="not = = toml [\n")
    with pytest.raises(UnparsedFilesError, match=r"uv\.lock"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_no_lock_file_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)
    assert _find(tmp_path, min_files=0) == []


def test_assert_names_the_fix_and_passes_a_clean_repo(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "good").mkdir()
    with pytest.raises(AssertionError, match=r"run `uv lock`"):
        assert_uv_lock_parity(_repo(tmp_path / "bad", lock=LOCK.replace('"<3,>=2.0"', '">=9"')), use_git=False)
    assert_uv_lock_parity(_repo(tmp_path / "good"), use_git=False)


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    bad = LOCK.replace('"<3,>=2.0"', '">=9"')
    root = tmp_path / "r"
    root.mkdir()
    _repo(root, lock=bad)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_uv_lock_parity(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_uv_lock_parity(root, baseline_path=baseline, use_git=False)
    (root / "uv.lock").write_text(bad.replace('{ name = "numpy" },\n', ""), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_uv_lock_parity(root, baseline_path=baseline, use_git=False)
