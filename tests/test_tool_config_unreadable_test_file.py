"""A test file that cannot be decoded is reported as unparsed, not skipped as if it did not shell out (audit 40-F3)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from py_ci_shared._core.errors import UnparsedFilesError
from py_ci_shared.tool_config_env_vars_set_in_every_tool_job import find_tool_config_env_vars_set_in_every_tool_job

_PATH = Path(__file__).resolve().parent / "test_tool_config_env_vars_set_in_every_tool_job.py"
_SPEC = importlib.util.spec_from_file_location("tool_config_builder", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
builder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(builder)


def _repo_with_undecodable_test(tmp_path: Path) -> Path:
    root = builder._repo(tmp_path, tests={})
    bad = root / "tests" / "test_meta" / "test_bad.py"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\xff\xfe\x00 not utf-8 \xff\n")
    return root


def test_an_undecodable_test_file_fails_the_run_by_name(tmp_path: Path) -> None:
    """Negative control (40-F3): the old gate skipped the file and reported nothing for it, so this call returned without raising."""
    root = _repo_with_undecodable_test(tmp_path)
    with pytest.raises(UnparsedFilesError, match=r"test_bad\.py"):
        find_tool_config_env_vars_set_in_every_tool_job(root, use_git=False)


def test_allow_unparsed_lets_the_run_continue(tmp_path: Path) -> None:
    root = _repo_with_undecodable_test(tmp_path)
    find_tool_config_env_vars_set_in_every_tool_job(root, use_git=False, allow_unparsed=True)
