"""Tests for py_ci_shared.cuda_coalesced_reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_coalesced_reads import (
    MARKER,
    RULE_INDIRECT,
    RULE_STRIDED,
    assert_cuda_coalesced_reads,
    find_cuda_coalesced_reads,
    uncoalesced_kernels,
)

BOM = b"\xef\xbb\xbf"


def _src(kernel: str) -> str:
    return 'SRC = r"""\n' + kernel + '\n"""\n'


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _find(root, **kw):
    return find_cuda_coalesced_reads(root, use_git=False, **kw)


STRIDED_LOOP = (
    'extern "C" __global__ void k(const int* X, int n, int n_features, int a, int* out) {\n'
    "    for (long long i = blockIdx.x * blockDim.x + threadIdx.x; i < n; i += gridDim.x * blockDim.x) {\n"
    "        atomicAdd(&out[X[i * (long long)n_features + a]], 1);\n    }\n}\n"
)
INDIRECT = (
    'extern "C" __global__ void runs(const unsigned char* xs, const int* starts, int S, int* out) {\n'
    "    const int s = blockIdx.x * blockDim.x + threadIdx.x;\n"
    "    if (s < S) {\n"
    "        const int lo = starts[s], hi = starts[s + 1];\n"
    "        for (int r = lo; r < hi; ++r) out[s] += xs[r];\n"
    "    }\n}\n"
)


def test_reports_the_seeded_violation(tmp_path):
    """A row-major read under a thread-strided loop is reported at the kernel, with the strided rule and the kernel in the key."""
    found = _find(_corpus(tmp_path, {"k.py": _src(STRIDED_LOOP).encode()}))
    assert [(f.path, f.rule, f.key) for f in found] == [("k.py", RULE_STRIDED, "k.py::k")]
    assert "the kernel k indexes" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """A column-major read, a block-row loop and a column-tiled read are coalesced."""
    good = (
        'extern "C" __global__ void k(const int* X, int n, int n_features, int a, int* out) {\n'
        "    for (long long i = blockIdx.x * blockDim.x + threadIdx.x; i < n; i += gridDim.x * blockDim.x) {\n"
        "        atomicAdd(&out[X[a * (long long)n + i]], 1);\n    }\n}\n"
        'extern "C" __global__ void rows(const int* X, int n, int n_features, int* out) {\n'
        "    for (int r = blockIdx.x; r < n; r += gridDim.x) {\n"
        "        out[r] = X[r * n_features + threadIdx.x];\n    }\n}\n"
        'extern "C" __global__ void tiled(const signed char* X, int n, int K, int tile, int* out) {\n'
        "    const int cj = threadIdx.x % tile; const int rg = threadIdx.x / tile; const int ng = blockDim.x / tile;\n"
        "    for (long long i = rg; i < n; i += ng) { atomicAdd(&out[X[i * (long long)K + cj]], 1); }\n}\n"
    )
    assert uncoalesced_kernels(_src(good)) == []


@pytest.mark.parametrize(
    "kernel",
    [
        # hoisted row offset
        'extern "C" __global__ void h(const int* X, int n, int F, int* o) {\n    for (long long i = threadIdx.x; i < n; i += blockDim.x) { long long off = i * F; o[i] = X[off + 3]; }\n}\n',
        # no loop at all: a global id
        'extern "C" __global__ void g(const int* X, int n, int F, int* o) {\n    long long i = blockIdx.x * blockDim.x + threadIdx.x;\n    if (i < n) o[i] = X[i * F + 3];\n}\n',
        # the factor on the left
        'extern "C" __global__ void l(const int* X, int n, int F, int* o) {\n    long long i = blockIdx.x * blockDim.x + threadIdx.x;\n    if (i < n) o[i] = X[F * i + 3];\n}\n',
    ],
)
def test_the_shapes_a_loop_only_detector_missed_are_reported(kernel):
    """A hoisted offset, a kernel with no ``for`` and a factor on the left of the index are strided reads."""
    assert [r for _n, r in uncoalesced_kernels(_src(kernel))] == [RULE_STRIDED]


def test_a_thread_run_over_a_run_table_is_reported_with_its_own_rule():
    """``lo = starts[s]; hi = starts[s + 1]; for (r = lo; r < hi; ++r) ... xs[r]`` with a thread-derived ``s`` is an indirect-run read, also when the bounds are written in the loop head."""
    assert uncoalesced_kernels(_src(INDIRECT)) == [("runs", RULE_INDIRECT)]
    direct_head = INDIRECT.replace(
        "const int lo = starts[s], hi = starts[s + 1];\n        for (int r = lo; r < hi; ++r)", "for (int r = starts[s]; r < starts[s + 1]; ++r)"
    )
    assert uncoalesced_kernels(_src(direct_head)) == [("runs", RULE_INDIRECT)]


def test_a_block_level_run_loop_is_not_an_indirect_run_read():
    """A run indexed by the block (``starts[blockIdx.x]``) walked with the thread index as the lane (``r += blockDim.x``) is coalesced."""
    coalesced = (
        'extern "C" __global__ void rows(const unsigned char* xs, const int* starts, int* out) {\n'
        "    const int b = blockIdx.x;\n"
        "    for (int r = starts[b] + threadIdx.x; r < starts[b + 1]; r += blockDim.x) atomicAdd(&out[b], xs[r]);\n}\n"
    )
    assert uncoalesced_kernels(_src(coalesced)) == []


def test_shared_memory_accesses_are_not_global_reads():
    """Reads through ``int* ones = sh;`` (a dynamic shared array) and a fill of a statically sized shared buffer are not reported; a fill of a dynamic shared array is."""
    alias = 'extern "C" __global__ void k(int* out, int P, int nb) {\n    extern __shared__ int sh[]; int* ones = sh;\n    for (int p = threadIdx.x; p < P; p += blockDim.x) { out[p] = ones[p * nb + 1]; }\n}\n'
    static_fill = (
        'extern "C" __global__ void fill_static(const double* __restrict__ edges, const int K, const int c, const int ne) {\n'
        "    __shared__ double se[256];\n    for (int s = threadIdx.x; s < ne; s += blockDim.x) se[s] = edges[(long long)s * K + c];\n    __syncthreads();\n}\n"
    )
    dynamic_fill = (
        'extern "C" __global__ void fill_dynamic(const double* __restrict__ yb, const long long words, const int B, const int b) {\n'
        "    extern __shared__ unsigned int sy[];\n    for (long long w = threadIdx.x; w < words; w += blockDim.x) sy[w] = yb[(long long)w * B + b];\n    __syncthreads();\n}\n"
    )
    assert uncoalesced_kernels(_src(alias)) == []
    assert uncoalesced_kernels(_src(static_fill)) == []
    assert [n for n, _r in uncoalesced_kernels(_src(dynamic_fill))] == ["fill_dynamic"]


def test_a_coalescing_ok_reason_suppresses_the_kernel():
    """``// coalescing-ok: <reason>`` in the kernel (or just above it) leaves it out; a marker with no reason does not."""
    marked = "// " + MARKER + " kept direct version, measured 0.62 ms against 0.86 ms for the staged one at 5000 strata\n" + INDIRECT
    assert uncoalesced_kernels(_src(marked)) == []
    inside = INDIRECT.replace("    if (s < S) {", "    // " + MARKER + " a kept version with a documented win axis\n    if (s < S) {")
    assert uncoalesced_kernels(_src(inside)) == []
    assert uncoalesced_kernels(_src("// " + MARKER + "\n" + INDIRECT)) == [("runs", RULE_INDIRECT)]
    assert uncoalesced_kernels(_src("// " + MARKER + " ok\n" + INDIRECT)) == [("runs", RULE_INDIRECT)]


def test_a_marker_belongs_to_its_own_kernel_only():
    """A reason written for one kernel does not silence the next kernel of the same string."""
    both = "// " + MARKER + " the first one is a kept version with a measured win axis\n" + STRIDED_LOOP + "\n" + INDIRECT
    assert [n for n, _r in uncoalesced_kernels(_src(both))] == ["runs"]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    """The BOM must not hide the kernel."""
    plain = _find(_corpus(tmp_path / "a", {"k.py": _src(STRIDED_LOOP).encode()}))
    bom = _find(_corpus(tmp_path / "b", {"k.py": BOM + _src(STRIDED_LOOP).encode()}))
    assert [(f.line, f.rule) for f in bom] == [(f.line, f.rule) for f in plain] and len(plain) == 1


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    """A file with a syntax error raises ``UnparsedFilesError`` naming it; ``allow_unparsed=True`` skips it."""
    root = _corpus(tmp_path, {"good.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    """A root with no Python files raises ``EmptyScanError``."""
    with pytest.raises(EmptyScanError):
        _find(_corpus(tmp_path, {"notes.txt": b"x"}), min_files=1)


def test_an_excluded_path_part_is_not_scanned(tmp_path):
    """``exclude=("_benchmarks",)`` drops the files under that directory."""
    root = _corpus(tmp_path, {"_benchmarks/b.py": _src(STRIDED_LOOP).encode(), "k.py": b"x = 1\n"})
    assert len(_find(root)) == 1 and _find(root, exclude=("_benchmarks",)) == []


def test_the_assert_names_the_kernel_and_a_baseline_with_a_note_accepts_it(tmp_path):
    """Without a baseline the assert raises naming the kernel; a baseline with a note for ``<path>::<kernel>`` accepts exactly that finding."""
    root = _corpus(tmp_path, {"k.py": _src(STRIDED_LOOP).encode()})
    with pytest.raises(AssertionError, match=r"strided global read"):
        assert_cuda_coalesced_reads(root, use_git=False)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        '{"schema": 1, "gate": "cuda_coalesced_reads", "entries": {"k.py::k": {"count": 1, "note": "kept: small matrix that sits in cache"}}}', encoding="utf-8"
    )
    assert_cuda_coalesced_reads(root, baseline_path=baseline, use_git=False)


def test_a_warp_uniform_loop_variable_is_not_a_thread_stride():
    """``w`` runs over the words per warp (started from ``threadIdx.x >> 5``) and the lanes read consecutive ``p``: ``ybits[w * P + p]`` is one contiguous line per warp, not a stride."""
    kernel = (
        'extern "C" __global__ void k(const unsigned int* ybits, int W, int P, unsigned int* out) {\n'
        "    const int warp = threadIdx.x >> 5; const int lane = threadIdx.x & 31;\n"
        "    for (int w = warp; w < W; w += blockDim.x / 32) { const int p = lane; out[p] += ybits[(long long)w * P + p]; }\n}\n"
    )
    assert uncoalesced_kernels(_src(kernel)) == []


def test_a_term_of_a_difference_or_a_constant_factor_is_not_a_row_offset():
    """``j = idx - i * ny`` (the column inside a cell) and ``i = 2LL * tid`` (a constant stride of 2) are not hoisted row offsets."""
    column = (
        'extern "C" __global__ void a(const double* freqs, int n, int ny, double* out) {\n'
        "    for (int idx = threadIdx.x; idx < n; idx += blockDim.x) { int i = idx / ny; int j = idx - i * ny; out[idx] = freqs[j]; }\n}\n"
    )
    constant = (
        'extern "C" __global__ void b(const double* u, int n, double* out) {\n'
        "    const int tid = threadIdx.x; const long long i = 2LL * tid; out[tid] = u[i];\n}\n"
    )
    assert uncoalesced_kernels(_src(column)) == []
    assert uncoalesced_kernels(_src(constant)) == []


def test_a_warp_index_taken_from_a_thread_variable_is_not_a_thread_stride():
    """``tid = threadIdx.x; warp = tid >> 5`` is the same group index as ``threadIdx.x >> 5``; ``lane = tid & 31`` stays thread-derived (its neighbours read neighbouring elements)."""
    kernel = (
        'extern "C" __global__ void k(const unsigned int* ybits, int W, int P, unsigned int* out) {\n'
        "    const int tid = threadIdx.x, lane = tid & 31, warp = tid >> 5, nw = blockDim.x >> 5;\n"
        "    for (int w = warp; w < W; w += nw) { out[lane] += ybits[(long long)w * P + lane]; }\n}\n"
    )
    assert uncoalesced_kernels(_src(kernel)) == []
    strided_lane = kernel.replace("ybits[(long long)w * P + lane]", "ybits[(long long)lane * P + w]")
    assert [n for n, _r in uncoalesced_kernels(_src(strided_lane))] == ["k"]
