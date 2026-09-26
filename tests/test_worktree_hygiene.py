"""Unit tests for the worktree-hygiene report: real git repositories, real worktrees, real leftovers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared.worktree_hygiene import (
    ORPHAN_DIR,
    REMOVABLE,
    REVIEW,
    GitQueryError,
    _hash,
    branches_without_unique_commits,
    main,
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
    (worktree / "kept.txt").write_bytes(b"upstream content\r\n")

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


class TestAuditRegressions:
    """Staged, ignored and unanswerable-git cases: none may ever yield REMOVABLE or "spare"."""

    def _worktree(self, clone: Path, name: str) -> Path:
        worktree = clone.parent / name
        _git(clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")
        return worktree

    def test_staged_only_work_is_unsaved_even_though_its_blob_exists(self, origin_and_clone):
        worktree = self._worktree(origin_and_clone, "wt_staged")
        (worktree / "new.py").write_text("print('staged, never committed')\n", encoding="utf-8")
        _git(worktree, "add", "new.py")
        blob = _git(worktree, "rev-parse", ":new.py").strip()
        assert subprocess.run(["git", "-C", str(origin_and_clone), "cat-file", "-e", blob]).returncode == 0

        finding = worktree_findings(origin_and_clone)[0]

        assert finding.verdict == REVIEW
        assert finding.residual == ("new.py",)

    def test_staged_content_differing_from_the_working_file_is_judged(self, origin_and_clone):
        worktree = self._worktree(origin_and_clone, "wt_staged_then_reverted")
        (worktree / "kept.txt").write_text("staged edit nobody committed\n", encoding="utf-8")
        _git(worktree, "add", "kept.txt")
        (worktree / "kept.txt").write_text("upstream content\n", encoding="utf-8")  # working file back to upstream

        assert unsaved_paths(origin_and_clone, worktree) == ["kept.txt"]

    def test_staged_content_already_on_a_ref_is_saved(self, origin_and_clone):
        worktree = self._worktree(origin_and_clone, "wt_staged_known")
        (worktree / "copy.txt").write_text("upstream content\n", encoding="utf-8")
        _git(worktree, "add", "copy.txt")

        assert unsaved_paths(origin_and_clone, worktree) == []
        assert worktree_findings(origin_and_clone)[0].verdict == REMOVABLE

    def test_an_ignored_file_is_unsaved_work(self, origin_and_clone):
        worktree = self._worktree(origin_and_clone, "wt_ignored")
        (worktree / ".gitignore").write_text(".env\ndata/\n", encoding="utf-8")
        (worktree / ".env").write_text("SECRET=only-here\n", encoding="utf-8")
        (worktree / "data").mkdir()
        (worktree / "data" / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        assert _git(worktree, "check-ignore", ".env").strip() == ".env"

        finding = worktree_findings(origin_and_clone)[0]

        assert finding.verdict == REVIEW
        assert set(finding.residual) >= {".env", "data/rows.csv"}

    def test_an_ignored_cache_directory_is_still_skipped(self, origin_and_clone):
        worktree = self._worktree(origin_and_clone, "wt_ignored_cache")
        (worktree / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        _git(worktree, "add", ".gitignore")
        _git(worktree, "commit", "-q", "-m", "ignore")
        _git(worktree, "push", "-q", "origin", "HEAD:master")
        (worktree / "__pycache__").mkdir()
        (worktree / "__pycache__" / "m.cpython-311.pyc").write_bytes(b"\x00\x01")

        assert unsaved_paths(origin_and_clone, worktree) == []

    def test_a_rename_source_is_not_read_as_an_entry(self, origin_and_clone):
        (origin_and_clone / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git(origin_and_clone, "add", "a.py")
        _git(origin_and_clone, "commit", "-q", "-m", "a")
        _git(origin_and_clone, "push", "-q", "origin", "master")
        worktree = self._worktree(origin_and_clone, "wt_rename")
        _git(worktree, "mv", "a.py", "bb.py")
        (worktree / "py").write_text("unrelated unsaved file whose name is the [3:] cut of 'a.py'\n", encoding="utf-8")

        unsaved = unsaved_paths(origin_and_clone, worktree)

        assert unsaved == ["py"]  # bb.py holds committed content; the old path token "a.py" is not an entry

    def test_a_bad_ref_raises_instead_of_calling_every_branch_spare(self, origin_and_clone):
        _git(origin_and_clone, "branch", "has-its-own", "origin/master")
        with pytest.raises(GitQueryError, match="origin/nope"):
            branches_without_unique_commits(origin_and_clone, ref="origin/nope")
        with pytest.raises(GitQueryError):
            worktree_findings(origin_and_clone, ref="origin/nope")
        assert "has-its-own" in branches_without_unique_commits(origin_and_clone)

    def test_the_cli_reports_a_bad_ref_with_exit_2(self, origin_and_clone, capsys):
        assert main([str(origin_and_clone), "--ref", "origin/nope"]) == 2
        assert "origin/nope" in capsys.readouterr().err
        assert main([str(origin_and_clone)]) == 0

    def test_blob_ids_match_git_hash_object(self, origin_and_clone):
        data = b"line one\r\nline two\n\x00binary"
        expected = (
            subprocess.run(["git", "-C", str(origin_and_clone), "hash-object", "--stdin"], input=data, capture_output=True, check=True).stdout.decode().strip()
        )
        assert _hash(origin_and_clone, data) == expected
        assert _hash(origin_and_clone, data + b"!") != expected

    def test_no_git_process_is_spawned_per_file(self, origin_and_clone, monkeypatch):
        leftover = origin_and_clone / ".claude" / "worktrees" / "big-orphan"
        leftover.mkdir(parents=True)
        for i in range(40):
            (leftover / f"f{i}.txt").write_text(f"file {i}\n", encoding="utf-8")
        calls: list[tuple[str, ...]] = []
        real_run = subprocess.run

        def counting_run(cmd, *args, **kwargs):
            calls.append(tuple(cmd))
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", counting_run)
        findings = worktree_findings(origin_and_clone)

        assert findings[0].verdict == ORPHAN_DIR and len(findings[0].residual) == 40
        assert len(calls) < 15


def test_an_unreadable_file_is_unsaved_work_not_a_crash(origin_and_clone, monkeypatch):
    """A running browser keeps its profile cache locked; reading it raised PermissionError and aborted the report for
    every worktree. An unreadable file is judged unsaved, so the worktree holding it is kept for review."""
    worktree = origin_and_clone.parent / "wt_locked"
    _git(origin_and_clone, "worktree", "add", "-q", "--detach", str(worktree), "origin/master")
    (worktree / ".gitignore").write_text("cache/\n", encoding="utf-8")
    (worktree / "cache").mkdir()
    locked_in_ignored_dir = worktree / "cache" / "data_0"
    locked_in_ignored_dir.write_bytes(b"held open by a browser")
    locked_untracked = worktree / "open.log"
    locked_untracked.write_bytes(b"held open too")
    locked = {locked_in_ignored_dir.resolve(), locked_untracked.resolve()}
    real_read_bytes = Path.read_bytes

    def read_bytes(self):
        if self.resolve() in locked:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    unsaved = unsaved_paths(origin_and_clone, worktree)

    assert {"cache/data_0", "open.log"} <= set(unsaved)
    assert worktree_findings(origin_and_clone)[0].verdict == REVIEW

