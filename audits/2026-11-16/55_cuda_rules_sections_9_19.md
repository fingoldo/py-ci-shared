# 55 cuda_rules_sections_9_19: round 2026-11-16 (T)

Scope: sections 9 and 12-19 of `C:\Users\Admin\Machine learning\mlframe\CUDA.md` (read whole once), the mlframe-only CUDA/GPU/KTC meta-tests, and the clang-tidy / compute-sanitizer tooling of section 17.
Commit audited: py-ci-shared `48e49b1`; corpus snapshot mlframe `d2ef3afd6` (read-only, the tree was moving; counts are a snapshot taken 2026-11-16), pyutilz `src/pyutilz/performance/gpu_hw_launch.py` (read for the width helper).
Corpus: `find_cuda_kernel_sources(src/mlframe, exclude=("_benchmarks","kernel_prototypes"))` gave **155 kernel sources** in 91 files (210 `__global__` functions); 2213 Python files, 360 mention cupy/RawKernel. Brief says ~85 kernels: that count is stale, the real denominator is 155 sources.
Method: throwaway detectors in `...\scratchpad\audit_T\55\` (`proto1..9.py`, `tidy_proto.py`, `tidy_cat.py`, `tree_real.py`, `cand.py`, `nvrtc_inf.py`), each over the corpus. GPU use: one NVRTC compile of four 1-line kernels (`nvrtc_inf.py`, cupy 14.1.1, GTX 1050 Ti idle, no launch) and one import of `_batch_pair_mi_cuda_shared_fused` to read `num_regs`; no kernel was launched. clang-tidy ran on 10 kernel files (host tool, no GPU).
Not covered: sections 1-8, 10, 11 (dimension 54); the Hugging Face / tensormux claims of section 3 (not mine); I did not run any mlframe test.
Previous round: `audits/2026-11-11/` and `2026-11-10/` have no CUDA-rules dimension (only 50-F7 `launch_dtype_audit` and 52-S16 touched GPU gates); nothing here re-proposes a dispositioned item. `cuda_launch_geometry`, `cuda_coalesced_reads`, `cuda_kernel_integer_width`, `cuda_kernel_pointer_types`, `cuda_kernel_factories`, `cuda_kernel_names`, `cuda_kernel_sources`, `cuda_offline_report`, `gpu_timing_sync`, `launch_dtype_audit` were read for their docstrings.

## Task 1. Classification of every actionable statement

Legend: GATED = an existing py-ci-shared gate or an existing mlframe test already enforces it. STATIC = formalisable static gate. STRUCT = formalisable as test-structure / doc gate. NOT = not statically checkable (reason).

| # | Statement (section) | Class | Detail |
|---|---|---|---|
| 9.a | Survey, then full ncu profile, `--set full --import-source yes --clock-control boost`, `-k regex`, `-c 1` | NOT | a command recipe for a human on another machine; nothing in the repo to parse |
| 9.b | Name the clock-control setting; never `-G`; build with `-lineinfo` | NOT / STATIC-trivial | `-G`/`--device-debug` in an `options=` tuple is checkable (measured: 17 option strings in src, values `--fmad=false` x10, `-std=c++14` x3, `-D..` x3, `--maxrregcount=40` x1; zero `-G`, zero `-lineinfo`; 145 RawKernel/RawModule constructions, 19 with `options=`). Zero hits, little value: not proposed. `-lineinfo` is a profiling-session flag, not a production one |
| 9.c | ncu must launch the program itself, not a `uv run` wrapper | NOT | human recipe |
| 9.d | Six analysis dimensions, thresholds (waves < 1, long_scoreboard > 40 percent, ...) | NOT | counter-derived, needs `ncu`; third-party claims ("to check, not to trust"). Already honest in the doc |
| 9.e | Playbook a-O (signal, cause, fix) | NOT | counter signals. The code-visible half of K (spill) is GATED (offline per-arch test + `cuda_offline_report`), of L (unintended FP64) is GATED by `cuda_kernel_pointer_types` + the `{REAL}` test + `fp64_share` in the offline report (report, not gate) |
| 9.f | Metric names changed between versions | NOT | doc-only; a script (`scripts/ncu_key_metrics.py`) holds them, see Task 2 tooling |
| 9.g | Record gpu model, arch, ncu version, clock-control, achieved clock next to every figure; one profile record per kernel version | STRUCT | see rule `gpu_profile_records` below. Measured: of 148 records in `audits/2026-10-22/profiles/`, gpu model/arch named in 86, clock/MHz in 27, registers in 26 |
| 9.h | `ERR_NVGPUCTRPERM`, locale `bad conversion`, NaN under replay | NOT | environment troubleshooting |
| 9.i | "single-path allowances in test_ktc_dispatch_selects_more_than_one_path are the first thing to re-check on the newer gpu" | STRUCT | mlframe-local test already pins each allowance with a text reason; a generic form is a candidate (Task 2) |
| 12.1 | Mark profiling window with `profilerStart/Stop` | STATIC-trivial | measured: no file under src (non-benchmark) calls either (the scripts live in `_benchmarks/profiling`), so a balance rule has no corpus to protect. Not proposed |
| 12.2-12.4 | nsys / ncu command lines; read reports via `ncu_report` because text pages fail on ru-RU | NOT | human recipe; `scripts/ncu_key_metrics.py` exists in mlframe (tooling candidate, Task 2) |
| 12.5 | `compute-sanitizer --tool memcheck` after any change to indexing | NOT (process) | needs a gpu and a real test run; see Task 3 for a skip-loud runner |
| 12.6 | ncu duration is not latency; estimated speedups are hypotheses | NOT | honesty rule; the timing form is GATED by `gpu_timing_sync` |
| 12.7-12.8 | venv_autopsia has no cupy; one heavy gpu job at a time | NOT | environment |
| 13.1 | Change traversal before tuning launch | NOT | design advice |
| 13.2 | Read the profile for the limiter (FP64 pipe at 1/64 on Ada) | NOT statically; report GATED-AS-REPORT | `cuda_offline_report` prints `fp64_share`; not a gate |
| 13.3 | Sweep tiling on device with warm clocks; defaults are module constants | NOT | timing |
| 13.4 | Time a warmed back-to-back loop, report min | GATED | `gpu_timing_sync`, `single_shot_timing_assertion` |
| 13.5 | Put preparation on the device too | NOT | profile-derived |
| 13.6 | Shared memory is a budget: global-memory fallback above 40 KB, CPU fallback with a logged warning | STRUCT (dimension 54 owns the checklist form) | static form: a `shared_mem=` launch whose size is not compared with a device limit; not measured here, listed for 54 |
| 13.7 | a fallback can hide the bug it falls back from; assert what the fallback received; one deletion mutation | NOT | needs a mutation run; the mutation sweep (`_mutation_sweep`) is the generic tool, per-kernel usage is a consumer decision |
| 13.8 | nvrtc through cupy has no `INFINITY`; build it from the bit pattern | GATED | the offline per-arch test compiles every kernel through cupy's nvrtc binding and fails on it. **Doc is incomplete**: `nvrtc_inf.py` on cupy 14.1.1 shows `INFINITY`, `NAN` and `HUGE_VAL` all fail (1 error), only `__longlong_as_double(0x7FF0000000000000LL)` compiles. Corpus: 0 kernels name any of the three. a text rule would be a faster, toolkit-free duplicate; low value, not proposed |
| 13.9 | Persist crossover as one monotone threshold | NOT | ktc behaviour; mlframe test `test_ktc_dispatch_selects_more_than_one_path` pins the symptom |
| 13.10 | Profile the whole request again once the kernel is fast | NOT | process |
| 14.a | Pick widths from device limits, `fixed_shared_block_size` / `block_size_for` with the kernel's own `num_regs` | GATED | `cuda_launch_geometry` (`cuda-literal-block-size`, hard) |
| 14.b | Replace the three hard-coded `gpu_capability_summary(0)` calls | STATIC but **already done**; doc stale | corpus now calls `gpu_capability_summary(launch_device_id())` at 3 sites (`_batch_pair_mi_cuda_kernels.py:117`, `_batch_pair_mi_cuda_shared_fused.py:178`, `_gpu_pairs.py:114`); 0 literal device ids remain (see rule `cuda-literal-device-id`) |
| 14.c | Declare what the helper cannot know: pow2 for `s >>= 1` tree reductions; multiples of 32 for shuffles; `#define THREADS` needs a module per width | STATIC | rule set `cuda_tree_reduction` (pow2), measured below. Warp multiples: `block_size_for` candidates are all multiples of 32 by construction, 0 literal blocks that are not a multiple of 32 in the 28 shuffle kernels' files, so no gate. `#define THREADS`: 0 kernels |
| 14.d | Grid caps (`min(64, ...)`, `_STAT_BLOCKS = 64`, `min(4096, ...)`) should follow `sm_count x resident blocks x waves` | STATIC | rule `cuda-literal-grid-cap` in module `cuda_grid_cap`, measured below |
| 14.e | Keep the old cap behind an env var and run selection-equivalence tests | NOT | behavioural |
| 14.f | Register-limited kernels gain nothing from width; the lever is the register count | NOT | counter-derived; `--maxrregcount` measured with no gain (rule below handles the report side) |
| 14.g | Verify on a newer arch: run `launch_report`, equivalence tests, then profile and record in `profiles/<module>.md` | STRUCT | the record half is `gpu_profile_records` |
| 14.h | grep a full fit for `OutOfMemoryError` and `FE raised` | NOT | needs a fit |
| 15.a | Per-column device loops launching ~15 kernels each: batch them; `(k, n)` row-major block | NOT statically with usable precision | prototype below: 1 hit in 360 files, a false positive. a runtime helper (launch count independent of column count) needs a gpu |
| 15.b | Candidates `_fe_cmi_redundancy_gate`, `_fe_raw_redundancy_anchors`, `_fe_retention_subsumption` | NOT | work item for mlframe, not a gate |
| 16.a | Use `device_argsort/device_sort`, not `cp.argsort(axis=0)` above 80000 rows | STATIC (advisory) | rule `cuda-direct-device-sort`, measured below |
| 16.b | Overlap the host draw with device scoring; device call outside the numba guard | NOT (the guard half is GATED by `nested_numba_parallel_guard`) | the overlap is a timeline property |
| 16.c | Tile sized by `ceil(k / threads)` not a fixed 16 | NOT | needs the thread count at run time |
| 16.d | Fisher-Yates on device opt-in | NOT | decision record |
| 16.e | Measuring: compare walls inside one process, clear the fit cache | NOT | `single_shot_timing_assertion` covers the timing form |
| 17.a | clang-tidy over extracted kernels with the documented check set | tool | Task 3 design + measurement |
| 17.b | `bugprone-narrowing-conversions` on `blockIdx.x`/`threadIdx.x` is noise | STATIC-config | measured 24 of 29 warnings (83 percent) are exactly that; the tool must filter them by default |
| 17.c | compute-sanitizer memcheck/racecheck on selected tests; cuBLAS racecheck false positive | tool | Task 3 (runner + suppression list), gpu-only, skip loud |
| 17.d | Wiring: manual or periodic, not a pre-commit gate | NOT | a decision, correct |
| 18.a | nvprof/ncu need an elevated shell; counters in a fixed order | NOT | counters |
| 18.b | Single-precision rejection, exact value for survivors (DP efficiency > 25 percent) | NOT | counter-derived; the precondition (inputs exact in float32) is data |
| 18.c | `achieved_occupancy` with grid size; grid `8 * multiprocessors` blocks in all | STATIC | same as 14.d (cap rule) plus `cuda_launch_geometry`; `test_cuda_grid_fill` is the local model-based check (Task 2) |
| 18.d | `gld_efficiency` low is a defect for strided neighbours, not for a warp broadcast | GATED | `cuda_coalesced_reads` encodes exactly that distinction (strided row read vs group index vs broadcast) |
| 18.e | Do not trust low `shared_efficiency`; time the variant | NOT | |
| 18.f | Tried and rejected: `--maxrregcount` and private histogram copies | NOT | records |
| 18.g | Metric replay corrupts kernels that accumulate into their own output | NOT as a gate; STATIC as a tool hint | listing the kernels with a read-modify-write on a non-const pointer is possible (`atomicAdd(&out[...])`), but its only consumer is a profiling script; not proposed |
| 19.1 | 4-byte loads for byte-coded arrays; rows padded to a stride multiple of 4; a misaligned 4-byte load faults | STATIC (advisory) for the alignment contract; NOT for the performance claim | rule `cuda-wide-load-alignment`, measured below |
| 19.2 | Single-precision rejection | NOT | |
| 19.3 | Split a block-starved grid: `splits = ceil(4 * SMs / blocks)`, segments >= 4096 rows | STRUCT-ish | the SM-count model is `test_cuda_grid_fill`; static half is 14.d |
| 19.4 | Chunk length of a sequential walk per thread | NOT | |
| 19.5 | Process: rank by share, interleave, run contract + offline tests before every kernel-text commit | STRUCT | "run before every kernel-text commit" is a hook; mlframe's pre-commit is the right place, py-ci-shared could ship a hook id that runs a named test when a staged file contains `__global__` (see item 9 of the ranked list) |
| 19.6 | Do not trust a low `shared_efficiency` / `gld_efficiency` on broadcast | NOT / GATED | as 18.d, 18.e |
| 19.7 | `cupy.power(x, 2)` -> product (`small_power`) | STATIC (advisory) | rule `cuda-cupy-small-power`, measured below |
| 19.8 | Upload codes at their real width (`int8` when < 128) | NOT statically (value range); STATIC-advisory by name only | measured: 45 line hits by name `codes|bins|labels` + `astype(int32/int64)`; first five judged: 1 host-only numpy (false positive), 4 unclear without range. False-positive share above the 30 percent bar, no value-range knowledge: classify NOT. The runtime form (`launch_dtype_audit` extended with a "values fit a narrower pointer type" hint) is the honest home |
| 19.9 | Stage a data-dependent lookup table in shared memory | NOT | counter-derived (kernel time drops 85 percent without the lookup) |
| 19.10 | Pass the consumer the width the producer can write (`{CT}` placeholder) | NOT statically | cross-module dtype flow; `launch_dtype_audit` checks equality, not minimality |
| 19.11 | Fuse a chain of cupy elementwise ops where a fused kernel exists; `--fmad=false` for value equality | NOT (advisory catalogue at best) | measured: 3 functions with >= 8 cupy elementwise calls in the whole corpus, all 3 unclear. `--fmad=false` is present in 10 of 17 option strings; a "fused kernel compiled without `--fmad=false` while its test compares against a cupy chain" is not decidable |
| 19.12 | Index an elementwise kernel over the OUTPUT layout, rows fastest | partly GATED | `cuda_coalesced_reads` covers `thread * name` strides; the `i / ncols` division form is not covered. Measured: 1 gid-division site in 155 kernels (`hermite_fe/_nonpoly_cells_gpu.py`, divisor `UC`, a compile-time constant), so no corpus; extend `cuda_coalesced_reads` with the division form only if a defect appears |
| 2 (cross-ref) | Contract point 5: profile record `audits/.../profiles/<module>.md` per module | STRUCT | mlframe test checks it for the compliant list only; generic form `gpu_profile_records` below |
| launch_bounds | a `__launch_bounds__` / `--maxrregcount` change needs a register report | GATED + local | offline per-arch test asserts no spill and `launch_bounds_violations`; the literal-minBlocks test is mlframe-only (Task 2); `--maxrregcount` appears once (`_gpu_resident_select_f32_compact.py:256`, `--maxrregcount=40`, justified by the comment at :200 with the 48-register figure) |

Summary: of 62 actionable statements, 5 are GATED today, 7 STATIC rules are worth building (one of them a family), 3 STRUCT gates, the rest are not statically checkable. The doc itself says so for the counter-derived ones.

## Measured prototypes (all over the 155-source corpus, snapshot)

Common canary form: files are Python (`.canary`), the kernel in a string constant, exactly the shape of `tests/canary/cuda_launch_geometry/violation/seed.py.canary`.

#### Rule family A: `cuda_grid_cap` (`cuda-literal-grid-cap`, ADVISORY with baseline, marker `# grid-cap-ok: <reason>` on the launch statement or the constant)
Flags: a launch grid tuple (reuse `cuda_launch_geometry._is_launch/_launch_block`) holding `min(...)`/`max(...)` with an integer literal operand <= 1024 (hardware limits 65535, 2**31-1 exempt), or a module constant named `*BLOCKS*`/`*GRID_X*`/`MAX_BLOCKS` bound to an integer literal in 16..1024 that a launch grid references. Not flagged: the file derives the SM count (`multiProcessorCount`, `sm_count`, `_SM_COUNT`, `MULTIPROCESSOR_COUNT`) in the same grid expression. ADVISORY because CUDA.md 14.4 says a cap change reorders float partial sums (needs selection-equivalence tests), so a hard gate would force blind edits in 6 kernels.
Violation 1:
```python
import cupy as cp


def run(kernel, x, out, n, cb):
    t = 256
    kernel((min(64, (n + t - 1) // t), cb), (t,), (x, out, n))
```
Violation 2:
```python
import cupy as cp

_STAT_BLOCKS = 64


def run(kernel, x, out, n):
    kernel((_STAT_BLOCKS,), (128,), (x, out, n))
```
Clean 1:
```python
import cupy as cp


def run(kernel, x, out, n, sm_count):
    t = 256
    kernel((min(sm_count * 8, (n + t - 1) // t),), (t,), (x, out, n))
```
Clean 2:
```python
import cupy as cp

_MAX_GRID_Y = 65535
_GRID_POINTS = 120


def run(kernel, x, out, rows):
    kernel((min(_MAX_GRID_Y, rows),), (128,), (x, out))  # grid-cap-ok: hardware limit of gridDim.y
```
Measurement (`proto3.py`): 191 launch-shaped calls; before refinement 12 hits (7 launch grids, 5 constants). True: `_group_distance_gpu.py:169 min(grid_x, 64)`, `_quantile_codes_gpu.py:135,142,149 min(64, ...)`, `_ksg_cd_gpu.py:33 _BLOCKS_PER_COLUMN=64`, `hermite_fe/_als_fused_gpu.py:25 _STAT_BLOCKS=64` = 6 true; `_modular_codes_gpu.py:231` and `_quantile_codes_gpu.py:288` (cap 1024) unclear (1024 blocks fill 128 SMs x 8, so harmless on any current card); `_wavelet_legs_fused_gpu.py:114` (cap 4096) benign; false positives `_MAX_GRID_Y = 65535` (x2, hardware limit, removed by the 65535 exemption) and `_GRID_POINTS = 120` (a numerical grid, removed by the "referenced in a launch grid" requirement). After refinement: 6 true, 2 unclear (cap 1024 would be dropped if the threshold is 512; recommended threshold 512 gives 6 true / 0 unclear / 0 false). 5 files in the corpus reference an SM count at all, so the bulk of the grid logic is not SM-aware: the finding is real, not noise. First five judged: group_distance (TP), quantile 135/142/149 (TP, TP, TP), ksg (TP).

#### Rule family B: `cuda_tree_reduction` (HARD for the launch half when the binding resolves, ADVISORY for the kernel declaration half; marker `// pow2-ok: <how the host guarantees it>` in the kernel, `# pow2-ok:` on the launch)
Kernel half `cuda-tree-width-undeclared`: a halving loop `for (int s = <blockDim.x|nt|THREADS> >> 1 (or / 2); s > 0; s >>= 1)` in a kernel whose text carries no width contract (`(nt & (nt - 1))` guard, `__trap`, or the marker). Launch half `cuda-tree-launch-nonpow2`: `block_size_for(k, ...)` / `launch_1d(...)` without `pow2=True` (and not `fixed_shared_block_size`, whose default is `pow2=True`), where `k` resolves to a tree kernel.
Why it matters (demonstrated): `pyutilz.performance.gpu_hw_launch.block_size_for` defaults `pow2=False`. `cand.py` output on this host: for 91 registers it returns 96 / 224 / 224 for work 100 / 1000 / 100000, for 53 registers + 25000 B smem it returns 384; the simulated tree loop (`tree_real.py`) sums 224 ones to **128**, 384 ones to **256**, 96 ones to **64**, 768 ones to **512** (only 256 gives 256). A tree-reduction kernel launched at such a width silently drops terms. Real corpus today: mlframe routes its tree kernels through `_tree_block(...)` (`_fe_batched_mi_cmi_hist.py:175`, `pow2=True, max_width=256`) or `pow2=True` (9 files), so I found **no live defect**; the gate is a regression guard for a helper whose default is the unsafe one.
Violation 1 (kernel half):
```python
import cupy as cp

_SRC = r"""
extern "C" __global__ void rowsum(const double* x, double* out, int n) {
    __shared__ double sh[256];
    int tid = threadIdx.x, nt = blockDim.x;
    double v = 0.0;
    for (int i = tid; i < n; i += nt) v += x[i];
    sh[tid] = v;
    __syncthreads();
    for (int s = nt >> 1; s > 0; s >>= 1) { if (tid < s) sh[tid] += sh[tid + s]; __syncthreads(); }
    if (tid == 0) out[blockIdx.x] = sh[0];
}
"""
```
Violation 2 (launch half):
```python
from pyutilz.performance.gpu_hw_launch import block_size_for


def run(kernel, x, out, n):
    threads = block_size_for(kernel, work_items=n)
    kernel((1,), (threads,), (x, out, n))
```
(the launch half only fires when the module also holds a tree kernel bound to `kernel`; in the canary the binding is a `cp.RawKernel(_SRC, "rowsum")` assignment in the same file.)
Clean 1: the violation-1 kernel with `if (nt & (nt - 1)) { return; }  // pow2-ok: host launches pow2=True` as its first statement.
Clean 2: `threads = block_size_for(kernel, work_items=n, pow2=True, max_width=256)`.
Measurement (`proto6.py`, `proto5.py`): 155 sources, **18 tree kernels in 13 files** (the naive regex `>>= 1` gave 44 kernels, 26 of them warp-shuffle loops `off = 16; off >>= 1` -> false positives, rejected: the pattern must start the loop from a block-derived bound). 0 of 18 declare a width contract (all 18 undeclared, 18 real tree loops: first five `_cmi_null_strata_gpu::_SRC`, `::_SRC_ROWS`, `_dcor_gpu`, `_fe_batched_mi_cmi::_BATCHED_JOINT_ENTROPY_SRC`, `_CMI_JOINT_ENTROPIES_SRC` are all `for (int s = nt >> 1; ...)` over a 256-wide static scratch). The launch half, evaluated at file level, gave 9 `block_size_for` calls without `pow2=True` in the 13 tree files; judged by reading the callee: `joint_hist1/2/3` (4 calls) are atomic histograms, `_finish` / `_CMI_ASSEMBLE` are non-tree, so **9 of 9 are file-level false positives**. Hence the launch half is only shippable with kernel binding (`get_function("name")` / `RawKernel(src, "name")` resolved inside the module); that resolver is not built in the prototype (unmeasured). 64 percent (56 of 87) of launches in the tree files take their block from a variable, so a literal-only rule sees nothing (0 non-pow2 literals).

#### Rule family C: `cuda_wide_load_alignment` (`cuda-wide-load-alignment`, ADVISORY, marker `// align-ok: <reason>`)
Flags: a kernel that casts a pointer to a wider element (`(const unsigned int*)`, `reinterpret_cast<const int4*>`, `uint4`) and declares no pitch parameter (`*stride*`, `*pitch*`, `ld*`, `*_pad*`) and carries no marker. Reason: CUDA.md 19.1, a misaligned 4-byte load faults; the stride padding is a host-side contract that nothing records on the kernel side.
Violation 1:
```python
_SRC = r"""
extern "C" __global__ void cnt(const unsigned char* codes, const long long n, int* out) {
    const unsigned int* c4 = (const unsigned int*)(codes + (long long)blockIdx.x * n);
    out[blockIdx.x] = (int)c4[threadIdx.x];
}
"""
```
Violation 2: same with `reinterpret_cast<const int4*>(counts + (long long)row * M)` and no stride argument.
Clean 1: the violation-1 kernel with an added `const long long stride` argument used as `codes + (long long)blockIdx.x * stride`.
Clean 2: violation-1 with `// align-ok: host pads rows to a multiple of 4 (n_pad)` on the cast line.
Measurement (`proto9.py`): 155 sources, **11 kernels with wide casts**, 3 carry a `stride` parameter (clean), 8 flagged. Judged on the text I read: `_batch_pair_mi_cuda_fm` `cm + a * n_pad` (true negative: pitch is named `n_pad`; the name list must include `pad`), `_honest_null_k4/_device` cast `ybits` (bit-packed word array already uint32, not a byte array: false positive), `_fe_batched_mi_cmi_lut` `int4` cast of `counts + row * M` (unclear: needs `M % 4 == 0`, enforced elsewhere?), `_eng_dedup_spearman_gpu` `fbits[k]` (bit words, false positive), `_fe_batched_mi_cmi_hist::_JOINT_HIST_SRC` (no host pad evidence, unclear). **0 confirmed defects, at least 3 of the first 5 false positives**: above the 30 percent bar, so ADVISORY only and only with a byte-pointer requirement (cast source type `char`), which would leave about 3 hits. Value: low-medium; build only after the cheaper rules.

#### Rule: `cuda-literal-device-id` (HARD, added to `cuda_launch_geometry`, marker `# device-ok: <reason>`)
Flags `gpu_capability_summary(<int literal>)`, `Device(<int>)`, `getDeviceProperties(<int>)`, `set_device(<int>)`, `cuda.select_device(<int>)`. Measured: **0 hits** in src (the three sites 14.b names now pass `launch_device_id()`); my broad first regex also matched `get_current_device()` (2 sites, not literal), so the rule is literal-only. A zero-hit rule needs canaries to prove it fires.
Violation 1: `summary = gpu_capability_summary(0)`. Violation 2: `with cp.cuda.Device(0): pass`. Clean 1: `summary = gpu_capability_summary(launch_device_id())`. Clean 2: `cp.cuda.Device(0).use()  # device-ok: single-GPU bench script`. Value: low (regression guard); implement inside `cuda_launch_geometry` (the same AST pass), effort S.

#### Rule: `cuda-direct-device-sort` (ADVISORY, module `cuda_device_sort`, marker `# sort-ok: <reason>`)
Flags `cp|cupy|xp .sort/.argsort(<expr>, axis=0)` (explicit axis 0 on a presumably multi-dimensional array) outside configured wrapper modules / functions (`safe_wrappers=("device_argsort","device_sort")`, the wrapper's own file exempt). The 80000-row threshold is not knowable statically, which is why this is advisory.
Violation 1: `order = cp.argsort(block, axis=0)`. Violation 2: `table = cupy.sort(table, axis=0)`. Clean 1: `order = device_argsort(block, axis=0)`. Clean 2: `s = cp.sort(vec)  # 1-D: per-vector path already` (1-D without axis is not flagged). 
Measurement (`proto3.py`): 9 line hits for `cp|cupy|xp.(arg)sort(`; by hand: 2 are the wrapper itself, 2 are docstring/comment text, 1 `cp.sort(key.ravel())` 1-D (clean), 1 `cp.argsort(z_d)` 1-D (clean), 1 `cp.sort(s, axis=1)` (axis 1 is the contiguous direction, clean per 15), **1 `axis=0` hit `_fe_batched_qbin.py:113` `cp.sort(table[1:], axis=0)`: a 21-row edge table per column, i.e. FAR below 80000 rows -> a size false positive** (0 true of 1). Zero live defects: the rule is regression prevention only.

#### Rule: `cuda-cupy-small-power` (ADVISORY, marker `# power-ok:`)
Flags `cp|cupy|xp.power(x, <literal 2|3|-1|-2|-3>)`. Measured: 12 `.power(` sites, **0** with those exponents (10 non-integer exponents / array exponents, which the doc itself says keep `power`; 2 wrapper lines). The doc's own measurement says the wall time did not change (33.1 -> 33.1 ms) and the gain is 4.3 ms of GPU time and rounding exactness, so ADVISORY. `x ** 2` on a cupy array is not typed statically and is not flagged. Violation 1: `y = cp.power(x, 2)`. Violation 2: `y = xp.power(x, -3)`. Clean 1: `y = small_power(x, 2)`. Clean 2: `y = cp.power(x, 0.5)`. Value: low; effort S; do last.

#### Rule: `gpu_profile_records` (STRUCT, HARD with a pending baseline; module name `gpu_profile_records`, no marker, the baseline is the allowance)
Spec: `assert_gpu_profile_records(kernel_root, records_dir, *, required: Mapping[str, str], family_map=None, baseline_path=...)`. Every module containing a `__global__` source (via `find_cuda_kernel_sources`) must have `records_dir/<module stem>.md` (or be a child in `family_map`), and each record must match every named regex (default set from `fe_gpu_coverage_inventory.contract_violations`: `cProfile`, `nsys|ncu|nvprof`, `launches? per`, `occupancy`, `throughput|utilization|bandwidth`, plus the CUDA.md section 9 provenance set: GPU model or `sm_NN`, `ncu --version`, clock). A record that exists but misses a field is a finding; a stale record (module deleted) is a finding.
Violation (directory fixture): `records/_foo_gpu.md` containing only `cProfile top 20 ...` while `_foo_gpu.py` holds a kernel. Violation 2: kernel module with no record. Clean 1: record containing all five tokens. Clean 2: child module listed in `family_map` whose parent has the record.
Measurement (`proto7.py`): 148 records; field prevalence: cProfile 80, GPU profiler 88, `launches per` 64, occupancy 78, throughput 80, GPU model/arch 86, clock 27, registers 26. 91 kernel-bearing files, **48 have no record of their own stem** (carved families and dispatcher-covered modules: mlframe keeps `CARVED_FAMILIES`, 100 lines, for exactly this, so `family_map` is mandatory in the API). With the five mlframe regexes a baseline of failing modules is needed; with the provenance fields 122 of 148 records fail, so provenance fields must be ADVISORY (applied to records written after the gate lands). First five unrecorded stems (`_abs_corr_tiled_gpu`, `_added_information_gpu`, `_als_fused_gpu`, `_als_kernels_gpu`, `_cat_pair_joint_gpu`): judged **unclear** (family or dispatcher coverage not decidable without `CARVED_FAMILIES`), which is the point of the mapping.

#### Not formalised (measured and rejected)
* Per-column device loop (15): `proto3.py`, loops over `columns|cols|range(ncols)` with >= 3 device ops: 1 hit in 360 files, `_parity_gpu.py:102 for c0 in range(0, len(columns), COLUMN_BLOCK)` = column-block streaming (false positive). 0 of 1.
* Elementwise chains (19.11): 3 functions with >= 8 cupy elementwise calls (`_binned_numeric_agg_resident._get_fused_stats` 13, `_gpu_resident_basis._gpu_basis_preprocess_batched` 12, `_hinge_detect_gpu_resident_batch._batched_precheck_gpu` 10), all unclear without profile data.
* Byte-coded loads in four separate reads (19.1 item as the task phrased it): the four-loads pattern `X[i], X[i+1], X[i+2], X[i+3]` on a byte pointer: **0 hits in 155 kernels** (the 3 hits of the first draft were `sums`, `tasks`, `stats` arrays, non-byte). 37 kernels have byte pointers; 7 already use 4-byte cast loads. The slow-by-counter claim (58 to 73 percent load efficiency) is not visible in source; classified NOT.
* `--maxrregcount` / `__launch_bounds__` without a register report: GATED by the offline test; 12 kernels have `__launch_bounds__`, **0 with a literal minBlocks** (the mlframe local test passes by construction).
* Literal block not a multiple of 32 with shuffles: 0.

## Task 2. mlframe-only CUDA/GPU/KTC meta-tests: centralisation

| mlframe test | project-specific | generic | proposed API in py_ci_shared | effort | benefit | verdict |
|---|---|---|---|---|---|---|
| `test_cuda_grid_fill.py` (69 lines) | imports `_cmi_null_strata_gpu`, `_family_maxt_cuda_bits`, `_fe_batched_mi_cmi_hist` and monkeypatches `_SM_COUNT`; the grid models are the launchers' own formulas | the pattern "grid size >= 2 x SMs or all the shape can supply, for SMs in (6, 24) and a shape matrix" | `assert_grid_fills_device(grid_fn, shapes, *, sms=(6, 24), blocks_per_sm=2, supply_fn=None)` returning the failing `(shape, sms, blocks)` tuples; a 15-line pure function | S | low-medium: the formula must still be written per launcher, the helper only saves the loop and the message. Not a static gate; the generic part is too thin | MOVE only as a small helper, after the static `cuda_grid_cap` |
| `test_fe_gpu_streaming_contract.py` + `_fe_gpu_contract.json` + `fe_gpu_coverage_inventory.py` (475 lines) | the whole contract (CPU twin, streaming, crit dtype, ktc, profile record), `CARVED_FAMILIES`, `KTC_DISPATCHERS`, `CPU_TWINS` dictionaries, module discovery from the MRMR import graph | the ratchet shape (compliant / exempt / pending, pending never grows, promotion test, every exemption has a reason longer than N chars) and the profile-record regex check | `gpu_contract_ratchet`: `assert_contract_ratchet(registry_path, modules, violations_fn)` + `gpu_profile_records` above. `violations_fn` stays the consumer's | M | medium: the same ratchet exists for other lists (the repo already has `Baseline`); the new part is only the three-state registry. The profile-record half is the useful export | MOVE the profile-record half only |
| `test_gpu_optout_makes_no_device_allocations.py` (152 lines) | the fit it runs, the probe-frame allowlist (`_disable_broken_cupy`, `numba_cuda_can_compile`), `MLFRAME_DISABLE_GPU` | the allocation counter fixture: hooks `cupy.cuda.set_allocator` and numba device arrays, walks `sys._getframe` for probe frames (the doc in the file says `traceback.extract_stack` once made it a multi-hour hang) | `device_alloc_counter(probe_frames=...)` context manager in a new `gpu_optout` module, plus `assert_no_device_allocations(fn, *, env=..., probe_frames=...)` | M | **high**: the failure is silent (a path routed to the gpu under an opt-out costs 37 percent of a fit), every gpu consumer (autopsia, pyutilz, llm_bench) has an opt-out contract, and the hook is hard to get right (the frame walk fix). Needs cupy to count, so it must skip loud without it | MOVE (best centralisation candidate) |
| `test_ktc_dispatch_selects_more_than_one_path.py` (80 lines) | the `discover_tuners("mlframe.feature_selection.filters")` call, `SINGLE_PATH_BY_MEASUREMENT`, the pair-residual sweep test | the logic "for every gpu-capable tuned kernel with measured regions, the set of `backend_choice` has >= 2 values unless listed with a reason" | `assert_tuned_kernels_use_more_than_one_path(registry, cache, *, allow, backend_names)` taking duck-typed registry and cache (no pyutilz import) | S | medium: needs a measured cache on the host, so it is a host-bound check; value is the dropped-variant detection (the 1e-8 vs 1e-9 incident) | MOVE as a duck-typed helper |
| `test_ktc_sweep_tolerances.py` (91 lines) | `sweep_backend_grid` is pyutilz API; `_MAX_TOLERANCE = 1e-6`; the exempt path fragments | an AST gate: a call with kwarg `equiv_rtol` / `equiv_atol` bound to a literal above a cap | `assert_sweep_tolerance_cap(root, *, call_names=("sweep_backend_grid",), kwarg_names=("equiv_rtol","equiv_atol"), max_value=1e-6, allow=...)` using `scan_python` + `Baseline`; marker `# tol-ok: <reason>` | S | medium-high: it is a pure static gate, cheap, and it protects a decision that locks a backend per host. It reads like a sibling of the existing `kernel_tuning` heuristics | MOVE (easy win). Note the local test re-implements `_enclosing_function` O(n^2) walks and its own `parsed_ast` cache; the shared form would use `_core` |
| `test_cuda_kernel_register_pressure.py` (247 lines) | the `_REGISTRY` of 17+ factories and `_SPILL_EXEMPT` | compile each factory per dtype with cupy, assert `local_size_bytes == 0`, skip loud with no device; the registry completeness half is already `cuda_kernel_factories` | `assert_no_register_spills(registry, *, exempt, dtypes)` taking `(module, attr, dtypes)` tuples | M | medium: needs a gpu, and the offline per-arch ptxas test (which needs no gpu) already checks spills for every extracted kernel on 7 architectures, so this test is largely redundant with `test_cuda_kernels_offline_per_arch`; keep local | KEEP LOCAL unless a consumer without ptxas needs it |
| `test_cuda_kernels_offline_per_arch.py` (290 lines) + `_offline_kernel_report.py` (re-exports `py_ci_shared.cuda_offline_report`) + `_offline_kernel_sources.py` (204 lines of recipes) | the recipe table (placeholders and `-D` options per kernel), `_SPILL_EXEMPT` (6 entries with long reasons), `_NEWER_ARCH_OK`, `MLFRAME_OFFLINE_*` env names | the engine is already shared. Still local and generic: the test BODY (`_check_variant`: one resource block per entry, spills in entries and `__noinline__` helpers, warning allowlist, `launch_bounds_violations`), `_skip_or_fail` with a REQUIRE env, `_archs()` parsing, `test_every_file_with_a_kernel_yields_a_variant` (this overlaps `cuda_kernel_names`), the literal-minBlocks test, the occupancy arithmetic cases | `assert_variant_assembles(variant, arch, ptxas, workdir, *, spill_exempt, warning_allowlist)` returning entry names, and `assert_no_literal_min_blocks(sources, *, fits_every_arch=...)` (the second one is the clearest local-only generic rule; it duplicates the kernel-text scan of `launch_bounds`) | M | **high** for the body (any consumer with a ptxas copies 150 lines today; the engine without the body is half a gate), medium for the literal-minBlocks check | MOVE the body and the minBlocks check; the recipe table and exemptions stay |
| `_offline_kernel_sources.py` placeholder fill table | per-kernel fills | nothing | none, but Task 3's tool must accept the consumer's variants rather than re-substituting (see `_orthogonal_xi_gpu` error below) | | | KEEP |

Tooling that lives only in mlframe scripts: `scripts/ncu_key_metrics.py` (ncu_report reader), `_benchmarks/profiling/{nvprof_fit,nvprof_autotag,nvprof_range_summary,rss_scale_fit,tick_utilization,launch_report}.py`. `ncu_key_metrics` is generic (any cupy project on a host with Nsight Compute 2025.x), small, and fixes the ru-RU `bad conversion` trap; it is a reasonable `py-ci-shared tool ncu_key_metrics` addition (effort S, benefit medium for the autopsia/pyutilz GPU work, but it is a profiling aid not a gate).

## Task 3. A clang-tidy / compute-sanitizer tool

Host check: `where clang-tidy` finds nothing on PATH, but `C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\Llvm\x64\bin\clang-tidy.exe` exists: **LLVM 20.1.8** (the doc verified 18.1.8). CUDA toolkit 12.9 at `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9` (`CUDA_PATH` set). So the tool must search PATH, `CLANG_TIDY`, then VS `Llvm\x64\bin`, and report which it used.

#### Design: `py_ci_shared.cuda_clang_tidy` (library + `py-ci-shared tool cuda_clang_tidy`)
* Input: either a source root (reuses `find_cuda_kernel_sources`, `exclude=("_benchmarks","kernel_prototypes")`) or, better, an explicit `variants: Mapping[str, str]` (key -> already concrete text), which is what mlframe's `_offline_kernel_sources.all_variants()` produces.
* Placeholder filling: default map `{REAL}->float`, `{ACC}->double`, `%THREADS%/%BLOCK%->128`; any other `{NAME}`/`%NAME%` or `{__FSTRING__}` left is **reported as unresolved and the kernel skipped loudly**, never replaced by `1`. Reason (measured below): my naive fallback of `1` produced `const 1* cols` and a hard clang error on `_orthogonal_xi_gpu.py::_SRC`, whose kernel also uses `u64` typedefs defined elsewhere. Reusing the consumer's variant table removes this class.
* Output: `.cu` files named `<flattened path>__<NAME>.cu` in a temp dir (`--keep` to retain), a JSON/markdown report, a `Baseline` for known findings keyed by file, kernel name, check id and normalised source line (no line numbers; re-keying on text, as `Baseline` already does).
* Invocation per file exactly as CUDA.md 17: `--checks="-*,bugprone-narrowing-conversions,bugprone-integer-division,bugprone-sizeof-expression,clang-analyzer-deadcode.DeadStores,clang-analyzer-core.*,bugprone-suspicious-missing-comma,bugprone-signed-char-misuse"`, `--header-filter="^$"`, `-- --cuda-gpu-arch=sm_61 -x cuda --cuda-path=<root> -w`; `--arch` configurable.
* Noise filter (default on, from section 17 and measured): drop `bugprone-narrowing-conversions` whose line assigns `blockIdx|threadIdx|blockDim|gridDim` members.
* Skips LOUDLY: no clang-tidy, no CUDA path, or a clang error on the file -> a SKIPPED/ERROR row in the report and exit code 3 (`REQUIRE_CLANG_TIDY=1` makes it exit 1); never exit 0 with an empty result. Parallelism with a `ProcessPoolExecutor` bounded by the budget helper (clang-tidy is CPU bound).
* compute-sanitizer: `--sanitizer memcheck|racecheck` wraps `compute-sanitizer(.bat) --tool X python -I -m pytest <tests> -x -s --no-cov -p no:cacheprovider`, requires a device (`cupy.cuda.runtime.getDeviceCount()`), includes a suppression file for the known cuBLAS `sgemm_*` racecheck false positives, parses `ERROR SUMMARY: N errors`, skips loudly with no device. Not a CI gate (CUDA.md 17 is right), a pre-release command.
* Not a pytest gate by default: `assert_clang_tidy_clean(...)` for a consumer's slow/nightly marker.

#### Measurement on 10 mlframe kernels (`tidy_proto.py`, `tidy_cat.py`, every 15th source)
Each file: 5.2 to 13.9 s wall, **total 80.1 s for 10 files (mean 8.0 s)**; the first call on a cold start was ~15 s. Extrapolation: 155 kernels ~ 21 min sequential, ~6 min on 4 workers. Results with the doc's check set: **29 warnings** over 9 clean files and **1 file with a hard error** (the unresolved-placeholder file above, 9 errors). Breakdown: 24 `bugprone-narrowing-conversions` of `blockIdx/threadIdx` to `int` (**83 percent, the documented noise**), 3 `narrowing double -> float` which are artefacts of my `{ACC}->double` substitution against `1.0f` literals (the kernel is correct under its real `{ACC}`), 2 `bugprone-signed-char-misuse` on `residual_rescue_gpu::_SCORE_SRC` `const int b = q[i];` with `signed char* q` (the 19.8 narrow-code kernel; benign while codes are < 128, which the host guarantees). After the default filter 5 remain, 0 are defects: consistent with the doc ("rarely fires"). The two real finds the doc cites (`_lof_gpu` uninitialised read, dead stores) are in `clang-analyzer-*`, none of which fired in this 10-kernel sample; the value of the tool is the periodic full pass, not a per-commit gate. clang 20 vs the doc's 18: no check was renamed in the set used.

## Findings (each is one ranked addition; ranking is by evidence / benefit / effort)

## 55-T1 (P2) No tool runs the documented clang-tidy extraction pass; the pass exists only as scratch scripts
- **Evidence:** CUDA.md 17 ("kept in the session scratchpad"); `where clang-tidy` returns nothing, yet the host has LLVM 20.1.8 under Visual Studio BuildTools and CUDA 12.9; `tidy_proto.py` ran 10 kernels in 80 s, 1 failed on an unresolved placeholder (`const 1* cols`), 24 of 29 warnings were the documented `blockIdx` noise. Section 17 attributes two real latent bugs (uninitialised read in `lof_scores`, dead stores) to this pass.
- **Impact:** the most productive static check the doc records cannot be repeated by anyone but the session that wrote it; it is also silently stale (clang-tidy 18 -> 20).
- **Proposed fix:** new `cuda_clang_tidy` module and `py-ci-shared tool` entry as designed above; consumes `find_cuda_kernel_sources` or the consumer's concrete variants; exit 3 when unavailable. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new `cuda_clang_tidy` runner (skips loudly without clang-tidy, `--require` makes absence a failure, unresolved placeholders are reported).

## 55-T2 (P2) `block_size_for` defaults to non-power-of-two widths and nothing flags a tree-reduction kernel launched with it
- **Evidence:** `pyutilz/performance/gpu_hw_launch.py:265` `pow2: bool = False`; `cand.py` returns 96, 224, 384 for realistic register/shared-memory pairs (output in section "Rule family B"); `tree_real.py`: the `s = nt >> 1; s > 0; s >>= 1` loop sums 224 ones to 128 and 768 ones to 512. Corpus: 18 tree kernels in 13 files, 0 declare a width contract; mlframe avoids the bug today through `_tree_block` / `pow2=True` (9 files), so no live defect was found.
- **Impact:** a future edit that swaps `_tree_block` for `block_size_for` gives wrong sums on cards where the occupancy rule picks a non-pow2 width, with passing tests on the dev card. CUDA.md 14.3 lists this as a manual declaration "the helper cannot know".
- **Proposed fix:** `cuda_tree_reduction` (kernel half ADVISORY from day one, launch half HARD only with binding resolution; file-level matching gave 9/9 false positives and must not ship). Canaries as above. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new narrow gate `cuda_tree_reduction_block` (a kernel bound by name inside one module); the file-level form was not shipped because it gave 9 of 9 false positives.

## 55-T3 (P2) Launch grid caps tuned on a 6-SM card are not gated
- **Evidence:** `proto3.py`: `_quantile_codes_gpu.py:135,142,149 min(64, ...)`, `_group_distance_gpu.py:169 min(grid_x, 64)`, `_ksg_cd_gpu.py:33 _BLOCKS_PER_COLUMN = 64`, `hermite_fe/_als_fused_gpu.py:25 _STAT_BLOCKS = 64`; only 5 of 360 GPU files reference an SM count; `cuda_launch_geometry` covers the block, not the grid.
- **Impact:** on a 100-SM card those launches use 64 blocks (under 1 wave); CUDA.md 14 and 18.c/19.3 measured 3.4x (`tail_counts`) for the same class.
- **Proposed fix:** `cuda_grid_cap` ADVISORY with a baseline (CUDA.md 14.4: a cap change reorders float sums, so no blind hard gate). Threshold 512 gives 6 true, 0 false in the corpus. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new gate `cuda_grid_caps`; 12 true hits on mlframe.

## 55-T4 (P2) The allocation-counting opt-out harness is local to mlframe
- **Evidence:** `tests/test_meta/test_gpu_optout_makes_no_device_allocations.py:1-60`: a hook on `cupy.cuda.set_allocator` and numba device arrays plus a frame-walk probe allowlist; the file's own comment records a multi-hour hang from `traceback.extract_stack`. The incident (37 percent of a 300k fit in cupy argsort under an opt-out) is generic to every GPU consumer.
- **Impact:** autopsia, pyutilz, llm_bench would each re-derive the hook and the hang.
- **Proposed fix:** `gpu_optout.assert_no_device_allocations(fn, probe_frames=...)` / a context manager; skip loud without cupy. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new `cuda_optout_allocations` (allocation counter and opt-out environment check).

## 55-T5 (P2) The body of the offline per-arch test and the literal-minBlocks check are local although the engine is shared
- **Evidence:** `test_cuda_kernels_offline_per_arch.py` (290 lines) re-implements ptxas lookup (`CUDA_PATH`, PATH), the REQUIRE-env skip/fail, `_check_variant`, and `test_no_kernel_hardcodes_the_min_blocks_of_launch_bounds`, while `cuda_offline_report` holds only the engine. Corpus: 12 kernels with `__launch_bounds__`, 0 literal minBlocks (the check has no corpus failures; it needs the canary).
- **Impact:** a second consumer with ptxas must copy 150 lines; `cuda_kernel_names` already needs the same variants mapping.
- **Proposed fix:** `cuda_offline_report.assert_variant_assembles(...)` and `assert_no_literal_min_blocks(sources)`; both ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new `cuda_offline_arch` (the offline per-arch glue) and `cuda_launch_bounds_literal`; agrees with the mlframe original (0 hits).

## 55-T6 (P2) KTC sweep tolerance gate is a pure static check that lives only in mlframe
- **Evidence:** `test_ktc_sweep_tolerances.py`: AST scan of `sweep_backend_grid(... equiv_rtol=, equiv_atol=)` above 1e-6; reimplements the parse cache and the enclosing-function walk.
- **Impact:** a loose tolerance can crown a divergent backend per host (the pair-residual incident named in `test_ktc_dispatch_selects_more_than_one_path`).
- **Proposed fix:** `assert_sweep_tolerance_cap(root, call_names, kwarg_names, max_value, allow)` via `scan_python` and `Baseline`. ADDITIVE; effort S.
- **Disposition:** RESOLVED (62f3f61): new gate `ktc_sweep_tolerances` (literal tolerances only; a computed tolerance cannot be judged statically); flags the same two sweeps as the mlframe original.

## 55-T7 (P3) No generic gate for the profile record the CUDA contract requires
- **Evidence:** CUDA.md 2.5 and 9 ("record GPU model, architecture, ncu version, clock next to every figure") versus `fe_gpu_coverage_inventory.contract_violations` (mlframe-only regexes, five fields) and `audits/2026-10-22/profiles/` (148 records: GPU model in 86, clock in 27, registers in 26; 48 of 91 kernel-bearing stems without an own record). 
- **Impact:** provenance fields are the doc's own rule for comparing machines and 82 percent of existing records do not state a clock.
- **Proposed fix:** `gpu_profile_records` with a mandatory `family_map` and provenance fields ADVISORY. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): covered by `cuda_profile_records`.

## 55-T8 (P3) Wide-load alignment contract (CUDA.md 19.1) is not recorded on the kernel
- **Evidence:** 11 kernels cast to wider element types, 3 declare a stride parameter; the doc states a misaligned 4-byte load faults. Precision of the prototype is low (>= 3 of the first 5 flags are bit-word arrays or already padded under another name).
- **Proposed fix:** `cuda_wide_load_alignment` ADVISORY restricted to byte-pointer casts and a name list `stride|pitch|ld|pad`. ADDITIVE; build after T1-T6.
- **Disposition:** BUILT, HARD (commit adfd4ab): `cuda_wide_load_alignment`, narrowed to byte-array wide loads that must declare their padded pitch; built per the owner's decision.

## 55-T9 (P3) Doc staleness and disagreements inside CUDA.md (relay to mlframe, nothing to fix here)
- **Evidence:** (a) section 14 "Recommendations 2" says three `gpu_capability_summary(0)` calls remain; the corpus has `launch_device_id()` at all three. (b) section 14 names `filters/_gpu_hw_launch.py`; the helper is `pyutilz/performance/gpu_hw_launch.py` (imported in 155 call sites). (c) sections 10.9 and 13.8 say NVRTC lacks `INFINITY`; `nvrtc_inf.py` shows `NAN` and `HUGE_VAL` fail too (cupy 14.1.1, CUDA 12.9), only the bit-pattern builds. (d) the brief and the doc count ~85 kernels; `find_cuda_kernel_sources` counts 155 sources in 91 files (103 files contain the text `__global__`, including benchmarks). (e) section 17 says clang-tidy 18.1.8 verified; the host has 20.1.8. (f) section 18 says CUDA 12.8 `nvprof`; Windows toolkits 12.x ship `nvprof`? not verified, not claimed.
- **Disposition:** RESOLVED in mlframe `CUDA.md` (commit ab09d37a4 and later): the three `gpu_capability_summary(0)` mentions and the `_gpu_hw_launch` path are corrected to `launch_device_id()` and `pyutilz/performance/gpu_hw_launch.py`, and the NVRTC macro statement is updated. Checked by grep.

## 55-T10 (P3) Cheap regression guards that need canaries because the corpus has zero hits
- **Evidence:** `cuda-literal-device-id` (0 hits), `cuda-cupy-small-power` (0 hits), `cuda-direct-device-sort` (1 axis=0 hit, a size false positive), four-load byte pattern (0 hits in 155).
- **Proposed fix:** fold `cuda-literal-device-id` into `cuda_launch_geometry`; `cuda-direct-device-sort` and `cuda-cupy-small-power` as one advisory module `cuda_device_idioms` with two rule ids; all advisory, canaries as in the snippets above.
- **Disposition:** BUILT, HARD (commit adfd4ab): `cuda_device_idioms` with the three rules (literal device id, cupy small-integer power, direct axis-0 device sort), canaries and tests; built per the owner's decision.

## Ranked list of the ten best additions

| rank | addition | evidence for the rank |
|---|---|---|
| 1 | `cuda_clang_tidy` tool (T1) | doc credits it with real bugs; measured working on this host in 8 s/kernel; the only item that finds new defect classes (uninitialised reads, dead stores) rather than guarding a known one; removes a single-person scratch dependency |
| 2 | `gpu_optout` allocation harness (T4) | silent failure with a measured 37 percent fit cost, generic to every gpu consumer, the implementation has a documented trap (frame walk) |
| 3 | `assert_variant_assembles` + `assert_no_literal_min_blocks` in `cuda_offline_report` (T5) | removes ~150 copied lines per consumer; highest reuse of the existing shared engine; no gpu needed |
| 4 | `cuda_tree_reduction` (T2) | demonstrated wrong sums (224 -> 128) from the helper's own default; 18 tree kernels, none declared; value is regression protection of a silent wrong-answer class |
| 5 | `cuda_grid_cap` (T3) | 6 true hits, 0 false after refinement; the documented class has a measured 3.4x; new coverage, not a duplicate |
| 6 | `assert_sweep_tolerance_cap` (T6) | trivial effort, pure static, protects a per-host backend decision |
| 7 | `gpu_profile_records` (T7) | the doc's own contract; measured 82 percent of records lack a clock; needs `family_map` to be usable |
| 8 | `assert_tuned_kernels_use_more_than_one_path` duck-typed helper (Task 2) | catches a dropped variant (1e-8 vs 1e-9 incident) but is host-bound |
| 9 | Hook id that runs a named test when a staged file contains `__global__` (19.5 "before every kernel-text commit") | the doc names two tests that caught real regressions (bare `float` in a comment, unused `stride4`); cheap in `pre-commit`; no corpus measurement possible |
| 10 | `cuda_device_idioms` (device id, direct sort, small power) + `cuda_wide_load_alignment` advisories (T8, T10) | all zero-live-defect regression guards; ordered last because they protect fixed findings |

## Considered and not raised

* **Four byte loads in the kernel (task item "byte-coded matrix read in four separate loads")**: 0 of 155 kernels match when the array must be a byte pointer; the 3 raw hits (`sums`, `tasks`, `stats`) are `double`/`int` arrays. Performance claim is counter-only.
* **Upload as int32/int64 where the values fit a byte**: 45 name-based hits, at least 1 of the first 5 is host-only numpy and the rest need value ranges; false-positive share above 30 percent; not statically decidable. Runtime hint in `launch_dtype_audit` is the right home (not raised as a finding; noted at 19.8).
* **Per-column device loop gate**: 1 hit in 360 files, a false positive (column-block streaming).
* **Chain of cupy elementwise ops**: 3 functions in the corpus, all unclear.
* **`--maxrregcount` / `__launch_bounds__` needs a register report**: already enforced by the offline per-arch test (`launch_bounds_violations`, spills) and the local literal-minBlocks test; `--maxrregcount` occurs once, justified in a comment.
* **Warp-multiple width for shuffles**: `block_size_for` candidates are multiples of 32 by construction (`cand.py` lists 32..1024 step 32), and no literal block violates it in the 28 shuffle kernels' files.
* **`INFINITY` text rule**: the offline NVRTC test already catches it; corpus has 0 uses of `INFINITY|NAN|HUGE_VAL`.
* **profilerStart/Stop balance**: no file in src calls either; the profiling scripts are under `_benchmarks/` (and the scratch `D:/Temp/exp`).
* **`test_cuda_kernel_register_pressure` to py-ci-shared**: largely redundant with the offline per-arch test (no GPU, 7 architectures); kept local.
* **`test_cuda_grid_fill` as a gate**: the generic part is a 15-line loop; the models are the launchers' formulas.
* **A tree-reduction rule matching `>>= 1`**: rejected, 26 of 44 matches are warp-shuffle loops (`off = 16`); the final pattern starts the loop from a block-derived bound.
* **A file-level launch rule for non-pow2 widths**: 9 of 9 false positives (the width helper calls belong to histogram or finish kernels in the same file); needs kernel binding.
* **Tool exit when clang-tidy missing**: rejected "silent pass"; the tool exits 3 and prints the search path it tried.

| id | sev | title |
|---|---|---|
| 55-T1 | P2 | No tool runs the documented clang-tidy extraction pass (scratch only) |
| 55-T2 | P2 | `block_size_for` default pow2=False; tree-reduction kernels unguarded (wrong sums demonstrated) |
| 55-T3 | P2 | Launch grid caps tuned on a 6-SM card are not gated |
| 55-T4 | P2 | Opt-out allocation-counting harness is mlframe-local |
| 55-T5 | P2 | Offline per-arch test body and literal-minBlocks check are mlframe-local |
| 55-T6 | P2 | ktc sweep tolerance gate is mlframe-local |
| 55-T7 | P3 | No generic gate for the gpu profile record |
| 55-T8 | P3 | Wide-load alignment contract not recorded on the kernel |
| 55-T9 | P3 | cuda.md staleness and internal disagreements |
| 55-T10 | P3 | Zero-hit regression guards need canaries (device id, direct sort, small power) |
