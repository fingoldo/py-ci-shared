"""`_core.corpus`: git's view inside a work tree, a pruned walk outside, exclusion relative to the root."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from py_ci_shared._core import DEFAULT_EXCLUDE, CorpusError, iter_files, relative_posix
from py_ci_shared._core.corpus import clear_listing_cache, iter_files_pruned
from py_ci_shared._core.git import git_env

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")


def _rels(root: Path, files: list[Path]) -> list[str]:
    return [f.relative_to(root).as_posix() for f in files]


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, env=git_env())


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
        subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True, capture_output=True, env=git_env())
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
        subprocess.run(["git", "-C", str(tmp_path), "add", "a.py", "b.py"], check=True, capture_output=True, env=git_env())
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


def _flaky_ls_files(monkeypatch, plan):
    """Replace each `git ls-files` call in turn with the next entry of *plan*: ``("exit", rc, stderr)`` or ``"timeout"``.

    Once the plan is spent the real git runs. Returns the list of calls seen so a test can count retries.
    """
    real = subprocess.run
    seen: list[str] = []
    steps = list(plan)

    def run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "ls-files" in list(argv):
            seen.append("ls-files")
            if steps:
                step = steps.pop(0)
                if step == "timeout":
                    raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 120)
                _, rc, stderr = step
                return subprocess.CompletedProcess(argv, rc, b"", stderr)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return seen


@needs_git
class TestTransientListingFailures:
    """A git that exits non-zero with no stderr, or times out, is retried; the same failure every time is an error that
    names the exit code, the stderr (or its absence) and the working directory."""

    def _repo(self, tmp_path: Path) -> Path:
        _git_init(tmp_path)
        _touch(tmp_path, "a.py", "generated/junk.py")
        (tmp_path / ".gitignore").write_text("generated/\n", encoding="utf-8")
        return tmp_path

    def test_one_silent_failure_is_retried_with_a_warning(self, tmp_path, monkeypatch, caplog):
        repo = self._repo(tmp_path)
        seen = _flaky_ls_files(monkeypatch, [("exit", 3221225794, b"")])
        with caplog.at_level(logging.WARNING, logger="py_ci_shared._core.corpus"):
            assert _rels(repo, iter_files(repo)) == ["a.py"]
        assert len(seen) == 2
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "ls-files" in r.getMessage()]
        assert len(warnings) == 1
        assert "3221225794" in warnings[0].getMessage()

    def test_a_single_timeout_is_retried(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        seen = _flaky_ls_files(monkeypatch, ["timeout"])
        assert _rels(repo, iter_files(repo)) == ["a.py"]
        assert len(seen) == 2

    def test_a_failure_every_time_raises_with_exit_code_and_cwd(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        seen = _flaky_ls_files(monkeypatch, [("exit", 7, b"")] * 10)
        with pytest.raises(CorpusError, match=r"exit code 7, stderr: no stderr") as info:
            iter_files(repo)
        assert len(seen) == 3  # the first attempt and two retries
        assert str(Path.cwd()) in str(info.value)

    def test_a_failure_with_stderr_is_not_retried(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        seen = _flaky_ls_files(monkeypatch, [("exit", 128, b"fatal: index file corrupt")] * 10)
        with pytest.raises(CorpusError, match=r"exit code 128, stderr: fatal: index file corrupt"):
            iter_files(repo)
        assert len(seen) == 1


@needs_git
class TestTrackedDirectoryNamedLikeBuildOutput:
    """Audit 42-F1: a tracked package directory named build/dist/venv is corpus; an untracked one still is not."""

    def test_a_tracked_package_named_build_is_listed(self, tmp_path):
        _git_init(tmp_path)
        _touch(tmp_path, "src/pkg/build/m.py", "src/pkg/ok.py")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True, env=git_env())
        assert _rels(tmp_path, iter_files(tmp_path)) == ["src/pkg/build/m.py", "src/pkg/ok.py"]

    def test_an_untracked_build_directory_is_still_skipped(self, tmp_path):
        _git_init(tmp_path)
        _touch(tmp_path, "src/pkg/ok.py")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True, env=git_env())
        _touch(tmp_path, "src/pkg/build/m.py", "dist/n.py")
        assert _rels(tmp_path, iter_files(tmp_path)) == ["src/pkg/ok.py"]

    def test_a_tracked_dot_directory_stays_excluded(self, tmp_path):
        _git_init(tmp_path)
        _touch(tmp_path, "src/ok.py", ".claude/w/x.py", "pkg/__pycache__/y.py")
        subprocess.run(["git", "add", "-Af"], cwd=tmp_path, check=True, capture_output=True, env=git_env())
        assert _rels(tmp_path, iter_files(tmp_path)) == ["src/ok.py"]


def test_the_walk_leaves_nested_repository_checkouts_out_like_git_does(tmp_path):
    """A nested clone (a ``.git`` DIRECTORY: a vendored repository) is listed by git as a bare directory and so never read;
    the walk must prune it too, or the corpus differs between a git checkout and a plain copy (audit 42-F11)."""
    _touch(tmp_path, "a.py", "vendor/clone/inner.py", "vendor/plain/kept.py")
    (tmp_path / "vendor" / "clone" / ".git").mkdir()
    assert _rels(tmp_path, iter_files(tmp_path, use_git=False)) == ["a.py", "vendor/plain/kept.py"]
    assert not any("clone" in str(p) for p in iter_files_pruned(tmp_path, (".py",)))


def test_the_root_itself_may_be_a_repository_checkout(tmp_path):
    """Only components BELOW the root are judged: a root that holds its own ``.git`` directory is the normal case."""
    _touch(tmp_path, "a.py")
    (tmp_path / ".git").mkdir()
    assert _rels(tmp_path, iter_files(tmp_path, use_git=False)) == ["a.py"]


@needs_git
class TestDubiousOwnership:
    """git refuses a work tree owned by someone else; the corpus used to become the walk (ignored files and nested clones
    included), so two machines read different files. It now raises, naming the fix (audit 42-F11)."""

    def test_raises_instead_of_widening_to_the_walk(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        _touch(tmp_path, "a.py", "ignored.py")
        (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
        assert _rels(tmp_path, iter_files(tmp_path)) == ["a.py"]
        monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
        clear_listing_cache()  # a root git called usable stays remembered; a new run starts empty
        probe = subprocess.run(["git", "-C", str(tmp_path), "rev-parse"], capture_output=True, env=git_env(), check=False)
        if b"dubious ownership" not in probe.stderr:
            pytest.skip("this git does not honour GIT_TEST_ASSUME_DIFFERENT_OWNER")
        with pytest.raises(CorpusError, match=r"dubious ownership.*safe\.directory"):
            iter_files(tmp_path)

    def test_the_walk_on_request_does_not_ask_git(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        _touch(tmp_path, "a.py")
        monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
        assert _rels(tmp_path, iter_files(tmp_path, use_git=False)) == ["a.py"]

    def test_a_plain_directory_is_still_walked(self, tmp_path, monkeypatch):
        _touch(tmp_path, "a.py")
        monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
        assert _rels(tmp_path, iter_files(tmp_path)) == ["a.py"]


class TestRelativePosixShortcut:
    """The string shortcut of ``relative_posix`` answers exactly what ``Path.relative_to`` answers (audit 48-F3)."""

    CASES = (
        ("C:/r", "C:/r/a/b.py"),
        ("C:\r", "C:\r\a\b.py"),
        ("C:/r", "C:\r\a\b.py"),
        ("r", "r/a.py"),
        (".", "./a.py"),
        ("./r", "./r/a.py"),
        ("/srv/r", "/srv/r/a/b.py"),
        ("C:/r", "C:/r/./a.py"),
        ("C:/r", "C:/r/../a.py"),
        ("C:/r", "C:/r//a.py"),
        ("C:/r/", "C:/r/a.py"),
        ("C:/r", "C:/rx/a.py"),
        ("C:/r", "c:/R/a.py"),
        ("C:/r", "C:/r/D:/a.py"),
        ("C:", "C:/a.py"),
        ("//srv/share", "//srv/share/a.py"),
        ("C:/r", "C:/r"),
    )

    @pytest.mark.parametrize(("root", "path"), CASES)
    def test_agrees_with_path_relative_to(self, root, path):
        from py_ci_shared._core.corpus import _prefix_relative

        quick = _prefix_relative(path, root)
        if quick is None:
            return  # the slow path decides: nothing to compare
        assert quick == Path(path).relative_to(Path(root)).as_posix()

    def test_the_common_spelling_takes_the_shortcut_and_odd_ones_do_not(self):
        from py_ci_shared._core.corpus import _prefix_relative

        assert _prefix_relative("C:/r/a/b.py", "C:/r") == "a/b.py"
        for root, path in [("C:/r", "C:/r/../a.py"), ("C:/r/", "C:/r/a.py"), ("C:/r", "C:/r/D:/a.py"), ("C:/r", "C:/rx/a.py"), ("C:/r", "C:/r/./a.py")]:
            assert _prefix_relative(path, root) is None

    def test_a_path_outside_the_root_and_a_pathlike_still_work(self, tmp_path):
        from py_ci_shared._core.corpus import relative_posix

        assert relative_posix(tmp_path / "a" / "b.py", tmp_path) == "a/b.py"
        assert relative_posix(str(tmp_path / "a" / "b.py"), tmp_path) == "a/b.py"
        assert relative_posix(tmp_path / "a.py", tmp_path / "sub") == (tmp_path / "a.py").as_posix()
