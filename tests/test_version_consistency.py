"""Unit tests for the shared version-consistency check, on real scratch trees."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from py_ci_shared.version_consistency import assert_versions_agree, version_sources


def _repo(tmp_path: Path, pyproject_version: str, init_version: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "p"\nversion = "{pyproject_version}"\n', encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text(f'__version__ = "{init_version}"\n', encoding="utf-8")
    return tmp_path


def test_agreeing_sources_pass(tmp_path):
    assert_versions_agree(_repo(tmp_path, "1.2.3", "1.2.3"), files=["pkg/__init__.py"])


def test_a_drift_fails_and_names_both_sources(tmp_path):
    with pytest.raises(pytest.fail.Exception, match=re.escape("1.2.4")):
        assert_versions_agree(_repo(tmp_path, "1.2.3", "1.2.4"), files=["pkg/__init__.py"])


def test_calendar_separators_are_equal_only_when_asked(tmp_path):
    repo = _repo(tmp_path, "2026.04.19", "2026-04-19")

    assert_versions_agree(repo, files=["pkg/__init__.py"], normalize_separators=True)
    with pytest.raises(pytest.fail.Exception):
        assert_versions_agree(repo, files=["pkg/__init__.py"])


def test_a_comparison_of_one_is_refused(tmp_path):
    """A regex that stopped matching leaves one source, which would always agree with itself."""
    repo = _repo(tmp_path, "1.0.0", "1.0.0")
    (repo / "pkg" / "__init__.py").write_text("VERSION = get_it_from_somewhere()\n", encoding="utf-8")

    assert version_sources(repo, files=["pkg/__init__.py"]) == {"pyproject.toml [project].version": "1.0.0"}
    with pytest.raises(pytest.fail.Exception, match=re.escape("pkg/__init__.py: no `__version__")):
        assert_versions_agree(repo, files=["pkg/__init__.py"])
    with pytest.raises(pytest.fail.Exception, match="nothing is being compared"):
        assert_versions_agree(repo)


def test_a_module_re_export_is_read_by_import(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "3.1.0", "3.1.0")
    (repo / "zz_version_probe.py").write_text('__version__ = "3.1.0"\n', encoding="utf-8")
    monkeypatch.syspath_prepend(str(repo))
    try:
        assert version_sources(repo, modules=["zz_version_probe"], pyproject=False) == {"zz_version_probe.__version__": "3.1.0"}
    finally:
        sys.modules.pop("zz_version_probe", None)


class TestAuditRegressions:
    def test_a_missing_or_unmatched_requested_source_is_reported(self, tmp_path):
        repo = _repo(tmp_path, "1.0.0", "1.0.0")
        (repo / "pkg" / "version.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
        problems: list = []
        found = version_sources(repo, files=["pkg/__init__.py", "pkg/verison.py"], problems=problems)
        assert len(found) == 2 and problems == ["pkg/verison.py: file does not exist"]
        with pytest.raises(pytest.fail.Exception, match=re.escape("pkg/verison.py: file does not exist")):
            assert_versions_agree(repo, files=["pkg/__init__.py", "pkg/verison.py"])
        assert_versions_agree(repo, files=["pkg/__init__.py", "pkg/version.py"])

    def test_a_missing_pyproject_version_is_reported_when_pyproject_is_requested(self, tmp_path):
        repo = _repo(tmp_path, "1.0.0", "1.0.0")
        (repo / "pyproject.toml").write_text('[project]\nname = "p"\ndynamic = ["version"]\n', encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="dynamic"):
            assert_versions_agree(repo, files=["pkg/__init__.py"])

    def test_a_bom_file_is_read(self, tmp_path):
        repo = _repo(tmp_path, "1.0.0", "1.0.0")
        (repo / "pkg" / "__init__.py").write_bytes(b'\xef\xbb\xbf__version__ = "1.0.0"\n')
        assert_versions_agree(repo, files=["pkg/__init__.py"])
