"""The deferred-entry counter counts literal collections, and only under the directories py-ci-shared scans."""

from __future__ import annotations

import logging
from pathlib import Path

from py_ci_shared.deferred_entries import count_user_deferred_entries


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_counts_each_literal_deferred_collection_by_file_stem_and_name(tmp_path: Path) -> None:
    _write(tmp_path, "test_alpha.py", '_USER_DEFERRED_A = {"x", "y", "z"}\n')
    assert count_user_deferred_entries(tmp_path) == {"test_alpha::_USER_DEFERRED_A": 3}


def test_an_annotated_assignment_counts_too(tmp_path: Path) -> None:
    _write(tmp_path, "test_beta.py", "_GRANDFATHERED_B: set[str] = {'a'}\n")
    assert count_user_deferred_entries(tmp_path) == {"test_beta::_GRANDFATHERED_B": 1}


def test_an_empty_set_call_counts_as_zero_but_a_call_with_an_argument_is_not_counted(tmp_path: Path) -> None:
    _write(tmp_path, "test_gamma.py", "_USER_DEFERRED_EMPTY = set()\n_USER_DEFERRED_FROM = set(_OTHER)\n")
    assert count_user_deferred_entries(tmp_path) == {"test_gamma::_USER_DEFERRED_EMPTY": 0}


def test_a_name_without_a_deferred_prefix_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "test_delta.py", "_KNOWN = {'a', 'b'}\n")
    assert count_user_deferred_entries(tmp_path) == {}


def test_extra_prefixes_extend_the_match(tmp_path: Path) -> None:
    _write(tmp_path, "test_eps.py", "_KNOWN_LEGACY = {'a', 'b'}\n")
    assert count_user_deferred_entries(tmp_path, extra_prefixes=("_KNOWN_",)) == {"test_eps::_KNOWN_LEGACY": 2}


def test_a_worktree_under_the_root_is_not_counted_as_a_second_copy(tmp_path: Path) -> None:
    _write(tmp_path, "test_zeta.py", "_USER_DEFERRED_Z = {'a'}\n")
    _write(tmp_path, ".claude/worktrees/w1/test_zeta.py", "_USER_DEFERRED_Z = {'a', 'b', 'c'}\n")
    assert count_user_deferred_entries(tmp_path) == {"test_zeta::_USER_DEFERRED_Z": 1}


def test_an_unparseable_file_is_reported_with_the_unreadable_ones(tmp_path: Path, caplog) -> None:
    """A file that does not parse is an unscanned file, reported like an unreadable one, never a silent zero."""
    _write(tmp_path, "test_broken.py", "_USER_DEFERRED_B = {\n")
    with caplog.at_level(logging.WARNING):
        assert count_user_deferred_entries(tmp_path) == {}
    assert "could not be read and were not scanned" in caplog.text
    assert "test_broken.py" in caplog.text


def test_a_missing_directory_is_an_empty_count(tmp_path: Path) -> None:
    assert count_user_deferred_entries(tmp_path / "absent") == {}
