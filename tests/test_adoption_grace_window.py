"""47-T1: a fixed pin lagging more releases than allowed is stale only once the first release it lacks has been out for
``grace_days`` (default 0: the release-count rule alone, as before)."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from py_ci_shared._core.git import git_env
from py_ci_shared.adoption_matrix import RefResolver, RepoReport, find_pins

URL = "git+https://github.com/fingoldo/py-ci-shared.git"
SHA = "a" * 40
TODAY = date(2026, 11, 16)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=git_env())
    return proc.stdout.strip()


def _report(tmp_path: Path, *, grace_days: int, since: date | None) -> RepoReport:
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "requirements-dev.txt").write_bytes(f"py-ci-shared @ {URL}@{SHA}\n".encode())
    pins = find_pins(root)
    ref = pins[0].ref
    return RepoReport(
        name="consumer",
        root=root,
        pins=pins,
        allow_behind=2,
        behind={ref: (3, "v1.23.11")},
        grace_days=grace_days,
        lacking_since={} if since is None else {ref: since},
        today=TODAY,
    )


def test_within_the_grace_window_a_lagging_pin_is_not_stale(tmp_path):
    assert _report(tmp_path, grace_days=7, since=date(2026, 11, 14)).stale_pins == []


def test_once_the_first_missing_release_is_grace_days_old_the_pin_is_stale(tmp_path):
    assert len(_report(tmp_path, grace_days=7, since=date(2026, 11, 9)).stale_pins) == 1


def test_grace_zero_keeps_the_release_count_rule(tmp_path):
    assert len(_report(tmp_path, grace_days=0, since=TODAY).stale_pins) == 1


def test_an_unknown_release_date_counts_as_past_the_grace(tmp_path):
    assert len(_report(tmp_path, grace_days=7, since=None).stale_pins) == 1


def test_first_lacking_date_is_the_date_of_the_first_release_the_commit_lacks(tmp_path):
    repo = tmp_path / "pcs"
    repo.mkdir()
    _git(repo, "init", "-q")
    shas = {}
    for name in ("early", "released", "late"):
        _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", name)
        shas[name] = _git(repo, "rev-parse", "HEAD")
        if name == "released":
            _git(repo, "tag", "-a", "-m", "release", "v1.2.3")
    resolver = RefResolver(repo)
    assert isinstance(resolver.first_lacking_date(shas["early"]), date)
    assert resolver.first_lacking_date(shas["late"]) is None
