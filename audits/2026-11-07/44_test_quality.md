# 44_test_quality

Round 2026-11-07. Every finding is OPEN until its Disposition line says otherwise.

Scope and results. Modules mutated by hand: 10 (deferred_drift, cli_flag_spelling, wallclock_test_waits, entry_points_resolvable, skip_as_pass,
definition_after_entry_guard, cache_key_sensitivity, advisory_warn, tool_versions, install_safe_hook), 21 mutations, run against a copy of
src/ and tests/ in the scratchpad (tests/conftest.py puts the repo's own src first on sys.path, so PYTHONPATH alone would not redirect the import), the module's own
test file only, `-p no:randomly -x`. 14 killed, 7 survived: 4 real test gaps (44-F3, F4, F5, F8) and 3 equivalent (cli_flag_spelling `len(counts) < 2`, two
tool_versions constants). Nothing in the repository was edited; the mutation harness was not run.

Other checks, no defect found:
- Environment and order: test_cli.py, test_worker_budget.py and test_advisory_warn.py run from an unrelated cwd (--rootdir at the repo), with and without pytest-randomly: all pass (38, 20, 4 tests).
  test_worker_budget.py also passes with PY_CI_WORKERS=7 exported (it sets and clears the variable itself).
- Canaries: tests/test_gate_teeth.py run from an unrelated cwd, 959 passed in 210 s: every violation canary is reported, every clean canary stays clean, BOM, unparsable and empty variants behave,
  for all 164 gates (so for any five). See 44-F1 for how strong the violation check is.
- Slowest tests: only test_gate_teeth.py was timed (slowest ten 1.8 s to 4.4 s, timezone_honest-bom and committed_line_endings-bom first). A suite-wide durations run was not made (not allowed).
  Real time.sleep appears in tests only inside child-process scripts that are killed (test_mutation_output_bounded.py:194 sleep 60, test_mutation_teeth_regressions.py:835 sleep 120,
  test_resource_leak_guard.py:194 sleep 4, test_script_entry_points.py:193 sleep 30) or as data strings for the wait gates; no test waits on the wall clock itself.
- AST scan (tests/test_*.py, 5133 test functions): 9 flagged, none is `assert True` or an assert on a mock's own return (script: scratchpad audit44/ast_scan.py).

## 44-F1 (P2) 137 of 164 canaries pin only the default token 'seed': a gate that loses a whole detection branch still passes tests/test_gate_teeth.py

- Evidence: tests/test_gate_teeth.py:56 (token default 'seed', tokens=() default). AST count over the 164 Canary(...) calls: 137 pass no tokens=, so the violation case asserts only that the failure text contains 'seed' (the seeded file name). Demonstration: in a scratchpad copy I made skip_as_pass._python_block_exits_zero return False (the gate lost its whole Python half). `pytest tests/test_gate_teeth.py -k skip_as_pass` -> 4 passed (canary/skip_as_pass/violation holds one file, tool/seed_docker.sh.canary, shell only); the gate's own tests/test_skip_as_pass.py -> 3 failed. Largest single-token violation corpora: validation_errors_echo_input (39 lines), fail_open_handlers (33), ci_install_covers_conftest (31), stub_signature_parity (29).
- Failure scenario: A refactor drops one branch of a gate (the Python path, one comparison form, one rule id). The canary still reports the first shape, the file name is in the message, teeth passes. Only the gate's own unit tests stand between that and a silent false negative in every consumer; a gate with thin own tests has no net.
- Proposed fix: Give every canary with more than one detection branch a tokens= tuple naming one rule id or message fragment per seeded shape, and make test_canary_fixture_has_teeth fail when the violation corpus holds fewer seeded shapes than the gate has rule ids. Start with the ten largest violation corpora.
- AST-checkable: no. FP risk: low: adding tokens cannot fail a gate that still reports every shape.
- Disposition: PARTIAL: RESOLVED 648caff for the highest-value subset. skip_as_pass (shell and Python halves) and wallclock_test_waits (time.sleep and async) canaries now seed both branches and pin each by file name via tokens=; proven: making _python_block_exits_zero return False now fails 2 teeth cases (was 4 passed). New ratchet test_default_token_only_canaries_never_grow (77 canaries pinned only 'seed' by Canary.token at that point; the ceiling is now 71). DEFERRED: the remaining 71 canaries. Second pass, commit "Gate teeth: per-rule canary tokens for 14 gates, both ratchet ceilings lowered": converted 6 thin canaries (validation_errors_echo_input, stub_signature_parity, statement_columns_exist_in_ddl, destructive_tests_throwaway_only, free_text_in_get_route, cuda_kernel_pointer_types) and gave 8 already-custom canaries per-rule tokens (fail_open_handlers 5 rules, ci_install_covers_conftest 3, git_dependency_pins, machine_specific_paths 4, optional_truthiness, no_source_text_tests, naive_utcnow, swallowed_exceptions), adding the missing violation shapes to the fail_open_handlers, validation_errors_echo_input, ci_install_covers_conftest, git_dependency_pins, machine_specific_paths and destructive_tests_throwaway_only corpora. Ceiling lowered 77 -> 71. Teeth proven on a scratch copy of src/ (8 teeth cases failed): fail_open_handlers with the quiet_substitution branch disabled, validation_errors_echo_input with the unconditional-handler message removed, destructive_tests_throwaway_only with destructive-test-never-run disabled, machine_specific_paths with the db-url rule removed. Not reachable from a canary: cuda-bare-float-pointer (needs include_float, not passed by the runner) and destructive-test-allowlist-stale (needs an allowlist). Gates with one rule id for all shapes (validation_errors_echo_input, stub_signature_parity, free_text_in_get_route) are pinned by message shape, since the rule id does not distinguish branches. Plan for the rest: one tokens= per rule id, largest corpora first, each lowering the ceiling. Note: the audit's 137 counted Canary() calls without tokens=; 60 of those already pin a custom token.

## 44-F2 (P2) test_a_pattern_that_would_not_compile_is_not_emitted is vacuous: it loops over mutants and asserts nothing when no 'regex widened' mutant exists

- Evidence: tests/test_mutation_teeth.py:854-863: `for mutant in ...: if 'regex widened' in mutant.description: re.compile(pattern)`; no assertion that any such mutant was produced. Demonstration in the scratchpad copy: renamed the label at src/py_ci_shared/_mutation_operators.py:484 to 'regex WIDE' (the operator still runs, the test's filter no longer matches); `pytest tests/test_mutation_teeth.py -k would_not_compile` -> 1 passed. Same shape at tests/test_mutation_teeth.py:1056-1063 (test_a_star_in_a_signature_is_still_parsed_and_rejected: `for mutant in generate_mutants(src)[0]: ast.parse(...)`, no count).
- Failure scenario: The regex-widening operator is renamed, reordered or stops firing; the test guarding 'never emit an uncompilable pattern' keeps passing on zero mutants, and a broken pattern later reaches every survivor list as a permanent unkillable entry.
- Proposed fix: Collect the filtered mutants into a list, assert it is non-empty (the anchored case must produce one), then compile each. Same for the signature test.
- AST-checkable: yes. FP risk: low: the repo already has a vacuous_loop_assertions gate; extend it to for-loops whose only body is a filter plus a bare call.
- Disposition: RESOLVED 648caff (+d9ae01e): both loops now collect the mutants and assert non-empty before checking; proven: renaming the label to 'regex WIDE' fails the would_not_compile test. The star-signature test exposed that def f(*args) yields no mutant at all, so its source was changed to one that does.

## 44-F3 (P2) wallclock_test_waits: the async-sleep regex (text mode) has no test; changing its zero-argument exemption survives

- Evidence: Hand mutation in the work copy: src/py_ci_shared/wallclock_test_waits.py:42 `(?!\s*0?\s*\))` -> `(?!\s*1?\s*\))`, ran tests/test_wallclock_test_waits.py -> 18 passed (SURVIVED). tests/test_wallclock_test_waits.py:91-92 exercises asyncio/trio.sleep only through the AST path; text mode (the fallback for an unparsable file) is exercised only with time.sleep (line 108) and Future.delayed.
- Failure scenario: An unparsable test file with `await asyncio.sleep(5)` is the case text mode exists for. If the pattern regresses (exempts sleep(1), or stops matching anyio) the gate reports the file clean while the same wait in a parsable file is reported.
- Proposed fix: Add a text-mode parametrized case: asyncio.sleep(1), trio.sleep(2), anyio.sleep(0.1) are findings; asyncio.sleep(0) and asyncio.sleep() are not, in a file that does not parse.
- AST-checkable: no. FP risk: none.
- Disposition: RESOLVED 648caff (+d9ae01e): text-mode parametrized cases for asyncio/trio/anyio sleeps and the zero/no-arg exemptions in an unparsable file; proven: the (?!\s*1?\s*\)) mutant fails 3 tests.

## 44-F4 (P2) skip_as_pass: the 'same-indent return/raise ends the block' rule is untested; flipping == to != survives

- Evidence: Mutation src/py_ci_shared/skip_as_pass.py:78 `indent_width(line) == depth and _PY_BLOCK_END.match(line)` -> `!= depth and`, ran tests/test_skip_as_pass.py -> 17 passed (SURVIVED). The sibling mutation on line 74, `<` -> `<=`, was KILLED.
- Failure scenario: `print('SKIPPED'); return` followed later at the same indent by sys.exit(0) must not be reported. With the rule inverted, a nested return ends the block and a same-indent return no longer does, so honest skip-then-return code is reported (a false positive needing a baseline entry) and a skip followed by a nested exit is missed.
- Proposed fix: Add two Python cases to tests/test_skip_as_pass.py: a same-indent `return` after the print followed by sys.exit(0) is clean; a `return` nested one level deeper, then exit(0) at the print's indent, is a finding.
- AST-checkable: no. FP risk: none.
- Disposition: RESOLVED 648caff (+d9ae01e): same-indent return and deeper-return cases; proven: == to != fails 2 tests.

## 44-F5 (P2) advisory_warn: the 'findings are WARNINGS ONLY' banner and the warned flag are untested; inverting the ruff return-code test survives

- Evidence: Mutation src/py_ci_shared/advisory_warn.py:52 `.returncode != 0` -> `.returncode == 0`, ran tests/test_advisory_warn.py -> 4 passed (SURVIVED). The tests (lines 30-53) assert only the call list and main() == 0; none captures stderr or fakes a non-zero tool exit. The same inversion exists at line 61 (pip-audit) and in the `if warned:` print at line 66.
- Failure scenario: The hook's only user-visible effect is the banner telling the developer ruff/C90/pip-audit findings are advisory. A regression prints it on clean runs or never, and the hook becomes noise or invisible.
- Proposed fix: Let the calls fixture return a configurable returncode; assert the banner is on stderr when any tool exits non-zero and absent when all exit 0; assert the exit code stays 0 in both.
- AST-checkable: no. FP risk: none.
- Disposition: RESOLVED 648caff (+d9ae01e): exits fixture with per-tool return codes, banner present when ruff or pip-audit exits non-zero, absent otherwise; proven: returncode == 0 mutant fails 2 tests. Line 61 (pip-audit) is covered by the parametrized case.

## 44-F6 (P3) Four tests assert only 'does not raise' for a registration function: test_double_registration_is_a_noop* never check the option is registered once

- Evidence: tests/test_code_audit_meta.py:206, tests/test_content_hash_version_bump_gate.py:184, tests/test_loc_budget.py:158, tests/test_readme_env_var_parity.py:401 (identical body: register twice, no assert). AST scan of 5133 test functions in tests/test_*.py (flag: no assert, no pytest.raises/fail, no assert_*/check*-named helper call, or only a constant-true assert) found 9: these 4 plus tests/test_audit_20261020_subprocess.py:65 (bare call, no raises check), tests/test_meta_test_utils_todo_hygiene.py:56 (audit-exempt, prints a count), tests/test_repo_hygiene.py:185 (compile() of a BOM source), and tests/test_mutation_teeth.py:854 and :1056 (see 44-F2). No `assert True` and no assert on a mock's own return was found.
- Failure scenario: A second registration that silently drops the option, or registers it twice under another name, still passes.
- Proposed fix: After the second call parse the flag and assert it is True and that the option appears once in the parser's actions.
- AST-checkable: yes. FP risk: low.
- Disposition: RESOLVED 648caff for the four double-registration tests (flag parses True and appears exactly once in the parser). The other flagged tests (test_audit_20261020_subprocess.py:65 and the two mutation_teeth ones via 44-F2) are covered or exempt; the AST gate extension for for-loop-only-filter-plus-call stays DEFERRED (needs the vacuous_loop_assertions gate, outside this fix's scope).

## 44-F7 (P3) Several canaries use a weak token that appears in any message about the file or the gate: 'main', 'seed', 'OSError', 'alice', 'limit', 'WHERE', '221'

- Evidence: AST over tests/test_gate_teeth.py Canary(...) entries: git_dependency_pins token='main'; entry_points_resolvable and stdlib_json_ban use the default 'seed'; swallowed_exceptions 'OSError' (backed by its tokens=); machine_specific_paths 'alice'; optional_truthiness 'limit'; no_source_text_tests 'WHERE'; drifted_duplicate_literals '221'; naive_utcnow 'utcnow' and import_cycles 'seedpkg' are the gate name or a path. canary/entry_points_resolvable/violation/pyproject.toml.canary names the project 'seed', so any message echoing the project name carries the token.
- Failure scenario: A gate that fails for a different reason (wrong rule, a parse error echoing the path) is read as biting.
- Proposed fix: Use rule ids ('swallowed-exception', 'unresolved-entry-point') as tokens; in test_canary_fixture_has_teeth reject a token that is a substring of the gate name or of a canary path.
- AST-checkable: yes. FP risk: low.
- Disposition: RESOLVED: commit "Gate teeth: per-rule canary tokens for 14 gates, both ratchet ceilings lowered" replaced all six weak tokens (git_dependency_pins:main -> release-branch, <no ref>, abc1234 and the uv sources table; machine_specific_paths:alice -> the four rule ids; optional_truthiness:limit -> the two optional-number shapes; no_source_text_tests:WHERE -> [source-text-test]; naive_utcnow:utcnow dropped, its per-branch tokens remain; swallowed_exceptions:OSError -> [swallowed-exception]). _WEAK_TOKENS_FROZEN is now empty, so any weak token fails test_weak_canary_tokens_never_grow.

## 44-F8 (P3) cli_flag_spelling: the long-option length boundary is unpinned

- Evidence: Mutation src/py_ci_shared/cli_flag_spelling.py:35 `len(a.value) > 2` -> `> 3` ran tests/test_cli_flag_spelling.py -> 9 passed (SURVIVED); a one-letter long option '--a' is then dropped. Mutation on line 67 `len(counts) < 2` -> `< 1` also survived but is equivalent (one spelling leaves nothing to report), so it is not counted as a defect.
- Failure scenario: A one-character long option (for example '--n' vs '--N') spelled differently across scripts is not reported.
- Proposed fix: Add a case with '--n' in one script and '--N' in two others.
- AST-checkable: no. FP risk: none.
- Disposition: RESOLVED 648caff (+d9ae01e): the audit's example does not work (the gate splits only on - versus _, never on case, so --n/--N never reports). The boundary is pinned with --_ x2 against --- x1, the shortest splittable stem; proven: > 3 mutant fails 1 test.

## 44-F9 (P3) tool_versions: a bump of a pinned constant is invisible to its own test file, and the two-week-age rule in its comment has no test (equivalent mutation, noted for scope)

- Evidence: Mutations BLACK_VERSION 26.5.1 -> 26.5.2 and ZIZMOR_VERSION 1.30.1 -> 1.30.2 in src/py_ci_shared/tool_versions.py: tests/test_tool_versions.py 3 passed (SURVIVED). The constants are checked only for consistency with workflows in tests/test_pinned_tool_versions.py:176-185 and tests/test_reusable_workflows.py:268, so a consistent bump is legitimate (equivalent mutation).
- Failure scenario: A tool version younger than two weeks (the supply-chain rule in the source comment) is pinned without any failing check.
- Proposed fix: Record the release date beside each constant and test its age against the commit date, or document that the rule is review-only.
- AST-checkable: no. FP risk: medium: needs a date source.
- Disposition: WON'T FIX: the mutations are equivalent (a consistent bump is legitimate and is checked against the workflows by test_pinned_tool_versions.py). The two-week age rule is review-only; enforcing it needs a release-date source per constant and a commit-date input, a medium false-positive risk for a rule a human applies when bumping.

