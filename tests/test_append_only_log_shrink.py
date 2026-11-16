"""append_only_log_shrink (audit 50-T12): no first-parent commit may leave an append-only data file with fewer rows.

Each case builds a throwaway repository through ``tests/_git_repo_helper.py``; nothing is read from a real checkout.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from py_ci_shared._core import CoreError
from py_ci_shared._core.git import run_git
from py_ci_shared.append_only_log_shrink import (
    DEFAULT_PATHS,
    RULE,
    TRAILER,
    assert_append_only_log_shrink,
    find_append_only_log_shrinks,
)

from _git_repo_helper import make_repo

LOG = "log.jsonl"


def _commit(repo: Path, message: str) -> None:
    run_git(repo, "add", "-A", check=True)
    run_git(repo, "commit", "-q", "-m", message, check=True)


def _put(repo: Path, rel: str, data: bytes) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _lines(n: int) -> bytes:
    return "".join(f'{{"i": {i}}}\n' for i in range(n)).encode("utf-8")


def _history(tmp_path: Path, *steps: tuple[str, bytes]) -> Path:
    """A repository whose first commit holds ``steps[0]`` and each later step is one commit ``(message, content)``."""
    repo = tmp_path / "repo"
    make_repo(repo, {LOG: steps[0][1].decode("utf-8")})
    for message, data in steps[1:]:
        _put(repo, LOG, data)
        _commit(repo, message)
    return repo


def test_growing_log_is_clean(tmp_path: Path) -> None:
    repo = _history(tmp_path, ("a", _lines(3)), ("append", _lines(4)))
    assert find_append_only_log_shrinks(repo) == []


def test_replacing_with_a_shorter_log_is_flagged(tmp_path: Path) -> None:
    repo = _history(tmp_path, ("a", _lines(3)), ("stale copy", _lines(1)))
    found = find_append_only_log_shrinks(repo)
    assert [(f.path, f.rule) for f in found] == [(LOG, RULE)]
    assert "3 to 1 rows" in found[0].message


def test_trailer_waives_a_declared_shrink(tmp_path: Path) -> None:
    repo = _history(tmp_path, ("a", _lines(3)), (f"reset the fixture\n\n{TRAILER}: test data was wrong", _lines(1)))
    assert find_append_only_log_shrinks(repo) == []


def test_empty_trailer_value_does_not_waive(tmp_path: Path) -> None:
    repo = _history(tmp_path, ("a", _lines(3)), (f"shrink\n\n{TRAILER}:   ", _lines(1)))
    assert len(find_append_only_log_shrinks(repo)) == 1


def test_deleted_file_is_a_shrink_to_zero(tmp_path: Path) -> None:
    repo = _history(tmp_path, ("a", _lines(2)))
    run_git(repo, "rm", "-q", LOG, check=True)
    _commit(repo, "drop the log")
    found = find_append_only_log_shrinks(repo)
    assert len(found) == 1 and "2 to 0 rows" in found[0].message


def test_file_outside_the_configured_paths_is_ignored(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", {"notes.txt": "a\nb\nc\n"})
    _put(repo, "notes.txt", b"a\n")
    _commit(repo, "shrink notes")
    assert find_append_only_log_shrinks(repo) == []
    assert len(find_append_only_log_shrinks(repo, paths=("notes.txt",))) == 1


def test_gzip_log_is_counted_after_gunzip(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", {"README.md": "x\n"})
    _put(repo, "ledger.jsonl.gz", gzip.compress(_lines(5)))
    _commit(repo, "a")
    _put(repo, "ledger.jsonl.gz", gzip.compress(_lines(2)))
    _commit(repo, "b")
    found = find_append_only_log_shrinks(repo)
    assert [f.path for f in found] == ["ledger.jsonl.gz"]
    assert "5 to 2 rows" in found[0].message


def test_gzip_with_same_rows_and_different_bytes_is_clean(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", {"README.md": "x\n"})
    _put(repo, "ledger.jsonl.gz", gzip.compress(_lines(3), compresslevel=1))
    _commit(repo, "a")
    _put(repo, "ledger.jsonl.gz", gzip.compress(_lines(3), compresslevel=9))
    _commit(repo, "recompress")
    assert find_append_only_log_shrinks(repo) == []


def test_unreadable_gzip_in_a_commit_is_reported(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", {"README.md": "x\n"})
    _put(repo, "ledger.jsonl.gz", gzip.compress(_lines(3)))
    _commit(repo, "a")
    _put(repo, "ledger.jsonl.gz", b"not gzip at all")
    _commit(repo, "corrupt")
    found = find_append_only_log_shrinks(repo)
    assert len(found) == 1 and "cannot be read" in found[0].message


def test_only_first_parent_chain_is_compared(tmp_path: Path) -> None:
    """A side-branch commit that shrinks is not on the first-parent chain; only the merge that takes its content is reported."""
    repo = make_repo(tmp_path / "repo", {LOG: _lines(3).decode("utf-8")})
    run_git(repo, "checkout", "-q", "-b", "side", check=True)
    _put(repo, LOG, _lines(1))
    _commit(repo, "side shrinks")
    run_git(repo, "checkout", "-q", "-", check=True)
    run_git(repo, "merge", "--no-ff", "-q", "-m", "merge side", "side", check=True)
    found = find_append_only_log_shrinks(repo)
    assert len(found) == 1 and "merge side" in found[0].message and "3 to 1 rows" in found[0].message


def test_max_commits_bounds_the_walk(tmp_path: Path) -> None:
    repo = _history(tmp_path, ("a", _lines(3)), ("shrink", _lines(1)), ("grow", _lines(2)))
    assert find_append_only_log_shrinks(repo, max_commits=1) == []
    assert len(find_append_only_log_shrinks(repo, max_commits=2)) == 1


def test_default_paths_cover_jsonl_and_gzipped_jsonl() -> None:
    assert "*.jsonl" in DEFAULT_PATHS and "*.jsonl.gz" in DEFAULT_PATHS


def test_assert_raises_naming_the_file(tmp_path: Path) -> None:
    repo = _history(tmp_path, ("a", _lines(3)), ("shrink", _lines(1)))
    with pytest.raises(AssertionError, match=r"log\.jsonl"):
        assert_append_only_log_shrink(repo)


def test_assert_passes_on_growing_history(tmp_path: Path) -> None:
    assert_append_only_log_shrink(_history(tmp_path, ("a", _lines(1)), ("b", _lines(2))))


def test_unknown_revision_is_an_error_not_a_pass(tmp_path: Path) -> None:
    repo = _history(tmp_path, ("a", _lines(3)))
    with pytest.raises(CoreError):
        find_append_only_log_shrinks(repo, rev="no-such-revision")
