# 41 gate false positives: round 2026-11-16 (T)

Commit audited: 48e49b1 (py_ci_shared), Python 3.14.3. Read-only. Scratch: scratchpad\audit_T\41\ (mkcorp.py, run_one.py, driver.py, hostile_one.py, ca_run.py).

#### Method

- Corpora (snapshots copied to scratch, .py only): pyutilz src+tests (626 files), py-ci-shared src+tests minus canaries (941), mlframe/src (3388, working tree moving, not a commit).
- Corpus gates: each `tests/_gate_teeth_canaries*.py` CANARIES[gate].run(dir) pointed at a corpus copy (the repo's own entry per gate, CUDA/GPU excluded; 167 gates). Many canary lambdas carry canary-specific args (prefixes, package names), and gates that need pyproject/workflows/baselines fail or raise CorpusError on a bare source copy: those rows are NOT false-positive evidence and are ignored below.
- code_audit: every scanner of `get_scanners()` through `run_all_checked(root, checks=[name])` per corpus: no scanner reported a failure on any real corpus.
- Crash hunt: for each of 167 gates, the clean canary plus one hostile `hostile_x.py` (also copied as `test_hostile_x.py` into every directory for the 37 gates that passed): syntax error, NUL byte, 5 MB single line, 5000 nested parentheses, 30000-operand `1+1+...`, 150 nested lists, empty file, a directory named hostile_x.py, latin-1 and invalid UTF-8 bytes.
- Pollution caveat: the committed_line_endings and tracked_secret_shapes canaries rewrite files in the directory they get (`{CR}`, `{TOKEN}`) and `git init` it, which corrupted tests/_gate_teeth_canaries.py in my first py-ci-shared copy; rerun as corpus `pcs2` without that file.
- Another caveat: `dead_public_callable` reported 1844 / 4058 findings on a mixed src+tests root, but on `src/` alone it reports 0 for pyutilz and mlframe (it is opt-in and exempts tests by design). Not a finding.
- STATUS: all runs finished (167 gates x 3 corpora, 105 code_audit scanners x 3 corpora, 1,837 hostile runs). pcs2 outcome: 88 pass, 60 fail, 18 raise (CorpusError / SourceReadError for missing config files, not defects), 1 skip.
- mlframe/src (3388 files, snapshot of a moving tree, no commit pinned) top counts: drifted_duplicate_functions 752, drifted_duplicate_literals 177, fail_open_handlers 178, import_time_global_side_effects 152, machine_specific_paths 65, caller_frame_mutation 45, env_write_restore 44. Judged by sample: machine_specific_paths (`D:/Temp` in `_benchmarks/` scripts) true by the rule; import_time_global_side_effects (`warnings.filterwarnings('ignore')` in bench_* scripts) true by the rule; env_write_restore on `src/mlframe/__init__.py:70-163` is deliberate import-time autoconfiguration of CUDA_HOME / OMP threads in library code (the message speaks of tests: scope issue, a consumer must exclude the file); fail_open_handlers prints "in gate code" for 178 sites in ordinary statistics code (e.g. evaluation/_bootstrap_clusters.py:101, a per-metric isolation already justified by a `noqa` comment), so pointing it at a whole package rather than gate code is noise by construction. No scanner or gate crashed on mlframe/src.

#### Crash results (hostile inputs)

| input | gates reporting a finding naming the file | silent pass | traceback |
|---|---|---|---|
| syntax error / NUL / bad UTF-8 / latin-1 | 109-113 | 52-56 (non-Python gates, or name/prefilter scoped) | llm_output_caps, timezone_honest, git_child_env |
| 5 MB line, empty, 150 nested lists | 0 | 167 | none |
| 5000 parentheses | 109 (SyntaxError reported) | 57 | llm_output_caps |
| 30000-term `1+1+...` | 0 | 143 | 23 RecursionError + 1 timeout |
| directory named x.py | 5 | 161 | doc_identifier_parity |

Silent passes judged by reading: the 37 Python-reading ones were rerun with the file in every directory; the remaining passes are YAML/TOML/config gates, or gates that prefilter by text token (source_text_claims, spec_bound_doubles, pytest_markers, git_child_env on NUL): a file with no trigger token cannot hold a finding (see last section).

## 41-T1 (P2) Twenty-three gates die with RecursionError on one long operator chain, instead of reporting a finding
- **Evidence:** a file `x = 1+1+...+1` (30000 operands) makes these raise `RecursionError: maximum recursion depth exceeded` (gate run through its canary entry): alembic_concurrently, connection_liveness_kwargs, connect_error_echo, constant_fallback_cache_key, env_write_restore, hardcoded_seed_in_library, hardcoded_token_ceilings, identity_comparisons, lf_file_writes, machine_specific_paths, numba_seed_range, order_losing_filters, printed_advice, psycopg2_param_arity, reiterated_iterable_params, rollback_then_continue, runtime_registry_mutation, secret_assertion_operands, sql_verifier_coverage, statement_columns_exist_in_ddl, statement_real_engine_coverage, unread_function_params; predictable_tmp_path hangs past 300 s (timeout); timezone_honest dies with a ruff stack overflow (exit 3221225725). Threshold probe (thr.py, identity_comparisons, `"a" + "a" + ...`): 800 operands pass, 1200 raise RecursionError (limit 1000).
- **Impact:** generated or data-like modules (long concatenations, big `or`/`+` chains) turn the gate into a traceback; the consumer can only exclude the file. Fail-closed, but not actionable.
- **Proposed fix:** make the recursive walkers iterative (`_core.node_index.walk` already is) or catch `RecursionError` in the `_core` scan helpers and report `path: too deeply nested to analyse` as an unparsed-file finding. ADDITIVE (red stays red, message improves).
- **Disposition:** RESOLVED. The recursive walkers and expression folds of the 23 gates are iterative (helpers `binop_operands` and `postorder_fold` in `_core/node_index.py`; shared `enclosing_functions` and `_string_value` cover several gates). predictable_tmp_path's quadratic subtree scan is now one pass, and machine_specific_paths computes literal texts in one pass. Test: `tests/test_deep_operator_chain_41_t1.py`, one case per gate, each a pass on a 30000-operand chain (red on the base commit: 22 RecursionError, 1 hang). Commits: 2d62079, 5dc5e2b, 63fb3bb, 08659b5, b4317ab, 136f229, f07fab7, 8d7a995, 6219e9e, 04556d4, 26f4528, d3d679d, 1391bdb, 6d6986f, eb7daf0, 32a3dee, 57f2404, e4ae6e3, 34c8cbd, c96943a, d6d8398 (the gate commits are one per gate or shared-helper group). Not covered: timezone_honest (its failure comes from ruff's own process, see 41-T2). Pre-existing failures of 11 baseline tests exist on the base commit as well and are unchanged.

## 41-T2 (P3) Three gates raise a raw exception on an unreadable or unparsable file with no allow_unparsed escape
- **Evidence:** git_child_env (`_python_spawns` calls `parse_source`, git_child_env.py:114) raises `SourceParseError` on a syntax-error test file; llm_output_caps raises `SourceParseError` / `SourceReadError` (syntax, 5000 parens, latin-1, bad UTF-8); timezone_honest raises `RuntimeError: ruff exited 1 without reporting a single finding` (syntax, NUL) and `ruff skipped input it was given` (latin-1, UTF-8). Every other scanning gate reports "N file(s) could not be parsed" and honours allow_unparsed. assert_no_new_unscrubbed_git_spawns has neither `allow_unparsed` nor a floor kwarg.
- **Impact:** fail-closed (good), but the failure is a traceback class the runner reports as ERROR and one broken fixture file blocks the gate with no switch (CLAUDE.md contract: unparsed list plus allow_unparsed).
- **Proposed fix:** route all three through `scan_python`/`check_scan` (UnparsedFilesError). ADDITIVE.
- **Disposition:** FIXED for `git_child_env` and `llm_output_caps` (tests `test_a_test_file_that_does_not_parse_is_a_finding_not_a_traceback`, `test_an_unparsable_file_is_unparsed_not_a_traceback`; the red run was not recorded for these two, only the T3/T4 reds were). DEFERRED for `timezone_honest`: its ruff exit-1 and stack-overflow paths come from ruff's own process, so the fix needs a wrapper around the ruff invocation and a follow-up.

## 41-T3 (P3) doc_identifier_parity crashes with PermissionError on a directory named like a source file
- **Evidence:** corpus containing a directory `hostile_x.py`: `PermissionError: [Errno 13] Permission denied` out of `Path.open` (hout/doc_identifier_parity__dirname.json). The other 161 gates ignore it or report it (loc_budget, optional_truthiness, phantom_code_references, source_text_claims, spec_bound_doubles report it).
- **Impact:** fail-closed crash on a legal layout (vendored trees can hold `*.py` directories).
- **Proposed fix:** skip non-files in the enumerator this gate uses (`iter_files` already does). ADDITIVE.
- **Disposition:** FIXED (test `tests/test_doc_identifier_parity.py::test_a_directory_named_like_a_source_file_is_skipped_not_a_crash`, red before the fix). Corpus reads now skip non-files.

## 41-T4 (P2) lock_held_across_io treats an asyncio semaphore under async with as a thread lock
- **Evidence:** pyutilz tests/llm/test_llm_core_audit_20260926_base.py:31-33: `async with self.semaphore: ... await asyncio.sleep(0)` is reported "asyncio.sleep runs while lock self.semaphore is held: every thread waiting for the lock waits for this I/O". 6 findings in pyutilz, all this shape (test_llm_core_audit_20260926_{base,factory,openrouter_provider,progress,meta}.py, test_domain_db_web_cloud_llm_text_audit_20260903_llm.py:231). An `async with` on an asyncio primitive yields to the loop; no thread waits. 6 of 6 false.
- **Impact:** consumer must mark or baseline; the advice (move the await out) is wrong for asyncio.
- **Proposed fix:** do not flag an `await` under `async with` of a non-threading lock; keep sync `with` plus blocking call. BEHAVIOUR CHANGE (only removes findings; Nothing to do).
- **Disposition:** FIXED (test `tests/test_lock_held_across_io.py::test_async_with_on_an_asyncio_semaphore_is_not_a_thread_lock`, red before the fix). Only `async with` on a lock bound to an `asyncio.*` constructor is exempt; a sync `with` on a threading lock is still reported. Behaviour change, changelog entry.

## 41-T5 (P2) default_via_or flags the `side_effect(...) or True` test-double idiom
- **Evidence:** pyutilz: 20 of 47 findings (e.g. tests/core/pythonlib/test_packages.py:80 `lambda name: seen.append(name) or True` reported as "caller passing the legitimate sentinel 0 is silently rewritten to True"). `list.append` returns None; `or True` is the standard record-and-return lambda; no sentinel exists. py-ci-shared: 6 of 151; also `spec.get("rev") or spec.get("tag") or spec.get("branch")` (_adoption_pins.py:166, sibling_floor_skew.py:183: string values) and `kwargs.get("timeout") or 120` (a true positive for timeout=0). 78 of 151 py-ci-shared findings are `or <call>(...)` defaults.
- **Impact:** about 40 percent false in pyutilz tests; default-on scanner.
- **Proposed fix:** ignore an `or` whose left operand is a call to a None-returning method (append/add/extend/update/write/info/debug) and `.get()` chains over string values. BEHAVIOUR CHANGE (only fewer findings).
- **Disposition:** FIXED for the `append(...) or True` idiom (`tests/code_audit/test_default_via_or_audit_41.py`). Behaviour change, changelog entry. DEFERRED for the `.get()` string-chain shape: `spec.get("rev") or spec.get("tag")` is syntactically identical to the real numeric bug pinned by `test_default_via_or_non_alias_get_chain_still_flagged`, so separating them needs type information or an owner-approved marker convention.

## 41-T6 (P3) redundant_test_fit_call flags calls that build fresh per-test objects or take fixture arguments
- **Evidence:** pyutilz 423 findings, py-ci-shared 670 (default-on). Top callee names: `_client` 28, `_provider` 13, `_run` 11, `_req` 5. tests/llm/test_generation_metadata.py:147 `_json_client(EXAMPLE_BODY)` (new stub client per test), tests/code_audit/test_registry_and_cli.py:155 `_write(tmp_path, "ok.py", ...)` (writes into a per-test tmp_path), tests/core/pythonlib/test_audit_20261015_silent_hardware.py:33 `_warnings(caplog)` (reads a per-test fixture). Message claims "deterministic, so every call after the first recomputes the same result". 5 of 5 sampled false.
- **Impact:** hundreds of reports to suppress in one consumer.
- **Proposed fix:** skip calls whose arguments include a pytest fixture name (tmp_path, caplog, monkeypatch, capsys, request) or whose callee name matches client/provider/stub/fake/write/make. BEHAVIOUR CHANGE (only fewer findings).
- **Disposition:** DEFERRED. a rule for which callee names and fixture arguments make a call a per-test builder is a heuristic with false-negative risk, sampled on only 5 findings; needs an owner decision on the rule.

## 41-T7 (P3) conceded_defect_pins and comment_cites_absolute_line misread innocent text
- **Evidence:** conceded_defect_pins (pyutilz, 13): tests/llm/test_deepseek_provider.py:157 `test_handle_special_status_200_noop` (docstring: "The no-op half must be distinguishable from the 402 half: silence, not a warning": the no-op is the correct behaviour), test_openai_compat.py:388 and test_llm_openrouter_part2.py:314 likewise; 'degenerate' in tests/stats/test_normality.py:122 (`..._is_degenerate_not_normal`) is a specified result. About 5 of 6 sampled false. comment_cites_absolute_line (opt-in; 231 pyutilz, 60 mlframe): tests/test_dev_freevar_analysis.py:39 `# assigned on line 2, only READ on line 3` describes a snippet inside the test, not a repository line; 3 of 3 sampled false.
- **Impact:** noise; the first reads as accusing correct tests.
- **Proposed fix:** conceded_defect_pins: skip 'no-op' and 'degenerate' when the test name or docstring says the case is expected, or drop the two words from its default vocabulary. comment_cites_absolute_line: skip test-file comments that name no file. BEHAVIOUR CHANGE (fewer findings).
- **Disposition:** DEFERRED. Dropping `no-op`/`degenerate` from the vocabulary or skipping by docstring each lose true positives in the same scanner; the choice needs an owner decision. The `comment_cites_absolute_line` skip rule is a small follow-up.

## 41-T8 (P3) unread_function_params and predictable_tmp_path flag test doubles and string literals
- **Evidence:** unread_function_params (pyutilz, 12): every sampled one is a fake accepting `timeout` to match the real signature (tests/openai_compat_support.py:200, :255, :315; test_openrouter_health_survivors_more.py:98, 108, 117); the caller passes it, dropping it breaks the call. 9 of 9 false. predictable_tmp_path (pyutilz, 10): tests/test_cloud.py:159-180 flags `"/tmp/src.bin"` passed to a mocked uploader and in `assert_called_once_with`: nothing is created there; test_logginglib_survivors.py:193 `filename = "/tmp/ipython-input-42.py"` is a fake traceback filename. Only tests/llm/test_llm_core_audit_20260926_messages.py:16 (a real `gettempdir()/name` build) is true: 7 of 8 false.
- **Impact:** a consumer scanning tests has to mark or baseline each.
- **Proposed fix:** unread_function_params: skip a def in a test module whose class/def name starts with fake/stub/_Fake or that is a nested `fake`/`boom`; predictable_tmp_path: flag a literal /tmp path only when it reaches open/Path/os calls, not a mock call argument or comparison. BEHAVIOUR CHANGE (fewer findings).
- **Disposition:** DEFERRED. The fake-name skip for `unread_function_params` and the mock-argument rule for `predictable_tmp_path` each need an AST-context rule that is not designed yet; a follow-up with canaries.

## 41-T9 (P3) possibly_unbound_local cannot correlate two branches on the same condition
- **Evidence:** pyutilz src/pyutilz/database/db/execution.py:433-456: `if mode in ["select","update"]: where_values, sql_where_templates = ...` then `if mode == "select": ... sql_where_templates`: bound on every path that reads it, yet 22 findings in pyutilz, the first 6 at execution.py:449-456 are this shape. Not all false: src/pyutilz/cloud/cloud.py:124 (`from botocore.exceptions import ClientError` inside the `try`, `except ClientError` after it) is a true positive (a missing botocore turns ImportError into UnboundLocalError). 6 of 7 sampled false.
- **Impact:** bounded by the existing `# unbound-ok:` marker.
- **Proposed fix:** correlation is undecidable in general; keep the gate; add to the message that a mode/flag-correlated branch takes the marker. ADDITIVE.
- **Disposition:** DEFERRED. Correlating two branches on one condition is undecidable in general; the existing `# unbound-ok:` marker stays the contract. The clarifying message text is an additive follow-up.

## 41-T10 (P2) phantom_code_references reports every backticked external, stdlib or prose name as a phantom
- **Evidence:** `assert_no_phantom_code_references(files, root, python_declarations(files))` over the clean py-ci-shared copy: 506 findings; pyutilz: 175. A random sample of 9 per corpus (seed 1) was external or non-symbol text in every case: `AsyncMock()` (unittest.mock), `sleep()`, `QuantileDMatrix` (xgboost), `LIKE` (SQL keyword), `to_plotly_json()`, `get_params()` (sklearn), `ForwardRef` (typing), `is_finite()` (polars), `information_schema.columns.data_type`, `EXCLUDED.col`, `display_ru()`; plus private names of other modules (`_logger`, `readonly_to_numpy_mutation._own_nodes`) that exist outside the declared set. Only `tests/test_meta/test_uncached_constant_cost_probe_warn.py:19` (names a test file that does not exist) looked like a real hit. Message: "`X` names nothing declared in this repo". The only escape is `extra_known` (hand-fed) or a baseline of hundreds of rows.
- **Impact:** adopting the gate with `declared = python_declarations(files)` (the documented usage) yields a baseline of the size of the repo's docstring vocabulary; about 100 percent of the sampled rows are false.
- **Proposed fix:** default `extra_known` to builtins, `sys.stdlib_module_names` members and their public attributes, SQL keywords, and any name imported (or attribute of an imported name) in the same file; only flag tokens that look like repo-local (same top-level package prefix, or a name close by edit distance to a declared one). BEHAVIOUR CHANGE (fewer findings; Nothing to do).
- **Disposition:** DEFERRED. Fixing it needs a declared-vocabulary source (stdlib names, SQL keywords, names imported per file) and a repo-local prefix rule, which changes the gate's default and the documented baseline usage; needs an owner decision on the default first.

#### Gates measured (first findings judged, pyutilz src+tests snapshot)

| gate | findings pyutilz / pcs / mlframe | judged | verdict |
|---|---|---|---|
| lock_held_across_io | 6 / 1 / 0 | 6 | 6 false (T4) |
| unread_function_params | 12 / 1 / n.r. | 9 | 9 false (T8) |
| predictable_tmp_path | 10 / 1 / n.r. | 8 | 7 false (T8) |
| conceded_defect_pins | 13 / 1 / 0 | 6 | 5 false (T7) |
| redundant_test_fit_call | 423 / 670 / 0 | 5 | 5 false (T6) |
| default_via_or | 47 / 151 / 3 | 10 | 4 of first 4 false, 20 of 47 idiom (T5) |
| possibly_unbound_local | 22 / 1 / n.r. | 7 | 1 true, 6 false (T9) |
| env_write_restore | 4 / 1 / 44 | 4 | all in src (hardware.py:47 sets CUDA_HOME on purpose): scope by config, not a gate defect |
| comment_cites_absolute_line | 231 / 17 / 60 | 3 | 3 false (T7) |
| uncached_constant_cost_probe | 56 / 32 / 123 | 4 | 4 true, advisory |
| vacuous_empty_pattern_match | 1 / 30 / 40 | 1 | true (openrouter_speech.py:429) |
| assert_in_loop_first_failure_only | 25 / 12 / 0 | 4 | 4 true by rule, style level |

(pcs column of the corpus gates is from the polluted first copy and shows 1 where a corrupted file masked the gate; the clean pcs2 run replaces it. n.r. = not reached at checkpoint.)

## Considered and not raised

- dead_public_callable 1844 / 4058 findings: artifact of mixing src and tests under one root; src-only gives 0 (pyutilz, mlframe).
- source_text_claims, spec_bound_doubles, pytest_markers, git_child_env (NUL case) pass a syntax-error or NUL file holding none of their trigger tokens: textual prefilter, a file without the token cannot contain a claim; spec_bound_doubles deliberately keeps unreadable files that mention an entry point (spec_bound_doubles.py:218).
- ci_*, coverage_config_parity, entry_points_resolvable, gate_integrity, git_dependency_pins, nginx_location_headers, sibling_floor_skew, test_partition_reachability, uv_lock_parity, tool_config_env_vars_set_in_every_tool_job, external_fact_tables ignore a hostile .py: they read yaml/toml/other files.
- 5 MB single line, empty file, 150 nested lists: all 167 gates pass with no traceback.
- Corpus-gate "fail" rows with EmptyScanError, CorpusError, SourceReadError on pyproject.toml/seed.yml (api_floor, ci_workflow_gate, consumer_import_census ...) on a bare source copy: the gate correctly refuses a corpus without its config; not a defect.
- tracked_secret_shapes PermissionError on my corpus: caused by the canary's own `git init` inside the scratch copy.

| id | sev | title |
|---|---|---|
| 41-T1 | P2 | 23 gates RecursionError on one long operator chain |
| 41-T2 | P3 | three gates raise raw exceptions, no allow_unparsed |
| 41-T3 | P3 | doc_identifier_parity PermissionError on directory named x.py |
| 41-T4 | P2 | lock_held_across_io flags asyncio semaphore |
| 41-T5 | P2 | default_via_or flags append() or True idiom |
| 41-T6 | P3 | redundant_test_fit_call flags fixture-fed builders |
| 41-T7 | P3 | conceded_defect_pins and comment_cites_absolute_line misreads |
| 41-T8 | P3 | unread_function_params and predictable_tmp_path in test doubles |
| 41-T9 | P3 | possibly_unbound_local branch correlation |
| 41-T10 | P2 | phantom_code_references flags external/stdlib backticked names (506 pcs, 175 pyutilz) |

Ranking by false-positive rate times suppression cost (from the samples above): phantom_code_references (about 100 percent, hundreds of rows), unread_function_params in tests (100 percent), lock_held_across_io (100 percent, 6 rows), redundant_test_fit_call (100 percent of 5 sampled, 423 rows), predictable_tmp_path in tests (88 percent), conceded_defect_pins (83 percent), possibly_unbound_local (86 percent, marker exists), default_via_or (about 40 percent). Gates with no false positive found in the sample: uncached_constant_cost_probe, vacuous_empty_pattern_match, assert_in_loop_first_failure_only, drifted_duplicate_functions/literals (rule-true, volume is a baseline matter).
