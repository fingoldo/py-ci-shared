"""Tests for py_ci_shared.cuda_const_restrict. The alias and nested-subscript cases pin the prototype's 15 -> 5 -> 1 history."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_const_restrict import RULE, assert_cuda_const_restrict, find_cuda_const_restrict

BOM = b"\xef\xbb\xbf"


def _find(root, **kw):
    return find_cuda_const_restrict(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _k(sig: str, body: str, lead: str = "") -> bytes:
    return f'K_SRC = r"""\n{lead}extern "C" __global__ void k({sig}) {{\n{body}\n}}\n"""\n'.encode()


def _names(tmp_path, sig, body, lead=""):
    found = _find(_corpus(tmp_path, {"m.py": _k(sig, body, lead)}))
    assert all(f.rule == RULE for f in found)
    return [f.message.split("`")[3] for f in found]


def test_read_only_int_pointer_is_reported_at_signature_line(tmp_path):
    root = _corpus(tmp_path, {"m.py": _k("const int* c, int* joint, double* tot, const int n", "    double t = 0.0;\n    for (int i = 0; i < n; ++i) t += joint[i] * c[i];\n    tot[0] = t;")})
    found = _find(root)
    assert [(f.line, f.rule) for f in found] == [(2, RULE)] and "`joint`" in found[0].message and "const T* joint" in found[0].message


def test_restrict_hint_is_kept_in_the_suggestion(tmp_path):
    found = _find(_corpus(tmp_path, {"m.py": _k("float* __restrict__ x, float* __restrict__ y, int n", "    y[0] = 2.0f * x[0] + y[0];")}))
    assert len(found) == 1 and "const T* __restrict__ x" in found[0].message


def test_clean_const_and_written(tmp_path):
    assert _names(tmp_path, "const float* __restrict__ x, float* __restrict__ y", "    y[0] = x[0];") == []


def test_atomic_histogram_is_a_write(tmp_path):
    assert _names(tmp_path, "const int* c, int* h, int n", "    atomicAdd(&h[c[0]], 1);") == []


def test_atomic_cast_form_is_a_write(tmp_path):
    assert _names(tmp_path, "const int* a, long long* counts", "    atomicAdd((unsigned long long*)&counts[a[0]], 1ULL);") == []


def test_nested_subscript_store_writes_the_outer_array_only(tmp_path):
    """counts[(long long)p * total + col_off[c] + rem] = v: the store is on `counts`; `col_off` inside the brackets is only read."""
    body = "    counts[(long long)p * total + col_off[c] + rem] = v;"
    assert _names(tmp_path, "const long long p, int* col_off, int* counts, int total, int c, int rem, int v", body) == ["col_off"]


def test_nested_subscript_read_of_index_array_is_not_a_write(tmp_path):
    assert _names(tmp_path, "int* a, const int* b", "    a[b[0]] = 1;") == []
    assert _names(tmp_path, "int* a, int* b", "    a[b[0]] = 1;") == ["b"]


def test_multi_dimensional_subscript_chain_store(tmp_path):
    assert _names(tmp_path, "int* a", "    a[1][2] = 3;") == []


def test_ternary_alias_is_an_escape(tmp_path):
    body = "    const double* d = (is_val ? va : tr) + pos * n;\n    out[0] = d[0];"
    assert _names(tmp_path, "double* va, double* tr, double* out, int is_val, int pos, int n", body) == []


def test_pointer_arithmetic_alias_and_call_argument_are_escapes(tmp_path):
    assert _names(tmp_path, "int* a, int* o", "    int* p = a + 4;\n    p[0] = 1;\n    o[0] = 0;") == []
    assert _names(tmp_path, "int* a, int* o", "    helper(a);\n    o[0] = 0;") == []


def test_compound_assign_increment_and_deref_are_writes(tmp_path):
    for stmt in ("a[0] += 1;", "a[0] |= 1;", "a[0]++;", "++a[0];", "a[0] <<= 1;", "*a = 1;"):
        assert _names(tmp_path, "int* a", "    " + stmt) == [], stmt


def test_comparison_and_shift_reads_are_not_writes(tmp_path):
    body = "    if (a[0] == 1 || a[1] <= 2 || a[2] >= 3 || a[3] != 4) o[0] = a[4] << 2;"
    assert _names(tmp_path, "int* a, int* o", body) == ["a"]


def test_reference_binding_is_an_escape(tmp_path):
    assert _names(tmp_path, "int* a, int* o", "    int& r = a[0];\n    r = 2;\n    o[0] = 0;") == []


def test_unused_pointer_and_multi_level_pointer_are_not_reported(tmp_path):
    assert _names(tmp_path, "int* unused, int** pp, int* o", "    o[0] = 0;") == []


def test_device_function_is_not_a_kernel(tmp_path):
    src = b'K_SRC = r"""\n__global__ void k(int* o) { o[0] = 0; }\n__device__ int rd(int* a) { return a[0]; }\n"""\n'
    assert _find(_corpus(tmp_path, {"m.py": src})) == []


def test_placeholder_typed_pointer_is_checked(tmp_path):
    assert _names(tmp_path, "{REAL}* x, {ACC}* out", "    out[0] = x[0];") == ["x"]


def test_marker_with_reason_suppresses_and_empty_does_not(tmp_path):
    sig, body = "int* a, int* o", "    o[0] = a[0];"
    assert _names(tmp_path, sig, body, lead="// const-ok: kept writable for the shared signature\n") == []
    assert _names(tmp_path / "e", sig, body, lead="// const-ok:\n") == ["a"]
    assert _names(tmp_path / "f", sig, body, lead="/* const-ok: */\n") == ["a"]


def test_commented_write_does_not_count(tmp_path):
    assert _names(tmp_path, "int* a, int* o", "    // a[0] = 1;\n    o[0] = a[0];") == ["a"]


def test_bom_file_reported_like_plain(tmp_path):
    data = _k("int* a, int* o", "    o[0] = a[0];")
    assert [(f.line, f.rule) for f in _find(_corpus(tmp_path, {"m.py": BOM + data}))] == [(2, RULE)]


def test_unparsable_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_empty_corpus_fails_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)


def test_assert_names_file_line_rule_and_fix(tmp_path):
    root = _corpus(tmp_path, {"m.py": _k("int* a, int* o", "    o[0] = a[0];")})
    with pytest.raises(AssertionError, match=r"m\.py:2.*cuda-pointer-param-not-const.*const T\*"):
        assert_cuda_const_restrict(root, use_git=False)
