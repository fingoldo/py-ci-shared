"""The resolution probe copies the repository; a symlink in it must stay a link, not turn into the target's files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from py_ci_shared import checkout_resolution


def test_a_repo_symlink_to_a_directory_outside_the_repo_is_not_copied_as_files(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("private", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    try:
        os.symlink(outside, repo / "docs", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here ({type(exc).__name__}); needs developer mode or admin on Windows")
    workdir = tmp_path / "work"
    workdir.mkdir()

    checkout_resolution._run_probe_in_a_copy(repo, "pkg", workdir, timeout=120, pytest_args=())

    copied = Path(workdir / "copy" / "docs")
    assert copied.is_symlink(), "the link must be copied as a link, not as the target's files"
