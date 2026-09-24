"""Tests for local_copy_report: synthetic consumer repos on disk."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.local_copy_report import assert_local_copies_do_not_grow, central_gate_names, find_local_copies

_WALKER = "import ast\nfrom pathlib import Path\n\ndef test_x():\n    for p in Path('.').rglob('*.py'):\n        ast.walk(ast.parse(p.read_text()))\n"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _found(root: Path, **kw: object) -> list[tuple[str, str]]:
    findings, _ = find_local_copies(root, use_git=False, **kw)  # type: ignore[arg-type]
    return [(f.path, f.message.split("`")[1]) for f in findings]


class TestByName:
    def test_a_walking_file_named_after_a_central_gate_is_reported(self, tmp_path):
        root = _repo(tmp_path, {"tests/test_meta/test_no_import_cycles.py": _WALKER})
        assert _found(root) == [("tests/test_meta/test_no_import_cycles.py", "import_cycles")]

    def test_generic_words_and_plurals_do_not_block_a_match(self, tmp_path):
        root = _repo(tmp_path, {"tests/test_meta/test_no_new_module_level_import_cycle.py": _WALKER})
        assert _found(root) == [("tests/test_meta/test_no_new_module_level_import_cycle.py", "import_cycles")]

    def test_a_per_site_regression_test_that_walks_nothing_is_not_a_copy(self, tmp_path):
        root = _repo(tmp_path, {"tests/test_meta/test_no_import_cycles.py": "def test_x():\n    assert 1 + 1 == 2\n"})
        assert _found(root) == []

    def test_a_long_site_specific_name_is_not_a_copy(self, tmp_path):
        root = _repo(tmp_path, {"tests/test_meta/test_import_cycles_in_gpu_kernels_and_reports.py": _WALKER})
        assert _found(root) == []

    def test_a_single_token_central_name_never_matches_by_name(self, tmp_path):
        root = _repo(tmp_path, {"tests/test_meta/test_registry.py": _WALKER})
        assert _found(root, central=["registry"]) == []

    def test_code_audit_scanner_copies_are_reported_against_code_audit_meta(self, tmp_path):
        root = _repo(tmp_path, {"tests/test_meta/test_no_bare_except.py": _WALKER})
        assert _found(root) == [("tests/test_meta/test_no_bare_except.py", "code_audit_meta:bare_except")]


class TestByContent:
    def test_a_signature_needs_every_pattern(self, tmp_path):
        both = "import ast\n\ndef test_r():\n    for n in ast.walk(ast.parse('')):\n        assert getattr(n, 'attr', '') != 'reload'\n"
        only_usage = "import importlib, json\n\ndef test_r():\n    importlib.reload(json)\n"
        root = _repo(tmp_path, {"tests/test_meta/test_hazard_one.py": both, "tests/test_meta/test_hazard_two.py": only_usage})
        assert _found(root) == [("tests/test_meta/test_hazard_one.py", "module_reload_safety")]

    def test_custom_signatures_replace_the_table(self, tmp_path):
        root = _repo(tmp_path, {"tests/test_meta/test_x.py": "MARK = 'zebra'\n"})
        assert _found(root, central=[], signatures={"zoo_gate": ("zebra",)}) == [("tests/test_meta/test_x.py", "zoo_gate")]
        assert _found(root, central=[], signatures={"zoo_gate": ("zebra", "lion")}) == []


class TestScope:
    def test_a_file_importing_py_ci_shared_or_code_audit_is_not_a_copy(self, tmp_path):
        wraps = "from py_ci_shared.import_cycles import assert_no_import_cycles\n" + _WALKER
        uses = "from pyutilz.dev.code_audit import run_all\n" + _WALKER
        aliased = "import py_ci_shared.import_cycles as g\n" + _WALKER
        root = _repo(
            tmp_path,
            {
                "tests/test_meta/test_no_import_cycles.py": wraps,
                "tests/test_meta/test_no_bare_except.py": uses,
                "tests/test_meta/test_import_cycles.py": aliased,
            },
        )
        assert _found(root) == []

    def test_only_meta_directories_are_judged_by_default(self, tmp_path):
        root = _repo(tmp_path, {"tests/unit/test_no_import_cycles.py": _WALKER})
        assert _found(root) == []
        assert _found(root, meta_dirs=None) == [("tests/unit/test_no_import_cycles.py", "import_cycles")]

    def test_central_names_are_this_packages_public_modules(self):
        names = central_gate_names()
        assert "import_cycles" in names and "local_copy_report" in names
        assert not any(n.startswith("_") for n in names)

    def test_a_bom_file_is_judged(self, tmp_path):
        root = _repo(tmp_path, {})
        p = root / "tests" / "test_meta" / "test_no_import_cycles.py"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"\xef\xbb\xbf" + _WALKER.encode())
        assert _found(root) == [("tests/test_meta/test_no_import_cycles.py", "import_cycles")]


class TestAssert:
    def test_an_unparsable_file_and_an_empty_tree_fail(self, tmp_path):
        root = _repo(tmp_path / "a", {"tests/test_meta/test_ok.py": "x = 1\n", "tests/test_meta/test_bad.py": "def (:\n"})
        with pytest.raises(pytest.fail.Exception, match=r"test_bad\.py"):
            assert_local_copies_do_not_grow(root, baseline_path=None, use_git=False)
        (tmp_path / "b" / "tests").mkdir(parents=True)
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_local_copies_do_not_grow(tmp_path / "b", baseline_path=None, use_git=False)

    def test_the_ratchet_fails_a_new_copy_and_a_migrated_one(self, tmp_path):
        root = _repo(tmp_path, {"tests/test_meta/test_no_import_cycles.py": _WALKER, "tests/test_meta/test_ok.py": "x = 1\n"})
        baseline = tmp_path / "bl.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_local_copies_do_not_grow(root, baseline_path=baseline, use_git=False)
        with pytest.raises(pytest.skip.Exception):
            assert_local_copies_do_not_grow(root, baseline_path=baseline, refresh=True, use_git=False)
        assert len(json.loads(baseline.read_text(encoding="utf-8"))["entries"]) == 1
        assert_local_copies_do_not_grow(root, baseline_path=baseline, use_git=False)
        (root / "tests" / "test_meta" / "test_no_bare_except.py").write_text(_WALKER, encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="bare_except"):
            assert_local_copies_do_not_grow(root, baseline_path=baseline, use_git=False)
        (root / "tests" / "test_meta" / "test_no_bare_except.py").unlink()
        (root / "tests" / "test_meta" / "test_no_import_cycles.py").unlink()
        with pytest.raises(pytest.fail.Exception, match="no longer found"):
            assert_local_copies_do_not_grow(root, baseline_path=baseline, use_git=False)
