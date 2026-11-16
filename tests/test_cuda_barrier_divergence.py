"""Tests for py_ci_shared.cuda_barrier_divergence: every taint decision is pinned, in both directions."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_barrier_divergence import (
    RULE_BRANCH,
    RULE_RETURN,
    assert_cuda_barrier_divergence,
    find_cuda_barrier_divergence,
)

BOM = b"\xef\xbb\xbf"


def _justify_entries(path: Path, where: str, branch: str) -> None:
    """Replace each refreshed note with a reason that names its own key and the test that seeds it."""
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    for key, entry in data["entries"].items():
        entry["note"] = f"{key}: the finding is seeded by {where} to exercise the {branch} of the gate, and it is not a defect in repo code."
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


PARAMS = "const float* x, float* y, const int K, const int n"
Q = '"' * 3


def _src(body: str, params: str = PARAMS, head: str = "") -> bytes:
    return f'K_SRC = r{Q}\n{head}extern "C" __global__ void k({params}) {{\n{body}\n}}\n{Q}\n'.encode()


def _rules(tmp_path: Path, body: str, *, advisory: bool = True, params: str = PARAMS, head: str = ""):
    (tmp_path / "m.py").write_bytes(_src(body, params, head))
    return [(f.line, f.rule) for f in find_cuda_barrier_divergence(tmp_path, include_advisory=advisory, use_git=False)]


# ---- reported -------------------------------------------------------------------------------------------------------------------------------------------------------------


def test_barrier_after_a_thread_dependent_return_is_advisory(tmp_path):
    body = "    __shared__ float sh[64];\n    const int t = threadIdx.x;\n    if (t >= K) return;\n    sh[t] = x[t];\n    __syncthreads();\n    y[t] = sh[K - 1 - t];"
    assert _rules(tmp_path, body) == [(7, RULE_RETURN)]
    assert _rules(tmp_path, body, advisory=False) == []


def test_barrier_in_a_thread_dependent_if_is_hard(tmp_path):
    body = "    __shared__ float sh[64];\n    if (threadIdx.x < 32) { sh[threadIdx.x] = 1.0f; __syncthreads(); y[threadIdx.x] = sh[31 - threadIdx.x]; }"
    assert _rules(tmp_path, body, advisory=False) == [(4, RULE_BRANCH)]


def test_barrier_in_the_else_of_a_thread_dependent_if(tmp_path):
    assert _rules(tmp_path, "    if (threadIdx.x == 0) { y[0] = 1.0f; } else { __syncthreads(); }", advisory=False) == [(3, RULE_BRANCH)]


def test_per_thread_trip_count_loop_with_a_barrier(tmp_path):
    body = "    for (int r = threadIdx.x; r < n; r += blockDim.x) { y[r] = x[r]; __syncthreads(); }"
    assert _rules(tmp_path, body, advisory=False) == [(3, RULE_BRANCH)]


def test_taint_flows_through_a_variable_and_a_thread_indexed_load(tmp_path):
    body = "    const int i = blockIdx.x * blockDim.x + threadIdx.x;\n    const float v = x[i];\n    if (v > 0.0f) { __syncthreads(); }"
    assert _rules(tmp_path, body, advisory=False) == [(5, RULE_BRANCH)]


def test_a_while_loop_whose_bound_becomes_thread_dependent_inside_the_body(tmp_path):
    body = "    int c = 0;\n    while (c < n) { __syncthreads(); c = threadIdx.x; }"
    assert _rules(tmp_path, body, advisory=False) == [(4, RULE_BRANCH)]


def test_a_barrier_in_a_branch_is_reported_once_as_a_branch(tmp_path):
    body = "    if (threadIdx.x >= K) return;\n    if (threadIdx.x < 3) { __syncthreads(); }"
    assert _rules(tmp_path, body) == [(4, RULE_BRANCH)]


def test_barrier_variants_count(tmp_path):
    assert _rules(tmp_path, "    if (threadIdx.x < 3) { int c = __syncthreads_count(1); }", advisory=False) == [(3, RULE_BRANCH)]


def test_lane_condition_with_a_shifted_index_is_still_thread_dependent(tmp_path):
    assert _rules(tmp_path, "    if (((threadIdx.x + 1) >> 5) == 0) { __syncthreads(); }", advisory=False) == [(3, RULE_BRANCH)]


# ---- NOT reported: the taint decisions ---------------------------------------------------------------------------------------------------------------------------------


def test_blockidx_y_is_not_a_variable_y(tmp_path):
    """The prototype's first run counted `blockIdx.y` as a use of a variable named `y` that was thread-dependent."""
    assert _rules(tmp_path, "    int y = threadIdx.x;\n    if (blockIdx.y >= n) return;\n    __syncthreads();") == []
    assert _rules(tmp_path, "    int y = threadIdx.x;\n    if (blockIdx.y < n) { __syncthreads(); }") == []


@pytest.mark.parametrize(
    "cond",
    ["blockIdx.x < n", "K > 3", "flags[blockIdx.x]", "gridDim.x > 1 && blockDim.x == 64", "n % 2 == 0", "LIMIT > 2"],
)
def test_uniform_conditions_do_not_report(tmp_path, cond):
    body = f"    __shared__ float sh[64];\n    if ({cond}) {{ sh[threadIdx.x] = 1.0f; __syncthreads(); }}\n    if ({cond}) return;\n    __syncthreads();"
    assert _rules(tmp_path, body, params="const int* flags, float* y, const int K, const int n", head="#define LIMIT 4\n") == []


def test_loop_with_a_thread_independent_bound(tmp_path):
    body = "    __shared__ float sh[64];\n    for (int j = 0; j < K; ++j) { sh[threadIdx.x] = x[j]; __syncthreads(); y[j] = sh[0]; }\n    for (int s = blockDim.x / 2; s > 0; s >>= 1) { __syncthreads(); }"
    assert _rules(tmp_path, body) == []


def test_a_warp_index_condition_is_not_reported(tmp_path):
    """Documented miss: a condition built only from the warp index is not reported."""
    body = "    const int warp = threadIdx.x >> 5;\n    if (warp == 0) { __syncthreads(); }\n    if (threadIdx.x / 32 > 1) { __syncthreads(); }"
    assert _rules(tmp_path, body) == []


def test_a_return_under_a_block_uniform_guard_is_not_a_divergent_return(tmp_path):
    assert _rules(tmp_path, "    if (blockIdx.x >= n) return;\n    __syncthreads();") == []


def test_a_return_only_flags_barriers_after_it(tmp_path):
    assert _rules(tmp_path, "    __syncthreads();\n    if (threadIdx.x >= K) return;\n    y[threadIdx.x] = 1.0f;") == []


def test_the_same_name_in_two_scopes_is_two_variables(tmp_path):
    """`i` is thread-dependent in the first loop and a plain counter in the second: the barrier of the second loop is fine."""
    body = (
        "    float a = 0.0f;\n    for (int i = threadIdx.x; i < n; i += blockDim.x) a += x[i];\n"
        "    for (int i = 0; i < K; ++i) { __syncthreads(); }\n    y[threadIdx.x] = a;"
    )
    assert _rules(tmp_path, body) == []


def test_no_implicit_flow_and_no_inheritance_from_the_enclosing_branch(tmp_path):
    body = (
        "    for (int r = threadIdx.x; r < n; r += blockDim.x) { int k = 5; k = 6; if (k > 2) { y[r] = 1.0f; } }\n"
        "    int z = 0;\n    if (threadIdx.x == 0) { z = 1; }\n    if (z) { __syncthreads(); }"
    )
    assert _rules(tmp_path, body) == []


def test_shared_scalars_and_block_reductions_are_uniform(tmp_path):
    head = "__device__ float block_sum(float v, float* s) { s[threadIdx.x] = v; __syncthreads(); return s[0]; }\n"
    body = (
        "    __shared__ int cnt;\n    __shared__ float slots[64];\n    if (threadIdx.x == 0) cnt = threadIdx.x;\n    __syncthreads();\n"
        "    if (cnt > 0) { __syncthreads(); }\n    const float tot = block_sum(y[threadIdx.x], slots);\n"
        "    if (tot < 2.0f) { y[0] = 0.0f; return; }\n    __syncthreads();"
    )
    assert _rules(tmp_path, body, params="float* y, const int n", head=head) == []


def test_a_comment_naming_the_barrier_is_not_a_barrier(tmp_path):
    assert _rules(tmp_path, "    if (threadIdx.x == 0) { /* __syncthreads(); */ y[0] = 1.0f; } // __syncthreads();") == []


def test_a_device_function_is_not_a_kernel(tmp_path):
    src = f'K_SRC = r{Q}\n__device__ void h(float* s) {{ if (threadIdx.x < 3) {{ __syncthreads(); }} }}\nextern "C" __global__ void k(float* y) {{ y[0] = 1.0f; }}\n{Q}\n'
    (tmp_path / "m.py").write_text(src)
    assert find_cuda_barrier_divergence(tmp_path, include_advisory=True, use_git=False) == []


# ---- suppression ----------------------------------------------------------------------------------------------------------------------------------------------------------


def test_marker_with_a_reason_suppresses_on_the_barrier_the_guard_or_the_line_above(tmp_path):
    assert _rules(tmp_path, "    if (threadIdx.x < 3) {\n        __syncthreads(); // barrier-ok: block is 3 wide\n    }", advisory=False) == []
    assert _rules(tmp_path, "    if (threadIdx.x < 3) { // barrier-ok: the launch has 3 threads\n        __syncthreads();\n    }", advisory=False) == []
    assert _rules(tmp_path, "    // barrier-ok: vacuous guard\n    if (threadIdx.x < 3) {\n        __syncthreads();\n    }", advisory=False) == []


def test_marker_with_an_empty_reason_or_another_word_does_not_suppress(tmp_path):
    assert _rules(tmp_path, "    if (threadIdx.x < 3) { __syncthreads(); } // barrier-ok:", advisory=False) == [(3, RULE_BRANCH)]
    assert _rules(tmp_path, "    if (threadIdx.x < 3) { __syncthreads(); } // sync-ok: why", advisory=False) == [(3, RULE_BRANCH)]


def test_python_side_marker(tmp_path):
    (tmp_path / "m.py").write_text(
        f'K_SRC = r{Q}\nextern "C" __global__ void k(float* y) {{ if (threadIdx.x < 3) {{ __syncthreads(); }} }}\n{Q}  # barrier-ok: three-thread launch\n'
    )
    assert find_cuda_barrier_divergence(tmp_path, use_git=False) == []


# ---- gate contract --------------------------------------------------------------------------------------------------------------------------------------------------------


def test_bom_file_is_reported_like_the_plain_one(tmp_path):
    (tmp_path / "m.py").write_bytes(BOM + _src("    if (threadIdx.x < 3) { __syncthreads(); }"))
    assert [f.rule for f in find_cuda_barrier_divergence(tmp_path, use_git=False)] == [RULE_BRANCH]


def test_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n")
    (tmp_path / "ok.py").write_text("x = 1\n")
    with pytest.raises(UnparsedFilesError, match=r"bad.py"):
        find_cuda_barrier_divergence(tmp_path, use_git=False)
    assert find_cuda_barrier_divergence(tmp_path, allow_unparsed=True, use_git=False) == []


def test_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_cuda_barrier_divergence(tmp_path, min_files=1, use_git=False)


def test_a_kernel_the_reader_cannot_parse_is_skipped_not_reported(tmp_path):
    (tmp_path / "m.py").write_bytes(_src("    for (auto v : range) { __syncthreads(); } if (threadIdx.x < 3) { __syncthreads(); }"))
    assert find_cuda_barrier_divergence(tmp_path, include_advisory=True, use_git=False) == []


def test_assert_raises_with_guidance_names_file_line_rule_and_passes_clean(tmp_path):
    (tmp_path / "m.py").write_bytes(_src("    if (threadIdx.x < 3) { __syncthreads(); }"))
    with pytest.raises(AssertionError, match=r"(?s)barrier-ok.*m\.py:3: \[cuda-barrier-in-divergent-branch\]"):
        assert_cuda_barrier_divergence(tmp_path, use_git=False)
    (tmp_path / "m.py").write_bytes(_src("    __syncthreads();"))
    assert_cuda_barrier_divergence(tmp_path, use_git=False)


def test_advisory_return_is_not_asserted_by_default(tmp_path):
    (tmp_path / "m.py").write_bytes(_src("    if (threadIdx.x >= K) return;\n    __syncthreads();"))
    assert_cuda_barrier_divergence(tmp_path, use_git=False)
    with pytest.raises(AssertionError, match="cuda-barrier-after-divergent-return"):
        assert_cuda_barrier_divergence(tmp_path, include_advisory=True, use_git=False)


def test_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    (tmp_path / "m.py").write_bytes(_src("    if (threadIdx.x < 3) { __syncthreads(); }"))
    base = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_cuda_barrier_divergence(tmp_path, baseline_path=base, refresh=True, use_git=False)
    _justify_entries(
        base, "tests/test_cuda_barrier_divergence.py::test_baseline_accepts_known_findings_and_fails_new_ones", "branch-divergent __syncthreads taint rule"
    )
    assert_cuda_barrier_divergence(tmp_path, baseline_path=base, use_git=False)
    (tmp_path / "n.py").write_bytes(_src("    if (threadIdx.x < 5) { __syncthreads(); }"))
    with pytest.raises(pytest.fail.Exception):
        assert_cuda_barrier_divergence(tmp_path, baseline_path=base, use_git=False)
