# 35_self_meta_and_scan_speed

Round 2026-11-04. Every finding is OPEN until its Disposition line says otherwise.

## 35-F1 (P1) ~32% of a code_audit scan of pyutilz is raw ast.walk: 183 call sites in 65 scanners bypass the memoised walk, and subtree walks re-walk the corpus ~5x

- Evidence: Wrapped ast.walk (scratchpad walkcount35.py), run_all_checked(pyutilz/src, parallel=False): total 48.0s, 15.2s inside ast.walk. 1,667 module-level walks (1.16M nodes) and 90,229 subtree walks (5.82M nodes, 5x the corpus). Top: async_primitive_reinit.py 12,598 subtree walks 2.7s; additive_epsilon_denominator.py 1.8s; accumulator_helper_bypassed.py 1.2s; effect_flag_outside_its_effect.py 0.9s; sentinel_guard_mismatch.py 0.8s; stats_key_coverage.py 0.8s; duplicate_function_body.py 0.6s; unreachable_import_fallback.py 183 uncached module walks. cProfile of same run: 147.9s, ast.walk cum 59.3s (iter_child_nodes 48.6s, isinstance 21s). grep: 183 'ast.walk(' in code_audit/ (65 files) vs 148 walk_tree( uses; top-level gates have 0 raw ast.walk (they use _core.node_index).
- Failure scenario: Each consumer scan pays about a third of its time re-traversing AST that a shared memo could serve; mlframe-size repos pay minutes.
- Proposed fix: (1) Swap whole-module ast.walk(tree) for walk_tree/nodes_of in the 65 files. (2) Add a memoised subtree_nodes(node) (children lists already built by _core.node_index.child_nodes) so nested FunctionDef walks cost O(total). Estimated saving 10-13s of 48s (~25%) on pyutilz/src. Add a shrink-only ratchet on raw ast.walk count in code_audit/ (baseline 183).
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. Every ast.walk (169 real calls in 57 scanners) and ast.iter_child_nodes (20 calls) in code_audit now goes through walk_nodes/child_nodes from _base, i.e. _core.node_index. node_index.walk memoises the descendants of ANY node (weak-keyed, freed with its tree), so a function walked inside a module walk costs one pass, not one per nesting level. tests/test_gates_use_shared_traversal.py::test_scanner_package_has_no_raw_ast_walk is the shrink-only ratchet (baseline 0, from 183+20). Measured on a copy of pyutilz/src (185 files, run_all_checked parallel=False, scan cache bypassed, same machine, interleaved): plain wall 39.2 and 41.5 s before, 26.0 to 28.2 s after (about 35 percent less); under cProfile 144.7 s before, 90.8 s after, ast.iter_child_nodes calls 16.85M to 3.19M, ast.walk calls 6.96M to 0. Findings are byte-identical: pyutilz/src 0 findings both ways (cmp of the sorted JSON), and py-ci-shared's own source copy 189 findings both ways. Self-gate (repo_hygiene NUL/BOM rules, 3 repeated calls over tests/): see 35-F3.

## 35-F2 (P2) code_audit._base._safe_parse and _core.source.parse_source are two separate parse caches: every file is parsed (and walked) twice in one run_all

- Evidence: Counted ast.parse (scratchpad parsecount35.py) on pyutilz/src, 185 files: 370 parses, callers code_audit/_base.py:310 x185 and _core/source.py x185 (scan_import_cycles -> py_ci_shared/import_cycles.py:344 scan_python). 3.1s parse total, ~1.5s duplicated. walk_tree memo (_base.py) and _core.node_index memo hang off different tree objects: _base.py shows 370 module walks / 451,548 nodes = 2 x 225,774.
- Failure scenario: ~3% extra time and doubled tree memory; a fix to one cache (BOM handling: _safe_parse reads plain utf-8 while _core.source decodes BOM; RecursionError skip) misses the other, so the two layers can disagree whether a file parses.
- Proposed fix: _safe_parse delegates to _core.source.parse_source (SourceReadError/SourceParseError -> None); walk_tree delegates to _core.node_index. One cache.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED. code_audit._base._safe_parse delegates to _core.source.parse_file; SourceReadError/SourceParseError (and RecursionError/MemoryError, kept from the old handler) give None. _PARSE_CACHE and its LRU are gone; _base.clear_parse_cache also clears the _core cache; walk_tree is list(walk_nodes(tree)) over the same memo. Result: parse_source calls per file 1 (the 185 files were parsed twice before, 370 parses), and a BOM file now parses in the scanners exactly as in every other gate (tests/code_audit/test_registry_invariants.py::test_safe_parse_reads_a_bom_file_like_core, test_parse_cache_is_shared_with_core_and_clearable replace the old LRU-bound test, which tested a cache that no longer exists).

## 35-F3 (P2) Pre-push self gate is I/O-bound, not parse-bound: _io.open is 340s of 606s profiled (23,576 opens, ~14 reads per file); parse only 6s

- Evidence: cProfile of cli.main(['run-all']) on py-ci-shared (scratchpad selfgate35.py; profiler overhead and other agents loading the box inflate absolutes): total 605.8s; _io.open tottime 339.99s over 23,576 calls; nt.scandir 9.1s over 24,804 calls (gate_cache._stat_entries 13.4s); node_index child_nodes+_walk ~50s; ast.parse 1,551 calls = 6.07s. Only ~1,600 candidate files exist (repo_hygiene._candidate_files: 1596 files, 757 .py, 712 .canary). repo_hygiene reported 363.5s, standard_stream_restore 39.8s, unresolved_imports 14.4s, shared_mutable_placeholder 15.5s.
- Failure scenario: The ~4 min pre-push cost scales with opens per file (NTFS/AV latency ~10ms each); a faster parser would change nothing. Gates reading bytes directly (repo_hygiene.py:211 _read_bytes_or_none, read_text corpora) re-open every file.
- Proposed fix: One process-wide bytes cache in _core.source keyed (path, mtime_ns, size) used by read_source, repo_hygiene._text_file_bytes and iter_files readers; meta-test asserting <=2 opens per file during run-all. Before implementing, run pstats.print_callers on _io.open to attribute opens by gate (not done here; the 363s repo_hygiene figure may include cold-cache and concurrent-load noise).
- AST-checkable: False; FP risk: med
- Disposition: RESOLVED. `_core.source.read_bytes_cached` is the one process-wide bytes cache (stat signature plus the racy-timestamp rule of the parse cache; files over 4 MB never kept; table dropped past 400 MB) used by read_source, parse_source, gate_cache.tree_digest and repo_hygiene._read_bytes_or_none; a counting io.open over repo_hygiene.find_text_files_with_nul_bytes plus find_text_files_with_a_bom, 3 rounds over tests/, went from 2446 opens to 421. Per-gate attribution is now a tool: `_core.open_audit.count_opens()` records every `io.open` (also `open`, `Path.read_text/read_bytes`) per file and per gate (the innermost py_ci_shared module on the stack that is not a `_core` helper), and `python -m py_ci_shared._core.open_audit` runs `run-all` under it. Measured over the whole `run-all` on this repo: 2589 opens over 1722 files (the profiled run in this finding had 23,576); the most-opened file is pyproject.toml with 4; by gate: cli 1723 (the corpus walk), doc_identifier_parity 857, version_consistency 2, baseline_ratchet 2, every other gate 1 or none. So no gate re-reads a file more than a couple of times and the remaining cost is the first read of each file. `tests/test_open_audit.py` pins the counter (attribution, opens outside the directory ignored) and caps opens per file at 2 over 4 rounds of repo_hygiene, read_source and parse_file on aged files, with a negative control (an uncached `read_bytes` loop reaches 4). The 54 gate-local `read_text` sites migrate to read_source as they are touched; the cap test is the net for the shared readers.

## 35-F4 (P2) budget="warn" lets repo_hygiene (363s vs 30s) and standard_stream_restore (39.8s vs 30s) pass the self gate with exit 0

- Evidence: selfgate35.out lines 1-2 'BUDGET repo_hygiene took 363.5s, over its 30s budget'; pyproject.toml:184 budget = "warn"; run-all returned RC 0.
- Failure scenario: A 10x slowdown of a gate (an uncached walk added) never fails pre-push; only a human reading stderr notices.
- Proposed fix: budget="fail" in the self-ci job with budget_s calibrated per gate (e.g. 3x median), keep warn locally; or ratchet per-gate seconds via baseline_trend.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED (additive). cli._report keeps the exit code but now also prints a BUDGET summary line after the totals, a GitHub ::warning annotation per over-budget gate (when GITHUB_ACTIONS is set) and writes the list to GITHUB_STEP_SUMMARY; budget = fail is unchanged and still fails. tests/test_cli.py::test_over_budget_is_announced_under_warn_without_changing_the_exit_code. No consumer turns red. Not changed on purpose: this repo's own pyproject keeps budget = warn, because calibrating budget_s per gate needs a quiet-machine run of the whole suite (repo_hygiene measured 363 s under load and 30 s budget); flipping to fail before that calibration would make the pre-push hook flaky.

## 35-F5 (P3) near_duplicate_function_body._candidate_pairs is O(n^2) over functions

- Evidence: cProfile: near_duplicate_function_body.py:209 _candidate_pairs 1.70s self / 5.73s cum; scan_near_duplicate_function_body 11.6s cumulative on pyutilz/src; difflib find_longest_match 196 calls 1.7s.
- Failure scenario: Quadratic in function count: a bigger consumer takes minutes in one scanner, and parallel mode cannot split a single scanner.
- Proposed fix: Bucket by (size band, normalised-token hash/minhash) and compare only within buckets.
- AST-checkable: False; FP risk: med
- Disposition: RESOLVED. `_candidate_pairs` was already an inverted shingle index; what stayed super-linear was counting EVERY posting of every shingle of every entry. It now visits each pair once from its smaller side and follows only a prefix of that side's rarest non-generic shingles (pigeonhole: a partner sharing `ceil(0.5*len)` shingles holds one of any `m-need+1`), then takes the exact shared count by set intersection. Output is identical, not approximately: the previous generator is kept as `_candidate_pairs_all_postings` and compared on py-ci-shared src (4254 functions, 4 pairs), py-ci-shared tests (5454, 30), pyutilz/src (1498, 50) and the whole pyutilz repo (7912, 33): equal sets, candidate generation 11.9 to 7.2 s, 5.7 to 2.8 s, 6.5 to 3.2 s, 15.3 to 5.0 s (noisy host). `test_prefix_filtered_candidates_equal_the_all_postings_reference` pins the equality on a randomised drifted corpus.

## 35-F6 (P3) No self meta-test for public API quality: 20 of 1018 public module-level functions not fully annotated, 68 lack docstrings, 194 public modules lack __all__

- Evidence: scratchpad props35.py over src/py_ci_shared (358 files): unannotated e.g. black_filtered_apply.discover_py_files, run_black_stdin, is_all_blank, looks_like_import_or_call_list; 68 public functions without docstring; 194 public modules without __all__. grep of tests/ found no repo-wide test of these (only per-gate tests).
- Failure scenario: Consumers import un-annotated functions that mypy treats as Any; star-imports leak helpers.
- Proposed fix: tests/test_self_api_hygiene.py with shrink-only baselines (20 / 68 / 194), prototype in props35.py.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. tests/test_self_api_hygiene.py with shrink-only baselines in tests/baselines/self_api_hygiene.json: unannotated public functions 20, public functions without docstring 61, public modules without __all__ 184 (the audit counted 68 and 194; the difference is in how a public module and a public function are delimited, here no path part and no name may start with an underscore). A new offender fails; a healed baseline entry fails until deleted. test_rule_detects_what_it_claims is the teeth check.

## 35-F7 (P3) print() in non-CLI library modules: 14 calls in arb_checks, baseline_ratchet, import_layering, sql_verify (not cli=true in registry)

- Evidence: props35.py: 55 print() calls without T201 noqa / file=; excluding modules with registry cli=true leaves 14 in arb_checks.py, baseline_ratchet.py, import_layering.py, sql_verify.py. pyproject.toml:156-165 lists T201 per-file ignores for CLI modules only.
- Failure scenario: A library assert_* function that prints pollutes consumer pytest output and cannot be silenced.
- Proposed fix: Self meta-test: print() only in registry cli=true modules or inside main()/_main(); baseline 14.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED. Same file, rule library_print: print() outside registry cli=true modules, outside main/_main/_cmd_*, without file= or a T201 noqa. Baseline 14 call sites in 6 functions (arb_checks.assert_arb_catalogues_are_sound 1, baseline_ratchet.Baseline.enforce 3, import_layering.assert_layering 1, sql_verify.check 2, check_loader 3, run_checks 4), the audit's 14. The advisory prints in those four modules are deliberate (pyproject T201 per-file ignores) and stay in the baseline until they move to a logger.

## 35-F8 (P3) No self meta-test on module-level call statements: 113 top-level Expr(Call) statements in library modules

- Evidence: props35.py: 113 module-level call statements excluding append/add/update/setdefault/register/extend (e.g. registry.py:155-159, ci_install_covers_conftest.py:201). test_import_side_effects.py and test_import_time_global_side_effects.py target consumer code and canaries, not this repo's own modules.
- Failure scenario: A top-level statement doing I/O or env mutation would run in every consumer that imports a gate.
- Proposed fix: AST meta-test over src/py_ci_shared: module-body Expr(Call) only from an allow-list (registration helpers); reviewed shrink-only baseline.
- AST-checkable: True; FP risk: high
- Disposition: RESOLVED. Same file, rule import_time_calls: module-body Expr(Call) other than append/add/update/setdefault/register/extend. The audit's 113 counted a different set than this definition (module-body statements, the only ones that run at import). Baseline: 3 (code_audit/registry.py register_scanner x105 and register_check_alias x7 are the scanner registration table, _LineLoader.add_constructor in ci_install_covers_conftest). Anything else at module level fails.

## 35-F9 (P3) Canary exemptions: 35 of 179 gates have no tests/canary/<name>/ directory; staleness of EXEMPT entries is not asserted

- Evidence: props35.py: gates 179, canary dirs 144, missing 35 (arb_checks, audit_round_format, baseline_hygiene, constant_relations, deferred_drift, repo_hygiene, version_consistency, workflow_runner_labels, ...). tests/test_gate_teeth.py:775 enforces canary-or-EXEMPT, so this is by design.
- Failure scenario: An EXEMPT gate that later gains a corpus scan (scan_python/iter_files) keeps its exemption unnoticed.
- Proposed fix: Test that every EXEMPT key names a registry gate and none of them satisfies _core.gate_contract.scans_a_corpus.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. tests/test_gate_teeth.py::test_canary_exemptions_are_valid_and_still_needed: every EXEMPT key names a module, and an EXEMPT gate whose source now calls a corpus function (_core.gate_contract.scans_a_corpus) must carry its reason in CORPUS_SCAN_EXEMPT_REASONS. Eight exempt gates already scan a corpus (config_getattr_default_parity, docs_inventory_parity, edge_function_hygiene, function_complexity, inert_patch_targets, prompt_field_parity, repo_hygiene, stale_comment_age); each has a recorded reason, and a ninth would fail the test.

## 35-F10 (P3) Verified already satisfied (no action): bare except 0, registry sorted/unique, registry vs README 0 absent, registry vs tests 0 missing, cli=true without main 0, registry entries naming absent functions 0

- Evidence: props35.py: bare except 0; registry 219 entries sorted True, dups 0; names absent from README 0; gates without test file 0; cli=true without def main []; absent entry functions 0. Existing guards: test_gate_entry_contract.py (min_files/allow_unparsed), test_gate_teeth.py, test_package_inventory.py, test_docs_inventory_parity.py. tests/baselines/*.json are keyed dicts (5 empty), so a sorted/dedup assertion has nothing to check today.
- Failure scenario: n/a
- Proposed fix: Optionally pin ruff E722 explicitly in select.
- AST-checkable: True; FP risk: low
- Disposition: WON'T FIX (nothing to fix). Verified already satisfied; the optional E722 pin is redundant: ruff's default select already contains E722 (E4, E7, E9, F), and a bare except would fail the blocking ruff hook.
