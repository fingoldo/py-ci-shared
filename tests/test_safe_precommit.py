"""Behavioral tests for the stash-restore race patch (py_ci_shared.safe_precommit).

Exercises the patched context manager directly against a real git repo, simulating the exact
race: unstaged changes get stashed, something ELSE (a concurrent session) mutates the same file
before the restore, so the restore-patch no longer applies. Unpatched pre_commit raises and would
abort the whole commit; the patched version must instead warn and let the `with` block exit
cleanly, leaving the patch file on disk untouched.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pre_commit")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


_BASE = "line_a\nline_b\nline_c\nline_d\nline_e\nline_f\nline_g\nline_h\n"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "test@test.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "tracked.py").write_text(_BASE)
    _git("add", "tracked.py", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    return repo


def _unstaged_edit(repo: Path) -> None:
    """Modify line_d in place -- a real replacement (not a pure insertion), so a concurrent
    replacement of the SAME line makes the stashed patch's context genuinely stop matching."""
    text = _BASE.replace("line_d\n", "line_d_UNSTAGED_EDIT\n")
    (repo / "tracked.py").write_text(text)


def _concurrent_commit(repo: Path) -> None:
    """A DIFFERENT session's own commit landing on the SAME line while ours is stashed. This is
    the real production race: pre-commit's rollback-and-retry (`git checkout -- .`) successfully
    handles a mere WORKING-TREE conflict (it discards whatever's there and reapplies against clean
    HEAD) -- what it cannot handle is HEAD itself moving forward, because the stashed patch's
    context was computed against the OLD HEAD blob and no longer matches the NEW one."""
    text = _BASE.replace("line_d\n", "line_d_CONCURRENT_SESSION_COMMIT\n")
    (repo / "tracked.py").write_text(text)
    _git("commit", "-q", "-a", "-m", "concurrent session's own commit", cwd=repo)


def test_patch_stash_restore_survives_a_conflicting_concurrent_edit(tmp_path, monkeypatch):
    """Simulate the exact race: stash unstaged changes, mutate the file underneath (as a
    concurrent session would), then verify the patched restore does not raise.

    Applies the patch via ``monkeypatch.setattr`` (not the real ``patch_stash_restore()``, which
    mutates the shared ``pre_commit.staged_files_only`` module attribute directly and would leak
    across tests since nothing else undoes it) so pytest reverts it automatically on teardown --
    otherwise this test's patch would still be active in whichever OTHER test runs next under
    pytest-randomly's randomized ordering, corrupting e.g. the unpatched-control test below.
    """
    import pre_commit.staged_files_only as sfo

    from py_ci_shared.safe_precommit import _patched_unstaged_changes_cleared

    monkeypatch.setattr(sfo, "_unstaged_changes_cleared", _patched_unstaged_changes_cleared(sfo))

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)

    # tracked.py is already at HEAD's _BASE content (nothing staged beyond init) -- the unstaged
    # edit below is what staged_files_only stashes and later tries to restore.
    _unstaged_edit(repo)

    patch_dir = repo / ".git" / "pc_patches"
    with sfo.staged_files_only(str(patch_dir)):
        # Inside the context, the working tree matches HEAD (unstaged edit stashed away).
        # Simulate a concurrent session editing the SAME line so the later patch-reapply
        # genuinely conflicts (context mismatch), not just an unrelated independent hunk.
        _concurrent_commit(repo)
    # No exception means the patched tail caught the un-restorable conflict and warned instead
    # of raising -- this is the whole point of the patch.

    # The concurrent edit must survive (we never silently discard it either).
    assert "line_d_CONCURRENT_SESSION_COMMIT" in (repo / "tracked.py").read_text()
    # The original unstaged edit must be preserved on disk as a patch file, not silently dropped.
    patches = list(patch_dir.glob("patch*"))
    assert patches, "the stashed-but-unrestorable patch file must be preserved, never deleted"
    assert "line_d_UNSTAGED_EDIT" in patches[0].read_text()


def test_unpatched_pre_commit_would_raise_on_the_same_race(tmp_path, monkeypatch):
    """Control: without the patch, pre_commit's own staged_files_only raises on this exact race
    (pins that the scenario above is a real bug in upstream pre-commit, not a strawman)."""
    import pre_commit.staged_files_only as sfo
    from pre_commit.util import CalledProcessError

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    _unstaged_edit(repo)

    patch_dir = repo / ".git" / "pc_patches"
    with pytest.raises(CalledProcessError):
        with sfo.staged_files_only(str(patch_dir)):
            _concurrent_commit(repo)


def test_patch_stash_restore_is_a_noop_when_pre_commit_missing(monkeypatch):
    """patch_stash_restore() must degrade gracefully (return False, never raise) if pre_commit
    isn't importable -- py-ci-shared is deliberately dependency-free."""
    from py_ci_shared import safe_precommit

    monkeypatch.setitem(sys.modules, "pre_commit.staged_files_only", None)
    monkeypatch.delitem(sys.modules, "pre_commit.staged_files_only", raising=False)
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name, *a, **kw):
        if name == "pre_commit.staged_files_only" or name.startswith("pre_commit"):
            raise ImportError("simulated: pre-commit not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    assert safe_precommit.patch_stash_restore() is False
