Written for: the next session picking up py-ci-shared gate enablement.

# Proposals for handoff: enable corpus gates on py-ci-shared

Context: `gate_enablement_triage.md` in this directory holds the per-gate verdicts from running every default-argument corpus gate on this repository. This file lists the proposed work, in order, without doing it.

## Proposal 1: fix the real defects (one pass, regression test per fix)

Each item is a defect in this repository. Fix it in code, add a test that fails before and passes after.

1. `unsafe_deserialization`: `yaml.load` to `yaml.safe_load` in `src/py_ci_shared/ci_default_branch_never_cancelled.py` (line 404) and `src/py_ci_shared/ci_install_covers_conftest.py` (line 205).
2. `connect_error_echo`: `src/py_ci_shared/sql_verify.py` (line 220) reports the exception text after a DSN call. Report the exception type and SQLSTATE only.
3. `connection_liveness_kwargs`: add the keepalive kwargs to psycopg2 connects in `embedded_postgres.py` (line 160), `schema_snapshot_parity.py` (lines 450, 455) and `sql_verify.py` (lines 217, 230).
4. `api_floor`: `tests/test_corpus_drift.py` (line 236) uses `sys.stdlib_module_names` (3.10) with `requires-python` 3.9. Guard it or use a fallback.
5. `committed_line_endings`: `src/py_ci_shared/_mutation_model.py` has 13 bare CR characters. Remove them and keep the CRLF lines.
6. `ci_default_branch_never_cancelled`: `.github/workflows/self-ci.yml` line 14 sets `cancel-in-progress: true` for pushes. Restrict it to pull requests. Note that this repo's default branch is `master`, while the gate's message names `main`; check the gate's branch logic before editing.
7. `workflow_runner_labels`: pin `windows-latest` and `macos-latest` in `self-ci.yml` (lines 50, 52), and add a github-actions update channel to `.github/dependabot.yml`. Use the gate's `--fix`, then review the diff.
8. `predictable_tmp_path`: `src/py_ci_shared/_core/gate_cache.py` (line 109) stores under the shared temp dir with no per-user component. Add one. Review `offline_suite_without_credentials.py` (line 122) too.
9. `reiterated_iterable_params` (9 sites, listed in the triage file): materialise `Iterable` parameters with `list(...)` at entry.
10. `single_shot_timing_assertion` (2 sites in `tests/test_mutation_teeth.py` line 611 and `tests/test_mutation_teeth_regressions.py` line 829): measure best-of-N.
11. `wrapper_protocol_parity`, `stub_signature_parity`, `unread_function_params`, `validation_errors_echo_input`: one small test or wrapper fix each (see triage file for locations).

## Proposal 2: exemptions with inline markers (deliberate sites, reason required)

- `env_write_restore` (`src/py_ci_shared/pytest_plugin.py` lines 81, 100): deliberate export of run state to workers. Marker with reason.
- `optional_imports_guarded` (6 modules): verify each is pytest-only, then mark.

## Proposal 3: decide per site

- `swallowed_exceptions` (10 sites): read each. Log at WARNING where the failure matters, mark `# swallow-ok: <reason>` where it is best-effort cleanup.
- `doc_identifier_parity` (73 names): check whether names are illustrative or real missing identifiers.
- `drifted_duplicate_functions` (51): consolidate the duplicates or document each pair. Large.
- `git_child_env` (65 test sites): scrub `GIT_*` in test git spawns through one helper.
- `wallclock_test_waits` (18 test sites): inject the clock or wait on the condition.
- `standard_stream_restore` (3), `hash_fed_by_array_copy` (1): read and decide.

## Proposal 4: enable gates (after 1 to 3 are green)

Add `[tool.py_ci_shared.gates.<name>]` entries only for gates that pass with their config. Do not enable gates that still have open findings: that would turn the CI red.

## Not proposed

- `stdlib_json_ban`: not applicable. py-ci-shared has no orjson dependency and no measured hot path.
- `cuda_kernel_integer_width`: not applicable. Its eight hits are fixtures inside its own test file.
