# 44 test quality: round 2026-11-16 (T)

Base commit 48e49b1. Read-only. Scratch: `C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\audit_T\44\`.

## Scope and method

- Read `audits/2026-11-11/44_test_quality.md` and TRACKER rows 44-* (44-S1/S2/R1/R2 RESOLVED, 44-F1 DEFERRED: 55 canaries pin only the default token; not re-proposed).
- AST scan of all 427 files under `tests/` (`scan.py`, `loops.py` in the scratch directory): tests with no assertion, `assert` inside a `for` over a dynamic iterable without a non-empty guard,
  `assert <const>`, `or True`, `pytest.raises(<broad>)` without `match`. Result: 0 `or True`, 0 constant asserts in test bodies, 10 tests without an `assert`
  (all checked: 7 delegate to a helper that asserts or are "must not raise" tests), 28 `for`-loops over dynamic iterables (all checked below), 29 broad `raises` without `match`
  (the 139 hits spelled `Exception` are `pytest.skip.Exception` / `pytest.fail.Exception`, which are specific).
- Grep sweeps for the clock, the network, `sys.platform`, `os.chdir`, `sys.modules` writes, `skipif`/`importorskip`.
- Read in full or in large part (more than 25 files): test_adoption_sha_ref, test_tracked_secret_shapes, test_baseline_ratchet, test_deletion_gates, test_gate_teeth (+ canary EXEMPT table),
  test_release_listing_pages, test_release_local (part), test_ci_green_client_error, test_security_supply_chain_workflows, test_reusable_workflows (part), test_workflow_install_commands,
  test_diagnostics_exit_codes, test_meta_test_utils_todo_hygiene, test_embedded_postgres (part), test_cli (part), test_external_fact_tables (clock), test_ci_health (clock),
  test_audit_20261020_subprocess, test_self_api_hygiene (part), test_repo_hygiene (part), test_naive_utcnow, test_timezone_honest, test_uncalled_functions, test_mypy_gate, test_vulture_warn, test_mutation_rerun (part).
- Timing: see "Slow tests".
- Mutation: see 44-T1. Module chosen: `adoption_sha_ref.py` (70 lines, supply-chain pin check, 4 tests). The run was started with a workaround driver and had not returned when this report was written (see "Not covered").

## 44-T1 (P2) The repo's mutation tooling cannot run on the repo's own modules from a checkout
- **Evidence:** `python -m py_ci_shared.mutation_rerun --repo . sweep src/py_ci_shared/adoption_sha_ref.py tests/test_adoption_sha_ref.py ck.json` fails. With the installed copy: `No module named py_ci_shared.mutation_rerun` (site-packages holds a stale 1.23.9, no editable install). With `PYTHONPATH=src`, or a snapshot copy of `src` on `sys.path`: `MutationHarnessError: the tests import src/py_ci_shared/adoption_sha_ref.py from outside the mutation sandbox ...`. Cause: `_mutation_runner.py:86-88` exports the parent's `sys.path` to the worker, which starts as `-m py_ci_shared._mutation_worker` (`_mutation_runner.py:551`), so `py_ci_shared` loads from the parent's copy, not the sandbox's. `use_warm_worker=False` fails the same way (`_verify_import_origin`, `_mutation_sweep.py:307`). README "Per-module mutation testing" does not say how to mutate py_ci_shared with itself; no survivors journal exists. The 2026-11-11 report ran no mutation.
- **Workaround used:** a driver imports the tool from a snapshot copy, removes that path from `sys.path`, sets `PYTHONPATH=src` (relative, resolved against the sandbox cwd).
- **Impact:** the gates' own tests are the one code base the shipped mutation tool cannot check, so "proven by mutation" claims for py_ci_shared are manual.
- **Proposed fix:** `_mutation_runner._pytest_env`: when the target is inside the package hosting the worker, put the sandbox `src` first in the child's `PYTHONPATH`; document it in the README section. ADDITIVE.
- **Disposition:** FIXED (test and change in this round's commit; see changelog Unreleased)

## 44-T2 (P3) Workflow-security tests glob `*.yml` only
- **Evidence:** `tests/test_reusable_workflows.py:16`, `test_security_supply_chain_workflows.py:92`, `test_pinned_tool_versions.py:122`, `test_workflow_install_commands.py:14`, `test_release_version.py:481`, `test_self_gates.py:61` use `.glob("*.yml")`. GitHub also runs `*.yaml` workflows; no test fails when `.github/workflows/x.yaml` appears. Such a file escapes the SHA-pin, moving-label, install-command and `RELEASE_TOKEN` checks.
- **Impact:** latent hole in checks that guard release and supply chain.
- **Proposed fix:** a test asserting no `.github/workflows/*.yaml`, or glob `*.y*ml`. ADDITIVE.
- **Disposition:** FIXED (test and change in this round's commit; see changelog Unreleased)

## 44-T3 (P3) `test_meta_test_utils_todo_hygiene.py` tests another repository's source
- **Evidence:** lines 18-34: `PYUTILZ_DIR = Path(pyutilz.__file__).resolve().parent`; `test_every_todo_marker_has_attribution` fails whenever pyutilz gains a bare TODO. Locally pyutilz is an editable install of a moving working tree; in CI it is the pinned SHA. The scanner is covered by `test_meta_test_utils.py`. The second test (line 56) asserts nothing (audit-exempt).
- **Impact:** false red from pyutilz edits; no signal about py_ci_shared.
- **Proposed fix:** point it at a seeded tmp tree. ADDITIVE (test only).
- **Disposition:** FIXED (test and change in this round's commit; see changelog Unreleased)

## 44-T4 (P3) `pytest.raises` without `match` on a broad exception where the message is the contract
- **Evidence:** `test_release_listing_pages.py:50` (`OSError`; the `monkeypatch.setattr` before it is unused by the call), `test_marker_runner_coverage.py:267` (`ValueError` over five bad expressions), `test_release_local.py:202`, `test_git_changed_lines.py:113`, `test_core_scopes.py:90`, `test_wallclock_python_mode.py:42`, `code_audit/test_registry_invariants.py:62`, `test_atomic_write_staging.py:32`, `test_embedded_postgres.py:80,106`. Sources raise distinct messages (`.github/scripts/release_guard.py:228`, `marker_runner_coverage.py:401-442`).
- **Proposed fix:** add `match=`. ADDITIVE (test only).
- **Disposition:** FIXED (test and change in this round's commit; see changelog Unreleased)

## 44-T5 (P3) `test_format_warn_reports_a_missing_tool_not_findings` asserts its own `sys.exit(0)`
- **Evidence:** `tests/test_diagnostics_exit_codes.py:111-115`: `main()` returns None, then the test calls `sys.exit(0)` inside `pytest.raises(SystemExit)` and asserts `code == 0`; the code under test never produces that 0. It still catches `main()` raising.
- **Proposed fix:** call `main()` and assert it returns. ADDITIVE.
- **Disposition:** FIXED (test and change in this round's commit; see changelog Unreleased)

## 44-T6 (P3) pandas is required by a module-level `importorskip` but is not in the `dev` extra
- **Evidence:** `tests/test_estimator_invariants.py:8`; the `dev` extra lists numpy, not pandas; it arrives only through pyutilz. `self-ci.yml:132` prints the skip count but does not fail on it.
- **Proposed fix:** add `pandas` to the dev extra. ADDITIVE.
- **Disposition:** FIXED (test and change in this round's commit; see changelog Unreleased)

## 44-T7 (P2) adoption_sha_ref: `main()` and the multi-pin loop are untested; 26 of 47 mutants survive
- **Evidence:** mutation sweep above. `tests/test_adoption_sha_ref.py` never calls `main` or runs the module. `.github/workflows/consumer-pins.yml:72` runs `python -m py_ci_shared.adoption_sha_ref --repos-file ... --resolve-in .`, and consumers read its `::warning title=uses-sha-without-ref-input::` annotations. Surviving: deleting both `sys.stdout.write` calls, emptying the annotation title, `required=True` -> `False`, swapping the arguments of `scan_repos`, `__main__` -> `""`. Line 37 `continue` -> `break` survives because no test has an exempt pin followed by a reportable pin in one file (the break would hide the second). `+` -> `*` in `_USES_SHA` survives because no test has an empty owner.
- **Impact:** the gate could print nothing, or mislabel its annotation, or stop at the first release-tag pin and miss a later between-release pin, and the suite stays green. The gate is report-only, which limits the harm to missed warnings.
- **Proposed fix:** tests: (a) `main([...])` over a tmp `repos.toml` and a tmp git checkout with a tag, asserting the annotation line and the count line and return 0; (b) a workflow with a release pin on line 3 and a between-release pin on line 6, expecting only line 6; (c) `uses: /py-ci-shared/...` expecting no report. ADDITIVE.
- **Disposition:** FIXED (test and change in this round's commit; see changelog Unreleased)

## Slow tests
Targeted run over 20 release/security/gate files (473 tests, all passed, 513 s on a loaded machine, `-p no:randomly`). Slowest: `test_hook_attestation.py::TestEndToEnd` tests 7-16 s each (real git repos), `test_reusable_workflows.py` fetch-retry tests 11-14 s (real retry delays), `test_release_version.py::test_cli_refuses_to_move_v1_for_a_back_port_tag` 13 s. No test is a pure sleep; the cost is real git/subprocess work. Not raised: no wrong-result risk, and CLAUDE.md already puts the rest of the suite in the release script.

## Mutation
`adoption_sha_ref.py` with the tests in `tests/test_adoption_sha_ref.py`, using the workaround driver of 44-T1 (one module, cold worker): `RUN mutants=47 killed=21 survived=26`. Survivors: lines 56-69, all of `main()` (argument parser, both `required=True`, the `::warning title=uses-sha-without-ref-input::` annotation, the summary line, the `return 0`, the `__main__` guard); line 37 `continue` -> `break`; line 26 `` removed and `+` -> `*` in `_USES_SHA`; line 27 `` removed in `_REF_INPUT`. See 44-T7.

## Not covered
- Mutation on any module other than `adoption_sha_ref.py`. Remaining ~390 test files were only AST-scanned.
- Order dependence: pytest-randomly is installed here; `sys.modules` writes in tests (`test_ci_green_client_error.py:19`, `test_checkout_resolution.py`) are leaks of harmless modules, not rejected or raised.

## Considered and not raised
- 139 `pytest.raises(...Exception)` hits: they are `pytest.skip.Exception`/`pytest.fail.Exception`.
- `assert True` hits: fixture strings written to tmp files.
- Network: no test opens a connection to the outside; `ls-remote` hits are argument-shape tests. Clock: `test_external_fact_tables`, `test_ci_health` inject `today`/`now`.
- `test_install_command_inputs_never_interpolated_into_run` covers 3 of 5 files, but `test_workflow_install_commands.py` scans all `*.yml`.
- 28 `for`-loop asserts over dynamic iterables: checked the security ones (`test_reusable_workflows.py:78,409`, `test_security_supply_chain_workflows.py:87`); floors or equality assertions follow.
- `test_audit_20261020_subprocess.py:17` asserts an env var the module sets with `setdefault`: weak but not wrong.

| id | sev | title |
|---|---|---|
| 44-T1 | P2 | shipped mutation tool cannot mutate py_ci_shared itself |
| 44-T2 | P3 | workflow tests glob *.yml only |
| 44-T3 | P3 | todo-hygiene test scans pyutilz |
| 44-T4 | P3 | broad raises without match |
| 44-T5 | P3 | test asserts its own sys.exit(0) |
| 44-T6 | P3 | pandas not in dev extra |
| 44-T7 | P2 | adoption_sha_ref: main() and the multi-pin loop untested (26 of 47 mutants survive) |

