"""The fingerprint reads the repo's python_files the way pytest does: an explicit empty value means no python files, an absent key means the defaults."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared._mutation_fingerprint import _DEFAULT_PYTHON_FILES, _python_files_patterns


def test_an_explicit_empty_python_files_in_pytest_ini_collects_nothing(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files =\n", encoding="utf-8")
    assert _python_files_patterns(tmp_path) == ()


def test_an_absent_python_files_in_pytest_ini_uses_the_defaults(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert _python_files_patterns(tmp_path) == _DEFAULT_PYTHON_FILES


def test_explicit_patterns_in_pytest_ini_are_read(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n", encoding="utf-8")
    assert _python_files_patterns(tmp_path) == ("check_*.py",)


def test_an_explicit_empty_list_in_pyproject_collects_nothing(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\npython_files = []\n", encoding="utf-8")
    assert _python_files_patterns(tmp_path) == ()


def test_an_absent_python_files_in_pyproject_uses_the_defaults(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8")
    assert _python_files_patterns(tmp_path) == _DEFAULT_PYTHON_FILES


def test_an_explicit_empty_python_files_in_tox_ini_collects_nothing(tmp_path: Path):
    (tmp_path / "tox.ini").write_text("[pytest]\npython_files =\n", encoding="utf-8")
    assert _python_files_patterns(tmp_path) == ()
