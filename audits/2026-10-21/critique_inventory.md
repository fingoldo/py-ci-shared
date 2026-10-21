# Critique of plan section 3: duplicates by behaviour

Read-only. Method: pyutilz detector scan functions and module docstrings (pyutilz `dev/code_audit/*.py`), py-ci-shared gate docstrings and `find_*` functions. Where pyutilz modules name a `Shared-gate counterpart`, that self-declaration was checked against the text. Not every module was read in full; the verdicts below rest on docstrings and the scan function's stated rule. Line numbers are `scan_*`/`find_*` definitions (pyutilz) or the `find_*` definition / module docstring line 1 (py-ci-shared).

Verdicts: SAME = same flagged shape; OVERLAPPING = shared cases and differing cases (stated); UNRELATED = name or self-declaration matches but the rules do not.

## Pairs

| pyutilz (file:line) | py-ci-shared (file:line) | Verdict | Differs in |
|---|---|---|---|
| `code_audit/import_cycles.py:179` | `py_ci_shared/import_cycles.py:329` | SAME (core) | Shared adds package-facade cycles and order-dependent from-imports. Both ignore function-body and TYPE_CHECKING imports. |
| `code_audit/source_text_assertions.py:207` | `py_ci_shared/source_text_claims.py:564` | SAME (core) | Shared is the wider AST detector; pyutilz's own docstring says shared reports every site it does. |
| `code_audit/reexport_patch_target.py:129` | `py_ci_shared/patch_target_read_at_call.py:272` | SAME | Same defect (patch on a re-exporting facade). pyutilz has a deliberate twin, see next row. |
| `code_audit/patch_target_is_a_reexport.py:176` | `py_ci_shared/patch_target_read_at_call.py:272` | OVERLAPPING | Twin of the row above; the two pyutilz modules differ only in false-positive suppression. `run_all()` runs only this one. |
| `code_audit/vacuous_loop_assertion.py:169` | `py_ci_shared/vacuous_loop_assertions.py:317` | OVERLAPPING | Both flag assert-only loops that may run zero times. They disagree on what counts as a floor (pyutilz docstring says so). Plan said "likely same": wrong. |
| `code_audit/stale_source_citations.py:125` | `py_ci_shared/stale_source_citations.py:281` | OVERLAPPING | pyutilz: cited file gone, or line past end, in .py/.sql/.md. Shared: self-citations, past-end lines, cited symbols; .py plus optional extra globs; file-gone left to pyutilz. |
| `code_audit/bare_except.py:35` | `py_ci_shared/swallowed_exceptions.py:262` | OVERLAPPING | pyutilz: bare/BaseException clause, all files including tests. Shared: silent body on broad or I/O catch, production only. |
| `code_audit/broad_except.py:212` | `py_ci_shared/swallowed_exceptions.py:262` | OVERLAPPING | pyutilz: broad handler with no warning log or raise, any body that returns or passes, all files. Shared: body is only pass/continue/constant, production only; DEBUG/INFO counts only with an opt-in flag. |
| `code_audit/silent_escalation.py:148` | `py_ci_shared/swallowed_exceptions.py:262` | OVERLAPPING | Log-only handler (pyutilz) vs silent handler (shared, with opt-in quiet-log mode). |
| `code_audit/non_neutral_except_fallback.py:254` | `py_ci_shared/fail_open_handlers.py:288` | OVERLAPPING | pyutilz: handler substitutes a non-neutral literal without a log above debug, any code. Shared: fail-open handlers in gate code only. |
| `code_audit/default_via_or.py:672` | `py_ci_shared/sentinel_or_fallback.py:65` | OVERLAPPING | pyutilz: general `x = arg or NONTRIVIAL` heuristic. Shared: `or` only on a declared list of settings (seed, timeout, max_tokens). Shared is a subset in scope. |
| `code_audit/getattr_literal_on_known_dataclass.py:165` | `py_ci_shared/config_getattr_default_parity.py:136` | OVERLAPPING | pyutilz: getattr literal naming a field the known dataclass lacks. Shared: getattr literal disagreeing with the declared default, plus names no dataclass declares. |
| `code_audit/sentinel_cached_as_answer.py:133` | `py_ci_shared/sentinel_or_fallback.py:65` | UNRELATED | pyutilz: failure sentinel written into a cache from an except handler. Not an `or` fallback. Plan listed this as overlapping. |
| `code_audit/sentinel_guard_mismatch.py:165` | `py_ci_shared/sentinel_or_fallback.py:65` | UNRELATED | pyutilz: sentinel passes an `is None` guard. Not an `or` fallback. |
| `code_audit/additive_epsilon_denominator.py:67` | `py_ci_shared/epsilon_padded_denominators.py:150` | OVERLAPPING | pyutilz: any additive pad on a division. Shared: only power denominators (`d**k + eps`). Pyutilz is the wider rule. |
| `code_audit/comment_names_missing_symbol.py:142` | `py_ci_shared/phantom_code_references.py:645` | OVERLAPPING | pyutilz: backticked `name()` private calls in comments, plus absolute-line citations. Shared: every backticked token and test file, plus absolute-line citations (`find_stale_absolute_line_citations`). Shared is the superset. |
| `code_audit/duplicate_function_body.py:76` | `py_ci_shared/drifted_duplicate_functions.py:132` | OVERLAPPING | pyutilz: identical bodies under any name. Shared: same name and signature, drifted apart. Different pairs; no overlap in flagged cases beyond the same helper copied twice. |
| `code_audit/near_duplicate_function_body.py:343` | `py_ci_shared/drifted_duplicate_functions.py:132` | OVERLAPPING | pyutilz: nearly identical bodies across names (difflib ratio). Shared: nearly identical bodies with the same name. |
| `code_audit/unpicklable_resource_state.py:117` | `py_ci_shared/pickle_state_completeness.py:255` | OVERLAPPING | pyutilz: a lock, thread, process or file handle assigned in `__init__` with no `__getstate__`. Shared: a live resource assigned outside `__init__`, and a copy-form `__getstate__` that forgets a cache. |
| `code_audit/nondiscriminating_test.py:148` | `py_ci_shared/nondiscriminating_shapes.py:311` | OVERLAPPING | Same concept, shape lists "barely overlap" (pyutilz docstring). Both run. |
| `code_audit/machine_dependent_test.py:136` (wall clock) | `py_ci_shared/single_shot_timing_assertion.py:451` | OVERLAPPING | pyutilz: any assert on an elapsed clock difference. Shared: ratio or ceiling from one sample per side. |
| `code_audit/machine_dependent_test.py:186` (sleep then assert) | `py_ci_shared/wallclock_test_waits.py:67` | OVERLAPPING | pyutilz: sleep of at least a threshold, plus an assert, in the test body. Shared: any `time.sleep` line in a test (no assert needed). |
| `code_audit/machine_dependent_test.py:136` | `py_ci_shared/clock_day_boundary.py:166` | UNRELATED | pyutilz docstring: disjoint shapes. |
| `code_audit/hardcoded_test_path.py:18` | `py_ci_shared/machine_specific_paths.py:160` | OVERLAPPING | pyutilz: drive letter or POSIX home path in a test file. Shared: same path shapes across all files, plus credentialed DB URLs. |
| `code_audit/source_text_assertions.py:207` (SQL side) | `py_ci_shared/no_source_text_tests.py:71` | OVERLAPPING | pyutilz reads .py and .sql for text assertions. Shared: SQL keyword text only. |
| `code_audit/source_text_assertions.py:207` (Python side) | `py_ci_shared/source_text_ban.py:187` | OVERLAPPING | Shared is a lexical line scan; pyutilz is AST. |
| `code_audit/mutable_defaults.py:399` | `py_ci_shared/shared_mutable_placeholder.py:131` | OVERLAPPING | pyutilz: mutable defaults and parameter aliasing. Shared: mutable placeholder returned by reference; its docstring also has a mutable-default class. |
| `code_audit/dead_wiring.py:101` | `py_ci_shared/uncalled_functions.py:277` | OVERLAPPING | Core is the same (no production call site). pyutilz: public names only, plus consumer roots. Shared: all module-level functions, with exemptions and baseline. |
| `code_audit/todo_hygiene.py:13` | `py_ci_shared/stale_comment_age.py:467` | OVERLAPPING | pyutilz: TODO without attribution. Shared: TODO or commented-out call older than a limit (issue reference exempts). Different axes. |
| `code_audit/docstring_numbers_moved_to_config.py:126` | `py_ci_shared/prose_numeric_claims.py:94` | OVERLAPPING | Same concept (hand-typed numbers drift). Pyutilz uses a heuristic on docstrings; shared needs registered claims. |
| `code_audit/llm_max_tokens_cap.py:45` | `py_ci_shared/hardcoded_token_ceilings.py:160` | OVERLAPPING | pyutilz: no `max_tokens` argument on a pyutilz LLM provider call. Shared: literal output ceilings. Absence vs literal. |
| `code_audit/undeclared_imports.py:139` | `py_ci_shared/unresolved_imports.py:377` | UNRELATED | Self-declared counterpart, but pyutilz checks undeclared third-party deps and shared checks first-party names. |
| `code_audit/constructor_param_overwritten.py:229` | `py_ci_shared/unread_init_params.py:127` | UNRELATED | pyutilz: param overwritten later. Shared: param never read. |
| `code_audit/sql_selects_unread_column.py:181` | `py_ci_shared/statement_columns_exist_in_ddl.py:922` | UNRELATED | Unread column vs nonexistent column. |
| `code_audit/table_drift.py:130` | `py_ci_shared/schema_snapshot_parity.py:353` | UNRELATED | Header/row drift in output vs DB schema drift. |
| `code_audit/sql_lint.py:49,161,213` | `py_ci_shared/sql_verify.py:185` | UNRELATED | Lint of SQL text vs executing SQL on Postgres. |
| `code_audit/credential_logging.py:57` | `py_ci_shared/secret_safe_test_output.py:90` | UNRELATED | Log arguments vs pytest report redaction. |
| `code_audit/duplicate_credential_regex.py:21` | `py_ci_shared/tracked_secret_shapes.py:189` | UNRELATED | Regex copies vs secret-shaped strings in git blobs. |
| `code_audit/skip_masking_except.py:36` | `py_ci_shared/skip_as_pass.py:92` | UNRELATED | Skip in except around a test call vs script printing SKIPPED and exiting 0. |
| `code_audit/sibling_guard_missing.py:155` | `py_ci_shared/guard_population.py:182` | UNRELATED | Missing sibling guard vs guard with empty file selection. |
| `code_audit/override_signature_drift.py:168` | `py_ci_shared/stub_signature_parity.py:378` | UNRELATED | Base method lacks an override's parameter vs test stub lacks a real parameter. |
| `code_audit/signature_gated_kwargs.py:79` | `py_ci_shared/kwarg_forwarding.py:182` | OVERLAPPING | Both are dropped-argument shapes; the signature-gate form is pyutilz-only. |
| `code_audit/nan_equality.py:42` | `py_ci_shared/polars_null_equality.py:198`, `identity_comparisons.py:223` | UNRELATED | NaN equality is not null or identity handling. |
| `code_audit/tautological_guard.py:68`, `vacuous_assertions.py:93,140` | none | pyutilz-only | No shared counterpart. |
| `code_audit/retry_loops.py:101` | none | pyutilz-only | Confirmed: no retry gate in py-ci-shared. |

## SAME-RULE pairs

Three: `import_cycles`, `source_text_assertions` vs `source_text_claims`, and `reexport_patch_target` vs `patch_target_read_at_call`. The first two are superset relations (the shared gate catches the pyutilz cases plus more).

## pyutilz detectors with no py-ci-shared counterpart (would need a port)

Confirmed by reading or by the absence of any counterpart: `retry_loops` (busy retry, unbounded retry), `tautological_guard`, `vacuous_assertions` (tautological is-not-None, vacuous same-target `or`), `vacuous_matching`, `dead_import`, `readonly_to_numpy_mutation`, `unraised_exceptions`, `credential_logging`, `duplicate_credential_regex`, `undeclared_imports` (declared-deps half), `unreachable_import_fallback`, `llm_max_tokens_cap` (absence half), `sql_lint` (MIN/MAX over JSON text, LIMIT without ORDER BY, OFFSET pagination), `sql_selects_unread_column`, `sentinel_cached_as_answer`, `sentinel_guard_mismatch`, `nan_equality`, `missed_await`, `mojibake`, `console_unicode`, `lazy_log_assertion`, `log_throttle`, `locals_get`, `locals_globals_output`, `network_timeout`, `dead_cli_flags`, `dead_endpoint_params`, `module_docstring`, `unit_suffix_mismatch`, `write_count_from_input`, `spy_arity`, `sibling_guard_missing`, `timing_literal_in_call`, `per_call_state_on_shared_instance`, `uncurated_star_export`, `return_annotation`, `docstring_args`.

Not individually read (list by name only, so treat as unverified): the remaining pyutilz modules not named above, such as `accumulator_helper_bypassed`, `assert_in_loop`, `async_primitive_reinit`, `claimed_invariants`, `closures`, `column_no_write_path`, `count_then_fetch_same_table`, `effect_flag_outside_its_effect`, `field_text_agreement`, `guard_decidable_from_constants`, `measurement_hygiene`, `mutation_during_iteration`, `partial_fix`, `provenance_flow`, `redundant_test_fit`, `settings_container_nodecode`, `sql_sibling_missing_time_bound`, `stats_key_coverage`, `uncached_constant_cost_probe`, `write_count_from_input`, `domain_boundary`. Several may have a shared gate under another name that the name-based pass missed; they need a read before being called pyutilz-only.

## py-ci-shared gates with no pyutilz counterpart (stay)

Most of the shared set, including: pytest and CI plumbing (`resource_leak_guard`, `pytest_*`, `ci_*`, `hook_*`, `gate_*`, `install_safe_hook`, `worker_budget`), mutation tooling (`mutation_*`, `teeth_sweep`), SQL verification (`sql_verify`, `schema_snapshot_parity`, `statement_columns_exist_in_ddl`, `sql_function_privileges`, `statement_compilation`), baselines and ratchets (`baseline_ratchet`, `ignore_ratchet`, `baseline_hygiene`), dependency and version gates (`git_dependency_pins`, `version_consistency`, `tool_versions`, `pinned_tool_versions`), `llm_output_caps`, `llm_call_archive_gate`, `repo_hygiene`, `worktree_hygiene`, `stale_comment_age`, `comment_repo_paths`, `timezone_honest`, `naive_utcnow`, `mypy_gate`, `value_bearing_asserts`, `pickle_state_completeness` (partly, see above), `function_length`, `complexity_ratchet`, `loc_budget`, `hash_key_determinism`.

## Corrections to plan section 3

1. Module names: `stale_source_citation` is `stale_source_citations.py`; `import_cycle` is `import_cycles.py`; `source_text_assertion` is `source_text_assertions.py`; `hardcoded_absolute_path_in_test` is a function in `hardcoded_test_path.py`; `shielded_resource_release_race` is a function in `shielded_resource_release.py`.
2. `vacuous_loop_assertion` vs `vacuous_loop_assertions`: OVERLAPPING, not "likely same". pyutilz's own docstring says the floor definitions disagree.
3. `sentinel_cached_as_answer` and `sentinel_guard_mismatch` are UNRELATED to `sentinel_or_fallback`, not overlapping.
4. `default_via_or` vs `config_getattr_default_parity`: UNRELATED. The overlap is with `sentinel_or_fallback`, and the relation is a subset.
5. `asymmetric_resource_guard`, `resource_leak_guard`, `shielded_resource_release` and `resource_release_paths`: no SAME pair. `resource_release_paths` has no pyutilz counterpart.
6. `reexport_patch_target` vs `patch_target_read_at_call`: SAME, not merely "to be checked". `patch_target_is_a_reexport` is its deliberate twin and maps to the same shared gate.
7. `retry loops`: confirmed pyutilz-only.
8. Missing from section 3, self-declared in pyutilz and worth tracking: `additive_epsilon_denominator` (epsilon_padded_denominators), `getattr_literal_on_known_dataclass` (config_getattr_default_parity), `comment_names_missing_symbol` (phantom_code_references), `duplicate_function_body` and `near_duplicate_function_body` (drifted_duplicate_functions), `unpicklable_resource_state` (pickle_state_completeness), `nondiscriminating_test` (nondiscriminating_shapes), `bare_except`, `broad_except` and `silent_escalation` (swallowed_exceptions).
9. `undeclared_imports` is self-declared as a counterpart of `unresolved_imports`, but the rules are disjoint: UNRELATED.

SAME-RULE pairs found: 3.
