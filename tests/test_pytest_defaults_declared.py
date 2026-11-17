"""Tests for py_ci_shared.pytest_defaults_declared: the mandatory pytest flags must be in the repo's ``addopts``."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError
from py_ci_shared.pytest_defaults_declared import assert_pytest_defaults_declared, find_pytest_defaults_declared


def _repo(tmp_path: Path, text: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
    return tmp_path


def test_reports_both_flags_missing_from_the_seeded_violation(tmp_path):
    found = find_pytest_defaults_declared(_repo(tmp_path, '[tool.pytest.ini_options]\naddopts = "-v --tb=short"\n'))
    assert [f.message for f in found] == [
        "addopts has no --durations=<n>: the slow tests of a bare pytest run go unreported",
        "addopts lacks -p no:cacheprovider: a bare pytest run writes .pytest_cache into the checkout",
    ]
    assert {f.line for f in found} == {2}


def test_the_nearest_correct_addopts_is_not_reported(tmp_path):
    assert find_pytest_defaults_declared(_repo(tmp_path, '[tool.pytest.ini_options]\naddopts = "-v --durations=200 -p no:cacheprovider"\n')) == []


def test_a_list_form_addopts_is_read_like_the_string_form(tmp_path):
    text = '[tool.pytest.ini_options]\naddopts = ["-v", "--durations=50", "-p", "no:cacheprovider"]\n'
    assert find_pytest_defaults_declared(_repo(tmp_path, text)) == []


def test_only_the_missing_durations_is_reported(tmp_path):
    found = find_pytest_defaults_declared(_repo(tmp_path, '[tool.pytest.ini_options]\naddopts = "-p no:cacheprovider"\n'))
    assert [f.message for f in found] == ["addopts has no --durations=<n>: the slow tests of a bare pytest run go unreported"]


def test_a_repo_without_the_pytest_table_is_one_finding(tmp_path):
    found = find_pytest_defaults_declared(_repo(tmp_path, '[project]\nname = "demo"\n'))
    assert [f.message for f in found] == ["no [tool.pytest.ini_options] table: the mandatory pytest flags are not declared"]


def test_a_root_without_a_pyproject_fails_the_scan(tmp_path):
    with pytest.raises(EmptyScanError):
        find_pytest_defaults_declared(tmp_path)


def test_the_assertion_names_every_missing_flag(tmp_path):
    with pytest.raises(AssertionError, match=r"2 pytest-defaults-declared finding\(s\)"):
        assert_pytest_defaults_declared(_repo(tmp_path, '[tool.pytest.ini_options]\naddopts = "-v"\n'))


def test_a_bom_prefixed_pyproject_is_read_like_the_plain_one(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(b"\xef\xbb\xbf" + b'[tool.pytest.ini_options]\naddopts = "-v --durations=200 -p no:cacheprovider"\n')
    assert find_pytest_defaults_declared(tmp_path) == []
