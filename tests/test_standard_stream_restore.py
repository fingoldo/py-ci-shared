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
    """The save, swap and unconditional put-back: the put-back is reported at its own line, the install is not."""
    src = "import sys\ndef quiet(devnull):\n    old = sys.stderr\n    sys.stderr = devnull\n    try:\n        pass\n    finally:\n        sys.stderr = old\n"
    found = find_unconditional_stream_restores([_write(tmp_path, src)])
    assert [f.lineno for f in found] == [8]
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
    src = "import sys\ndef quiet(a):\n    old = sys.stderr\n    if sys.stdout is a:\n        sys.stderr = old\n"
    found = find_unconditional_stream_restores([_write(tmp_path, src)])
    assert [(f.lineno, "stderr" in f.what) for f in found] == [(5, True)]


def test_a_guard_in_another_function_does_not_count(tmp_path):
    """The check is per function: a guarded helper does not vouch for an unguarded one in the same module."""
    src = "import sys\nOLD = sys.stderr\ndef safe(d):\n    if sys.stderr is d:\n        sys.stderr = OLD\n" "def unsafe():\n    sys.stderr = OLD\n"
    assert [f.lineno for f in find_unconditional_stream_restores([_write(tmp_path, src)])] == [7]


def test_contextlib_redirects_and_tuple_restores_are_reported(tmp_path):
    """``redirect_stdout``/``redirect_stderr`` restore unconditionally; a tuple assignment restores both streams."""
    src = (
        "import contextlib, io, sys\ndef a():\n    with contextlib.redirect_stdout(io.StringIO()):\n        pass\n"
        "def b():\n    out, err = sys.stdout, sys.stderr\n    yield\n    sys.stdout, sys.stderr = out, err\n"
    )
    found = find_unconditional_stream_restores([_write(tmp_path, src)])
    assert [(f.lineno, f.what.split()[0]) for f in found] == [(3, "contextlib.redirect_stdout"), (8, "restores"), (8, "restores")]


def test_the_assert_names_the_site_and_honours_allow(tmp_path):
    """The failure lists ``path:line``; the same ``path:line``, or ``path::function``, in ``allow`` passes."""
    root = _write(
        tmp_path,
        "import sys\nclass Quiet:\n    def __enter__(self):\n        self._old = sys.stdout\n    def __exit__(self, *a):\n        sys.stdout = self._old\n",
    )
    with pytest.raises(AssertionError, match=r"mod\.py:6"):
        assert_standard_streams_restored_only_if_still_ours([root])
    assert_standard_streams_restored_only_if_still_ours([root], allow=[f"{(root / 'mod.py').as_posix()}:6"])
    assert_standard_streams_restored_only_if_still_ours([root], allow=[f"{(root / 'mod.py').as_posix()}::Quiet.__exit__"])
    with pytest.raises(AssertionError):
        assert_standard_streams_restored_only_if_still_ours([root], allow=[f"{(root / 'mod.py').as_posix()}::Quiet.__enter__"])


def test_an_unparsable_file_fails_the_assert_unless_allowed(tmp_path):
    """A file the gate could not read is reported, not skipped; ``allow_unparsed`` lets the rest of the tree be judged."""
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="could not be parsed"):
        assert_standard_streams_restored_only_if_still_ours([tmp_path])
    assert_standard_streams_restored_only_if_still_ours([tmp_path], allow_unparsed=True)


@pytest.mark.parametrize(
    "src",
    [
        pytest.param(
            "import sys\ndef f(old):\n    if sys.stderr is None:\n        return\n    try:\n        pass\n    finally:\n        sys.stderr = old_err\nold_err = sys.stderr\n",
            id="an-unrelated-is-None-check",
        ),
        pytest.param("import sys as _sys\ndef f(devnull):\n    old = _sys.stdout\n    _sys.stdout = devnull\n    _sys.stdout = old\n", id="import-sys-as"),
        pytest.param("import sys\ndef f(devnull):\n    old = sys.stdout\n    setattr(sys, 'stdout', devnull)\n    setattr(sys, 'stdout', old)\n", id="setattr"),
        pytest.param(
            "import sys\ndef f(devnull):\n    old = sys.stderr\n    def inner():\n        return sys.stderr is devnull\n    sys.stderr = old\n",
            id="a-check-in-a-nested-function",
        ),
        pytest.param(
            "import sys\ndef f(devnull):\n    old = sys.stdout\n    sys.stdout = devnull\n    try:\n        pass\n    finally:\n        sys.stdout = old\n    assert sys.stdout is old\n",
            id="an-identity-check-that-guards-nothing",
        ),
    ],
)
def test_an_identity_check_that_does_not_guard_the_restore_does_not_exempt_it(tmp_path, src):
    """N-10: any ``is`` comparison with the stream anywhere in the scope (nested functions too) exempted every restore,
    and aliases of ``sys`` and ``setattr`` were invisible."""
    assert [f.what.split()[0] for f in find_unconditional_stream_restores([_write(tmp_path, src)])] == ["restores"]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("import io, sys\nsys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')\n", id="process-lifetime-wrapper"),
        pytest.param(
            "import sys\ndef fixture():\n    before = (sys.stdout, sys.stderr)\n    yield\n"
            "    leaked = [n for n, s, b in (('stdout', sys.stdout, before[0]), ('stderr', sys.stderr, before[1])) if s is not b]\n"
            "    if leaked:\n        sys.stdout, sys.stderr = before\n",
            id="guard-through-a-computed-name",
        ),
        pytest.param(
            "import sys\ndef f(devnull):\n    old = sys.stdout\n    sys.stdout = devnull\n    if sys.stdout is not devnull:\n        return\n    sys.stdout = old\n",
            id="an-early-exit-guard",
        ),
    ],
)
def test_the_consumer_false_positive_shapes_are_clean(tmp_path, src):
    """N-11: a CLI's process-lifetime wrapper restores nothing, and mlframe's conftest guards its restore through a
    name computed from ``s is not b``; both were reported (29 glossum and 2 mlframe findings)."""
    assert find_unconditional_stream_restores([_write(tmp_path, src)]) == []
