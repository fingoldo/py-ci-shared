# 37 Gate defects found while wiring the gates into the consumers (step 8)

Round 2026-11-04, step 8 of PLAN.md. Each row is a false positive, a crash or a missing option in a py-ci-shared gate, found by a
consumer agent when it enabled the gate. Consumers work around each one locally (marker, exclusion) and the gate must be fixed here.
Every row is worked: RESOLVED with what was done, or WON'T FIX with the reason. Disposition on each row is OPEN until then.

| # | Gate | Defect | Seen in | Disposition |
|---|------|--------|---------|-------------|
| 37-F1 | skip_as_pass, comment_repo_paths | read tracked binary files (.png, .zip, .prof) as text and report them as unreadable; no way to exclude | polyvocab_app, glossum | RESOLVED f965a27 |
| 37-F2 | skip_as_pass | flags a warning `echo` that contains the word "skipped" (`tool/check-hooks-were-not-bypassed.sh:65`) | polyvocab_app | RESOLVED c8985f0 |
| 37-F3 | machine_specific_paths | takes the sed regex `s://.*::` for a URL (`tool/check-edge-function-auth.sh:47`) | polyvocab_app | RESOLVED 4782aa3 |
| 37-F4 | fail_open_handlers | `return exc.sqlstate` in an except is flagged as a silent default return although it returns the failure's own data | polyvocab_app | RESOLVED d9665da |
| 37-F5 | ci_pin_version_skew | `AttributeError: 'WindowsPath' object has no attribute 'rsplit'` on Windows through `run-all` | claude-usage-notifier, flutter_app_core, llm_bench, algopacksimple, dash_app_core | RESOLVED c0fdd0b: the config layer filled a parameter named `repo` with the repo root; renamed to `pin_repo` (CHANGELOG behaviour change). Test: `test_the_config_layer_kwargs_are_accepted_by_the_gate`. |
| 37-F6 | tracked_secret_shapes, sibling_floor_skew, import_side_effects | hard-fail or error on a repo with no applicable subject (no config table / no pyproject / no test_*.py); should SKIP | flutter repos | RESOLVED 5ebb446: the three gates skip with a reason when there is no subject (no shape table or pyproject, no test module or target); malformed config still fails. Regression tests fail on 5445c96. |
| 37-F7 | pickle_state_completeness | flags plain counters that are meant to be pickled (`SpeechAccounting.cached_chars`) | pyutilz | RESOLVED in 86c3ed7 |
| 37-F8 | env_flag_parsing | flags path, number, URL, secret-key and credential-presence reads, not only boolean flags | pyutilz, llm_bench, dash_app_core, algopacksimple | RESOLVED c0fdd0b: only boolean flags judged (path/number/URL/host/credential/secret names skipped; comparisons count only against on/off spellings). Test: `test_only_boolean_flags_are_judged`, `test_a_boolean_flag_compared_with_a_spelling_of_on_is_still_judged`. |
| 37-F9 | runtime_registry_mutation | flags an import-time registering decorator (`kernel_tuner`) | pyutilz | RESOLVED in 86c3ed7 |
| 37-F10 | unsafe_deserialization | flags `pd.read_pickle` on bytes that come from a sidecar-verified helper | pyutilz | RESOLVED in 86c3ed7 |
| 37-F11 | patch_target_read_at_call | misses late reads through `getattr(sys.modules.get(pkg), NAME)`, a `_pkg().NAME` helper and function-local `from X import Y` | pyutilz, autopsia | RESOLVED in 86c3ed7 |
| 37-F12 | identity_comparisons | flags `is` on StrEnum members (`node.kind is NodeKind.CAUSE`) | autopsia | RESOLVED in 86c3ed7 |
| 37-F13 | shared_mutable_placeholder | flags `threading.RLock()` | autopsia | RESOLVED in 86c3ed7 |
| 37-F14 | no_source_text_tests | flags prose containing the word "FROM" | autopsia | RESOLVED in 86c3ed7 |
| 37-F15 | verified_then_reopened | flags a size-checked truncate reopen; no suppression marker | autopsia, mlframe | RESOLVED 5ae727c |
| 37-F16 | hash_key_determinism | flags digests of exact wire bytes that key an archive | autopsia | RESOLVED 5ae727c |
| 37-F17 | check_then_act_outside_lock | blind to a call made under a guard | autopsia | RESOLVED 5ae727c |
| 37-F18 | lock_held_across_io | treats a cross-process file lock and a database advisory claim (`claim_lock`) as a thread lock | autopsia, glossum | RESOLVED 5ae727c |
| 37-F19 | git_child_env, wallclock_test_waits | look for a `tool/meta/baselines` directory the repo does not have | autopsia | WON'T FIX (2026-11-07, cannot be reproduced): on 5445c96 the default and configured baseline paths both pass on a repo without tool/meta/baselines (tests pin this), and no consumer repo or command that fails was supplied. |
| 37-F20 | validation_errors_echo_input | flags validators of the env-loaded Settings class, which is not a 422 request path | glossum | RESOLVED c0fdd0b: validators of a BaseSettings (or *Settings) class are not judged. Test: `test_a_validator_of_an_env_loaded_settings_class_is_not_a_request_path`. |
| 37-F21 | workflow_runner_labels | `--fix .` creates an untracked `.github/dependabot.yml` | glossum | RESOLVED 5ebb446: --fix never creates dependabot.yml; it only extends an existing one. Test fails on 5445c96. |
| 37-F22 | secret_assertion_operands | flags non-secret env names (OMP_NUM_THREADS, CUDA_PATH, PYTHONHASHSEED); needs a secret-name filter | mlframe | RESOLVED c0fdd0b: `os.environ.get` with a non-secret literal name is harmless, like the subscript and getenv forms; secret-named reads still tainted. Tests: `non_secret_get`, `non_secret_get_path`, `non_secret_hashseed` (SAFE table); `environ_get_of_secret_name` (OFFENDERS). |
| 37-F23 | checkout_resolution | fails in a worktree because the editable install points at the main checkout | mlframe | RESOLVED c0fdd0b: `assert_modules_resolve_to_checkout` puts the checkout's own package first on sys.path. Test: `test_a_worktree_resolves_its_own_package_when_another_checkout_is_first_on_the_path`. KNOWN TEST-TIMING GAP (not a defect of this row): `test_a_probe_that_fails_is_reported_with_its_output` and `test_a_second_call_with_the_same_workdir_takes_a_fresh_copy` hit a 120 s subprocess timeout when run together under load on the worktree machine; evidence: pass on master (scratch git archive, -n 0, 2 passed in 290.9 s), pass on full HEAD archive (2 passed in 122.2 s), pass on HEAD plus the five edited src files (2 passed in 135.1 s), probe_fails alone in the worktree (1 passed in 39.9 s), fresh_copy alone in the worktree (1 passed in 210.5 s). Timeouts left unchanged. |
| 37-F24 | module_reload_safety | TOML `allowed` entries arrive as lists and raise `unhashable type: 'list'`; test files are scanned as production code; `del sys.modules[k]` that restores a snapshot is flagged | algopacksimple (`test_bot_audit.py:127`, `tests/test_trading_strategy.py:82`) | RESOLVED 5ebb446: TOML allowlist entries normalised to tuples; test files excluded from the production scan; del sys.modules guarded by a snapshot counts as a restore. Tests fail on 5445c96. |
| 37-F25 | fail_open_handlers (admit_on_error) | a rate-limit counter `d[name] = count` inside an `except` in a loop is read as admitting the loop element | algopacksimple (`simplemethods/walkforward/runner.py:577`) | RESOLVED 5ebb446: d[name] = count (a counter, not the element) is not an admission. Tests fail on 5445c96. |
| 37-F26 | baseline_hygiene | cannot read a list-shaped baseline (the code-audit baseline), expects `{key: note}` | llm_bench | RESOLVED 5ebb446: list-shaped baselines of {key, note} records are read with their notes. Test fails on 5445c96. |
| 37-F27 | git_child_env | does not recognise the helper `_without_git_hook_env` that scrubs `GIT_*` | llm_bench | RESOLVED 5ebb446: helpers that scrub GIT_* by name or by the env they return are recognised. Test fails on 5445c96. |
| 37-F28 | gate config (`[tool.py_ci_shared]`) | a gate with a `[gates.X]` table must not also be in `enable`; the error says so but the generated examples do not | llm_bench, algopacksimple | RESOLVED 5ebb446: the enable/[gates.X] conflict error says to remove the name from enable; the dup_baseline_percent_key example no longer shows both. Tests fail on 5445c96. |
| 37-F29 | drifted_duplicate_functions | reads `@overload` stubs as copies of each other (five `build_*_prompt` stubs) | glossum | RESOLVED in 86c3ed7 |
| 37-F30 | import_cycles | does not honour the `# audit-exempt: import_cycle` marker that the same cycle carries in upwork_shared/_circuit.py:1 | social | RESOLVED de1aa90 |
| 37-F31 | sentinel_or_fallback | ignores `# falsy-ok:` on an `x if x else y` expression, although its docstring promises the marker (run_screening.py:231, 282) | social | RESOLVED 32755e5 |
| 37-F32 | env_flag_parsing | reports `PROXY_USER` (a credential string) as a boolean flag for `if not user` and `user == ""` (_observability_lookups.py:92) | social | RESOLVED c0fdd0b: same fix as 37-F8 (`PROXY_USER` is a credential name, so `if not user` and `user == ""` are not judged). |
| 37-F33 | ddl_lock_safety | reads SQL inside a `--` comment (RUNBOOK sql: a commented-out SET NOT NULL) | social | RESOLVED 5ae727c |
| 37-F34 | unresolved_module_attributes | resolves a bare `db` to dashboard/db.py when the code means the sibling project's db.py; not safe monorepo-wide per project | social | RESOLVED in 86c3ed7 |
| 37-F35 | fail_open_handlers, drifted_duplicate_literals (autopsia consumer) | 102 fail-open and 75 drifted-literal entries in autopsia are false positives recorded in audits/2026-11-05/gate_false_positives.md (file:line and reason each); the gates must stop flagging them, then autopsia's baselines go to zero | autopsia | PARTIALLY RESOLVED (2026-11-06, shapes 3 and 4 narrowed in `silent_default_return`, see CHANGELOG 'Gate false positives 2026-11-06'). Counts on autopsia `bc787483` (102 baseline keys, `find_fail_open_handlers` before and after): 52 were already no longer flagged before this change; this change clears 13 more (8 local-SQLite not-installed reads: `search_drug_vocab`, `display_ru`, `umls_preferred_curie`, `icpc2_codes_for`, `procedure_codes_for`, `rxcuis_for`, `umls_curie_for_rxcui`, `loaded_restricted_sources`; 5 value-parse fallbacks: `is_stale`, `_patient_age_years`, `_parse_hpoa_frequency`, `_float_env`, `_age_years`). 42 keys were still flagged before this change (the 37 recorded earlier was stale) and 39 are after the key-builder narrowing: 23 kept flagged on purpose, 8 need an owner decision and 8 need an autopsia edit; the per-key table below gives the decision and reason for each. The 75 drifted-literal entries are not touched here. |
| 37-F36 | fail_open_handlers | flags `admit_on_error` handlers that log at WARNING through `log_throttle` and store a constant under a loop key (`_dcd_pair_su_batch.py::pair_su_batch`, `_gpu_resident_operand_build.py::_upload_dtype_groups`, `_mrmr_fingerprints.py::_replay_fitted_state`, `polynom_pair_fe.py::_run_polynom_pair_f_same_pairs_old_per`) | mlframe | RESOLVED 60c6e50 (class A). Narrowed: a loud handler is exempt only when it stores a constant under the loop key; a loud handler that keeps the element is still flagged. Behaviour change in CHANGELOG |
| 37-F37 | drifted_duplicate_literals (literal-set) | flags per-kernel row-count sweep grids as drifted copies (`literal-set::(10000, 50000)`, `(20000, 100000)`, `(5000, 10000)`, `(8000, 32000)` in mlframe) | mlframe | RESOLVED 5db144a (class B): `# literal-set-ok: <reason>` on the site line or the line above, the existing `_core.line_has_marker` mechanism |
| 37-F38 | literal-set grids | (see 37-F37; the same class, split in mlframe baselines) | mlframe | RESOLVED 5db144a (class B, see 37-F37) |
| 37-F39 | xfail untracked-reason | does not follow skip markers built in a variable under a runtime condition (eight entries in mlframe `_xfail_baseline.json`) | mlframe | RESOLVED 2b9a19d (class C): a variable-bound mark call in a scope with an `if` on getoption/env/xdist/fast mode is not judged for untracked-reason |
| 37-F40 | fail_open_handlers | treats a handler whose only log is `log_throttle(...)` as silent (overlaps 37-F36) | mlframe | RESOLVED d7f1cf4: class E. A DEBUG `log_throttle` substitution is now flagged (canary failed on master). Earlier check: the report's log_throttle-only handler cases already pass on master (a logging-and-returning handler has two statements, so silent_default_return never fires); the admission side is covered by 60c6e50. A log_throttle DEBUG handler that substitutes a value is a missed case (false negative), not a false positive; not in scope here |

More rows are appended as the remaining consumer reports arrive (social, llm_bench group).

## 37-F35 per-key disposition (2026-11-06, recomputed)

Recomputed with the detector at 70f2152 over autopsia `bc787483` (`tests/test_meta/_fail_open_handlers_baseline.json`, 102 keys). 42 keys were still flagged before this change, not the 37 the row above recorded (70f2152 re-flagged the value-parse fallbacks). After the key-builder narrowing: 39 still flagged.

| Key (autopsia path::function) | Shape | Decision | Reason |
|---|---|---|---|
| api_rate_limit.py::max_concurrent_heavy | 1 env | kept flagged | bad env value keeps default, no log |
| api_rate_limit.py::heavy_queue_wait_seconds | 1 env | kept flagged | bad env value keeps default, no log |
| reason/request_budget.py::_float_env | 1 env | kept flagged | bad env value keeps default, no log |
| api_rate_limit.py::_is_trusted | 2 fail-closed | kept flagged: fail-closed | unparseable host is not trusted |
| pickle_cache_integrity.py::_secret | 2 | kept flagged: fail-closed | MAC key read |
| pickle_cache_integrity.py::load_verified | 2 | kept flagged: fail-closed | MAC verification |
| pickle_cache_integrity.py::trailer_mac | 2 | kept flagged: fail-closed | MAC trailer |
| pickle_cache_integrity.py::load_verified_bytes | 2 | kept flagged: fail-closed | MAC verification |
| pickle_cache_integrity.py::signed_pickle | 2 | kept flagged: fail-closed | signing |
| input_keyed_cache.py::load_record (x2) | 2 | kept flagged: fail-closed | digest verification of recorded inputs |
| kb/overlay_freshness_verdict.py::load_valid | 2 | kept flagged: fail-closed | verification of recorded verdict |
| reason/consilium_store.py::is_minted_case_id | 2 | kept flagged: fail-closed | HMAC tag check |
| reason/cupy_probe.py::cupy_device_usable | 2 | kept flagged: fail-closed | device probe |
| differential_cache.py::_encode | 5 builder | narrowed | json.dumps TypeError to deepcopy, no I/O error swallowed |
| differential_cache.py::_freeze_object | 5 builder | narrowed | pickle failure to deepcopy, no I/O error swallowed |
| kb/overlay_input_stamp.py::_key | 5 builder | narrowed | relative_to ValueError, no I/O error swallowed |
| input_keyed_cache.py::_git | 5 | kept flagged | catches OSError and SubprocessError: swallowed I/O |
| kb/cochrane/full_text.py::_doi_version | 5 | needs owner decision | name is not key/encode/freeze; a version comparison, and a version-named rule would also widen to value parsers |
| graph_side_tables.py::_kept_in_table_order | 5 | needs owner decision | KeyError fallback is result-identical, but the name is not a key builder |
| vocab/structure.py::_read (x2) | 3 | needs autopsia edit | catches sqlite3.Error; rule accepts only OperationalError. Changing the except to sqlite3.OperationalError clears it |
| vocab/local_install.py::_read | 3 | needs autopsia edit | catches sqlite3.Error (same) |
| vocab/ccsr_icd10cm.py::categories_for | 3 | needs autopsia edit | catches sqlite3.Error (same) |
| vocab/ccsr_icd10cm.py::causes_for_category | 3 | needs autopsia edit | catches sqlite3.Error (same) |
| api_noema_reference.py::_base_rate_priors | 3 absent file | needs autopsia edit | catches OSError; rule accepts FileNotFoundError |
| graph_loading.py::load_seed | 3 absent file | needs autopsia edit | catches OSError; rule accepts FileNotFoundError |
| reason/clinical_overlays.py::_load_with_overlays | 3 absent file | needs autopsia edit | catches OSError; rule accepts FileNotFoundError |
| ingest/fhir_import.py::_age_years | value parse | kept flagged | unparseable value to default (70f2152 rule) |
| ingest/integrity.py::is_stale | value parse | kept flagged | unparseable value to default (70f2152 rule) |
| reason/clinical_support_severity.py::_patient_age_years | value parse | kept flagged | unparseable age becomes no age (70f2152 rule) |
| vocab/orphanet.py::_parse_hpoa_frequency | value parse | kept flagged | unparseable value to None (70f2152 rule) |
| ingest/mortality_codes.py::_xref_patterns_checked | unclassified | kept flagged | catches Exception on import; autopsia could narrow to ImportError |
| ingest/sources.py::fetch_epmc_fulltext | unclassified | kept flagged | catches Exception, no log; network fallback |
| ingest/to_graph_edges.py::modifier_to_moderator | unclassified | needs owner decision | returns a reason code; _carries_reason accepts only text with a space or colon |
| ingest/attempt_quarantine.py::split_archive | unclassified | needs owner decision | unreadable line counted and kept in real output, not logged |
| kb/cochrane/discovery.py::discover | unclassified | needs owner decision | skipped record logged at INFO is flagged as substitution |
| reason/eliminate_masses_wide.py::wide_masses | unclassified | needs owner decision | designed EliminationTooWideError with exact fallback |
| reason/byte_budget_cache.py::array_nbytes | unclassified | needs owner decision | 0-byte fallback under-counts a budget |
| reason/clinical_history.py::_family_history_var | unclassified | needs owner decision | resolver ValueError or KeyError silently becomes None |
| vocab/expand.py::analytes | unclassified | kept flagged | silent empty dict on bad JSON |
| reason/renal_clearance.py::load_drug_renal_clearance | unclassified | kept flagged | documented, not logged; WARNING log would clear it |

Baseline refresh needed in autopsia: 63 keys go stale (60 already unflagged at 70f2152 plus the 3 narrowed here); 39 remain.
