"""35-F3: opens are counted per gate, and the shared readers open each file at most twice however many gates and rounds read it."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from py_ci_shared import repo_hygiene
from py_ci_shared._core import source
from py_ci_shared._core.open_audit import count_opens

_MAX_OPENS_PER_FILE = 2


@pytest.fixture
def aged_tree(tmp_path: Path) -> Path:
    """Files old enough for the read caches to trust (a file modified in the last seconds is deliberately re-read)."""
    old = time.time() - 3600
    for i in range(12):
        f = tmp_path / f"m{i}.py"
        f.write_text("def f():\n    return 1\n", encoding="utf-8")
        os.utime(f, (old, old))
    return tmp_path


def test_every_open_is_counted_and_named_after_the_module_that_caused_it(tmp_path: Path):
    inside_dir = tmp_path / "in"
    inside_dir.mkdir()
    target = inside_dir / "a.txt"
    target.write_text("x", encoding="utf-8")
    with count_opens(str(inside_dir)) as counts:
        target.read_text(encoding="utf-8")
        target.read_bytes()
        with open(target, encoding="utf-8"):
            pass
        (tmp_path / "elsewhere.txt").write_text("y", encoding="utf-8")  # outside the counted directory
    assert sum(counts.by_file.values()) == 3 and len(counts.by_file) == 1
    assert dict(counts.by_gate()) == {"<outside the package>": 3}
    with count_opens(str(inside_dir)) as gated:
        repo_hygiene.find_text_files_with_nul_bytes(inside_dir)
    assert set(gated.by_gate()) == {"py_ci_shared.repo_hygiene"}, "the gate is the py_ci_shared module, not the _core reader beneath it"


def test_the_shared_readers_open_each_file_at_most_twice_over_many_rounds(aged_tree: Path):
    files = sorted(aged_tree.glob("*.py"))
    with count_opens(str(aged_tree)) as counts:
        for _ in range(4):
            repo_hygiene.find_text_files_with_nul_bytes(aged_tree)
            repo_hygiene.find_text_files_with_a_bom(aged_tree)
            for f in files:
                source.read_source(f)
                source.parse_file(f)
    worst, opens = counts.max_per_file()
    assert len(counts.by_file) == len(files)
    assert opens <= _MAX_OPENS_PER_FILE, f"{worst} was opened {opens} times:\n{counts.report()}"


def test_a_reader_that_bypasses_the_cache_is_caught_by_the_cap(aged_tree: Path):
    files = sorted(aged_tree.glob("*.py"))
    with count_opens(str(aged_tree)) as counts:
        for _ in range(4):
            for f in files:
                f.read_bytes()
    assert counts.max_per_file()[1] == 4 > _MAX_OPENS_PER_FILE
