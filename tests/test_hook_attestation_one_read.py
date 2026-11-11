"""find_hook_attestation reads the committed hook config of a whole range in one git process, not one ``git show`` per commit (audit 48-F13)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared import hook_attestation
from py_ci_shared._core import git as core_git
from py_ci_shared._core.git import git_env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=git_env()).stdout


@pytest.fixture
def repo_three_commits(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    for n in range(3):
        (repo / hook_attestation.CONFIG).write_text(f"repos: []  # {n}\n", encoding="utf-8")
        _git(repo, "add", hook_attestation.CONFIG)
        _git(repo, "commit", "-q", "-m", f"change {n}")
    return repo


def test_the_range_is_read_with_one_cat_file_and_no_show(repo_three_commits, monkeypatch) -> None:
    """Negative control (48-F13): the old per-commit reads ran `git show` three times for this range."""
    seen: list[str] = []
    real = core_git.run_git

    def counting(repo, *args, **kwargs):
        seen.append(args[0])
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(hook_attestation, "run_git", counting)
    monkeypatch.setattr(core_git, "run_git", counting)
    hook_attestation.find_hook_attestation(repo_three_commits, "HEAD~2..HEAD")
    assert seen.count("show") == 0, f"git show ran {seen.count('show')} times"
    assert seen.count("cat-file") == 1


def test_the_hashes_match_the_config_each_commit_carries(repo_three_commits) -> None:
    commits = hook_attestation.commits_in(repo_three_commits, "HEAD~2..HEAD")
    hashes = hook_attestation._config_hashes(repo_three_commits, [c.sha for c in commits])
    for commit in commits:
        blob = subprocess.run(
            ["git", "-C", str(repo_three_commits), "show", f"{commit.sha}:{hook_attestation.CONFIG}"],
            capture_output=True,
            check=True,
            env=git_env(),
        ).stdout  # bytes: text mode would turn a Windows CRLF into LF and change the hash
        assert hashes[commit.sha] == hook_attestation.config_hash(blob)
