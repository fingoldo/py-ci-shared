# Retrofit group 1: min_* floor and allow_unparsed on corpus gate entries

Scope: 25 entries of 22 modules listed under `g1` in `gate_contract_groups.json`. 20 entries retrofitted, 5 not (reasons below).
Baseline `tests/baselines/gate_entry_contract.json` not edited; the 20 retrofitted lines are for the parent to delete.

Conventions used:
- `allow_unparsed: bool = False`. With `False` the code path is unchanged. With `True` an unreadable or unparsable file is no longer a failure (its findings are dropped); the floor still applies to the parsed files.
- Enforce-based gates (`enforce_findings`, which has no switch of its own) clear `scan.unparsed` before the call when `allow_unparsed` is set. The ScanResult is local to the gate, so nothing else sees the change.

### RETRO-101 (Low) -- atomic_write_staging.assert_atomic_write_staging -- added `allow_unparsed`; `min_files` already present (default 1, parsed files)
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Clears `scan.unparsed` before `enforce_findings` when allowed.
**Verified:** `python -m ruff check` on the file passes; targeted `tests/test_atomic_write_staging.py` passes; `tests/test_gate_contract_group1.py` (unparsable file fails unless allowed, floor still applies) passes.

### RETRO-102 (Low) -- audit_wave_filenames.assert_no_new_audit_wave_filenames -- NOT retrofitted
**Disposition:** NOT RETROFITTED -- reads file names only, nothing is parsed, so allow_unparsed has no meaning
It reads test file names only (`iter_files` + name match); nothing is parsed, so there is no unparsable file for `allow_unparsed` to mean anything about. Its floor `min_files` already exists. Leaving it in the baseline.
**Verified:** read of `src/py_ci_shared/audit_wave_filenames.py` lines 55-104 (no parse call in the scan path).

### RETRO-103 (Low) -- coverage_config_parity.assert_coverage_config_parity -- added `allow_unparsed`, passed to `report(...)`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`report()` already takes `allow_unparsed` and reports `problems` only when it is False. `min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_coverage_config_parity.py` passes.

### RETRO-104 (Low) -- discarded_model_copy.assert_no_discarded_model_copy -- added `allow_unparsed`; `scan.check_floor()` replaced by `scan.assert_ok(allow_unparsed=...)`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Floor (`min_files`, default 1) unchanged, now followed by the unparsed check that the old code skipped. `allowed` checks unchanged.
**Verified:** `python -m ruff check` passes; targeted `tests/test_discarded_model_copy.py` passes.

### RETRO-105 (Low) -- docs_inventory_parity.assert_no_inventory_drift -- NOT retrofitted
**Disposition:** NOT RETROFITTED -- takes problem strings and reads no files; the contract belongs on its find_* producers
Takes a list of problem strings and a label; it reads no files. The corpus scan lives in the `find_*` helpers, which are not gate entries. A floor or switch on this function would be a no-op. The producers would need the contract, which is a separate change.
**Verified:** read of `src/py_ci_shared/docs_inventory_parity.py` lines 394-399.

### RETRO-106 (Low) -- env_flag_parsing.assert_env_flags_use_one_parser -- added `allow_unparsed`; unparsed message gated on it
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present (floor on parsed files).
**Verified:** `python -m ruff check` passes; targeted `tests/test_env_flag_parsing.py` passes.

### RETRO-107 (Low) -- function_length.assert_functions_do_not_grow -- added `allow_unparsed`; unparsed problems omitted when allowed
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
Floor is `min_functions` (default 50), already present and already a `min_*` name. I did not measure the function count of this repository against 50; that check is still to do before a consumer relies on the default.
**Verified:** `python -m ruff check` passes; targeted `tests/test_function_length.py` passes.

### RETRO-108 (Low) -- hash_key_determinism.assert_hash_keys_are_deterministic -- added `allow_unparsed`; clears `scan.unparsed` before `enforce_findings`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_hash_key_determinism.py` passes.

### RETRO-109 (Low) -- import_layering.assert_layering -- added `min_files` (default 1) and `allow_unparsed`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`find_layering_violations` gained the same two keyword arguments. The floor counts files that matched a rule's `from_glob` (`examined`). Default 1 keeps today's behaviour: the existing check already fails when rules are given and nothing matched; the message now reads "examined nothing (N file(s) matched, need M)". The per-target judgement was moved into `_rule_problems` to stay under ruff's C901 limit; no change in what it reports.
The floor 1 is the smallest real floor: a rule that matches no file examines nothing. I did not measure a larger number for this repository, since it holds no layering rules of its own.
**Verified:** `python -m ruff check src/py_ci_shared/import_layering.py` passes; `python -m pytest -n 4 --no-cov tests/test_import_layering.py` 23 passed.

### RETRO-110 (Low) -- local_copy_report.assert_local_copies_do_not_grow -- added `allow_unparsed`; clears `scan.unparsed` on each scan
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_local_copy_report.py` passes.

### RETRO-111 (Low) -- meta_private_imports.assert_no_private_meta_imports -- added `allow_unparsed`; unparsed report gated on it
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present (parsed files).
**Verified:** `python -m ruff check` passes; targeted `tests/test_meta_private_imports.py` passes.

### RETRO-112 (Low) -- no_xfail_to_defer.assert_no_xfail_to_defer -- added `allow_unparsed`; clears `scan.unparsed` before `enforce_findings`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_no_xfail_to_defer.py` and `tests/test_no_xfail_to_defer_known_gap.py` pass.

### RETRO-113 (Low) -- phantom_code_references.assert_no_count_claim_mismatches -- added `min_files` (default 1) and `allow_unparsed`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
BEHAVIOUR CHANGE for direct callers: the old function silently passed on an empty list and silently skipped unparsable files (their comments were never read). Now an empty list fails ("would check nothing") and an unreadable file is a finding unless `allow_unparsed`. A floor of 0 would be the vacuous pass the contract forbids, so 1 is the floor: the gate has to read at least one file. I did not run this function over the full repository tree.
**Verified:** `python -m ruff check` passes; `tests/test_gate_contract_group1.py` (empty input fails, unparsable file is a finding) passes.

### RETRO-114 (Low) -- phantom_code_references.assert_no_phantom_code_references -- added `allow_unparsed`, passed to `find_phantom_code_references` (new keyword, default False)
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
With `False` the unparsable-file violation is reported as before. `min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_phantom_code_references.py` passes.

### RETRO-115 (Low) -- phantom_code_references.assert_no_stale_absolute_line_citations -- added `min_files` (default 1) and `allow_unparsed`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
BEHAVIOUR CHANGE, same reasoning as above: empty input now fails, and an unparsable file was a silent skip (`continue`) and is now a finding unless `allow_unparsed`. Floor 1, same reasoning.
**Verified:** `python -m ruff check` passes; `tests/test_gate_contract_group1.py` passes.

### RETRO-116 (Low) -- pickle_state_completeness.assert_no_pickle_state_gaps -- added `allow_unparsed`; clears `scan.unparsed` before `enforce_findings`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_pickle_state_completeness.py` passes.

### RETRO-117 (Low) -- pickle_state_completeness.assert_pickle_round_trips -- NOT retrofitted
**Disposition:** NOT RETROFITTED -- runtime round trip, no source parsed
It round-trips runtime objects from `factories`; it parses no source, so `allow_unparsed` has no meaning. The existing "empty mapping fails" check is a floor of 1 in disguise; renaming it to a `min_factories` parameter would fix the floor half only, and the entry would stay in the baseline for the switch. Left as is.
**Verified:** read of `src/py_ci_shared/pickle_state_completeness.py` lines 141-163.

### RETRO-118 (Low) -- private_imports.assert_no_private_cross_package_imports -- added `allow_unparsed`; unparsed report gated on it
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_private_imports.py` passes.

### RETRO-119 (Low) -- repo_hygiene.assert_repo_hygiene -- NOT retrofitted
**Disposition:** NOT RETROFITTED -- git and byte checks, no parse step and no count to floor
It checks git-tracked generated files, NUL/BOM bytes in text files, required files, scripts under `audits/`, and numeric CI gates. None of those is a parse step, so an "unparsable file" switch has no meaning, and none of the checks counts a corpus, so there is no floor to add. Left in the baseline.
**Verified:** read of `src/py_ci_shared/repo_hygiene.py` lines 313-357.

### RETRO-120 (Low) -- save_failure_markers.assert_markers_are_fatal -- added `allow_unparsed`; unparsed message gated on it
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_markers` already present (default 1).
**Verified:** `python -m ruff check` passes; targeted `tests/test_save_failure_markers.py` passes.

### RETRO-121 (Low) -- sql_verifier_coverage.assert_verifier_covers_statements -- added `allow_unparsed`; unparsed message gated on it
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_constants` already present (default 1).
**Verified:** `python -m ruff check` passes; targeted `tests/test_sql_verifier_coverage.py` passes.

### RETRO-122 (Low) -- stale_source_citations.assert_no_stale_source_citations -- added `allow_unparsed`; clears `scan.unparsed` before `enforce_findings`
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_stale_source_citations.py` passes.

### RETRO-123 (Low) -- survivorship_scoring.assert_no_survivorship_scoring -- added `allow_unparsed`; `scan.check_unparsed()` skipped when allowed
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_survivorship_scoring.py` passes.

### RETRO-124 (Low) -- timezone_honest.assert_timezone_honest -- NOT retrofitted
**Disposition:** NOT RETROFITTED -- corpus is read by ruff; a real switch needs a decision on ruff's syntax-error behaviour, not verified
Floor `min_files` exists. The corpus is read by ruff, not by the `_core` scanner, so an unparsable file is ruff's business. Adding a real `allow_unparsed` needs a decision on what the ruff run skips when a file has a syntax error, and I did not verify ruff's behaviour on syntax errors here. Left in the baseline.
**Verified:** read of `src/py_ci_shared/timezone_honest.py` lines 150-218.

### RETRO-125 (Low) -- unresolved_imports.assert_all_from_imports_resolve -- added `allow_unparsed` (passed to `_unresolved`, which skips unparsable files and index-level unparsed files when set)
**Disposition:** RETROFITTED -- allow_unparsed and a min_* floor added to the gate; the gate passes on this repository.
`min_files` already present.
**Verified:** `python -m ruff check` passes; targeted `tests/test_unresolved_imports.py` passes; `tests/test_gate_contract_group1.py` passes.

## Whole-group verification

- `python -m ruff check` on each changed module and `tests/test_gate_contract_group1.py`: All checks passed.
- `python -m mypy src/py_ci_shared`: Success, no issues in 252 source files.
- `python -m pytest -n 4 --no-cov` over the targeted tests of the 20 changed modules, `tests/test_gate_contract_group1.py`, and `tests/test_gate_entry_contract.py`: 409 passed, 1 failed. The failure is `test_no_new_corpus_gate_entry_lacks_the_floor_or_allow_unparsed`, reporting the 20 retrofitted lines as "retrofitted or renamed" in the baseline. That is the intended shrink signal; the parent deletes those lines. The "no new gap" half of that test passes.
