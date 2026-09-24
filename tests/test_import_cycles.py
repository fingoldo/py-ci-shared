"""Tests for import_cycles: real packages on disk, and the import-order verdicts checked against a real interpreter."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.import_cycles import RULE_CYCLE, RULE_ORDER, assert_no_import_cycles, find_import_cycles


def _pkg(tmp_path: Path, files: dict[str, str], name: str = "pkg") -> Path:
    root = tmp_path / name
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def _rules(root: Path) -> list[tuple[str, str, int]]:
    findings, _ = find_import_cycles(root, use_git=False)
    return [(f.rule, f.path, f.line) for f in findings]


def _imports_first(tmp_path: Path, module: str) -> bool:
    proc = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


_SPLIT = {
    "__init__.py": "",
    "a.py": """
        def f():
            return 1

        from pkg.b import g
    """,
    "b.py": """
        from pkg.a import f

        def g():
            return f()
    """,
}


class TestCycles:
    def test_a_two_module_cycle_is_one_finding_naming_both(self, tmp_path):
        root = _pkg(tmp_path, _SPLIT)
        findings, scan = find_import_cycles(root, use_git=False)
        cycles = [f for f in findings if f.rule == RULE_CYCLE]
        assert len(cycles) == 1
        assert cycles[0].message == "2-module import cycle: pkg.a -> pkg.b -> pkg.a"
        assert scan.parsed_count == 3

    def test_function_local_type_checking_and_main_guard_imports_are_not_edges(self, tmp_path):
        root = _pkg(
            tmp_path,
            {
                "__init__.py": "",
                "a.py": """
                    from typing import TYPE_CHECKING
                    if TYPE_CHECKING:
                        from pkg.b import g
                    def f():
                        from pkg.b import g
                        return g
                    if __name__ == "__main__":
                        from pkg.b import g
                """,
                "b.py": "from pkg.a import f\ndef g():\n    return f\n",
            },
        )
        assert _rules(root) == []

    def test_an_import_in_a_try_or_else_branch_is_an_edge(self, tmp_path):
        root = _pkg(
            tmp_path,
            {"__init__.py": "", "a.py": "try:\n    import pkg.b\nexcept OSError:\n    pass\n", "b.py": "if True:\n    pass\nelse:\n    import pkg.a\n"},
        )
        assert [r[0] for r in _rules(root)] == [RULE_CYCLE]

    def test_naming_an_ancestor_through_a_dotted_import_is_not_a_cycle(self, tmp_path):
        root = _pkg(tmp_path, {"__init__.py": "from pkg import x\n", "x.py": "import pkg.y\n", "y.py": "Y = 1\n"})
        assert _rules(root) == []

    def test_a_sibling_reached_through_the_package_is_not_a_cycle(self, tmp_path):
        root = _pkg(tmp_path, {"__init__.py": "", "sub/__init__.py": "from . import a, b\n", "sub/a.py": "X = 1\n", "sub/b.py": "from . import a as av\n"})
        assert _rules(root) == []
        assert _imports_first(tmp_path, "pkg.sub.b") and _imports_first(tmp_path, "pkg.sub")

    def test_a_name_the_parent_binds_imported_relatively_is_still_a_cycle(self, tmp_path):
        root = _pkg(tmp_path, {"__init__.py": "", "sub/__init__.py": "C = 1\nfrom . import b\n", "sub/b.py": "from . import C\n"})
        assert [r[0] for r in _rules(root)] == [RULE_CYCLE]

    def test_importing_names_from_the_parent_package_is_a_cycle(self, tmp_path):
        root = _pkg(tmp_path, {"__init__.py": "C = 1\nfrom pkg.x import X\n", "x.py": "from pkg import C\nX = C\n"})
        assert [r[0] for r in _rules(root)] == [RULE_CYCLE]

    def test_relative_imports_resolve(self, tmp_path):
        root = _pkg(tmp_path, {"__init__.py": "", "sub/__init__.py": "", "sub/a.py": "f = 1\nfrom ..b import g\n", "b.py": "g = 1\nfrom .sub.a import f\n"})
        assert [r[0] for r in _rules(root)] == [RULE_CYCLE]
        assert _imports_first(tmp_path, "pkg.b") and _imports_first(tmp_path, "pkg.sub.a")

    def test_a_src_layout_with_no_package_name_resolves_top_level_packages(self, tmp_path):
        src = tmp_path / "src"
        _pkg(src, {"__init__.py": "", "a.py": "import two.b\n"}, name="one")
        _pkg(src, {"__init__.py": "", "b.py": "import one.a\n"}, name="two")
        findings, _ = find_import_cycles(src, use_git=False)
        assert [f.message for f in findings] == ["2-module import cycle: one.a -> two.b -> one.a"]


class TestImportOrder:
    def test_a_bottom_reexport_fails_only_when_the_sibling_is_imported_first(self, tmp_path):
        root = _pkg(tmp_path, _SPLIT)
        order = [f for f in find_import_cycles(root, use_git=False)[0] if f.rule == RULE_ORDER]
        assert [(f.path, f.line) for f in order] == [("a.py", 5)]
        assert "when pkg.b is imported first" in order[0].message and "pkg.a is imported first" not in order[0].message
        assert "has not bound g" in order[0].message
        # the verdict is the interpreter's: pkg.a first loads, pkg.b first raises
        assert _imports_first(tmp_path, "pkg.a") is True
        assert _imports_first(tmp_path, "pkg.b") is False

    def test_binding_the_name_before_the_back_edge_is_clean(self, tmp_path):
        files = dict(_SPLIT)
        files["b.py"] = "def g():\n    return 1\n\nfrom pkg.a import f\n"
        root = _pkg(tmp_path, files)
        assert [r for r in _rules(root) if r[0] == RULE_ORDER] == []
        assert _imports_first(tmp_path, "pkg.b") is True

    def test_an_ancestor_package_is_loaded_first(self, tmp_path):
        # pkg/__init__ defines C then imports x; x takes C from pkg. Importing pkg.x "first" runs pkg first: fine.
        root = _pkg(tmp_path, {"__init__.py": "C = 1\nfrom pkg.x import X\n", "x.py": "from pkg import C\nX = C\n"})
        assert [r for r in _rules(root) if r[0] == RULE_ORDER] == []
        assert _imports_first(tmp_path, "pkg.x") is True

    def test_the_parent_binding_too_late_fails_every_order(self, tmp_path):
        root = _pkg(tmp_path, {"__init__.py": "from pkg.x import X\nC = 1\n", "x.py": "from pkg import C\nX = C\n"})
        order = [f for f in find_import_cycles(root, use_git=False)[0] if f.rule == RULE_ORDER]
        assert [(f.path, f.line) for f in order] == [("x.py", 1)]
        assert _imports_first(tmp_path, "pkg.x") is False

    def test_a_try_catching_import_error_survives(self, tmp_path):
        files = dict(_SPLIT)
        files["b.py"] = "try:\n    from pkg.a import f\nexcept ImportError:\n    f = None\n\ndef g():\n    return f\n"
        root = _pkg(tmp_path, files)
        assert [r for r in _rules(root) if r[0] == RULE_ORDER] == []
        assert _imports_first(tmp_path, "pkg.b") is True

    def test_a_try_catching_something_else_does_not(self, tmp_path):
        files = dict(_SPLIT)
        files["b.py"] = "try:\n    from pkg.a import f\nexcept KeyError:\n    f = None\n\ndef g():\n    return f\n"
        root = _pkg(tmp_path, files)
        assert [r[0] for r in _rules(root) if r[0] == RULE_ORDER] == [RULE_ORDER]
        assert _imports_first(tmp_path, "pkg.b") is False

    def test_a_star_import_or_module_getattr_in_the_target_is_not_judged(self, tmp_path):
        star = dict(_SPLIT)
        star["b.py"] = "from pkg.c import *\nfrom pkg.a import f\n\ndef g():\n    return f\n"
        star["c.py"] = "Z = 1\n"
        assert [r for r in _rules(_pkg(tmp_path / "s", star)) if r[0] == RULE_ORDER] == []
        dyn = dict(_SPLIT)
        dyn["b.py"] = "from pkg.a import f\n\ndef __getattr__(name):\n    return name\n"
        assert [r for r in _rules(_pkg(tmp_path / "d", dyn)) if r[0] == RULE_ORDER] == []

    def test_a_submodule_named_in_from_import_is_imported_not_looked_up(self, tmp_path):
        root = _pkg(tmp_path, {"__init__.py": "from pkg import sub\nfrom pkg.sub import S\n", "sub.py": "import pkg\nS = 1\n"})
        assert [r for r in _rules(root) if r[0] == RULE_ORDER] == []
        assert _imports_first(tmp_path, "pkg.sub") is True


class TestCorpusAndBaseline:
    def test_a_bom_file_takes_part(self, tmp_path):
        root = _pkg(tmp_path, _SPLIT)
        (root / "b.py").write_bytes(b"\xef\xbb\xbf" + (root / "b.py").read_bytes())
        assert RULE_CYCLE in [r[0] for r in _rules(root)]

    def test_an_unparsable_file_fails_the_assert(self, tmp_path):
        root = _pkg(tmp_path, {"__init__.py": "", "ok.py": "x = 1\n", "bad.py": "def (:\n"})
        _, scan = find_import_cycles(root, use_git=False)
        assert [p.rel for p in scan.unparsed] == ["bad.py"]
        with pytest.raises(pytest.fail.Exception, match=r"bad.py"):
            assert_no_import_cycles(root, use_git=False)

    def test_an_empty_corpus_fails_the_floor(self, tmp_path):
        (tmp_path / "empty").mkdir()
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_no_import_cycles(tmp_path / "empty", use_git=False)

    def test_a_clean_package_passes_and_a_cycle_fails_without_a_baseline(self, tmp_path):
        clean = _pkg(tmp_path / "c", {"__init__.py": "", "a.py": "import pkg.b\n", "b.py": "B = 1\n"})
        assert_no_import_cycles(clean, use_git=False)
        with pytest.raises(pytest.fail.Exception, match="import-cycle"):
            assert_no_import_cycles(_pkg(tmp_path / "x", _SPLIT), use_git=False)

    def test_the_baseline_is_required_written_on_refresh_and_then_ratchets(self, tmp_path):
        root = _pkg(tmp_path, _SPLIT)
        baseline = tmp_path / "bl.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_import_cycles(root, baseline_path=baseline, use_git=False)
        with pytest.raises(pytest.skip.Exception):
            assert_no_import_cycles(root, baseline_path=baseline, refresh=True, use_git=False)
        entries = json.loads(baseline.read_text(encoding="utf-8"))["entries"]
        assert sorted(k.split("::")[0] for k in entries) == [RULE_CYCLE, RULE_ORDER]
        assert_no_import_cycles(root, baseline_path=baseline, use_git=False)
        (root / "c.py").write_text("import pkg.d\n", encoding="utf-8")
        (root / "d.py").write_text("import pkg.c\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"pkg.c -> pkg.d"):
            assert_no_import_cycles(root, baseline_path=baseline, use_git=False)

    def test_a_cycle_key_does_not_move_with_line_numbers(self, tmp_path):
        root = _pkg(tmp_path, _SPLIT)
        before = {f.key for f in find_import_cycles(root, use_git=False)[0]}
        (root / "a.py").write_text("# moved\n\n" + (root / "a.py").read_text(encoding="utf-8"), encoding="utf-8")
        after = {f.key for f in find_import_cycles(root, use_git=False)[0]}
        assert before == after and len(before) == 2
