"""unpruned_tree_walk: a function documented as taking a repository root must prune .claude/worktrees before walking it."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError
from py_ci_shared.unpruned_tree_walk import (
    RULE,
    assert_unpruned_tree_walk,
    find_unpruned_tree_walk,
)

NL = chr(10)


def _write(root: Path, lines: list, rel: str = "pkg/scan.py") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NL.join(lines) + NL, encoding="utf-8")


def _find(tmp_path: Path, lines: list, **kwargs):
    _write(tmp_path, lines)
    return find_unpruned_tree_walk(tmp_path, use_git=False, min_files=1, **kwargs)


ROOT_WALK = [
    "from pathlib import Path",
    "",
    "def find_sources(repo_root):",
    '    """repo_root: the repo root; every module under it."""',  # documented root
    '    return list(Path(repo_root).rglob("*.py"))',
]


def test_a_documented_root_walk_with_no_prune_is_reported_at_the_walk(tmp_path):
    found = _find(tmp_path, ROOT_WALK)
    assert [(f.path, f.line, f.rule) for f in found] == [("pkg/scan.py", 5, RULE)]


def test_a_walk_that_names_and_prunes_worktrees_is_clean(tmp_path):
    lines = [
        "from pathlib import Path",
        "",
        "def find_sources(repo_root):",
        '    """repo_root: the repo root; every module under it."""',
        '    return [p for p in Path(repo_root).rglob("*.py") if ".claude" not in p.parts and "worktrees" not in p.parts]',
    ]
    assert _find(tmp_path, lines) == []


def test_a_walk_of_a_parameter_not_documented_as_a_root_is_not_checked(tmp_path):
    lines = [
        "from pathlib import Path",
        "",
        "def scan_fixture_dir(directory):",
        '    """Every fixture file in the directory."""',
        '    return list(Path(directory).rglob("*.py"))',
    ]
    assert _find(tmp_path, lines) == []


def test_os_walk_of_a_documented_root_is_reported(tmp_path):
    lines = [
        "import os",
        "",
        "def walk(repo_root):",
        '    """repo_root: the repository root to walk."""',
        "    for dirpath, dirnames, files in os.walk(repo_root):",
        "        pass",
    ]
    assert [f.line for f in _find(tmp_path, lines)] == [5]


def test_a_reasoned_marker_accepts_the_walk(tmp_path):
    lines = [
        "from pathlib import Path",
        "",
        "def find_sources(repo_root):",
        '    """repo_root: the repo root; every module under it."""',
        "    # tree-walk-ok: this repo never has a .claude directory under the scanned root, reviewed",
        '    return list(Path(repo_root).rglob("*.py"))',
    ]
    assert _find(tmp_path, lines) == []


def test_a_marker_without_a_reason_is_a_finding(tmp_path):
    lines = list(ROOT_WALK)
    lines.insert(4, "    # tree-walk-ok:")
    found = _find(tmp_path, lines)
    assert len(found) == 1 and "needs a reason" in found[0].message


def test_a_test_module_is_not_scanned_by_default(tmp_path):
    _write(tmp_path, ROOT_WALK, rel="tests/test_scan.py")
    (tmp_path / "empty").mkdir()
    _write(tmp_path / "empty", ROOT_WALK, rel="tests/test_scan.py")
    with pytest.raises(EmptyScanError):
        find_unpruned_tree_walk(tmp_path / "empty", use_git=False, min_files=1)


def test_an_empty_tree_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        assert_unpruned_tree_walk(tmp_path, use_git=False)
