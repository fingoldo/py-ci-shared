# Move of pyutilz.dev.meta_test_utils into py_ci_shared (2026-11-05)

Owner-approved, per the Option B decision in `audits/2026-10-21/option_b_migration.md`: py-ci-shared owns the gates and the
meta-test tooling; pyutilz keeps no re-export and every consumer imports `py_ci_shared.meta_test_utils` directly.

## What moved

- `pyutilz/src/pyutilz/dev/meta_test_utils.py` became `src/py_ci_shared/meta_test_utils.py`. Public names are identical (`__all__` unchanged).
- Internal changes: imports are package-relative and top-level (the deferred import of `code_audit._base` and its stale "imported BY
  code_audit" comment are gone; nothing in `code_audit` imports this module); the tree walk is `_core.corpus.iter_files_pruned` with
  `DEFAULT_EXCLUDE`; `findings_ratchet` reads its baseline with stdlib `json` instead of `pyutilz.core.jsonlib` (the only real pyutilz
  dependency); `count_user_deferred_entries` is imported from `deferred_entries` instead of wrapped. It contains no `walk-ok` marker and
  `tests/test_no_unpruned_tree_walk.py` has no exemption for it (the guard scans every module under `src/py_ci_shared`).
- Registered in `registry.INTERNAL_MODULES` (it is a toolkit, not a gate).
- `pyutilz.dev._pruned_walk` was NOT moved. Decision: pyutilz declares no py-ci-shared dependency (`pyproject.toml` has none, TESTING.md
  and `tests/conftest.py` find it through `PY_CI_SHARED_DIR`), and `dev.persistence_sweep` (stays in pyutilz) needs a pruned walk at import
  time, so pyutilz keeps its 30-line private copy, its test and its own unpruned-walk guard.
- Tests moved to `tests/`: `test_meta_test_utils.py`, `test_meta_test_utils_roundtrip.py`, `test_meta_test_utils_capture_signature.py`,
  `test_meta_test_utils_dead_helpers.py`, `test_meta_test_utils_todo_hygiene.py`, plus two new files assembled from pyutilz test files:
  `test_meta_test_utils_audit_regressions.py` (from `test_dev_fixes_regression.py` and `test_domain_core_dev_system_audit_20260903.py`)
  and `test_meta_test_utils_pruned_walk.py` (the meta_test_utils half of pyutilz `test_dev_pruned_walk.py`).

## Consumers changed (no shim)

- pyutilz: module deleted; 13 lazy re-exports removed from `dev/__init__.py`; `tests/conftest.py` regexp line; `webtext.py` comment;
  TESTING.md; CHANGELOG ("Behaviour change"); `tests/test_meta/test_api_stability.py`, `test_deferred_drift.py`, `test_test_source_parity.py`;
  stale ignore entry `scan_todo_markers` in `uncalled_functions_ignore.py`.
- mlframe: `tests/test_meta/test_api_stability.py`, `test_dead_helpers.py`, `test_deferred_drift.py`.
- glossum_backend_scripts: `tests/test_meta/test_verdict_field_roundtrip.py`, `test_verdict_roundtrip_all.py`.
- social (monorepo): `upwork/new_scraper/production_scrapers/tests/test_meta/test_deferred_drift.py`,
  `upwork/new_scraper/realtime_applications/tests/test_meta/test_dead_helpers.py`.
- autopsia (detached worktree on d2201df3): `tests/test_meta/_shared.py`, `test_audit_dispositions_are_backed.py`, `test_mutation_teeth.py`,
  `tests/test_paid_objects_survive_their_round_trip.py`.
- No import found in algopacksimple, dash_app_core, llm_bench, noema_app, noema_master, polyvocab_app and the other repositories.

## Pins the owner must move

Not changed here. `ci_pin_version_skew` fails when workflow pins disagree with each other or with the installed version, so a pin edit
needs one real py-ci-shared release (version tag + sha) applied everywhere at once; a placeholder sha would be wrong the moment this
report is committed. Push py-ci-shared first, tag it, then:

- autopsia: `pyproject.toml` `test` and `dev` extras (`py-ci-shared @ git+...@85d40eff...  # v1.23.6`) and every workflow pin
  (`.github/workflows/*.yml`, same sha, `py-ci-shared-ref`). The test extra is what installs the module for the meta-tests.
- mlframe: workflow pins `2ab4fdf4... # v1.23.8` in `.github/workflows/*.yml`. py-ci-shared is not a declared dependency; CI installs it
  through the workflow step and local runs through `PY_CI_SHARED_DIR`. The three test_meta files now need that install to contain the module.
- glossum_backend_scripts, social: check their CI install step for the py-ci-shared ref (not inspected); they import the module in tests only.
- pyutilz: no pin to move (no declared dependency); its remaining tests that import `py_ci_shared.meta_test_utils` are skipped by
  `tests/conftest.py` `collect_ignore` where py-ci-shared is absent (py-ci-shared needs Python 3.9+).

## Tests run (PYTHONPATH pointing at this checkout's src; one file at a time)

- py-ci-shared: test_meta_test_utils 11, _roundtrip 48, _capture_signature 11, _dead_helpers 1, _todo_hygiene 2, _audit_regressions 6,
  _pruned_walk 4, test_no_unpruned_tree_walk 13, test_package_inventory 514, all passed (roundtrip and capture_signature re-run after
  the ruff fixes made by the commit hook).
- pyutilz: test_api_stability 1, test_deferred_drift 1, test_dev_pruned_walk 4, test_no_unpruned_tree_walk 13, test_uncalled_functions_gate 1,
  test_dev_fixes_regression 15, test_domain_core_dev_system_audit_20260903 34 passed. test_test_source_parity: 1 passed, 1 failed on
  `tests/code_audit/test_pyutilz_bound_redundant_test_fit.py` having no target module; that file and the missing exemption are not part of
  this change.
- mlframe: test_deferred_drift 1, test_dead_helpers 1, test_api_stability 2 passed.
- autopsia (worktree on d2201df3, clinical_hpoa.jsonl copied in): test_paid_objects_survive_their_round_trip 5 passed,
  test_audit_dispositions_are_backed 3 passed 1 xfailed; test_mutation_teeth failed with MutationHarnessError (its unmutated baseline
  exceeded 300 s on the loaded machine; the module imported fine).

## Not run or not committed

- glossum_backend_scripts: its two test files refuse to run (DATABASE_URL names the production database); only the import was changed.
- social: the two edited files are NOT committed; the repo's pre-commit meta-test hooks fail on 9 pre-existing `lock-held-across-io`
  findings in files this change does not touch.
- Pins are unchanged (see above).
