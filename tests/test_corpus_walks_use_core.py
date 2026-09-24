"""Gates that walked or parsed files on their own now go through ``_core``: one skip set, and unparsable files are loud."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared import (
    _mutation_fingerprint,
    effect_assertion_parity,
    llm_call_archive_gate,
    repo_hygiene,
    test_partition_reachability,
    timezone_honest,
)
from py_ci_shared._core import DEFAULT_EXCLUDE, UnparsedFilesError


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def test_the_local_skip_sets_are_the_canonical_one():
    assert effect_assertion_parity._SKIP_DIRS is DEFAULT_EXCLUDE
    assert llm_call_archive_gate._SKIP_DIRS is DEFAULT_EXCLUDE


def test_llm_finders_raise_on_an_unparsable_file_instead_of_skipping_it(tmp_path):
    _write(tmp_path / "src" / "ok.py", "client.messages.create(x=1)\n")
    assert llm_call_archive_gate.find_direct_sdk_calls(tmp_path, ["src"]) != {}
    _write(tmp_path / "src" / "broken.py", "def broken(:\n")
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        llm_call_archive_gate.find_direct_sdk_calls(tmp_path, ["src"])


def test_llm_python_files_skip_every_default_excluded_dir(tmp_path):
    _write(tmp_path / "src" / "a.py", "x = 1\n")
    _write(tmp_path / "src" / "site-packages" / "b.py", "x = 1\n")
    _write(tmp_path / "src" / ".claude" / "c.py", "x = 1\n")
    assert [p.name for p in llm_call_archive_gate.python_files(tmp_path, ["src"])] == ["a.py"]


def test_permanent_skips_ignore_node_modules(tmp_path):
    spec = "test.skip('x', () => {})\n"
    _write(tmp_path / "e2e" / "a.spec.ts", spec)
    _write(tmp_path / "e2e" / "node_modules" / "lib" / "b.spec.ts", spec)
    found = test_partition_reachability.find_permanent_skips([tmp_path / "e2e"])
    assert len(found) == 1 and found[0].startswith("a.spec.ts:1:")


def test_unreferenced_scripts_skip_caches_and_keep_real_scripts(tmp_path):
    _write(tmp_path / "scripts" / "run_me.py", "")
    _write(tmp_path / "scripts" / "__pycache__" / "stale.py", "")
    assert test_partition_reachability.find_unreferenced_scripts([tmp_path / "scripts"], "") == ["scripts/run_me.py"]
    assert test_partition_reachability.find_unreferenced_scripts([tmp_path / "scripts"], "run_me.py") == []


def test_excluded_code_dirs_ignore_bytecode_only_dirs(tmp_path):
    _write(tmp_path / "pyproject.toml", '[tool.ruff]\nexclude = ["legacy", "cacheonly"]\n')
    _write(tmp_path / "legacy" / "old.py", "x = 1\n")
    _write(tmp_path / "cacheonly" / "__pycache__" / "old.py", "")
    assert timezone_honest.excluded_code_dirs(tmp_path) == ["legacy"]


def test_repo_hygiene_walk_outside_git_prunes_skipped_dirs(tmp_path):
    _write(tmp_path / "a.md", "﻿bom\n")
    _write(tmp_path / "node_modules" / "b.md", "﻿bom\n")
    assert repo_hygiene.find_text_files_with_a_bom(tmp_path) == ["a.md"]


def test_fingerprint_expands_a_test_dir_like_pytest(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "m.py", "x = 1\n")
    _write(tmp_path / "tests" / "test_a.py", "def test_a():\n    pass\n")
    _write(tmp_path / "tests" / "helper.py", "y = 1\n")
    _write(tmp_path / "tests" / "sub" / "conftest.py", "")
    before = _mutation_fingerprint.fingerprint(tmp_path, "pkg/m.py", ["tests"])
    _write(tmp_path / "tests" / "helper.py", "y = 2\n")
    assert _mutation_fingerprint.fingerprint(tmp_path, "pkg/m.py", ["tests"]) == before
    _write(tmp_path / "tests" / "sub" / "conftest.py", "z = 1\n")
    after_conftest = _mutation_fingerprint.fingerprint(tmp_path, "pkg/m.py", ["tests"])
    assert after_conftest != before
    _write(tmp_path / "tests" / "venv" / "test_vendored.py", "def test_v():\n    pass\n")
    assert _mutation_fingerprint.fingerprint(tmp_path, "pkg/m.py", ["tests"]) == after_conftest
    _write(tmp_path / "tests" / "test_a.py", "def test_a():\n    assert 1\n")
    assert _mutation_fingerprint.fingerprint(tmp_path, "pkg/m.py", ["tests"]) != after_conftest
