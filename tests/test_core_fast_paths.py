"""The parse cache's stat fast path, the resolve cache and the cached module walk keep what the slow paths guarantee."""

from __future__ import annotations

import ast
import os
import time
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache, parse_source
from py_ci_shared._core import node_index as ni
from py_ci_shared._core import source as src


@pytest.fixture(autouse=True)
def _fresh():
    clear_parse_cache()
    yield
    clear_parse_cache()


def _age(path: Path, seconds: int) -> None:
    """Move the file's mtime into the past, as a file left untouched for a while has."""
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_a_stable_old_file_is_served_without_reading_it_again(tmp_path, monkeypatch):
    """Once a file's stat signature is older than the racy window, a repeat request does not read or hash it."""
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    _age(f, 60)
    first = parse_source(f)
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    again = parse_source(f)
    assert again[1] is first[1] and not reads


def test_a_same_size_rewrite_inside_the_racy_window_is_reparsed(tmp_path):
    """A file rewritten right after it was read has a fresh mtime, so the stat shortcut is not trusted and the digest decides."""
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    old = parse_source(f)[1]
    f.write_text("x = 2\n", encoding="utf-8")  # same size, same tick on a coarse clock
    new = parse_source(f)[1]
    assert new is not old and ast.literal_eval(new.body[0].value) == 2


def test_a_rewrite_of_an_old_file_that_changes_size_or_mtime_is_reparsed(tmp_path):
    """An aged entry whose file then changes (new size, new mtime) falls back to the digest and returns the new tree."""
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    _age(f, 60)
    old = parse_source(f)[1]
    f.write_text("x = 22\n", encoding="utf-8")
    assert ast.literal_eval(parse_source(f)[1].body[0].value) == 22
    assert parse_source(f)[1] is not old


def test_a_deleted_file_is_an_error_not_a_stale_hit(tmp_path):
    """A cached file that disappears raises the typed read error instead of returning its old tree."""
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    _age(f, 60)
    parse_source(f)
    f.unlink()
    with pytest.raises(src.SourceReadError):
        parse_source(f)


def test_resolve_cached_follows_the_working_directory_for_relative_paths(tmp_path, monkeypatch):
    """A relative path is resolved against the current directory, so the same spelling in two directories is two entries."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    monkeypatch.chdir(tmp_path / "a")
    first = src.resolve_cached("f.py")
    monkeypatch.chdir(tmp_path / "b")
    second = src.resolve_cached("f.py")
    assert first == (tmp_path / "a" / "f.py").resolve() and second == (tmp_path / "b" / "f.py").resolve()


def test_cached_module_walk_equals_ast_walk_and_does_not_pin_the_tree():
    """``walk`` of a module replays the stdlib order from the second call on, and the memo is freed with the tree."""
    import gc
    import weakref

    tree = ast.parse("def f(a):\n    return [x for x in a if x]\n\nclass C:\n    y = 1\n")
    expected = list(ast.walk(tree))
    assert list(ni.walk(tree)) == expected and list(ni.walk(tree)) == expected
    sub = tree.body[0]
    assert list(ni.walk(sub)) == list(ast.walk(sub))
    ref = weakref.ref(tree)
    del tree, sub, expected
    gc.collect()
    assert ref() is None
