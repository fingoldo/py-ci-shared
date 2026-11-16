"""Tests for py_ci_shared.cuda_grid_caps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_grid_caps import RULE, assert_cuda_grid_caps, find_cuda_grid_caps

BOM = b"\xef\xbb\xbf"


def _find(root, **kw):
    return find_cuda_grid_caps(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _found(tmp_path: Path, body: str, head: str = ""):
    src = head + "\n\ndef run(kernel, x, out, n, dev):\n" + "".join("    " + line + "\n" for line in body.splitlines())
    return _find(_corpus(tmp_path, {"m.py": src.encode()}))


def test_a_literal_cap_inside_the_grid_is_reported(tmp_path):
    found = _found(tmp_path, "kernel((min(64, n // 256), 3), (256,), (x, out))")
    assert [(f.line, f.rule) for f in found] == [(4, RULE)] and "literal 64" in found[0].message


def test_a_cap_behind_a_local_name_is_followed(tmp_path):
    found = _found(tmp_path, "blocks = max(1, min(64, n // 256))\nkernel((blocks,), (256,), (x, out))")
    assert [f.line for f in found] == [4]


def test_a_module_constant_cap_is_resolved(tmp_path):
    assert len(_found(tmp_path, "kernel((min(_CAP, n),), (256,), (x, out))", "_CAP = 128")) == 1


def test_a_name_that_is_the_block_width_or_a_range_step_is_not_a_cap(tmp_path):
    assert _found(tmp_path, "threads = min(256, dev.t)\nkernel(((n + threads - 1) // threads,), (threads,), (x, out))") == []
    body = "block = min(n, 256)\nfor c in range(0, n, block):\n    kernel((block,), (128,), (x, out))"
    assert _found(tmp_path, body) == []


def test_an_sm_aware_grid_and_large_caps_and_floors_are_not_reported(tmp_path):
    body = "\n".join(
        [
            "kernel((min(64, dev.multiProcessorCount * 8),), (256,), (x, out))",
            "sms = dev.sm_count",
            "kernel((min(64, sms * 8),), (256,), (x, out))",
            "kernel((min(65535, n),), (256,), (x, out))",
            "kernel((min(4096, n),), (256,), (x, out))",
            "kernel((max(64, n),), (256,), (x, out))",
            "kernel((min(8, n),), (256,), (x, out))",
        ]
    )
    assert _found(tmp_path, body) == []


def test_a_blocks_constant_is_reported_only_when_a_launch_uses_it(tmp_path):
    head = "_STAT_BLOCKS = 64\n_GRID_POINTS = 120\n_UNUSED_BLOCKS = 32"
    found = _found(tmp_path, "kernel((_STAT_BLOCKS,), (256,), (x, out))\nlog(_GRID_POINTS)", head)
    assert [(f.line, "_STAT_BLOCKS" in f.message) for f in found] == [(1, True)]


def test_a_marker_needs_a_reason(tmp_path):
    ok = "kernel((min(64, n),), (256,), (x, out))  # grid-ok: the algorithm keeps 64 partials"
    assert _found(tmp_path, ok) == []
    assert len(_found(tmp_path, "kernel((min(64, n),), (256,), (x, out))  # grid-ok:")) == 1
    assert _found(tmp_path / "c", "_STAT_BLOCKS = 64  # grid-ok: partial count is the layout\nkernel((_STAT_BLOCKS,), (256,), (x, out))") == []
    assert _found(tmp_path / "d", "b = min(64, n)  # grid-ok: tile count\nkernel((b,), (256,), (x, out))") == []


def test_a_numba_launch_is_a_launch(tmp_path):
    assert len(_found(tmp_path, "kern[min(64, n), 256](x)")) == 1


def test_bom_unparsable_and_empty(tmp_path):
    body = b"def f(k, x):\n    k((min(64, 3),), (128,), (x,))\n"
    assert [f.line for f in _find(_corpus(tmp_path / "a", {"m.py": BOM + body}))] == [2]
    root = _corpus(tmp_path / "b", {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []
    (tmp_path / "e").mkdir()
    with pytest.raises(EmptyScanError):
        _find(tmp_path / "e", min_files=1)


def test_assert_message_and_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": b"def f(k, x):\n    k((min(64, 3),), (128,), (x,))\n"})
    with pytest.raises(AssertionError, match=r"multiProcessorCount.*grid-ok"):
        assert_cuda_grid_caps(root, use_git=False)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_cuda_grid_caps(root, baseline_path=baseline, refresh=True, use_git=False)
    data = json.loads(baseline.read_text(encoding="utf-8"))
    reasons = {
        "cuda-literal-grid-cap::m.py::this launch grid is capped by the literal 64 in min(...): derive the cap from the device (multiProcessorCount x resident blocks x waves) or mark `# grid-ok: <reason>`": (
            "The finding cuda-literal-grid-cap::m.py::this launch grid is capped by the literal 64 in min(...) is seeded by "
            "tests/test_cuda_grid_caps.py::test_assert_message_and_baseline to exercise the literal-grid-cap branch of the gate, "
            "and it is not a defect in repo code."
        ),
    }
    for key, entry in data["entries"].items():
        entry["note"] = reasons[key]
    baseline.write_bytes(json.dumps(data, indent=2, sort_keys=True).encode("utf-8"))
    assert_cuda_grid_caps(root, baseline_path=baseline, use_git=False)
    (root / "n.py").write_bytes(b"def f(k, x):\n    k((min(32, 3),), (128,), (x,))\n")
    with pytest.raises(pytest.fail.Exception):
        assert_cuda_grid_caps(root, baseline_path=baseline, use_git=False)
