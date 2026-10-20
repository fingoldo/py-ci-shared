"""A symlink in a corpus is never read as a corpus file: its target may sit outside the checkout."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from py_ci_shared._core.corpus import iter_files


def _link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink on this machine ({type(exc).__name__}); needs developer mode or admin on Windows")


def test_a_symlink_to_a_file_outside_the_root_is_not_part_of_the_corpus(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 1\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "real.py").write_text("X = 1\n", encoding="utf-8")
    _link(root / "linked.py", outside)

    found = [p.name for p in iter_files(root, ["*.py"], use_git=False)]

    assert found == ["real.py"]


def test_regular_files_are_still_found(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("B = 1\n", encoding="utf-8")

    assert [p.name for p in iter_files(tmp_path, ["*.py"], use_git=False)] == ["a.py", "b.py"]
