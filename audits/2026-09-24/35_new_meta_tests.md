# Audit: new centralized meta-tests (proposals, each tracked as a finding)

Read-only research. Sources: README.md and the docstrings of all 110 modules in `src/py_ci_shared/`; local meta-tests in
autopsia, dash_app_core, glossum_backend_scripts, llm_bench, pyutilz, mlframe (worktrees skipped); the user memory dir;
`audits/TRACKER.md` (round 2026-09-04). "Local copy" below means a file that does NOT import `py_ci_shared`.

Already central, so not proposed again: import side effects, module reload safety, value-bearing asserts, source-text
claims, naive utcnow / timezone, latched availability flags, epsilon denominators, drifted duplicates, fail-open
handlers, nondiscriminating shapes, vacuous loops, private imports, loc/function budgets, gate integrity/population
canary, mutation teeth, marker/partition reachability, audit-round format, CI workflow gates. Mutable defaults live in
the pyutilz `code_audit` scanner (run via `code_audit_meta`).




   consumer hand-writes wrapper test files. The plugin reads `[tool.py_ci_shared] gates = [...]` plus per-gate config from
   pyproject and generates one test item per gate. Removes wrapper drift (proposal 2); adoption becomes one config line.
   counterpart; one parametrized test asserts each gate flags the seed and passes the clean one, and that applying the
   seed changed the file (feedback_teeth_check_must_assert_substitution_applied). A meta-test over
   `src/py_ci_shared/*.py` enforces that every gate has one.
   refactor that makes a gate inert fails here (per-gate version of `gate_population_canary`).
   pre-commit usable.
   `docs_inventory_parity`; README documents about 25 of 110 modules today.
   carry local copies, feeding proposal 2 automatically.

## Findings

### NEW-1 (High) -- gate `import_cycles`

**Disposition:** OPEN

- **Bug class:** module-level import cycles and import-order-dependent loading (bottom-of-module re-export)
- **Method:** AST import graph of top-level imports; SCC detection; second rule: a name re-exported below a sibling's top import
- **False-positive risk:** function-local imports excluded; baseline_ratchet for existing SCCs
- **Repos:** all
- **Evidence:** 5 local copies: autopsia/tests/test_meta/test_no_circular_imports.py, test_no_new_module_level_import_cycles.py, test_no_module_depends_on_import_order.py; llm_bench/tests/test_meta/test_no_import_cycles.py; pyutilz/tests/test_meta/test_no_import_cycles.py; mlframe/tests/test_meta/test_no_import_cycles.py
- **Effort:** M
- **Priority:** P0

### NEW-2 (High) -- gate `local_copy_report` (migrate locals onto existing central gates)

**Disposition:** OPEN

- **Bug class:** local copies drift from the central fix
- **Method:** report: local meta-test whose rule matches a central module but does not import it
- **False-positive risk:** report only
- **Repos:** llm_bench, pyutilz, autopsia, glossum, mlframe
- **Evidence:** module reload: llm_bench/tests/test_meta/test_no_unsafe_module_reload.py (538 LOC), pyutilz/tests/test_meta/test_no_module_reload.py, autopsia/tests/test_meta/test_no_reload_without_snapshot.py vs `module_reload_safety`; llm_bench/tests/test_meta/test_no_source_text_proxy_assertions.py (469 LOC) vs `source_text_claims`; glossum test_no_empty_or_tautological_tests.py vs `nondiscriminating_shapes`; mutable-default copies (memory: project_retire_local_mutable_default_copies)
- **Effort:** S
- **Priority:** P0

### NEW-3 (High) -- gate `swallowed_exceptions`

**Disposition:** OPEN

- **Bug class:** bare `except:` / `except Exception: pass` / `except OSError: pass` continuing with wrong state
- **Method:** AST: handler body is only pass/continue/debug-log, no re-raise and no returned sentinel
- **False-positive risk:** best-effort cleanup is legit: `# swallow-ok: <reason>` + ratchet
- **Repos:** all
- **Evidence:** pyutilz/tests/test_meta/test_no_bare_except.py and mlframe/tests/test_meta/test_no_bare_except.py (2 local copies); memory feedback_silent_correctness_bug_classes; swallowed OSError hid a 25 GB cache for 2 days (reference_np_save_appends_npy_breaks_staging_rename); TRACKER 20-F13, 20-F17
- **Effort:** S
- **Priority:** P0

### NEW-4 (High) -- gate `pickle_state_completeness` (runtime + static)

**Disposition:** OPEN

- **Bug class:** runtime cache attributes break pickle / joblib fan-out
- **Method:** runtime: consumer-listed factories, warm the object, pickle round trip; static: attribute assigned outside `__init__` named `_cache/_memo/_buf/_handle` absent from `__getstate__` exclusions
- **False-positive risk:** fixtures supplied by consumer; static half ratcheted
- **Repos:** mlframe, pyutilz
- **Evidence:** feedback_runtime_caches_break_pickle; mlframe test_cluster_aggregate_setstate_default.py
- **Effort:** M
- **Priority:** P1

### NEW-5 (High) -- gate `test_resource_leaks` (pytest plugin)

**Disposition:** OPEN

- **Bug class:** tests leaking subprocesses, threads, sockets/DB connections, `logging.disable`, env vars
- **Method:** autouse fixture: snapshot psutil children, threading.enumerate, open sockets, `logging.root.manager.disable`, os.environ diff; fail at teardown
- **False-positive risk:** known daemon threads allowlisted by name
- **Repos:** all
- **Evidence:** feedback_tests_must_not_leak_processes_or_connections (50 leaked trends_rollup runs, upwork); mlframe test_no_module_level_logging_disable.py, test_no_module_level_env_mutation_in_tests.py, test_no_numba_config_env_restore_footgun.py
- **Effort:** M
- **Priority:** P1

### NEW-6 (High) -- gate `atomic_write_staging`

**Disposition:** OPEN

- **Bug class:** write-then-`os.replace` staging broken (np.save appends `.npy`; non-atomic cache rewrite)
- **Method:** AST: `np.save(p)` with p not ending `.npy` followed by `os.replace(p, ...)`; direct text-mode rewrite of a cache/baseline file without temp+replace
- **False-positive risk:** low; allow-comment
- **Repos:** mlframe, pyutilz, py-ci-shared itself
- **Evidence:** reference_np_save_appends_npy_breaks_staging_rename; TRACKER 21-F9
- **Effort:** S
- **Priority:** P1

### NEW-7 (High) -- gate `hash_key_determinism`

**Disposition:** OPEN

- **Bug class:** JSON serialised for a hash/cache key without key sorting
- **Method:** AST: `json.dumps`/`orjson.dumps` flowing into hashlib `.update`/key variable without `sort_keys=True`/`OPT_SORT_KEYS`
- **False-positive risk:** list payloads allowed
- **Repos:** all
- **Evidence:** feedback_json_hash_sort_keys; TRACKER 20-F1, 21-F1 (cache key incomplete, same round twice)
- **Effort:** S
- **Priority:** P1

### NEW-8 (High) -- gate `sentinel_or_fallback`

**Disposition:** OPEN

- **Bug class:** `x or default` where 0/""/False is a meaningful sentinel
- **Method:** AST: `BoolOp(Or)` reading a key/attr from a consumer-declared sentinel list (max_tokens, timeout, seed, n_jobs, limit)
- **False-positive risk:** general form is noisy: only declared names; reuse `optional_truthiness`
- **Repos:** pyutilz, glossum, llm_bench
- **Evidence:** reference_max_tokens_sentinel_zero_disables_derived_timeout (pyutilz `_timeout_for`)
- **Effort:** S
- **Priority:** P1

### NEW-9 (High) -- gate `no_xfail_to_defer`

**Disposition:** OPEN

- **Bug class:** xfail/skip parking a fixable bug
- **Method:** AST: every xfail/skip needs `reason=` naming an external component or tracked issue, xfail needs `strict=True`; count ratchet
- **False-positive risk:** free-text reasons: ratchet the count
- **Repos:** all (149 test files use xfail in mlframe/pyutilz/autopsia)
- **Evidence:** feedback_no_xfail_to_defer, feedback_dont_accept_documented_skips
- **Effort:** S
- **Priority:** P1

### NEW-10 (High) -- gate `timing_assertions`

**Disposition:** OPEN

- **Bug class:** single-shot wall-clock ratio asserts; tests on the real clock crossing a day boundary
- **Method:** AST: perf_counter/time.time pair feeding an assert without repeat/min-of-N; now()/time() plus calendar arithmetic without a freezer
- **False-positive risk:** `perf`-marked tests exempt
- **Repos:** mlframe, glossum, upwork
- **Evidence:** mlframe/tests/test_meta/test_no_single_shot_timing_assertion.py (4 flake fixes); glossum_backend_scripts/tests/test_meta/test_no_test_depends_on_the_wall_clock.py; feedback_browser_tests_control_the_clock
- **Effort:** M
- **Priority:** P1

### NEW-11 (Med) -- gate `stale_source_citations`

**Disposition:** OPEN

- **Bug class:** `file.py:NNN` citations in log strings/comments that rot
- **Method:** regex over literals and comments; verify line exists and holds the cited symbol
- **False-positive risk:** skip fixture data
- **Repos:** mlframe, glossum
- **Evidence:** mlframe/tests/test_meta/test_no_stale_source_line_citations.py (183 of 185 citations wrong); glossum_backend_scripts/tests/test_meta/test_no_stale_source_citations.py
- **Effort:** S
- **Priority:** P2

### NEW-12 (Med) -- gate `console_encoding_safety`

**Disposition:** OPEN

- **Bug class:** non-ASCII in print/log crashing on cp1251/cp1252 consoles
- **Method:** AST: non-ASCII literal in print()/logger.* args in production
- **False-positive risk:** i18n modules allowlisted
- **Repos:** pyutilz, autopsia, mlframe
- **Evidence:** pyutilz/tests/test_meta/test_no_unicode_in_console_output.py; feedback_windows_encoding
- **Effort:** S
- **Priority:** P2

### NEW-13 (Med) -- gate `lf_file_writes`

**Disposition:** OPEN

- **Bug class:** `Path.write_text` / text-mode open reintroducing CRLF into LF-required files
- **Method:** AST: write_text/open('w') without `newline=""` targeting .yml/.yaml/.sh/baseline json
- **False-positive risk:** ratchet
- **Repos:** py-ci-shared (17 modules use write_text), all
- **Evidence:** feedback_write_bytes_not_write_text_on_windows; feedback_teeth_check_must_assert_substitution_applied
- **Effort:** S
- **Priority:** P2

### NEW-14 (Med) -- gate `module_cache_thread_safety`

**Disposition:** OPEN

- **Bug class:** module-level mutable cache mutated in functions in a file with no Lock; lazy `from X import` inside joblib-threaded workers
- **Method:** AST: module dict mutated inside def with no `threading.Lock` in file; ImportFrom inside a function passed to `delayed()`
- **False-positive risk:** import-time-only caches allowed; ratchet
- **Repos:** mlframe, pyutilz
- **Evidence:** mlframe test_no_unlocked_module_cache.py (3+ independent fixes), test_no_lazy_from_import_under_joblib_delayed.py (NameError race)
- **Effort:** M
- **Priority:** P2

### NEW-15 (Med) -- gate `machine_specific_paths`

**Disposition:** OPEN

- **Bug class:** hardcoded drive/user paths, model cache dirs, DB URLs
- **Method:** regex over py/yaml/toml: drive letters, `C:\Users\`, `/home/<u>`, literal `postgres://`
- **False-positive risk:** md docs skipped; allowlist
- **Repos:** all
- **Evidence:** glossum test_no_machine_specific_paths.py, test_no_hardcoded_model_cache_paths.py, test_no_hardcoded_db_url.py
- **Effort:** S
- **Priority:** P2

### NEW-16 (Med) -- gate `hardcoded_token_ceilings`

**Disposition:** OPEN

- **Bug class:** literal `max_tokens` at LLM call sites truncating paid output
- **Method:** AST: max_tokens/max_output_tokens keyword with int literal
- **False-positive risk:** tests skipped
- **Repos:** glossum, llm_bench, pyutilz, autopsia
- **Evidence:** glossum test_no_hardcoded_token_ceilings.py; reference_cold_catalogue_silently_caps_llm_output
- **Effort:** S
- **Priority:** P2

### NEW-17 (Med) -- gate `coverage_config_parity`

**Disposition:** OPEN

- **Bug class:** `fail_under` applied to narrow/nightly cov runs; numba-blind coverage
- **Method:** config parity: workflow steps rendering coverage on a subset must pass a derived threshold or own config
- **False-positive risk:** low
- **Repos:** pyutilz, mlframe
- **Evidence:** reference_coverage_fail_under_applies_to_narrow_ci_runs (two workflows sank 2026-09-20); reference_numba_coverage_blind
- **Effort:** S
- **Priority:** P2

### NEW-18 (Med) -- gate `pytest_addopts_path_runs`

**Disposition:** OPEN

- **Bug class:** hooks/CI steps naming test paths inherit `addopts -m` and run nothing
- **Method:** extend `marker_runner_coverage`: each path-naming pytest call must override `-m` or collect >0
- **False-positive risk:** low
- **Repos:** glossum, all
- **Evidence:** reference_pytest_addopts_applies_to_every_run
- **Effort:** S
- **Priority:** P2

### NEW-19 (Low) -- gate `rollback_then_continue`

**Disposition:** OPEN

- **Bug class:** rollback() in loop except then continue without resetting accumulators
- **Method:** AST, generalize glossum copy
- **False-positive risk:** low
- **Repos:** glossum, production_scrapers, upwork
- **Evidence:** glossum test_no_rollback_then_continue_in_loop.py
- **Effort:** S
- **Priority:** P3

### NEW-20 (Low) -- gate `reiterated_iterable_params`

**Disposition:** OPEN

- **Bug class:** `Iterable` param consumed twice
- **Method:** AST, generalize pyutilz copy
- **False-positive risk:** low
- **Repos:** all
- **Evidence:** pyutilz test_no_reiterated_iterable_params.py
- **Effort:** S
- **Priority:** P3

### NEW-21 (Low) -- gate `numba_seed_range`

**Disposition:** OPEN

- **Bug class:** seeds >= 2**63 fed to njit `np.random.seed`
- **Method:** AST: `getrandbits(64)`/`2**64` flowing to a seed call
- **False-positive risk:** low
- **Repos:** mlframe, pyutilz
- **Evidence:** reference_numba_seed_int64_overflow_silently_breaks_rng_restore
- **Effort:** S
- **Priority:** P3

### NEW-22 (Low) -- gate `stdlib_json_ban` (opt-in)

**Disposition:** OPEN

- **Bug class:** stdlib json on hot paths (user hard rule)
- **Method:** AST import ban, opt-in
- **False-positive risk:** would flag 21 py-ci-shared modules
- **Repos:** autopsia, mlframe
- **Evidence:** autopsia test_no_stdlib_json_import.py; feedback_orjson_compile_regex
- **Effort:** S
- **Priority:** P3

### NEW-23 (Low) -- gate `polars_null_equality`

**Disposition:** OPEN

- **Bug class:** `min()==max()` / `==` on nullable polars columns
- **Method:** AST heuristic, advisory only
- **False-positive risk:** medium
- **Repos:** mlframe
- **Evidence:** feedback_eq_missing_null_handling
- **Effort:** S
- **Priority:** P3

### NEW-24 (Low) -- gate `plotly_annotation_loop`

**Disposition:** OPEN

- **Bug class:** `fig.add_annotation` inside a loop (O(n^2))
- **Method:** AST call-in-loop
- **False-positive risk:** low
- **Repos:** mlframe, dash_app_core
- **Evidence:** reference_plotly_add_annotation_on2
- **Effort:** S
- **Priority:** P3

### INFRA-1 (High) -- pytest11 plugin py_ci_shared.pytest_plugin

**Disposition:** OPEN

- **Priority:** P0
- **Proposal:** No `[project.entry-points.pytest11]` exists today, so every

### INFRA-2 (High) -- Gate-teeth self-check

**Disposition:** OPEN

- **Priority:** P0
- **Proposal:** Every gate module ships `SEEDED_VIOLATIONS` (snippet + expected finding) and a clean

### INFRA-3 (High) -- Canary corpus

**Disposition:** OPEN

- **Priority:** P1
- **Proposal:** `tests/canary/` holds one mini-repo per bug class; CI runs every gate over it, so a

### INFRA-4 (High) -- Per-gate runtime budget

**Disposition:** OPEN

- **Priority:** P1
- **Proposal:** The plugin times each gate and fails past a `budget_s` from the registry, keeping

### INFRA-5 (Med) -- Registry + README parity

**Disposition:** OPEN

- **Priority:** P2
- **Proposal:** A single `GATES` registry (name, kind, since, budget, teeth fixture) wired to

### INFRA-6 (Med) -- Consumer adoption matrix

**Disposition:** OPEN

- **Priority:** P2
- **Proposal:** Extend `config_drift_check` to report which consumers run which gates and which
