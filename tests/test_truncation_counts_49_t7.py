"""49-T7: a capped list says how many entries it left out, and the unreadable-file error is no exception."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared import tracked_secret_shapes as tss
from py_ci_shared._core import CorpusError

ONE = {"demo": {"pattern": "SECRET"}}


def _repo(tmp_path: Path, names: list[str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name in names:
        (tmp_path / name).write_bytes(b"x\n")
    return tmp_path


def test_unreadable_list_says_how_many_it_left_out(tmp_path: Path) -> None:
    names = [f"f{i:02d}.bin" for i in range(25)]
    repo = _repo(tmp_path, [])
    with pytest.raises(CorpusError) as info:
        tss.scan_tracked_secret_shapes(repo, shapes=ONE, files=names, min_files=0)
    message = str(info.value)
    assert "f19.bin" in message
    assert "f20.bin" not in message
    assert "... and 5 more" in message
