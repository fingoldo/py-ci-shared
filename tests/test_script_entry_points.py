"""Tests for py_ci_shared.script_entry_points: the three structural rules and the behavioural double-execution half."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, ImportAliases, UnparsedFilesError
from py_ci_shared.script_entry_points import (
    RULE_COLLISION,
    RULE_CWD,
    RULE_UNREGISTERED,
    _cwd_path_edits,
    assert_script_entry_points,
    double_execution_findings,
    find_script_entry_points,
)

BOM = b"\xef\xbb\xbf"

ENTRY = 'import sys\n\n_STOP = object()\n\n\nif __name__ == "__main__":\n    print(_STOP)\n'
REGISTERED = (
    'if __name__ == "__main__":\n    import sys as _sys\n\n    _sys.modules.setdefault("seed", _sys.modules["__main__"])\n\n_STOP = object()\n\n\n'
    'if __name__ == "__main__":\n    print(_STOP)\n'
)
TOP_IMPORT = "import seed\n"
LAZY_IMPORT = "def f():\n    from seed import _STOP\n    return _STOP\n"


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else str(data).encode())
    return tmp_path


def _rules(root: Path, dirs=(".",), **kw) -> list:
    return [(f.path, f.line, f.rule) for f in find_script_entry_points(root, dirs, **kw)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"seed.py": ENTRY, "user.py": TOP_IMPORT})
    found = find_script_entry_points(root, ["."])
    assert [(f.path, f.line, f.rule) for f in found] == [("seed.py", 6, RULE_UNREGISTERED)]
    assert "user.py:1" in found[0].message and "sys.modules.setdefault" in found[0].message


def test_a_function_local_import_counts_as_importing(tmp_path):
    root = _corpus(tmp_path, {"seed.py": ENTRY, "user.py": LAZY_IMPORT})
    assert _rules(root) == [("seed.py", 6, RULE_UNREGISTERED)]


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    root = _corpus(tmp_path / "ok", {"seed.py": REGISTERED, "user.py": TOP_IMPORT})
    assert find_script_entry_points(root, ["."]) == []
    # an entry point nobody imports needs no registration; a module without a main block needs none either
    assert _rules(_corpus(tmp_path / "a", {"seed.py": ENTRY, "user.py": "x = 1\n"})) == []
    assert _rules(_corpus(tmp_path / "b", {"seed.py": "_STOP = 1\n", "user.py": TOP_IMPORT})) == []


def test_registering_under_another_name_or_outside_a_main_block_does_not_count(tmp_path):
    wrong_name = REGISTERED.replace('"seed"', '"other"')
    outside = 'import sys\n\nsys.modules.setdefault("seed", sys.modules["__main__"])\n\nif __name__ == "__main__":\n    pass\n'
    assert _rules(_corpus(tmp_path / "a", {"seed.py": wrong_name, "user.py": TOP_IMPORT}))[0][2] == RULE_UNREGISTERED
    outside_found = _rules(_corpus(tmp_path / "b", {"seed.py": outside, "user.py": TOP_IMPORT}))
    assert outside_found and outside_found[0][2] == RULE_UNREGISTERED


@pytest.mark.parametrize(
    "stmt",
    [
        'sys.path.insert(0, ".")',
        'sys.path.insert(0, "")',
        'sys.path.append(".")',
        'sys.path.insert(0, "..")',
        'sys.path.insert(0, "src")',
        "sys.path.insert(0, os.getcwd())",
        "sys.path.append(str(Path.cwd()))",
        "here = os.getcwd()\nsys.path.insert(0, here)",
        "base = Path.cwd().parent\nroot = base / 'x'\nsys.path.insert(0, str(root))",
    ],
)
def test_a_cwd_path_edit_is_reported(tmp_path, stmt):
    root = _corpus(tmp_path, {"seed.py": f"import os\nimport sys\nfrom pathlib import Path\n\n{stmt}\n"})
    assert [r for _, _, r in _rules(root)] == [RULE_CWD]


@pytest.mark.parametrize(
    "stmt",
    [
        "sys.path.insert(0, str(Path(__file__).resolve().parent))",
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))",
        "HERE = Path(__file__).resolve().parent\nROOT = HERE.parent\nsys.path.insert(0, str(ROOT))",
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))",
        "sys.path.insert(0, '/opt/app')",
        "sys.path.insert(0, 'C:\\\\app')",
    ],
)
def test_a_file_derived_or_absolute_path_edit_is_not_reported(tmp_path, stmt):
    root = _corpus(tmp_path, {"seed.py": f"import os\nimport sys\nfrom pathlib import Path\n\n{stmt}\n"})
    assert _rules(root) == []


def test_an_aliased_sys_path_is_followed():
    tree = ast.parse('from sys import path as p\np.insert(0, ".")\n')
    assert [line for line, _ in _cwd_path_edits(tree, ImportAliases.from_tree(tree))] == [2]


def test_probe_dirs_are_checked_for_the_cwd_rule_only(tmp_path):
    """A probe is its own process: its `import seed` is the first execution of seed, not a second one."""
    probe = "import sys\nsys.path.insert(0, '.')\nimport seed\n"
    root = _corpus(tmp_path, {"seed.py": ENTRY, "probes/p.py": probe})
    assert _rules(root, ["."], probe_dirs=["probes"]) == [("probes/p.py", 2, RULE_CWD)]
    assert _rules(root, ["."]) == []


def test_only_the_top_level_of_a_script_directory_is_read(tmp_path):
    root = _corpus(tmp_path, {"seed.py": REGISTERED, "pkg/inner.py": 'import sys\nsys.path.insert(0, ".")\n'})
    assert _rules(root) == []


def test_a_module_name_defined_by_two_directories_is_reported_not_guessed(tmp_path):
    root = _corpus(tmp_path, {"seed.py": REGISTERED, "extra/seed.py": "x = 1\n", "user.py": TOP_IMPORT})
    found = find_script_entry_points(root, [".", "extra"])
    assert [(f.path, f.rule) for f in found] == [("extra/seed.py", RULE_COLLISION), ("seed.py", RULE_COLLISION)]
    assert "extra/seed.py, seed.py" in found[0].message


def test_a_script_directory_is_required_and_must_exist(tmp_path):
    root = _corpus(tmp_path, {"seed.py": "x = 1\n"})
    with pytest.raises(ValueError, match="at least one script directory"):
        find_script_entry_points(root, [])
    with pytest.raises(FileNotFoundError, match="nope"):
        find_script_entry_points(root, [".", "nope"])


def test_the_assert_message_names_the_findings_and_the_fix(tmp_path):
    root = _corpus(tmp_path, {"seed.py": ENTRY, "user.py": TOP_IMPORT})
    with pytest.raises(AssertionError, match=r"(?s)Register every imported entry point.*seed\.py:6"):
        assert_script_entry_points(root, ["."])


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _rules(_corpus(tmp_path / "a", {"seed.py": ENTRY, "user.py": TOP_IMPORT}))
    bom = _rules(_corpus(tmp_path / "b", {"seed.py": BOM + ENTRY.encode(), "user.py": BOM + TOP_IMPORT.encode()}))
    assert plain == bom == [("seed.py", 6, RULE_UNREGISTERED)]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"seed.py": REGISTERED, "user.py": TOP_IMPORT, "broken.py": "def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_script_entry_points(root, ["."])
    assert find_script_entry_points(root, ["."], allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_script_entry_points(_corpus(tmp_path / "e", {"notes.txt": "x"}), ["."])
    root = _corpus(tmp_path / "r", {"seed.py": "x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_script_entry_points(root, ["."], min_files=2)


# ---- behavioural half ------------------------------------------------------------------------------------------------------------------


def test_double_execution_flags_an_unregistered_entry_point_and_passes_a_registered_one(tmp_path):
    bad = _corpus(tmp_path / "bad", {"seed.py": ENTRY, "user.py": TOP_IMPORT})
    found = double_execution_findings(bad, ["."], timeout=60)
    assert [(f.path, f.rule) for f in found] == [("seed.py", "double-execution")]
    good = _corpus(tmp_path / "good", {"seed.py": REGISTERED, "user.py": TOP_IMPORT})
    assert double_execution_findings(good, ["."], timeout=60) == []


def test_double_execution_does_not_run_the_work_block(tmp_path):
    work = REGISTERED.replace("print(_STOP)", "raise SystemExit('work ran')")
    good = _corpus(tmp_path, {"seed.py": work, "user.py": TOP_IMPORT})
    assert double_execution_findings(good, ["."], timeout=60) == []


def test_double_execution_skip_needs_a_reason_and_a_real_entry_point(tmp_path):
    root = _corpus(tmp_path, {"seed.py": ENTRY, "user.py": TOP_IMPORT})
    assert double_execution_findings(root, ["."], skip={"seed": "starts a live health check at import"}) == []
    with pytest.raises(ValueError, match="no reason"):
        double_execution_findings(root, ["."], skip={"seed": " "})
    with pytest.raises(ValueError, match="stale skip"):
        double_execution_findings(root, ["."], skip={"gone": "was removed"})


def test_double_execution_reports_a_timeout_by_name(tmp_path):
    slow = REGISTERED.replace("_STOP = object()", "import time\ntime.sleep(30)\n_STOP = object()")
    root = _corpus(tmp_path, {"seed.py": slow, "user.py": TOP_IMPORT})
    found = double_execution_findings(root, ["."], timeout=2)
    assert [(f.path, f.rule) for f in found] == [("seed.py", "double-execution")] and "within 2s" in found[0].message


def test_double_execution_reports_a_module_that_cannot_load(tmp_path):
    root = _corpus(tmp_path, {"seed.py": REGISTERED + "import not_a_real_module_xyz\n", "user.py": TOP_IMPORT})
    found = double_execution_findings(root, ["."], timeout=60)
    assert len(found) == 1 and "not_a_real_module_xyz" in found[0].message
