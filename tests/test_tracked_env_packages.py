"""Tests for py_ci_shared.tracked_env_packages: a tracked Python file under a directory code_audit prunes is reported."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.tracked_env_packages import RULE, assert_tracked_env_packages, find_tracked_env_packages

BOM = b"\xef\xbb\xbf"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def test_reports_a_tracked_file_under_env(tmp_path):
    root = _corpus(tmp_path, {"src/app/env/seed.py": b"def seed(): return 1\n", "src/app/ok.py": b"x = 1\n"})
    found = find_tracked_env_packages(root, use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("src/app/env/seed.py", 1, RULE)]
    assert "'env/'" in found[0].message


def test_negative_control_names_that_are_not_pruned_directories_are_not_reported(tmp_path):
    root = _corpus(
        tmp_path,
        {
            "src/app/environment.py": b"x = 1\n",  # a file named like 'env', not a directory
            "src/app/envoy/m.py": b"x = 1\n",  # a directory whose name merely starts with 'env'
            "src/app/builder/m.py": b"x = 1\n",  # 'build' as a prefix only
            "src/app/ok/m.py": b"x = 1\n",
        },
    )
    assert find_tracked_env_packages(root, use_git=False) == []


def test_a_bom_prefixed_file_under_env_is_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"src/app/env/seed.py": BOM + b"def seed(): return 1\n"})
    found = find_tracked_env_packages(root, use_git=False)
    assert [f.path for f in found] == ["src/app/env/seed.py"]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"src/app/env/broken.py": b"def (:\n", "src/app/ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_tracked_env_packages(root, use_git=False)
    assert find_tracked_env_packages(root, use_git=False, allow_unparsed=True) == []  # skipped, not reported as a finding


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_tracked_env_packages(tmp_path, use_git=False)


def test_assert_fails_on_a_finding_and_passes_on_a_clean_tree(tmp_path):
    clean = _corpus(tmp_path / "clean", {"src/app/ok.py": b"x = 1\n"})
    assert_tracked_env_packages(clean, use_git=False)
    dirty = _corpus(tmp_path / "dirty", {"src/app/env/m.py": b"x = 1\n"})
    with pytest.raises(AssertionError, match="tracked-env-packages"):
        assert_tracked_env_packages(dirty, use_git=False)
