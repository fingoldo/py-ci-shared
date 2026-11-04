# 37 Gate defects found while wiring the gates into the consumers (step 8)

Round 2026-11-04, step 8 of PLAN.md. Each row is a false positive, a crash or a missing option in a py-ci-shared gate, found by a
consumer agent when it enabled the gate. Consumers work around each one locally (marker, exclusion) and the gate must be fixed here.
Every row is worked: RESOLVED with what was done, or WON'T FIX with the reason. Disposition on each row is OPEN until then.

| # | Gate | Defect | Seen in | Disposition |
|---|------|--------|---------|-------------|
| 37-F1 | skip_as_pass, comment_repo_paths | read tracked binary files (.png, .zip, .prof) as text and report them as unreadable; no way to exclude | polyvocab_app, glossum | OPEN |
| 37-F2 | skip_as_pass | flags a warning `echo` that contains the word "skipped" (`tool/check-hooks-were-not-bypassed.sh:65`) | polyvocab_app | OPEN |
| 37-F3 | machine_specific_paths | takes the sed regex `s://.*::` for a URL (`tool/check-edge-function-auth.sh:47`) | polyvocab_app | OPEN |
| 37-F4 | fail_open_handlers | `return exc.sqlstate` in an except is flagged as a silent default return although it returns the failure's own data | polyvocab_app | OPEN |
| 37-F5 | ci_pin_version_skew | `AttributeError: 'WindowsPath' object has no attribute 'rsplit'` on Windows through `run-all` | claude-usage-notifier, flutter_app_core | OPEN |
| 37-F6 | tracked_secret_shapes, sibling_floor_skew, import_side_effects | hard-fail or error on a repo with no applicable subject (no config table / no pyproject / no test_*.py); should SKIP | flutter repos | OPEN |
| 37-F7 | pickle_state_completeness | flags plain counters that are meant to be pickled (`SpeechAccounting.cached_chars`) | pyutilz | OPEN |
| 37-F8 | env_flag_parsing | flags path and number variables, not boolean flags | pyutilz | OPEN |
| 37-F9 | runtime_registry_mutation | flags an import-time registering decorator (`kernel_tuner`) | pyutilz | OPEN |
| 37-F10 | unsafe_deserialization | flags `pd.read_pickle` on bytes that come from a sidecar-verified helper | pyutilz | OPEN |
| 37-F11 | patch_target_read_at_call | misses late reads through `getattr(sys.modules.get(pkg), NAME)`, a `_pkg().NAME` helper and function-local `from X import Y` | pyutilz, autopsia | OPEN |
| 37-F12 | identity_comparisons | flags `is` on StrEnum members (`node.kind is NodeKind.CAUSE`) | autopsia | OPEN |
| 37-F13 | shared_mutable_placeholder | flags `threading.RLock()` | autopsia | OPEN |
| 37-F14 | no_source_text_tests | flags prose containing the word "FROM" | autopsia | OPEN |
| 37-F15 | verified_then_reopened | flags a size-checked truncate reopen; no suppression marker | autopsia, mlframe | OPEN |
| 37-F16 | hash_key_determinism | flags digests of exact wire bytes that key an archive | autopsia | OPEN |
| 37-F17 | check_then_act_outside_lock | blind to a call made under a guard | autopsia | OPEN |
| 37-F18 | lock_held_across_io | treats a cross-process file lock and a database advisory claim (`claim_lock`) as a thread lock | autopsia, glossum | OPEN |
| 37-F19 | git_child_env, wallclock_test_waits | look for a `tool/meta/baselines` directory the repo does not have | autopsia | OPEN |
| 37-F20 | validation_errors_echo_input | flags validators of the env-loaded Settings class, which is not a 422 request path | glossum | OPEN |
| 37-F21 | workflow_runner_labels | `--fix .` creates an untracked `.github/dependabot.yml` | glossum | OPEN |
| 37-F22 | secret_assertion_operands | flags non-secret env names (OMP_NUM_THREADS, CUDA_PATH, PYTHONHASHSEED); needs a secret-name filter | mlframe | OPEN |
| 37-F23 | checkout_resolution | fails in a worktree because the editable install points at the main checkout | mlframe | OPEN |

More rows are appended as the remaining consumer reports arrive (social, llm_bench group).
