"""The api_floor result cache: a file whose bytes, floor and vermin version were analysed before is not analysed again."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from py_ci_shared import api_floor


@pytest.fixture
def fake_vermin(tmp_path, monkeypatch):
    """Redirect the cache to a throwaway home and replace the vermin subprocess by a recorder."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(api_floor, "_vermin_version", lambda: "9.9")
    calls: list[list[str]] = []
    state = {"complete": True}

    def raw(paths, target):
        calls.append(list(paths))
        by_path = {os.path.normcase(os.path.normpath(p)): [] for p in paths}
        for p in paths:
            if Path(p).read_text(encoding="utf-8").startswith("NEW"):
                by_path[os.path.normcase(os.path.normpath(p))].append(f"{p}:3:1:False:3.10:'str.removeprefix' member")
        return by_path, state["complete"]

    monkeypatch.setattr(api_floor, "_run_vermin_raw", raw)
    return calls, state


def _source(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_a_second_run_over_unchanged_files_does_not_start_vermin(tmp_path, fake_vermin):
    calls, _ = fake_vermin
    clean = _source(tmp_path, "clean.py", "x = 1\n")
    newer = _source(tmp_path, "newer.py", "NEW api used here\n")

    first = api_floor._run_vermin([clean, newer], "3.9")
    second = api_floor._run_vermin([clean, newer], "3.9")

    assert len(calls) == 1, "the second run must come from the cache"
    assert second == first and [h[3] for h in first] == ["'str.removeprefix' member"]


def test_an_edited_file_is_analysed_again_and_its_neighbours_are_not(tmp_path, fake_vermin):
    calls, _ = fake_vermin
    stable = _source(tmp_path, "stable.py", "x = 1\n")
    edited = _source(tmp_path, "edited.py", "y = 1\n")
    api_floor._run_vermin([stable, edited], "3.9")

    Path(edited).write_text("NEW api now\n", encoding="utf-8")
    hits = api_floor._run_vermin([stable, edited], "3.9")

    assert calls[-1] == [edited], "only the changed file is sent to vermin"
    assert [h[3] for h in hits] == ["'str.removeprefix' member"]


def test_a_different_floor_is_a_different_cache_entry(tmp_path, fake_vermin):
    calls, _ = fake_vermin
    source = _source(tmp_path, "m.py", "x = 1\n")

    api_floor._run_vermin([source], "3.9")
    api_floor._run_vermin([source], "3.10")

    assert len(calls) == 2


def test_a_split_that_could_not_be_trusted_is_not_cached(tmp_path, fake_vermin):
    """When vermin printed a path that matched none of the inputs, a per-file empty result could hide a violation."""
    calls, state = fake_vermin
    state["complete"] = False
    source = _source(tmp_path, "m.py", "NEW api\n")

    api_floor._run_vermin([source], "3.9")
    api_floor._run_vermin([source], "3.9")

    assert len(calls) == 2


def test_a_corrupt_cache_entry_is_a_miss(tmp_path, fake_vermin):
    calls, _ = fake_vermin
    source = _source(tmp_path, "m.py", "x = 1\n")
    api_floor._run_vermin([source], "3.9")
    for entry in (tmp_path / "home").rglob("*.json"):
        entry.write_text("not json", encoding="utf-8")

    api_floor._run_vermin([source], "3.9")

    assert len(calls) == 2


def test_vermin_summary_lines_do_not_make_the_split_untrusted(tmp_path, monkeypatch):
    """vermin ends its output with lines such as ":::!2:3.12:", which name no file; they must not count as a stray path."""
    source = tmp_path / "m.py"
    source.write_text("x = 1" + chr(10), encoding="utf-8")
    out = f"{source}:7:1:False:3.12:'a' member" + chr(10) + ":::!2:3.12:" + chr(10)

    monkeypatch.setattr(api_floor.subprocess, "run", lambda cmd, **kw: __import__("subprocess").CompletedProcess(cmd, 1, out, ""))

    by_path, complete = api_floor._run_vermin_raw([str(source)], "3.9")

    assert complete is True
    assert list(by_path.values()) == [[f"{source}:7:1:False:3.12:'a' member"]]


def test_two_files_with_identical_bytes_each_report_under_their_own_path(tmp_path, fake_vermin):
    """The cache key is the file's bytes, so the stored result must not carry the first file's path."""
    first = _source(tmp_path, "first.py", "NEW api")
    second = _source(tmp_path, "second.py", "NEW api")

    api_floor._run_vermin([first], "3.9")
    hits = api_floor._run_vermin([second], "3.9")

    assert [h[0] for h in hits] == [second]
