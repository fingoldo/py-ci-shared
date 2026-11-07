# 50 New gate mining (round 2026-11-07)

Read-only. Sources: consumer git logs since 2026-10-17 (pyutilz, mlframe, glossum_backend_scripts, llm_bench, algopacksimple; autopsia is not checked out under Machine learning), audits 2026-11-04/06/07, the README catalogue and src/py_ci_shared (registry.toml lives at src/py_ci_shared/registry.toml, not the package root).

Classes dropped because an existing gate already covers them (checked against module docstrings, not assumed): stdlib API above the requires-python floor (pyutilz e909e02, zoneinfo on 3.8; api_floor names it), check-then-act on a shared dict (pyutilz d1af527, llm_bench 862d4cc; check_then_act_outside_lock), `x or default` zero fallbacks (glossum 4a18f42a; code_audit default_via_or), bare `double*` CUDA kernel (mlframe 6e2cc9c97; cuda_kernel_pointer_types), py-ci-shared pin skew across files (glossum 2371cbce; ci_pin_version_skew), ruff pin drift (pyutilz 9fac3ca; pinned_tool_versions), wall-clock sleeps (pyutilz f0e8cb3; wallclock_test_waits), meta-tests passing on an empty scan (mlframe 18617a2d8, pyutilz a811d4c; vacuous_loop_assertions, guard_population), sys.modules stub leaks (pyutilz 6bcb474, 1127eed; module_reload_safety). Mypy-only fixes (mlframe aea57b756, 9bdb50a88) belong to the mypy gate.

Caveat: hit counts come from quick scans (scratchpad scripts and mypy runs); the loaded machine cut some runs short and each affected count says so. The AST approximation in F1 is an upper bound, not a hit count.

| id | sev | title | hits |
|---|---|---|---|
| 50-F1 | P1 | possibly_unbound_local | mypy-measured 1 in 65 files scanned; AST upper bound is noise |
| 50-F2 | P2 | nested_numba_parallel_guard | 0 to 8 files outside mlframe (reachability not computed) |
| 50-F3 | P2 | uv_lock_parity | 0 now, 1 historical |
| 50-F4 | P3 | nan_admitted_by_reject_guard | 4 unreviewed regex hits (mlframe) |
| 50-F5 | P3 | instance_clear_leaves_module_cache | 1 (likely false positive) |
| 50-F6 | P3 | tool_config_env_vars_set_in_every_tool_job | 0 direct |
| 50-F7 | P3 | kernel launch dtype audit (runtime) | not run, needs a GPU |
| 50-F8 | P3 | njit twin parity (runtime) | 294 njit files, 8 test files use py_func |

## Findings

## 50-F1 [P1] possibly_unbound_local: a local bound only inside one branch of an if/try and read after it (UnboundLocalError on the cached or second call)

- Evidence: mlframe 169f17000 (Fix Muon Triton dispatch crashing on every call after the first): device_name and speedup were bound only in the `verdict is None` branch of maybe_newton_schulz_triton and read by a log line after it; every call after the first at a calibrated shape raised UnboundLocalError and crashed training; single-call gate tests passed. Existing coverage does not catch it: on a 10-line reproduction (scratchpad snip.py) `ruff check --select ALL` reports nothing for it (no rule), plain `mypy` and `mypy --check-untyped-defs` print only an unrelated var-annotated error, and only `mypy --check-untyped-defs --enable-error-code possibly-undefined` reports `Name "device_name" may be undefined`. src/py_ci_shared/mypy_gate.py has no enable-error-code, and the mlframe and pyutilz pyproject.toml enable no possibly-undefined (grep). HIT COUNT, measured with that mypy flag (read-only, --ignore-missing-imports, cache in scratchpad): llm_bench/src 0 of 45 files; pyutilz/src/pyutilz/core 1 of 20 files (core/array_summary.py:41, `njit` bound only in a try-import); the glossum/ and mlframe/training/neural runs exceeded the time budget on the loaded machine and are NOT counted. A crude AST approximation (names bound only in earlier compound statements, scratchpad unbound.py) over-reports (pyutilz 186, mlframe 4101, glossum 1624, llm_bench 29, algopacksimple 162) because it counts loop variables read after their loop; it is an upper bound, not a hit count.
- Failure scenario: A branch-local binding read after the branch passes every unit test that takes one path, then crashes in production on the second (cached) call, as the Muon dispatcher did.
- Proposed fix / AST sketch: Add the `possibly-undefined` error code to the shared mypy gate (non-test source), or implement an AST definite-assignment pass: per function, names bound only under if/try/for/while/with arms and read at function level after the construct, excluding loop targets, global/nonlocal and arms ending in raise/return/continue/break. Shrink-only baseline.
- AST checkable: True
- FP risk: medium for the pure AST version (loop variable after loop, try-import fallbacks, `while True` with break); low if delegated to mypy possibly-undefined (the one measured hit is a real try-import binding).
- Disposition: OPEN

## 50-F2 [P2] nested_numba_parallel_guard: promote mlframe's test_no_unguarded_nested_parallel (a parallel=True/prange kernel reachable from a thread pool must pass one process-wide lock)

- Evidence: mlframe src/mlframe/_numba_parallel_guard.py docstring: the first three-OS CI run crashed 92 xdist workers with `Fatal Python error: Aborted` on macOS (numba workqueue layer is not thread-safe; several pool threads stopped at one parallel=True call site); b5931c12b had to force NUMBA_THREADING_LAYER=tbb on the macOS CI leg; 12406c085 took 29 unguarded paths to 0. The check lives only in mlframe/tests/test_meta/test_no_unguarded_nested_parallel.py; grep of py-ci-shared/src for prange/parallel=True/THREADING_LAYER finds only loop_invariant_cast.py (unrelated), so no shared gate covers it. HIT COUNT (git grep, non-test files): mlframe parallel=True in 245 files and thread pools in 66 (guarded by the local test); pyutilz 4 parallel=True files (core/array_summary.py, performance/kernel_tuning/code_versioning.py, text/similarity/_numba_api.py, _numba_kernels.py) and 11 thread-using modules, none importing a guard; glossum_backend_scripts 1 (scripts/benchmark_fuzzy_reason.py) and 3 thread modules; algopacksimple 3 (walkforward/grid*.py) and 2 thread modules; llm_bench 0. Pool-to-kernel reachability was not computed outside mlframe, so the true count outside mlframe is between 0 and 8 files.
- Failure scenario: A library function with a prange kernel (pyutilz.text.similarity) called from a consumer's ThreadPoolExecutor aborts the process on macOS with no Python traceback, visible only on the macOS CI leg.
- Proposed fix / AST sketch: New gate nested_parallel_guard: collect @njit/@jit/@vectorize functions with parallel=True or prange; build the module-level call graph (reuse _import_graph); flag any function submitting to ThreadPoolExecutor/threading.Thread that transitively reaches such a kernel without the configured guard context manager on the path. Shrink-only baseline, guard name configurable (default parallel_kernel_entry), marker `# parallel-guarded: reason`.
- AST checkable: True
- FP risk: medium: transitive reachability over-approximates through dynamic dispatch; the per-repo guard name and the marker bound it.
- Disposition: OPEN

## 50-F3 [P2] uv_lock_parity: uv.lock must agree with pyproject.toml (requires-dist names and specifiers) without network

- Evidence: mlframe b5931c12b: 'uv.lock was out of step with pyproject.toml, so the lint job's `uv lock --check` failed on every run' (3 lock lines). glossum 2371cbce pinned py-ci-shared 'across CI, pyproject and uv.lock' by hand. Existing code only READS uv.lock (adoption_matrix.py _uv_lock_pins, _ci_install_parts.py package names); grep for 'uv lock' in py-ci-shared/src finds no parity check, so nothing fails before CI. Scan (scratchpad uvlock.py, tomllib) compares the root package's [package.metadata] requires-dist/requires-dev names with pyproject dependencies, optional-dependencies and dependency-groups. HIT COUNT today: mlframe 0, glossum_backend_scripts 0; pyutilz, llm_bench and algopacksimple have no uv.lock. Historical: 1 (mlframe) in 3 weeks; uv.lock changed 8 (mlframe) and 12 (glossum) times in that window, so the drift window is real.
- Failure scenario: A dependency edit in pyproject without `uv lock` leaves master red on the lint job (or installs the older locked set in a --frozen job) until someone notices; pre-push gates do not see it.
- Proposed fix / AST sketch: New gate uv_lock_parity: tomllib-parse pyproject and uv.lock; fail on a declared requirement missing from or differing in requires-dist/requires-dev (normalised names, specifier equality), a locked extra no longer declared, a requires-python mismatch; optionally run `uv lock --check --offline` when uv is on PATH.
- AST checkable: False
- FP risk: low: pure TOML comparison; risk is uv.lock format changes (gate on `version`/`revision`).
- Disposition: OPEN

## 50-F4 [P3] nan_admitted_by_reject_guard: `if loss >= worst: continue` used to keep a top-N admits NaN, because NaN fails the comparison

- Evidence: mlframe 584c9d880: CPU _insert_top admitted a NaN loss into the last slot because NaN fails the 'loss >= worst' reject; the later non-finite filter dropped it, so a cardinality returned one candidate short; fixed with `not loss < worst`. Existing NaN gates (polars_null_equality, epsilon_padded_denominators, ordinal_rank_scatter, njit_fastmath_nan) cover other shapes, none an inverted-comparison reject (checked docstrings). HIT COUNT (git grep regex for `if <loss|score|val|cost name> (>=|<=) (worst|cutoff|threshold|best_..)`, non-test, unreviewed): mlframe 4, pyutilz 0, glossum 0, algopacksimple 0; llm_bench not scanned.
- Failure scenario: A NaN candidate takes a top-N slot or passes a threshold reject; downstream non-finite filtering silently shrinks the result.
- Proposed fix / AST sketch: AST: an if whose test is one ordered comparison of a float-valued local against a name like worst/cutoff/best/thresh and whose body is continue/return/break must be written as the negated accepting comparison or be preceded by an isnan/isfinite test on the same name.
- AST checkable: True
- FP risk: high on names alone (ints, counters); needs float evidence (np.nan nearby, float annotation, sibling isnan); keep advisory.
- Disposition: OPEN

## 50-F5 [P3] instance_clear_leaves_module_cache: a clear/reset/release method touches only self state while sibling methods use a module-level cache container

- Evidence: mlframe 29f1e5c8d: xgb_shim clear_cache() reset instance pointers only, so the next fit hit the module-level LRU and reused the same QuantileDMatrix, keeping the C++ memory the docstring promised to free. No existing gate covers the method-level asymmetry (shared_mutable_placeholder, module_cache_thread_safety, module_state_test_reset address thread safety and test reset). HIT COUNT (scratchpad clearcache.py, non-test files of 5 repos): 1 total, pyutilz src/pyutilz/performance/kernel_tuning/cache/cache_class.py:352 KernelTuningCache.reset does not touch module-level _UNCONSTRAINED_DIM_WARNED (a warn-once set, probably intended, likely a false positive); mlframe 0 after the fix, glossum 0, llm_bench 0, algopacksimple 0.
- Failure scenario: A documented 'free memory / forget state' method leaves a process-wide cache populated and later calls reuse stale entries.
- Proposed fix / AST sketch: AST: per class, module-level mutable containers (dict/list/set/OrderedDict/deque) referenced by non-clear methods; a method named clear*/reset*/release*/free*/invalidate* that references self but none of those containers is flagged; marker `# cache-scope-ok: reason`.
- AST checkable: True
- FP risk: medium: warn-once sets and registries are intentionally process-wide; the one measured hit is of that kind.
- Disposition: OPEN

## 50-F6 [P3] tool_config_env_vars_set_in_every_tool_job: a variable a tool config reads ($VAR in [tool.ruff] extend) must be defined in every CI job that runs the tool

- Evidence: llm_bench d5990de: pyproject [tool.ruff] extend reads $PY_CI_SHARED_DIR; the meta-tests job ran the timezone_honest gate (which runs ruff) and failed 'environment variable not found' until a resolve step was added. ci_workflow_gate / ci_install_covers_* check installs, not config-referenced variables (no match for such a check in src). HIT COUNT (scratchpad envjobs.py: variables referenced by pyproject vs workflow jobs that call ruff directly, 5 consumers): 0 jobs missing the variable today; the indirect case (a pytest job whose tests shell out to ruff) is not statically visible, so real exposure is larger than 0.
- Failure scenario: A new CI job that runs ruff, or a test that shells out to it, fails on every run until the variable step is copied in.
- Proposed fix / AST sketch: Parse pyproject tool tables for ${VAR}/$VAR references, then require every workflow job with a step invoking the tool (or pytest over a tree with a test that runs it) to define VAR in env or write it to GITHUB_ENV earlier in the job.
- AST checkable: False
- FP risk: low for the direct form; the indirect pytest form needs an allowlist.
- Disposition: OPEN

## 50-F7 [P3] Runtime harness: launch-time kernel argument dtype audit promoted from mlframe, for templated kernels the static cuda_kernel_pointer_types gate cannot see

- Evidence: mlframe 6e2cc9c97: row_argmax declared `const double*` while resident operands were float32 (wrong indices, illegal address, `Fatal Python error: Aborted` in the next cp.argsort). The shared cuda_kernel_pointer_types gate (rule cuda-bare-double-pointer) already flags that exact kernel, so the original defect is covered and NOT proposed again. Not covered: a {REAL}-templated kernel launched with a buffer of the other dtype. mlframe's src/mlframe/feature_selection/_benchmarks/kernel_arg_dtype_audit.py (wraps cupy.RawKernel/RawModule, compares each launch's array dtype with the declared pointer, exit 1 on mismatch) is the only check and is mlframe-local. HIT COUNT: RawKernel/RawModule/ElementwiseKernel users: mlframe 105 files, pyutilz 2, glossum 0; the harness itself was not run (needs a GPU).
- Failure scenario: A templated kernel compiled for the wrong type reads half or double the buffer; the fault surfaces kernels later.
- Proposed fix / AST sketch: Move the wrapper into py_ci_shared as an opt-in runtime helper (like numeric_edge_matrix / target_leak_canary) with GPU skip and a `python -m` entry.
- AST checkable: False
- FP risk: low (runtime facts); needs a GPU CI leg, otherwise it only skips.
- Disposition: OPEN

## 50-F8 [P3] Runtime harness: pure-Python fallback vs njit twin parity (same arithmetic order, bit-identical on the same input)

- Evidence: mlframe 1b56cfb90: the pure-Python frac-diff fallback divided by weights[0] each step while the numba kernel multiplied by 1.0/weights[0]; 205 of 400 elements differed and the error compounds through the recurrence. Also 1822e5a0d (GPU stage accumulates in double to match the njit twin). Nothing in py-ci-shared compares a py_func to its compiled kernel (grep py_func in src: none). HIT COUNT: njit-using files: mlframe 285, pyutilz 6, algopacksimple 3; test files referencing .py_func: mlframe 5, pyutilz 3, so most twins are unchecked.
- Failure scenario: Machines without numba (or the NUMBA_DISABLE_JIT coverage job) silently compute different numbers than the production path.
- Proposed fix / AST sketch: Helper assert_njit_twin_parity(fn, inputs) running fn.py_func and fn on seeded inputs; plus an inventory ratchet over @njit functions with a Python-level fallback or twin name that no test mentions with py_func.
- AST checkable: True
- FP risk: medium: an inventory over every njit function is noisy; restrict to those with a fallback or twin.
- Disposition: OPEN
