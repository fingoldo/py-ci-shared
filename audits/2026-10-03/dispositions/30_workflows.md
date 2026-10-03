# Dispositions: round 2026-10-03 / 30 (workflows, docs, own tests) plus 10 / N-6, N-7, N-13, N-14

### WF-1

**Disposition:** RESOLVED -- lint-advisory.yml "pip-audit dependency vulnerability scan" now audits the calling project
(`.`, or `-r` per file of the new `pip-audit-requirements` input). Reproduced on a scratch project pinning
`requests==2.0.0`: the old command audited pip-audit's own 28-package venv ("No known vulnerabilities"); the new step
script, run as-is, reports `requests 2.0.0`, 12 vulnerabilities. Tests:
tests/test_reusable_workflows.py::test_pip_audit_audits_the_calling_project_by_default,
::test_pip_audit_audits_the_given_requirements_files (execute the step with a recording `uvx`).

### WF-2

**Disposition:** RESOLVED -- release.yml publish moves v1 first, skips the move when v1 already points at the tag, and
creates the release only when `gh release view` finds none, so "Re-run failed jobs" completes. Test:
tests/test_release_version.py::test_release_publish_is_rerunnable_and_moves_v1_before_the_release_page.

### WF-3

**Disposition:** RESOLVED -- version 1.20.0 (pyproject.toml, `__init__.__version__`, three workflow defaults).
tests/test_release_version.py::test_a_released_version_is_declared_only_by_its_own_tagged_commit fails when
`v{VERSION}` is tagged and HEAD is not that commit (rule in .github/scripts/release_guard.py `version_problem`, tested by
::test_version_problem and ::test_version_problem_on_a_scratch_repository); ::test_every_registry_since_is_a_release_up_to_the_declared_version
holds `since <= version`. On the old tree (1.19.0, HEAD past v1.19.0) the new test fails.

### WF-4

**Disposition:** RESOLVED -- ruff-blocking.yml, lint-advisory.yml ("Resolve PY_CI_SHARED_DIR") and black-filtered.yml
("Install py-ci-shared") retry the pinned ref 3 times, then `exit 1`; the master fallback is gone. lint-blocking and
mypy-* never fetched py-ci-shared (checked: no fetch step). Tests (execute the real step scripts with fake git/uv):
tests/test_reusable_workflows.py::test_a_pinned_ref_that_cannot_be_fetched_fails_after_retries_and_never_fetches_master,
::test_a_transient_fetch_failure_is_retried_and_succeeds, ::test_black_filtered_fails_when_the_pinned_ref_cannot_be_installed,
::test_py_ci_shared_is_fetched_only_at_the_pinned_ref_and_never_at_master. All fail against the old workflows.

### WF-5

**Disposition:** RESOLVED -- new input `force-remote-fetch` on the three workflows; self-ci.yml gains
integration-ruff-blocking-remote-fetch, integration-black-filtered-remote-install, integration-lint-advisory-remote-fetch
calling them at `github.event.pull_request.head.sha || github.sha`. The failure path is covered by the executed step
tests above. Tests: ::test_self_ci_runs_the_consumer_fetch_path_of_each_self_fetching_workflow,
::test_inside_this_repo_the_local_checkout_is_used_unless_a_remote_fetch_is_forced. First real proof is the next self-ci run.

### WF-6

**Disposition:** RESOLVED -- verify waits (up to 30 min) for a successful self-ci.yml run of the tagged SHA
(`release_guard.py ci-green`) and runs `python -m pytest tests/ -ra`, the whole suite. README/CLAUDE.md wording corrected.
Tests: tests/test_release_version.py::test_release_verify_requires_the_newest_tag_green_self_ci_and_the_whole_suite,
::test_ci_verdict, ::test_wait_for_ci_polls_until_the_run_finishes, ::test_wait_for_ci_gives_up_as_pending_after_the_wait.

### WF-7

**Disposition:** RESOLVED -- release.yml `workflow_dispatch` input `rollback-to` (job `rollback`, target validated by
`release_guard.py rollback`); procedure in CLAUDE.md "Rolling back a release" and README "Pinning and releases". Tests:
::test_release_rollback_is_a_dispatch_through_the_guard, ::test_cli_rollback_accepts_an_earlier_release_and_refuses_an_unknown_one,
::test_the_rollback_procedure_is_documented.

### WF-8

**Disposition:** RESOLVED -- README "Pinning and releases" and the PY_CI_SHARED_DIR paragraph describe the
`py-ci-shared-ref` input, its default, the SHA-pin caveat and the no-fallback failure; `job_workflow_sha` no longer appears.

### WF-9

**Disposition:** RESOLVED (code) / OWNER STEP -- publish and rollback run in `environment: release` (auto-created on
first run; zizmor auditor no longer reports secrets-outside-env for RELEASE_TOKEN). Owner must, in repository settings:
add a deployment rule to `release` (v* tags + default branch), move RELEASE_TOKEN into it, optionally a tag ruleset.
Documented in CLAUDE.md "Versions and releases". Test: ::test_release_publish_is_rerunnable_and_moves_v1_before_the_release_page (asserts the environment).

### WF-10

**Disposition:** RESOLVED -- README:11 lists the real blocking and advisory bundles; install tags v1.17.0 -> v1.19.0;
release.yml in "Deliberately NOT here" explained as this repo's own; "runs the test suite" now true (WF-6). Test:
tests/test_release_version.py::test_the_readme_install_tag_is_the_newest_release_or_this_one.

### WF-11

**Disposition:** RESOLVED (partly) -- canaries added for 7 of the 18: ci_workflow_paths, ci_test_dir_reachability,
coverage_config_parity, gate_config_honesty, pytest_addopts_path_runs, marker_runner_coverage, gate_integrity
(tests/canary/<gate>/, `CANARIES.update` in tests/test_gate_teeth.py, EXEMPT entries removed; 466 teeth tests pass).
Not done in this round, EXEMPT kept: hook_hygiene, test_partition_reachability, doc_identifier_parity,
phantom_code_references, effect_assertion_parity, sql_verifier_coverage, audit_path_references,
audit_disposition_parity, disposition_test_references, timezone_honest, import_layering. Side observations: gate_integrity
raises a bare yaml ParserError without the file name on an unparsable pre-commit config; pytest_addopts_path_runs loses
the clean file's markers when another test file is unparsable (reports a spurious path-run finding next to the parse error).

### WF-12

**Disposition:** RESOLVED -- root cause: pytest-timeout (`timeout = 300`, thread method on Windows) calls `os._exit` on
the xdist worker when a test passes 300 s. Measured: TestConcurrencyChangesSpeedAndNothingElse 282 s under -n 4 (256 s
alone); reproduced the exact "node down: Not properly terminated / replacing crashed worker" with `--timeout=20`.
Fix: `@pytest.mark.timeout(1200)` on that class and on test_scaffold's nested-pytest test; test_mutation_worker uses
WARM_TIMEOUT = 600 with a 1200 s module mark. Same `--timeout=20 -n 4` run after the fix: 17 passed, no crash. Tests:
tests/test_mutation_teeth.py::test_the_worker_pool_tests_carry_a_budget_above_the_global_timeout,
tests/test_mutation_worker.py::test_the_warm_runner_tests_outlast_their_worker_budget.

### WF-13

**Disposition:** WON'T FIX -- measured serially, no xdist, this workstation: four_workers 256 s, survivors 91 s,
test_refresh_writes_a_missing_baseline 54 s; CI legs take 99-258 s for the whole suite. The cost is Windows interpreter
start per nested pytest/mutant run, which is what these end-to-end tests exist to exercise; WF-12 removes the failure mode.

### WF-14

**Disposition:** RESOLVED -- config-drift-check.yml concurrency; corpus-drift baseline filtered `branch=master` and the
newest run with a non-expired artifact; black-filtered reads BLACK_VERSION from tool_versions; scheduled reports and
release install with `-c .github/constraints/runtime.txt` (exact PyYAML/tomli/pytest). windows-latest/macos-latest kept
on purpose (comment in self-ci.yml; images 2026-10-02: windows-2025-vs2026, macos-26-arm64) and allowlisted. Tests:
tests/test_reusable_workflows.py::test_every_scheduled_workflow_has_a_concurrency_group, ::test_corpus_drift_takes_its_baseline_from_master_and_skips_expired_artifacts,
::test_black_filtered_reads_the_black_version_from_tool_versions, ::test_the_constraints_file_pins_every_runtime_dependency_exactly,
::test_scheduled_reports_and_the_release_install_with_the_constraints, ::test_moving_runner_labels_are_only_the_reviewed_ones.

### WF-15

**Disposition:** RESOLVED (partly) -- tests/test_reusable_workflows.py loop replaced by
::test_py_ci_shared_is_fetched_only_at_the_pinned_ref_and_never_at_master (asserts the exact set of fetching steps).
`find_floorless_loops` over tests/ now reports 2: tests/test_ci_install_covers_conftest.py (owned by the gate agent,
not edited here) and tests/test_package_inventory.py (audit: false positive, 147 entries). Dogfooding
vacuous_loop_assertions in [tool.py_ci_shared] left to the pyproject owner once the first is fixed.

### WF-16

**Disposition:** NOT A DEFECT -- probe (scratchpad dt_probe.py): stdout swapped and displayhook no-op before
`doctest.testmod` -> 0 failed; module state cleared so the function returns None -> 1 failed, "Got nothing". The only
stream swaps in src are in _mutation_worker's subprocess `main()` and docstring/fixture text in standard_stream_restore.

### N-6

**Disposition:** RESOLVED -- src/py_ci_shared/ci_health.py `assess_workflow` (line ~169) sets billing-refused runs aside;
`billing` only when nothing else is red since the last success; `_billing_refusals` probes newest failures until the first
code failure (cap 20, unprobed count as code failures, shown as "older failures not probed"). Real run 2026-10-03:
polyvocab_app/CI and flutter_app_core/CI were "billing" and are now RED (code failures from 09-02). Tests:
tests/test_ci_health.py::test_a_billing_refusal_on_top_of_a_code_failure_streak_is_still_red and 6 more.

### N-7

**Disposition:** RESOLVED -- `main` exits 2 when `--only` names an unknown consumer or the list is empty. Tests:
::test_only_with_an_unknown_consumer_fails_without_a_request, ::test_only_with_one_known_and_one_unknown_consumer_fails,
::test_a_config_with_no_consumers_fails. Silent 600 s run: profiled 180 sequential `gh api` calls = 419 s, output only
at the end. Now: per-request timeout (`gh` subprocess too), `--deadline` 600 s, 8 concurrent consumers, per-consumer
progress on stderr: same run 103 s. Tests: ::test_main_past_the_deadline_still_reports_and_fails,
::test_gh_api_turns_a_hung_call_into_an_api_error, ::test_consumers_are_read_concurrently, ::test_the_ci_health_workflow_deadline_fits_its_timeout.

### N-13

**Disposition:** RESOLVED -- same as WF-3.

### N-14

**Disposition:** RESOLVED -- verify and publish run `release_guard.py major-move "$TAG"`: refused unless the tag is the
highest release of its major (numeric order). Tests: tests/test_release_version.py::test_major_move_only_for_the_newest_release_of_its_major,
::test_cli_refuses_to_move_v1_for_a_back_port_tag (scratch repo: v1.18.0, v1.19.0, then v1.18.1). Suite and self-ci: WF-6.
