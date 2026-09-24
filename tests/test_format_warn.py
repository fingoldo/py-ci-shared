"""`format_warn` splits a long file list so no command line exceeds the Windows limit, and still checks every file."""

from __future__ import annotations

import subprocess
import sys

from py_ci_shared import format_warn


def test_chunks_respect_the_limit_and_keep_every_file_in_order():
    files = [f"pkg/module_{i:04d}.py" for i in range(2000)]
    chunks = format_warn._chunks(files, limit=8000)
    assert len(chunks) > 1
    assert [f for c in chunks for f in c] == files
    assert all(sum(len(f) + 1 for f in c) <= 8000 for c in chunks)


def test_a_short_list_is_one_chunk_and_an_oversized_name_goes_alone():
    assert format_warn._chunks(["a.py", "b.py"], limit=100) == [["a.py", "b.py"]]
    long_name = "x" * 150 + ".py"
    assert format_warn._chunks(["a.py", long_name, "b.py"], limit=100) == [["a.py"], [long_name], ["b.py"]]


def test_main_runs_every_tool_on_every_chunk_within_the_command_limit(monkeypatch):
    files = [f"pkg/module_{i:04d}.py" for i in range(2000)]
    calls: list[list[str]] = []

    def record(cmd, *args, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(format_warn.subprocess, "run", record)
    monkeypatch.setattr(sys, "argv", ["format_warn", *files, "README.md"])
    format_warn.main()
    n_chunks = len(format_warn._chunks(files))
    assert n_chunks > 1 and len(calls) == 3 * n_chunks
    assert all(len(" ".join(c)) < 32767 for c in calls)
    seen = {a for c in calls for a in c if a.endswith(".py") and a.startswith("pkg/")}
    assert seen == set(files)
