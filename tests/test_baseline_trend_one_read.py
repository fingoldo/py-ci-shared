"""baseline_trend reads every version of a baseline in one git process, not one ``git show`` per commit (audit 48-F12)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from py_ci_shared import baseline_trend
from py_ci_shared._core import git as core_git
from py_ci_shared._core.git import git_env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=git_env()).stdout


@pytest.fixture
def repo_with_three_baseline_versions(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    (repo / "baselines").mkdir()
    for n, entries in enumerate([{"a": "x"}, {"a": "x", "b": "y"}, {"a": "x", "b": "y", "c": "z"}], start=1):
        (repo / "baselines" / "rule.json").write_text(json.dumps({"accepted": entries}), encoding="utf-8")
        _git(repo, "add", "baselines/rule.json")
        _git(repo, "commit", "-q", "-m", f"v{n}")
    return repo


def test_three_versions_are_counted_with_one_cat_file_and_no_show(repo_with_three_baseline_versions, monkeypatch) -> None:
    calls: list[str] = []
    real = core_git.run_git

    def counting(repo, *args, **kwargs):
        calls.append(args[0])
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(baseline_trend, "run_git", counting)
    monkeypatch.setattr(core_git, "run_git", counting)
    points = baseline_trend.history(str(repo_with_three_baseline_versions), "baselines/rule.json", None)
    assert [count for _date, _sha, count in points] == [1, 2, 3]
    assert calls.count("show") == 0, f"git show was run {calls.count('show')} times"
    assert calls.count("cat-file") == 1


def test_a_missing_blob_is_skipped_not_counted(repo_with_three_baseline_versions) -> None:
    """The batch parser must keep its place when an object is missing: a bad spec yields None and does not shift the rest."""
    text = baseline_trend._blobs(str(repo_with_three_baseline_versions), ["HEAD:baselines/rule.json", "HEAD:no/such/file"])
    assert text[0] is not None and b"accepted" in text[0]
    assert text[1] is None
