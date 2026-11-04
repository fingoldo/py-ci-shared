# 35_self_meta_and_scan_speed

Round 2026-11-04. Every finding is OPEN until its Disposition line says otherwise.

## 35-F1 (P1) ~32% of a code_audit scan of pyutilz is raw ast.walk: 183 call sites in 65 scanners bypass the memoised walk, and subtree walks re-walk the corpus ~5x

- Evidence: Wrapped ast.walk (scratchpad walkcount35.py), run_all_checked(pyutilz/src, parallel=False): total 48.0s, 15.2s inside ast.walk. 1,667 module-level walks (1.16M nodes) and 90,229 subtree walks (5.82M nodes, 5x the corpus). Top: async_primitive_reinit.py 12,598 subtree walks 2.7s; additive_epsilon_denominator.py 1.8s; accumulator_helper_bypassed.py 1.2s; effect_flag_outside_its_effect.py 0.9s; sentinel_guard_mismatch.py 0.8s; stats_key_coverage.py 0.8s; duplicate_function_body.py 0.6s; unreachable_import_fallback.py 183 uncached module walks. cProfile of same run: 147.9s, ast.walk cum 59.3s (iter_child_nodes 48.6s, isinstance 21s). grep: 183 'ast.walk(' in code_audit/ (65 files) vs 148 walk_tree( uses; top-level gates have 0 raw ast.walk (they use _core.node_index).
- Failure scenario: Each consumer scan pays about a third of its time re-traversing AST that a shared memo could serve; mlframe-size repos pay minutes.
- Proposed fix: (1) Swap whole-module ast.walk(tree) for walk_tree/nodes_of in the 65 files. (2) Add a memoised subtree_nodes(node) (children lists already built by _core.node_index.child_nodes) so nested FunctionDef walks cost O(total). Estimated saving 10-13s of 48s (~25%) on pyutilz/src. Add a shrink-only ratchet on raw ast.walk count in code_audit/ (baseline 183).
- AST-checkable: True; FP risk: low
- Disposition: OPEN

## 35-F2 (P2) code_audit._base._safe_parse and _core.source.parse_source are two separate parse caches: every file is parsed (and walked) twice in one run_all

- Evidence: Counted ast.parse (scratchpad parsecount35.py) on pyutilz/src, 185 files: 370 parses, callers code_audit/_base.py:310 x185 and _core/source.py x185 (scan_import_cycles -> py_ci_shared/import_cycles.py:344 scan_python). 3.1s parse total, ~1.5s duplicated. walk_tree memo (_base.py) and _core.node_index memo hang off different tree objects: _base.py shows 370 module walks / 451,548 nodes = 2 x 225,774.
- Failure scenario: ~3% extra time and doubled tree memory; a fix to one cache (BOM handling: _safe_parse reads plain utf-8 while _core.source decodes BOM; RecursionError skip) misses the other, so the two layers can disagree whether a file parses.
- Proposed fix: _safe_parse delegates to _core.source.parse_source (SourceReadError/SourceParseError -> None); walk_tree delegates to _core.node_index. One cache.
- AST-checkable: False; FP risk: low
- Disposition: OPEN

## 35-F3 (P2) Pre-push self gate is I/O-bound, not parse-bound: _io.open is 340s of 606s profiled (23,576 opens, ~14 reads per file); parse only 6s

- Evidence: cProfile of cli.main(['run-all']) on py-ci-shared (scratchpad selfgate35.py; profiler overhead and other agents loading the box inflate absolutes): total 605.8s; _io.open tottime 339.99s over 23,576 calls; nt.scandir 9.1s over 24,804 calls (gate_cache._stat_entries 13.4s); node_index child_nodes+_walk ~50s; ast.parse 1,551 calls = 6.07s. Only ~1,600 candidate files exist (repo_hygiene._candidate_files: 1596 files, 757 .py, 712 .canary). repo_hygiene reported 363.5s, standard_stream_restore 39.8s, unresolved_imports 14.4s, shared_mutable_placeholder 15.5s.
- Failure scenario: The ~4 min pre-push cost scales with opens per file (NTFS/AV latency ~10ms each); a faster parser would change nothing. Gates reading bytes directly (repo_hygiene.py:211 _read_bytes_or_none, read_text corpora) re-open every file.
- Proposed fix: One process-wide bytes cache in _core.source keyed (path, mtime_ns, size) used by read_source, repo_hygiene._text_file_bytes and iter_files readers; meta-test asserting <=2 opens per file during run-all. Before implementing, run pstats.print_callers on _io.open to attribute opens by gate (not done here; the 363s repo_hygiene figure may include cold-cache and concurrent-load noise).
- AST-checkable: False; FP risk: med
- Disposition: OPEN

## 35-F4 (P2) budget="warn" lets repo_hygiene (363s vs 30s) and standard_stream_restore (39.8s vs 30s) pass the self gate with exit 0

- Evidence: selfgate35.out lines 1-2 'BUDGET repo_hygiene took 363.5s, over its 30s budget'; pyproject.toml:184 budget = "warn"; run-all returned RC 0.
- Failure scenario: A 10x slowdown of a gate (an uncached walk added) never fails pre-push; only a human reading stderr notices.
- Proposed fix: budget="fail" in the self-ci job with budget_s calibrated per gate (e.g. 3x median), keep warn locally; or ratchet per-gate seconds via baseline_trend.
- AST-checkable: False; FP risk: low
- Disposition: OPEN

## 35-F5 (P3) near_duplicate_function_body._candidate_pairs is O(n^2) over functions

- Evidence: cProfile: near_duplicate_function_body.py:209 _candidate_pairs 1.70s self / 5.73s cum; scan_near_duplicate_function_body 11.6s cumulative on pyutilz/src; difflib find_longest_match 196 calls 1.7s.
- Failure scenario: Quadratic in function count: a bigger consumer takes minutes in one scanner, and parallel mode cannot split a single scanner.
- Proposed fix: Bucket by (size band, normalised-token hash/minhash) and compare only within buckets.
- AST-checkable: False; FP risk: med
- Disposition: OPEN

## 35-F6 (P3) No self meta-test for public API quality: 20 of 1018 public module-level functions not fully annotated, 68 lack docstrings, 194 public modules lack __all__

- Evidence: scratchpad props35.py over src/py_ci_shared (358 files): unannotated e.g. black_filtered_apply.discover_py_files, run_black_stdin, is_all_blank, looks_like_import_or_call_list; 68 public functions without docstring; 194 public modules without __all__. grep of tests/ found no repo-wide test of these (only per-gate tests).
- Failure scenario: Consumers import un-annotated functions that mypy treats as Any; star-imports leak helpers.
- Proposed fix: tests/test_self_api_hygiene.py with shrink-only baselines (20 / 68 / 194), prototype in props35.py.
- AST-checkable: True; FP risk: low
- Disposition: OPEN

## 35-F7 (P3) print() in non-CLI library modules: 14 calls in arb_checks, baseline_ratchet, import_layering, sql_verify (not cli=true in registry)

- Evidence: props35.py: 55 print() calls without T201 noqa / file=; excluding modules with registry cli=true leaves 14 in arb_checks.py, baseline_ratchet.py, import_layering.py, sql_verify.py. pyproject.toml:156-165 lists T201 per-file ignores for CLI modules only.
- Failure scenario: A library assert_* function that prints pollutes consumer pytest output and cannot be silenced.
- Proposed fix: Self meta-test: print() only in registry cli=true modules or inside main()/_main(); baseline 14.
- AST-checkable: True; FP risk: med
- Disposition: OPEN

## 35-F8 (P3) No self meta-test on module-level call statements: 113 top-level Expr(Call) statements in library modules

- Evidence: props35.py: 113 module-level call statements excluding append/add/update/setdefault/register/extend (e.g. registry.py:155-159, ci_install_covers_conftest.py:201). test_import_side_effects.py and test_import_time_global_side_effects.py target consumer code and canaries, not this repo's own modules.
- Failure scenario: A top-level statement doing I/O or env mutation would run in every consumer that imports a gate.
- Proposed fix: AST meta-test over src/py_ci_shared: module-body Expr(Call) only from an allow-list (registration helpers); reviewed shrink-only baseline.
- AST-checkable: True; FP risk: high
- Disposition: OPEN

## 35-F9 (P3) Canary exemptions: 35 of 179 gates have no tests/canary/<name>/ directory; staleness of EXEMPT entries is not asserted

- Evidence: props35.py: gates 179, canary dirs 144, missing 35 (arb_checks, audit_round_format, baseline_hygiene, constant_relations, deferred_drift, repo_hygiene, version_consistency, workflow_runner_labels, ...). tests/test_gate_teeth.py:775 enforces canary-or-EXEMPT, so this is by design.
- Failure scenario: An EXEMPT gate that later gains a corpus scan (scan_python/iter_files) keeps its exemption unnoticed.
- Proposed fix: Test that every EXEMPT key names a registry gate and none of them satisfies _core.gate_contract.scans_a_corpus.
- AST-checkable: True; FP risk: low
- Disposition: OPEN

## 35-F10 (P3) Verified already satisfied (no action): bare except 0, registry sorted/unique, registry vs README 0 absent, registry vs tests 0 missing, cli=true without main 0, registry entries naming absent functions 0

- Evidence: props35.py: bare except 0; registry 219 entries sorted True, dups 0; names absent from README 0; gates without test file 0; cli=true without def main []; absent entry functions 0. Existing guards: test_gate_entry_contract.py (min_files/allow_unparsed), test_gate_teeth.py, test_package_inventory.py, test_docs_inventory_parity.py. tests/baselines/*.json are keyed dicts (5 empty), so a sorted/dedup assertion has nothing to check today.
- Failure scenario: n/a
- Proposed fix: Optionally pin ruff E722 explicitly in select.
- AST-checkable: True; FP risk: low
- Disposition: OPEN
