"""Tests for py_ci_shared.cuda_tree_reduction_block."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_tree_reduction_block import RULE, assert_cuda_tree_reduction_block, find_cuda_tree_reduction_block

BOM = b"\xef\xbb\xbf"

TREE = 'extern "C" __global__ void rowsum(const double* x, double* out) {\n  int tid = threadIdx.x, nt = blockDim.x;\n  for (int s = nt >> 1; s > 0; s >>= 1) { if (tid < s) sh[tid] += sh[tid + s]; }\n}\n'


def _find(root, **kw):
    return find_cuda_tree_reduction_block(root, use_git=False, **kw)


def _module(call: str, src: str = TREE, extra: str = "") -> bytes:
    text = f'import cupy as cp\nfrom pyutilz.performance.gpu_hw_launch import block_size_for\n\n_SRC = r"""\n{src}"""\n\n\ndef run(n):\n    kernel = cp.RawKernel(_SRC, "rowsum")\n{extra}    threads = {call}\n    return threads\n'
    return text.encode()


def _found(tmp_path, call, **kw):
    (tmp_path / "m.py").write_bytes(_module(call, **kw))
    return _find(tmp_path)


def test_reports_the_default_width_feeding_a_tree_kernel(tmp_path):
    found = _found(tmp_path, "block_size_for(kernel, work_items=n)")
    assert [(f.rule, f.line) for f in found] == [(RULE, 14)] and "rowsum" in found[0].message


def test_pow2_true_or_a_variable_and_a_wrapper_are_not_reported(tmp_path):
    assert _found(tmp_path, "block_size_for(kernel, work_items=n, pow2=True)") == []
    assert _found(tmp_path, "block_size_for(kernel, work_items=n, pow2=flag)") == []
    assert _found(tmp_path, "min(256, block_size_for(kernel, work_items=n))") == []
    assert len(_found(tmp_path, "block_size_for(kernel, work_items=n, pow2=False)")) == 1


def test_a_kernel_with_a_contract_or_a_warp_shuffle_is_not_a_tree(tmp_path):
    guarded = TREE.replace("  int tid", "  if (blockDim.x & (blockDim.x - 1)) { return; }\n  int tid")
    assert _found(tmp_path, "block_size_for(kernel, work_items=n)", src=guarded) == []
    declared = TREE.replace("  int tid", "  // pow2-ok: host launches pow2=True\n  int tid")
    assert _found(tmp_path, "block_size_for(kernel, work_items=n)", src=declared) == []
    warp = TREE.replace("int s = nt >> 1", "int s = 16")
    assert _found(tmp_path, "block_size_for(kernel, work_items=n)", src=warp) == []


def test_an_unbound_or_other_kernel_is_not_judged(tmp_path):
    other = TREE.replace("rowsum", "other")
    assert _found(tmp_path, "block_size_for(kernel, work_items=n)", src=other) == []
    (tmp_path / "m.py").write_bytes(_module("block_size_for(kern, work_items=n)").replace(b"def run(n)", b"def run(n, kern)"))
    assert _find(tmp_path) == []


def test_a_result_adjusted_later_and_a_marker(tmp_path):
    assert _found(tmp_path, "block_size_for(kernel, work_items=n)  # pow2-ok: rounded by the caller") == []
    assert len(_found(tmp_path, "block_size_for(kernel, work_items=n)  # pow2-ok:")) == 1
    assert _found(tmp_path, "block_size_for(kernel, work_items=n)\n    threads = 1 << (threads.bit_length() - 1)") == []


def test_a_kernel_bound_through_a_module_function(tmp_path):
    text = _module("block_size_for(kern, work_items=n)").decode().replace('    kernel = cp.RawKernel(_SRC, "rowsum")\n', "    kern = _get()\n")
    text += '\n\ndef _get():\n    return cp.RawKernel(_SRC, "rowsum")\n'
    (tmp_path / "m.py").write_text(text, encoding="utf-8")
    assert len(_find(tmp_path)) == 1


def test_bom_unparsable_and_empty(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "m.py").write_bytes(BOM + _module("block_size_for(kernel, work_items=n)"))
    assert len(_find(tmp_path / "a")) == 1
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "broken.py").write_bytes(b"def broken(:\n")
    (tmp_path / "b" / "ok.py").write_bytes(b"x = 1\n")
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(tmp_path / "b")
    assert _find(tmp_path / "b", allow_unparsed=True) == []
    (tmp_path / "e").mkdir()
    with pytest.raises(EmptyScanError):
        _find(tmp_path / "e", min_files=1)


def test_assert_names_the_fix(tmp_path: Path):
    (tmp_path / "m.py").write_bytes(_module("block_size_for(kernel, work_items=n)"))
    with pytest.raises(AssertionError, match=r"rowsum.*pow2=True"):
        assert_cuda_tree_reduction_block(tmp_path, use_git=False)
