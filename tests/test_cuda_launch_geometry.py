"""Tests for py_ci_shared.cuda_launch_geometry."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_launch_geometry import RULE, assert_cuda_launch_geometry, find_cuda_launch_geometry

BOM = b"\xef\xbb\xbf"


def _find(root, **kw):
    return find_cuda_launch_geometry(root, use_git=False, **kw)


def _assert(root, **kw):
    return assert_cuda_launch_geometry(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _module(body: str) -> bytes:
    return ("THREADS = 256\n\n\ndef run(kernel, x, out, n, dev):\n" + "".join("    " + line + "\n" for line in body.splitlines())).encode()


def _found(tmp_path: Path, body: str):
    return _find(_corpus(tmp_path, {"m.py": _module(body)}))


def test_reports_the_seeded_violation(tmp_path):
    """A literal block size is reported at the launch line, with the rule and the size."""
    found = _found(tmp_path, "kernel((n,), (256,), (x, out))")
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 5, RULE)]
    assert "(256,)" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """A block from the device, an arithmetic expression over names, a one-thread kernel, and a call with only two tuples (not a launch)."""
    body = "\n".join(
        [
            "block = min(256, dev.max_threads)",
            "kernel(((n + block - 1) // block,), (block,), (x, out))",
            "kernel((n,), (dev.width(kernel),), (x, out))",
            "kernel((1,), (1,), (x, out))",
            "other((1, 2), (256,))",
        ]
    )
    assert _found(tmp_path, body) == []


def test_a_module_constant_and_a_local_literal_are_the_same_literal(tmp_path):
    """A name bound once to an integer literal, at module level or in the function, is the literal."""
    body = "kernel((n,), (THREADS,), (x, out))\nwidth = 128\nkernel((n,), (width,), (x, out))"
    assert [f.line for f in _found(tmp_path, body)] == [5, 7]


def test_a_name_bound_twice_or_to_something_else_is_not_a_literal(tmp_path):
    """A name that is rebound, or bound to a call, is not judged."""
    body = "width = 128\nwidth = dev.pick()\nkernel((n,), (width,), (x, out))\nsize = dev.pick()\nkernel((n,), (size,), (x, out))"
    assert _found(tmp_path, body) == []


def test_a_two_dimensional_block_is_reported_once(tmp_path):
    """A (32, 8) tile is one finding listing its sizes."""
    found = _found(tmp_path, "kernel((n, n), (32, 8), (x, out))")
    assert len(found) == 1 and "(32, 8)" in found[0].message


def test_a_review_marker_with_a_reason_suppresses(tmp_path):
    """`# launch-ok: reason` on the call line is the review."""
    assert _found(tmp_path, "kernel((n,), (32,), (x, out))  # launch-ok: one warp per item") == []
    assert len(_found(tmp_path, "kernel((n,), (32,), (x, out))  # fine")) == 1


def test_a_multiline_launch_is_marked_on_its_last_line(tmp_path):
    """The marker may sit on the first or the last line of the call."""
    body = "kernel(\n    (n,),\n    (256,),\n    (x, out),\n)  # launch-ok: shared array sized by the block"
    assert _found(tmp_path, body) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    """The BOM must not hide the launch."""
    plain = _found(tmp_path / "a", "kernel((n,), (256,), (x, out))")
    bom = _find(_corpus(tmp_path / "b", {"m.py": BOM + _module("kernel((n,), (256,), (x, out))")}))
    assert [(f.line, f.rule) for f in bom] == [(f.line, f.rule) for f in plain] == [(5, RULE)]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    """A file the gate cannot read is a finding of its own, not a pass."""
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    """A root with no Python files must not pass for a clean scan."""
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)


def test_assert_raises_with_the_guidance_and_passes_a_clean_corpus(tmp_path):
    """The assertion names the fix; a corpus whose launches take the block from the device passes."""
    with pytest.raises(AssertionError, match=r"device.*launch-ok"):
        _assert(_corpus(tmp_path / "bad", {"m.py": _module("kernel((n,), (256,), (x, out))")}))
    _assert(_corpus(tmp_path / "good", {"m.py": _module("block = dev.block(kernel)\nkernel((n,), (block,), (x, out))")}))


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    """With a baseline file, a recorded launch passes and a new one fails; refresh writes the baseline."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": _module("kernel((n,), (256,), (x, out))")})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        _assert(root, baseline_path=baseline, refresh=True)
    _assert(root, baseline_path=baseline)
    (root / "n.py").write_bytes(_module("kernel((n,), (128,), (x, out))"))
    with pytest.raises(pytest.fail.Exception):
        _assert(root, baseline_path=baseline)
