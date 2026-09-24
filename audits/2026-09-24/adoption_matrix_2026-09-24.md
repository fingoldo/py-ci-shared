# py-ci-shared adoption matrix

## Summary

| repo | pins | distinct refs | pins agree | moving pins | silent skips | local copies | modules used |
|---|---|---|---|---|---|---|---|
| autopsia | 6 | `<unpinned>`, `v1` | **no** | 6 | 2 | 3 | 22 |
| mlframe | 21 | `64e2b6bab644`, `71957762ddd9`, `8b30f4d25ac5`, `915217a4a8cd`, `94b1c0dede79`, `f50288e5a6f5` | **no** | 0 | 5 | 2 | 71 |
| pyutilz | 12 | `51d618ad318e`, `797f04559568`, `<unpinned>`, `f50288e5a6f5`, `v1` | **no** | 6 | 11 | 6 | 32 |
| social | 9 | `f103a364bad6`, `v1` | **no** | 7 | 7 | 0 | 61 |
| dash_app_core | 4 | `f50288e5a6f5`, `v1` | **no** | 3 | 3 | 0 | 17 |
| llm_bench | 5 | `<unpinned>`, `a49421c79f42`, `v1` | **no** | 4 | 1 | 4 | 14 |
| glossum_backend_scripts | 7 | `4e9c72c25726`, `<unpinned>`, `v1` | **no** | 6 | 1 | 3 | 46 |
| flutter_app_core | 1 | `master` | yes | 1 | 2 | 0 | 14 |
| polyvocab_app | 1 | `master` | yes | 1 | 3 | 0 | 17 |
| noema_app | 0 | none | yes | 0 | 0 | 0 | 4 |
| flutter_uptime_monitor | 1 | `<sibling>` | yes | 1 | 0 | 0 | 0 |
| algopacksimple | 5 | `1fd408df105f`, `393c9839ee60`, `<unpinned>`, `f26052fd4fee` | **no** | 1 | 0 | 0 | 3 |
| claude-usage-notifier | 4 | `<sibling>`, `f26052fd4fee` | **no** | 1 | 0 | 0 | 1 |

## Pins

| repo | where | location | target | ref | kind |
|---|---|---|---|---|---|
| autopsia | `.github/workflows/black-filtered.yml:18` | workflow | workflow:black-filtered.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| autopsia | `.github/workflows/lint-advisory.yml:19` | workflow | workflow:lint-advisory.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| autopsia | `.github/workflows/mypy-full.yml:21` | workflow | workflow:mypy-full.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| autopsia | `.github/workflows/ruff-blocking.yml:20` | workflow | workflow:ruff-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| autopsia | `.github/workflows/ruff-blocking.yml:25` | workflow | workflow:ruff-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| autopsia | `pyproject.toml:118` | pyproject | package | `<unpinned>` | unpinned (moving) |
| mlframe | `.github/workflows/black-filtered.yml:32` | workflow | workflow:black-filtered.yml | `915217a4a8cdecc60b67c2c6c8dfe81de2f8a91c` | sha |
| mlframe | `.github/workflows/ci.yml:259` | workflow | action:install-pyutilz | `71957762ddd99200a66e3312a8ef9293b4c5d939` | sha |
| mlframe | `.github/workflows/ci.yml:416` | workflow | action:upload-codecov | `f50288e5a6f5b7f972efb60f948ce6184c8e9e70` | sha |
| mlframe | `.github/workflows/ci.yml:502` | workflow | action:install-pyutilz | `71957762ddd99200a66e3312a8ef9293b4c5d939` | sha |
| mlframe | `.github/workflows/ci.yml:633` | workflow | workflow:ruff-blocking.yml | `94b1c0dede79e8444c08288e9832b5d7f2dcf895` | sha |
| mlframe | `.github/workflows/ci.yml:639` | workflow | workflow:lint-blocking.yml | `64e2b6bab64414b8508dc05100a9421849a74a64` | sha |
| mlframe | `.github/workflows/ci.yml:673` | workflow | workflow:lint-advisory.yml | `915217a4a8cdecc60b67c2c6c8dfe81de2f8a91c` | sha |
| mlframe | `.github/workflows/ci.yml:690` | workflow | workflow:ruff-blocking.yml | `94b1c0dede79e8444c08288e9832b5d7f2dcf895` | sha |
| mlframe | `.github/workflows/ci.yml:697` | workflow | workflow:black-filtered.yml | `915217a4a8cdecc60b67c2c6c8dfe81de2f8a91c` | sha |
| mlframe | `.github/workflows/ci.yml:703` | workflow | workflow:lint-blocking.yml | `64e2b6bab64414b8508dc05100a9421849a74a64` | sha |
| mlframe | `.github/workflows/codecov-full.yml:240` | workflow | action:upload-codecov | `f50288e5a6f5b7f972efb60f948ce6184c8e9e70` | sha |
| mlframe | `.github/workflows/deep-nightly.yml:85` | workflow | action:install-pyutilz | `71957762ddd99200a66e3312a8ef9293b4c5d939` | sha |
| mlframe | `.github/workflows/deep-nightly.yml:179` | workflow | action:install-pyutilz | `71957762ddd99200a66e3312a8ef9293b4c5d939` | sha |
| mlframe | `.github/workflows/deep-nightly.yml:245` | workflow | action:upload-codecov | `f50288e5a6f5b7f972efb60f948ce6184c8e9e70` | sha |
| mlframe | `.github/workflows/docs.yml:35` | workflow | workflow:docs.yml | `915217a4a8cdecc60b67c2c6c8dfe81de2f8a91c` | sha |
| mlframe | `.github/workflows/fs-benchmark-nightly.yml:76` | workflow | action:install-pyutilz | `71957762ddd99200a66e3312a8ef9293b4c5d939` | sha |
| mlframe | `.github/workflows/macos-abort-probe.yml:188` | workflow | action:install-pyutilz | `71957762ddd99200a66e3312a8ef9293b4c5d939` | sha |
| mlframe | `.github/workflows/mypy-full.yml:40` | workflow | workflow:mypy-full.yml | `915217a4a8cdecc60b67c2c6c8dfe81de2f8a91c` | sha |
| mlframe | `.github/workflows/numba-coverage.yml:216` | workflow | action:upload-codecov | `f50288e5a6f5b7f972efb60f948ce6184c8e9e70` | sha |
| mlframe | `.pre-commit-config.yaml:641` | pre-commit | pre-commit | `v1.0.0` (= `8b30f4d25ac5`) | release |
| mlframe | `requirements-dev.txt:58` | requirements | package | `94b1c0dede79e8444c08288e9832b5d7f2dcf895` | sha |
| pyutilz | `.github/workflows/black-filtered.yml:20` | workflow | workflow:black-filtered.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| pyutilz | `.github/workflows/ci.yml:82` | workflow | package | `v1.16.1` (= `797f04559568`) | release |
| pyutilz | `.github/workflows/ci.yml:104` | workflow | action:upload-codecov | `51d618ad318e5ae1022bed214f91584953381113` | sha |
| pyutilz | `.github/workflows/ci.yml:133` | workflow | workflow:ruff-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| pyutilz | `.github/workflows/ci.yml:139` | workflow | workflow:lint-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| pyutilz | `.github/workflows/ci.yml:151` | workflow | workflow:lint-advisory.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| pyutilz | `.github/workflows/codecov-full.yml:197` | workflow | action:upload-codecov | `f50288e5a6f5b7f972efb60f948ce6184c8e9e70` | sha |
| pyutilz | `.github/workflows/docs.yml:33` | workflow | workflow:docs.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| pyutilz | `.github/workflows/mypy-full.yml:61` | workflow | package | `v1.16.1` (= `797f04559568`) | release |
| pyutilz | `.github/workflows/numba-coverage.yml:140` | workflow | action:upload-codecov | `f50288e5a6f5b7f972efb60f948ce6184c8e9e70` | sha |
| pyutilz | `.pre-commit-config.yaml:295` | pre-commit | pre-commit | `v1.3.5` (= `51d618ad318e`) | release |
| pyutilz | `requirements-dev.txt:13` | requirements | package | `<unpinned>` | unpinned (moving) |
| social | `.github/workflows/dashboard-ci.yml:56` | workflow | workflow:ruff-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| social | `.github/workflows/production-scrapers-ci.yml:80` | workflow | package | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| social | `.github/workflows/production-scrapers-ci.yml:151` | workflow | package | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| social | `.github/workflows/production-scrapers-ci.yml:194` | workflow | config-clone | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| social | `.github/workflows/production-scrapers-ci.yml:255` | workflow | workflow:ruff-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| social | `.github/workflows/realtime-applications-ci.yml:120` | workflow | config-clone | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| social | `.github/workflows/realtime-applications-ci.yml:219` | workflow | workflow:ruff-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| social | `upwork/new_scraper/production_scrapers/requirements.txt:56` | requirements | package | `f103a364bad61bfc576f5fdd09729a197ba5fe46` | sha |
| social | `upwork/new_scraper/realtime_applications/pyproject.toml:426` | pyproject | package | `f103a364bad61bfc576f5fdd09729a197ba5fe46` | sha |
| dash_app_core | `.github/workflows/ci.yml:53` | workflow | action:upload-codecov | `f50288e5a6f5b7f972efb60f948ce6184c8e9e70` | sha |
| dash_app_core | `.github/workflows/ci.yml:63` | workflow | workflow:ruff-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| dash_app_core | `.github/workflows/ci.yml:67` | workflow | workflow:lint-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| dash_app_core | `.github/workflows/ci.yml:79` | workflow | workflow:lint-advisory.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| llm_bench | `.github/workflows/black-filtered.yml:25` | workflow | workflow:black-filtered.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| llm_bench | `.github/workflows/ci.yml:119` | workflow | config-clone | `<unpinned>` | unpinned (moving) |
| llm_bench | `.github/workflows/ci.yml:141` | workflow | package | `a49421c79f4262326295ed14ed3859e9b85fdc5b` | sha |
| llm_bench | `.github/workflows/mypy-full.yml:36` | workflow | workflow:mypy-full.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| llm_bench | `pyproject.toml:65` | pyproject | package | `<unpinned>` | unpinned (moving) |
| glossum_backend_scripts | `.github/workflows/black-filtered.yml:16` | workflow | workflow:black-filtered.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| glossum_backend_scripts | `.github/workflows/ci.yml:340` | workflow | workflow:ruff-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| glossum_backend_scripts | `.github/workflows/ci.yml:346` | workflow | workflow:lint-blocking.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| glossum_backend_scripts | `.github/workflows/ci.yml:359` | workflow | workflow:lint-advisory.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| glossum_backend_scripts | `.github/workflows/mypy.yml:16` | workflow | workflow:mypy-beachhead.yml | `v1` (= `71cf6d041e88`) | moving-tag (moving) |
| glossum_backend_scripts | `pyproject.toml:199` | pyproject | package | `<unpinned>` | unpinned (moving) |
| glossum_backend_scripts | `uv.lock:2560` | uv.lock | package | `4e9c72c25726b4aae12c1f7bf8c8b559d405de07` | sha |
| flutter_app_core | `.github/workflows/ci.yml:27` | workflow | package | `master` (= `3c65a70cf9c9`) | branch (moving) |
| polyvocab_app | `.github/workflows/ci.yml:278` | workflow | package | `master` (= `3c65a70cf9c9`) | branch (moving) |
| flutter_uptime_monitor | `pyproject.toml:18` | pyproject | ruff-extend | `<sibling>` | sibling (moving) |
| algopacksimple | `.github/workflows/black.yml:25` | workflow | workflow:black-filtered.yml | `1fd408df105f2a7d22d36a239d94a39ef888305a` | sha |
| algopacksimple | `.github/workflows/ci.yml:82` | workflow | package | `<unpinned>` | unpinned (moving) |
| algopacksimple | `.github/workflows/ci.yml:120` | workflow | workflow:ruff-blocking.yml | `393c9839ee600097a351116584233ea365649927` | sha |
| algopacksimple | `.github/workflows/ci.yml:124` | workflow | workflow:lint-blocking.yml | `f26052fd4fee0d667aa6f4c3290d77e7ca6c1c06` | sha |
| algopacksimple | `.github/workflows/ci.yml:133` | workflow | workflow:lint-advisory.yml | `393c9839ee600097a351116584233ea365649927` | sha |
| claude-usage-notifier | `.github/workflows/ci.yml:38` | workflow | workflow:ruff-blocking.yml | `f26052fd4fee0d667aa6f4c3290d77e7ca6c1c06` | sha |
| claude-usage-notifier | `.github/workflows/ci.yml:44` | workflow | workflow:lint-blocking.yml | `f26052fd4fee0d667aa6f4c3290d77e7ca6c1c06` | sha |
| claude-usage-notifier | `.github/workflows/ci.yml:52` | workflow | workflow:lint-advisory.yml | `f26052fd4fee0d667aa6f4c3290d77e7ca6c1c06` | sha |
| claude-usage-notifier | `pyproject.toml:18` | pyproject | ruff-extend | `<sibling>` | sibling (moving) |

## Modules

X = the repo's tracked non-Markdown files outside `audits/` name `py_ci_shared.<module>` or enable it in `[tool.py_ci_shared]`.

| module | autopsia | mlframe | pyutilz | social | dash_app_core | llm_bench | glossum_backend_scripts | flutter_app_core | polyvocab_app | noema_app | flutter_uptime_monitor | algopacksimple | claude-usage-notifier | n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `adoption_matrix` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `atomic_write_staging` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `baseline_trend` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `checkpoint_isolation` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `cli` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `clock_day_boundary` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `config_drift_check` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `coverage_config_parity` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `hardcoded_token_ceilings` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `hash_key_determinism` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `import_cycles` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `install_safe_hook` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `kwarg_forwarding` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `lf_file_writes` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `local_copy_report` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `machine_specific_paths` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `module_cache_thread_safety` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `no_xfail_to_defer` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `numba_seed_range` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `order_losing_filters` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `pickle_state_completeness` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `plotly_annotation_loop` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `polars_null_equality` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `pytest_addopts_path_runs` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `pytest_plugin` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `randomly_seed_guard` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `registry` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `reiterated_iterable_params` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `resource_leak_guard` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `rollback_then_continue` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `safe_precommit` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `sentinel_or_fallback` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `setup_env` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `sql_verify` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `stale_source_citations` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `stdlib_json_ban` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `swallowed_exceptions` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `vulture_warn` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `worktree_hygiene` | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `alembic_concurrently` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `audit_path_references` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `conceded_defect_pins` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `config_call_site_parity` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `config_getattr_default_parity` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `dataclass_case_completeness` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `db_transaction_completeness` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `deletion_gates` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `discarded_model_copy` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `edge_function_hygiene` | . | . | . | . | . | . | . | . | X | . | . | . | . | 1 |
| `embedded_postgres` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `env_example_round_trip` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `env_flag_parsing` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `fail_open_handlers` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `gate_population_canary` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `git_changed_lines` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `ignore_ratchet` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `index_coverage` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `llm_call_archive_gate` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `mutation_teeth` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `nondiscriminating_shapes` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `printed_advice` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `prompt_field_parity` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `runtime_registry_mutation` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `save_failure_markers` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `source_text_ban` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `sql_verifier_coverage` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `sqlalchemy_text_binds` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `statement_compilation` | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `survivorship_scoring` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `teeth_sweep` | . | . | . | X | . | . | . | . | . | . | . | . | . | 1 |
| `unread_init_params` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `unresolved_imports` | . | X | . | . | . | . | . | . | . | . | . | . | . | 1 |
| `_toml_compat` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `arb_checks` | . | . | . | . | . | . | . | . | X | X | . | . | . | 2 |
| `bandit_warn` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `changelog_promise_parity` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `checkout_resolution` | X | . | . | . | X | . | . | . | . | . | . | . | . | 2 |
| `doc_identifier_parity` | . | X | . | . | . | . | X | . | . | . | . | . | . | 2 |
| `drifted_duplicate_functions` | . | X | X | . | . | . | . | . | . | . | . | . | . | 2 |
| `epsilon_padded_denominators` | . | X | X | . | . | . | . | . | . | . | . | . | . | 2 |
| `function_length` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `gate_config_honesty` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `gpu_timing_sync` | . | X | X | . | . | . | . | . | . | . | . | . | . | 2 |
| `guard_population` | . | . | . | . | . | . | . | X | X | . | . | . | . | 2 |
| `hash_fed_by_array_copy` | . | X | X | . | . | . | . | . | . | . | . | . | . | 2 |
| `hook_hygiene` | . | . | . | . | . | . | . | X | X | . | . | . | . | 2 |
| `identity_comparisons` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `import_layering` | . | . | . | . | . | . | . | X | X | . | . | . | . | 2 |
| `inert_patch_targets` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `latched_availability_flags` | . | X | X | . | . | . | . | . | . | . | . | . | . | 2 |
| `marker_runner_coverage` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `naive_utcnow` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `optional_truthiness` | . | X | X | . | . | . | . | . | . | . | . | . | . | 2 |
| `package_doctests` | . | X | . | . | . | . | X | . | . | . | . | . | . | 2 |
| `private_imports` | . | X | . | . | . | . | X | . | . | . | . | . | . | 2 |
| `pydantic_field_bounds` | . | X | . | . | . | . | X | . | . | . | . | . | . | 2 |
| `resource_release_paths` | . | X | . | . | . | . | X | . | . | . | . | . | . | 2 |
| `sql_function_privileges` | . | . | . | . | . | . | X | . | X | . | . | . | . | 2 |
| `test_partition_reachability` | . | . | . | . | . | . | . | X | X | . | . | . | . | 2 |
| `timezone_honest` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `version_consistency` | . | X | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `version_tag_currency` | . | . | . | . | . | . | . | X | X | . | . | . | . | 2 |
| `audit_disposition_parity` | . | X | . | . | . | . | . | X | X | . | . | . | . | 3 |
| `audit_wave_filenames` | . | X | . | X | . | . | X | . | . | . | . | . | . | 3 |
| `content_hash_version_bump_gate` | X | X | . | X | . | . | . | . | . | . | . | . | . | 3 |
| `dart_scanners` | . | . | . | . | . | . | . | X | X | X | . | . | . | 3 |
| `deferred_drift` | . | X | X | X | . | . | . | . | . | . | . | . | . | 3 |
| `disposition_test_references` | X | X | . | X | . | . | . | . | . | . | . | . | . | 3 |
| `meta_private_imports` | . | X | X | . | . | X | . | . | . | . | . | . | . | 3 |
| `module_reload_safety` | . | X | . | X | . | . | X | . | . | . | . | . | . | 3 |
| `prose_numeric_claims` | X | . | X | X | . | . | . | . | . | . | . | . | . | 3 |
| `source_text_claims` | . | X | . | X | . | . | X | . | . | . | . | . | . | 3 |
| `spec_bound_doubles` | . | . | X | X | . | . | X | . | . | . | . | . | . | 3 |
| `tool_versions` | . | X | . | X | . | . | X | . | . | . | . | . | . | 3 |
| `tracker_summary_parity` | X | X | . | X | . | . | . | . | . | . | . | . | . | 3 |
| `uncalled_functions` | X | X | . | . | . | . | X | . | . | . | . | . | . | 3 |
| `vacuous_loop_assertions` | X | X | . | X | . | . | . | . | . | . | . | . | . | 3 |
| `docs_inventory_parity` | . | X | X | . | . | . | . | X | X | . | . | . | . | 4 |
| `mypy_gate` | X | . | X | X | . | . | X | . | . | . | . | . | . | 4 |
| `readme_env_var_parity` | . | X | . | X | . | X | X | . | . | . | . | . | . | 4 |
| `advisory_warn` | . | X | X | X | . | X | X | . | . | . | . | . | . | 5 |
| `audit_round_format` | X | X | . | X | . | . | X | . | . | X | . | . | . | 5 |
| `baseline_hygiene` | X | . | . | X | . | . | X | X | X | . | . | . | . | 5 |
| `baseline_ratchet` | . | X | . | X | . | . | . | X | X | X | . | . | . | 5 |
| `ci_test_dir_reachability` | . | X | X | X | X | . | X | . | . | . | . | . | . | 5 |
| `ci_workflow_timeout_gate` | . | X | X | X | X | . | X | . | . | . | . | . | . | 5 |
| `entry_points_resolvable` | . | X | X | X | X | . | X | . | . | . | . | . | . | 5 |
| `fail_message_quality` | X | X | X | X | . | X | . | . | . | . | . | . | . | 5 |
| `ci_workflow_gate` | . | X | X | X | X | X | X | . | . | . | . | . | . | 6 |
| `ci_workflow_paths` | . | X | . | X | X | . | X | X | X | . | . | . | . | 6 |
| `format_warn` | . | X | X | X | . | X | X | . | . | . | . | X | . | 6 |
| `gate_integrity` | X | X | X | X | X | . | X | . | . | . | . | . | . | 6 |
| `git_dependency_pins` | . | X | X | X | X | X | X | . | . | . | . | . | . | 6 |
| `loc_budget` | . | X | X | X | X | X | X | . | . | . | . | . | . | 6 |
| `phantom_markdown_links` | X | X | X | X | X | . | X | . | . | . | . | . | . | 6 |
| `pinned_tool_versions` | X | X | X | X | X | . | X | . | . | . | . | . | . | 6 |
| `pytest_markers` | X | X | X | X | . | X | X | . | . | . | . | . | . | 6 |
| `value_bearing_asserts` | X | X | X | X | . | X | X | . | . | . | . | . | . | 6 |
| `code_audit_meta` | . | X | X | X | X | X | X | . | . | . | . | X | . | 7 |
| `effect_assertion_parity` | X | X | X | X | X | X | X | . | . | . | . | . | . | 7 |
| `import_side_effects` | X | X | X | X | X | X | X | . | . | . | . | . | . | 7 |
| `phantom_code_references` | X | X | . | X | X | . | X | X | X | . | . | . | . | 7 |
| `black_filtered_apply` | X | X | X | X | . | X | X | . | . | . | . | X | X | 8 |
| `repo_hygiene` | X | X | X | X | X | . | X | X | X | . | . | . | . | 8 |
| `stale_comment_age` | X | X | X | X | X | . | X | X | X | . | . | . | . | 8 |
| **modules used** | 22 | 71 | 32 | 61 | 17 | 14 | 46 | 14 | 17 | 4 | 0 | 3 | 1 | |

## Shared workflows and actions

| target | autopsia | mlframe | pyutilz | social | dash_app_core | llm_bench | glossum_backend_scripts | flutter_app_core | polyvocab_app | noema_app | flutter_uptime_monitor | algopacksimple | claude-usage-notifier |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| action:install-pyutilz | . | `71957762ddd9` | . | . | . | . | . | . | . | . | . | . | . |
| action:upload-codecov | . | `f50288e5a6f5` | `51d618ad318e`, `f50288e5a6f5` | . | `f50288e5a6f5` | . | . | . | . | . | . | . | . |
| workflow:black-filtered.yml | `v1` | `915217a4a8cd` | `v1` | . | . | `v1` | `v1` | . | . | . | . | `1fd408df105f` | . |
| workflow:docs.yml | . | `915217a4a8cd` | `v1` | . | . | . | . | . | . | . | . | . | . |
| workflow:lint-advisory.yml | `v1` | `915217a4a8cd` | `v1` | . | `v1` | . | `v1` | . | . | . | . | `393c9839ee60` | `f26052fd4fee` |
| workflow:lint-blocking.yml | . | `64e2b6bab644` | `v1` | . | `v1` | . | `v1` | . | . | . | . | `f26052fd4fee` | `f26052fd4fee` |
| workflow:mypy-beachhead.yml | . | . | . | . | . | . | `v1` | . | . | . | . | . | . |
| workflow:mypy-full.yml | `v1` | `915217a4a8cd` | . | . | . | `v1` | . | . | . | . | . | . | . |
| workflow:ruff-blocking.yml | `v1` | `94b1c0dede79` | `v1` | `v1` | `v1` | . | `v1` | . | . | . | . | `393c9839ee60` | `f26052fd4fee` |

## Findings

- autopsia: `.github/workflows/black-filtered.yml:18` pins-disagree: 2 different py-ci-shared refs: <unpinned>: pyproject.toml:118; v1: .github/workflows/black-filtered.yml:18, .github/workflows/lint-advisory.yml:19, .github/workflows/mypy-full.yml:21, .github/workflows/ruff-blocking.yml:20 (+1)
- autopsia: `.github/workflows/black-filtered.yml:18` moving-pin: workflow:black-filtered.yml pinned to v1 (moving-tag): it changes without a commit here
- autopsia: `.github/workflows/lint-advisory.yml:19` moving-pin: workflow:lint-advisory.yml pinned to v1 (moving-tag): it changes without a commit here
- autopsia: `.github/workflows/mypy-full.yml:21` moving-pin: workflow:mypy-full.yml pinned to v1 (moving-tag): it changes without a commit here
- autopsia: `.github/workflows/ruff-blocking.yml:20` moving-pin: workflow:ruff-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- autopsia: `.github/workflows/ruff-blocking.yml:25` moving-pin: workflow:ruff-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- autopsia: `pyproject.toml:118` moving-pin: package pinned to <unpinned> (unpinned): it changes without a commit here
- autopsia: `tests/test_meta/test_unasserted_effects.py:43` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- autopsia: `tests/test_meta/test_unasserted_effects.py:71` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- autopsia: `tests/test_meta/test_no_module_depends_on_import_order.py:1` local-copy: duplicates central `import_cycles` (matched by content); it does not import py_ci_shared
- autopsia: `tests/test_meta/test_no_new_module_level_import_cycles.py:1` local-copy: duplicates central `import_cycles` (matched by name); it does not import py_ci_shared
- autopsia: `tests/test_meta/test_no_reload_without_snapshot.py:1` local-copy: duplicates central `module_reload_safety` (matched by content); it does not import py_ci_shared
- mlframe: `.github/workflows/black-filtered.yml:32` pins-disagree: 6 different py-ci-shared refs: 64e2b6bab644: .github/workflows/ci.yml:639, .github/workflows/ci.yml:703; 71957762ddd9: .github/workflows/ci.yml:259, .github/workflows/ci.yml:502, .github/workflows/deep-nightly.yml:85, .github/workflows/deep-nightly.yml:179 (+2); 8b30f4d25ac5: .pre-commit-config.yaml:641; 915217a4a8cd: .github/workflows/black-filtered.yml:32, .github/workflows/ci.yml:673, .github/workflows/ci.yml:697, .github/workflows/docs.yml:35 (+1); 94b1c0dede79: .github/workflows/ci.yml:633, .github/workflows/ci.yml:690, requirements-dev.txt:58; f50288e5a6f5: .github/workflows/ci.yml:416, .github/workflows/codecov-full.yml:240, .github/workflows/deep-nightly.yml:245, .github/workflows/numba-coverage.yml:216
- mlframe: `tests/test_meta/test_code_audit_tests_baseline.py:37` importorskip: importorskip('py_ci_shared.code_audit_meta'): the gate turns SKIPPED when the install is missing or too old
- mlframe: `tests/test_meta/test_discovery_algo_version_bumped.py:16` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- mlframe: `tests/test_meta/test_printed_advice_wired.py:16` importorskip: importorskip('py_ci_shared.printed_advice'): the gate turns SKIPPED when the install is missing or too old
- mlframe: `tests/test_meta/test_shared_checks_wired.py:26` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- mlframe: `tests/test_meta/test_shared_uncalled_functions.py:23` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- mlframe: `tests/test_meta/test_no_bare_except.py:1` local-copy: duplicates central `code_audit_meta:bare_except` (matched by name); it does not import py_ci_shared
- mlframe: `tests/test_meta/test_no_import_cycles.py:1` local-copy: duplicates central `import_cycles` (matched by name); it does not import py_ci_shared
- pyutilz: `.github/workflows/black-filtered.yml:20` pins-disagree: 5 different py-ci-shared refs: 51d618ad318e: .github/workflows/ci.yml:104, .pre-commit-config.yaml:295; 797f04559568: .github/workflows/ci.yml:82, .github/workflows/mypy-full.yml:61; <unpinned>: requirements-dev.txt:13; f50288e5a6f5: .github/workflows/codecov-full.yml:197, .github/workflows/numba-coverage.yml:140; v1: .github/workflows/black-filtered.yml:20, .github/workflows/ci.yml:133, .github/workflows/ci.yml:139, .github/workflows/ci.yml:151 (+1)
- pyutilz: `.github/workflows/black-filtered.yml:20` moving-pin: workflow:black-filtered.yml pinned to v1 (moving-tag): it changes without a commit here
- pyutilz: `.github/workflows/ci.yml:133` moving-pin: workflow:ruff-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- pyutilz: `.github/workflows/ci.yml:139` moving-pin: workflow:lint-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- pyutilz: `.github/workflows/ci.yml:151` moving-pin: workflow:lint-advisory.yml pinned to v1 (moving-tag): it changes without a commit here
- pyutilz: `.github/workflows/docs.yml:33` moving-pin: workflow:docs.yml pinned to v1 (moving-tag): it changes without a commit here
- pyutilz: `requirements-dev.txt:13` moving-pin: package pinned to <unpinned> (unpinned): it changes without a commit here
- pyutilz: `tests/conftest.py:11` collect-ignore: sets collect_ignore when py_ci_shared does not import: those tests vanish from the run
- pyutilz: `tests/conftest.py:163` swallowed: swallows the ImportError of py_ci_shared and carries on
- pyutilz: `tests/test_meta/test_code_audit_baseline.py:319` importorskip: importorskip('py_ci_shared.code_audit_meta'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_meta/test_code_audit_tests_baseline.py:112` importorskip: importorskip('py_ci_shared.code_audit_meta'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_meta/test_docs_inventory_parity.py:24` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_meta/test_gate_integrity.py:31` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_meta/test_gpu_timing_synchronize.py:27` importorskip: importorskip('py_ci_shared.gpu_timing_sync'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_meta/test_prose_numeric_claims.py:26` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_meta/test_shared_checks_wired.py:18` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_typing_lint_hygiene_audit_20260903.py:184` importorskip: importorskip('py_ci_shared.pinned_tool_versions'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_typing_lint_hygiene_audit_20260903.py:195` importorskip: importorskip('py_ci_shared.pinned_tool_versions'): the gate turns SKIPPED when the install is missing or too old
- pyutilz: `tests/test_meta/test_no_bare_except.py:1` local-copy: duplicates central `code_audit_meta:bare_except` (matched by name); it does not import py_ci_shared
- pyutilz: `tests/test_meta/test_no_import_cycles.py:1` local-copy: duplicates central `import_cycles` (matched by name); it does not import py_ci_shared
- pyutilz: `tests/test_meta/test_no_module_reload.py:1` local-copy: duplicates central `module_reload_safety` (matched by content); it does not import py_ci_shared
- pyutilz: `tests/test_meta/test_no_reiterated_iterable_params.py:1` local-copy: duplicates central `reiterated_iterable_params` (matched by name); it does not import py_ci_shared
- pyutilz: `tests/test_meta/test_no_unicode_in_console_output.py:1` local-copy: duplicates central `code_audit_meta:console_unicode` (matched by name); it does not import py_ci_shared
- pyutilz: `tests/test_meta/test_reexport_package_idiom.py:1` local-copy: duplicates central `import_cycles` (matched by content); it does not import py_ci_shared
- pyutilz: unknown-module `py_ci_shared.gone` in `tests/code_audit/test_stale_source_citations.py`
- social: `.github/workflows/dashboard-ci.yml:56` pins-disagree: 2 different py-ci-shared refs: f103a364bad6: upwork/new_scraper/production_scrapers/requirements.txt:56, upwork/new_scraper/realtime_applications/pyproject.toml:426; v1: .github/workflows/dashboard-ci.yml:56, .github/workflows/production-scrapers-ci.yml:80, .github/workflows/production-scrapers-ci.yml:151, .github/workflows/production-scrapers-ci.yml:194 (+3)
- social: `.github/workflows/dashboard-ci.yml:56` moving-pin: workflow:ruff-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- social: `.github/workflows/production-scrapers-ci.yml:80` moving-pin: package pinned to v1 (moving-tag): it changes without a commit here
- social: `.github/workflows/production-scrapers-ci.yml:151` moving-pin: package pinned to v1 (moving-tag): it changes without a commit here
- social: `.github/workflows/production-scrapers-ci.yml:194` moving-pin: config-clone pinned to v1 (moving-tag): it changes without a commit here
- social: `.github/workflows/production-scrapers-ci.yml:255` moving-pin: workflow:ruff-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- social: `.github/workflows/realtime-applications-ci.yml:120` moving-pin: config-clone pinned to v1 (moving-tag): it changes without a commit here
- social: `.github/workflows/realtime-applications-ci.yml:219` moving-pin: workflow:ruff-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- social: `upwork/new_scraper/dashboard/tests/test_unasserted_effects.py:37` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- social: `upwork/new_scraper/dashboard/tests/test_unasserted_effects.py:69` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- social: `upwork/new_scraper/production_scrapers/tests/test_meta/test_no_invented_module_attributes.py:41` importorskip: importorskip('py_ci_shared.inert_patch_targets'): the gate turns SKIPPED when the install is missing or too old
- social: `upwork/new_scraper/production_scrapers/tests/test_meta/test_unasserted_effects.py:37` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- social: `upwork/new_scraper/production_scrapers/tests/test_meta/test_unasserted_effects.py:69` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- social: `upwork/new_scraper/realtime_applications/tests/test_meta/test_unasserted_effects.py:37` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- social: `upwork/new_scraper/realtime_applications/tests/test_meta/test_unasserted_effects.py:69` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- dash_app_core: `.github/workflows/ci.yml:53` pins-disagree: 2 different py-ci-shared refs: f50288e5a6f5: .github/workflows/ci.yml:53; v1: .github/workflows/ci.yml:63, .github/workflows/ci.yml:67, .github/workflows/ci.yml:79
- dash_app_core: `.github/workflows/ci.yml:63` moving-pin: workflow:ruff-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- dash_app_core: `.github/workflows/ci.yml:67` moving-pin: workflow:lint-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- dash_app_core: `.github/workflows/ci.yml:79` moving-pin: workflow:lint-advisory.yml pinned to v1 (moving-tag): it changes without a commit here
- dash_app_core: `tests/conftest.py:20` swallowed: swallows the ImportError of py_ci_shared and carries on
- dash_app_core: `tests/test_meta/test_code_audit_baseline.py:24` importorskip: importorskip('py_ci_shared.code_audit_meta'): the gate turns SKIPPED when the install is missing or too old
- dash_app_core: `tests/test_meta/test_shared_checks_wired.py:208` importorskip: importorskip('py_ci_shared.effect_assertion_parity'): the gate turns SKIPPED when the install is missing or too old
- llm_bench: `.github/workflows/black-filtered.yml:25` pins-disagree: 3 different py-ci-shared refs: <unpinned>: .github/workflows/ci.yml:119, pyproject.toml:65; a49421c79f42: .github/workflows/ci.yml:141; v1: .github/workflows/black-filtered.yml:25, .github/workflows/mypy-full.yml:36
- llm_bench: `.github/workflows/black-filtered.yml:25` moving-pin: workflow:black-filtered.yml pinned to v1 (moving-tag): it changes without a commit here
- llm_bench: `.github/workflows/ci.yml:119` moving-pin: config-clone pinned to <unpinned> (unpinned): it changes without a commit here
- llm_bench: `.github/workflows/mypy-full.yml:36` moving-pin: workflow:mypy-full.yml pinned to v1 (moving-tag): it changes without a commit here
- llm_bench: `pyproject.toml:65` moving-pin: package pinned to <unpinned> (unpinned): it changes without a commit here
- llm_bench: `tests/test_meta/test_shared_effect_assertion_parity.py:28` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- llm_bench: `tests/test_meta/test_no_audit_wave_filenames.py:1` local-copy: duplicates central `audit_wave_filenames` (matched by name); it does not import py_ci_shared
- llm_bench: `tests/test_meta/test_no_import_cycles.py:1` local-copy: duplicates central `import_cycles` (matched by name); it does not import py_ci_shared
- llm_bench: `tests/test_meta/test_no_source_text_proxy_assertions.py:1` local-copy: duplicates central `source_text_claims` (matched by content); it does not import py_ci_shared
- llm_bench: `tests/test_meta/test_no_unsafe_module_reload.py:1` local-copy: duplicates central `module_reload_safety` (matched by content); it does not import py_ci_shared
- glossum_backend_scripts: `.github/workflows/black-filtered.yml:16` pins-disagree: 3 different py-ci-shared refs: 4e9c72c25726: uv.lock:2560; <unpinned>: pyproject.toml:199; v1: .github/workflows/black-filtered.yml:16, .github/workflows/ci.yml:340, .github/workflows/ci.yml:346, .github/workflows/ci.yml:359 (+1)
- glossum_backend_scripts: `.github/workflows/black-filtered.yml:16` moving-pin: workflow:black-filtered.yml pinned to v1 (moving-tag): it changes without a commit here
- glossum_backend_scripts: `.github/workflows/ci.yml:340` moving-pin: workflow:ruff-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- glossum_backend_scripts: `.github/workflows/ci.yml:346` moving-pin: workflow:lint-blocking.yml pinned to v1 (moving-tag): it changes without a commit here
- glossum_backend_scripts: `.github/workflows/ci.yml:359` moving-pin: workflow:lint-advisory.yml pinned to v1 (moving-tag): it changes without a commit here
- glossum_backend_scripts: `.github/workflows/mypy.yml:16` moving-pin: workflow:mypy-beachhead.yml pinned to v1 (moving-tag): it changes without a commit here
- glossum_backend_scripts: `pyproject.toml:199` moving-pin: package pinned to <unpinned> (unpinned): it changes without a commit here
- glossum_backend_scripts: `tests/test_meta/test_shared_effect_assertion_parity.py:27` importorskip: importorskip('py_ci_shared'): the gate turns SKIPPED when the install is missing or too old
- glossum_backend_scripts: `tests/test_meta/test_no_empty_or_tautological_tests.py:1` local-copy: duplicates central `nondiscriminating_shapes` (matched by content); it does not import py_ci_shared
- glossum_backend_scripts: `tests/test_meta/test_no_hardcoded_token_ceilings.py:1` local-copy: duplicates central `hardcoded_token_ceilings` (matched by name); it does not import py_ci_shared
- glossum_backend_scripts: `tests/test_meta/test_no_machine_specific_paths.py:1` local-copy: duplicates central `machine_specific_paths` (matched by name); it does not import py_ci_shared
- flutter_app_core: `.github/workflows/ci.yml:27` moving-pin: package pinned to master (branch): it changes without a commit here
- flutter_app_core: `tool/check_shared_scanners.py:44` exit-0: exits 0 when py_ci_shared does not import: a missing install reads as a pass
- flutter_app_core: `tool/meta/scanners.py:522` availability-flag: `_SHARED_AVAILABLE`: sets a flag to False when py_ci_shared does not import: the checks behind it stop running
- polyvocab_app: `.github/workflows/ci.yml:278` moving-pin: package pinned to master (branch): it changes without a commit here
- polyvocab_app: `scripts/check_l10n.py:59` exit-0: exits 0 when py_ci_shared does not import: a missing install reads as a pass
- polyvocab_app: `tool/check_shared_scanners.py:50` exit-0: exits 0 when py_ci_shared does not import: a missing install reads as a pass
- polyvocab_app: `tool/meta/scanners.py:526` availability-flag: `_SHARED_AVAILABLE`: sets a flag to False when py_ci_shared does not import: the checks behind it stop running
- flutter_uptime_monitor: `pyproject.toml:18` moving-pin: ruff-extend pinned to <sibling> (sibling): it changes without a commit here
- algopacksimple: `.github/workflows/black.yml:25` pins-disagree: 4 different py-ci-shared refs: 1fd408df105f: .github/workflows/black.yml:25; 393c9839ee60: .github/workflows/ci.yml:120, .github/workflows/ci.yml:133; <unpinned>: .github/workflows/ci.yml:82; f26052fd4fee: .github/workflows/ci.yml:124
- algopacksimple: `.github/workflows/ci.yml:82` moving-pin: package pinned to <unpinned> (unpinned): it changes without a commit here
- claude-usage-notifier: `.github/workflows/ci.yml:38` pins-disagree: 2 different py-ci-shared refs: <sibling>: pyproject.toml:18; f26052fd4fee: .github/workflows/ci.yml:38, .github/workflows/ci.yml:44, .github/workflows/ci.yml:52
- claude-usage-notifier: `pyproject.toml:18` moving-pin: ruff-extend pinned to <sibling> (sibling): it changes without a commit here
