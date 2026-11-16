# 53 cuda_gate_review: round 2026-11-16 (T)

Scope: the 11 gates cuda_coalesced_reads, cuda_kernel_factories, cuda_kernel_integer_width, cuda_kernel_names, cuda_kernel_pointer_types, cuda_kernel_sources, cuda_launch_geometry, cuda_offline_report, gpu_timing_sync, nested_numba_parallel_guard, numba_seed_range.
Commit audited: py-ci-shared 48e49b1. Corpus: mlframe src/mlframe at HEAD d2ef3afd6 with 35 uncommitted files (moving snapshot, measured 2026-11-16).
Method: read the modules (the helper bodies of numba_seed_range and nested_numba_parallel_guard were NOT read line by line); ran every gate over the corpus; ran about 55 adversarial snippets (scratch `audit_T\53\adv.py`, `adv2.py`); mutation-tested each canary by loading a modified copy of the module (31 rule mutations) and re-running the violation and clean canary files (`mut.py`); checked README/registry/test-file rows; grepped the corpus for kernel forms.
Not covered: cuda_offline_report was read but not run (no NVRTC compile and no GPU launch was done at all). cuda_kernel_names needs a consumer-built `compiled` map, so its 220 hits with `compiled={}` on mlframe are meaningless. Rule coverage against CUDA.md is by headings plus a keyword grep, not a line-by-line reading. Unit tests (`tests/test_<gate>.py`) were not examined individually.

## Verdict table

| gate | verdict | main issue |
|---|---|---|
| cuda_coalesced_reads | WEAK | misses `X[threadIdx.x*n]`, pointer-offset rows, macros; scans raw .py text (comments), wrong line, duplicate-name key |
| cuda_kernel_factories | OK, narrow | naming convention `kernel(cp, real)` only; canary never exercises the registered path |
| cuda_kernel_integer_width | OK | empty-reason suppression; `size_t x = int*int` not flagged; printf-string FP; canary blind to product rule |
| cuda_kernel_names | OK | canary proves nothing about matching; both `__launch_bounds__` placements handled |
| cuda_kernel_pointer_types | DEFECT | `__global__ __launch_bounds__(N) void k(double*)` silently skipped (53-T1); `double2*`, `double x[]`, typedef/#define missed |
| cuda_kernel_sources | WEAK extractor | inline `RawKernel("""...""")` argument not extracted; multi-literal sources keep only the literal holding `__global__` |
| cuda_launch_geometry | OK | canary blind to loose/numba/cross-module paths |
| cuda_offline_report | OK, table gap | ARCH_LIMITS lacks sm_80/sm_100 though required_arch returns 80 |
| gpu_timing_sync | DEFECT (FN) | does not see the launch idiom `kernel((g,),(b,),args)` on a RawKernel variable, torch tensor ops, array methods |
| nested_numba_parallel_guard | OK | 9 corpus hits, 1 traced TP; canary has no per-rule token |
| numba_seed_range | OK, contract drift | signature lacks `min_files`/`allow_unparsed`; canary blind to 1 of 3 mutations |

## Corpus run (mlframe snapshot)

| gate | hits | sample judgement |
|---|---|---|
| kernel_sources | 173 kernel-bearing assignments (about 85 distinct kernels) | extractor |
| coalesced_reads | 1 (`_fe_pure_form_retention_gpu_resident.py:104 pair_gram_blocks`) | UNCLEAR: `fc[r * row_stride + p*col_stride]` with r thread-strided is coalesced only if row_stride is 1 at runtime; the A/B reads are coalesced |
| integer_width | 0 | corpus clean for the rules as written |
| pointer_types | 0 hard, 14 float (advisory) | 5 of 5 sampled float hits are kernels deliberately float32 (`radix_select_f32`, `transpose_f32`, `fe_materialise*`, `bin_codes_f32e_OUTNAME`, `row_attention_stage4_fused`): false positives |
| launch_geometry | 0 | consistent |
| kernel_factories (registered=()) | 16 | meaningless without the consumer registry |
| gpu_timing_sync | 0 on every .py | suspicious, see 53-T2 |
| nested_numba_parallel_guard | 9 | `_engineered_gates.py:185` TRUE POSITIVE: `pool.map(_keep_at)` reaches `fast_r2_score(...)` at `_fe_accuracy_gate.py:126`, outside the `parallel_kernel_entry()` blocks at lines 113 and 119. Others (`_orth_auto_scorer_fe.py:349,388`, `_orthogonal_jmim_fe.py:394`, `_binning.py:175`) have the same shape, not traced individually |
| numba_seed_range | 0 | consistent |

Python-written GPU kernels: 21 non-benchmark `@..cuda.jit` global kernels (plus 5 device functions) in 9 files (decorator aliased `_nb_cuda.jit` or `cuda.jit`), found by AST (`pyk.py`). Mentions in the corpus: 288 RawKernel, 63 RawModule, 28 ElementwiseKernel, 17 ReductionKernel, 8 cupy.fuse, 0 cupyx.jit, 0 `.cu` files. No gate covers the 21 numba kernels beyond launch_geometry seeing `kern[blocks, 256](...)`.

## Findings

## 53-T1 (P1) cuda_kernel_pointer_types skips every kernel declared `__global__ __launch_bounds__(N) void name(...)`
- **Evidence:** `_KERNEL` = `__global__\s+(?:\w+\s+)*?void\s+(\w+)\s*\((.*?)\)\s*\{`. Run (adv.py): `extern "C" __global__ __launch_bounds__(256) void k(const double* x, int n) { }` gives `pt: []`. The other placement `__global__ void __launch_bounds__(256) k(const double* x ...)` is found, but under the name `__launch_bounds__`. cuda_kernel_names and cuda_coalesced_reads accept the bound before `void`.
- **Impact:** the hard rule (out-of-bounds read that poisons the context) silently passes for exactly the register-tuned kernels, which CUDA.md sections 10 and 14 recommend writing.
- **Proposed fix:** accept an optional `__launch_bounds__(...)` / `__maxnreg__` before and after `void` in `_KERNEL`, report the real name, add the form to the canary violation. BEHAVIOUR CHANGE (stricter; `What to do:` fix or mark `// dtype-ok:`).
- **Disposition:** RESOLVED (62f3f61): `cuda_kernel_pointer_types` finds `__launch_bounds__` / `__maxnreg__` kernels under their real name; 38 new tests failed on the old code first.

## 53-T2 (P1) gpu_timing_sync is blind to a launch through a kernel object
- **Evidence:** `_gpu_call_name` (gpu_timing_sync.py:134) accepts dotted names rooted at cuda/cupy/cp/cudf/cuml/torch.cuda/numba.cuda and `x[...](...)` subscripts. adv2.py: `K = cp.RawKernel(...)` then `t0=perf_counter(); K((n,),(256,),(x,n)); return perf_counter()-t0` gives `[]`; same with an imported `kern` gives `[]`; `x.cuda() @ x.cuda().T` gives `[]`; `a.sum()` on a cupy array gives `[]`. mlframe has 288 RawKernel mentions and the gate returns 0 findings over all its files, and cannot tell clean from blind.
- **Impact:** CUDA.md section 6 rule 1 (recorded 16x phantom win) is enforced only for `cp.xxx(...)` calls, not for the kernels the project writes.
- **Proposed fix:** collect names bound to `cupy.RawKernel/ElementwiseKernel/ReductionKernel/RawModule(...).get_function` and numba.cuda.jit functions via ImportAliases, and count a call to such a name, or any call of the `(grid_tuple, block_tuple, args)` shape (reuse `cuda_launch_geometry._launch_block`), as GPU work. BEHAVIOUR CHANGE (can turn a consumer red; `What to do:` add a sync or the marker).
- **Disposition:** RESOLVED (62f3f61): `gpu_timing_sync` counts a launch through a kernel object as device work. Finding the gate blind was NOT confirmed on mlframe: 1479 timed regions, 24 with device work, all 24 synchronized, 0 findings before and after.

## 53-T3 (P2) gpu_timing_sync: any `synchroni*` call name ends a region, and any `x[k](...)` is GPU work
- **Evidence (adv2.py):** `log.synchronize_with_remote()` after `cp.matmul` gives `[]` (the word rule is deliberate, line ~70, but the receiver is not checked); `d[k](x)` (dict dispatch) between two perf_counter reads gives a `direct-gpu-call` finding (false positive; shape 1 does not require a GPU-aware module).
- **Impact:** a silent pass for a misnamed barrier; a false red for dict dispatch.
- **Proposed fix:** require the subscript-call base to be a known kernel (T2) or the file to import numba.cuda; restrict sync words to device-ish receivers or bare names. ADDITIVE for the FP fix, BEHAVIOUR CHANGE for the FN fix.
- **Disposition:** RESOLVED (62f3f61): only a device barrier ends a timed region, and `d[k](x)` is no longer a launch.

## 53-T4 (P2) cuda_coalesced_reads misses common strided forms
- **Evidence (adv.py):** `X[threadIdx.x * n]` no hit (`_strided` considers only loop vars and ids assigned from threadIdx); `const float* row = X + i * n_feat; row[3]` no hit; `X[idx[i] * n_feat]` no hit; `#define ROW(i) ((i)*n_feat)` no hit. Handled: direct `i*n_feat`, loop-variable form, hoisted offset, escaped-newline strings, `blockIdx.x*n + t` (correctly clean), `threadIdx.y` rows (correctly clean).
- **Impact:** the pointer-offset row base is a common spelling of the 6.7% gld-efficiency case the rule was written for.
- **Proposed fix:** treat `threadIdx.x` itself as a candidate var in `_strided`; track `T* p = X + <thread var> * <name>` and then any `p[...]`. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED (62f3f61): `cuda_coalesced_reads` reports the pointer-row stride forms; 1 to 15 findings on mlframe (Behaviour change).

## 53-T5 (P2) cuda_coalesced_reads reads raw .py text; comments count, the line and the baseline key are wrong
- **Evidence:** `find_cuda_coalesced_reads` passes `parsed.source` to `uncoalesced_kernels`. A Python comment `# __global__ void fake(float* X, int n) { int i=threadIdx.x; float v = X[i*n]; }` gives `cuda-strided-row-read` (false positive). Line is `source.find(name)`: a kernel on line 6 whose name also appears on line 2 is reported at line 2. Two sources with the same kernel name in one file give two findings with the same key `m.py::same`, so one baseline entry accepts both.
- **Impact:** false red from commented-out kernels; baseline cannot tell two kernels apart; wrong lines.
- **Proposed fix:** iterate literals extracted by AST (as cuda_kernel_sources does), key `path::NAME#k` for duplicates, line from the literal. BEHAVIOUR CHANGE (keys change only for duplicate names).
- **Disposition:** RESOLVED (62f3f61): `cuda_coalesced_reads` reads comment-stripped kernel text, so comments and docstrings no longer count and the line number is right.

## 53-T6 (P2) cuda_kernel_sources misses inline sources and truncates multi-literal sources
- **Evidence (adv2.py):** only `Assign`/`AnnAssign` values are read. `return cp.RawKernel("""extern "C" __global__ void inl(...)""", "inl")` is absent from the result. `HEAD = '...__global__ void parts(float* x) {'; BODY = ' x[0]=1; }'; SRC = HEAD + BODY` yields key `m.py::HEAD` with the first half only. Attribute, dict and class forms work (`mod`, `kernels`, `kernels#2`).
- **Impact:** an offline NVRTC check built on it never compiles those kernels, or compiles a truncated one.
- **Proposed fix:** also walk the arguments of RawKernel/RawModule/ElementwiseKernel/ReductionKernel calls (key by enclosing function), and resolve `Name` operands of a concatenation through module constants as cuda_kernel_integer_width does. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): `cuda_kernel_sources` extracts inline `RawKernel` arguments and joins `SRC = HEAD + BODY`.

## 53-T7 (P2) No gate covers the 21 numba.cuda kernels written in Python
- **Evidence:** AST count in mlframe (non-benchmark): 21 global and 5 device functions in 9 files. All C++ gates key on `__global__`.
- **Impact:** 32-bit index products, strided reads and float64 buffers are unchecked in them.
- **Proposed fix:** a sibling opt-in gate over the Python AST (`cuda.grid(1) * n` into int-typed names, strided subscripts on `cuda.threadIdx.x`). ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): new gate `numba_cuda_kernels` with four rules (unguarded grid index, divergent syncthreads, unsynchronised read-modify-write, float64 in a float32 kernel); 0 findings on mlframe's 25 kernels.

## 53-T8 (P2) Canaries do not prove most rules
- **Evidence (mut.py: a rule mutated out, violation and clean canary re-run):** CANARY BLIND (violation still fails, clean still passes) for: coalesced (indirect-run, hoisted offset, marker, shared blanking, group-index exemption: 5 of 5), integer_width (product rule, marker), pointer_types (probe rule, `{REAL}` exemption, float rule), launch_geometry (loose launch, numba launch, cross-module constants, int()/arithmetic literal), factories (registry lookup), names (coverage test, template-prefix match), gpu_timing_sync (injected callable, `.cpu()` sync, sync-before-work, marker, asnumpy, subscript launch: 6 of 6), nested (joblib rule), numba_seed_range (wide-binop). NOTICED for comment stripping, long-long exemption, dtype-ok marker, launch-ok marker, and two seed-range mutations. The harness asserts "fails" and for two gates one token; cuda_kernel_names runs with `compiled={}`, so a gate that reports everything passes.
- **Impact:** a refactor that drops a rule keeps `test_gate_teeth` green (unit tests may still catch it; not checked).
- **Proposed fix:** `tokens=` for every rule a violation seed targets, one violation seed per rule, clean seeds that hit each exemption (registered factory, compiled map hit, sync after injected callable). ADDITIVE.
- **Disposition:** RESOLVED (the commit that adds the cuda_kernel_names and cuda_kernel_factories canary seeds, same change as this note): mutation pass on a scratch copy, 19 rule mutants. Before: 2 of 10 names mutants and 1 of 9 factories mutants noticed (the canaries used `compiled={}` and a single unregistered factory). After: every non-equivalent mutant noticed by the canary (names: N1, N4, N6-N11; factories: F1-F8 with F2b added). Two names mutants are equivalent on valid CUDA and need no seed: dropping the `__global__` prefilter (the findall returns nothing without it) and dropping `- {"void"}` (the optional `void` group consumes the keyword; `__global__ void(` is not valid CUDA). The seeds: compiled-map coverage (exact, template prefix, per-file, prefix length), launch-bounds before `void`, the excluded path part, nested and method factories, a second parameter that is not `real`, and the registry match by module, tail module and name. Pytest: `tests/test_gate_teeth.py` plus both unit tests, 221 passed.
- Earlier note (superseded): for the six repaired gates the authors removed each rule in turn and the canaries notice it (pointer_types, integer_width, coalesced_reads: 24 mutations caught by canaries and 2 more by unit tests; each new rule of gpu_timing_sync, numba_seed_range and cuda_launch_geometry caught); `cuda_kernel_names` and `cuda_kernel_factories` have had no mutation pass yet.

## 53-T9 (P3) Suppression markers accept an empty reason
- **Evidence:** `// width-ok:` followed by a newline suppresses (adv.py: `iw: []`). `dtype-ok:`, `launch-ok:`, `gpu-timing-async-intentional`, `seed-range-ok` likewise. Only `coalescing-ok:` requires about 12 characters (`_MARKER_RE`).
- **Impact:** "the reason is the review" is not enforced.
- **Proposed fix:** require a non-empty reason after each marker. BEHAVIOUR CHANGE (rare).
- **Disposition:** RESOLVED (62f3f61): a suppression marker with an empty reason no longer suppresses, in every gate that has one (pointer_types, integer_width, coalesced_reads, gpu_timing_sync, cuda_launch_geometry, numba_seed_range). Behaviour change.

## 53-T10 (P3) pointer_types and integer_width small gaps
- **Evidence (adv.py):** `const double x[]` and `double2*` not flagged; `typedef double real;` or `#define T double` then `const T* x` not flagged; `{REAL}*` next to an untemplated `double* acc` skips the whole kernel (`any(token in params)`); `size_t idx = n * m;` not flagged by cuda-int-index-product (matches `int` only); `printf("a long way")` gives a cuda-platform-long false positive. Handled correctly: `unsigned long`, `long2`/`ulong`, `long` newline `long`, `long double`, `unsigned int idx = a*b`, `(int)blockIdx.x * n`, comments, multi-line signatures, `static __global__`, `double**`, `.format` placeholders, a pattern split across a concatenation.
- **Proposed fix:** extend `_DOUBLE_PTR` to `double\d?\s*\*` and `double name[]`, resolve typedef/#define aliases inside the unit, strip string literals in `_strip_comments`. BEHAVIOUR CHANGE for the hard-rule extensions.
- **Disposition:** RESOLVED (62f3f61): `double2*`/`double4*`, `double x[]` and typedef/define/using aliases are reported by `cuda_kernel_pointer_types`.

## 53-T11 (P3) cuda_offline_report ARCH_LIMITS lacks sm_80 and sm_100
- **Evidence:** keys are 61, 70, 75, 86, 89, 90, 120; `required_arch` returns 80 for cp.async, bf16 and `__reduce_*_sync`; `blocks_per_sm(80, ...)` indexes `ARCH_LIMITS[80]` and raises `KeyError` (read from the code at the `ARCH_LIMITS[arch]` lookup; not run).
- **Impact:** an A100 sweep crashes instead of reporting.
- **Proposed fix:** add 80 `(2048, 32, 65536, 164*1024)` (and 100 if wanted). ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): sm_60, sm_80 and sm_100 added to `cuda_offline_report.ARCH_LIMITS`. The sm_100 limits were written from memory and still need a check against the NVIDIA table.

## 53-T12 (P3) numba_seed_range entry contract differs from the other gates
- **Evidence:** `find_wide_numba_seeds(root, *, skip_dir_names, include_tests, use_git)` has no `min_files` or `allow_unparsed` (CLAUDE.md requires both); calling it with `allow_unparsed=True` raised `TypeError`. An empty root passes silently.
- **Proposed fix:** add both parameters. ADDITIVE (default `min_files=1` is a BEHAVIOUR CHANGE only for an empty root).
- **Disposition:** RESOLVED (62f3f61): `find_wide_numba_seeds` takes `min_files` and `allow_unparsed` like the other gates.

## 53-T13 (P3) pointer_types advisory float rule is noise on mlframe
- **Evidence:** 14 `cuda-bare-float-pointer` hits; 5 of 5 sampled are deliberate float32 kernels (names above): 100 percent sampled false positives, over the 30 percent bar.
- **Proposed fix:** keep it advisory and skip kernels named `*f32*`/`*float*`. ADDITIVE.
- **Disposition:** RESOLVED (62f3f61): the advisory float rule skips float32-named kernels and modules that say float32; 14 findings on mlframe became 0.

## CUDA.md rule coverage by the existing gates

| CUDA.md rule | gate | coverage |
|---|---|---|
| s3/s14 block size from device limits | cuda_launch_geometry | strict, loose and numba launches and one-hop imported constants; not a literal default argument of a helper |
| s3 coalesced global access | cuda_coalesced_reads | partial (T4, T5) |
| s3/s10 no 32-bit indices, `long` is 4 bytes on Windows | cuda_kernel_integer_width | `long` fully; index products only `int`-declared and advisory |
| s2/s10 dtype templating `{REAL}` | cuda_kernel_pointer_types | defective with `__launch_bounds__` (T1) |
| s6 synchronize inside the timed region | gpu_timing_sync | partial (T2, T3) |
| s9 K, s10 register spills, launch bounds | cuda_offline_report (measures) and cuda_kernel_factories (registry completeness) | the spill assertion itself lives in the consumer |
| s10 every kernel compiled offline | cuda_kernel_sources + cuda_kernel_names | extraction gaps (T6) |
| nested numba parallel | nested_numba_parallel_guard | covered by a name-based call graph |
| seeds beyond int64 | numba_seed_range | covered |
| bank conflicts, vector loads, occupancy, bandwidth, section 8 third-party claims | none | profiler-only or third-party; not static gates |

## Considered and not raised
- cuda_kernel_integer_width `long` in comments, `long long`, `long double`, `ulong`: correctly ignored (adv.py).
- pointer_types: a double in a C comment is not flagged; `template<typename T>` kernels are not flagged by design (launch_dtype_audit covers them at run time).
- cuda_kernel_names handles `__launch_bounds__` both before and after `void` (adv2.py), and `static __global__`.
- All 11 gates have a README row, a registry.toml entry and a tests file (grep counts 1 each).
- mlframe gpu_timing 0 hits was not claimed as a false negative by itself; T2 rests on constructed snippets.
- Partial templating (`{REAL}` plus `double* acc`) is a documented design choice; kept as a P3 note in T10 only.

| id | sev | title |
|---|---|---|
| 53-T1 | P1 | pointer_types skips `__global__ __launch_bounds__(N) void` kernels |
| 53-T2 | P1 | gpu_timing_sync blind to launches through kernel objects |
| 53-T3 | P2 | gpu_timing_sync sync-name and subscript-call looseness |
| 53-T4 | P2 | coalesced_reads misses threadIdx.x*n and pointer-offset rows |
| 53-T5 | P2 | coalesced_reads scans raw text; wrong line; duplicate key |
| 53-T6 | P2 | kernel_sources misses inline args, truncates multi-literal |
| 53-T7 | P2 | no gate for 21 numba.cuda kernels in Python |
| 53-T8 | P2 | canaries do not prove most rules |
| 53-T9 | P3 | empty suppression reasons accepted |
| 53-T10 | P3 | pointer_types and integer_width small gaps |
| 53-T11 | P3 | ARCH_LIMITS lacks sm_80 and sm_100 |
| 53-T12 | P3 | numba_seed_range lacks min_files and allow_unparsed |
| 53-T13 | P3 | float advisory rule noisy on mlframe |
