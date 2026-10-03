"""`_core.corpus`: git's view inside a work tree, a pruned walk outside, exclusion relative to the root."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from py_ci_shared._core import DEFAULT_EXCLUDE, CorpusError, iter_files, relative_posix

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")


def _rels(root: Path, files: list[Path]) -> list[str]:
    return [f.relative_to(root).as_posix() for f in files]


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)


class TestWalk:
    def test_default_exclude_is_the_union_of_the_old_lists(self):
        for name in (
            "__pycache__",
            ".git",
            ".venv",
            "venv",
            "build",
            "dist",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
            ".claude",
            ".tox",
            ".eggs",
            "site-packages",
            "node_modules",
        ):
            assert name in DEFAULT_EXCLUDE, name
        # value_bearing_asserts' scope names are that gate's policy, not corpus junk
        assert "tests" not in DEFAULT_EXCLUDE and "scripts" not in DEFAULT_EXCLUDE

    def test_excluded_dirs_are_pruned_and_output_is_sorted(self, tmp_path):
        _touch(tmp_path, "b.py", "a.py", "sub/c.py", "build/x.py", ".venv/lib/y.py", "sub/__pycache__/z.py", "notes.txt")
        assert _rels(tmp_path, iter_files(tmp_path, use_git=False)) == ["a.py", "b.py", "sub/c.py"]

    def test_a_root_under_a_dir_named_build_is_not_excluded(self, tmp_path):
        """ARCH-15: matching the skip list against ABSOLUTE parts skipped a whole checkout under `build/`."""
        root = tmp_path / "build" / "venv" / "checkout"
        _touch(root, "pkg/m.py", "pkg/build/gen.py")
        assert _rels(root, iter_files(root, use_git=False)) == ["pkg/m.py"]

    def test_patterns_match_names_or_relative_paths(self, tmp_path):
        _touch(tmp_path, "a.py", "b.pyi", "d/e.py", "d/f.txt")
        assert _rels(tmp_path, iter_files(tmp_path, ("*.pyi",), use_git=False)) == ["b.pyi"]
        assert _rels(tmp_path, iter_files(tmp_path, ("d/*",), use_git=False)) == ["d/e.py", "d/f.txt"]
        assert _rels(tmp_path, iter_files(tmp_path, ("*.py", "*.txt"), use_git=False)) == ["a.py", "d/e.py", "d/f.txt"]

    def test_a_custom_exclude_replaces_the_default(self, tmp_path):
        _touch(tmp_path, "build/x.py", "keep/y.py", "drop/z.py")
        assert _rels(tmp_path, iter_files(tmp_path, exclude={"drop"}, use_git=False)) == ["build/x.py", "keep/y.py"]

    def test_a_missing_root_raises(self, tmp_path):
        with pytest.raises(CorpusError, match="does not exist"):
            iter_files(tmp_path / "missing")
        (tmp_path / "f.py").write_text("", encoding="utf-8")
        with pytest.raises(CorpusError, match="not a directory"):
            iter_files(tmp_path / "f.py")
        assert _rels(tmp_path, iter_files(tmp_path, use_git=False)) == ["f.py"]  # control

    def test_forcing_git_outside_a_work_tree_raises(self, tmp_path):
        with pytest.raises(CorpusError, match="git work tree"):
            iter_files(tmp_path, use_git=True)


@needs_git
class TestGit:
    def test_ignored_files_are_left_out_and_untracked_ones_kept(self, tmp_path):
        _git_init(tmp_path)
        _touch(tmp_path, "tracked.py", "untracked.py", "out/ignored.py", "gen.py")
        (tmp_path / ".gitignore").write_text("out/\ngen.py\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True, capture_output=True)
        assert _rels(tmp_path, iter_files(tmp_path)) == ["tracked.py", "untracked.py"]
        assert _rels(tmp_path, iter_files(tmp_path, include_untracked=False)) == ["tracked.py"]
        # control: the walk (no git) sees the ignored files, proving git was what dropped them
        assert _rels(tmp_path, iter_files(tmp_path, use_git=False)) == ["gen.py", "out/ignored.py", "tracked.py", "untracked.py"]

    def test_a_checkout_under_build_is_scanned_through_git(self, tmp_path):
        root = tmp_path / "build" / "repo"
        root.mkdir(parents=True)
        _git_init(root)
        _touch(root, "pkg/m.py", "pkg/build/gen.py")
        assert _rels(root, iter_files(root)) == ["pkg/m.py"]

    def test_a_subdirectory_root_lists_relative_to_itself(self, tmp_path):
        _git_init(tmp_path)
        _touch(tmp_path, "src/pkg/a.py", "other/b.py")
        assert _rels(tmp_path / "src", iter_files(tmp_path / "src")) == ["pkg/a.py"]

    def test_a_tracked_file_deleted_from_disk_is_not_listed(self, tmp_path):
        _git_init(tmp_path)
        _touch(tmp_path, "a.py", "b.py")
        subprocess.run(["git", "-C", str(tmp_path), "add", "a.py", "b.py"], check=True, capture_output=True)
        (tmp_path / "b.py").unlink()
        assert _rels(tmp_path, iter_files(tmp_path)) == ["a.py"]

    def test_an_ignored_root_falls_back_to_the_walk(self, tmp_path):
        _git_init(tmp_path)
        (tmp_path / ".gitignore").write_text("scratch/\n", encoding="utf-8")
        _touch(tmp_path, "scratch/a.py")
        assert _rels(tmp_path / "scratch", iter_files(tmp_path / "scratch")) == ["a.py"]
        assert _rels(tmp_path, iter_files(tmp_path, ("*.py",))) == []  # control: from the repo root it IS ignored


def test_relative_posix_never_raises(tmp_path):
    inside = tmp_path / "a" / "b.py"
    assert relative_posix(inside, tmp_path) == "a/b.py"
    outside = tmp_path.parent / "elsewhere.py"
    assert relative_posix(outside, tmp_path) == outside.as_posix()
    assert relative_posix(inside, None) == inside.as_posix()


def _failing_ls_files(monkeypatch, outcome):
    """Make only `git ls-files` time out (outcome None) or exit non-zero; every other git call is real."""
    real = subprocess.run

    def run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "ls-files" in list(argv):
            if outcome is None:
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 120)
            return subprocess.CompletedProcess(argv, outcome, b"", b"fatal: index file corrupt")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


@needs_git
class TestListingFailures:
    """K-4: once git says the root IS a work tree, a failed listing is an error, never a silent switch to the walk
    (which reads ignored build output and submodule content)."""

    def _repo(self, tmp_path: Path) -> Path:
        _git_init(tmp_path)
        _touch(tmp_path, "a.py", "generated/junk.py")
        (tmp_path / ".gitignore").write_text("generated/\n", encoding="utf-8")
        assert _rels(tmp_path, iter_files(tmp_path)) == ["a.py"]  # control: git mode drops the ignored file
        return tmp_path

    @pytest.mark.parametrize("outcome", [None, 128], ids=["timeout", "exit-128"])
    def test_a_failed_ls_files_raises_instead_of_walking(self, tmp_path, monkeypatch, outcome):
        repo = self._repo(tmp_path)
        _failing_ls_files(monkeypatch, outcome)
        with pytest.raises(CorpusError, match=r"ls-files|timed out"):
            iter_files(repo)

    def test_the_walk_stays_available_on_request(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        _failing_ls_files(monkeypatch, 128)
        assert _rels(repo, iter_files(repo, use_git=False)) == ["a.py", "generated/junk.py"]


def test_the_walk_leaves_submodule_checkouts_out_like_git_does(tmp_path):
    """A directory holding a `.git` FILE is a submodule (or linked worktree): git lists it as one gitlink, never its
    files, so the walk must not read them either."""
    _touch(tmp_path, "a.py", "vendor/sub/inner.py", "vendor/plain/kept.py")
    (tmp_path / "vendor" / "sub" / ".git").write_text("gitdir: ../../.git/modules/sub\n", encoding="utf-8")
    assert _rels(tmp_path, iter_files(tmp_path, use_git=False)) == ["a.py", "vendor/plain/kept.py"]
