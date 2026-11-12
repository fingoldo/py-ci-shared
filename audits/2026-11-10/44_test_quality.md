# 44_test_quality

Round 2026-11-10. Every finding is OPEN until its Disposition line says otherwise. Read-only audit: nothing under src/ or tests/ was edited.

Scope and method. Sampled across tests/ (488 test files, including tests/code_audit/). Greps for vacuous or type-only assertions, `assert True`, unrestored environment, cwd and sys.modules state, wall-clock or timing assertions, and xfail use. Deep reads of tests/test_env_flag_parsing.py, tests/test_naive_utcnow.py, tests/test_core_fast_paths.py, tests/test_mypy_gate.py, tests/test_version_tag_currency.py, tests/test_cli.py and tests/test_mutation_teeth_regressions.py (lock test). Hand mutations were run in a scratch copy of src/ and tests/ outside the repo (scratchpad `m44`), each run against the module's own test file with `-p no:randomly`. Gate-teeth and mutation-teeth canaries and the 2026-11-07 findings (44-F1..F9 of that round) were not re-checked and are not re-reported.

Checks with no defect found:
- `assert True` / `assert 1 == 1` hits: all are fixture source strings written to tmp_path for scanners to read, not real assertions in the repo's tests.
- Bare-truthy assertions (`assert x` alone): none. `is not None` as the only assertion: the 16 hits are either followed by a content assertion or are fixture strings.
- Flakiness: no real wall-clock comparison in tests (timing-related hits are fixture strings for the timing gates). The one real lock-contention test (tests/test_mutation_teeth_regressions.py:571-585) is deterministic: the holder thread is still inside the with-block while the contender times out.
- Shared state: the environment writes found outside monkeypatch are inside tests/conftest.py (restored in a finally), or fixture strings. The `os.chdir` and `sys.modules` hits are fixture strings.
- Naive-utcnow gate (tests/test_naive_utcnow.py): aware-library exclusion, getattr literal branch and the call-node-once rule each killed by a mutation (3 of 3).
- Env-flag gate: the not-flag, bool(int()) unwrap, bool() and truthiness shapes each killed by a mutation (see 44-F1 for the two survivors).

## 44-R1 (P3) env_flag_parsing: the NotIn membership shape and the case-insensitive comparison are untested; both mutations survive

- Severity: P3 (gate false negative in a guard that is meant to keep hand-parsed boolean switches out of the repo).
- Evidence: src/py_ci_shared/env_flag_parsing.py:146 `right.value.lower() in _BOOL_WORDS` -> `right.value in _BOOL_WORDS` (mutant M2): `pytest tests/test_env_flag_parsing.py` -> 24 passed. src/py_ci_shared/env_flag_parsing.py:148 `isinstance(op, (ast.In, ast.NotIn))` -> `isinstance(op, (ast.In,))` (mutant M4): 24 passed. tests/test_env_flag_parsing.py:14-30 (test_each_hand_parse_shape_is_reported) and :110-115 pin only `in ('1', 'true')` (In, lowercase) and `== '1'` / `== 'yes'` (lowercase). No test has `not in` over a boolean literal set, nor an uppercase value such as `== 'TRUE'`.
- Scenario: A refactor that drops NotIn from the membership branch, or drops the lower() normalisation, lets `os.environ.get('P_X', '').lower() not in ('0', 'false')` and `os.environ.get('P_X') == 'TRUE'` pass the gate. Both are hand-parsed boolean switches that bypass the shared parser, the exact class the gate exists to reject.
- Proposed disposition: OPEN. Add two cases to tests/test_env_flag_parsing.py: `if os.environ.get('P_A', '') not in ('0', 'false'):` is reported with shape "membership in a literal collection", and `if os.environ.get('P_B') == 'TRUE':` is reported with shape "compared with 'TRUE'". Each mutant above should then fail one test. Priority low; no false-positive risk.

## 44-R2 (P3) test_a_stable_old_file_is_served_without_reading_it_again asserts a property the fast path alone provides, and passes with the fast path disabled

- Severity: P3 (a named performance property is untested; the test is green on the regression it names).
- Evidence: tests/test_core_fast_paths.py:30-40. The test name and docstring promise that a repeat request "does not read or hash" the file. The assertions are `again[1] is first[1]` and `not reads`, where `reads` counts `Path.read_bytes` calls. Mutant M8 in src/py_ci_shared/_core/source.py (`stable = _fast_lookup(...)` replaced by `stable = None`, disabling the fast path in parse_source) -> `pytest tests/test_core_fast_paths.py` -> 7 passed. Reason: when the fast path is off, parse_source calls read_bytes_cached, which serves the file from its own process cache (src/py_ci_shared/_core/source.py:74-88) without calling Path.read_bytes, and the parse cache returns the same tree object. So the identity and read-count assertions both still hold, and the blake2b digest is still computed on the cached bytes, which the test does not observe.
- Scenario: Someone removes or breaks `_fast_lookup` (for example its stat-signature check is always None). The parse path still returns correct trees through the lower cache layers, so every test passes, and the per-call hashing and cache-lookup cost comes back unnoticed in the gate suite (which parses the same package for about forty gates).
- Proposed disposition: OPEN. Make the test observe the layer it names: monkeypatch `hashlib.blake2b` (as imported in src/py_ci_shared/_core/source.py) or `read_bytes_cached` in the source module, and assert it is not called on the second request. Then re-run the M8 mutant; it must fail. Also add a sibling assertion that a file modified within the racy window is re-read (the test's own docstring covers that case but the file has no such test).

## Summary

- Findings: 2 (P3: 2; P0-P2: 0).
- Sample: 488 test files; deep reads of 7 files plus greps across the rest. Hand mutations: 8 (M2, M3, M3b, M4, M5, M6, M7, M8), 6 killed, 2 survived (the two findings above).
- Not covered: the full suite was not run (instructions), so the durations and slowest-test check of the 2026-11-07 round was not repeated. Test files outside the sampled set were judged only by grep, so an absence of findings there is not proof of none.
- Notes for the owner: the test-quality culture in this repo is strong (most sampled gates are killed by every mutation tried); the remaining gaps are in the detail branches of gates, not in the core logic.
