"""Tests for py_ci_shared.cuda_kernel_fp_types."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.cuda_kernel_fp_types import RULE_BARE, RULE_BARE_UNTYPED, RULE_LITERAL, assert_cuda_kernel_fp_types, find_cuda_kernel_fp_types


def _find(tmp_path: Path, body: str, **kw):
    (tmp_path / "m.py").write_text('K_SRC = r"""\n' + body + '"""\n', encoding="utf-8")
    return find_cuda_kernel_fp_types(tmp_path, use_git=False, **kw)


def test_bare_double_in_typed_kernel(tmp_path):
    found = _find(tmp_path, 'extern "C" __global__ void k(const {REAL}* x, int n) {\n    double a = 0;\n}\n')
    assert [(f.line, f.rule) for f in found] == [(3, RULE_BARE)]


def test_placeholders_and_calls_are_clean(tmp_path):
    assert _find(tmp_path, 'extern "C" __global__ void k(const {REAL}* x, {ACC}* o) {\n    {ACC} a = float(1); // double in comment\n}\n') == []


def test_untyped_kernel_only_with_flag(tmp_path):
    body = 'extern "C" __global__ void k(const double* x) {\n}\n'
    assert _find(tmp_path, body) == []
    assert [f.rule for f in _find(tmp_path, body, include_untyped=True)] == [RULE_BARE_UNTYPED]


def test_marker_needs_reason(tmp_path):
    body = 'extern "C" __global__ void k(const {REAL}* x) {\n    double a = 0; // fp-type-ok: %s\n}\n'
    assert _find(tmp_path, body % "edges are float64") == []
    assert [f.rule for f in _find(tmp_path, body % "")] == [RULE_BARE]


def test_literal_rule(tmp_path):
    body = 'extern "C" __global__ void k({REAL}* x, int n) {\n    x[0] = x[0] * %s;\n}\n'
    assert [f.rule for f in _find(tmp_path, body % "0.1", include_advisory=True)] == [RULE_LITERAL]
    assert _find(tmp_path, body % "0.1") == []
    for ok in ("0.5", "1.0", "0.1f", "({REAL})0.1"):
        assert _find(tmp_path, body % ok, include_advisory=True) == [], ok
    assert [f.rule for f in _find(tmp_path, 'extern "C" __global__ void k({REAL}* x) {\n    if (x[0] > 1e-9) {}\n}\n', include_advisory=True)] == [RULE_LITERAL]


def test_assert_raises_with_rule(tmp_path):
    (tmp_path / "m.py").write_text('K_SRC = r"""\nextern "C" __global__ void k({REAL}* x) { double a; }\n"""\n', encoding="utf-8")
    try:
        assert_cuda_kernel_fp_types(tmp_path, use_git=False)
    except AssertionError as e:
        assert "[cuda-bare-fp-type]" in str(e)
    else:
        raise AssertionError("no raise")
