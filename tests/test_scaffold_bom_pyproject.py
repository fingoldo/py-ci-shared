"""A pyproject.toml saved with a byte order mark (a Windows editor's habit) still yields the scaffold's version (audit 45-F11)."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared._scaffold import _version


def test_the_version_is_read_from_a_bom_prefixed_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_bytes(b'\xef\xbb\xbf[project]\nversion = "1.2.3"\n')
    assert _version(tmp_path) == "1.2.3"


def test_a_plain_pyproject_is_read_as_before(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_bytes(b'[project]\nversion = "4.5.6"\n')
    assert _version(tmp_path) == "4.5.6"
