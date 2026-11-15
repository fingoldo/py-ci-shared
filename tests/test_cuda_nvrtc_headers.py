"""Tests for py_ci_shared.cuda_nvrtc_headers."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.cuda_nvrtc_headers import FIXES, assert_cuda_nvrtc_headers, find_cuda_nvrtc_headers


def _find(tmp_path: Path, body: str):
    (tmp_path / "m.py").write_text('K_SRC = r"""\n' + body + '"""\n', encoding="utf-8")
    return find_cuda_nvrtc_headers(tmp_path, use_git=False)


@pytest.mark.parametrize("name", sorted(FIXES))
def test_every_listed_macro_is_reported_with_a_fix(tmp_path, name):
    found = _find(tmp_path, f'extern "C" __global__ void k(float* m) {{\n    m[0] = {name};\n}}\n')
    assert [(f.line, f.rule) for f in found] == [(3, "cuda-nvrtc-undefined-macro")]
    assert FIXES[name] in found[0].message


def test_clean_shapes(tmp_path):
    assert _find(tmp_path, '#define INFINITY __int_as_float(0x7f800000)\nextern "C" __global__ void k(float* m) { m[0] = INFINITY; }\n') == []
    assert _find(tmp_path, 'extern "C" __global__ void k(float* m) { m[0] = __int_as_float(0x7f800000); /* NAN */ } // FLT_MAX\n') == []
    assert _find(tmp_path, '#include <math_constants.h>\nextern "C" __global__ void k(float* m) { m[0] = CUDART_INF_F; }\n') == []


def test_cudart_without_include(tmp_path):
    assert len(_find(tmp_path, 'extern "C" __global__ void k(float* m) { m[0] = CUDART_INF_F; }\n')) == 1


def test_marker_needs_reason(tmp_path):
    body = 'extern "C" __global__ void k(float* m) { m[0] = FLT_MAX; } // nvrtc-ok: %s\n'
    assert _find(tmp_path, body % "built with -include float.h") == []
    assert len(_find(tmp_path, body % "")) == 1


def test_assert_message(tmp_path):
    (tmp_path / "m.py").write_text('K_SRC = r"""\nextern "C" __global__ void k(float* m) { m[0] = -INFINITY; }\n"""\n', encoding="utf-8")
    with pytest.raises(AssertionError, match=r"\[cuda-nvrtc-undefined-macro\].*__int_as_float"):
        assert_cuda_nvrtc_headers(tmp_path, use_git=False)
