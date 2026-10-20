Written for: maintainers of py-ci-shared deciding which corpus gates to enable on this repository.

# Corpus gate triage on py-ci-shared (2026-10-20)

Source: running every `assert_*` corpus gate on this repository with default arguments (`/d/Temp/mypyrepro/prof/run_gates_on_self_v2.py`). Gates that need a config (kwargs) were not run; they are listed as `needs-config` and are not triaged here.

Counts from the run: 45 clean, 28 with findings, 10 needing config to call, 120 needing config, 0 import errors.

Verdicts: **fix** = real defect in this repo, fix in code. **exempt** = deliberate site, documented with an inline marker and reason. **n/a** = gate's domain does not apply to this repo. **pending** = not yet triaged.

## Already configured in `[tool.py_ci_shared]` (findings are not regressions)

- `version_consistency`: configured with `files` and `modules`. The run's "only 1 version source" came from calling it without config.
- `pytest_markers`: configured, passes.
- `comment_repo_paths`: configured with `roots` and `files`, passes. The run's findings used all of `src/` and `tests/` without config.

## Not enabled: applicability

- `stdlib_json_ban` (79 findings, opt-in): **n/a**. py-ci-shared's runtime dependencies are `tomli` and `PyYAML`; it has no orjson dependency and no measured hot path. Adopting orjson means rewriting 79 sites and adding a runtime dependency with no measured gain. Do not enable.
- `cuda_kernel_integer_width` (8 findings): **n/a**. All eight hits are CUDA source snippets inside `tests/test_cuda_kernel_integer_width.py`, which are the gate's own fixtures. The repo has no CUDA code.

## Fix in code (real defects)

- `unsafe_deserialization` (2): `yaml.load` without a safe loader at `src/py_ci_shared/ci_default_branch_never_cancelled.py:404` and `src/py_ci_shared/ci_install_covers_conftest.py:205`. Replace with `yaml.safe_load`.
- `connect_error_echo` (1): `src/py_ci_shared/sql_verify.py:220` formats the exception after a DSN call. libpq can echo the connection string. Report `type(e).__name__` and `pgcode` only.
- `connection_liveness_kwargs` (5): psycopg2 connects in `embedded_postgres.py:160`, `schema_snapshot_parity.py:450,455`, `sql_verify.py:217,230` lack keepalive kwargs. Spread the keepalive kwargs.
- `api_floor` (1): `tests/test_corpus_drift.py:236` uses `sys.stdlib_module_names` (3.10) while `requires-python` is 3.9. Guard it or use a fallback.
- `committed_line_endings` (1): `src/py_ci_shared/_mutation_model.py` has 13 bare CR characters. Remove the bare CRs (CRLF is the repo's line ending, so keep the 236 CRLF lines and fix only the stray CRs).
- `ci_default_branch_never_cancelled` (1): `.github/workflows/self-ci.yml:14` sets `cancel-in-progress: true` for pushes. Restrict it to pull requests.
- `workflow_runner_labels` (3): `.github/workflows/self-ci.yml:50,52` use moving labels (`windows-latest`, `macos-latest`), and `.github/dependabot.yml` has no github-actions update channel. Run the gate's `--fix`, then review the diff.
- `predictable_tmp_path` (4): `src/py_ci_shared/_core/gate_cache.py:109` writes the cache under the shared temp dir with no per-user component. Add a per-user subdirectory. `offline_suite_without_credentials.py:122` needs the same review.
- `reiterated_iterable_params` (9): `Iterable` parameters consumed twice in `coverage_config_parity.py:153`, `ddl_lock_safety.py:387`, `disposition_test_references.py:182`, `gate_config_honesty.py:148`, `local_copy_report.py:155`, `prompt_field_parity.py:205`, `stale_comment_age.py:400`, `tracker_summary_parity.py:190`, `wrapper_protocol_parity.py:136`. Materialise with `list(...)` at entry.
- `single_shot_timing_assertion` (2): `tests/test_mutation_teeth.py:611` and `tests/test_mutation_teeth_regressions.py:829` assert a wall-clock ceiling on one run. Use best-of-N.
- `wrapper_protocol_parity` (1): `tests/test_protocol_attributes.py:30` copies `cache_clear` but not `has_value` in the wrapper. Forward it.
- `stub_signature_parity` (1): `tests/test_ci_health.py:499` stub lacks `base`. Add `**kwargs`.
- `unread_function_params` (2): `tests/test_mutation_teeth_regressions.py:58,428` unused `timeout`. Rename to `_timeout`.
- `validation_errors_echo_input` (1): `tests/test_env_example_round_trip.py:39` puts the validated value into the error. Report the field and bound, not the value.

## Exempt with reason (deliberate sites, inline marker)

- `env_write_restore` (2): `src/py_ci_shared/pytest_plugin.py:81,100` write `os.environ` on purpose to export the plugin's run state to xdist workers and subprocesses. Mark each with the reason.
- `optional_imports_guarded` (6): `pytest` imports at module level in `offline_suite_without_credentials.py`, `pytest_known_gap.py`, `pytest_plugin.py`, `resource_leak_guard.py`, `secret_safe_test_output.py`, `stub_signature_guard.py`. These modules are pytest plugins or pytest helpers and cannot be imported without pytest, so the marker is the honest answer. Verify each module is pytest-only before marking.
- `swallowed_exceptions` (10): best-effort cleanup and probes (`_mutation_runner.py:140,177,390`, `_mutation_worker.py:198,220`, `embedded_postgres.py:59,87,108`, `mutation_teeth.py:317`, `vacuous_loop_assertions.py:200`). Each needs a read before deciding: log at WARNING where the failure matters, mark `# swallow-ok` where it is cleanup. Not yet decided per site.

## Pending triage (not yet read in detail)

- `doc_identifier_parity` (73 names): the names may be illustrative doc identifiers. Needs a per-name check.
- `drifted_duplicate_functions` (51): real duplication to consolidate, large.
- `git_child_env` (65 new violations in tests): tests spawn `git` without scrubbing `GIT_*`. Real. Fix by a scrubbing env helper.
- `wallclock_test_waits` (18): tests sleep on the wall clock. Real. Inject the clock or wait on the condition.
- `standard_stream_restore` (3), `hash_fed_by_array_copy` (1): not yet read.
