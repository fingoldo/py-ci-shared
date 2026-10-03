# 2026-10-03 round: remainders of the partly-done findings

Worktree from origin/master `f5a28b8`. Every fix below has a test that fails on the code before it (checked by
reverting the source file and re-running the test).

### N-19

**Disposition:** RESOLVED -- `Baseline.regenerate` (`src/py_ci_shared/_core/baseline.py:271`) no longer catches the
`BaselineError` of a corrupt baseline and turns it into `previous=None`, so a refresh raises "baseline X is unreadable
(JSONDecodeError: ...); fix or delete it" and leaves the file untouched instead of rendering "does not exist, and seeding
it would accept ...". `load_json` (line 80) and `Baseline.load` (line 252) use that wording for bad JSON and for a wrong
shape. Sibling paths fixed the same way: `mutation_teeth._regenerate` (`src/py_ci_shared/mutation_teeth.py:814`, it dropped
the notes of a corrupt baseline and overwrote it), and the raw `json.loads` of a baseline in `function_length`,
`ignore_ratchet`, `import_side_effects`, `audit_wave_filenames`, `fail_open_handlers`, `loc_budget`,
`phantom_code_references` and `vacuous_loop_assertions` now go through `load_json` (named error, no raw
`JSONDecodeError`). Tests: `tests/test_core_baseline.py::test_a_corrupt_baseline_refresh_says_unreadable_not_missing_and_keeps_the_file`
(6 cases: bad JSON, a merge-conflict marker, a wrong shape, with and without growth),
`::test_mutation_teeth_regenerate_does_not_drop_the_notes_of_a_corrupt_baseline`,
`::test_sibling_ratchets_name_a_corrupt_baseline_instead_of_a_json_traceback[function_length|ignore_ratchet|audit_wave_filenames]`;
all 10 fail on origin/master.

### WF-11

**Disposition:** RESOLVED -- the 11 remaining EXEMPT entries are now real canaries (`tests/canary/<gate>/` with
violation/clean/bom and, where the gate parses, unparsable; `CANARIES.update` in `tests/test_gate_teeth.py`): hook_hygiene,
test_partition_reachability, doc_identifier_parity, phantom_code_references, effect_assertion_parity,
sql_verifier_coverage, audit_path_references, audit_disposition_parity, disposition_test_references, timezone_honest,
import_layering. No exemption was kept for this list. Markdown/shell readers (hook_hygiene, doc_identifier_parity,
audit_disposition_parity) have `parses=False`: they read text, so there is no unparsable input. The canaries found
gate defects, fixed here:
- `effect_assertion_parity`: an unparsable `test_*.py` was silently absent from `build_import_map` and never reported;
  `find_unasserted_effects` now parses every file it walks (`src/py_ci_shared/effect_assertion_parity.py:824`). Test:
  `tests/test_effect_assertion_parity.py::test_an_unparsable_test_the_import_map_dropped_is_still_reported`.
- test_partition_reachability (the module): an unparsable `dart_test.yaml` raised a bare yaml error without the file name (now
  `SourceParseError` naming file and line, `src/py_ci_shared/test_partition_reachability.py:104`), and a runner directory
  with no runner in it passed (now fails, line 231). Tests: `tests/test_test_partition_reachability.py::test_an_unparsable_tags_file_fails_naming_it`,
  `::test_a_runner_directory_without_runners_fails_instead_of_passing`.
- No floor: `phantom_code_references.assert_no_phantom_code_references` (`min_files=1`, line 581),
  `doc_identifier_parity.assert_doc_identifiers_exist` (`min_files=1` on documents checked, line 191; its `**kwargs` became
  the explicit keywords of `find_absent_doc_identifiers`) and `timezone_honest.assert_timezone_honest` (`min_files=1` on
  non-test Python under the scan paths, line 156) passed an empty corpus. Tests: the `-empty` canary cases;
  `tests/baselines/gate_entry_contract.json` records the three as `allow_unparsed` only.
The two problems the 2026-10-03 workflows agent noticed: `gate_integrity._load_yaml` (`src/py_ci_shared/gate_integrity.py:82`)
raises `SourceParseError` naming the file and line, and `assert_narrowings_declared` fails with it
(`tests/test_gate_integrity.py::test_an_unparsable_config_fails_naming_the_file_and_line[precommit|workflow]`);
`pytest_addopts_path_runs` keeps the markers of the parsed files when another test file is unparsable
(`_marked_in_parsed_files`, `src/py_ci_shared/pytest_addopts_path_runs.py:88`), so only the broken file is reported
(`tests/test_pytest_addopts_path_runs.py::test_a_broken_file_does_not_strip_the_markers_of_the_parsed_ones`).
`tests/test_gate_teeth.py`: 564 passed (was 466 after the first 7).

### WF-15

**Disposition:** RESOLVED -- `tests/test_ci_install_covers_conftest.py:254`: the env loop counts the variables it
checked and asserts the set equals the gate's input map, so a renamed env name fails instead of skipping every
assertion. The false positive in `tests/test_package_inventory.py:79` got the floor the scanner asks for
(`len(registry.GATES) >= 100`). The finding's dogfooding step is done: `[tool.py_ci_shared.gates.vacuous_loop_assertions]`
in `pyproject.toml:244` (all `tests/**/test_*.py`, `min_files = 100`, empty baseline `tests/baselines/vacuous_loop_assertions.json`)
and the gate is in the dogfood set of `tests/test_self_gates.py`. Test: `tests/test_self_gates.py::test_gate_passes_on_this_repo[vacuous_loop_assertions]`,
which fails on the old `test_ci_install_covers_conftest.py` naming line 248.

### G-7

**Disposition:** RESOLVED -- `local_copy_report` judges every `conftest.py` under the test dirs by content
(`include_conftest`, `src/py_ci_shared/local_copy_report.py:165`; `_conftest_copies` line 144) against the new
`CONFTEST_SIGNATURES` (line 99): `resource_leak_checks:streams` = an autouse fixture, an identity comparison on
`sys.stdout`/`sys.stderr`, and an assignment back to them. A conftest that imports `py_ci_shared.resource_leak_checks` is
not reported. Measured read-only on fresh worktrees of origin HEAD: mlframe 1 finding off -> 2 on, the new one is
`tests/conftest.py` (`_restore_closed_standard_streams`, the guard G-7 named); pyutilz 3 -> 3, glossum_backend_scripts
1 -> 1 (no conftest hit in 337 and 1104 parsed files). No consumer calls `local_copy_report` (git grep over every repo
under Machine learning: only py-ci-shared's own README, CHANGELOG and an audit), so the default is `True`: no baseline
can grow; `include_conftest=False` restores the old scan. Tests: `tests/test_local_copy_report.py::TestConftest` (5: the
guard, near misses without a restore / without identity or autouse / importing the central check, a nested conftest
outside the meta dirs).

### G-12

**Disposition:** RESOLVED --
(a) `python -m py_ci_shared.stale_comment_age --stale-warning-summary REPO [REPO ...] [--scan-dir D] [--max-age-days N]
[--warn-days N]` (`stale_warning_summary`, `src/py_ci_shared/stale_comment_age.py:543`; `main` line 581): one line per
checked-out repo ("N going stale within W days, M already older than L days"), scope and limits from the repo's own
`[tool.py_ci_shared.gates.stale_comment_age]` table, else `.`/30/7, then `stale-comment early warnings: N`. A repo that
cannot be dated (shallow, not a git tree, missing dir) is "not checked", counted, and makes the exit code 1. Run on the
three consumers: mlframe 0 / 12 stale, pyutilz 1 / 0, glossum_backend_scripts 0 / 5, total 1. Tests:
`tests/test_stale_comment_age.py::TestStaleWarningSummary` (3).
(b) Precision of the commented-out-code detector. The populations are far below 40, so every flagged item was read:
mlframe `src` (its gate's scope) 6 flagged now, 6 real (`# num_zerocross(arr),`, four lines in the vendored infonet
model, `# example_highd()`); its baseline `_stale_comment_baseline.json` holds 34 commented-out-code entries, all 34 real
(prints, plt calls, `torch.save`, a `super().init(...)`); pyutilz `src` 1 flagged, 0 real (`# self.stats.setdefault("k", 0)`
in `stats_key_coverage.py`), and 2 of 2 false at 635d0c5^ (plus `# self._inc_stat("k")`, the line that commit
reworded); glossum_backend_scripts `glossum`,`scripts` 0 flagged; py-ci-shared itself 1 flagged, 0 real
(`# Path.open(mode, buffering, encoding, errors, newline)` in `lf_file_writes.py`). Precision before: 40/43 overall,
mlframe 40/40, pyutilz 0/2, py-ci-shared 0/1. All three false positives are one class: an AST analyser labelling a branch
with the call shape it matches. Fix: in a file that imports `ast`, a code-shaped comment whose called name is also a
string literal of that file is a label (`_shapes_this_file_matches`, line 335, used in `_candidates`). After: mlframe 6/6
still flagged, pyutilz 0, pyutilz at 635d0c5^ 0, py-ci-shared 0. Tests: `tests/test_stale_comment_age.py::TestShapeLabelsInAnalysers`
(3: the labels are not code; a real disabled call in the same analyser still is; the same label outside an AST analyser
still is).

## Verification

32 test files (the touched modules' tests plus test_package_inventory, test_self_gates, test_gate_teeth,
test_gate_entry_contract, test_scaffold, test_mutation_teeth, test_docs_inventory_parity, test_corpus_drift), -n 4:
1847 passed, 1 failed (self-gate complexity_ratchet: `assert_partitions_reachable` grew to 16, `find_unasserted_effects`
shrank to 20). Fixed by two helpers in test_partition_reachability and the lowered ceiling in
`tests/baselines/complexity_ratchet.json`; the affected selection was then re-run: 77 passed. ruff 0.16.1 clean, mypy clean, black
26.5.1 clean on the changed files (6 files on origin/master are already unformatted and were left alone).
