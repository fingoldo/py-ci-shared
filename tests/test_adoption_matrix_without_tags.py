"""A --resolve-in checkout with no release tags must fail the run, not let the stale-pin check pass silently (audit 47-F14)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from py_ci_shared._core.git import git_env
from py_ci_shared.adoption_matrix import main


def _untagged_checkout(tmp_path: Path) -> Path:
    pcs = tmp_path / "pcs"
    pcs.mkdir()
    git = ["git", "-C", str(pcs), "-c", "user.email=t@e", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True, capture_output=True, env=git_env())
    (pcs / "README.md").write_text("no tags here\n", encoding="utf-8")
    subprocess.run([*git, "add", "README.md"], check=True, capture_output=True, env=git_env())
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True, capture_output=True, env=git_env())
    return pcs


def _consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "requirements.txt").write_text("requests==2.0\n", encoding="utf-8")
    return root


def test_a_resolver_without_release_tags_fails_the_run(tmp_path: Path, capsys) -> None:
    pcs = _untagged_checkout(tmp_path)
    consumer = _consumer(tmp_path)
    assert main([str(consumer), "--no-local-copies", "--resolve-in", str(pcs)]) == 1
    assert "has no vX.Y.Z release tags" in capsys.readouterr().err


def test_advisory_mode_reports_it_without_failing(tmp_path: Path, capsys) -> None:
    pcs = _untagged_checkout(tmp_path)
    consumer = _consumer(tmp_path)
    main([str(consumer), "--no-local-copies", "--resolve-in", str(pcs), "--advisory"])
    assert "has no vX.Y.Z release tags" not in capsys.readouterr().err
