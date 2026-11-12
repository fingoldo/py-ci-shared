"""The sandbox's bytecode cache: a nested run reuses compiled modules, and the mutated file is never read from a stale entry."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from py_ci_shared import _mutation_runner as runner


def _import_value(sandbox: Path) -> str:
    """Import ``m`` in a fresh interpreter the way a nested run does, and print ``m.f()``."""
    done = subprocess.run(
        [sys.executable, "-c", "import m; print(m.f())"],
        cwd=sandbox,
        env={**runner._pytest_env(runner.pyc_cache_dir(sandbox)), "PYTHONPATH": str(sandbox)},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return done.stdout.strip()


def _rewrite_same_size_same_mtime(path: Path, text: str) -> None:
    """What two operator swaps of equal length written within one second look like to the import system."""
    before = path.stat()
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_env_without_a_cache_directory_writes_no_bytecode():
    env = runner._pytest_env()
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_env_with_a_cache_directory_caches_there_and_does_not_forbid_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    env = runner._pytest_env(tmp_path / "cache")
    assert env["PYTHONPYCACHEPREFIX"] == str(tmp_path / "cache")
    assert "PYTHONDONTWRITEBYTECODE" not in env


def test_the_cache_sits_beside_the_sandbox_not_inside_it(tmp_path):
    sandbox = tmp_path / "repo"
    cache = runner.pyc_cache_dir(sandbox)
    assert cache.parent == tmp_path
    assert sandbox not in cache.parents


def test_a_stale_entry_is_served_without_forget_and_never_after_it(tmp_path):
    sandbox = tmp_path / "repo"
    sandbox.mkdir()
    target = sandbox / "m.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert _import_value(sandbox) == "1"
    assert list(runner.pyc_cache_dir(sandbox).rglob("m.*.pyc")), "the first run must have cached the module, or this test measures nothing"

    _rewrite_same_size_same_mtime(target, "def f():\n    return 2\n")
    assert _import_value(sandbox) == "1", "same size and same mtime: the import system trusts the cache, which is the hazard forget_bytecode closes"

    runner.forget_bytecode(sandbox, target)
    assert _import_value(sandbox) == "2"


def test_forget_bytecode_leaves_other_modules_cached(tmp_path):
    sandbox = tmp_path / "repo"
    sandbox.mkdir()
    (sandbox / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (sandbox / "other.py").write_text("X = 1\n", encoding="utf-8")
    cache = runner.pyc_cache_dir(sandbox)
    subprocess.run(
        [sys.executable, "-c", "import m, other"],
        cwd=sandbox,
        env={**runner._pytest_env(cache), "PYTHONPATH": str(sandbox)},
        check=True,
        timeout=120,
    )
    assert list(cache.rglob("other.*.pyc"))
    runner.forget_bytecode(sandbox, sandbox / "m.py")
    assert not list(cache.rglob("m.*.pyc"))
    assert list(cache.rglob("other.*.pyc")), "only the mutated file's bytecode is dropped; the rest of the sandbox keeps its cache"


def test_forget_bytecode_without_a_cache_directory_is_a_no_op(tmp_path):
    runner.forget_bytecode(tmp_path / "repo", tmp_path / "repo" / "m.py")
