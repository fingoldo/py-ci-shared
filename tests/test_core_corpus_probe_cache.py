"""git_listing remembers that a root is a usable work tree, but never remembers the file listing itself."""

from __future__ import annotations

import subprocess

import pytest

from py_ci_shared._core import corpus
from py_ci_shared._core.corpus import clear_listing_cache, git_listing
from py_ci_shared._core.git import git_env

pytestmark = pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0, reason="git not installed")


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=git_env())


@pytest.fixture
def repo(tmp_path):
    clear_listing_cache()
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("A = 1" + chr(10), encoding="utf-8")
    yield tmp_path
    clear_listing_cache()


def test_the_work_tree_probes_run_once_per_root(repo, monkeypatch):
    seen: list[tuple[str, ...]] = []
    real = corpus._git

    def counting(root, *args):
        seen.append(args)
        return real(root, *args)

    monkeypatch.setattr(corpus, "_git", counting)

    for _ in range(3):
        git_listing(repo)

    assert [a[0] for a in seen].count("rev-parse") == 1 and [a[0] for a in seen].count("check-ignore") == 1
    assert [a[0] for a in seen].count("ls-files") == 3, "the file listing itself is read fresh every time"


def test_a_file_added_between_listings_is_seen(repo):
    first = git_listing(repo)

    (repo / "b.py").write_text("B = 1" + chr(10), encoding="utf-8")
    second = git_listing(repo)

    assert first == ["a.py"] and second == ["a.py", "b.py"]


def test_a_directory_that_becomes_a_repository_later_is_noticed(tmp_path):
    clear_listing_cache()
    (tmp_path / "a.py").write_text("A = 1" + chr(10), encoding="utf-8")
    inside_a_plain_directory = git_listing(tmp_path)

    _git(tmp_path, "init", "-q")
    after_init = git_listing(tmp_path)
    clear_listing_cache()

    assert inside_a_plain_directory is None and after_init == ["a.py"]


def test_clearing_the_cache_makes_the_next_listing_probe_again(repo, monkeypatch):
    seen: list[str] = []
    real = corpus._git

    def counting(root, *args):
        seen.append(args[0])
        return real(root, *args)

    monkeypatch.setattr(corpus, "_git", counting)
    git_listing(repo)
    clear_listing_cache()
    git_listing(repo)

    assert seen.count("rev-parse") == 2
