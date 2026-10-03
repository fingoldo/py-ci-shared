# Dispositions: 20_core_infra.md (K-1 .. K-17)

Every RESOLVED item has a regression test that was run against the unfixed tree (a second worktree at `04e8795` with
the new test files copied in) and failed there, then passed on the fix. Exceptions are named in the item.

### K-1

**Disposition:** RESOLVED -- `cli._print` writes the line and, on `UnicodeEncodeError`, re-encodes it with
`backslashreplace` for the stream's own codec, so the finding, the remaining gates and the summary are printed and the
exit code is the contract's (1 findings, 2 ERROR). No global `reconfigure` of `sys.stdout` (an in-process `main()`
call must not change the caller's streams). `src/py_ci_shared/cli.py:45`. Tests:
`tests/test_cli.py::TestAudit20261003::test_k1_a_finding_the_console_cannot_encode_is_escaped_not_a_crash`
(subprocess, `PYTHONIOENCODING=cp1251`, `src/数据.py`, expects exit 1, `数据.py:2` and the summary) and
`test_k1_an_error_gate_keeps_exit_2_on_a_console_that_cannot_print_it` (exit 2, was 1 from the traceback).

### K-2

**Disposition:** RESOLVED -- decision: the plugin keeps reading pytest's rootdir `pyproject.toml` (pytest semantics,
backwards compatible); the CLI keeps the nearest `pyproject.toml` (or `--repo`). When they would disagree, i.e. the
nearest `pyproject.toml` above the invocation directory is a different file with its own `[tool.py_ci_shared]`
table, the plugin raises `UsageError` naming both files and the `--rootdir` fix, instead of dropping the gates.
`--py-ci-gates=on` with no table found is a `UsageError` too. Documented in the plugin docstring and README
"Configuring gates". `src/py_ci_shared/pytest_plugin.py:122` (`_locate_config`), `:90` (on-without-table). Tests:
`tests/test_pytest_plugin.py::test_k2_a_table_below_pytests_rootdir_is_a_usage_error_not_a_silent_skip`,
`test_k2_gates_on_without_any_table_is_a_usage_error` (both fail on the old tree: exit 0, "1 passed"), and
`test_k2_pointing_rootdir_at_the_table_runs_the_gates_the_cli_runs` (the documented way out; passes on both trees).

### K-3

**Disposition:** RESOLVED -- `_expand` escapes the repo root with `glob.escape` before appending the user's glob
(3.9-compatible; `root_dir=` is 3.10+), and a glob that matches nothing is a `ConfigError` naming it.
`src/py_ci_shared/_core/config.py:244`. Tests: `tests/test_cli.py::TestAudit20261003::test_k3_globs_work_under_a_root_with_glob_characters`
(`proj[1]`), `test_k3_a_glob_that_matches_nothing_is_a_config_error`. This repo's own table still loads
(`tests/test_self_gates.py`).

### K-4

**Disposition:** RESOLVED -- `git_listing` returns None only for "git missing", "not a work tree" and "root ignored".
Once `rev-parse` says the root is a work tree, a non-zero or timed-out `ls-files` raises `CorpusError` (a timed-out
probe too); the walk is reached only by `use_git=False` (explicit opt-in). The timeout is configurable through
`PY_CI_SHARED_GIT_TIMEOUT_S` (the `_core.git` runner, K-14). Submodules are excluded in both modes: the walk prunes
a directory holding a `.git` FILE (submodule / linked worktree), as git lists a submodule only as a gitlink.
`src/py_ci_shared/_core/corpus.py:63,73,100`. Tests: `tests/test_core_corpus.py::TestListingFailures::test_a_failed_ls_files_raises_instead_of_walking[timeout|exit-128]`,
`test_the_walk_stays_available_on_request`, `test_the_walk_leaves_submodule_checkouts_out_like_git_does`.

### K-5

**Disposition:** RESOLVED -- `multiprocessing` is imported with the plugin module, so the no-psutil process probe no
longer registers `__mp_main__` inside the first test; `__main__`/`__mp_main__` are never "libraries"; on Python < 3.10
(no `sys.stdlib_module_names`) a module is stdlib when it is built in or its `__file__` is under
`sysconfig.get_paths()["stdlib"|"platstdlib"]` and not in site-packages. psutil stays optional (not declared): the
guard now works without it, and degrades loudly, a `pytest_report_header` line says sockets are not checked and
processes are multiprocessing children only. `src/py_ci_shared/resource_leak_guard.py:33,185,202,270`. Tests:
`tests/test_resource_leak_guard.py::TestAudit20261003K5::test_without_psutil_the_first_test_is_guarded_and_the_run_says_psutil_is_missing`
(a `psutil.py` that raises ImportError; old tree: 1 passed, leak missed),
`test_stdlib_and_script_modules_are_not_libraries_without_stdlib_module_names` (deletes `sys.stdlib_module_names`;
fails on the old tree), `test_with_psutil_the_same_leak_is_caught`, `test_a_stdlib_import_inside_the_test_does_not_hide_its_leak`
(the 3.9 repro; it can only fail on 3.9). Clean-env verification: `uv venv --python 3.9|3.11|3.13` with only
`pip install <checkout> pytest` (no psutil): `test_pytest_plugin.py::test_the_table_loads_the_resource_leak_guard_only_when_asked`,
`test_resource_leak_guard.py`, `test_core_git.py`, `test_core_node_index.py` all pass; results in the final report.

### K-6

**Disposition:** RESOLVED -- only a literal `v` is stripped: `t == version or (t.startswith("v") and t[1:] == version)`.
`src/py_ci_shared/version_tag_currency.py:136`. Test: `tests/test_version_tag_currency.py::TestAudit20261003::test_k6_only_a_literal_v_is_stripped_from_a_tag`
(tag `10.2.3` vs version `0.2.3`, plus `v0.2.3` and bare `0.2.3` controls).

### K-7

**Disposition:** RESOLVED -- `enable` must be a list of non-empty strings; `module`/`entry` non-empty strings;
`budget_s` a positive int/float (bool rejected); `enabled` a bool; `gates` a table; one gate may not be enabled twice,
dashes and underscores counted alike, across `enable` and gate tables. All raise `ConfigError` naming the key, so the
CLI exits 2 and the plugin raises `UsageError`. `src/py_ci_shared/_core/config.py:136,143,151,160`. Tests:
`tests/test_cli.py::TestAudit20261003::test_k7_malformed_shapes_are_config_errors_naming_the_key` (8 cases) and
`test_k7_a_bad_budget_is_exit_2_not_a_traceback`.

### K-8

**Disposition:** RESOLVED -- the iterable branch of `scan_python` dedupes on `path.resolve()` (absolute path on a
symlink loop) and keeps the first spelling for `rel`. `src/py_ci_shared/_core/scan.py:128-143,157`. Test:
`tests/test_core_scan.py::test_one_file_reached_by_two_spellings_is_scanned_once`.

### K-9

**Disposition:** RESOLVED -- `_INDEX` and `_MEMO` are `weakref.WeakKeyDictionary`s keyed on the tree, so an entry dies
with its tree (a tree replaced in the parse cache, or a plain `ast.parse` tree the caller dropped). The root node is
not stored in the index (a value referencing its own key would pin it); `nodes_of` inserts it at walk position 0.
`node_index.index_size()` added for the test. `src/py_ci_shared/_core/node_index.py:33-35,63-80,96-120`. Tests:
`tests/test_core_node_index.py::test_a_replaced_tree_is_freed_with_its_index_and_memos` (5 rewrites leave 1 live tree,
was 5), `test_the_root_node_is_still_returned_in_walk_order` (behaviour unchanged; passes on both trees).

### K-10

**Disposition:** RESOLVED -- `_count` requires an `int` (not bool) `>= 1` for both the `{"count": n}` and the bare-int
entry shapes, otherwise `BaselineError("<path>: entry 'k' has count ...")`. No committed baseline in this repo has a
zero count (grep over every `*.json`). `src/py_ci_shared/_core/baseline.py:105`. Tests:
`tests/test_core_baseline.py::test_a_malformed_count_is_a_baseline_error_naming_the_file` (str, negative, zero, float,
bool, bare negative int) and the control `test_a_well_formed_count_still_loads`.

### K-11

**Disposition:** RESOLVED -- `budget_verdict` returns None for an ERROR result. `src/py_ci_shared/_core/runner.py:117`.
Test: `tests/test_cli.py::TestAudit20261003::test_k11_a_configuration_error_has_no_budget_line` (CLI stderr has no
`BUDGET`, plus a FAILED control that still gets the verdict).

### K-12

**Disposition:** RESOLVED -- `RepoConfig.gate` compares names with `-` mapped to `_` and lists the enabled names in
its error. `src/py_ci_shared/_core/config.py:79`. Test:
`tests/test_cli.py::TestAudit20261003::test_k12_run_accepts_the_dashed_name_and_lists_what_is_enabled`.

### K-13

**Disposition:** RESOLVED -- every git call now goes through `_core.git.run_git` (bytes out, `git_text` = UTF-8 with
`errors="replace"`), including `_consumers._git_clone` (`src/py_ci_shared/_consumers.py:84`), and every remaining
`text=True` subprocess call in `src/` passes `encoding="utf-8", errors="replace"`: `checkout_resolution.py:100`,
`import_side_effects.py:311`, `guard_population.py:217`, `pinned_tool_versions.py:76`, `setup_env.py:56,61,89,91`
(`embedded_postgres.py` and `install_safe_hook.py` moved to the runner). Tests:
`tests/test_core_git.py::test_a_clone_error_survives_a_cp1251_console` (the report's repro in a subprocess with
`PYTHONUTF8=0`, `PYTHONIOENCODING=cp1251`, destination `dИr/x`; old tree: `'git clone exited 128'`),
`test_no_text_mode_subprocess_decodes_with_the_locale_codec` (AST meta-test over `src/`) with its teeth check
`test_the_text_mode_detector_has_teeth`, and `test_output_is_bytes_and_git_text_decodes_utf8_whatever_the_locale`.

### K-14

**Disposition:** RESOLVED -- new `src/py_ci_shared/_core/git.py`: `run_git(repo, *args, timeout, check, stdin, env,
inherit_location)` returns `CompletedProcess[bytes]`, runs `git -C repo`, timeout = argument, else
`PY_CI_SHARED_GIT_TIMEOUT_S`, else 120 s; strips `REPO_LOCATION_VARS` (`GIT_DIR`, `GIT_INDEX_FILE`, `GIT_WORK_TREE`,
`GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`, ... the location half of `git rev-parse --local-env-vars`) and keeps
configuration (`GIT_CONFIG_*`, `GIT_TERMINAL_PROMPT`), so the test suite's `GIT_CONFIG_*` isolation and
`_consumers`' token header still work; raises the typed `GitError` (`returncode`, `stderr`, `timed_out`) for a
missing binary, a timeout, or `check=True` with a non-zero exit. Plus `git_output`, `git_text`, `git_env`; exported
from `_core.__all__`. Migrated callers (thin local wrappers kept, so each module's own error contract is unchanged):
`_core/corpus.py` (`_git`), `adoption_matrix.py` (`RefResolver._git`, `__call__`), `baseline_trend.py` (`_run_git`),
`git_dependency_pins.py` (`_git`, timeout 60 kept), `stale_comment_age.py` (`_git`, GitError -> OSError as its callers
expect), `version_tag_currency.py` (`_run_git`), `worktree_hygiene.py` (`_run`, import swap only),
`git_changed_lines.py` (both calls; its private `_git_env` removed), and three runners the report did not list:
`repo_hygiene.tracked_files`, `corpus_drift._commit`, `embedded_postgres.main_checkout_file`, `install_safe_hook`
(`inherit_location=True`: it patches the repository the user is in). `tests/test_git_changed_lines.py`'s fixture helper now builds its env with `git_env()` too (it dropped every `GIT_*`, so the session's `GIT_CONFIG_*` isolation was off for the fixture commits but on for the diff, and the user's `core.autocrlf` made every line read as changed: 7 failures until aligned). Not migrated, with the reason in the meta-test:
`safe_precommit.py` runs inside pre-commit through pre-commit's own `cmd_output` on the committing repository.
Tests: `tests/test_core_git.py` (11 tests: hook `GIT_DIR` does not redirect `-C`, env policy, timeout env var,
missing binary, `check`), and the meta-test `test_no_module_spawns_git_outside_the_core_runner` with
`test_the_git_spawn_detector_has_teeth` (fails on the old tree with 16 direct spawns in 13 modules; the `text=True` meta-test with 16 sites).

### K-15

**Disposition:** RESOLVED (incremental, backwards compatible, as the report proposes; no mass rewrite) -- new
`src/py_ci_shared/_core/gate_contract.py`: `contract_gaps(func)` (lacks a `min_*` floor and/or `allow_unparsed`),
`scans_a_corpus(source)` (the module calls `scan_python`/`scan_tree`/`iter_files`/`read_text_corpus`), and
`check_scan(scan, min_files=, allow_unparsed=)`, the one-line body a retrofitted entry uses. Ratchet
`tests/test_gate_entry_contract.py` with baseline `tests/baselines/gate_entry_contract.json` (the 85 entries of the
89 corpus-scanning `assert_*` entries that lacked either on 2026-10-03): a NEW entry missing either fails, and a
baseline line that no longer applies fails too (shrink-only). Teeth checked by deleting one baseline line (the test
names it). README "Configuring gates" documents `min_files`/`allow_unparsed` as the canonical names; CLAUDE.md points
at the ratchet. Migration list (retrofit = add the parameter(s) through `check_scan`, delete the line):

Missing both (floor + allow_unparsed), 17: `alembic_concurrently.assert_concurrently_is_in_autocommit_blocks`, `config_call_site_parity.assert_no_module_scope_frozen_cli_defaults`, `doc_identifier_parity.assert_doc_identifiers_exist`, `docs_inventory_parity.assert_no_inventory_drift`, `edge_function_hygiene.assert_edge_functions_are_sound`, `import_layering.assert_layering`, `import_side_effects.assert_imports_have_no_side_effects`, `phantom_code_references.assert_no_count_claim_mismatches`, `phantom_code_references.assert_no_phantom_code_references`, `phantom_code_references.assert_no_stale_absolute_line_citations`, `pickle_state_completeness.assert_pickle_round_trips`, `repo_hygiene.assert_repo_hygiene`, `sql_function_privileges.assert_definer_functions_are_locked_down`, `stale_comment_age.assert_no_stale_todos`, `statement_compilation.assert_no_mocked_statement_constructors`, `test_partition_reachability.assert_partitions_reachable`, `timezone_honest.assert_timezone_honest`

Missing allow_unparsed only, 68: `atomic_write_staging.assert_atomic_write_staging`, `audit_path_references.assert_no_open_round_paths`, `audit_wave_filenames.assert_no_new_audit_wave_filenames`, `clock_day_boundary.assert_no_clock_day_boundary`, `complexity_ratchet.assert_complexity_does_not_grow`, `config_call_site_parity.assert_call_site_defaults_match_schema_defaults`, `config_call_site_parity.assert_every_cfg_get_call_resolves_to_a_schema_field`, `config_call_site_parity.assert_every_schema_field_has_a_reader`, `config_call_site_parity.assert_no_divergent_cfg_get_call_site_defaults`, `config_getattr_default_parity.assert_getattr_defaults_match_schema`, `coverage_config_parity.assert_coverage_config_parity`, `dataclass_case_completeness.assert_every_dataclass_has_a_case`, `db_transaction_completeness.assert_no_new_incomplete_transaction`, `discarded_model_copy.assert_no_discarded_model_copy`, `disposition_test_references.assert_disposition_tests_exist`, `drifted_duplicate_functions.assert_no_drifted_duplicate_functions`, `effect_assertion_parity.assert_effects_are_asserted`, `env_flag_parsing.assert_env_flags_use_one_parser`, `epsilon_padded_denominators.assert_no_epsilon_padded_power_denominators`, `fail_message_quality.assert_fail_messages_actionable`, `fail_open_handlers.assert_no_new_fail_open_handlers`, `function_complexity.assert_complexity_does_not_grow`, `function_length.assert_functions_do_not_grow`, `gpu_timing_sync.assert_no_unsynchronized_gpu_timings`, `hardcoded_token_ceilings.assert_no_hardcoded_token_ceilings`, `hash_fed_by_array_copy.assert_no_hash_fed_by_array_copy`, `hash_key_determinism.assert_hash_keys_are_deterministic`, `identity_comparisons.assert_no_identity_comparisons`, `import_cycles.assert_no_import_cycles`, `import_side_effects.assert_no_new_import_time_env_mutations`, `latched_availability_flags.assert_no_latched_availability_flags`, `lf_file_writes.assert_no_crlf_writes`, `llm_call_archive_gate.assert_every_llm_call_is_archived`, `local_copy_report.assert_local_copies_do_not_grow`, `machine_specific_paths.assert_no_machine_specific_paths`, `marker_runner_coverage.assert_every_marked_test_is_selected`, `meta_private_imports.assert_no_private_meta_imports`, `module_cache_thread_safety.assert_thread_safe_module_caches`, `module_reload_safety.assert_no_reloads_in_code`, `module_reload_safety.assert_no_unpaired_reloads`, `naive_utcnow.assert_no_naive_utcnow`, `no_xfail_to_defer.assert_no_xfail_to_defer`, `numba_seed_range.assert_numba_seeds_fit_int64`, `pickle_state_completeness.assert_no_pickle_state_gaps`, `plotly_annotation_loop.assert_no_plotly_annotation_loops`, `polars_null_equality.assert_polars_null_equality`, `private_imports.assert_no_private_cross_package_imports`, `pytest_addopts_path_runs.assert_path_runs_select_tests`, `pytest_markers.assert_markers_registered`, `readme_env_var_parity.assert_no_new_undocumented_env_vars`, `readme_env_var_parity.assert_readme_documents_every_env_var`, `reiterated_iterable_params.assert_no_reiterated_iterable_params`, `resource_release_paths.assert_released_on_every_path`, `rollback_then_continue.assert_no_rollback_then_continue`, `runtime_registry_mutation.assert_writes_have_replay`, `save_failure_markers.assert_markers_are_fatal`, `sentinel_or_fallback.assert_no_sentinel_or_fallback`, `sql_verifier_coverage.assert_verifier_covers_statements`, `sqlalchemy_text_binds.assert_no_colon_cast_binds`, `stale_source_citations.assert_no_stale_source_citations`, `stdlib_json_ban.assert_no_stdlib_json`, `survivorship_scoring.assert_no_survivorship_scoring`, `swallowed_exceptions.assert_no_swallowed_exceptions`, `uncalled_functions.assert_no_new_uncalled_function`, `unread_init_params.assert_no_unread_init_params`, `unresolved_imports.assert_all_from_imports_resolve`, `vacuous_loop_assertions.assert_no_new_floorless_loop`, `value_bearing_asserts.assert_no_value_bearing_asserts`

### K-16

**Disposition:** RESOLVED -- `find_stale_pin` counts only full releases (no prerelease suffix) spelled with the pin's
own prefix (`v` or none) and newer than the pin; the filter is `version_tag_currency.is_release_tag`, which
`adoption_matrix` now uses instead of its own `_FULL_RELEASE` regex, so the two modules agree.
`src/py_ci_shared/version_tag_currency.py:104,201`, `src/py_ci_shared/adoption_matrix.py:148`. Tests:
`tests/test_version_tag_currency.py::TestAudit20261003::test_k16_prereleases_and_other_spellings_are_not_releases_behind`
(the report's repro: pin `v1.0.0`, tags `v1.1.0-rc.1`, `v1.1.0-rc.2`, `10.2.3` -> not behind; then `v1.1.0`, `v1.2.0`
-> "2 release(s) behind v1.2.0"), `test_k16_the_release_filter_is_the_one_adoption_matrix_uses`.

### K-17

**Disposition:** RESOLVED -- README "Compatibility promise for `@v1`" (next to the `@v1` policy): stable within v1 are
`_core.__all__`, every module's `find_*`/`assert_*` and their existing keywords, the `[tool.py_ci_shared]` keys, the
CLI subcommands/flags/exit codes, the baseline formats and the reusable workflows' inputs; may change within v1 are
defect fixes that make a gate stricter, private names, message wording; every change that can turn a green consumer
red or changes what a refresh writes gets a CHANGELOG entry starting **Behaviour change** (the 1.18.0 shrink-only
refresh named as the case this prevents). CLAUDE.md "Versions and releases" carries the rule for contributors, and
CHANGELOG "Unreleased" applies it to this round's stricter behaviour (K-2, K-3, K-4, K-7, K-10). Documentation only;
no test (nothing executable to regress).
