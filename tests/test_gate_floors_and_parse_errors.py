"""Unparsable inputs fail by name, BOM files are read like plain ones, and an empty corpus fails every entry below."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared import (
    config_call_site_parity,
    env_flag_parsing,
    git_dependency_pins,
    module_reload_safety,
    phantom_markdown_links,
    pytest_markers,
    unresolved_imports,
    vacuous_loop_assertions,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
FLAG_READ = 'import os\nif os.environ.get("SEED_FLAG"):\n    pass\n'


def _write(path: Path, text: str, *, bom: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return path


class TestEnvFlagParsing:
    def test_a_bom_file_is_read_like_a_plain_one(self, tmp_path):
        plain = _write(tmp_path / "plain.py", FLAG_READ)
        bom = _write(tmp_path / "bom.py", FLAG_READ, bom=True)
        found = env_flag_parsing.find_hand_parsed_env_flags([plain, bom], tmp_path, ("SEED_",))
        assert [(r.path, r.var) for r in found] == [("bom.py", "SEED_FLAG"), ("plain.py", "SEED_FLAG")]

    def test_an_unparsable_file_fails_by_name(self, tmp_path):
        clean = _write(tmp_path / "clean.py", "x = 1\n")
        broken = _write(tmp_path / "broken.py", "def broken(:\n")
        env_flag_parsing.assert_env_flags_use_one_parser([clean], tmp_path, ("SEED_",), {})
        with pytest.raises(AssertionError, match=r"broken\.py"):
            env_flag_parsing.assert_env_flags_use_one_parser([clean, broken], tmp_path, ("SEED_",), {})

    def test_the_floor_counts_parsed_files(self, tmp_path):
        broken = _write(tmp_path / "broken.py", "def broken(:\n")
        with pytest.raises(AssertionError, match="parsed"):
            env_flag_parsing.assert_env_flags_use_one_parser([broken], tmp_path, ("SEED_",), {})
        env_flag_parsing.assert_env_flags_use_one_parser([], tmp_path, ("SEED_",), {}, min_files=0)


class TestGitDependencyPins:
    def test_a_single_line_dependency_array_is_scanned(self, tmp_path):
        bad = _write(tmp_path / "bad" / "pyproject.toml", '[project]\nname = "s"\ndependencies = ["lib @ git+https://h.example/o/lib.git@main"]\n')
        good = _write(tmp_path / "good" / "pyproject.toml", f'[project]\nname = "s"\ndependencies = ["lib @ git+https://h.example/o/lib.git@{SHA}"]\n')
        assert git_dependency_pins.find_unpinned_git_dependencies(bad) == ["main"]
        assert git_dependency_pins.find_unpinned_git_dependencies(good) == []

    def test_optional_and_group_dependencies_are_scanned(self, tmp_path):
        p = _write(
            tmp_path / "pyproject.toml",
            '[project]\nname = "s"\n[project.optional-dependencies]\nx = ["a @ git+https://h.example/o/a.git@dev"]\n'
            '[dependency-groups]\ndev = ["b @ git+https://h.example/o/b.git"]\n',
        )
        assert git_dependency_pins.find_unpinned_git_dependencies(p) == ["dev", "<no ref>"]

    def test_an_unparsable_pyproject_fails_naming_the_file(self, tmp_path):
        p = _write(tmp_path / "pyproject.toml", '[project\nname = "s\n')
        with pytest.raises(git_dependency_pins.PyprojectParseError):
            git_dependency_pins.find_unpinned_git_dependencies(p)
        with pytest.raises(pytest.fail.Exception, match=r"pyproject\.toml"):
            git_dependency_pins.assert_all_git_dependencies_pinned(p)
        ok = _write(tmp_path / "ok" / "pyproject.toml", '[project]\nname = "s"\n')
        git_dependency_pins.assert_all_git_dependencies_pinned(ok)


def _empty_and_one(tmp_path: Path, name: str, text: str) -> "tuple[Path, Path]":
    empty = tmp_path / "empty"
    empty.mkdir()
    one = tmp_path / "one"
    _write(one / name, text)
    return empty, one


class TestFloors:
    def test_module_reload_safety(self, tmp_path):
        empty, one = _empty_and_one(tmp_path, "m.py", "x = 1\n")
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            module_reload_safety.assert_no_reloads_in_code([empty], tmp_path)
        module_reload_safety.assert_no_reloads_in_code([empty], tmp_path, min_files=0)
        module_reload_safety.assert_no_reloads_in_code([one], tmp_path)

    def test_vacuous_loop_assertions(self, tmp_path):
        _, one = _empty_and_one(tmp_path, "test_a.py", "def test_a():\n    assert True\n")
        baseline = tmp_path / "baseline.json"
        with pytest.raises(AssertionError, match="parsed"):
            vacuous_loop_assertions.assert_no_new_floorless_loop([], tmp_path, baseline)
        vacuous_loop_assertions.assert_no_new_floorless_loop([], tmp_path, baseline, min_files=0)
        vacuous_loop_assertions.assert_no_new_floorless_loop([one / "test_a.py"], tmp_path, baseline)

    def test_config_call_site_parity(self, tmp_path):
        _, one = _empty_and_one(tmp_path, "m.py", "x = 1\n")
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            config_call_site_parity.assert_no_divergent_cfg_get_call_site_defaults(tmp_path, [])
        config_call_site_parity.assert_no_divergent_cfg_get_call_site_defaults(tmp_path, [], min_files=0)
        config_call_site_parity.assert_no_divergent_cfg_get_call_site_defaults(tmp_path, [one / "m.py"])

    def test_unresolved_imports(self, tmp_path):
        empty, one = _empty_and_one(tmp_path, "m.py", "x = 1\n")
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            unresolved_imports.assert_all_from_imports_resolve([empty], [empty], resolvable_prefixes=("m",))
        unresolved_imports.assert_all_from_imports_resolve([empty], [empty], resolvable_prefixes=("m",), min_files=0)
        unresolved_imports.assert_all_from_imports_resolve([one], [one], resolvable_prefixes=("m",))

    def test_phantom_markdown_links(self, tmp_path):
        _, one = _empty_and_one(tmp_path, "a.md", "# a\n")
        with pytest.raises(pytest.fail.Exception, match="read"):
            phantom_markdown_links.assert_no_phantom_markdown_links([], tmp_path)
        phantom_markdown_links.assert_no_phantom_markdown_links([], tmp_path, min_files=0)
        phantom_markdown_links.assert_no_phantom_markdown_links([one / "a.md"], tmp_path)

    def test_pytest_markers(self, tmp_path):
        (tmp_path / "tests").mkdir()
        with pytest.raises(pytest.fail.Exception, match="test file"):
            pytest_markers.assert_markers_registered(tmp_path)
        pytest_markers.assert_markers_registered(tmp_path, min_files=0)
        _write(tmp_path / "tests" / "test_a.py", "def test_a():\n    pass\n")
        pytest_markers.assert_markers_registered(tmp_path)
