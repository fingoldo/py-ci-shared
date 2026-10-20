"""A [tool.py_ci_shared] corpus entry must stay inside the repository root."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core.config import ConfigError, _expand


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "m.py").write_text("X = 1\n", encoding="utf-8")
    return root


def test_a_relative_entry_inside_the_root_is_kept(tmp_path):
    root = _repo(tmp_path)

    assert _expand(root, "src/m.py") == [root / "src/m.py"]


def test_a_glob_inside_the_root_is_kept(tmp_path):
    root = _repo(tmp_path)

    assert [p.name for p in _expand(root, "src/*.py")] == ["m.py"]


@pytest.mark.parametrize("item", ["../outside.py", "src/../../outside.py"])
def test_a_parent_climbing_entry_is_rejected(tmp_path, item):
    root = _repo(tmp_path)

    with pytest.raises(ConfigError, match="outside the repository root"):
        _expand(root, item)


def test_an_absolute_entry_outside_the_root_is_rejected(tmp_path):
    root = _repo(tmp_path)
    elsewhere = tmp_path / "elsewhere.py"
    elsewhere.write_text("Y = 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="outside the repository root"):
        _expand(root, str(elsewhere))


def test_a_glob_that_climbs_out_of_the_root_is_rejected(tmp_path):
    root = _repo(tmp_path)
    (tmp_path / "other.py").write_text("Z = 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="outside the repository root"):
        _expand(root, "../*.py")
