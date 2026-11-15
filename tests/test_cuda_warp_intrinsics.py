"""Tests for py_ci_shared.cuda_warp_intrinsics."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_warp_intrinsics import (
    RULE_ACTIVEMASK,
    RULE_FULL_MASK,
    RULE_SHFL,
    RULE_VOTE,
    assert_cuda_warp_intrinsics,
    find_cuda_warp_intrinsics,
)

BOM = b"\xef\xbb\xbf"
PARAMS = "float* x, int* out, const int n"
Q = '"' * 3


def _src(body: str, params: str = PARAMS, head: str = "") -> bytes:
    return f'K_SRC = r{Q}\n{head}extern "C" __global__ void k({params}) {{\n{body}\n}}\n{Q}\n'.encode()


def _rules(tmp_path: Path, body: str, *, advisory: bool = True, head: str = ""):
    (tmp_path / "m.py").write_bytes(_src(body, head=head))
    return [(f.line, f.rule) for f in find_cuda_warp_intrinsics(tmp_path, include_advisory=advisory, use_git=False)]


@pytest.mark.parametrize("name", ["__shfl", "__shfl_up", "__shfl_down", "__shfl_xor"])
def test_legacy_shfl_is_hard(tmp_path, name):
    assert _rules(tmp_path, f"    float v = x[threadIdx.x];\n    v += {name}(v, 1);\n    x[0] = v;", advisory=False) == [(4, RULE_SHFL)]


@pytest.mark.parametrize("name", ["__ballot", "__any", "__all"])
def test_legacy_vote_is_advisory(tmp_path, name):
    body = f"    int b = {name}(x[0] > 0);\n    out[0] = b;"
    assert _rules(tmp_path, body) == [(3, RULE_VOTE)]
    assert _rules(tmp_path, body, advisory=False) == []


def test_sync_forms_with_a_full_mask_in_a_full_warp_loop_are_clean(tmp_path):
    body = "    float v = x[threadIdx.x];\n    for (int o = 16; o > 0; o >>= 1) v += __shfl_down_sync(0xffffffffu, v, o);\n    if ((threadIdx.x & 31) == 0) x[blockIdx.x] = v;"
    assert _rules(tmp_path, body) == []


def test_ballot_sync_with_an_explicit_mask_is_clean(tmp_path):
    body = "    const int i = blockIdx.x * blockDim.x + threadIdx.x;\n    const unsigned b = __ballot_sync(0xffffffffu, i < n && x[i] > 0);\n    if ((threadIdx.x & 31) == 0) atomicAdd(&out[0], __popc(b));"
    assert _rules(tmp_path, body) == []


def test_full_mask_inside_a_thread_dependent_if(tmp_path):
    body = "    float v = x[threadIdx.x];\n    if (threadIdx.x < n) { v += __shfl_down_sync(0xffffffffu, v, 1); }\n    x[threadIdx.x] = v;"
    assert _rules(tmp_path, body, advisory=False) == [(4, RULE_FULL_MASK)]


def test_full_mask_in_the_else_branch_and_through_a_variable(tmp_path):
    body = "    const int i = blockIdx.x * blockDim.x + threadIdx.x;\n    const unsigned FULL = 0xFFFFFFFFu;\n    float v = x[i];\n    if (i < n) { v = 1.0f; } else { v = __shfl_sync(FULL, v, 0); }\n    x[i] = v;"
    assert _rules(tmp_path, body, advisory=False) == [(6, RULE_FULL_MASK)]


def test_full_mask_through_a_define(tmp_path):
    body = "    if (threadIdx.x < n) { x[0] = __shfl_sync(FULL_MASK, x[0], 0); }"
    assert _rules(tmp_path, body, head="#define FULL_MASK 0xffffffff\n", advisory=False) == [(4, RULE_FULL_MASK)]


@pytest.mark.parametrize(
    "cond",
    ["blockIdx.y < n", "n > 3", "blockIdx.x == 0", "(threadIdx.x >> 5) == 0", "threadIdx.x / 32 < 2"],
)
def test_full_mask_under_a_warp_or_block_uniform_condition_is_clean(tmp_path, cond):
    """`blockIdx.y` is not a variable `y`; a warp-index condition keeps all 32 lanes together."""
    body = f"    const int y = threadIdx.x;\n    if ({cond}) {{ x[0] = __shfl_sync(0xffffffffu, x[0], 0); }}"
    assert _rules(tmp_path, body) == []


def test_a_partial_mask_in_a_divergent_branch_is_not_the_full_mask_rule(tmp_path):
    assert _rules(tmp_path, "    if (threadIdx.x < 16) { x[0] = __shfl_sync(0x0000ffffu, x[0], 0); }") == []


def test_a_sync_call_inside_the_condition_is_outside_the_branch(tmp_path):
    assert _rules(tmp_path, "    if (__any_sync(0xffffffffu, threadIdx.x < n)) { x[0] = 1.0f; }") == []


def test_activemask_as_mask_is_advisory_directly_and_through_a_variable(tmp_path):
    body = "    unsigned m = __activemask();\n    x[0] = __shfl_sync(__activemask(), x[0], 0);\n    x[1] = __shfl_down_sync(m, x[1], 1);"
    assert _rules(tmp_path, body) == [(4, RULE_ACTIVEMASK), (5, RULE_ACTIVEMASK)]
    assert _rules(tmp_path, body, advisory=False) == []


def test_activemask_alone_is_not_a_finding(tmp_path):
    assert _rules(tmp_path, "    unsigned m = __activemask();\n    out[0] = __popc(m);") == []


def test_a_device_helper_is_checked_too(tmp_path):
    src = f'K_SRC = r{Q}\n__device__ float h(float v) {{ return __shfl_xor(v, 1); }}\nextern "C" __global__ void k(float* x) {{ x[0] = h(x[0]); }}\n{Q}\n'
    (tmp_path / "m.py").write_text(src)
    assert [f.rule for f in find_cuda_warp_intrinsics(tmp_path, use_git=False)] == [RULE_SHFL]


def test_a_comment_naming_a_legacy_intrinsic_is_prose(tmp_path):
    assert _rules(tmp_path, "    // was __shfl_down(v, 1) before sm_70\n    /* __ballot(p) */\n    x[0] = 1.0f;") == []


def test_a_sync_suffixed_name_is_not_legacy(tmp_path):
    assert _rules(tmp_path, "    x[0] = __shfl_sync(0xffffffffu, x[0], 0) + __shfl_xor_sync(0xffffffffu, x[0], 1);") == []


def test_marker_with_a_reason_suppresses_and_an_empty_one_does_not(tmp_path):
    assert _rules(tmp_path, "    x[0] = __shfl_down(x[0], 1); // warp-ok: sm_61-only build") == []
    assert _rules(tmp_path, "    // warp-ok: sm_61-only build\n    x[0] = __shfl_down(x[0], 1);") == []
    assert [r for _, r in _rules(tmp_path, "    x[0] = __shfl_down(x[0], 1); // warp-ok:")] == [RULE_SHFL]
    assert [r for _, r in _rules(tmp_path, "    x[0] = __shfl_down(x[0], 1); // dtype-ok: why")] == [RULE_SHFL]


def test_marker_on_the_guarding_if_suppresses_the_mask_rule(tmp_path):
    body = "    if (threadIdx.x < n) { // warp-ok: n is a multiple of 32 by construction\n        x[0] = __shfl_sync(0xffffffffu, x[0], 0);\n    }"
    assert _rules(tmp_path, body) == []


def test_python_side_marker(tmp_path):
    (tmp_path / "m.py").write_text(f'K_SRC = r{Q}\nextern "C" __global__ void k(float* x) {{ x[0] = __shfl_down(x[0], 1); }}\n{Q}  # warp-ok: sm_61 build\n')
    assert find_cuda_warp_intrinsics(tmp_path, use_git=False) == []


def test_bom_file_is_reported_like_the_plain_one(tmp_path):
    (tmp_path / "m.py").write_bytes(BOM + _src("    x[0] = __shfl_down(x[0], 1);"))
    assert [f.rule for f in find_cuda_warp_intrinsics(tmp_path, use_git=False)] == [RULE_SHFL]


def test_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n")
    (tmp_path / "ok.py").write_text("x = 1\n")
    with pytest.raises(UnparsedFilesError, match=r"bad.py"):
        find_cuda_warp_intrinsics(tmp_path, use_git=False)
    assert find_cuda_warp_intrinsics(tmp_path, allow_unparsed=True, use_git=False) == []


def test_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_cuda_warp_intrinsics(tmp_path, min_files=1, use_git=False)


def test_assert_raises_with_guidance_and_passes_clean(tmp_path):
    (tmp_path / "m.py").write_bytes(_src("    x[0] = __shfl_down(x[0], 1);"))
    with pytest.raises(AssertionError, match=r"(?s)warp-ok.*m\.py:3: \[cuda-legacy-shfl\]"):
        assert_cuda_warp_intrinsics(tmp_path, use_git=False)
    (tmp_path / "m.py").write_bytes(_src("    x[0] = __shfl_down_sync(0xffffffffu, x[0], 1);"))
    assert_cuda_warp_intrinsics(tmp_path, use_git=False)


def test_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    (tmp_path / "m.py").write_bytes(_src("    x[0] = __shfl_down(x[0], 1);"))
    base = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_cuda_warp_intrinsics(tmp_path, baseline_path=base, refresh=True, use_git=False)
    assert_cuda_warp_intrinsics(tmp_path, baseline_path=base, use_git=False)
    (tmp_path / "n.py").write_bytes(_src("    x[0] = __shfl_xor(x[0], 1);"))
    with pytest.raises(pytest.fail.Exception):
        assert_cuda_warp_intrinsics(tmp_path, baseline_path=base, use_git=False)
