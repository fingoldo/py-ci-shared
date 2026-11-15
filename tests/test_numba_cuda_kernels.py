"""Tests for py_ci_shared.numba_cuda_kernels."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.numba_cuda_kernels import (
    RULE_DIVERGENT_SYNC,
    RULE_FLOAT,
    RULE_RMW,
    RULE_UNGUARDED,
    assert_numba_cuda_kernels,
    find_numba_cuda_kernels,
)

HEAD = "import numpy as np\nfrom numba import cuda\n\n\n"


def _rules(tmp_path: Path, src: str, head: str = HEAD):
    (tmp_path / "m.py").write_text(head + src, encoding="utf-8")
    return [(f.rule, f.line - head.count("\n")) for f in find_numba_cuda_kernels(tmp_path, use_git=False)]


def _only(tmp_path, src, rule, **kw):
    return [r for r in _rules(tmp_path, src, **kw) if r[0] == rule]


def test_unguarded_index_is_reported_and_each_guard_form_is_not(tmp_path):
    bad = _only(tmp_path, "@cuda.jit\ndef k(x, out):\n    i = cuda.grid(1)\n    out[i] = x[i]\n", RULE_UNGUARDED)
    assert len(bad) == 2  # one finding per parameter per statement: out and x
    for body in (
        "    if i >= n:\n        return\n    out[i] = x[i]\n",
        "    if i < n:\n        out[i] = x[i]\n",
        "    for j in range(i, n, 32):\n        out[j] = x[j]\n",
        "    v = x[i] if i < n else 0\n",
        "    ok = i < n and x[i] > 0\n",
    ):
        assert not _only(tmp_path, f"@cuda.jit\ndef k(x, out, n):\n    i = cuda.grid(1)\n{body}", RULE_UNGUARDED), body


def test_unguarded_index_through_a_derived_name_and_blockidx_spelling(tmp_path):
    src = "@cuda.jit\ndef k(x, out, m):\n    tid = cuda.blockIdx.x * cuda.blockDim.x + cuda.threadIdx.x\n    p = tid // m\n    out[p] = 1\n"
    assert _only(tmp_path, src, RULE_UNGUARDED)
    guarded = src.replace("    p = ", "    if tid >= 10:\n        return\n    p = ")
    assert not _only(tmp_path, guarded, RULE_UNGUARDED)
    # one block per item: the index is block-uniform, nothing to bound
    assert not _only(tmp_path, "@cuda.jit\ndef k(x, out):\n    p = cuda.blockIdx.x\n    out[p] = x[p]\n", RULE_UNGUARDED)


def test_divergent_syncthreads_cases(tmp_path):
    base = "@cuda.jit\ndef k(x, n):\n    tid = cuda.threadIdx.x\n"
    assert _only(tmp_path, base + "    if tid < n:\n        cuda.syncthreads()\n", RULE_DIVERGENT_SYNC)
    assert _only(tmp_path, base + "    if tid >= n:\n        return\n    cuda.syncthreads()\n", RULE_DIVERGENT_SYNC)
    assert _only(tmp_path, base + "    for i in range(tid, n, 32):\n        cuda.syncthreads()\n", RULE_DIVERGENT_SYNC)
    assert _only(tmp_path, base + "    while tid < n:\n        cuda.syncthreads()\n        tid += 1\n", RULE_DIVERGENT_SYNC)
    assert not _only(tmp_path, base + "    if tid < n:\n        x[tid] = 1\n    cuda.syncthreads()\n", RULE_DIVERGENT_SYNC)
    # a block-uniform branch and a block-uniform early return keep the barrier convergent
    uni = "@cuda.jit\ndef k(x, n):\n    b = cuda.blockIdx.x\n    if b >= n:\n        return\n    cuda.syncthreads()\n"
    assert not _only(tmp_path, uni, RULE_DIVERGENT_SYNC)
    assert not _only(tmp_path, base + "    for i in range(n):\n        cuda.syncthreads()\n", RULE_DIVERGENT_SYNC)


def test_rmw_cases(tmp_path):
    base = "@cuda.jit\ndef k(x, acc, n):\n    tid = cuda.threadIdx.x\n"
    assert _only(tmp_path, base + "    acc[0] += x[tid]\n", RULE_RMW)
    assert _only(tmp_path, base + "    acc[0] = acc[0] + x[tid]\n", RULE_RMW)
    assert _only(tmp_path, base + "    acc[cuda.blockIdx.x] += x[tid]\n", RULE_RMW)
    assert _only(tmp_path, base + "    for r in range(tid, n, 32):\n        acc[0] += x[r]\n", RULE_RMW)
    assert not _only(tmp_path, base + "    if tid == 0:\n        acc[0] += 1\n", RULE_RMW)
    assert not _only(tmp_path, base + "    if tid != 0:\n        return\n    acc[0] += 1\n", RULE_RMW)
    assert not _only(tmp_path, base + "    acc[tid] += 1\n", RULE_RMW)
    assert not _only(tmp_path, base + "    acc[0] = x[tid]\n", RULE_RMW)  # a plain store is not a read-modify-write
    assert not _only(tmp_path, base + "    cuda.atomic.add(acc, 0, x[tid])\n", RULE_RMW)
    assert not _only(tmp_path, base + "    loc = cuda.local.array(4, dtype=np.int64)\n    loc[0] += 1\n", RULE_RMW)
    assert not _only(tmp_path, base + "    acc[x[tid]] += 1\n", RULE_RMW)  # data-dependent cell: undecidable
    assert not _only(tmp_path, base + "    j = 0\n    j = 1\n    acc[j] += 1\n", RULE_RMW)  # a rebound name is not proven uniform


def test_rmw_shared_array_and_uniform_derived_index(tmp_path):
    src = "@cuda.jit\ndef k(x, n):\n    sh = cuda.shared.array(8, dtype=np.int32)\n    c = n // 2\n    sh[c] += 1\n"
    assert _only(tmp_path, src, RULE_RMW)


def test_float_mix_cases(tmp_path):
    mixed = "@cuda.jit\ndef k(x):\n    a = np.float32(1)\n    b = np.float64(2)\n    c = np.double(3)\n"
    assert [r[1] for r in _only(tmp_path, mixed, RULE_FLOAT)] == [4, 5]
    assert not _only(tmp_path, "@cuda.jit\ndef k(x):\n    b = np.float64(2)\n", RULE_FLOAT)
    sig = '@cuda.jit("void(float32[:])")\ndef k(x):\n    b = np.float64(2)\n'
    assert _only(tmp_path, sig, RULE_FLOAT)
    assert not _only(tmp_path, '@cuda.jit("void(float32[:], float64)")\ndef k(x, s):\n    b = np.float64(2)\n', RULE_FLOAT)
    # a device helper is read by the float rule too
    assert _only(tmp_path, "@cuda.jit(device=True)\ndef h(x):\n    return np.float32(x) + np.float64(1)\n", RULE_FLOAT)


@pytest.mark.parametrize(
    "head,deco,grid",
    [
        ("from numba import cuda\n", "@cuda.jit", "cuda.grid(1)"),
        ("from numba import cuda as _nb\n", "@_nb.jit(cache=True)", "_nb.grid(1)"),
        ("import numba.cuda as cuda\n", "@cuda.jit", "cuda.grid(1)"),
        ("import numba\n", "@numba.cuda.jit(cache=True)", "numba.cuda.grid(1)"),
        ("from numba.cuda import jit, grid\n", "@jit", "grid(1)"),
        ("import numba.cuda\n", "@numba.cuda.jit", "numba.cuda.grid(1)"),
    ],
)
def test_kernel_discovery_forms(tmp_path, head, deco, grid):
    src = f"{deco}\ndef k(x, out):\n    i = {grid}\n    out[i] = 1\n"
    assert _only(tmp_path, src, RULE_UNGUARDED, head=head + "\n\n")


def test_device_true_and_foreign_jit_are_not_kernels(tmp_path):
    body = "def h(x, out):\n    i = cuda.grid(1)\n    out[i] = 1\n"
    assert not _rules(tmp_path, "@cuda.jit(device=True)\n" + body)
    assert not _rules(tmp_path, "@cuda.jit(device=True, inline=True)\n" + body)
    assert not _rules(tmp_path, "@njit\n" + body, head=HEAD + "from numba import njit\n")
    assert not _rules(tmp_path, "@cupy.jit\n" + body, head=HEAD + "import cupy\n")
    assert _rules(tmp_path, "@cuda.jit(device=False)\n" + body)


def test_kernel_nested_in_a_factory_is_found(tmp_path):
    src = "def make():\n    @cuda.jit\n    def k(x, out):\n        i = cuda.grid(1)\n        out[i] = 1\n    return k\n"
    assert _only(tmp_path, src, RULE_UNGUARDED)


def test_marker_on_line_and_on_header_and_empty_reason_does_not_suppress(tmp_path):
    body = "    i = cuda.grid(1)\n    out[i] = 1{m}\n"
    assert _only(tmp_path, "@cuda.jit\ndef k(out):\n" + body.format(m=""), RULE_UNGUARDED)
    assert not _only(tmp_path, "@cuda.jit\ndef k(out):\n" + body.format(m="  # numba-cuda-ok: exact launch"), RULE_UNGUARDED)
    assert not _only(tmp_path, "@cuda.jit\ndef k(out):  # numba-cuda-ok: exact launch\n" + body.format(m=""), RULE_UNGUARDED)
    assert not _only(tmp_path, "@cuda.jit  # numba-cuda-ok: exact launch\ndef k(out):\n" + body.format(m=""), RULE_UNGUARDED)
    assert _only(tmp_path, "@cuda.jit\ndef k(out):\n" + body.format(m="  # numba-cuda-ok:"), RULE_UNGUARDED)
    assert _only(tmp_path, "@cuda.jit\ndef k(out):\n" + body.format(m="  # numba-cuda-ok:   "), RULE_UNGUARDED)


def test_assert_names_file_line_rule_and_fix(tmp_path):
    (tmp_path / "m.py").write_text(HEAD + "@cuda.jit\ndef k(out):\n    i = cuda.grid(1)\n    out[i] = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError) as err:
        assert_numba_cuda_kernels(tmp_path, use_git=False)
    text = str(err.value)
    assert "m.py:8" in text and RULE_UNGUARDED in text and "if i >= n: return" in text


def test_clean_corpus_passes_and_floors_hold(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    assert_numba_cuda_kernels(tmp_path, use_git=False)
    with pytest.raises(EmptyScanError):
        assert_numba_cuda_kernels(tmp_path, min_files=5, use_git=False)
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError):
        assert_numba_cuda_kernels(tmp_path, use_git=False)
    assert_numba_cuda_kernels(tmp_path, use_git=False, allow_unparsed=True)
