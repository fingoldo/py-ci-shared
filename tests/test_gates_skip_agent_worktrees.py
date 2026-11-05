"""Gates that walk a root must not enter ``.claude`` (Claude Code's agent worktrees, each a COMPLETE copy of the repository).

Each test builds a toy repo with a real file and a decoy under ``.claude/worktrees/agent-x/`` and hands the gate the repo ROOT, the
way a consumer does. Two checks per gate: the result names only the real file, and no directory below ``.claude`` was ever listed
(``os.scandir`` recorded), because a gate that lists the copies and filters afterwards is correct but takes minutes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from py_ci_shared import _mutation_runner, contract_ledger, dart_scanners, deferred_entries, destructive_tests_throwaway_only, llm_output_caps
from py_ci_shared._core.corpus import iter_files_pruned
from py_ci_shared._core.open_audit import count_opens
from py_ci_shared._mutation_speed import expand_test_files
from py_ci_shared.ci_install_covers_entry_imports import _Entry, _pytest_files
from py_ci_shared._ci_install_parts import Repo
from py_ci_shared.code_audit import _todo_markers
from py_ci_shared.code_audit.column_no_write_path import _collect_sql_declarations
from py_ci_shared.code_audit.redundant_test_fit import scan_redundant_test_fit_calls
from py_ci_shared.code_audit.source_text_assertions import scan_source_text_assertions

DECOY_DIR = ".claude/worktrees/agent-x"


def write(root: Path, rel: str, text: str = "x = 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def listed(monkeypatch):
    """Every directory ``os.scandir`` was asked to list; the test asserts none sits below ``.claude``."""
    seen: list[str] = []
    real = os.scandir

    def recording(path="."):
        seen.append(os.fsdecode(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", recording)
    return seen


def assert_never_entered(seen: list[str]) -> None:
    assert seen, "the gate listed no directory at all, so this test proved nothing"
    assert [p for p in seen if ".claude" in Path(p).parts] == []


def test_iter_files_pruned_skips_claude_and_filters_by_suffix(tmp_path, listed):
    write(tmp_path, "pkg/a.py")
    write(tmp_path, "pkg/b.txt")
    write(tmp_path, f"{DECOY_DIR}/pkg/c.py")
    write(tmp_path, "node_modules/d.py")
    assert iter_files_pruned(tmp_path, (".py",)) == [tmp_path / "pkg" / "a.py"]
    assert_never_entered(listed)


def test_iter_files_pruned_takes_every_file_without_suffixes_and_honours_exclude_dirs(tmp_path):
    write(tmp_path, "a/x.py")
    write(tmp_path, "b/y.txt")
    write(tmp_path, f"{DECOY_DIR}/z.py")
    assert iter_files_pruned(tmp_path, exclude_dirs={"a", ".claude"}) == [tmp_path / "b" / "y.txt"]
    assert iter_files_pruned(tmp_path / "missing", (".py",)) == []


def test_iter_files_pruned_ignores_where_the_root_itself_lives(tmp_path):
    root = tmp_path / ".claude" / "worktrees" / "agent-y"
    write(root, "pkg/a.py")
    assert iter_files_pruned(root, (".py",)) == [root / "pkg" / "a.py"]


def test_llm_output_caps(tmp_path, listed):
    write(tmp_path, "pkg/a.py", "CAP_TOKENS = 5\n")
    write(tmp_path, f"{DECOY_DIR}/pkg/b.py", "CAP_TOKENS = 7\n")
    found = llm_output_caps.numeric_output_caps([tmp_path], repo_root=tmp_path, name_hints=("CAP",), default_hints=())
    assert len(found) == 1
    assert ".claude" not in repr(found)
    assert_never_entered(listed)


def test_destructive_tests_conftest_guard_directories(tmp_path, listed):
    conftest = "def pytest_sessionstart(session):\n    guard()\n"
    write(tmp_path, "tests/conftest.py", conftest)
    write(tmp_path, f"{DECOY_DIR}/tests/conftest.py", conftest)
    dirs = destructive_tests_throwaway_only._guarded_dirs([tmp_path], tmp_path, frozenset({"guard"}))
    assert dirs == {"tests"}
    assert_never_entered(listed)


def test_tree_mentions_dash_does_not_read_a_worktree_copy(tmp_path, listed):
    write(tmp_path, "pkg/a.py", "x = 1\n")
    write(tmp_path, f"{DECOY_DIR}/pkg/b.py", "import dash\n")
    assert _mutation_runner._tree_mentions_dash(str(tmp_path)) is False
    assert_never_entered(listed)


def test_expand_test_files(tmp_path, listed):
    write(tmp_path, "tests/test_a.py")
    write(tmp_path, f"tests/{DECOY_DIR}/test_b.py")
    assert expand_test_files(tmp_path, ["tests"]) == ["tests/test_a.py"]
    assert_never_entered(listed)


def test_snapshot_files(tmp_path, listed):
    write(tmp_path, "store/a.bin", "abc")
    write(tmp_path, f"{DECOY_DIR}/b.bin", "abcdef")
    snapshot = contract_ledger.snapshot_files(tmp_path)
    assert snapshot == {"store/a.bin": 3}
    assert_never_entered(listed)


def test_snapshot_files_still_skips_the_dirs_the_caller_names(tmp_path):
    write(tmp_path, "store/a.bin", "abc")
    write(tmp_path, "cache/b.bin", "abcdef")
    assert contract_ledger.snapshot_files(tmp_path, skip_dirs=["cache"]) == {"store/a.bin": 3}


def test_ci_install_pytest_files(tmp_path, listed):
    write(tmp_path, "tests/test_a.py")
    write(tmp_path, f"{DECOY_DIR}/tests/test_b.py")
    entry = _Entry("pytest", ".", ["."], tmp_path, 1, None, "pytest .")  # type: ignore[arg-type]
    assert _pytest_files(Repo(tmp_path), entry) == [tmp_path / "tests" / "test_a.py"]
    assert_never_entered(listed)


def test_dart_files_under(tmp_path, listed):
    write(tmp_path, "lib/a.dart", "class A {}\n")
    write(tmp_path, f"{DECOY_DIR}/lib/b.dart", "class B {}\n")
    assert dart_scanners.dart_files_under(tmp_path, ".") == ["lib/a.dart"]
    assert_never_entered(listed)


def test_deferred_entries_count(tmp_path, listed):
    write(tmp_path, "test_real.py", "_USER_DEFERRED_A = {'a'}\n")
    write(tmp_path, f"{DECOY_DIR}/test_decoy.py", "_USER_DEFERRED_B = {'a', 'b'}\n")
    assert deferred_entries.count_user_deferred_entries(tmp_path) == {"test_real::_USER_DEFERRED_A": 1}
    assert_never_entered(listed)


def test_column_no_write_path_sql_declarations(tmp_path, listed):
    write(tmp_path, "sql/a.sql", "CREATE TABLE t (id int);\n")
    write(tmp_path, f"{DECOY_DIR}/sql/b.sql", "CREATE TABLE u (id int);\n")
    declarations, texts = _collect_sql_declarations(tmp_path, tmp_path)
    assert list(declarations) == ["t"]
    assert len(texts) == 1
    assert_never_entered(listed)


def test_todo_markers(tmp_path, listed):
    write(tmp_path, "pkg/a.py", "# TODO(me): real\n")
    write(tmp_path, f"{DECOY_DIR}/pkg/b.py", "# TODO(me): decoy\n")
    found = _todo_markers.scan_todo_markers(tmp_path)
    assert [Path(p).name for p, *_ in found] == ["a.py"]
    assert_never_entered(listed)


@pytest.mark.parametrize("scanner", [scan_redundant_test_fit_calls, scan_source_text_assertions])
def test_test_file_scanners_open_only_the_real_file(tmp_path, listed, scanner):
    real = write(tmp_path, "tests/test_a.py", "def test_a():\n    assert 1\n")
    decoy = write(tmp_path, f"{DECOY_DIR}/tests/test_b.py", "def test_b():\n    assert 1\n")
    with count_opens(str(tmp_path)) as counts:
        scanner(tmp_path)
    opened = {Path(p) for p in counts.by_file}
    assert real.resolve() in {p.resolve() for p in opened}
    assert decoy.resolve() not in {p.resolve() for p in opened}
    assert_never_entered(listed)
