"""Unit tests for the worktree-hygiene report: real git repositories, real worktrees, real leftovers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.worktree_hygiene import (
    ORPHAN_DIR,
    REMOVABLE,
    REVIEW,
    branches_without_unique_commits,
    orphan_directories,
    registered_worktrees,
    report,
    unsaved_paths,
    worktree_findings,
)


def _git(repo: Path, *args: str) -> str:
    """Run git in *repo*, failing the test loudly when git itself fails."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout


@pytest.fixture
def origin_and_clone(tmp_path):
    """A bare 'remote' with one commit, and a clone of it whose origin/HEAD resolves."""
    bare = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "master")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    (seed / "kept.txt").write_text("upstream content\n", encoding="utf-8")
    _git(seed, "add", "kept.txt")
    _git(seed, "commit", "-q", "-m", "seed")
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True, capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "T")
    _git(clone, "remote", "set-head", "origin", "master")
    return clone


def test_a_worktree_matching_upstream_is_removable(origin_and_clone):
    """Nothing changed, HEAD is on origin: the worktree is debris."""
    worktree = origin_and_clone.parent / "wt_clean"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")

    findings = worktree_findings(origin_and_clone)

    assert [f.verdict for f in findings] == [REMOVABLE]
    assert findings[0].head_on_remote is True


def test_a_copy_of_an_upstream_file_is_not_unsaved_work(origin_and_clone):
    """The copy a landing leaves behind reads as dirty forever; identical content is not work."""
    worktree = origin_and_clone.parent / "wt_copy"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")
    (worktree / "kept.txt").write_text("upstream content\r\n", encoding="utf-8", newline="")

    assert unsaved_paths(origin_and_clone, worktree) == []
    assert worktree_findings(origin_and_clone)[0].verdict == REMOVABLE


def test_content_committed_anywhere_in_history_is_not_unsaved(origin_and_clone):
    """A stale copy sitting on an old commit differs from master while holding nothing new."""
    (origin_and_clone / "kept.txt").write_text("second revision\n", encoding="utf-8")
    _git(origin_and_clone, "commit", "-q", "-am", "second revision")
    _git(origin_and_clone, "push", "-q", "origin", "master")
    worktree = origin_and_clone.parent / "wt_old"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")
    (worktree / "kept.txt").write_text("upstream content\n", encoding="utf-8")  # the FIRST commit's content

    assert unsaved_paths(origin_and_clone, worktree) == []


def test_real_uncommitted_work_is_reported_not_removable(origin_and_clone):
    """Content that exists nowhere else is the one thing this must never call debris."""
    worktree = origin_and_clone.parent / "wt_work"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")
    (worktree / "new_analysis.md").write_text("a finding nobody committed\n", encoding="utf-8")

    findings = worktree_findings(origin_and_clone)

    assert findings[0].verdict == REVIEW
    assert findings[0].residual == ("new_analysis.md",)
    assert "new_analysis.md" in report(origin_and_clone)


def test_a_deletion_is_not_unsaved_work(origin_and_clone):
    """What a deletion removes is still in the ref, so a deletion-only worktree is removable."""
    worktree = origin_and_clone.parent / "wt_deleted"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")
    (worktree / "kept.txt").unlink()

    assert unsaved_paths(origin_and_clone, worktree) == []


def test_a_worktree_off_every_remote_branch_needs_a_look(origin_and_clone):
    """A commit no remote contains is unsaved even when the working tree is clean."""
    worktree = origin_and_clone.parent / "wt_local_commit"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")
    (worktree / "kept.txt").write_text("local only\n", encoding="utf-8")
    _git(worktree, "commit", "-q", "-am", "a commit that never left this machine")

    finding = worktree_findings(origin_and_clone)[0]

    assert finding.verdict == REVIEW and finding.head_on_remote is False
    assert "no remote branch" in finding.note


def test_an_unregistered_directory_is_found_and_judged(origin_and_clone):
    """The 100 MB copies git has forgotten appear in no git listing at all."""
    leftover = origin_and_clone / ".claude" / "worktrees" / "agent-abandoned"
    leftover.mkdir(parents=True)
    (leftover / "scratch.txt").write_text("left behind\n", encoding="utf-8")

    assert orphan_directories(origin_and_clone) == [leftover]
    findings = worktree_findings(origin_and_clone)
    assert [f.verdict for f in findings] == [ORPHAN_DIR]
    assert findings[0].registered is False


def test_a_shared_cache_directory_is_left_alone(origin_and_clone):
    """A dot-directory beside the worktrees is a deliberate shared cache, not debris."""
    cache = origin_and_clone / ".claude" / "worktrees" / ".mlframe_mypy_cache_shared"
    cache.mkdir(parents=True)
    (cache / "cache.db").write_bytes(b"\x00")

    assert orphan_directories(origin_and_clone) == []


def test_a_registered_worktree_missing_from_disk_is_prunable(origin_and_clone):
    """git keeps the registration after the directory goes; say so instead of crashing on it."""
    worktree = origin_and_clone.parent / "wt_vanished"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")
    (worktree / "kept.txt").unlink()
    worktree.rmdir() if not any(worktree.iterdir()) else None
    for leftover in sorted(worktree.rglob("*"), reverse=True):
        leftover.unlink() if leftover.is_file() else leftover.rmdir()
    if worktree.exists():
        worktree.rmdir()

    finding = worktree_findings(origin_and_clone)[0]

    assert finding.verdict == REMOVABLE and "prune" in finding.note


def test_a_branch_whose_commits_are_upstream_is_spare(origin_and_clone):
    """A branch is spare when every commit of its own is already in the ref, by patch id."""
    _git(origin_and_clone, "branch", "already-merged", "origin/master")
    _git(origin_and_clone, "branch", "has-its-own", "origin/master")
    worktree = origin_and_clone.parent / "wt_branch"
    _git(origin_and_clone, "worktree", "add", "-q", str(worktree), "has-its-own")
    (worktree / "only-here.txt").write_text("unique\n", encoding="utf-8")
    _git(worktree, "add", "only-here.txt")
    _git(worktree, "commit", "-q", "-m", "a commit of its own")

    spare = branches_without_unique_commits(origin_and_clone)

    assert "already-merged" in spare
    assert "has-its-own" not in spare  # checked out, and it carries a commit the ref lacks
    assert "master" not in spare


def test_registered_worktrees_lists_the_main_checkout_first(origin_and_clone):
    """The main checkout leads the list, and it is never judged as debris."""
    worktree = origin_and_clone.parent / "wt_order"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")

    listed = registered_worktrees(origin_and_clone)

    assert listed[0].resolve() == origin_and_clone.resolve()
    assert [f.path.resolve() for f in worktree_findings(origin_and_clone)] == [worktree.resolve()]
