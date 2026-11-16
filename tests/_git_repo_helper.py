"""One way to make a git repository in a test: three git processes (init, add, commit), no ``git config``.

The commit identity comes from the session's GIT_ISOLATION environment (tests/conftest.py), so a repository needs no
``git config`` spawns. Helpers that used to run two of those per repository now call :func:`make_repo` (audit 48-F15).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Optional

from py_ci_shared._core.git import git_env


def _git(repo: Path, *args: str, env: Optional[Mapping[str, str]] = None) -> None:
    merged = git_env(env)  # drops GIT_DIR / GIT_INDEX_FILE etc., so a hook that exports them cannot retarget the fixture
    subprocess.run(["git", *args], cwd=repo, env=merged, capture_output=True, text=True, check=True, timeout=60)


def make_repo(
    root: Path,
    files: Optional[Mapping[str, str]] = None,
    *,
    commit_env: Optional[Mapping[str, str]] = None,
    init_args: Sequence[str] = (),
    tags: Sequence[str] = (),
) -> Path:
    """An initialised repository at *root* with one commit holding *files* (relative path to text, written as UTF-8).

    *commit_env* adds variables for the commit only (a commit date, say). *tags* point at that commit."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", *init_args)
    for rel, text in (files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial", env=commit_env)
    for tag in tags:
        _git(root, "tag", tag)
    return root
