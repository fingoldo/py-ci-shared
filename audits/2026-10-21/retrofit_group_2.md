# Gate contract retrofit, group 2

Scope: the 25 `module.function` entries of group 2 (`gate_contract_groups.json`, key `g2`). Each entry now takes
`allow_unparsed: bool = False` (keyword-only, default keeps the old result) and a `min_*` floor. Default behaviour for
existing callers is unchanged unless noted. `tests/baselines/gate_entry_contract.json` was not edited.

Common pattern: when the gate reported unparsable files through `scan.unparsed` (or an `unparsed-file` finding), the
new `allow_unparsed=True` drops those problems, the same as `comment_repo_paths.assert_comment_repo_paths`. Where a
floor was missing, the new parameter defaults to 1, which is the value the gate already enforced implicitly.
The floor is a parsed-file or target count, so a default of 1 means "the gate must see at least one real file", never
a number that passes an empty scan.

Verification (run once for the whole group, shared by every block below):
`python -m ruff check` on the 22 changed modules: All checks passed. `python -m mypy src/py_ci_shared`: Success, no
issues in 252 source files. Targeted tests with `-n 4 --no-cov` on the 22 gate test modules plus
`tests/test_gate_entry_contract.py`: 507 passed and one expected failure,
`test_no_new_corpus_gate_entry_lacks_the_floor_or_allow_unparsed`. That test fails only on the "retrofitted" half: it
lists all 25 entries as still in `gate_entry_contract.json`, which is for the parent to delete. Its new-gap half passes.
The new `tests/test_gate_contract_retrofit_group2.py` (5 tests) passes on its own, `-n 4 --no-cov`, and ruff is clean on it.

### RETRO-201 (Low) -- clock_day_boundary.assert_no_clock_day_boundary -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Passes `allow_unparsed` by clearing `scan.unparsed` before `enforce_findings`. Floor: `min_files=1` (existing), at least
one test file parsed.
**Verified:** ruff check and mypy clean; `tests/test_clock_day_boundary.py` passes.

### RETRO-202 (Low) -- dataclass_case_completeness.assert_every_dataclass_has_a_case -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
The unparsable-files message is emitted only when `allow_unparsed` is False. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_dataclass_case_completeness.py` passes.

### RETRO-203 (Low) -- disposition_test_references.assert_disposition_tests_exist -- added `allow_unparsed` (also on `find_missing_test_references`); floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
An unparsable test file is not reported as a problem when `allow_unparsed` is True (the names it defines are then
unconfirmed, which the caller accepts). Floor: `min_files=1` (existing), at least one audit file given.
**Verified:** ruff check and mypy clean; `tests/test_disposition_test_references.py` passes; regression test
`test_disposition_references_reports_unparsable_test_unless_allowed` passes.

### RETRO-204 (Low) -- edge_function_hygiene.assert_edge_functions_are_sound -- added `min_files=1` (new) and `allow_unparsed`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
New floor: `min_files=1`, a count of `*.ts` function sources (excluding `*.test.ts`) that must be found. The `not files`
branch is kept verbatim, so the default 0-file result is unchanged; the floor adds a message only for 1 to
`min_files-1` files. `allow_unparsed` skips unreadable files instead of reporting them. The value is the same implicit
floor the gate already had.
**Verified:** ruff check and mypy clean; `tests/test_edge_function_hygiene.py` passes; regression test
`test_edge_function_floor_counts_real_sources` passes.

### RETRO-205 (Low) -- fail_message_quality.assert_fail_messages_actionable -- added `allow_unparsed` (also on `fail_message_problems`); floor `min_audited=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Unparsable test files are not reported as bad messages when `allow_unparsed` is True. Floor: `min_audited=1`
(existing), at least one `pytest.fail` call audited.
**Verified:** ruff check and mypy clean; `tests/test_fail_message_quality.py` passes; regression test
`test_fail_message_quality_reports_unparsable_file_unless_allowed` passes.

### RETRO-206 (Low) -- gpu_timing_sync.assert_no_unsynchronized_gpu_timings -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
The unparsed-files problem is emitted only when `allow_unparsed` is False. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_gpu_timing_sync.py` passes.

### RETRO-207 (Low) -- identity_comparisons.assert_no_identity_comparisons -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Unparsable files are dropped from the problem list when `allow_unparsed` is True. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_identity_comparisons.py` passes.

### RETRO-208 (Low) -- import_side_effects.assert_imports_have_no_side_effects -- added `min_targets=1` (new floor) and `allow_unparsed`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
New floor: `min_targets=1`, the number of import targets, replacing the old `if not names` check (same effect at the
default). `allow_unparsed` is an alias that adds to `tolerate_unimportable`: an import target that cannot be imported is
the probe's equivalent of an unparsable file, so `allow_unparsed=True` tolerates it the same way. Floor: 1 target, the
minimum that makes the probe examine anything.
**Verified:** ruff check and mypy clean; `tests/test_import_side_effects.py` passes.

### RETRO-209 (Low) -- import_side_effects.assert_no_new_import_time_env_mutations -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
The unparsed-files problem is emitted only when `allow_unparsed` is False. Floor: `min_files=1` (existing), at least one
matching test file parsed.
**Verified:** ruff check and mypy clean; `tests/test_import_side_effects.py` passes.

### RETRO-210 (Low) -- lf_file_writes.assert_no_crlf_writes -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`problems=[] if allow_unparsed else scan.unparsed` passed to `report`. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_lf_file_writes.py` passes.

### RETRO-211 (Low) -- machine_specific_paths.assert_no_machine_specific_paths -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Unreadable or unparsable files are dropped from `problems` when `allow_unparsed` is True. Floor: `min_files=1`
(existing).
**Verified:** ruff check and mypy clean; `tests/test_machine_specific_paths.py` passes.

### RETRO-212 (Low) -- module_cache_thread_safety.assert_thread_safe_module_caches -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`problems=[] if allow_unparsed else scan.unparsed`. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_module_cache_thread_safety.py` passes.

### RETRO-213 (Low) -- module_reload_safety.assert_no_reloads_in_code -- added `allow_unparsed` (also on `_reloads_in_code`); floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Unparsable files are not returned as reload sites when `allow_unparsed` is True. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_module_reload_safety.py` passes.

### RETRO-214 (Low) -- module_reload_safety.assert_no_unpaired_reloads -- added `allow_unparsed` (also on `find_unpaired_reloads`); floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Unparsable test files are not returned as sites when `allow_unparsed` is True. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_module_reload_safety.py` passes.

### RETRO-215 (Low) -- plotly_annotation_loop.assert_no_plotly_annotation_loops -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`problems=[] if allow_unparsed else scan.unparsed`. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_plotly_annotation_loop.py` passes.

### RETRO-216 (Low) -- pytest_addopts_path_runs.assert_path_runs_select_tests -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`problems=[] if allow_unparsed else problems`, where `problems` holds the unparsable test files. Floor: `min_files=1`
(existing).
**Verified:** ruff check and mypy clean; `tests/test_pytest_addopts_path_runs.py` passes.

### RETRO-217 (Low) -- readme_env_var_parity.assert_readme_documents_every_env_var -- added `allow_unparsed` (through `_read_vars_or_fail`); floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
The unparsed check in `_read_vars_or_fail` runs only when `allow_unparsed` is False. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_readme_env_var_parity.py` passes.

### RETRO-218 (Low) -- readme_env_var_parity.assert_no_new_undocumented_env_vars -- added `allow_unparsed` (through `_read_vars_or_fail`); floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Same change as the sibling entry. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_readme_env_var_parity.py` passes.

### RETRO-219 (Low) -- resource_release_paths.assert_released_on_every_path -- added `allow_unparsed`; floor `min_subjects=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Unparsable files are not reported when `allow_unparsed` is True. Floor: `min_subjects=1` (existing), at least one module
constructs the resource.
**Verified:** ruff check and mypy clean; `tests/test_resource_release_paths.py` passes.

### RETRO-220 (Low) -- sentinel_or_fallback.assert_no_sentinel_or_fallback -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`scan.unparsed` cleared before `enforce_findings` when `allow_unparsed` is True. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_sentinel_or_fallback.py` passes.

### RETRO-221 (Low) -- sqlalchemy_text_binds.assert_no_colon_cast_binds -- added `allow_unparsed` (also on `find_colon_cast_binds`); floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
A `.py` file that cannot be parsed is not reported and not counted as scanned, both as before, but the report line is
skipped when `allow_unparsed` is True. Floor: `min_files=1` (existing), counted over parsed files.
**Verified:** ruff check and mypy clean; `tests/test_sqlalchemy_text_binds.py` passes; regression test
`test_sqlalchemy_colon_cast_reports_unparsable_file_unless_allowed` passes.

### RETRO-222 (Low) -- statement_compilation.assert_no_mocked_statement_constructors -- signature now names `min_files` and `allow_unparsed` (was `**kwargs`)
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files: int = 1` and `allow_unparsed: bool = False` are now explicit keyword-only parameters, forwarded to
`find_mocked_statement_constructors`. `**kwargs` still carries the remaining options. Floor: `min_files=1`, the same
default the finder already had.
**Verified:** ruff check and mypy clean; `tests/test_statement_compilation.py` passes.

### RETRO-223 (Low) -- swallowed_exceptions.assert_no_swallowed_exceptions -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`scan.unparsed` cleared before `enforce_findings` when `allow_unparsed` is True. Floor: `min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_swallowed_exceptions.py` passes.

### RETRO-224 (Low) -- uncalled_functions.assert_no_new_uncalled_function -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
The floor and unparsable-file checks moved into the private helper `_walk_problems(scan, allow_unparsed)`, which skips the
unparsed check when `allow_unparsed` is True. The helper keeps the function under the C901 limit of 10 (ruff). Floor:
`min_files=1` (existing).
**Verified:** ruff check and mypy clean; `tests/test_uncalled_functions.py` passes.

### RETRO-225 (Low) -- vacuous_loop_assertions.assert_no_new_floorless_loop -- added `allow_unparsed`; floor `min_files=1` already present
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Passed through to `_floorless_loops`, which already took `allow_unparsed`. Floor: `min_files=1` (existing), at least one
test file parsed.
**Verified:** ruff check and mypy clean; `tests/test_vacuous_loop_assertions.py` passes.

## Not retrofitted

None. All 25 entries in group 2 now pass `contract_gaps` (checked with `contract_gaps` over the 25 functions: no gaps).

## Notes for the parent

- `tests/baselines/gate_entry_contract.json` still lists all 25 entries; delete them there.
- New test file: `tests/test_gate_contract_retrofit_group2.py` (5 tests, all pass).
