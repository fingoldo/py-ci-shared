# 54 cuda_rules_sections_1_8: round 2026-11-16 (T)

Formalising the rules of `C:\Users\Admin\Machine learning\mlframe\CUDA.md` sections 1-8, 10 and 11 (section 9 and 12-19 are dimension 55; the review of the existing
gates themselves is dimension 53).

## Scope, method, commit audited

- py-ci-shared audited at `48e49b1`. Corpus: `C:\Users\Admin\Machine learning\mlframe\src\mlframe` at mlframe HEAD `d2ef3afd6`, a SNAPSHOT taken 2026-11-16
  about 02:40 to 03:40 local; another session is editing mlframe, so counts move. Nothing in mlframe or in py-ci-shared (other than this file) was edited, no
  GPU kernel was launched, no pytest was run (this dimension adds no code).
- CUDA.md was read whole (559 lines). Every actionable statement of sections 1-8, 10, 11 is classified in "Rule inventory" below as
  (a) ALREADY GATED, (b) static gate, (c) test-structure or documentation gate, (d) not statically checkable.
- Corpus size: `find_cuda_kernel_sources` finds 173 sources in 103 files (233 `__global__` tokens); my kernel splitter parsed 228 kernels. Excluding the
  `_benchmarks` directories (what a gate would scan): 155 sources, 208 kernels. Python side: 3388 files, 514 touch cupy/numba.cuda/RawKernel (368 outside
  `_benchmarks`); tests: 777 files mention cuda/cupy.
- Every prototype is a throwaway detector in `C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\audit_T\54\`
  (`kcorpus.py` loader, `detect_kernel.py`, `detect_refine.py`, `detect_d9.py`, `detect_d16b.py`, `detect_py.py`, `detect_p3.py`, `detect_p67.py`, `detect_p8.py`,
  `detect_parity3.py`, `detect_e1.py`, `final_measure.py` for the measurements; `canary_check.py` + `canaries.py` + `canary_ktc_profile.py` for the canaries;
  `nvrtc_probe.py` for the compile probes). All 11 canary pairs below (44 snippets) were run through the prototype: every violation fires, every clean file is silent
  (output at the end of "Canary validation").
- NVRTC probes (allowed by the brief): CUDA 12.9 NVRTC through `cupy_backends.cuda.libs.nvrtc`, `createProgram` / `compileProgram` / `getPTX`, no device context,
  no launch, one program at a time, for `compute_61` and `compute_70`. Results in "Verified facts".
- NOT covered: the review of the existing nine CUDA gates (dimension 53); sections 9 and 12-19 (55); the thresholds quoted in section 8 that I could not tie to
  NVIDIA documentation (flagged ADVISORY, not checked against the URLs, per CUDA.md's own warning at line 388). I had no network access to NVIDIA documentation:
  every "NVIDIA says" below is from my knowledge of the CUDA C++ Programming Guide / Best Practices Guide and is labelled as such, and the two checks I could make
  on this host (NVRTC) are labelled "verified".

## Verified facts (NVRTC 12.9, this host, no launch)

| probe | compute_61 | compute_70 | consequence |
|---|---|---|---|
| `INFINITY`, `NAN`, `FLT_MAX`, `INT_MAX`, `HUGE_VALF` in a kernel | `identifier "..." is undefined` | same | cuda.md:376 names only `INFINITY`; the set is wider (six identifiers tested, all undefined) |
| `__int_as_float(0x7f800000)` | compiles | compiles | the documented workaround is sound |
| legacy `__shfl_down(v, 1)` | compiles | `identifier "__shfl_down" is undefined` | a kernel written and tested on the Pascal dev host compiles there and breaks on the sm_89 machine |
| legacy `__ballot(p)` | compiles | compiles (deprecated, no error on 12.9) | only the `__shfl*` family is removed on 12.9 |
| `x[i] = x[i] * 0.1;` (x float) | 4 `.f64` tokens in PTX | | unintended FP64: the literal is a double |
| `x[i] = x[i] * 0.1f;` and `* (float)0.1` | 0 `.f64` | | |
| `x[i] = x[i] * 0.5;` (x float) | 0 `.f64` | | the compiler folds an exactly representable literal: a gate must not flag 0.5, 1.0, 2.0 (check that the literal round-trips through float32) |
| `if (x[i] > 1e-9)` (x float) | 3 `.f64` | | a comparison with an unsuffixed literal also runs in FP64 |
| `pow(double, 2.0)` vs `x*x` | 441 vs 35 PTX lines | | static size only, no timing; the compiler does not strength-reduce it |
| `pow(float, 2)` / `powf(x, 2.0f)` / `x*x` | 59 / 160 / 35 PTX lines | | |
| `atomicAdd(float*)` / `atomicAdd(double*)` | compile | compile | double atomics exist on sm_61 (native since sm_60) |

Claims relied on, with their provenance (a rule whose only support is third party is ADVISORY):

| claim in cuda.md | provenance | status |
|---|---|---|
| `__syncthreads()` in conditional code is only defined when the condition is identical across the block | NVIDIA programming guide (my knowledge) | authoritative; HARD for a barrier inside a thread-dependent branch. a barrier after a thread-dependent `return` is in the same documented category, but real hardware counts exited threads as arrived, so ADVISORY |
| sync intrinsics need a mask naming exactly the participating lanes; `__activemask()` inside a divergent region sees only the current path | NVIDIA programming guide + the "Using cuda Warp-Level Primitives" developer blog (my knowledge) | authoritative |
| `__shfl*` without `_sync` removed for sm_70+ | verified by nvrtc 12.9 above (`__shfl_down` undefined for compute_70) | verified |
| a floating-point `atomicAdd` sum depends on arrival order | follows from non-associative FP add; the guide leaves atomic order unspecified | authoritative, but mlframe claude.md says bit-identity is not a requirement, so a determinism NOTE is the right strength (ADVISORY) |
| more than 48 KB dynamic shared memory per block needs `cudaFuncSetAttribute(MaxDynamicSharedMemorySize)` | NVIDIA programming guide | authoritative |
| `float4` loads need 16-byte alignment | NVIDIA programming guide | authoritative |
| shared tile `[32][33]`, bank = `(byte address / 4) % 32` | NVIDIA best practices guide | authoritative; the quoted "2 to 3x" gain is technillogue's and third party |
| `const T* __restrict__` enables the read-only path | NVIDIA programming guide / best practices | authoritative; the size of the gain is not claimed |
| occupancy 50% target, 25% fine when compute-bound, "grid at least the SM count", "avoid block size 1024", "two-pass preferred for reproducibility", "zeroed buffer hides a bug" | tensormux / Hugging Face / atrex (cuda.md:43, 253-257) | third party, "to check, not to trust": ADVISORY only |
| "32 resident blocks on sm_86" (cuda.md:59) and "32 resident blocks on SM70 and later" (cuda.md:253) | tensormux | WRONG per the NVIDIA compute-capability table as I know it: max resident blocks per SM is 32 for cc 6.x, 7.0, 8.0, 9.0 but 16 for 7.5 and 8.6 (24 for 8.9). See 54-T9 |
| "block 1024 gives two blocks per SM on a 2048-thread SM" | tensormux | true only for cc 7.0, 8.0, 9.0; cc 7.5 holds 1024 threads per SM, cc 8.6 / 8.9 hold 1536 (one 1024 block) |

## Rule inventory (every actionable statement)

Legend: (a) gated, (b) static gate, (c) test-structure or documentation gate, (d) not statically checkable. Gate ids refer to the specs in the next part.

#### Section 1 (host) and 2 (the FE contract)

| statement | class | where |
|---|---|---|
| 1: per-host fingerprint, kernels kept per version so the dispatcher chooses per host; a kernel that wins here can lose elsewhere | (d) | needs a second gpu |
| 2.1 CPU kernel and fused gpu kernel both kept | (c) | mlframe-local regexes in `fe_gpu_coverage_inventory.contract_violations` (`@njit` and `RawKernel` text). Twin tests: (a) `njit_twin_untested`, `njit_twin_parity`. Centralise as part of G12 |
| 2.2 column-block streaming with a RAM and a VRAM bound, OOM falls back to the CPU kernel | (c) + (b) | block streaming is a name regex locally (`block_cols|column block|...`); the OOM fallback must be logged: G10. The bound itself: (d) |
| 2.3 no bare `double`/`float` in kernel source, `{REAL}` values, `{ACC}` sums | (a) partly + (b) | (a) `cuda_kernel_pointer_types` rule `cuda-bare-double-pointer` covers pointer PARAMETERS only (it flags `double*` with no `{REAL}` among the parameters). The body, locals, `__shared__` arrays, scalar parameters, casts and FP literals are not seen. Local: `BARE_FP_TYPE` regex for the 139 "compliant" modules only. Gate G1 |
| 2.4 dispatch through the kernel-tuning cache with a measured fallback | (c) | local regex `kernel_tuner\(`; G13 formalises the sweep placements; the choice itself (d) |
| 2.5 profiled twice, numbers in `audits/.../profiles/<module>.md` with occupancy, throughput, launch stats, launches per call | (c) | local `contract_violations` checks the record exists and contains five keywords; G12. The numbers: (d) |
| ratchet `_fe_gpu_contract.json` (pending only shrinks) | (a) | `baseline_ratchet`/`Baseline` shape; the JSON is local |

#### Section 3 (kernel design)

| statement | class | where |
|---|---|---|
| one block per work item, shared histogram, reduce in place | (d) | design |
| power-of-two block size when the block reduces with a tree | (d) / (b) weak | `cuda_launch_geometry` (a) already forbids literal block sizes, so the width must come from a helper; the helper's power-of-two property is (d). `__launch_bounds__(N)` and `#define THREADS N` constants not a multiple of 32: measured 0 hits (considered, below) |
| dynamic shared memory sized from the table | (d) | design; the 48 KB limit: see section 10.9 row |
| integer counts, REAL arithmetic only where needed, ACC for thresholded sums | (d) + (b) | G1 checks the types are placeholders, not that the choice of ACC is right |
| placeholders substituted before compile, one module per type cached in a dict | (c)/(b) | `cuda_kernel_factories` (a) covers the `kernel(cp, real)` factory shape; the cache itself G9 |
| `crit_float_dtype()` chooses the type | (d) | |
| test both REAL types against the CPU kernel (1e-6 float, 1e-9 double) | (c) | G11 sub-rule `cuda-parity-single-dtype` |
| keep every kernel version under a distinct name, write a "win axis" note | (c) | 11 version-suffixed sources in the non-benchmark corpus; a doc gate "a module with two or more versions mentions a win axis" is possible but I did not measure its precision (free text) and do not recommend it |
| block size a multiple of 32, start at 128 or 256 (tensormux) | (b) weak, third party | `cuda_launch_geometry` (a) flags literals; non-multiple `__launch_bounds__` / `#define`: 0 hits, not recommended |
| occupancy = min(register, shared, block limit); 50% target; take registers from `ptxas -v` | (d) | needs the compiler/profiler. Offline per-arch report: (a) `cuda_offline_report` (report, not a gate) |
| `gridDim.y/z` below 65,535; launch 5-20 us; grid under 100 blocks is a fusion candidate | (d) | the limit is `<= 65535` (the doc says "below": off by one, harmless). Grid sizes are runtime values |
| coalesced access first, vectorized loads | (a) + (b) | (a) `cuda_coalesced_reads` (strided row reads, indirect run reads). Stores are not covered: extension E1 below, 21 of 208 kernels |
| `float4` needs `(base + offset) % 16 == 0` and a scalar tail | (b) | 3 files mention `float4`/`int4`/`uchar4`/`__ldg` (I did not check which token or whether they are aligned); not measured further, not recommended |
| shared tile padded `[32][33]` | (b) | G6 |
| reductions: shuffle with explicit mask, barrier between warp write and read, two-pass for reproducibility | (b) + (d) | masks: G2. barrier ordering and two-pass: (d) |
| fp atomic sum is non-deterministic; init the output to the identity and document it | (b) | G5 |
| guard every global access in the tail block | (b) | candidate D9: first prototype 11 hits, all false positives of a naive guard regex; refined detector (any comparison on the global-id variable or a variable derived from it) finds 0 of 103 kernels that compute a global id. Authors guard universally. Not recommended (Considered) |
| initialize outputs to identity or sentinel, a zeroed buffer can hide a bug | (d) | third party; `cp.zeros` vs `cp.empty` cannot be judged statically |
| choose the architecture target explicitly (sm_61) | (d) | cupy compiles for the current device; `cuda_offline_report` (a) builds per architecture |
| global memory: classify warp footprint, SoA vs AoS, `__ldg`, coalesce writes, 80% bandwidth target | (a) partly | coalesced reads (a); writes E1; the rest (d) |
| `const T* __restrict__` for data not written in the launch | (b) | G7 |
| shared tiling: conflict multiplier, padded stride in every index, `cp.async` barrier, 48 KB attribute, 2 blocks per SM | (b) + (d) | padding G6; `cp.async` is sm_80+ (corpus has none); 48 KB: section 10.9 row |
| warp divergence classification, cost model, branchless `y=(x>0)*f(x)` | (d) | semantic. `__ballot_sync`/`__any_sync` masks: G2 |
| numerical stability: wide accumulators for half inputs, Welford not `E[x^2]-E[x]^2`, subtract the max before `exp`, tolerances, adversarial tests | (a) partly + (b) + (c) | the variance pattern in Python is (a) `cancellation_prone_moments`, which does not read string constants, so a kernel string is unseen; the kernel-text variant found 0 hits in 208 kernels (Considered). `half` accumulators: 0 uses of `__half` in the corpus. Tolerance and adversarial-input tests: G11 sub-rule (advisory) |
| boundaries: predicate before load, identity fill of shared slots, mask every tile, test tile-1/tile+1/prime | (d) + (c) | semantic; the test-size part is G11 sub-rule `cuda-parity-no-boundary-sizes` (third party, ADVISORY) |
| tile size and partitioning: grid at least as many blocks as SMs, last tile useful fraction | (d) | local static model `test_cuda_grid_fill.py` (a-local) |
| fuse elementwise ops, chain under 20-30 live values | (d) | register pressure: local `test_cuda_kernel_register_pressure.py` needs a device |

#### Section 4 (dispatch and residency)

| statement | class | where |
|---|---|---|
| do not hard-code thresholds, block sizes or backend choices; register with `kernel_tuner(...)` | (a) + (b) | block sizes: (a) `cuda_launch_geometry`. Thresholds: candidate D-P8, 2 hits, see Considered |
| equivalence tolerance of the sweep follows the REAL type, not a fixed 1e-9 | (a-local) | mlframe `test_ktc_sweep_tolerances.py`; centralisable (AST over `sweep_backend_grid(equiv_rtol=...)`); belongs to dimension 55's "local meta-tests" |
| `MLFRAME_DISABLE_GPU=1` / empty `CUDA_VISIBLE_DEVICES` wins | (d) | local runtime test `test_gpu_optout_makes_no_device_allocations.py`; the local file explains why a structural gate would be about 100 false positives |
| residency: sweep in the placement the caller has; every host-vs-device entry is swept in the placements its callers can reach; declare `_SWEEP_RESIDENCIES` / `_sweep_place`; one tuner entry per scenario | (c) | G13. Measured: 72 non-benchmark `gpu_capable=True` registrations, 1 declares `residencies=` |
| measure the resident case even where production is host-in; transfer share above half / under a tenth | (d) | `residency_report` is a measurement |

#### Section 5 (memory and streaming)

| statement | class | where |
|---|---|---|
| stride rows to the budget before anything is built; convert and score by column blocks; two blocks of int8 codes at once | (d) | design; the incident (n x p int64 temporaries) is a host-memory design rule, not a cuda-gate topic |
| size the block from free memory (`cp.cuda.runtime.memGetInfo()`) | (c) weak | a streaming module without `memGetInfo`/`mem_info`/`free_bytes`: not measured, name-based, not recommended |
| a memory bound is a test (`tracemalloc` peak under a quarter of one n x p matrix) | (c) weak | "module with block streaming has a test that calls `tracemalloc`"; not measured, not recommended (naming) |
| fall back instead of failing (OOM / driver fault finishes on the CPU kernel and logs a warning) | (b) | G10 |
| withhold, do not guess: return `None` | (d) | |

#### Section 6 (timing and profiling honesty)

| statement | class | where |
|---|---|---|
| synchronize inside the timed region | (a) | `gpu_timing_sync` (docstring: direct cupy/cuda/torch.cuda/numba.cuda calls, kernel launches `kern[b,t](...)`, injected callables in a gpu-aware module; `.cpu()/.item()/.numpy()/.tolist()` end a region). Its depth is dimension 53 |
| measure three points: host, gpu resident, gpu with upload | (d) | benchmark design |
| warm up, then time; exclude numba and nvrtc compilation | (c) | candidate D-P5: per-function detection is not viable (0 of 6 hits true), see Considered. `single_shot_timing_assertion` (a) is the existing neighbour |
| beware memoized wrappers (time the uncached function, novel seeds) | (d) | |
| cProfile is blind to async gpu work; nvprof percentages are serialization-distorted; a microbenchmark can lie; benchmark real shapes; contention dominates variance | (d) | all need a run or a human reading |
| profiler availability table (nvprof, nsys, ncu on Pascal) | (d) | host facts |
| Nsight Compute reference: workflow, metric names, atrex bottleneck thresholds, caveats | (d) | a runtime report parser could implement the classifier table (library, not a gate); out of scope |
| nvprof metric equivalents | (d) | |

#### Section 7 (case study) and 8 (survey)

| statement | class | where |
|---|---|---|
| timings, speedups, counters of the pair residual test | (d) | measured on one host |
| the fused kernel agrees with the CPU kernel for both REAL types and covers every pair once at block boundaries | (c) | G11 (dtype and boundary sub-rules) |
| ktc sweep tolerance `max(1e-9, 8*eps(REAL))`; "ktc chooses more than one path" | (a-local) | `test_ktc_sweep_tolerances.py`, `test_ktc_dispatch_selects_more_than_one_path.py` (runtime, needs a measured cache) |
| candidate optimizations not done | (d) | |
| section 8 surveyed repositories, their rules and thresholds | (d) | third party; every rule taken from it is ADVISORY (see provenance table) |
| "What to take" 1-7: profile before editing, bottleneck thresholds, ncu honesty rules, AKO4ALL benchmark protocol (discard first trial, warmups, median, fresh inputs, mutation sentinel), tensormux test plan (size sweeps, fixed seed, adversarial inputs, sanitizers in CI) | (c) / (d) | fixed seed and size sweeps: G11 sub-rules; sanitizer in CI: a workflow-text check for `compute-sanitizer`, no gpu CI exists in mlframe, (d); the rest (d) |

#### Section 10 (new-module checklist) and 11 (open items)

| statement | class | where |
|---|---|---|
| 10.1 reuse an existing batched entry, measure on your shape | (d) | |
| 10.2 CPU kernel first as the reference | (a) | `njit_twin_untested`, `njit_twin_parity` |
| 10.3 `{REAL}`/`{ACC}`, one compiled module per type | (b) | G1, G9 |
| 10.4 stream by column blocks; OOM fallback | (b) | G10 |
| 10.5 register with the ktc, fallback heuristic, sweep over own axes | (c) | G13 (residency); registration presence is the local regex |
| 10.6 tests: versions equal, streamed equals unblocked for several block widths, gpu equals CPU for both REAL types, every pair covered once, memory-bound test, regression test; gpu-vs-host equality on 2 or more independent seeds/datasets; port every tolerance from the production routine | (c) | G11 (seeds, dtype); "port every tolerance from the routine actually called" is (d) |
| 10.7 profile: cProfile + gpu profiler, record in `profiles/<module>.md`; register-pressure registry; a spill must be fixed or exempted | (c) + (a) | G12; the registry: (a) `cuda_kernel_factories` |
| 10.8 move the module from `pending` to `compliant` | (a) | ratchet |
| 10.9 check the profiler install before claiming it missing | (d) | |
| 10.9 time as warmed back-to-back loop and report the min; state the ramp-inclusive median separately | (d) / (c) weak | |
| 10.9 read the limiter before tuning the launch | (d) | |
| 10.9 a kernel with a fallback needs tests asserting what the fallback received, plus one deletion mutation | (c) | needs call-graph knowledge of "which test exercises the fallback"; `mutation_teeth` (a) is the generic neighbour. Not formalised |
| 10.9 do not let a failing device call end a request: log it, answer on the CPU kernel, an explicit env override raises | (b) | G10 |
| 10.9 nvrtc through cupy has no `INFINITY` | (b) | G4 (wider than the sentence) |
| 10.9 check the dynamic shared-memory request against the device limit, fall back above a budget | (b)/(c) | candidate D-P6, precision too low (below) |
| 10.9 prepare tables on the device too | (d) | |
| 10.9 persist the crossover as one monotone threshold from a sweep | (d) | ktc library |
| 11 open items (warp-private histograms, walk 90 pending modules, write a skill, device-derived widths, re-measure CPU-only kernels, spot-check thresholds) | (d) | work items. Disagreement recorded in 54-T10: "90 pending" vs 113 in the JSON |

## Gate specifications

Common conventions (from the existing gates): a module `src/py_ci_shared/cuda_<name>.py` with `find_cuda_<name>` and `assert_cuda_<name>`, reading kernels through
`find_cuda_kernel_sources` (C text) or `scan_python` (Python AST); `min_files` floor, `allow_unparsed`, `use_git`; findings carry a rule id; suppression is
`// <word>-ok: <reason>` inside the kernel string or `# <word>-ok: <reason>` on the Python statement (the reason is mandatory: an empty reason is not a suppression).
Canary layout: `tests/canary/<module>/{violation,clean,bom,unparsable}/*.py.canary` with the kernel in `_SRC = r"""..."""` and `cp.RawKernel(_SRC, "name")`, exactly like
`tests/canary/cuda_kernel_pointer_types/violation/seed.py.canary`. The kernel text below is the string content; wrap it that way. All of them are ADDITIVE and opt-in
(a consumer calls `assert_*`); none needs a GPU or the CUDA toolkit.

#### G1 `cuda_kernel_fp_types` (rank 1)

- Rules: `cuda-bare-fp-type` (HARD): the words `double`/`float` outside a cast-free placeholder in a kernel source that is type-generic (contains `{REAL}` or `{ACC}`),
  anywhere in the kernel text (locals, `__shared__` arrays, scalar parameters, casts, helper `__device__` functions); `cuda-unsuffixed-fp-literal` (ADVISORY): an FP literal
  that does not round-trip through float32 (`0.1`, `1e-9`, not `0.5`/`1.0`), not directly under an explicit cast, inside a statement that touches a `{REAL}` pointer
  or local and has no `{ACC}`. The first closes contract point 2.3 (CUDA.md:25, 38-39); the second the unintended-FP64 check (CUDA.md:133-135, 329) that `cuda_offline_report`
  only reports.
- Suppression: `// fp-type-ok: <reason>` anywhere in the kernel text (one reason per kernel).
- Scope: sources with a placeholder get the HARD rule; sources with neither placeholder are reported only with `include_untyped` (ADVISORY; they are fixed-type kernels by design:
  44 sources in the corpus including benchmarks).
- Detection: strip `//` and `/* */`, replace `{WORD}` by `@WORD@` so brace matching works (placeholders in kernel NAMES such as `quantile_codes_{TAG}` broke my splitter, so
  the bare-type rule scans the whole source text; only the literal rule needs per-statement splitting), regex `\b(double|float)\b(?!\s*\()` per kernel text, float32 round trip
  with `struct` for the literal.
- Provenance: project contract (CUDA.md:25) for the HARD rule; the FP64 cost is verified (PTX `.f64` tokens 4 vs 0, above) and NVIDIA's FP64 rate on consumer parts (1/32
  here, CUDA.md:199) is a documented fact.
- Violations (kernel text):

```
// violation/bare_double.canary  (rule cuda-bare-fp-type)
extern "C" __global__ void row_sum(const {REAL}* __restrict__ x, {ACC}* __restrict__ out, const int n) {
    double acc = 0.0;
    for (int i = threadIdx.x; i < n; i += blockDim.x) acc += (double)x[i];
    out[blockIdx.x] = acc;
}

// violation/literal_promotes.canary  (rule cuda-unsuffixed-fp-literal)
extern "C" __global__ void scale({REAL}* __restrict__ x, const int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) x[i] = x[i] * 0.1;
}
```

- Clean:

```
// clean/placeholders.canary
extern "C" __global__ void row_sum(const {REAL}* __restrict__ x, {ACC}* __restrict__ out, const int n) {
    {ACC} acc = 0;
    for (int i = threadIdx.x; i < n; i += blockDim.x) acc += ({ACC})x[i] * ({REAL})0.1;
    out[blockIdx.x] = acc;
}

// clean/float64_on_purpose.canary
extern "C" __global__ void code_edges(const {REAL}* __restrict__ v, const double* __restrict__ edges, int* __restrict__ out, const int n) { // fp-type-ok: edges are float64 by contract with the host binner
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) out[i] = (double)v[i] > edges[0];
}
```

- Measured (155 non-benchmark sources, 208 kernels): `cuda-bare-fp-type` 5 sources in 4 files with the whole-source scan (4 sources in 3 files with a per-kernel splitter that
  misses `quantile_codes_{TAG}`): `_quantile_codes_gpu.py::_SRC`, `_resident_candidate_mi.py::_BUILD_OPCANDS_CM_SRC` and `::_BUILD_OPCANDS_SRC`,
  `info_theory/_cmi_cuda.py::_CMI_FROM_JOINT_SRC`, `shap_proxied_fs/_shap_proxy_gpu.py::_KERNEL_SRC`. Judgement of the five: 5 true positives against the rule as written (all
  five declare `{REAL}`/`{ACC}` and also use bare `double`: `const double* EDGES`, `double* red`, `double acc`). `_quantile_codes_gpu` is probably intentional (its Python carries
  "Intentional float64" comments), so it needs the marker; the other four have no stated reason. None of the four files is in `_fe_gpu_contract.json` (compliant, exempt or pending),
  so no existing check sees them. `cuda-unsuffixed-fp-literal`: 4 sources (`_gpu_resident_basis.py::_BASIS_PREPROCESS_SRC`, `::_ROBUST_SCALE_COMBINE_SRC`, `_lof_gpu.py::_SRC`,
  `_extra_basis_resident.py::_BSPLINE_SRC`), all true patterns (`if (!(std > 1e-12))`, `out[i] = sc > 1e-12 ? sc : (...)`, `lrd_new > 1e-12 ? lrd_new : 1e-12`), each costs one FP64
  compare or convert; the effect is small (the runtime metric would be `sm__pipe_fp64_cycles_active`, CUDA.md:133), hence ADVISORY.
- Existing coverage: `cuda_kernel_pointer_types` sees only `double*`/`float*` parameters without a `{REAL}` parameter. It does not see `const double inv_n` (scalar parameter of
  `_CMI_FROM_JOINT_SRC`) or any local.

#### G2 `cuda_warp_intrinsics` (rank 5)

- Rules: `cuda-legacy-shfl` (HARD): `__shfl`, `__shfl_up`, `__shfl_down`, `__shfl_xor` without `_sync` (verified: undefined for compute_70 on NVRTC 12.9, compiles for compute_61);
  `cuda-legacy-vote` (ADVISORY): `__ballot`, `__any`, `__all` (deprecated since CUDA 9, still compile on 12.9); `cuda-full-mask-in-divergent-branch` (HARD): a `_sync` intrinsic
  with the full mask `0xffffffff` inside an `if` whose condition depends on `threadIdx` (lanes named in the mask do not all execute it); `cuda-activemask-as-mask` (ADVISORY):
  `__activemask()` passed as the mask argument (the NVIDIA blog says it does not establish convergence).
- Suppression: `// warp-ok: <reason>`.
- Detection: regex per kernel; thread-dependence by a flow-insensitive taint of variables assigned from `threadIdx` (member accesses `.x/.y/.z` must not count as variable uses; my
  first prototype counted `blockIdx.y` as a use of a variable named `y` and made a false positive, fixed with `(?<![.\w])`).
- Provenance: HARD rules are verified (legacy) or NVIDIA-documented (mask); the vote rule and `__activemask` are ADVISORY.
- Why it matters: this repo's consumers run CPU-only CI, so an NVRTC failure on the newer machine (sm_89, CUDA.md:15) is invisible until someone runs there.
- Violations:

```
// violation/legacy_shfl.canary
extern "C" __global__ void red(float* x) {
    float v = x[threadIdx.x];
    for (int o = 16; o > 0; o >>= 1) v += __shfl_down(v, o);
    if ((threadIdx.x & 31) == 0) x[blockIdx.x] = v;
}

// violation/full_mask_in_divergent_if.canary
extern "C" __global__ void red(float* x, const int n) {
    float v = x[threadIdx.x];
    if (threadIdx.x < n) { v += __shfl_down_sync(0xffffffffu, v, 1); }
    x[threadIdx.x] = v;
}
```

- Clean:

```
// clean/full_warp_loop.canary
extern "C" __global__ void red(float* x) {
    float v = x[threadIdx.x];
    for (int o = 16; o > 0; o >>= 1) v += __shfl_down_sync(0xffffffffu, v, o);
    if ((threadIdx.x & 31) == 0) x[blockIdx.x] = v;
}

// clean/ballot_sync_explicit_mask.canary
extern "C" __global__ void cnt(const int* p, int* out, const int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    const unsigned b = __ballot_sync(0xffffffffu, i < n && p[i] > 0);
    if ((threadIdx.x & 31) == 0) atomicAdd(&out[0], __popc(b));
}
```

- Measured (208 kernels): 0 hits for every rule (no legacy intrinsic, no divergent full mask, no `__activemask` in the corpus). A zero-hit rule: the canaries above are what prove it fires.
  The corpus does use `_sync` forms with `0xffffffffu` everywhere I looked (for example `_bootstrap_rank_mi_tied_gpu.py` `__shfl_up_sync(0xffffffffu, v, o)`).

#### G3 `cuda_barrier_divergence` (rank 8)

- Rules: `cuda-barrier-in-divergent-branch` (HARD): `__syncthreads()` (or `__syncwarp` with a partial mask is out of scope) inside an `if`/`while`/`for` whose condition depends on
  `threadIdx`; `cuda-barrier-after-divergent-return` (ADVISORY): a thread-dependent `return` (`if (t >= K) return;`) followed later in the kernel by `__syncthreads()`.
- Suppression: `// barrier-ok: <reason>` (for example "the launch block equals K, so the guard is vacuous").
- Detection: same taint as G2; brace matching; for `for` loops the init and condition are both inspected (a `for (r = tid; r < n; r += blockDim.x)` loop with a barrier inside has a
  per-thread trip count).
- Provenance: HARD part is the programming guide; the return rule is ADVISORY because exited threads are counted as arrived by the hardware I know, and `compute-sanitizer --tool synccheck`
  is the dynamic authority (CUDA.md:214).
- Violations:

```
// violation/barrier_after_return.canary
extern "C" __global__ void k(const float* x, float* y, const int K) {
    extern __shared__ float sh[];
    const int t = threadIdx.x;
    if (t >= K) return;
    sh[t] = x[t];
    __syncthreads();
    y[t] = sh[K - 1 - t];
}

// violation/barrier_in_branch.canary
extern "C" __global__ void k(float* y) {
    __shared__ float sh[64];
    if (threadIdx.x < 32) { sh[threadIdx.x] = 1.0f; __syncthreads(); y[threadIdx.x] = sh[31 - threadIdx.x]; }
}
```

- Clean:

```
// clean/predicated_load_then_barrier.canary
extern "C" __global__ void k(const float* x, float* y, const int n) {
    extern __shared__ float sh[];
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    sh[threadIdx.x] = (i < n) ? x[i] : 0.0f;
    __syncthreads();
    if (i < n) y[i] = sh[blockDim.x - 1 - threadIdx.x];
}

// clean/block_uniform_condition.canary
extern "C" __global__ void k(const int* flags, float* y) {
    __shared__ float sh[64];
    if (flags[blockIdx.x]) { sh[threadIdx.x] = 1.0f; __syncthreads(); y[threadIdx.x] = sh[63 - threadIdx.x]; }
}
```

- Measured (208 kernels): 1 kernel, 3 barrier sites, `_kernels_cupy.py::_ROW_ATTENTION_STAGE4_KERNEL_SRC` `row_attention_stage4_fused` (`int t = threadIdx.x; if (t >= K) return;` then three
  `__syncthreads()`). Judgement: true positive for the pattern, benign at the only launch (`_kernels_cupy.py:352-354` launches `(k,)` threads per block, so `t >= K` is never true). The
  prototype's first run also reported `periodogram_power_b` and `tied_replicate_mi` (the `blockIdx.y`/variable-`y` taint bug above): after the fix they are gone, which shows the
  guard `f >= nfreq[c]` on block indices is correctly seen as uniform. Branch rule: 0 hits.

#### G4 `cuda_nvrtc_headers` (rank 6)

- Rule: `cuda-nvrtc-undefined-macro` (HARD): an identifier from {`INFINITY`, `NAN`, `HUGE_VAL`, `HUGE_VALF`, `FLT_MAX`, `FLT_MIN`, `FLT_EPSILON`, `DBL_MAX`, `DBL_MIN`, `DBL_EPSILON`,
  `INT_MAX`, `INT_MIN`, `UINT_MAX`, `LONG_MAX`, `LLONG_MAX`, `ULLONG_MAX`, `SIZE_MAX`} used in a kernel source that does not `#define` it; and any `CUDART_*` constant without
  `#include <math_constants.h>`. Verified undefined on NVRTC 12.9: `INFINITY`, `NAN`, `FLT_MAX`, `INT_MAX`, `HUGE_VALF` (the others are the same family, not individually probed: the
  gate should list only the five probed identifiers as HARD and the rest as ADVISORY until probed).
- Suppression: `// nvrtc-ok: <reason>` (a build that passes `-include` or a header path).
- Detection: comment-stripped identifier regex minus `#define`s in the same source. Supports CUDA.md:376, which names only `INFINITY`.
- Violations:

```
// violation/infinity.canary
extern "C" __global__ void k(float* m, const int n) {
    float best = -INFINITY;
    for (int i = 0; i < n; ++i) best = fmaxf(best, m[i]);
    m[0] = best;
}

// violation/flt_max.canary
extern "C" __global__ void k(const float* v, int* out) {
    out[threadIdx.x] = v[threadIdx.x] > FLT_MAX;
}
```

- Clean:

```
// clean/math_constants.canary
#include <math_constants.h>
extern "C" __global__ void k(float* m) { m[0] = CUDART_INF_F; }

// clean/bit_pattern.canary
extern "C" __global__ void k(float* m) { m[0] = __int_as_float(0x7f800000); }
```

- Measured (155 sources): 0 hits. The corpus follows the documented workaround; the gate is a regression guard and the canaries prove it fires. It matters beyond the second machine because CPU-only CI never compiles
  the source.

#### G5 `cuda_atomic_determinism` (rank 7)

- Rule: `cuda-float-atomic-add` (ADVISORY): `atomicAdd` on a `float`/`double`/`{REAL}`/`{ACC}` target (global parameter or `__shared__` array) whose kernel carries no determinism note.
  The note is `// atomic-fp-ok: <reason>` in the kernel text, or the same marker in the Python module docstring/comment of the file that holds the source (the reason says why order
  dependence is acceptable).
- Detection: type of the atomic's base name from the parameter list and `__shared__` declarations; unknown base (a local pointer alias) is reported separately as `unresolved` and not
  counted (69 of 185 atomic sites in the corpus have an unresolved base).
- Provenance: NVIDIA (FP add is not associative; order unspecified), CUDA.md:48, 254 (tensormux "document it"). ADVISORY because mlframe's CLAUDE.md says bit-identity is not a requirement.
- Violations:

```
// violation/double_global_atomic.canary
extern "C" __global__ void total(const double* __restrict__ x, double* __restrict__ out, const int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) atomicAdd(&out[0], x[i]);
}

// violation/float_shared_atomic.canary
extern "C" __global__ void total(const float* __restrict__ x, float* __restrict__ out, const int n) {
    __shared__ float sh[1];
    if (threadIdx.x == 0) sh[0] = 0.0f;
    __syncthreads();
    atomicAdd(&sh[0], x[blockIdx.x * blockDim.x + threadIdx.x]);
    __syncthreads();
    if (threadIdx.x == 0) out[blockIdx.x] = sh[0];
}
```

- Clean:

```
// clean/integer_atomic.canary
extern "C" __global__ void hist(const int* __restrict__ c, int* __restrict__ h, const int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) atomicAdd(&h[c[i]], 1);
}

// clean/float_atomic_with_reason.canary
// atomic-fp-ok: the sum is order-dependent; the parity test compares at rtol 1e-9, not bit-for-bit
extern "C" __global__ void total(const double* __restrict__ x, double* __restrict__ out, const int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) atomicAdd(&out[0], x[i]);
}
```

- Measured (208 kernels): 10 kernels in 9 sources / 6 files with a resolvable FP atomic: `_cell_moments_kernel.py` (`fold_cell_sums`, `fold_cell_centred`, `cell_sums`, `cell_centred`; `atomicAdd(&sh[...], d2 * wi)`
  on a `double` shared array), `_fe_batched_mi_cmi.py::_ENT_NNZ_1D_SRC` (`ent_nnz_1d`), `_gpu_resident_basis.py::_ABS_CORR_SRC` (`abs_corr`), `_group_distance_gpu.py` (`gd_stats`, `gd_w1`),
  `_mi_greedy_cmi_fe_gpu.py::_ENT_NNZ_SRC` (`ent_nnz`), `_orthogonal_xi_gpu.py` (`xi_walk`). Judgement of the first five kernels: 5 of 5 are true floating atomics. None has a determinism note in the
  kernel text, and `grep -ci "determin|order-dependent|summation order|reproducib"` over the six Python files gives 0, 1, 0, 0, 0, 0 (the single hit is in `_fe_batched_mi_cmi.py`, not about the atomic).
  CUDA.md:48 says "our integer counts in shared memory are exact and the final tree reduction has a fixed order"; ten kernels do not fit that sentence (see 54-T6).

#### G6 `cuda_smem_bank_padding` (not top ten)

- Rule: `cuda-smem-unpadded-column-access` (ADVISORY): a 2-D `__shared__` array whose last dimension is a multiple of 32 (`[32][32]`, `[9][32]`, or a `TILE` macro), read or written as
  `a[<lane-derived>][<not lane-derived>]` (a column walk: lanes of a warp hit the same bank). Marker `// bank-ok: <reason>`.
- Naive version (any `[N][32]`) found 12 hits in 6 sources, almost all false positives (`sh[8][32]` indexed `[warp][lane]` is conflict-free); the access-pattern refinement found 2.
- Violations:

```
// violation/unpadded_tile_transpose.canary
extern "C" __global__ void tr(const float* in, float* out) {
    __shared__ float tile[32][32];
    tile[threadIdx.y][threadIdx.x] = in[threadIdx.y * 32 + threadIdx.x];
    __syncthreads();
    out[threadIdx.y * 32 + threadIdx.x] = tile[threadIdx.x][threadIdx.y];
}

// violation/row_per_thread_reads_column.canary
extern "C" __global__ void fin(double* out, const int nw) {
    __shared__ double sh[9][32];
    if (threadIdx.x < 9) { double t = 0.0; for (int w = 0; w < nw; ++w) t += sh[threadIdx.x][w]; out[threadIdx.x] = t; }
}
```

- Clean: the `[32][33]` padded version of the first; and `red[warp][lane]` with `__shared__ double red[8][32]` (lane is the column index).
- Measured (208 kernels): 2 hits, `_orthogonal_univariate_fe/_fourier_batch_gpu.py` `sincos_gram_b` and `_fourier_fused_gpu.py` `sincos_gram` (`sh[threadIdx.x][w2]`, 9 threads walking a column of `sh[9][32]` doubles). True positives for the
  pattern; the cost is negligible (9 threads, a handful of iterations). The corpus already pads (`[32][33]` in `_gpu_resident_select_kernels.py` `transpose_f32`, `[8][33]` in
  `_gpu_resident_basis.py`), which is the evidence the convention is followed. ADVISORY.

#### G7 `cuda_const_restrict` (rank 10)

- Rule: `cuda-pointer-param-not-const` (ADVISORY): a pointer parameter without `const` that the kernel body never writes through (no `p[...] =`, `+=`, `++`, `*p =`, no atomic, never passed or aliased
  as a bare pointer value, including a ternary). Marker `// const-ok: <reason>`.
- Violations:

```
// violation/read_only_int_pointer.canary
extern "C" __global__ void mi(const int* c, int* joint, double* tot, const int n) {
    double t = 0.0;
    for (int i = 0; i < n; ++i) t += joint[i] * c[i];
    tot[0] = t;
}

// violation/read_only_restrict_float.canary
extern "C" __global__ void axpy(float* __restrict__ x, float* __restrict__ y, const int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) y[i] = 2.0f * x[i] + y[i];
}
```

- Clean: `const float* __restrict__ x` with a written `y`; a histogram `int* h` written only through `atomicAdd(&h[c[i]], 1)`.
- Measured: 1032 pointer parameters, 306 without `const`, 1 never written: `_gpu_kernel_sources.py::COMPUTE_MI_FROM_CLASSES_CUDA_SRC` `compute_mi_from_classes_cuda` (`int *joint_counts`, read-only): true positive.
  A simpler detector (no alias handling, no nested-subscript matching) produced 15 hits and 14 were false positives (`counts[(long long)p * total + col_off[c] + rem] = ...` with a nested subscript;
  `(is_val ? va : tr) + pos * n_legs`; a first run of 16 hits was reduced to 5 by fixing the atomic cast form `(unsigned long long*)&counts[a[i]]`, and to 1 by balanced bracket matching and ternary aliasing).
  Lesson for the build: the store test must use balanced brackets and must treat any bare use of the name as an alias. The corpus follows the convention (726 of 1032 pointer parameters are `const`). The gain is a
  documented read-only-cache enablement, not a number, hence ADVISORY and rank 10.

#### G8 `cuda_pow_small_exponent` (not top ten)

- Rule: `cuda-pow-small-integer` (ADVISORY): `pow`/`powf` with a literal exponent 2, 3, 4 or 0.5 in kernel text, and `cupy.power(x, <small int>)` / `cp.float_power` in Python. Marker `// pow-ok: <reason>`.
- Violations: `x[i] = pow(x[i], 2.0);` (double kernel) and `x[i] = powf(x[i], 3.0f);`. Clean: `x[i] * x[i]`; `pow(x[i], p)` with a runtime exponent.
- Evidence: static PTX size, measured above (441 lines for `pow(double, 2.0)` against 35 for `x*x`); no timing. Not in CUDA.md at all, so there is no project rule behind it.
- Measured: 0 hits in 155 sources (kernel text) and 0 `cp.power(..., 2)` in Python. Zero-hit, not recommended; the canaries prove the detector fires (they ran).

#### G9 `cuda_kernel_cache`

- Rule: `cuda-kernel-built-per-call` (HARD): a `cp.RawKernel`/`RawModule`/`ElementwiseKernel`/`ReductionKernel` constructed inside a function that neither carries `lru_cache`/`cache`/`memoize`, nor
  reads from and stores into a module-level mapping (`_X.get(key)` then `_X[key] = ...`). Source: CUDA.md:39 ("one compiled module per type, cached in a dict"). Marker `# kernel-cache-ok: <reason>`.
- Violations (Python canary files):

```
import cupy as cp

_SRC = "extern \"C\" __global__ void k(float* x) { x[0] = 1.0f; }"


def run(x):
    kern = cp.RawKernel(_SRC, "k")
    kern((1,), (32,), (x,))
    return x
```

```
import cupy as cp


def score(blocks):
    out = []
    for b in blocks:
        ek = cp.ElementwiseKernel("float32 x", "float32 y", "y = x * x", "sq")
        out.append(ek(b))
    return out
```

- Clean: the `_get_kernel(real)` factory with `_KERNELS.get(real)` / `_KERNELS[real] = kern` under a lock; a `@functools.lru_cache(maxsize=None)` factory.
- Measured (368 non-benchmark GPU modules): the first heuristic (constructor inside any function with no decorator) found 15 sites in 11 files; judging all of them, 15 of 15 are the `get`-then-store cache pattern
  (`_pack_module`, `_dcor_kernel`, `_get_kernel_v2/v3`, `_get_cmi_from_joint_kernel`, `_get_xlogx_kernel`, ...). With the get/store test the true count is 0. So the rule has no current violation; it is a regression
  guard, and 15 correct caches is the evidence that "cached" is recognisable. Rank 11 (next after the ten).

#### G10 `cuda_fallback_logged` (rank 9)

- Rule: `cuda-fallback-not-logged` (ADVISORY): an `except` handler around GPU work (the `try` body mentions `cp.`/`cupy`/a `*_gpu`/`*_cuda` callable/a kernel call) that catches a device error type or a broad `Exception`,
  neither logs (`logger.*`, `log_throttle`, `warnings.warn`) nor re-raises. Excluded: `ImportError`/`ModuleNotFoundError`/`AttributeError`/`KeyError`/`ValueError`/`TypeError`/`OSError`. Marker
  `# fallback-logged-ok: <reason>` on the `except` line or in its body (for a best-effort inner handler whose outer handler already logged).
- Sources: CUDA.md:84 and 375 ("logs a warning"), mlframe CLAUDE.md "Say so when a gate makes a run slower" (`log_throttle`). The existing neighbours are mlframe-local `test_broad_except_logging_gpu_*` files and the generic
  `swallowed_exceptions` / `fail_open_handlers`; none keys on "the try body is GPU work", so none can see the OOM-then-CPU shape.
- Violations (Python canaries):

```
import cupy as cp


def score(x):
    try:
        return _score_gpu(cp.asarray(x))
    except cp.cuda.memory.OutOfMemoryError:
        return _score_cpu(x)
```

```
import cupy as cp


def score(x, out):
    try:
        out[:] = cp.asnumpy(_kernel_gpu(x))
    except Exception:
        _fill_host(x, out)
```

- Clean:

```
import logging
import cupy as cp
from mlframe.utils import log_throttle

logger = logging.getLogger(__name__)


def score(x):
    try:
        return _score_gpu(cp.asarray(x))
    except cp.cuda.memory.OutOfMemoryError as exc:
        log_throttle(logger, "score.oom", logging.WARNING, "device out of memory at n=%d (%s); scoring on the CPU kernel", len(x), exc)
        return _score_cpu(x)
```

```
import cupy as cp


def note_cooldown():
    try:
        cp.cuda.runtime.deviceSynchronize()
    except Exception:  # fallback-logged-ok: best-effort cooldown note, the caller already logged the fallback
        pass
```

- Measured (368 non-benchmark GPU modules): 23 handlers in 20 files. First five judged by reading the code: (1) `_bootstrap_rank_mi_gpu.py:185` FALSE POSITIVE (inner best-effort cooldown note, the outer `except` at 176 logs a warning);
  (2) `_fe_additive_fusion.py:163` TRUE POSITIVE (`except tuple(_dev_errs): pass  # genuine cupy/device/linalg fault -> CPU path`, no log); (3) `_fe_batched_mi_cmi_hist.py:610` TRUE POSITIVE
  (`_sm_count` silently returns 16 when the device query fails); (4) `_gpu_resident_extval.py:171` FALSE POSITIVE (inner best-effort, outer `logger.debug` already there); (5) `_gpu_resident_select.py:237` FALSE POSITIVE
  (`except MemoryError: pass  # over budget: already logged above`). 2 of 5 true: 40%, above the 30% limit, so ADVISORY with the marker; the false positives are all "logged elsewhere in the same function", which the marker states.
  A narrowing that would remove two of the three: skip a handler whose body is only `pass` when the enclosing function already has a `logger.*` call earlier in the same `try` statement's `except` chain (needs a sibling-handler look).

#### G11 `cuda_parity_test_shape` (rank 4)

- Rules (test files, AST): `cuda-parity-single-seed` (ADVISORY, then a ratchet): a test function that calls a GPU-named callable (`*gpu*`, `*cuda*`, `*cupy*`, `*device*`, or `backend="cuda"`) and a host-named one (`*cpu*`, `*njit*`,
  `*host*`, `*reference*`, `backend="njit"`), compares with `allclose`/`array_equal`/`approx`, builds random data (`default_rng`, `RandomState`, `np.random`), and has fewer than two seed sources (a `seed`/`rng` parametrize
  argument, a loop `for seed in`, or at least two distinct seed literals); `cuda-parity-single-dtype` (ADVISORY): a test of a module that substitutes `{REAL}` that does not mention both `float32` and `float64`;
  `cuda-parity-no-boundary-sizes` (ADVISORY, third party): none of 1, 31, 33, 1025 or a prime among the sizes (tensormux, CUDA.md:254-256; I did not prototype this one). Names that mark a non-parity test (`fall`, `dispatch`,
  `thresh`, `downgrad`, `oom`, `optout`, `without`, `skip`) are excluded. Marker `# parity-ok: <reason>` (an adversarial-value test has no random seed to vary).
- Source: CUDA.md:367 (10.6): "GPU-vs-host equality must pass on 2 or more independent seeds/datasets" after `_fe_accuracy_gate_kernel.py` diverged on 4 of 100 combos of a second seed (a project incident, so the seed rule has a
  measured basis; the size list does not).
- Violations (test canaries):

```
import numpy as np


def test_gpu_matches_cpu():
    x = np.random.default_rng(0).normal(size=1000)
    assert np.allclose(score_gpu(x), score_cpu(x))
```

```
import numpy as np


def test_cuda_equals_njit_for_every_size():
    rng = np.random.default_rng(7)
    for n in (1, 31, 33, 1025):
        x = rng.normal(size=n)
        assert np.allclose(mi_cuda(x), mi_njit(x))
```

- Clean:

```
import numpy as np
import pytest


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_gpu_matches_cpu(seed):
    x = np.random.default_rng(seed).normal(size=1000)
    assert np.allclose(score_gpu(x), score_cpu(x))
```

```
import numpy as np


def test_gpu_matches_cpu_on_two_independent_datasets():
    for seed in (11, 12):
        x = np.random.default_rng(seed).normal(size=1000)
        assert np.allclose(score_gpu(x), score_cpu(x))
```

- Measured (mlframe tests, 777 files mention cuda/cupy): first broad detector 591 comparison tests, 551 with fewer than two seed sources (93%), but the first five hits were mostly not parity tests (fallback and dispatcher tests): 1 of 5 true. With the callable-name,
  random-data and test-name filters: 72 parity tests, 5 with two or more seed sources, 59 with exactly one literal seed, 8 with random data and no literal seed; so 67 of 72 would be flagged. First five (after the filter): `test_device_bitset_null_matches_the_host_bitset_null`
  (seed 2, TRUE), `test_cupy_dtw_matches_cpu_for_unequal_lengths` (seed 0, TRUE), `test_banded_numba_cuda_matches_banded_cupy` (seed 99, TRUE), `test_order_and_ties_equal_the_host` (no literal seed, UNCLEAR: may draw from a fixture),
  `test_grid_mi_equals_the_host_kernel` (no literal seed, UNCLEAR). 3 true, 2 unclear, 0 false in the sample: about 60% or better. Because 93% of the population would be red the gate needs a baseline (`Baseline`) from day one: ADVISORY with a ratchet,
  not a hard gate.

#### G12 `cuda_profile_records` (rank 3)

- Rules: `cuda-profile-record-missing` (ADVISORY, then a ratchet): a module that defines a CUDA kernel source (found by `find_cuda_kernel_sources`, `_benchmarks` excluded) has no `<profiles_dir>/<module stem>.md` (a `family` mapping handles carved
  modules: the local `_CARVED_PARENT`); `cuda-profile-record-incomplete:<item>`: the record lacks one of `cProfile`, a GPU profiler name (`nsys|ncu|nvprof`), "launches per", "occupancy", "throughput|utilization|bandwidth". The five items are exactly the mlframe-local
  `contract_violations` strings (`fe_gpu_coverage_inventory.py:433-448`), so this centralises an existing check; parameters: `profiles_dir`, `required` (patterns), `modules` (default: every kernel-bearing file), `exempt` (reasons required).
  Marker: none (an exemption is an entry with a reason, as in `_fe_gpu_contract.json`).
- Provenance: project contract point 5 (CUDA.md:27, 368). The numbers in a record stay (d).
- Violations / clean (directory canaries; checked by `canary_ktc_profile.py`):

```
violation/ : pkg/_k_gpu.py.canary   (a module with _SRC = r"""extern "C" __global__ void k(...) {...}""")   and no profiles/_k_gpu.md
violation/ : profiles/_k2_gpu.md.canary = "cProfile top 20 ... nvprof ... 3 launches per call"   (no occupancy, no throughput)
clean/     : pkg/_k_gpu.py.canary + profiles/_k_gpu.md.canary = "cProfile top 20 ... ncu --set full ... 3 launches per call ... achieved occupancy 0.96 ... DRAM throughput 4.6 GB/s"
clean/     : a module with no kernel source (CPU only) and no record
```

- Measured (mlframe, snapshot): 91 non-benchmark files hold a kernel source; 43 have a record named after the file stem, 48 do not (for example `_added_information_gpu.py`, `_cat_pair_joint_gpu.py`, `_cell_moments_kernel.py`, `_abs_corr_tiled_gpu.py`, `_conditional_bin_moments.py`;
  some may be covered by a family record, which is why the name rule needs the carved-parent mapping, so 48 is an upper bound). Of the 43 records, 31 lack at least one of the five items. Judgement of the first five missing: `_kernels_cupy.py`,
  `_abs_corr_tiled_gpu.py`, `_added_information_gpu.py`, `_cat_pair_joint_gpu.py`, `_cat_weighted_pair_gpu.py` have no record and are not in `_fe_gpu_contract.json` compliant list (`compliant` 139, `exempt` 63, `pending` 113): 5 of 5 consistent with the contract being applied only
  to the FE modules the MRMR layer imports. Today the local test is the only gate and it is not a library.

#### G13 `cuda_ktc_residency` (rank 2)

- Rule: `cuda-ktc-residency-undeclared` (ADVISORY, then a ratchet): a call `kernel_tuner(..., gpu_capable=True, ...)` that passes no `residencies=` and carries no `# residency-ok: <reason>` comment on the call (the reason names the single boundary, as CUDA.md:68-70 requires:
  "a module with one boundary says so and why"). Sources: CUDA.md:68-72 and the pyutilz `kernel_tuner(residencies=...)` signature (`pyutilz/performance/kernel_tuning/registry.py:103-108`).
- Violations (Python canaries):

```
from pyutilz.performance.kernel_tuning.registry import kernel_tuner

_SPEC = kernel_tuner(kernel_name="pair_mi", variant_fns=(pair_mi_njit,), tuner=_sweep, axes={"n": [1000, 10000]}, fallback=_fallback, gpu_capable=True)
```

```
from pyutilz.performance.kernel_tuning.registry import kernel_tuner

_SPEC = kernel_tuner(
    kernel_name="rank_bin",
    variant_fns=(rank_bin_njit,),
    tuner=_sweep,
    axes={"n": [1000]},
    fallback=_fallback,
    gpu_capable=True,
    salt="v2",
)
```

- Clean: the same call with `residencies=(("host", "host"), ("device", "device"))`; the same call with `# residency-ok: the only caller holds host codes and consumes host results, so host-in / host-out is the one boundary` on the call; a CPU-only `kernel_tuner(...)` with no `gpu_capable`.
- Measured (mlframe src): 76 `gpu_capable=True` call sites, 4 in `_benchmarks`, 72 in library code; `residencies=` appears at 1 (`_unary_elementwise_tuning.py:95`), so 71 of 72 library registrations declare nothing (unless a helper splats the keyword in; I grepped call sites, not the pyutilz registry's resolved specs). The module attribute `_SWEEP_RESIDENCIES` that CUDA.md:70 says a module declares
  exists in no module: `_ktc_fallback.py:86` reads `vars(module).get("_SWEEP_RESIDENCIES", [("host", "host")])`, and its default is host-in / host-out, so 71 sweeps measure only that placement. First five sites (`data_valuation/_propagate_gpu_ktc.py:140`, `feature_engineering/transformer/random_features.py:479`,
  `batch_mi_noise_gate_gpu.py:879`, `batch_pair_mi_gpu.py:848`, `batch_pair_usability_corr_gpu.py:827`): 5 of 5 undeclared, true positives against the text of the rule. Whether the default placement is the right one for each is the module owner's call, which is exactly what the marker records.
  Detection is exact AST (no heuristic), so there is no false-positive risk in the detector itself; the cost is a 71-entry baseline.

#### Gates specified, measured, and NOT recommended (summary form; their canaries are not machine-validated)

| id | module | rule | what I measured | verdict |
|---|---|---|---|---|
| D9 | `cuda_unguarded_index` | global index `blockIdx*blockDim+threadIdx` with no comparison on it or on a variable derived from it | naive guard regex: 11 hits, 11 false positives (a guard like `row >= (long long)kc * pbc` or a grid-stride loop `for (i = t; i < M; ...)`); refined: 0 of 103 kernels that compute a global id | the corpus guards universally; zero-hit; not worth a module. Violation: `x[blockIdx.x*blockDim.x+threadIdx.x] = 1.0f;`. Clean: the same with `if (i < n)` |
| P1 | `cuda_device_id` | `Device(0)`, `select_device(0)`, `getDeviceProperties(0)`, `"cuda:0"` in library code | 1 `Device(0)` and 2 `getDeviceProperties(0)` in `_benchmarks` (printing the gpu name), 2 `'cuda:0'` in docstring examples of `providers.py`; library code 0 | zero true hits; benign in benchmarks |
| P3 | `cuda_transfer_in_loop` | `.get()`/`asnumpy`/`asarray`/`item()` inside a loop | broad: 214 sites in 64 files, the first five all legitimate block streaming or benchmarks (0 of 5 true). Refined to non-block-stride loops, library only: `item()` 0; `.get()/asnumpy` 7 sites in 4 files; judged: `_gpu_metrics.py:617-618` (a per-column loop calling the gpu AUC and copying back each time) TRUE (2 sites), three `out.append(cp.asnumpy(mi))` in `_streamed*` column-block loops FALSE (block results), `_fourier...:360` and `_usability_greedy_clf_batched.py:114` FALSE (a one-shot generator and a `hasattr` fallback). 2 of 7 true: 29%, at the limit | ADVISORY at best, not recommended; the right owner is cuda.md section 15 (dimension 55) |
| P5 | `cuda_timing_warmup` | timed gpu region with no earlier call of the same callee | 6 hits, 0 true: three are production timings for log lines (`_step_pairmi.py`, `polynom_pair_fe.py`), three are timing helpers whose warm-up is in the caller (`pair_mi_shared_fused_ab.py` warms in module scope before `run`, `bench_mi_from_codes_tiled.py::_timed_ms`) | a per-function rule cannot see module-scope warm-ups; not viable as written. a viable variant would need the call graph of a benchmark script |
| P6 | `cuda_dynamic_smem_limit` | non-constant `shared_mem=` with no limit comparison | 88 launches with `shared_mem=`, 52 non-constant with no limit comparison found by name in 31 files; the first five judged: `_added_information_gpu.py:108` (the candidates are pre-filtered by `fits`), `_cat_pair_joint_gpu.py:113` (`_MAX_CELLS = 8192`, 32 KB, documented at line 23), `_bootstrap_rank_mi_tied_gpu.py:339` (`_launch_shared_bytes` helper) are FALSE; `_kernels_cupy.py:352` benign (`2*k*8` with k bounded by the block) | the limit is checked through differently named constants; name matching fails. Not recommended; the NVIDIA rule is real, but the check would need value ranges |
| P8 | `cuda_tuned_constants` | size comparison against a literal/constant selecting a gpu branch in a module with no ktc marker | name-based constants: 73 hits (first six: `_PARALLEL_THRESHOLD ... # measurement-backed fallback, used when the ktc has not tuned this shape yet`, `REGRESSION_THRESHOLD = 1.0` in a benchmark, ... all not violations or unclear). AST variant (compare against a constant of at least 256 selecting a branch that calls a gpu callable, module without any ktc marker): 2 sites, `_kernels_cupy.py:320 topk_ids.shape[1] > _MAX_STAGE4_K` FALSE (a capability guard), `discretization/_discretization_cuda.py:137 n_cols >= 1000` UNCLEAR (the dispatch may be tuned in another module) | zero reliable hits; not recommended |
| P9 | | module constants `_THREADS = 256` | 47 hits in 41 files | already (a) `cuda-literal-block-size` of `cuda_launch_geometry` (the corpus suppresses them with `# launch-ok: one block per candidate is the design`) |
| D15 | `cuda_block_size_shape` | `__launch_bounds__(N)` or a `THREADS`/`BLOCK` constant not a multiple of 32 | 0 hits in 155 sources | not recommended |
| D17 | extension of `cancellation_prone_moments` | `x/n - m*m` variance pattern in kernel text | 0 hits in 208 kernels | `cancellation_prone_moments` does not read string constants, so a future kernel with the pattern is invisible; the corpus is clean today |
| E1 | extension of `cuda_coalesced_reads` | strided global STORES `out[tid*K+col]` | 23 store sites in 21 of 208 kernels with a thread-derived index times a non-constant; first five distinct kernels: `wstrad[w * BMAX + ql]` FALSE (`w` is the warp index), `hess_s[a * p + b]` UNCLEAR, `out[(long long)k * n + i0 + r]` FALSE, `out[tid*K+col]` TRUE (`radix_select_f32_v4`), same TRUE (`radix_select_f32_bsearch`): 2 of 5 true (40%); needs a lane-versus-warp taint split | belongs to dimension 53's review of the coalescing gate; ADVISORY if built |

## Canary validation

`canaries.py` runs the final-spec prototype of G1-G11 over the 44 snippets above (and `canary_ktc_profile.py` the G12/G13 ones). Last run:

```
fp_types violation v1_bare_double_in_typed_source OK / v2_unsuffixed_literal_promotes_to_fp64 OK / clean c1 OK / c2 OK
warp, barrier, headers, atomics, smem, constptr, powsmall, kernel_cache, fallback_logged, parity_shape: all four snippets OK each
problems: 0
G13: V1 OK, V2 OK, C1 OK, C2 OK, C3 (CPU-only) OK;  G12: [_partial.py incomplete:occupancy, incomplete:throughput; _none.py missing]
```

Two prototype bugs found and fixed on the way, both worth carrying into the build: (1) the taint test matched the bare variable name `y` inside `blockIdx.y`, which turned a block-uniform guard into a thread-dependent one (3 phantom
barrier findings); (2) the marker test must be per kernel, not per line, otherwise the second use of a `double` in a marked kernel is reported.

## Findings

## 54-T1 (P2) Kernel bodies with bare `double` sit outside every gate and outside the FE contract
- **Evidence:** `final_measure.py` over mlframe `d2ef3afd6` (non-benchmark, 208 kernels): `cuda-bare-fp-type` in `_resident_candidate_mi.py::_BUILD_OPCANDS_CM_SRC` and `::_BUILD_OPCANDS_SRC`, `info_theory/_cmi_cuda.py::_CMI_FROM_JOINT_SRC`,
  `shap_proxied_fs/_shap_proxy_gpu.py::_KERNEL_SRC`, and (whole-source scan) `_quantile_codes_gpu.py::_SRC`. Each source declares `{REAL}`/`{ACC}` and also writes `double` (for example `double* red = (double*)shmem;`, `const double inv_n`). None of the four files
  is listed in `tests/test_meta/_fe_gpu_contract.json` (`compliant`, `exempt`, `pending` all checked by name). `cuda_kernel_pointer_types` passes them: its rule reads only `double*`/`float*` parameters (`cuda_kernel_pointer_types.py:11` docstring).
- **Impact:** contract point 2.3 (CUDA.md:25) "No bare double or float" is enforced only by a regex in a mlframe-local test over the 139 compliant modules; a module outside the list is free to violate it, and `_cmi_cuda` computes four entropies in `double`
  regardless of the crit dtype (documented in its docstring as intended). Also the local regex scans raw text including comments.
- **Proposed fix:** gate G1 `cuda_kernel_fp_types` (ADDITIVE, opt-in). mlframe then marks the intentional ones `// fp-type-ok:` or converts them.
- **Disposition:** RESOLVED (62f3f61): new gate `cuda_kernel_fp_types`; 5 sources in 4 mlframe files reported. Status 2026-11-16: RESOLVED, gate on master (commits 62f3f61 and c807641, 219 tests of the 11 cuda gate test modules pass).

## 54-T2 (P2) Legacy `__shfl*` compiles on the Pascal host and fails on every sm_70+ target; no gate sees it
- **Evidence:** `nvrtc_probe.py`: `__shfl_down(x[threadIdx.x], 1)` compiles for `compute_61`, `t.cu(1): error: identifier "__shfl_down" is undefined` for `compute_70` (NVRTC 12.9). The corpus has none today (0 hits), so no demonstrated failing consumer input.
- **Impact:** CUDA.md:15 names a second machine (cc 8.9) and a third; py-ci-shared consumers run CPU-only CI, so a kernel written on the dev host would first fail on the newer machine. No existing gate checks the intrinsic names
  (`cuda_offline_report` builds per architecture but needs the toolkit and a consumer to call it).
- **Proposed fix:** G2 `cuda_warp_intrinsics`, rules `cuda-legacy-shfl` (HARD) and the others as specified. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new gate `cuda_warp_intrinsics` (legacy unmasked warp intrinsics, sm_70+). Status 2026-11-16: RESOLVED, gate on master (commits 62f3f61 and c807641; its tests pass in the 219-test targeted run).

## 54-T3 (P2) NVRTC lacks more than `INFINITY`; CUDA.md names one macro and no gate checks any
- **Evidence:** `nvrtc_probe.py`: `INFINITY`, `NAN`, `FLT_MAX`, `INT_MAX`, `HUGE_VALF` each give `identifier ... is undefined` on both architectures; `__int_as_float(0x7f800000)` compiles. CUDA.md:376 states only `INFINITY`. 0 uses in the corpus.
- **Impact:** a kernel using `FLT_MAX` (the natural choice for a min-reduction seed) fails at first compile, which CPU-only CI never reaches.
- **Proposed fix:** G4 `cuda_nvrtc_headers`; also widen the CUDA.md sentence. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new gate `cuda_nvrtc_headers`; nvrtc 12.9 leaves 17 macros undefined, all re-verified. Status 2026-11-16: RESOLVED, gate on master (commits 62f3f61 and c807641; `cuda_nvrtc_headers` tests pass in the 219-test targeted run).

## 54-T4 (P2) The residency contract of section 4 is declared by 1 of 72 `gpu_capable` registrations
- **Evidence:** `grep -rn "gpu_capable=True"` over mlframe `src` = 76 sites (4 in `_benchmarks`); `grep -rn "residencies="` = 2 matches, one of them a docstring in `_ktc_fallback.py:31`, the other the only real declaration (`_unary_elementwise_tuning.py:95`).
  `_SWEEP_RESIDENCIES` appears only in `_ktc_fallback.py:80,86`, where it defaults to `[("host", "host")]`. CUDA.md:70: "Every host-versus-device entry is swept in the placements its callers can have ... A module declares which of them its callers reach".
- **Impact:** 71 GPU crossovers are measured in one placement; per CUDA.md:68 a verdict is "only valid for the data placement the sweep used", so a caller with resident operands gets the host-in threshold. This is the mechanism behind the section 6 note that a kernel which wins resident and
  loses end to end is a residency problem.
- **Proposed fix:** G13 `cuda_ktc_residency`, ADVISORY with a ratchet baseline of the 71 sites; each marker states the single boundary. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new gate `cuda_ktc_residency`; 76 registrations in mlframe would need a baseline. Status 2026-11-16: RESOLVED, gate on master (commits 62f3f61 and c807641; tests pass in the 219-test targeted run).

## 54-T5 (P2) The profile-record contract is a mlframe-local regex over a hand-kept list; 48 of 91 kernel files have no record
- **Evidence:** `fe_gpu_coverage_inventory.py:396-448` (`contract_violations`) is the only implementation; the `_fe_gpu_contract.json` counts are `compliant` 139, `exempt` 63, `pending` 113. Kernel-bearing non-benchmark files: 91; with `audits/2026-10-22/profiles/<stem>.md`: 43; of those, 31 lack at
  least one of cProfile / GPU profiler / launches per call / occupancy / throughput (checked by `re` over the record text). Upper bound for "no record": 48 (family records not mapped).
- **Impact:** a repo that adopts the CUDA contract has to copy the 475-line inventory module; the part that is generic (record exists, five required items, carved modules) is not shared.
- **Proposed fix:** G12 `cuda_profile_records` with `profiles_dir`, `required`, `exempt`, family mapping. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new gate `cuda_profile_records` (library form of the mlframe-local contract, ratchet baseline). Status 2026-11-16: RESOLVED, gate on master (commits 62f3f61 and c807641; tests pass in the 219-test targeted run).

## 54-T6 (P3) Ten kernels use floating-point `atomicAdd` with no determinism note, against the narrative of CUDA.md:48
- **Evidence:** `final_measure.py`, rule `cuda-float-atomic-add`: `_cell_moments_kernel.py` (4 kernels, `atomicAdd(&sh[f * nc + c], d2 * wi)` into a `double` shared array), `_fe_batched_mi_cmi.py::ent_nnz_1d`, `_gpu_resident_basis.py::abs_corr`,
  `_group_distance_gpu.py::gd_stats` and `gd_w1`, `_mi_greedy_cmi_fe_gpu.py::ent_nnz`, `_orthogonal_xi_gpu.py::xi_walk`. `grep -ci "determin|order-dependent|summation order|reproducib"` over the six files: 0, 1, 0, 0, 0, 0. CUDA.md:48: "a floating-point atomic sum is non-deterministic, while our integer counts in
  shared memory are exact".
- **Impact:** run-to-run differences in the last bits of cell moments and group distances, undocumented. mlframe's CLAUDE.md says bit-identity is not a requirement, so this is a documentation gap, not a defect; it matters when two code paths are compared for equality.
- **Proposed fix:** G5 `cuda_atomic_determinism`, ADVISORY with `atomic-fp-ok`. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new advisory gate `cuda_atomic_determinism`; 12 kernels in 7 files on mlframe. Status 2026-11-16: RESOLVED, gate on master (commits 62f3f61 and c807641; tests pass in the 219-test targeted run).

## 54-T7 (P3) `row_attention_stage4_fused` places three barriers after a thread-dependent `return`
- **Evidence:** `_kernels_cupy.py`, kernel `row_attention_stage4_fused`: `int t = threadIdx.x; if (t >= K) return;` then `__syncthreads()` ×3. The launch at `_kernels_cupy.py:352-354` is `(n_queries,), (k,)`, so `t >= K` never fires: latent, not live. (The same kernel also uses bare `double` and mixes `float inv_s = 1.0f / s;` with a `double s`.)
- **Impact:** if the block is ever launched wider than K (a tuned block size), the barrier is formally undefined; `compute-sanitizer --tool synccheck` would flag it.
- **Proposed fix:** G3 `cuda_barrier_divergence` (ADVISORY for the return form); in the kernel, replace the early return by a predicate. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new gate `cuda_barrier_divergence`; it reports exactly the three barriers of `row_attention_stage4_fused` on mlframe. Status 2026-11-16: RESOLVED, gate on master (commits 62f3f61 and c807641; tests pass in the 219-test targeted run).

## 54-T8 (P3) Silent device-fault fallbacks remain: two of five sampled handlers are real
- **Evidence:** `detect_p67.py`: 23 handlers in 20 files; judged five: `_fe_additive_fusion.py:163` (`except tuple(_dev_errs): pass`) and `_fe_batched_mi_cmi_hist.py:610` (`_sm_count` returns 16 when the query fails, unlogged) are real; the other three log in an outer handler or earlier.
  mlframe CLAUDE.md "Say so when a gate makes a run slower" and CUDA.md:84, 375.
- **Impact:** a silent CPU fallback is how a 50x regression survived for weeks (the 2026-08-02 entries in mlframe's CLAUDE.md). The sampled rate suggests about 9 of the 23 are real.
- **Proposed fix:** G10 `cuda_fallback_logged`, ADVISORY, marker `fallback-logged-ok`. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new advisory gate `cuda_fallback_logged`; 15 handlers on mlframe, about 2 of 5 real. Status 2026-11-16: RESOLVED, gate on master (commits 62f3f61 and c807641; tests pass in the 219-test targeted run).

## 54-T9 (P3) CUDA.md states two hardware facts that contradict the NVIDIA table
- **Evidence:** CUDA.md:59 "32 resident blocks on sm_86" and CUDA.md:253 "32 resident blocks on SM70 and later" (a tensormux claim). The NVIDIA compute-capability table gives 16 resident blocks per SM for cc 7.5 and 8.6 and 24 for 8.9 (32 for 7.0, 8.0, 9.0), and 1536 resident threads for 8.6 / 8.9, 1024 for 7.5. I could not
  fetch the page here: this is from my knowledge of the table, to be spot-checked at the Programming Guide's "Technical Specifications per Compute Capability". Also CUDA.md:253 "avoid 1024 (two blocks per SM on a 2048-thread SM)" holds only for cc 7.0, 8.0, 9.0.
- **Impact:** the occupancy arithmetic in the doc is used to pick geometry for the sm_89 machine (CUDA.md:15), where the block limit is lower than quoted.
- **Proposed fix:** correct the sentences in mlframe (outside this repo). A py-ci-shared `external_fact_tables` entry could pin the table with a dated check (that gate exists: `external_fact_tables.py`). ADDITIVE.
- **Disposition:** RESOLVED in mlframe `CUDA.md` (commit ab09d37a4 and later): the 32-blocks-on-sm_86 claim is corrected to the per-cc table, and the 1024-thread advice is limited to cc 7.0, 8.0 and 9.0. Checked by grep of the corrected sentences, not by a full re-read of the doc.

## 54-T10 (P3) Internal disagreements and stale counts inside CUDA.md
- **Evidence (each pair quoted from the file):** (1) CUDA.md:383 "Walk the 90 pending FE modules" against `_fe_gpu_contract.json`: pending 113, `n_pending_at_creation` 226 (compliant 139, exempt 63). (2) CUDA.md:46 "a launch costs about 5 to 20 us" against CUDA.md:200 "The GPU has a fixed cost of about 0.5 ms per launch": the second is the
  end-to-end cupy call (argument packing, Python), the first the hardware figure; neither is wrong, but a crossover derived from one is 25-100x off from the other. (3) CUDA.md:40 "float32 terms agree with the float64 CPU result to about 4e-9 relative on the pair kernel" against CUDA.md:200 "its difference to the CPU is about 1e-8 for float terms" (same comparison, two numbers).
  (4) CUDA.md:40 tests allow 1e-6 for float and 1e-9 for double, CUDA.md:57 and 256 give fp32 rtol 1e-5 / atol 1e-8 (tensormux): the project tolerance is tighter than the third-party one; the doc does not say the choice is deliberate. (5) CUDA.md:46 "`gridDim.y` and `gridDim.z` stay below 65,535": the limit is inclusive.
  (6) CUDA.md:376 names one NVRTC macro (54-T3).
- **Impact:** a reader who computes a launch-bound crossover or a tolerance from the wrong sentence gets a wrong answer; section 8 already warns that extractions were made by a small model (CUDA.md:208, 388).
- **Proposed fix:** edit the doc in mlframe; `prose_numeric_claims` (py-ci-shared) can pin the module counts ("90 pending") to the JSON. ADDITIVE.
- **Disposition:** RESOLVED in mlframe `CUDA.md` (commit ab09d37a4 and later): the pending count is 113 with the JSON's own numbers, the 5-20 us hardware launch and the 0.5 ms end-to-end cupy call are stated as different things, the gridDim limit is inclusive, and the NVRTC macro list is corrected. Checked by grep of the corrected sentences, not by a full re-read.

## 54-T11 (P2) GPU-vs-host parity tests use one seed in 67 of 72 cases, against the rule written for the incident
- **Evidence:** `detect_parity3.py` over `mlframe/tests` (snapshot): 72 tests that call a GPU-named and a host-named callable, build random data and compare, 5 with two or more seed sources, 59 with one literal seed, 8 with none. CUDA.md:367: "must pass on 2 or more independent seeds/datasets, not one fixture ... (`_fe_accuracy_gate_kernel.py`, 2026-11-11: converged on the first seed tried, diverged on 4/100 combos of a second)".
- **Impact:** the incident class (a kernel that mirrors an iterative routine diverging on a boundary input) is untested by construction in most parity tests; the rule exists only as prose.
- **Proposed fix:** G11 `cuda_parity_test_shape` as ADVISORY with a ratchet baseline of the 67. ADDITIVE.
- **Disposition:** RESOLVED: every GPU-vs-host parity test in mlframe that compares device with host values runs on two seeds or datasets (commits b38c7d1, b58922a93, 7c3fbdd45 on mlframe master); the seven non-parity tests carry `# parity-ok:` reasons (commit ec6a5f852 and its successors). `cuda_parity_test_shape` reports 0 hits on mlframe at 0af26b487, checked 2026-11-17.

## Considered and not raised

- **Unguarded global index (D9)**: the naive regex gave 11 hits, all false positives (guards written as `row >= (long long)kc * pbc` or grid-stride loops). With a guard defined as any comparison on the id or a derived variable, 0 of 103 kernels that compute a global id are unguarded. Rejected: zero-hit, the corpus guards.
- **Hard-coded device id 0 (P1)**: library code 0; the three hits are `_benchmarks` printing `getDeviceProperties(0)['name']` and `Device(0)` in `bench_mrmr.py:72`, plus two `'cuda:0'` strings in docstring examples (`providers.py:150,192`, `neural/data.py:364,559`). Rejected.
- **Kernel constructed in a hot function (P2)**: 15 sites flagged, 15 are caches (`get` then store, under a lock). True count 0. Kept as G9 (regression guard), not raised as a finding.
- **Transfer inside a loop (P3)**: 214 sites; the first five are block streaming (the section 5 design) or benchmarks. Refined: 2 of 7 true (29%). Rejected as a gate; the true cases (`_gpu_metrics.py:617-618`, a per-column device loop) belong to CUDA.md section 15.
- **No warm-up before a timed GPU call (P5)**: 6 hits, 0 true (production log timings; helpers warmed by their caller). A per-function rule cannot work; rejected.
- **Dynamic shared memory without a device-limit check (P6)**: 52 candidate launches, the first five are limited through `fits`, `_MAX_CELLS`, `_launch_shared_bytes`; name matching cannot see it. Rejected as a gate; the NVIDIA rule stands.
- **Tuned constant bypassing the KTC (P8)**: 73 name hits, none a violation on inspection of the first six; the AST variant finds 2 sites, 1 false, 1 unclear. `_PARALLEL_THRESHOLD = 20_000  # measurement-backed fallback, used when the KTC has not tuned this shape yet` is the fallback heuristic section 4 asks for. Rejected.
- **Literal block-size constants (P9)**: already `cuda-literal-block-size`; the corpus suppresses 47 with `# launch-ok`. Covered, not new.
- **`pow(x, 2)` and `cupy.power(x, 2)` (D4)**: 0 hits in kernels and Python. Specified as G8 only because the brief asked; PTX size is the only evidence (441 vs 35 lines for double). Not recommended.
- **Unpadded shared tile (D6)**: the corpus pads (`[32][33]`, `[8][33]`); the access-pattern refinement leaves 2 negligible hits. Specified as G6, ADVISORY, not in the top ten.
- **`variance = E[x^2] - E[x]^2` in kernel text (D17)**: 0 hits in 208 kernels (the project already fixed this class, mlframe CLAUDE.md 2026-09-02 follow-up). `cancellation_prone_moments` does not read kernel strings: a gap with no current instance.
- **Multiple of 32 / power of two for `__launch_bounds__` and thread constants (D15)**: 0 hits.
- **Legacy vote intrinsics (`__ballot`, `__any`, `__all`)**: still compile on NVRTC 12.9 for compute_70; kept ADVISORY inside G2, not a HARD rule, because I verified only that they compile, not that a later toolkit keeps them.
- **`__shfl_*_sync` with a non-full mask**: the corpus does not use partial masks; the prototype rule `D3c` (mask variable or literal other than the full mask) would have flagged legitimate computed masks (ballot results). Dropped from the spec.
- **"Win axis" note for versioned kernels, "memory bound as a test", "size block from `memGetInfo`" (section 3 / 5)**: classified (c) weak: they need free-text or name matching and I could not make a measurable detector; not specified.
- **Whether `gpu_timing_sync` fully covers the sync-in-timed-region rule**: its docstring claims direct cupy/cuda/torch/numba calls, kernel launches and injected callables in GPU-aware modules; depth is dimension 53 (`53_cuda_gate_review.md`). Not re-audited here.
- **`MLFRAME_DISABLE_GPU` honoured (section 4)**: (d) by the project's own argument (`test_gpu_optout_makes_no_device_allocations.py` docstring: a structural gate would be about 100 false positives); the runtime test is the right shape.
- **Occupancy, bandwidth, speedups, Nsight metric tables, bottleneck thresholds (sections 6, 8)**: (d), need a profiler or a timing run.

## Ranked: the ten best new gates to build

| rank | module | evidence for the rank |
|---|---|---|
| 1 | `cuda_kernel_fp_types` (G1) | The only gate with a real, currently-live contract violation outside every existing check: 5 type-generic sources in 4 files with bare `double`, none in the FE contract list, invisible to `cuda_kernel_pointer_types`; verified FP64 promotion (PTX `.f64` 4 vs 0); precision 5 of 5 on the five; centralises a rule that exists today only as a mlframe-local regex over 139 modules. Spec is simple and the marker removes intentional cases. |
| 2 | `cuda_ktc_residency` (G13) | The largest measured gap against a written rule: 71 of 72 `gpu_capable` registrations declare nothing, and cuda.md:70 says every one must. Exact AST detection with no false-positive risk in the detector; cost is a 71-entry baseline. It is the formal form of the section 4/6 residency lesson (a kernel that wins resident and loses end to end). |
| 3 | `cuda_profile_records` (G12) | Contract point 5 exists only as a copy-only mlframe inventory (475 lines). Measured: up to 48 of 91 kernel files without a record and 31 of 43 records incomplete. Library form lets py-ci-shared consumers (autopsia, pyutilz) adopt the contract without copying code. |
| 4 | `cuda_parity_test_shape` (G11) | Backed by a real incident (4 of 100 combos diverged on a second seed); 67 of 72 current parity tests use one seed or none; precision about 60% on the five checked (3 true, 2 unclear). Needs a ratchet baseline from day one, hence below the exact-detection gates. |
| 5 | `cuda_warp_intrinsics` (G2) | Verified on this host: `__shfl_down` compiles for compute_61 and is undefined for compute_70 on nvrtc 12.9. Zero hits today, but CPU-only CI cannot see the failure and a sm_89 machine exists; HARD rule is documented and verified; cheap regex; four canaries pass. |
| 6 | `cuda_nvrtc_headers` (G4) | Verified: five identifiers undefined on nvrtc 12.9 (cuda.md names one). Zero hits today, same blind spot as rank 5 (CPU-only CI never compiles the source); trivial detector; HARD. |
| 7 | `cuda_atomic_determinism` (G5) | Real and common: 10 kernels in 9 sources / 6 files use FP atomics with no note, 5 of 5 judged true. ADVISORY because mlframe does not require bit-identity; value is that the gap between cuda.md:48 and the code becomes visible and each exception is justified in writing. |
| 8 | `cuda_barrier_divergence` (G3) | Authoritative NVIDIA rule; 1 latent true positive found (`row_attention_stage4_fused`, three barriers after a thread-dependent `return`); the prototype's first run showed how easily taint analysis goes wrong (the `blockIdx.y` bug), which the canaries now pin. HARD for the in-branch form, ADVISORY for the return form. |
| 9 | `cuda_fallback_logged` (G10) | Directly enforces mlframe claude.md "Say so when a gate makes a run slower" and cuda.md:84, 375; 23 sites, 2 of 5 judged real (40%), all false positives are "logged in an outer handler" and a marker states it. ADVISORY because of the 40%. |
| 10 | `cuda_const_restrict` (G7) | Cheap, NVIDIA-documented, and the corpus follows the convention (726 of 1032 pointer parameters are `const`), so the one real hit (`compute_mi_from_classes_cuda`) is a clean signal; the prototype history (15 -> 5 -> 1 hits) is a warning about alias and nested-subscript handling that the spec records. Lowest value of the ten, but the lowest risk. Next in line, not in the ten: `cuda_kernel_cache` (G9, 15 of 15 caches recognised, 0 violations), `cuda_smem_bank_padding` (G6, 2 negligible hits). |

| id | sev | title |
|---|---|---|
| 54-T1 | P2 | Kernel bodies with bare `double` sit outside every gate and outside the FE contract |
| 54-T2 | P2 | Legacy `__shfl*` compiles on the Pascal host and fails on every sm_70+ target; no gate sees it |
| 54-T3 | P2 | nvrtc lacks more than `INFINITY`; cuda.md names one macro and no gate checks any |
| 54-T4 | P2 | The residency contract of section 4 is declared by 1 of 72 `gpu_capable` registrations |
| 54-T5 | P2 | The profile-record contract is a mlframe-local regex over a hand-kept list; 48 of 91 kernel files have no record |
| 54-T6 | P3 | Ten kernels use floating-point `atomicAdd` with no determinism note, against the narrative of cuda.md:48 |
| 54-T7 | P3 | `row_attention_stage4_fused` places three barriers after a thread-dependent `return` |
| 54-T8 | P3 | Silent device-fault fallbacks remain: two of five sampled handlers are real |
| 54-T9 | P3 | cuda.md states two hardware facts that contradict the NVIDIA table |
| 54-T10 | P3 | Internal disagreements and stale counts inside cuda.md |
| 54-T11 | P2 | gpu-vs-host parity tests use one seed in 67 of 72 cases, against the rule written for the incident |
