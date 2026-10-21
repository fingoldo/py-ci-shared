# Retrofit group 0: corpus-gate keyword contract

Scope: the 26 entries of group g0 (23 modules); 24 retrofitted, 2 not. Each retrofitted entry gained a keyword `allow_unparsed: bool = False`
(default keeps the current failure on an unparsable file) and, where it had none, a `min_*` floor defaulting to 1,
the floor the module already used elsewhere in the package. Default-argument callers see the same result except where
an empty or unparsable corpus previously passed silently, which is the defect the contract targets.

Floor value 1 is real on this repository: every module below scans a directory with files in it (the package, the
migrations, the tests), so a default of 1 is not vacuous. A corpus with zero parsed files fails.

### RETRO-1 (Low) -- alembic_concurrently.assert_concurrently_is_in_autocommit_blocks -- added `min_files: int = 1` (floor) and `allow_unparsed: bool = False`; empty versions dir fails via `parsed_count < min_files`, unparsable migration fails unless allowed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_gate_contract_retrofit_group0.py tests/test_alembic_concurrently.py -n 4 --no-cov` passed.

### RETRO-2 (Low) -- audit_path_references.assert_no_open_round_paths -- added `allow_unparsed: bool = False`; the unparsable-file problem is skipped when set. Floor `min_files` already existed (default 1).
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_audit_path_references.py -n 4 --no-cov` passed.

### RETRO-3 (Low) -- config_call_site_parity.assert_call_site_defaults_match_schema_defaults -- added `allow_unparsed: bool = False`, threaded through `_calls_or_fail`. Floor `min_files` (default 1) and `min_checked` (default 1) already existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_config_call_site_parity.py -n 4 --no-cov` passed.

### RETRO-4 (Low) -- config_call_site_parity.assert_every_cfg_get_call_resolves_to_a_schema_field -- added `allow_unparsed: bool = False` (via `_calls_or_fail`). Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** as above.

### RETRO-5 (Low) -- config_call_site_parity.assert_every_schema_field_has_a_reader -- added `allow_unparsed: bool = False` (via `_calls_or_fail`). Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** as above.

### RETRO-6 (Low) -- config_call_site_parity.assert_no_divergent_cfg_get_call_site_defaults -- added `allow_unparsed: bool = False` (via `_calls_or_fail`). Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** as above.

### RETRO-7 (Low) -- config_call_site_parity.assert_no_module_scope_frozen_cli_defaults -- added `min_files: int = 1` (floor, a parsed-count check now in `find_module_scope_frozen_cli_defaults`, which also gained `min_files` and `allow_unparsed`, both keyword-only with defaults 0 and False so other callers are unchanged) and `allow_unparsed: bool = False`. The entry's `except` now catches `AssertionError` so the floor failure is reported with `pytest.fail`.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_config_call_site_parity.py -n 4 --no-cov` passed.

### RETRO-8 (Low) -- config_getattr_default_parity.assert_getattr_defaults_match_schema -- added `allow_unparsed: bool = False` (last parameter). Floor `min_files` (default 1) existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_gate_contract_retrofit_group0.py -n 4 --no-cov` (full module suite in the targeted run) passed.

### RETRO-9 (Low) -- db_transaction_completeness.assert_no_new_incomplete_transaction -- added `allow_unparsed: bool = False` (keyword, before `**find_kwargs`), passed to `scan.assert_ok`. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_db_transaction_completeness.py -n 4 --no-cov` passed.

### RETRO-10 (Low) -- doc_identifier_parity.assert_doc_identifiers_exist -- added `allow_unparsed: bool = False`, passed to `_absent`, which skips an unreadable document instead of reporting it when set. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_doc_identifier_parity.py -n 4 --no-cov` passed.

### RETRO-11 (Low) -- effect_assertion_parity.assert_effects_are_asserted -- added `allow_unparsed: bool = False`, passed to `find_unasserted_effects` (new keyword, default False) which omits the `<unparsable>` problems when set. Floor `min_modules` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_effect_assertion_parity.py -n 4 --no-cov` passed.

### RETRO-12 (Low) -- fail_open_handlers.assert_no_new_fail_open_handlers -- added `allow_unparsed: bool = False`, passed to `_baseline_problems` (keyword) which skips the unparsed-file problem when set. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_fail_open_handlers.py -n 4 --no-cov` passed.

### RETRO-13 (Low) -- hardcoded_token_ceilings.assert_no_hardcoded_token_ceilings -- added `allow_unparsed: bool = False`, forwarded to the shared `report()` helper, which already had the parameter. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_hardcoded_token_ceilings.py -n 4 --no-cov` passed.

### RETRO-14 (Low) -- import_cycles.assert_no_import_cycles -- added `allow_unparsed: bool = False`. `enforce_findings` (in `_gate_run.py`, outside this group, not edited) has no such switch, so the local scan's `unparsed` list is cleared when the flag is set; the scan is created fresh inside this call. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_import_cycles.py -n 4 --no-cov` passed.

### RETRO-15 (Low) -- llm_call_archive_gate.assert_every_llm_call_is_archived -- added `allow_unparsed: bool = False`, threaded through `_parsed` (new keyword) into `find_direct_sdk_calls`, `find_unwrapped_providers`, `find_generate_calls_without_archive` (each gained a keyword `allow_unparsed` defaulting to False). Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_llm_call_archive_gate.py -n 4 --no-cov` passed.

### RETRO-16 (Low) -- marker_runner_coverage.assert_every_marked_test_is_selected -- added `allow_unparsed: bool = False`, forwarded to `find_unselected_marked_tests` (new keyword) which drops the `<unparsed>` problems when set. Floor `min_marked` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_marker_runner_coverage.py -n 4 --no-cov` passed.

### RETRO-17 (Low) -- naive_utcnow.assert_no_naive_utcnow -- added `allow_unparsed: bool = False`; the unparsed-file problem is skipped when set. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_naive_utcnow.py tests/test_gate_contract_retrofit_group0.py -n 4 --no-cov` passed.

### RETRO-18 (Low) -- polars_null_equality.assert_polars_null_equality -- added `allow_unparsed: bool = False`, forwarded to the shared `report()` helper. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_polars_null_equality.py -n 4 --no-cov` passed.

### RETRO-19 (Low) -- pytest_markers.assert_markers_registered -- added `allow_unparsed: bool = False`, threaded through `registered_markers`, `find_unregistered_markers`, `conftest_markers` and `used_markers` (each gained a keyword defaulting to False; their unparsable-file raises are skipped when set). Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_pytest_markers.py -n 4 --no-cov` passed.

### RETRO-20 (Low) -- runtime_registry_mutation.assert_writes_have_replay -- added `allow_unparsed: bool = False`; `check_unparsed()` is skipped when set. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_runtime_registry_mutation.py -n 4 --no-cov` passed.

### RETRO-21 (Low) -- sql_function_privileges.assert_definer_functions_are_locked_down -- added `min_files: int = 1` (floor: fewer `.sql` files than the floor is a problem, replacing the `if not files` check, which is the same behaviour at 1) and `allow_unparsed: bool = False`, forwarded to `find_unlocked_definer_functions` (keyword-only, defaults 1 and False). An unreadable migration is recorded by the new helper `_read_migration` only when not allowed. Floor value 1 is the old "no .sql files" check.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_sql_function_privileges.py -n 4 --no-cov` passed.

### RETRO-22 (Low) -- stdlib_json_ban.assert_no_stdlib_json -- added `allow_unparsed: bool = False`, forwarded to the shared `report()` helper. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_gate_contract_retrofit_group0.py -n 4 --no-cov` passed.

### RETRO-23 (Low) -- unread_init_params.assert_no_unread_init_params -- added `allow_unparsed: bool = False`, skips `check_unparsed()` and is passed to `find_unread_init_params` (which already had the switch). Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_unread_init_params.py -n 4 --no-cov` passed.

### RETRO-24 (Low) -- value_bearing_asserts.assert_no_value_bearing_asserts -- added `allow_unparsed: bool = False`; the unparsed-file problem is skipped when set. Floor `min_files` existed.
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
**Verified:** `pytest tests/test_value_bearing_asserts.py -n 4 --no-cov` passed.

## Not retrofitted

- **stale_comment_age.assert_no_stale_todos** (no floor, no allow_unparsed). Its only unreadable-file path is a lossy UTF-8 decode, deliberate and never a failure, so an `allow_unparsed` switch would change nothing. A `min_files` floor would make an empty scan fail, which changes today's result for a caller whose scan dirs hold no candidate files. Needs an owner decision on which semantics the gate should take.
- **test_partition_reachability.assert_partitions_reachable** (no floor, no allow_unparsed). An unreadable runner or index file already fails hard (`pytest.fail`) and cannot be skipped without making the reachability check vacuous; the input is a set of named paths, not a scanned corpus, so a file-count floor does not map onto it. Needs a decision on the contract for non-corpus inputs.

## Verification

- `python -m ruff check` on all 20 changed `src/py_ci_shared` modules and the new test file: all checks passed.
- `python -m mypy src/py_ci_shared`: Success, no issues found in 252 source files.
- `python -m black --check` on the changed files: clean after formatting `pytest_markers.py`, `sql_function_privileges.py`, `config_call_site_parity.py` (black reflowed only the new signatures and call lines). The only remaining black complaint in `src/py_ci_shared` is `phantom_code_references.py`, not in this group.
- Targeted tests, `pytest -n 4 --no-cov` over the 23 modules' test files plus `tests/test_gate_contract_retrofit_group0.py` (new regression tests: naive_utcnow, stdlib_json_ban, alembic_concurrently, unread_init_params) and `tests/test_gate_entry_contract.py` / `tests/test_gate_teeth.py`: 1386 passed, 1 failed. The failure is `test_no_new_corpus_gate_entry_lacks_the_floor_or_allow_unparsed` only in its second assertion, which reports the 24 retrofitted lines in `tests/baselines/gate_entry_contract.json` as stale. That is the expected shrink-only signal; the baseline was not edited, as instructed. Its first assertion (no new gaps) passes, so no entry in the group is still missing the contract except the two listed above, which remain in the baseline.
- Re-run after formatting: the affected test files (`test_sql_function_privileges`, `test_pytest_markers`, `test_config_call_site_parity`, `test_marker_runner_coverage`, `test_llm_call_archive_gate`, `test_gate_teeth`, and the new test file): 986 passed.

Lines to delete from `tests/baselines/gate_entry_contract.json` (parent's job): the 24 retrofitted entries above, i.e. every g0 entry except `stale_comment_age.assert_no_stale_todos` and `test_partition_reachability.assert_partitions_reachable`.
