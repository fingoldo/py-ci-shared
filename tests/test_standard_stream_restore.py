"""Tests for ``py_ci_shared.standard_stream_restore``."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.standard_stream_restore import assert_standard_streams_restored_only_if_still_ours, find_unconditional_stream_restores


def _write(tmp_path: Path, source: str) -> Path:
    """Write ``source`` as one module under ``tmp_path`` and return the directory."""
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    return tmp_path


def test_an_unconditional_restore_is_reported_with_its_line(tmp_path):
    """The save, swap and unconditional put-back: both assignments are reported, at their own lines."""
    src = "import sys\ndef quiet(devnull):\n    old = sys.stderr\n    sys.stderr = devnull\n    try:\n        pass\n    finally:\n        sys.stderr = old\n"
    found = find_unconditional_stream_restores([_write(tmp_path, src)])
    assert [f.lineno for f in found] == [4, 8]
    assert all("sys.stderr" in f.what for f in found)


def test_an_identity_guarded_restore_is_clean(tmp_path):
    """Restoring only while its own stream is installed is the safe shape and is not reported."""
    src = (
        "import sys\ndef quiet(devnull):\n    old = sys.stdout\n    sys.stdout = devnull\n    try:\n        pass\n"
        "    finally:\n        if sys.stdout is devnull:\n            sys.stdout = old\n"
    )
    assert find_unconditional_stream_restores([_write(tmp_path, src)]) == []


def test_the_guard_must_be_on_the_same_stream(tmp_path):
    """Checking stdout does not make an unconditional stderr restore safe."""
    src = "import sys\ndef quiet(a, b):\n    if sys.stdout is a:\n        pass\n    sys.stderr = b\n"
    found = find_unconditional_stream_restores([_write(tmp_path, src)])
    assert [(f.lineno, "stderr" in f.what) for f in found] == [(5, True)]


def test_a_guard_in_another_function_does_not_count(tmp_path):
    """The check is per function: a guarded helper does not vouch for an unguarded one in the same module."""
    src = "import sys\ndef safe(d):\n    if sys.stderr is d:\n        sys.stderr = None\n" "def unsafe(old):\n    sys.stderr = old\n"
    assert [f.lineno for f in find_unconditional_stream_restores([_write(tmp_path, src)])] == [6]


def test_contextlib_redirects_and_tuple_swaps_are_reported(tmp_path):
    """``redirect_stdout``/``redirect_stderr`` restore unconditionally; a tuple assignment swaps both streams."""
    src = "import contextlib, io, sys\ndef a():\n    with contextlib.redirect_stdout(io.StringIO()):\n        pass\ndef b(x, y):\n    sys.stdout, sys.stderr = x, y\n"
    found = find_unconditional_stream_restores([_write(tmp_path, src)])
    assert [(f.lineno, f.what.split()[0]) for f in found] == [(3, "contextlib.redirect_stdout"), (6, "assigns"), (6, "assigns")]


def test_the_assert_names_the_site_and_honours_allow(tmp_path):
    """The failure lists ``path:line``; the same ``path:line`` in ``allow`` passes."""
    root = _write(tmp_path, "import sys\ndef install(w):\n    sys.stdout = w\n")
    with pytest.raises(AssertionError, match=r"mod\.py:3"):
        assert_standard_streams_restored_only_if_still_ours([root])
    assert_standard_streams_restored_only_if_still_ours([root], allow=[f"{(root / 'mod.py').as_posix()}:3"])


def test_an_unparsable_file_fails_the_assert_unless_allowed(tmp_path):
    """A file the gate could not read is reported, not skipped; ``allow_unparsed`` lets the rest of the tree be judged."""
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="could not be parsed"):
        assert_standard_streams_restored_only_if_still_ours([tmp_path])
    assert_standard_streams_restored_only_if_still_ours([tmp_path], allow_unparsed=True)
