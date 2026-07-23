"""Unit tests for the entry-point resolvability check (llm_bench 09-High
audit finding).

Real scratch pyproject.toml + importable module, same no-mocking
convention as this package's other tests.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.entry_points_resolvable import assert_all_entry_points_resolvable, find_unresolvable_entry_points


def _write_pyproject(tmp_path: Path, scripts_toml: str) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(f'[project]\nname = "scratch"\nversion = "0.0.0"\n\n{scripts_toml}\n', encoding="utf-8")
    return p


def _install_fake_module(monkeypatch, name: str, **attrs) -> None:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)


def test_resolvable_entry_point_not_flagged(tmp_path, monkeypatch):
    _install_fake_module(monkeypatch, "_scratch_mod_ok", main=lambda: None)
    p = _write_pyproject(tmp_path, '[project.scripts]\nscratch-cli = "_scratch_mod_ok:main"\n')
    assert find_unresolvable_entry_points(p) == []


def test_missing_module_flagged(tmp_path):
    p = _write_pyproject(tmp_path, '[project.scripts]\nscratch-cli = "_scratch_mod_does_not_exist:main"\n')
    violations = find_unresolvable_entry_points(p)
    assert len(violations) == 1
    assert "_scratch_mod_does_not_exist" in violations[0]


def test_missing_attribute_flagged(tmp_path, monkeypatch):
    _install_fake_module(monkeypatch, "_scratch_mod_no_attr")
    p = _write_pyproject(tmp_path, '[project.scripts]\nscratch-cli = "_scratch_mod_no_attr:main"\n')
    violations = find_unresolvable_entry_points(p)
    assert len(violations) == 1
    assert "no attribute" in violations[0]


def test_malformed_spec_without_colon_flagged(tmp_path):
    p = _write_pyproject(tmp_path, '[project.scripts]\nscratch-cli = "not_a_valid_spec"\n')
    violations = find_unresolvable_entry_points(p)
    assert len(violations) == 1
    assert "module:attr" in violations[0]


def test_entry_points_group_also_checked(tmp_path, monkeypatch):
    _install_fake_module(monkeypatch, "_scratch_plugin_mod_ok", register=lambda: None)
    p = _write_pyproject(tmp_path, '[project.entry-points."scratch.plugins"]\nfoo = "_scratch_plugin_mod_ok:register"\n')
    assert find_unresolvable_entry_points(p) == []


def test_no_scripts_section_is_clean(tmp_path):
    p = _write_pyproject(tmp_path, "")
    assert find_unresolvable_entry_points(p) == []


class TestAssertAllEntryPointsResolvable:
    def test_fails_on_unresolvable(self, tmp_path):
        p = _write_pyproject(tmp_path, '[project.scripts]\nscratch-cli = "_scratch_mod_missing_xyz:main"\n')
        with pytest.raises(pytest.fail.Exception, match=r"_scratch_mod_missing_xyz"):
            assert_all_entry_points_resolvable(p)

    def test_passes_when_clean(self, tmp_path):
        p = _write_pyproject(tmp_path, "")
        assert_all_entry_points_resolvable(p)  # does not raise
