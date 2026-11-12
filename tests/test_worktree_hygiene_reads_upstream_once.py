"""The upstream tree and object format are read once per report, not once per linked worktree (audit 48-F18)."""

from __future__ import annotations

import subprocess

import pytest

from py_ci_shared import worktree_hygiene
from py_ci_shared._core.git import git_env


def _git(repo, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=git_env()).stdout


@pytest.fixture
def clone_with_three_worktrees(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "master")
    (seed / "kept.txt").write_text("upstream content\n", encoding="utf-8")
    _git(seed, "add", "kept.txt")
    _git(seed, "commit", "-q", "-m", "seed")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True, capture_output=True, env=git_env())
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True, capture_output=True, env=git_env())
    _git(clone, "remote", "set-head", "origin", "master")
    for name in ("wt_a", "wt_b", "wt_c"):
        _git(clone, "worktree", "add", "-q", "--detach", str(tmp_path / name), "origin/master")
    return clone


def test_the_upstream_tree_is_listed_once_for_three_worktrees(clone_with_three_worktrees, monkeypatch) -> None:
    calls = []
    real = worktree_hygiene._upstream_blobs

    def counting(repo, ref):
        calls.append(ref)
        return real(repo, ref)

    monkeypatch.setattr(worktree_hygiene, "_upstream_blobs", counting)
    findings = worktree_hygiene.worktree_findings(clone_with_three_worktrees)
    assert len([f for f in findings if f.registered]) == 3
    assert len(calls) == 1, f"the upstream tree was listed {len(calls)} times for 3 worktrees"


def test_the_verdicts_are_unchanged_by_reading_once(clone_with_three_worktrees) -> None:
    findings = worktree_hygiene.worktree_findings(clone_with_three_worktrees)
    assert {f.path.name: f.verdict for f in findings if f.registered} == {"wt_a": "REMOVABLE", "wt_b": "REMOVABLE", "wt_c": "REMOVABLE"}
