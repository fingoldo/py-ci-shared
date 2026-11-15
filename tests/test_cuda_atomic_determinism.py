"""Tests for py_ci_shared.cuda_atomic_determinism."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_atomic_determinism import RULE, RULE_UNRESOLVED, assert_cuda_atomic_determinism, find_cuda_atomic_determinism

BOM = b"\xef\xbb\xbf"


def _find(root, **kw):
    return find_cuda_atomic_determinism(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _src(kernel: str, *, head: str = "", lead: str = "") -> bytes:
    return f'{head}K_SRC = r"""\n{lead}{kernel}\n"""\n'.encode()


def _found(tmp_path, kernel, **kw):
    head, lead = kw.pop("head", ""), kw.pop("lead", "")
    return _find(_corpus(tmp_path, {"m.py": _src(kernel, head=head, lead=lead)}), **kw)


GLOBAL_DOUBLE = 'extern "C" __global__ void total(const double* x, double* out, const int n) {\n    int i = threadIdx.x;\n    if (i < n) atomicAdd(&out[0], x[i]);\n}'
SHARED_FLOAT = 'extern "C" __global__ void t(const float* x, float* out) {\n    __shared__ float sh[1];\n    atomicAdd(&sh[0], x[0]);\n    atomicAdd(&sh[0], x[1]);\n    out[0] = sh[0];\n}'
INT_HIST = 'extern "C" __global__ void hist(const int* c, int* h, const int n) {\n    int i = threadIdx.x;\n    if (i < n) atomicAdd(&h[c[i]], 1);\n}'


def test_global_double_atomic_is_reported_at_its_line(tmp_path):
    found = _found(tmp_path, GLOBAL_DOUBLE)
    assert [(f.line, f.rule) for f in found] == [(4, RULE)]
    assert "kernel `total`" in found[0].message and "1 `atomicAdd`" in found[0].message


def test_shared_float_atomic_counts_the_sites_once_per_kernel(tmp_path):
    found = _found(tmp_path, SHARED_FLOAT)
    assert [f.rule for f in found] == [RULE] and "2 `atomicAdd`" in found[0].message


def test_placeholder_typed_atomic_is_reported(tmp_path):
    kernel = GLOBAL_DOUBLE.replace("double", "{ACC}")
    assert [f.rule for f in _found(tmp_path, kernel)] == [RULE]


def test_integer_atomics_are_never_reported(tmp_path):
    """Negative control: an int histogram, an unsigned long long cast, a long long counter."""
    assert _found(tmp_path, INT_HIST) == []
    cast = 'extern "C" __global__ void k(const int* a, long long* counts) {\n    atomicAdd((unsigned long long*)&counts[a[0]], 1ULL);\n}'
    assert _found(tmp_path, cast) == []
    shared_int = 'extern "C" __global__ void k(int* o) {\n    __shared__ unsigned int sh[4];\n    atomicAdd(&sh[0], 1u);\n    o[0] = sh[0];\n}'
    assert _found(tmp_path, shared_int) == []


def test_fp_cast_target_is_reported(tmp_path):
    kernel = 'extern "C" __global__ void k(const int* a, unsigned long long* c) {\n    atomicAdd((double*)&c[0], 1.0);\n}'
    assert [f.rule for f in _found(tmp_path, kernel)] == [RULE]


def test_unresolved_base_is_reported_only_on_request(tmp_path):
    kernel = 'extern "C" __global__ void k(double* a, double* b, int c) {\n    atomicAdd((c ? a : b) + 1, 1.0);\n}'
    assert _found(tmp_path, kernel) == []
    assert [f.rule for f in _found(tmp_path, kernel, include_unresolved=True)] == [RULE_UNRESOLVED]


def test_local_pointer_alias_declaration_resolves(tmp_path):
    kernel = 'extern "C" __global__ void k(double* a) {\n    double* p = a + 1;\n    atomicAdd(&p[0], 1.0);\n}'
    assert [f.rule for f in _found(tmp_path, kernel)] == [RULE]


def test_marker_with_reason_in_kernel_suppresses(tmp_path):
    assert _found(tmp_path, GLOBAL_DOUBLE, lead="// atomic-fp-ok: parity test uses rtol 1e-9\n") == []


def test_marker_without_reason_does_not_suppress(tmp_path):
    assert [f.rule for f in _found(tmp_path, GLOBAL_DOUBLE, lead="// atomic-fp-ok:\n")] == [RULE]
    assert [f.rule for f in _found(tmp_path, GLOBAL_DOUBLE, lead="/* atomic-fp-ok: */\n")] == [RULE]


def test_python_comment_and_docstring_note_suppress(tmp_path):
    assert _found(tmp_path, GLOBAL_DOUBLE, head="# atomic-fp-ok: tolerance covers the order\n") == []
    assert _found(tmp_path / "d", GLOBAL_DOUBLE, head='"""Kernels.\n\natomic-fp-ok: order does not matter here.\n"""\n') == []
    assert [f.rule for f in _found(tmp_path / "e", GLOBAL_DOUBLE, head="# atomic-fp-ok:\n")] == [RULE]


def test_commented_out_atomic_is_prose(tmp_path):
    kernel = 'extern "C" __global__ void k(double* a) {\n    // atomicAdd(&a[0], 1.0);\n    a[0] = 1.0;\n}'
    assert _found(tmp_path, kernel) == []


def test_two_kernels_one_source_each_reported(tmp_path):
    found = _found(tmp_path, GLOBAL_DOUBLE + "\n" + SHARED_FLOAT)
    assert [f.message.split("`")[1] for f in found] == ["total", "t"]


def test_bom_file_reported_like_plain(tmp_path):
    plain = _found(tmp_path / "a", GLOBAL_DOUBLE)
    bom = _find(_corpus(tmp_path / "b", {"m.py": BOM + _src(GLOBAL_DOUBLE)}))
    assert [(f.line, f.rule) for f in bom] == [(f.line, f.rule) for f in plain]


def test_unparsable_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_empty_corpus_fails_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)


def test_assert_names_file_line_rule_and_fix(tmp_path):
    root = _corpus(tmp_path, {"m.py": _src(GLOBAL_DOUBLE)})
    with pytest.raises(AssertionError, match=r"m\.py:4.*cuda-float-atomic-add.*atomic-fp-ok"):
        assert_cuda_atomic_determinism(root, use_git=False)
    assert_cuda_atomic_determinism(_corpus(tmp_path / "ok", {"m.py": _src(INT_HIST)}), use_git=False)
