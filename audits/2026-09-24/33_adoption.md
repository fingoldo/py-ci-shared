# Audit: consumer adoption

Read-only survey of every sibling repo under `C:\Users\Admin\Machine learning\` that references py-ci-shared (worktrees, `*-wt*`, `wt_*`, tmp dirs excluded).
Autopsia was read from `Redline/autopsia` (HEAD ed27ed5, 2026-09-23); the top-level `autopsia/` clone is stale (ad16b72, 2026-09-08) and was ignored.
Redline itself is not a git repo; sitetest-toolkit has no reference to py-ci-shared. flutter_uptime_monitor has only a ruff `extend` path.
noema_app's hits under `audits/` and `*.md` were excluded from the matrix (only code/config counts).

## Reference point

| ref | SHA | commits behind remote master |
|---|---|---|
| remote `origin/master` (current) | `41cbadc` | 0 |
| local checkout `py-ci-shared` HEAD | `39e9b49` | 4 (missing `env_flag_parsing`, `config_getattr_default_parity`, `survivorship_scoring`, source_text_claims `+=` fix) |
| moving tag `v1` (remote) | `2c5752a` | 20 |
| latest release tag `v1.16.1` | `797f045` | 36 |

Behind counts from `gh api repos/fingoldo/py-ci-shared/compare/<pin>...41cbadc5`, or local `git rev-list --count <pin>..39e9b49` + 4 (marked ~).

## How each repo pulls py-ci-shared

| repo | package install (tests / hooks) | reusable workflows / actions | lag |
|---|---|---|---|
| algopacksimple | CI `ci.yml:82` unpinned `git+...py-ci-shared.git` (floating master); pre-commit expects manual `pip install -e ../py-ci-shared` | black-filtered `1fd408d` (v1.2.1), ruff-blocking + lint-advisory `393c983` (v1.3.0), lint-blocking `f26052f` (v1.2.0) | workflows ~165-173 behind |
| autopsia (Redline/autopsia) | `pyproject.toml:118,691` bare `"py-ci-shared"` in `[dev]` with no URL (not on PyPI); CI `ci.yml:42` installs `.[test,llm]` only, so py_ci_shared is never installed in CI | black-filtered, lint-advisory, mypy-full, ruff-blocking `@v1` | v1 = 20 behind |
| claude-usage-notifier | none | ruff/lint-blocking/lint-advisory `f26052f` (v1.2.0) | ~173 behind |
| claude_notifier | none | same, `f26052f` (v1.2.0) | ~173 behind |
| dash_app_core | NOT in `[dev]` extras (`pyproject.toml:51-56`); CI installs `.[dev]` only | ruff/lint-blocking/lint-advisory `@v1`; upload-codecov `f50288e` (v1.3.4) | action ~149 behind |
| flutter_app_core | CI `ci.yml:27` `@master` (floating) | none | floating |
| flutter_uptime_monitor | none; `pyproject.toml:18` `extend = "../py-ci-shared/configs/ruff-base.toml"` | none, no CI | n/a |
| glossum_backend_scripts | `pyproject.toml:199` unpinned; `uv.lock:2562` locks `4e9c72c` | black-filtered, ruff/lint-blocking, lint-advisory, mypy-beachhead `@v1` | lock 26 behind; v1 20 behind |
| llm_bench | CI `ci.yml:141` `@a49421c`; CI `ci.yml:119` also `git clone --depth 1` of master; `pyproject.toml:65` unpinned | black-filtered, mypy-full `@v1` | package 152 behind, config clone floating |
| mlframe | `requirements-dev.txt:58` `@41cbadc` (= current master) | ruff-blocking `41cbadc`; lint-blocking `64e2b6b` (19 behind); black-filtered/lint-advisory/docs/mypy-full `915217a` (62 behind); install-pyutilz `7195776` (commented "v1.4.0", really v1.3.4+1, ~148 behind); upload-codecov `f50288e` | 0 / 19 / 62 / ~148 |
| noema_app | none in CI; `tool/check-*.py` need a local `pip install -e ../py-ci-shared` | none | n/a |
| polyvocab_app | CI `ci.yml:278` `@master` (floating) | none | floating |
| pyutilz | CI `ci.yml:82`, `mypy-full.yml:61` `@v1.16.1`; `requirements-dev.txt:13` unpinned | ruff/lint-blocking/lint-advisory/black-filtered/docs `@v1`; upload-codecov `51d618a` (v1.3.5) in ci.yml, `f50288e` (v1.3.4) in codecov-full.yml | 36 behind |
| social | `production-scrapers-ci.yml:80,151` pip `@v1`; `:194`, `realtime-applications-ci.yml:120` clone `--branch v1`; `production_scrapers/requirements.txt:56`, `realtime_applications/pyproject.toml:426` `@f103a36` | ruff-blocking `@v1` (3 workflows) | v1 20 behind; f103a36 21 behind |

## Adoption matrix (Python modules)

X = the repo's tracked non-Markdown, non-`audits/` files reference `py_ci_shared.<module>` (import, `python -m`, or path).
Columns: algo=algopacksimple, autop=Redline/autopsia, cun=claude-usage-notifier, cn=claude_notifier, dac=dash_app_core, fac=flutter_app_core, fum=flutter_uptime_monitor, glos=glossum_backend_scripts, llmb=llm_bench, mlf=mlframe, noema=noema_app, poly=polyvocab_app, pyu=pyutilz, soc=social.

| module | algo | autop | cun | cn | dac | fac | fum | glos | llmb | mlf | noema | poly | pyu | soc | n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `_mutation_worker` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `baseline_trend` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `checkpoint_isolation` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `config_drift_check` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `install_safe_hook` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `safe_precommit` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `setup_env` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `sql_verify` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `vulture_warn` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `worktree_hygiene` | . | . | . | . | . | . | . | . | . | . | . | . | . | . | 0 |
| `alembic_concurrently` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `audit_path_references` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `config_call_site_parity` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `dataclass_case_completeness` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `db_transaction_completeness` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `deletion_gates` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `discarded_model_copy` | . | . | . | . | . | . | . | . | . | X | . | . | . | . | 1 |
| `edge_function_hygiene` | . | . | . | . | . | . | . | . | . | . | . | X | . | . | 1 |
| `embedded_postgres` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `env_example_round_trip` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `fail_open_handlers` | . | . | . | . | . | . | . | . | . | X | . | . | . | . | 1 |
| `gate_population_canary` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `git_changed_lines` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `ignore_ratchet` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `index_coverage` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `llm_call_archive_gate` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `mutation_teeth` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `nondiscriminating_shapes` | . | . | . | . | . | . | . | . | . | X | . | . | . | . | 1 |
| `prompt_field_parity` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `runtime_registry_mutation` | . | . | . | . | . | . | . | . | . | X | . | . | . | . | 1 |
| `save_failure_markers` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `source_text_ban` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `sql_verifier_coverage` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `sqlalchemy_text_binds` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `statement_compilation` | . | . | . | . | . | . | . | X | . | . | . | . | . | . | 1 |
| `teeth_sweep` | . | . | . | . | . | . | . | . | . | . | . | . | . | X | 1 |
| `unread_init_params` | . | . | . | . | . | . | . | . | . | X | . | . | . | . | 1 |
| `unresolved_imports` | . | . | . | . | . | . | . | . | . | X | . | . | . | . | 1 |
| `_toml_compat` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `arb_checks` | . | . | . | . | . | . | . | . | . | . | X | X | . | . | 2 |
| `bandit_warn` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `changelog_promise_parity` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `checkout_resolution` | . | X | . | . | X | . | . | . | . | . | . | . | . | . | 2 |
| `doc_identifier_parity` | . | . | . | . | . | . | . | X | . | X | . | . | . | . | 2 |
| `drifted_duplicate_functions` | . | . | . | . | . | . | . | . | . | X | . | . | X | . | 2 |
| `epsilon_padded_denominators` | . | . | . | . | . | . | . | . | . | X | . | . | X | . | 2 |
| `function_length` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `gate_config_honesty` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `gpu_timing_sync` | . | . | . | . | . | . | . | . | . | X | . | . | X | . | 2 |
| `guard_population` | . | . | . | . | . | X | . | . | . | . | . | X | . | . | 2 |
| `hash_fed_by_array_copy` | . | . | . | . | . | . | . | . | . | X | . | . | X | . | 2 |
| `hook_hygiene` | . | . | . | . | . | X | . | . | . | . | . | X | . | . | 2 |
| `identity_comparisons` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `import_layering` | . | . | . | . | . | X | . | . | . | . | . | X | . | . | 2 |
| `inert_patch_targets` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `latched_availability_flags` | . | . | . | . | . | . | . | . | . | X | . | . | X | . | 2 |
| `marker_runner_coverage` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `naive_utcnow` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `optional_truthiness` | . | . | . | . | . | . | . | . | . | X | . | . | X | . | 2 |
| `package_doctests` | . | . | . | . | . | . | . | X | . | X | . | . | . | . | 2 |
| `private_imports` | . | . | . | . | . | . | . | X | . | X | . | . | . | . | 2 |
| `pydantic_field_bounds` | . | . | . | . | . | . | . | X | . | X | . | . | . | . | 2 |
| `resource_release_paths` | . | . | . | . | . | . | . | X | . | X | . | . | . | . | 2 |
| `sql_function_privileges` | . | . | . | . | . | . | . | X | . | . | . | X | . | . | 2 |
| `test_partition_reachability` | . | . | . | . | . | X | . | . | . | . | . | X | . | . | 2 |
| `timezone_honest` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `version_consistency` | . | . | . | . | . | . | . | . | . | X | . | . | . | X | 2 |
| `version_tag_currency` | . | . | . | . | . | X | . | . | . | . | . | X | . | . | 2 |
| `audit_disposition_parity` | . | . | . | . | . | X | . | . | . | X | . | X | . | . | 3 |
| `audit_wave_filenames` | . | . | . | . | . | . | . | X | . | X | . | . | . | X | 3 |
| `content_hash_version_bump_gate` | . | X | . | . | . | . | . | . | . | X | . | . | . | X | 3 |
| `dart_scanners` | . | . | . | . | . | X | . | . | . | . | X | X | . | . | 3 |
| `deferred_drift` | . | . | . | . | . | . | . | . | . | X | . | . | X | X | 3 |
| `disposition_test_references` | . | X | . | . | . | . | . | . | . | X | . | . | . | X | 3 |
| `meta_private_imports` | . | . | . | . | . | . | . | . | X | X | . | . | X | . | 3 |
| `module_reload_safety` | . | . | . | . | . | . | . | X | . | X | . | . | . | X | 3 |
| `prose_numeric_claims` | . | X | . | . | . | . | . | . | . | . | . | . | X | X | 3 |
| `source_text_claims` | . | . | . | . | . | . | . | X | . | X | . | . | . | X | 3 |
| `spec_bound_doubles` | . | . | . | . | . | . | . | X | . | . | . | . | X | X | 3 |
| `tool_versions` | . | . | . | . | . | . | . | X | . | X | . | . | . | X | 3 |
| `tracker_summary_parity` | . | X | . | . | . | . | . | . | . | X | . | . | . | X | 3 |
| `uncalled_functions` | . | X | . | . | . | . | . | X | . | X | . | . | . | . | 3 |
| `vacuous_loop_assertions` | . | X | . | . | . | . | . | . | . | X | . | . | . | X | 3 |
| `docs_inventory_parity` | . | . | . | . | . | X | . | . | . | X | . | X | X | . | 4 |
| `mypy_gate` | . | X | . | . | . | . | . | X | . | . | . | . | X | X | 4 |
| `readme_env_var_parity` | . | . | . | . | . | . | . | X | X | X | . | . | . | X | 4 |
| `advisory_warn` | . | . | . | . | . | . | . | X | X | X | . | . | X | X | 5 |
| `audit_round_format` | . | X | . | . | . | . | . | X | . | X | X | . | . | X | 5 |
| `baseline_hygiene` | . | X | . | . | . | X | . | X | . | . | . | X | . | X | 5 |
| `baseline_ratchet` | . | . | . | . | . | X | . | . | . | X | X | X | . | X | 5 |
| `ci_test_dir_reachability` | . | . | . | . | X | . | . | X | . | X | . | . | X | X | 5 |
| `ci_workflow_timeout_gate` | . | . | . | . | X | . | . | X | . | X | . | . | X | X | 5 |
| `entry_points_resolvable` | . | . | . | . | X | . | . | X | . | X | . | . | X | X | 5 |
| `fail_message_quality` | . | X | . | . | . | . | . | . | X | X | . | . | X | X | 5 |
| `ci_workflow_gate` | . | . | . | . | X | . | . | X | X | X | . | . | X | X | 6 |
| `ci_workflow_paths` | . | . | . | . | X | X | . | X | . | X | . | X | . | X | 6 |
| `format_warn` | X | . | . | . | . | . | . | X | X | X | . | . | X | X | 6 |
| `gate_integrity` | . | X | . | . | X | . | . | X | . | X | . | . | X | X | 6 |
| `git_dependency_pins` | . | . | . | . | X | . | . | X | X | X | . | . | X | X | 6 |
| `loc_budget` | . | . | . | . | X | . | . | X | X | X | . | . | X | X | 6 |
| `phantom_markdown_links` | . | X | . | . | X | . | . | X | . | X | . | . | X | X | 6 |
| `pinned_tool_versions` | . | X | . | . | X | . | . | X | . | X | . | . | X | X | 6 |
| `pytest_markers` | . | X | . | . | . | . | . | X | X | X | . | . | X | X | 6 |
| `value_bearing_asserts` | . | X | . | . | . | . | . | X | X | X | . | . | X | X | 6 |
| `code_audit_meta` | X | . | . | . | X | . | . | X | X | X | . | . | X | X | 7 |
| `effect_assertion_parity` | . | X | . | . | X | . | . | X | X | X | . | . | X | X | 7 |
| `import_side_effects` | . | X | . | . | X | . | . | X | X | X | . | . | X | X | 7 |
| `phantom_code_references` | . | X | . | . | X | X | . | X | . | X | X | X | . | X | 8 |
| `repo_hygiene` | . | X | . | . | X | X | . | X | . | X | . | X | X | X | 8 |
| `stale_comment_age` | . | X | . | . | X | X | . | X | . | X | . | X | X | X | 8 |
| `black_filtered_apply` | X | X | X | X | . | . | . | X | X | X | . | . | X | X | 9 |
| **modules used** | 3 | 22 | 1 | 1 | 17 | 14 | 0 | 46 | 14 | 66 | 5 | 17 | 32 | 61 | |

The three modules on remote master after local HEAD (`env_flag_parsing`, `config_getattr_default_parity`, `survivorship_scoring`) are adopted nowhere.

#### Reusable workflows / composite actions

| workflow/action | algo | autop | cun | cn | dac | glos | llmb | mlf | pyu | soc |
|---|---|---|---|---|---|---|---|---|---|---|
| ruff-blocking.yml | X | X | X | X | X | X | . | X | X | X |
| lint-blocking.yml | X | . | X | X | X | X | . | X | X | . |
| lint-advisory.yml | X | X | X | X | X | X | . | X | X | . |
| black-filtered.yml | X | X | . | . | . | X | X | X | X | . |
| mypy-full.yml | . | X | . | . | . | . | X | X | . | . |
| mypy-beachhead.yml | . | . | . | . | . | X | . | . | . | . |
| docs.yml | . | . | . | . | . | . | . | X | X | . |
| config-drift-check.yml | . | . | . | . | . | . | . | . | . | . |
| actions/upload-codecov | . | . | . | . | X | . | . | X | X | . |
| actions/install-pyutilz | . | . | . | . | . | . | . | X | . | . |

## Findings

### ADOPT-1 (High) -- CI installs .[test,llm] only; py-ci-shared is in [dev], so it is never installed

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: RESOLVED -- autopsia `pyproject.toml`: pinned `py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared@<sha> # v1.17.0` added to the `test` extra, which CI's `pip install -e ".[test,llm]"` (`.github/workflows/ci.yml`, and mypy-full's install-command) already installs, so `tests/conftest.py`'s `pytest_addoption` imports and the 13 meta-test files that import py_ci_shared have the package in CI; verified by: the 13 py_ci_shared-importing meta-test files against the pin (ad_autopsia_4 red on 8 -> ad_autopsia_5 green after the adaptations under ADOPT-2), plus the whole `tests/test_meta/` hook suite (ad_autopsia_11_meta: 425 passed, 16 failed; the 6 failures this change caused are fixed and green in ad_autopsia_12, 102 passed; the other 10 need the built KB seeds and overlays this worktree lacks, e.g. `only 11156 named rows ... seeds not built?`, 681 of 11506 MONDO causes, `track_b_llm_sourced.jsonl: 0 rows`: `test_kb_name_hygiene`, `test_ru_translation_coverage`, `test_guards_are_not_vacuous`, `test_every_numeric_row_states_its_license`, `test_narrow_population_priors_do_not_accumulate` x2, `test_ru_labels_are_diseases_not_icd_rubrics`, `test_track_{a,b}_dependency_guard_completeness`; the coordinator's commit runs the hook in a checkout with built seeds)]

- **Original id:** A-01
- **Where:** Redline/autopsia `.github/workflows/ci.yml:42`, `tests/conftest.py:58-59`
- **Finding:** CI installs `.[test,llm]` only; py-ci-shared is in `[dev]`, so it is never installed. `pytest_addoption` imports `py_ci_shared.content_hash_version_bump_gate` / `uncalled_functions` unconditionally, so the CI test job cannot start, and 11 test files import py_ci_shared at module level. Masked right now because Actions jobs are billing-blocked (A-30).
- **Proposed fix:** Put a git-pinned py-ci-shared into the `test` extra (or a CI install step) at the same SHA as the workflows.

### ADOPT-2 (High) -- [dev] lists bare "py-ci-shared" with no direct URL

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: RESOLVED -- autopsia `pyproject.toml` `[dev]`: bare `"py-ci-shared"` replaced with the same git+SHA direct reference (the bare name in `[tool.deptry.per_rule_ignores] DEP002` is a distribution name for deptry, not a dependency, and stays). Adapting to 1.17.0 (8 meta tests red on the pin, all 19 green on the old `v1`; every refresh inspected, nothing new accepted unread): `test_unasserted_effects.py` both `pytest.importorskip` sites -> hard import; `audits/implemented/2026-09-02/TRACKER.md` row 18 was missing its Location cell, so the by-name column parser read its Disposition as empty -> cell restored from the lane report; phantom_code_references: 11 stale backticked names fixed in comments (the twelfth, in `api_noema_parse_localization.py`, is left baselined: that file feeds the API content-hash gate, and a comment edit there would force a schema-version bump; `ParseCache.MAX_ENTRIES` -> `parse_cache.MAX_ENTRIES`, `Chain.strength` -> `Chain.weakest`, `Excluded.because`/`Worksheet.unexplained_findings` -> the `ExcludedOut`/`WorksheetOut` view models, `CausalGraph.temporal_class` -> `temporal_class_of`, ...), third-party heads (`pyutilz`, `cupy`, `numba`, `scipy`, `rapidfuzz`, `fastapi`, `starlette`, `URLError`) added to `_EXTRA_KNOWN`; baseline refreshed: 12 `evidence_core.*` false entries drained, 24 added, each read (FHIR resource paths, World Bank indicator codes, SPARQL `LANG()`, domain names, deliberate "never `CausalGraph.Node.severity`"/"used to carry `Provenance.temporal_class`" historical claims, the Flutter client's `Worksheet.fromJson`); phantom_markdown_links: 9 dead links fixed (`docs/noema_design.md`, `docs/regulatory_matrix.md` -> `audits/implemented/...`; five `SWEEP_REVIEW_*` -> `bench/gap_disease_src/SWEEP_REVIEW_TRACKER.md`) and a verbatim quote re-wrapped so `[CI]: 0.44-0.45)` no longer starts a line (Markdown parsed it as a link-reference definition and dropped the text) -> baseline `[]`; uncalled_functions: 11 public helpers with only test callers (`vocab.coverage`, `snomed.fsn`, `loinc_ru.display_ru`, ...) newly visible (1.17.0 stopped counting a same-named attribute call elsewhere) -> ratchet baseline; effect_assertion_parity: 3 new sites were SELECT-only reads; on 530b82e only `vocab/structure_drugs.py` is still reported and keeps its reason; vacuous_loop_assertions over production trees: 7 raise-only validation loops where zero iterations is correct -> `_VALIDATION_LOOPS_THAT_ACCEPT_EMPTY` with a stale-entry check; stale_comment_age: a `# DDXPlus (...)` section banner read as commented-out code -> reworded; fail_message_quality: one message given its action; verified by: ad_autopsia_5.log (19 passed), ad_autopsia_10.log (54 passed)]

- **Original id:** A-02
- **Where:** Redline/autopsia `pyproject.toml:118,691`
- **Finding:** `[dev]` lists bare `"py-ci-shared"` with no direct URL. The name is not on PyPI (`pip index versions` finds nothing), so `pip install -e .[dev]` fails on a clean machine, and anyone registering that name on PyPI would be installed (dependency confusion).
- **Proposed fix:** `py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@<sha>`.

### ADOPT-3 (High) -- py-ci-shared is not a dependency, yet tests/test_meta/test_no_top_level_side_effects.py...

**Disposition:** RESOLVED -- [social, dash_app_core, llm_bench, glossum: RESOLVED -- dash_app_core: pinned `py-ci-shared` and `pyutilz @ ...@8a40c46b9d27e456016bfd29b31a3e563a51323f` (code_audit_meta wraps pyutilz.dev.code_audit; the SHA the local baseline was built with) added to `[project.optional-dependencies].dev` in `pyproject.toml` (and to deptry DEP002); CI's test job already installs `.[dev]` before pytest. `tests/conftest.py` imports unguarded; `tests/test_meta/test_code_audit_baseline.py` and `test_shared_checks_wired.py::test_no_new_unasserted_effect` import directly instead of `importorskip`. Found on the way and fixed: both meta tests imported `orjson`, which nothing declares now that py-ci-shared dropped it (-> stdlib `json`); `test_declared_entry_points_resolve` hit 1.17.0's floor with no entry points declared (-> `min_entries=0`, reason in the docstring); the stale-TODO test uses `git blame`, so the CI test checkout got `fetch-depth: 0`; the git-pin test no longer exempts first-party URLs. verified by: `pytest tests/test_meta` (ad_dash_app_core_2.log 52 passed; _4.log with the plugin loaded 52 passed), gate items `pytest -p py_ci_shared.pytest_plugin -m py_ci_shared` (ad_dash_app_core_3.log 17 passed), yamllint clean.]

- **Original id:** A-03
- **Where:** dash_app_core `pyproject.toml:51-56`, `.github/workflows/ci.yml:43`
- **Finding:** py-ci-shared is not a dependency, yet `tests/test_meta/test_no_top_level_side_effects.py:13` and `test_the_suite_tests_this_checkout.py:23` import it at module level (collection error in CI), `test_code_audit_baseline.py:24` and `test_shared_checks_wired.py:208` `importorskip` it (silent skip), `conftest.py:18-21` swallows ImportError.
- **Proposed fix:** Add pinned py-ci-shared to `[dev]`; drop the importorskip/except once installed.

### ADOPT-4 (High) -- On ImportError these gates print "SKIPPED" and sys.exit(0): a missing install reads as...

**Disposition:** RESOLVED -- [flutter_app_core, polyvocab_app: RESOLVED -- flutter_app_core `tool/check_shared_scanners.py`, polyvocab_app `tool/check_shared_scanners.py` and `scripts/check_l10n.py`: the `except ImportError` branch now prints `FAILED - py-ci-shared is not importable (<reason>)` plus the pinned install line to stderr and exits 1 (was `SKIPPED` + exit 0). Module docstrings that promised a skip are rewritten. verified by: each script run with PYTHONPATH pointing at a stub `py_ci_shared` that raises ImportError -> exit 1 with the FAILED line (logs/ad_absent_1.log); with PYTHONPATH=<pcs 03cfff1>/src, check_l10n.py exit 0 and flutter_app_core check_shared_scanners.py exit 0 (logs/ad_*_2.log).]

- **Original id:** A-04
- **Where:** polyvocab_app `tool/check_shared_scanners.py:~50-54`, `scripts/check_l10n.py:60-64`; flutter_app_core `tool/check_shared_scanners.py:45-49`
- **Finding:** On ImportError these gates print "SKIPPED" and `sys.exit(0)`: a missing install reads as a pass. noema_app's equivalents (`tool/check-arb.py:53-58`, `check-audit-rounds.py:31-36`, `check-baselined-rules.py`) correctly `return 1`.
- **Proposed fix:** Exit 1 like noema_app.

### ADOPT-5 (High) -- _SHARED_AVAILABLE=False on ImportError silently drops 7 Dart scans from SCANS; the ratc...

**Disposition:** RESOLVED -- [flutter_app_core, polyvocab_app: RESOLVED -- both repos' `tool/meta/scanners.py`: the `try/except ImportError` around `from py_ci_shared.dart_scanners import ...` and the `_SHARED_AVAILABLE` flag are gone; the import is unguarded and the seven shared scans sit in the `SCANS` literal unconditionally, so a missing package fails the module load (check-baselined-rules.py and regen_baselines.py both exit 1) instead of silently dropping seven rules. verified by: stub-package run above (both check-baselined-rules.py and regen_baselines.py exit 1 with ImportError in both repos); real run with the pinned pcs: all 15 (flutter_app_core) / 16 (polyvocab_app) rules report, exit 0.]

- **Original id:** A-05
- **Where:** flutter_app_core `tool/meta/scanners.py:521-523,589`; polyvocab_app `tool/meta/scanners.py:525-527,587`
- **Finding:** `_SHARED_AVAILABLE=False` on ImportError silently drops 7 Dart scans from `SCANS`; the ratchet then runs fewer rules and still passes.
- **Proposed fix:** Fail on import failure (noema_app's `tool/meta/scanners.py` imports unguarded).

### ADOPT-6 (Med) -- collect_ignore of the meta tests whenever import py_ci_shared fails

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: NOT A DEFECT -- ad1's part: none of autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app has a `collect_ignore` on a py_ci_shared import failure (`git grep collect_ignore` in each worktree); the finding is pyutilz-only; verified by: git grep] [mlframe, pyutilz: RESOLVED -- pyutilz tests/conftest.py: `collect_ignore` applies only under `sys.version_info < (3, 9)` and is derived from every test file with a top-level `py_ci_shared` import (was a hand list of 6, now 16, so new gate files are covered); the conftest's `register_refresh_option` import is unguarded on 3.9+ (was `except ImportError: pass`); CI installs py-ci-shared via `pip install -r requirements-dev.txt`, whose marker skips it on 3.8. verified by: pyutilz meta run on 3.14 collecting every gate file.] [social, dash_app_core, llm_bench, glossum: NOT A DEFECT -- these four repos have no `collect_ignore` on an ImportError of py_ci_shared (grepped every conftest); the ImportError handlers left in `social/.../tests/conftest.py`, `glossum/tests/conftest.py` guard httpx/requests/thinc, not the gate package. verified by: `grep -rn "collect_ignore\|except ImportError" tests conftest.py` in each worktree.]

- **Original id:** A-06
- **Where:** pyutilz `tests/conftest.py:9-16`
- **Finding:** `collect_ignore` of the meta tests whenever `import py_ci_shared` fails. Justified for the 3.8 legs, but applied on every interpreter, so a broken install on 3.9+ silently drops those gates.
- **Proposed fix:** Condition on `sys.version_info < (3, 9)`; fail otherwise.

### ADOPT-7 (Med) -- pytest.importorskip("py_ci_shared..."): whole gate files turn SKIPPED when the package...

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: RESOLVED -- ad1's part (autopsia `tests/test_meta/test_unasserted_effects.py`, lines 43 and 71): both `pytest.importorskip("py_ci_shared.effect_assertion_parity")` calls replaced by a hard `from py_ci_shared import effect_assertion_parity` in `_shared()`, which `regenerate_baseline()` now also uses, so a missing or too-old package is an ImportError, never a skip. No other importorskip/SKIPPED fallback exists in ad1's repos (noema_app's `tool/check-*.py` already `return 1` on ImportError); verified by: ad_autopsia_5.log (test_unasserted_effects passes against the pin), `git grep importorskip` in each worktree] [mlframe, pyutilz: RESOLVED -- (mlframe, pyutilz part) every `pytest.importorskip("py_ci_shared...")` replaced by a hard import: mlframe test_shared_checks_wired.py, test_shared_uncalled_functions.py, test_discovery_algo_version_bumped.py, test_code_audit_tests_baseline.py, test_kwarg_forwarding_wired.py, test_printed_advice_wired.py, tests/training/composite/ensemble/test_frame_carrier_parity.py; pyutilz test_shared_checks_wired.py, test_gate_integrity.py, test_docs_inventory_parity.py, test_prose_numeric_claims.py, test_gpu_timing_synchronize.py, test_code_audit_baseline.py, test_code_audit_tests_baseline.py and tests/test_typing_lint_hygiene_audit_20260903.py (its two F06 tests now `skipif(sys.version_info < (3, 9))` plus a hard import). The shared `require()` helper the finding proposes does not exist in py-ci-shared and is not needed once the package is a hard CI dependency. verified by: no `importorskip("py_ci_shared` left in either repo; meta runs.] [social, dash_app_core, llm_bench, glossum: RESOLVED -- every py_ci_shared `importorskip` in these repos is a hard import: glossum `tests/test_meta/test_shared_effect_assertion_parity.py`; llm_bench `tests/test_meta/test_shared_effect_assertion_parity.py`; social `{dashboard/tests,production_scrapers/tests/test_meta,realtime_applications/tests/test_meta}/test_unasserted_effects.py` (both sites in each: `_shared()` at line 37 and `regenerate_baseline` at 69) and `production_scrapers/tests/test_meta/test_no_invented_module_attributes.py:41`; dash_app_core under ADOPT-3. verified by: social ad_social_10/12/14.log, llm_bench ad_llm_bench_4.log, glossum ad_glossum_6.log (all passed).]

- **Original id:** A-07
- **Where:** mlframe `tests/test_meta/test_shared_checks_wired.py:26`, `test_shared_uncalled_functions.py:23`, `test_discovery_algo_version_bumped.py:16`, `test_code_audit_tests_baseline.py:37`; pyutilz `tests/test_meta/test_shared_checks_wired.py:18`, `test_gate_integrity.py:31`, `test_docs_inventory_parity.py:24`, `test_prose_numeric_claims.py:26`, `test_gpu_timing_synchronize.py:27`, `test_code_audit_baseline.py:319`, `test_code_audit_tests_baseline.py:112`, `tests/test_typing_lint_hygiene_audit_20260903.py:184,195`; glossum `tests/test_meta/test_shared_effect_assertion_parity.py:27`; llm_bench same file `:28`; autopsia `tests/test_meta/test_unasserted_effects.py:71`; social `upwork/new_scraper/{dashboard/tests,production_scrapers/tests/test_meta,realtime_applications/tests/test_meta}/test_unasserted_effects.py:69`; dash_app_core (A-03)
- **Finding:** `pytest.importorskip("py_ci_shared...")`: whole gate files turn SKIPPED when the package is absent or too old for the module (the typical pin-lag case). Inconsistent with glossum `test_dependency_pinning.py:203-206`, which `pytest.fail`s on the same condition.
- **Proposed fix:** One shared helper (e.g. `py_ci_shared.require(module)`) that fails under `CI=true` and skips only locally; replace every importorskip site.

### ADOPT-8 (Med) -- Black-pin cross-check reads ../py-ci-shared/.github/workflows/black-filtered.yml from a...

**Disposition:** RESOLVED -- [social, dash_app_core, llm_bench, glossum: RESOLVED -- glossum `tests/test_meta/test_dependency_pinning.py` compares the black pin with `py_ci_shared.tool_versions.BLACK_VERSION` from the installed package (shipped at 530b82e); the sibling-checkout read and its skip are gone. verified by: ad_glossum_21.log (test_dependency_pinning in 66 passed).]

- **Original id:** A-08
- **Where:** glossum_backend_scripts `tests/test_meta/test_dependency_pinning.py:212-214`
- **Finding:** Black-pin cross-check reads `../py-ci-shared/.github/workflows/black-filtered.yml` from a sibling checkout and skips if absent, so it always skips in CI, and locally it compares against whatever the sibling is at, not the pin.
- **Proposed fix:** Export `BLACK_VERSION` from `py_ci_shared.tool_versions` next to `RUFF_VERSION` and read that.

### ADOPT-9 (Med) -- mypy_gate --min-files 150 dashboard \/\/ true: result and population floor both discard...

**Disposition:** RESOLVED -- [social, dash_app_core, llm_bench, glossum: RESOLVED -- social `.pre-commit-config.yaml`: `mypy_gate --min-files 150 dashboard || true` replaced by hook `mypy-ratchet` running new `upwork/new_scraper/dashboard/scripts/mypy_ratchet.py --min-files 280 --max-errors 607 dashboard`: fails when mypy does not print its terminator (patterns imported from `py_ci_shared.mypy_gate`), when fewer than 280 files were checked, or when errors exceed the recorded 607 (measured 2026-09-25: 607 errors, 315 files, log ad_social_mypy_dash.log). Regression tests `dashboard/tests/test_mypy_ratchet.py` (floor, ceiling at/above, missing terminator, clean success line). verified by: ad_social_14.log (dashboard py_ci_shared tests + ratchet tests, the ratchet's 5 pass), a real run of the script: `Found 607 errors in 268 files (checked 317 source files)`, rc 0.]

- **Original id:** A-09
- **Where:** social `.pre-commit-config.yaml:669`
- **Finding:** `mypy_gate --min-files 150 dashboard \|\| true`: result and population floor both discarded; a no-op labelled as a gate.
- **Proposed fix:** Blocking with a baseline, or `advisory_warn`-style warn mode; not `\|\| true`.

### ADOPT-10 (Med) -- mypy_gate --min-files floors hand-set per repo with no recorded measurement; mlframe an...

**Disposition:** RESOLVED -- [mlframe, pyutilz: RESOLVED -- (mlframe, pyutilz part) floors derived from a measured count and held there by a test. pyutilz: mypy_gate checked 266 source files on 2026-09-25 (`Success: no issues found in 266 source files`), so `--min-files` moved 200 -> 250 in .pre-commit-config.yaml, .github/workflows/mypy-full.yml and scripts/prove_meta_checks.py, with the measurement in the comment; new tests/test_meta/test_mypy_gate_floor.py fails when the two venues disagree or the floor leaves 90-100% of the `.py` count under src/pyutilz. mlframe: the blocking pre-commit `mypy-full` hook ran bare `python -m mypy`; it now runs `python -m py_ci_shared.mypy_gate --min-files 1500 src/mlframe` (mypy_gate reported 1668 checked files, equal to the `.py` count under src/mlframe outside legacy/_benchmarks/benchmarks/profiling) and tests/test_meta/test_mypy_gate_floor.py holds the floor within 85-100% of that count. mlframe CI's mypy-full.yml is the shared reusable workflow, which takes no floor input. That mypy run found 1 real error the new blocking hook would hit: `src/mlframe/training/composite/ensemble/_oof_external.py:80` passed `inner.group_column` (`str | None`) to `_extract_groups(..., str)` behind a separate `getattr(...)` truth test that mypy cannot connect to it. Fixed by binding the column once and testing that binding; at runtime None never reached the call (the guard held), so this is a typing fix and needs no runtime test (origin/master made the identical change since c43b7fc; take origin's version on rebase). mypy on the file: Success. verified by: mypy_gate runs (logs ad_pyutilz_mypy, ad_mlframe_mypy); both floor tests pass (ad_mlframe_13, ad_pyutilz_8).] [social, dash_app_core, llm_bench, glossum: RESOLVED -- floors measured and recorded next to each call: social production_scrapers 417 files -> `--min-files 375`, realtime_applications 463 -> `415` (logs ad_social_mypy_production_scrapers.log / _realtime_applications.log, both "Success: no issues"), dashboard 315 -> ratchet floor 280 (ADOPT-9); glossum `glossum` 437 files -> `--min-files 390` (was 250, comment said 311; ad_glossum_mypy.log). llm_bench and dash_app_core run whole-project blocking mypy (llm_bench `mypy-full.yml` `advisory: false`, dash_app_core `mypy src/` in CI and pre-commit); mypy itself errors on an empty target, so no floor is needed there. verified by: the mypy runs above.]

- **Original id:** A-10
- **Where:** autopsia `.pre-commit-config.yaml:69` (`--min-files 1200`), glossum `:295` (250), pyutilz `:323` + `.github/workflows/mypy-full.yml:70` (200), social `:452` (250), `:1011` (280), `:669` (150)
- **Finding:** `mypy_gate --min-files` floors hand-set per repo with no recorded measurement; mlframe and llm_bench use `mypy-full.yml` only, dash_app_core has neither.
- **Proposed fix:** Derive floors from a committed count, or document the measurement at each call; wire mypy_gate uniformly.

### ADOPT-11 (Med) -- One job installs the package at a49421c (152 behind) but clones unpinned master for con...

**Disposition:** RESOLVED -- [social, dash_app_core, llm_bench, glossum: RESOLVED -- llm_bench `.github/workflows/ci.yml` lint job: the `git clone` of py-ci-shared master is gone; the step installs the pinned package and sets `PY_CI_SHARED_DIR` from `py-ci-shared config-path ruff-base` (verified locally: prints `<pkg>\configs\ruff-base.toml`); the black step reuses that install instead of `@a49421c`; `uvx ruff` pinned to 0.16.1. One SHA for config and gate code. verified by: `python -m py_ci_shared.cli config-path ruff-base`, yamllint clean.]

- **Original id:** A-11
- **Where:** llm_bench `.github/workflows/ci.yml:119` vs `:141`
- **Finding:** One job installs the package at `a49421c` (152 behind) but clones unpinned master for configs: ruff base config and gate code from different versions.
- **Proposed fix:** One SHA for both.

### ADOPT-12 (Med) -- CI installs @v1 (moving tag) while declared deps pin f103a36: local and CI run differen...

**Disposition:** RESOLVED -- [social, dash_app_core, llm_bench, glossum: RESOLVED -- social: `production-scrapers-ci.yml` (two `pip install ...@v1`), both `git clone --branch v1` config steps (production-scrapers, realtime-applications; now install the pin and resolve `PY_CI_SHARED_DIR` from `config-path`), the three `ruff-blocking.yml@v1` calls (plus `py-ci-shared-ref`), `production_scrapers/requirements.txt` and `realtime_applications/pyproject.toml` (were `f103a36`) all at the one pin; the "deliberately on a moving tag" comments now say the SHA pin is deliberate. verified by: grep over the repo finds no py-ci-shared ref without the SHA; yamllint clean.]

- **Original id:** A-12
- **Where:** social `production-scrapers-ci.yml:80,151,194`, `realtime-applications-ci.yml:120` vs `production_scrapers/requirements.txt:56`, `realtime_applications/pyproject.toml:426`
- **Finding:** CI installs `@v1` (moving tag) while declared deps pin `f103a36`: local and CI run different gate code.
- **Proposed fix:** One pin; CI installs from the declared requirement.

### ADOPT-13 (Med) -- Five different py-ci-shared SHAs in one repo (41cbadc, 64e2b6b, 915217a, 7195776, f5028...

**Disposition:** RESOLVED -- [mlframe, pyutilz: RESOLVED -- mlframe: every `uses: fingoldo/py-ci-shared/...` ref in .github/workflows (ci.yml, black-filtered.yml, codecov-full.yml, deep-nightly.yml, docs.yml, fs-benchmark-nightly.yml, macos-abort-probe.yml, mypy-full.yml, numba-coverage.yml; five former SHAs 41cbadc/a5f2154/64e2b6b/915217a/7195776/f50288e) now `@530b82e... # v1.17.0`, the misleading `# v1` / `# v1.4.0` comments replaced; `py-ci-shared-ref: <sha> # v1.17.0` added to every ruff-blocking/black-filtered/lint-advisory call; requirements-dev.txt and the pre-commit `rev:` (was v1.0.0) on the same SHA. New guard tests/test_meta/test_py_ci_shared_pin.py fails on a second SHA, a tag/branch ref, an unpinned requirement or a missing/disagreeing `# vX.Y.Z` comment, and checks the installed package includes the pin; verified by: the pin test's parser run over the tree (26 refs, one SHA, one tag).]

- **Original id:** A-13
- **Where:** mlframe `.github/workflows/ci.yml:259,416,502,633,639,673,690,697,703`, `docs.yml:35`, `mypy-full.yml:40`, `black-filtered.yml:32`, `deep-nightly.yml:85,179,245`, `codecov-full.yml:240`, `numba-coverage.yml:216`, `macos-abort-probe.yml:188`, `requirements-dev.txt:58`
- **Finding:** Five different py-ci-shared SHAs in one repo (41cbadc, 64e2b6b, 915217a, 7195776, f50288e); several commented `# v1` though 19-62 commits apart; `install-pyutilz@7195776` is commented `v1.4.0` but is v1.3.4+1.
- **Proposed fix:** One pin for every `uses:` ref and the requirement, bumped together; correct the comments. glossum's `test_ci_workflow_config.py:86` already checks ref consistency and could move into `ci_workflow_gate`.

### ADOPT-14 (Med) -- Two versions of the same upload-codecov action in one repo.

**Disposition:** RESOLVED -- [mlframe, pyutilz: RESOLVED -- pyutilz: upload-codecov in ci.yml (51d618a, v1.3.5) and codecov-full.yml / numba-coverage.yml (f50288e, v1.3.4) all on `@530b82e... # v1.17.0`; the rewritten tests/test_meta/test_shared_pin_covers_what_we_import.py enforces one SHA across all refs; verified by: the pin test's parser over the tree (11 refs, one SHA).]

- **Original id:** A-14
- **Where:** pyutilz `.github/workflows/ci.yml:104` (`51d618a`, v1.3.5) vs `codecov-full.yml:197` (`f50288e`, v1.3.4)
- **Finding:** Two versions of the same `upload-codecov` action in one repo.
- **Proposed fix:** Same pin.

### ADOPT-15 (Med) -- Gate package floats on master while workflows are ~165-173 commits old; tool_versions.R...

**Disposition:** RESOLVED -- [mlframe, pyutilz: RESOLVED -- (mlframe, pyutilz part) no floating package-vs-workflow split remains: both repos install py-ci-shared from requirements-dev.txt at the same SHA the workflows use, and `RUFF_VERSION` is read from that same commit (`py-ci-shared-ref` input). verified by: pin tests' parser over both trees.]

- **Original id:** A-15
- **Where:** algopacksimple `.github/workflows/ci.yml:82` (floating master) vs `ci.yml:120,124,133`, `black.yml:25` (v1.2.0-v1.3.0)
- **Finding:** Gate package floats on master while workflows are ~165-173 commits old; `tool_versions.RUFF_VERSION` and the workflow's ruff can disagree.
- **Proposed fix:** Pin package and workflows to one SHA.

### ADOPT-16 (Med) -- Pinned to v1.2.0 (~173 behind); no meta-test gates at all, only the three lint workflows.

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: RESOLVED -- ad1's part (claude-usage-notifier; claude_notifier belongs to another agent): `.github/workflows/ci.yml` ruff-blocking/lint-blocking/lint-advisory moved from `f26052f # v1.2.0` to the pin, with `py-ci-shared-ref` on the two workflows that take it; `pyproject.toml` gains `[project.optional-dependencies] dev` with the pinned py-ci-shared (its pre-commit `black-filtered-blocking` hook runs `python -m py_ci_shared.black_filtered_apply`) and `ruff==0.16.1`; ruff `extend` fixed as in ADOPT-20. The baseline gate set is not adopted there, for the reasons under ADOPT-27; verified by: `ruff check .` clean against the pinned base config, TOML/YAML parse of the edited files]

- **Original id:** A-16
- **Where:** claude-usage-notifier, claude_notifier `.github/workflows/ci.yml:38,44,52`
- **Finding:** Pinned to v1.2.0 (~173 behind); no meta-test gates at all, only the three lint workflows.
- **Proposed fix:** Bump; adopt the baseline set (A-27).

### ADOPT-17 (Med) -- Unpinned git installs of master: any py-ci-shared push can break or silently change the...

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: RESOLVED -- ad1's part (algopacksimple `ci.yml:82`, unpinned `git+...py-ci-shared.git`): the package is now declared once, in a PEP 735 `[dependency-groups] dev` in `pyproject.toml` (the repo has no `[project]` table), pinned to the SHA with `ruff==0.16.1`, and CI installs that group (`python -m pip install --group "$GITHUB_WORKSPACE/pyproject.toml:dev"`); every `uses:` ref moved from `@v1` to the SHA with `py-ci-shared-ref` where accepted; the pre-commit header's `pip install -e "../py-ci-shared[dev]"` sibling instruction replaced with `python -m pip install --group dev`; verified by: `pip install --dry-run --group pyproject.toml:dev`, YAML parse, yamllint clean, ad_algopacksimple_4.log] [mlframe, pyutilz: RESOLVED -- (pyutilz part) requirements-dev.txt was an unpinned `git+...py-ci-shared.git` (floating master) while CI and mypy-full.yml installed `@v1.16.1` inline and the reusable workflows ran `@v1`. Now one SHA everywhere, CI installs the declared requirement (`pip install -r requirements-dev.txt`), and `test_dev_requirements_git_dependencies_are_pinned_or_first_party` runs the central `assert_all_git_dependencies_pinned` on requirements-dev.txt in both repos with no first-party exemption (530b82e reads requirements files again; the interim per-line check is gone). verified by: pin tests' parser over both trees.] [social, dash_app_core, llm_bench, glossum: RESOLVED -- glossum `pyproject.toml` dev dep pinned and `uv.lock` regenerated with the repo's own tool (`uv lock --upgrade-package py-ci-shared`, uv 0.11.29: `py-ci-shared v0.1.0 (4e9c72c2) -> v1.17.0 (03cfff19)`, only that entry changed, orjson -> pyyaml in its deps; `uv lock --check` passes); glossum's workflows (black-filtered, mypy-beachhead, ruff-blocking, lint-blocking, lint-advisory) moved from `@v1` to the pin, and `test_ci_workflow_config.py` now requires that SHA and checks the dev dependency carries it. llm_bench `pyproject.toml` dev dep pinned; its git-pin test no longer exempts first-party URLs. verified by: ad_glossum_3.log (ci_workflow_config, dependency_pinning, workflow gates: 38 passed), `uv lock --check`, ad_llm_bench_4.log.] [flutter_app_core, polyvocab_app: RESOLVED -- flutter_app_core `.github/workflows/ci.yml:27` and polyvocab_app `.github/workflows/ci.yml:278` now install `py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared@530b82e8d7d995045bce0cb48f1e520250794935 # v1.17.0`; the install hint printed by the three gate scripts carries the same pinned line. No other py-ci-shared reference (requirements file, `uses:`, hook) exists in either repo. verified by: `grep -rn "fingoldo/py-ci-shared"` over both worktrees -> only the pinned SHA, no `@master`/`@v1`.]

- **Original id:** A-17
- **Where:** flutter_app_core `ci.yml:27`, polyvocab_app `ci.yml:278`, algopacksimple `ci.yml:82`, glossum `pyproject.toml:199`, llm_bench `pyproject.toml:65`, pyutilz `requirements-dev.txt:13`
- **Finding:** Unpinned git installs of master: any py-ci-shared push can break or silently change these repos' gates. `git_dependency_pins` is exactly this check, and glossum/llm_bench/pyutilz run it, so it either skips dev extras / requirements-dev or the entries are baselined.
- **Proposed fix:** Pin SHAs; make `git_dependency_pins` cover dev extras and requirements-dev files.

### ADOPT-18 (Low) -- The moving tag v1 is 20 commits behind master, so @v1 repos run older workflows than SH...

**Disposition:** RESOLVED -- v1.17.0 was tagged at 6a8e382 (the SHA every consumer pins) and v1 moved to it; release.yml now moves v1 with a RELEASE_TOKEN because GITHUB_TOKEN may not update a ref to a commit that changes workflows (CANARY-53).

- **Original id:** A-18
- **Where:** autopsia, dash_app_core, glossum, pyutilz, social (`@v1`)
- **Finding:** The moving tag `v1` is 20 commits behind master, so `@v1` repos run older workflows than SHA-pinned repos; the tag lags releases.
- **Proposed fix:** Move `v1` on every release, or pin SHAs everywhere.

### ADOPT-19 (Low) -- Local clone 4 commits behind origin/master; hooks in repos that use an editable sibling...

**Disposition:** RESOLVED -- the main py-ci-shared checkout was fast-forwarded to origin/master and its editable install refreshed (1.17.0 metadata, pytest11 plugin registered); the scheduled consumer-pins workflow (NEW-36) now reports stale pins daily.

- **Original id:** A-19
- **Where:** local `C:\Users\Admin\Machine learning\py-ci-shared`
- **Finding:** Local clone 4 commits behind origin/master; hooks in repos that use an editable sibling install (algopacksimple, social, noema_app) run stale gates.
- **Proposed fix:** Pull; extend `pinned_tool_versions` to compare the installed py-ci-shared version against the repo's pin.

### ADOPT-20 (Low) -- extend = "../py-ci-shared/configs/ruff-base.toml" sibling path; ruff errors on any mach...

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: RESOLVED -- flutter_uptime_monitor and claude-usage-notifier `pyproject.toml`: `extend = "../py-ci-shared/configs/ruff-base.toml"` -> `extend = "$PY_CI_SHARED_DIR/configs/ruff-base.toml"` (ruff cannot run `py-ci-shared config-path`; the env var is set by the ruff-blocking workflow in CI and by `python -m py_ci_shared.setup_env` locally, and the comment names `config-path` for pointing it at the installed package), and both gain a `dev` extra carrying the pinned py-ci-shared (which ships the config) and `ruff==0.16.1`; verified by: `PY_CI_SHARED_DIR=<pin>/src/py_ci_shared ruff check .` clean in both repos]

- **Original id:** A-20
- **Where:** flutter_uptime_monitor `pyproject.toml:18`
- **Finding:** `extend = "../py-ci-shared/configs/ruff-base.toml"` sibling path; ruff errors on any machine without that sibling. dash_app_core and autopsia use `$PY_CI_SHARED_DIR`.
- **Proposed fix:** Use `$PY_CI_SHARED_DIR`.

### ADOPT-21 (Low) -- Hook id pinned-tool-versions (python -m py_ci_shared.pinned_tool_versions) defined twic...

**Disposition:** RESOLVED -- [mlframe, pyutilz: RESOLVED -- mlframe .pre-commit-config.yaml: the first `pinned-tool-versions` local hook (short comment, no stages) removed; the one with the longer comment and `stages: [pre-commit]` kept, still ahead of every ruff hook; verified by: grep shows one `id: pinned-tool-versions`.]

- **Original id:** A-21
- **Where:** mlframe `.pre-commit-config.yaml:47` and `:112`
- **Finding:** Hook id `pinned-tool-versions` (`python -m py_ci_shared.pinned_tool_versions`) defined twice; runs twice per commit.
- **Proposed fix:** Remove one, keep the longer comment.

### ADOPT-22 (Med) -- Local copy of py_ci_shared.index_coverage (same class and 9 functions, cosmetic diffs o...

**Disposition:** RESOLVED -- [social, dash_app_core, llm_bench, glossum: RESOLVED -- social `realtime_applications/scripts/_index_coverage.py` deleted; `scripts/check_indexes.py` and `tests/test_audit_2026_09_08_index_coverage.py` import `py_ci_shared.index_coverage`. The AST diff showed the shared module is a strict superset (UNIQUE, INCLUDE, NULLS FIRST/LAST, same-target, casts), so nothing local was lost. verified by: `test_audit_2026_09_08_index_coverage.py` in ad_social_12.log (passed).]

- **Original id:** A-22
- **Where:** social `upwork/new_scraper/realtime_applications/scripts/_index_coverage.py` (used by `check_indexes.py:32`, `tests/test_audit_2026_09_08_index_coverage.py:33`)
- **Finding:** Local copy of `py_ci_shared.index_coverage` (same class and 9 functions, cosmetic diffs only), while `production_scrapers/scripts/check_schema_drift.py:54` imports the shared one. Fixes to one will not reach the other.
- **Proposed fix:** Import `py_ci_shared.index_coverage`; delete `_index_coverage.py`.

### ADOPT-23 (Med) -- Hand-copied scanner module, already drifted: flutter_app_core's _IMPORT regex (~line 29...

**Disposition:** RESOLVED -- [flutter_app_core, polyvocab_app: RESOLVED -- every scan that `py_ci_shared.dart_scanners` provides at 03cfff1 (painter-animation, repaint-isolation, hardcoded-ui-strings, tappable-semantics, non-directional-layout, parse-serialize-catch, provider-state-hygiene) was already delegated in both repos and stays delegated (now unguarded, see ADOPT-5). polyvocab_app's copy is brought level with flutter_app_core's fixed one: `_IMPORT` now matches `^(?:import|export)\s+'...'` (the C03-21 barrel-export fix) and the unused `_TEST_BODY` regex is removed. The two files now differ only in repo-specific configuration (generated-file prefixes, `_shared_files` excluding l10n, `repo_has_clock_seam`, polyvocab's `unused-l10n-keys` scan) and history comments. The eight scans py-ci-shared does not provide are listed under `## Scanners to upstream`; moving them needs a py-ci-shared change, after which both files reduce to noema-style wrappers. The 1.17.0 dart_scanners key change (content hashes) was refreshed once in both repos with every written note carried over (count-per-file identical, mapped positionally; flutter_app_core parse-serialize-catch had stale per-file ordinals, so its 4 notes were mapped by content to the jsonDecode sites they describe). verified by: import-cycles still 1 / 4 accepted and no new cycle with `export` counted; `python tool/check-baselined-rules.py` exit 0 in both repos against the pinned pcs (logs/ad_flutter_app_core_2.log, logs/ad_polyvocab_app_2.log); `diff` of the two scanners.py files shows only the repo-specific lines.]

- **Original id:** A-23
- **Where:** flutter_app_core `tool/meta/scanners.py` vs polyvocab_app `tool/meta/scanners.py` (~600 lines each)
- **Finding:** Hand-copied scanner module, already drifted: flutter_app_core's `_IMPORT` regex (~line 294) matches `import\|export` (audit C03-21 fix) and dedups test roots; polyvocab_app `:289` is import-only, so barrel-export cycles are invisible there. `py_ci_shared.import_layering:47` already parses both. noema_app (131 lines) is the thin-wrapper shape.
- **Proposed fix:** Move `scan_import_cycles` and the other generic scans into `py_ci_shared.dart_scanners`; reduce both files to noema-style wrappers.

### ADOPT-24 (Med) -- Same bug classes implemented twice with no cross-reference: additive_epsilon_denominato...

**Disposition:** DEFERRED -- [mlframe, pyutilz: DEFERRED -- mapping recorded, no scanner deleted. Canonical owner per pair, with the baseline entries that depend on the pyutilz side (counted 2026-09-25 in each repo's `_code_audit_baseline.json` / `_code_audit_tests_baseline.json`): `additive_epsilon_denominator` (default-on; mlframe src 69 entries) vs `epsilon_padded_denominators` (mlframe runs both): owner py-ci-shared, the pyutilz scanner stays while mlframe's 69 keys depend on it. `vacuous_loop_assertion` (opt-in, 0 entries) vs `vacuous_loop_assertions`: owner py-ci-shared; the pyutilz one is opt-in and unused, safe to retire. `nondiscriminating_test` (default-on; mlframe tests 1011, pyutilz tests 6) vs `nondiscriminating_shapes`: the pyutilz scanner stays canonical for the test-quality ratchet (1,017 keyed entries), py-ci-shared's is the per-shape zero-tolerance supplement. `source_text_assertion` (default-on; mlframe tests 261, pyutilz tests 1) vs `source_text_claims` (mlframe runs both): same split. `near_duplicate_function_body` + `duplicate_function_body` (pyutilz 1+4, mlframe 0+3) vs `drifted_duplicate_functions`: py-ci-shared owns drift, pyutilz keeps exact-duplicate detection. `reexport_patch_target` (opt-in, 0) + `patch_target_is_a_reexport` (0 entries) vs `inert_patch_targets`: owner py-ci-shared; neither pyutilz scanner has a dependent key, both can be retired. `undeclared_import` (pyutilz 5 entries) vs `unresolved_imports`: different rules (undeclared dependency vs unresolvable name), both kept. `comment_names_missing_symbol` (mlframe 6, pyutilz 5) + `stale_source_citation` (opt-in, 0) vs `phantom_code_references` and the new `stale_source_citations`: py-ci-shared owns citations (now zero-tolerance in both repos); the opt-in pyutilz `stale_source_citation` can be retired, `comment_names_missing_symbol` stays for its 11 keys. `getattr_literal_on_known_dataclass` (0 entries) vs `config_getattr_default_parity`: owner py-ci-shared. Deferred part: the per-pair hit-set diff on one corpus and making the pyutilz scanners delegate change pyutilz scanners in a way that re-keys every consumer's code-audit baseline (7 repos), so it needs its own coordinated round after 1.17.0 lands. verified by: registry.py `OPT_IN_ONLY` and `register_scanner` lists at the worktree head; key counts computed from the four baseline files.]

- **Original id:** A-24
- **Where:** pyutilz `src/pyutilz/dev/code_audit/*` vs py_ci_shared
- **Finding:** Same bug classes implemented twice with no cross-reference: `additive_epsilon_denominator`/`epsilon_padded_denominators`, `vacuous_loop_assertion`/`vacuous_loop_assertions`, `nondiscriminating_test`/`nondiscriminating_shapes`, `source_text_assertions`/`source_text_claims`, `near_duplicate_function_body`+`duplicate_function_body`/`drifted_duplicate_functions`, `reexport_patch_target`+`patch_target_is_a_reexport`/`inert_patch_targets`, `undeclared_imports`/`unresolved_imports`, `comment_names_missing_symbol`+`stale_source_citations`/`phantom_code_references`, `getattr_literal_on_known_dataclass`/(remote) `config_getattr_default_parity`. Rule equivalence NOT verified; these are independent implementations of the same class.
- **Proposed fix:** Per pair: run both on one corpus and diff the hit sets, pick an owner, make the other delegate.

### ADOPT-25 (Med) -- Modules adopted by no consumer: baseline_trend, checkpoint_isolation, config_drift_chec...

**Disposition:** NOT A DEFECT -- the zero-adoption modules (baseline_trend, checkpoint_isolation, config_drift_check, setup_env, sql_verify, vulture_warn) are opt-in tools for situations the consumers do not have; unused is not broken. adoption_matrix (INFRA-6) and the daily consumer-pins workflow now report module usage per repo, so a gate a repo needs but lacks is visible.

- **Original id:** A-25
- **Where:** all
- **Finding:** Modules adopted by no consumer: `baseline_trend`, `checkpoint_isolation`, `config_drift_check` (plus its `config-drift-check.yml`), `setup_env`, `sql_verify`, `vulture_warn` (referenced by `advisory_warn`, so possibly reached indirectly). Expected-unused tooling: `worktree_hygiene`, `install_safe_hook`, `safe_precommit`, `_mutation_worker` (internal to `mutation_teeth`). New on remote, unadopted: `env_flag_parsing`, `config_getattr_default_parity`, `survivorship_scoring`.
- **Proposed fix:** Wire `checkpoint_isolation` (mlframe), `sql_verify`/`config_drift_check` (glossum/social), `baseline_trend` (all ratchet users), or document why each is unused; label tooling modules in the README.

### ADOPT-26 (Med) -- 28 modules with exactly one consumer; generic ones worth rolling out: fail_open_handler...

**Disposition:** RESOLVED -- [mlframe, pyutilz: RESOLVED -- (mlframe, pyutilz part) adopted in both repos in tests/test_meta/test_shared_gates_adopted.py: numba_seed_range, sentinel_or_fallback, stale_source_citations, module_cache_thread_safety, swallowed_exceptions, no_xfail_to_defer, plus import_cycles (NEW-2) and, in pyutilz, module_reload_safety's production half. The generic single-consumer modules the finding lists (fail_open_handlers, nondiscriminating_shapes, unread_init_params, discarded_model_copy, runtime_registry_mutation, unresolved_imports) already run in mlframe; rolling them into pyutilz was not done in this round. verified by: gate runs in the ad_mlframe / ad_pyutilz logs.] [social, dash_app_core, llm_bench, glossum: RESOLVED -- per gate, these repos: dash_app_core `[tool.py_ci_shared]` in `pyproject.toml` (gate items under bare `pytest` in CI, `py-ci-shared-run-all` pre-commit hook): swallowed_exceptions, hash_key_determinism, sentinel_or_fallback, pickle_state_completeness, rollback_then_continue, atomic_write_staging, stale_source_citations, hardcoded_token_ceilings, machine_specific_paths, no_xfail_to_defer, clock_day_boundary, unread_init_params, discarded_model_copy, runtime_registry_mutation, unresolved_imports all 0; import_cycles 1 baselined with reason (a false cycle, U-3); fail_open_handlers over tests/scripts 0 (empty baseline). llm_bench: the same 15 at 0 except hardcoded_token_ceilings 1 (`Benchmark.preflight` `max_tokens=1024`, marked `# token-ceiling-ok`: a liveness ping whose truncation counts as alive), plus gate_integrity (11 narrowings declared with reasons) and coverage_gate_parity; CI runs them in the meta job (`py-ci-shared run-all`). glossum: `tests/test_meta/test_shared_static_gates.py` wires rollback_then_continue, pickle_state_completeness, swallowed_exceptions, hash_key_determinism, sentinel_or_fallback, atomic_write_staging, stale_source_citations, import_cycles, no_xfail_to_defer, clock_day_boundary, all 0 after the fixes under the live bugs below. Not applicable: env_example_round_trip (llm_bench has no pydantic Settings; dash_app_core has no .env.example), dataclass_case_completeness (needs a repo-chosen case list; glossum already uses it), nondiscriminating_shapes (a library with no gate entry). Skipped for cost as instructed: mutation_teeth, teeth_sweep. social part DEFERRED: its three projects already run 60+ shared gates and this pass went to the 1.17.0 breakages there (below); the generic set is the next social item. verified by: ad_dash_app_core_3.log (17 gate items), ad_llm_bench_5.log (18 gate items), ad_glossum_10.log (10 passed).] [flutter_app_core, polyvocab_app: NOT A DEFECT -- for flutter_app_core and polyvocab_app: the finding lists Python repos (mlframe, pyutilz, glossum, social, autopsia, llm_bench, dash_app_core) as roll-out targets, and every module named there scans Python source; neither Flutter repo has Python packages to point them at. verified by: no pyproject.toml / Python package in either worktree; module list read in 33_adoption.md.]

- **Original id:** A-26
- **Where:** see matrix
- **Finding:** 28 modules with exactly one consumer; generic ones worth rolling out: `fail_open_handlers`, `nondiscriminating_shapes`, `unread_init_params`, `discarded_model_copy`, `runtime_registry_mutation`, `unresolved_imports` (mlframe only); `mutation_teeth`, `teeth_sweep`, `ignore_ratchet`, `git_changed_lines`, `source_text_ban`, `config_call_site_parity`, `audit_path_references` (social only); `gate_population_canary`, `save_failure_markers`, `env_example_round_trip`, `dataclass_case_completeness` (glossum only). Legitimately narrow (DB/LLM/Supabase specific): `alembic_concurrently`, `sqlalchemy_text_binds`, `statement_compilation`, `db_transaction_completeness`, `embedded_postgres`, `index_coverage`, `sql_verifier_coverage`, `deletion_gates`, `llm_call_archive_gate`, `prompt_field_parity`, `edge_function_hygiene`.
- **Proposed fix:** Roll the generic ones out to mlframe, pyutilz, glossum, social, autopsia, llm_bench, dash_app_core.

### ADOPT-27 (Med) -- audit_round_format (required in every Python project) missing in pyutilz, llm_bench, da...

**Disposition:** DEFERRED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: DEFERRED -- ad1's part. algopacksimple: `pinned_tool_versions` wired as a pre-commit hook (ruff-pre-commit rev `v0.15.20` -> `v0.16.1`, `ruff==0.16.1` in the dev group; the tree is ruff-0.16.1 clean) and `python -m py_ci_shared.pinned_tool_versions` exits 0; `value_bearing_asserts`, `effect_assertion_parity` and `import_side_effects` (import-time env mutation) wired in the new root `test_shared_gates.py`, added to CI's explicit file list; value_bearing_asserts found 7 production asserts that `-O` strips, fixed (see "Live bugs"), effect parity and env mutation found 0 over 32 modules / 33 test files, so no baselines. `audit_round_format` is NOT wired in algopacksimple, claude-usage-notifier or flutter_uptime_monitor: none has an `audits/` directory or any dated round, and 1.17.0 fails an empty scan, so the gate would be red or vacuous; next action: wire it with the first round that lands. claude-usage-notifier: `effect_assertion_parity`, `import_side_effects` and `value_bearing_asserts` are not adopted: the repo has no test suite (CI is `compileall`), no `assert`, and every module imports `config`, which reads required secrets from the environment at import by design, so each gate would be vacuous or would fail on the missing `.env` rather than on a defect; next action: adopt them if a test suite is added. noema_app already runs `audit_round_format` via `tool/check-audit-rounds.py` (green on the pin); verified by: ad_algopacksimple_4.log (5 passed), ad_noema_app_check-audit-rounds.log] [mlframe, pyutilz: RESOLVED -- (pyutilz part) new tests/test_meta/test_audit_rounds_countable.py: every finding heading (`### F01.` / `### MT-3.`; ids are file-scoped, so the repo-wide uniqueness half of `assert_rounds_countable` does not apply and each file is checked with `finding_problems`) has a `**Disposition**`, the four inventory headings are named as prose, and `assert_rounds_filed` runs with a shrink-only baseline for the two 2026-07-21 rounds that predate trackers. Fixed the cp1252 em dashes in audits/implemented/2026-09-03/10-performance.md that made 1.17.0's reader fail the file. mlframe already wires audit_round_format (tracker statuses). verified by: 2 passed (ad_pyutilz_3).] [social, dash_app_core, llm_bench, glossum: RESOLVED -- audit_round_format: dash_app_core `tests/test_meta/test_audit_rounds_are_countable.py` (no `audits/` exists: the test asserts that state explicitly and that no dated round directory sits outside `audits/`; the first real round switches it to `assert_rounds_countable` + `assert_rounds_filed` unchanged); llm_bench `tests/test_meta/test_audit_rounds_are_countable.py` (its one round, `2026-07-22_full-audit`, has no per-finding dispositions and no tracker, so it is listed as known debt with that reason, shrink-only, and any new round must be countable; `assert_rounds_filed` with that one known entry). llm_bench also gets pinned_tool_versions (pre-commit hook; `ruff>=0.1` -> `ruff==0.16.1`) and gate_integrity (above). verified by: ad_llm_bench_4.log, ad_dash_app_core_4.log.] [flutter_app_core, polyvocab_app: NOT A DEFECT -- for flutter_app_core and polyvocab_app: neither is named in the finding's per-repo gap list, and both already run `audit_disposition_parity` over their `audits/` trees via check_shared_scanners.py. verified by: 33_adoption.md ADOPT-27 text; grep of both repos' tool/ scripts.]

- **Original id:** A-27
- **Where:** per-repo gaps in the widely adopted set
- **Finding:** `audit_round_format` (required in every Python project) missing in pyutilz, llm_bench, dash_app_core, algopacksimple, both claude notifiers; `effect_assertion_parity`, `import_side_effects`, `value_bearing_asserts` missing in algopacksimple and the notifiers; `code_audit_meta` and `loc_budget` missing in autopsia; `pinned_tool_versions` missing in llm_bench and algopacksimple; `gate_integrity` missing in llm_bench.
- **Proposed fix:** Adopt the common baseline set in each Python repo.

### ADOPT-28 (Low) -- autopsia calls lint-advisory but not lint-blocking; llm_bench and social call neither.

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: RESOLVED -- autopsia: new `.github/workflows/lint-blocking.yml` calling the reusable `lint-blocking.yml` at the pin (source `autopsia`, the pre-commit codespell path list, interrogate at the ratcheted 74.6 floor, zizmor on, deptry with `.[test,llm,dev]` after pyutilz), the CI twin of the blocking pre-commit hooks; lint-advisory kept; verified by: YAML parse; not run (Actions billing-blocked, ADOPT-30)] [social, dash_app_core, llm_bench, glossum: RESOLVED -- llm_bench `ci.yml` job `lint-blocking` calls the shared workflow at the pin (source `src/llm_bench`, codespell `src/llm_bench README.md`, interrogate floor 65 = its measured `[tool.interrogate]` value, deptry with the pyutilz install); checked locally first: bandit 0, vulture 0, interrogate 70.2% >= 65, and codespell failed on `truncat` (a deliberate substring in benchmark.py, a pre-existing red in the hygiene job too) -> added to `ignore-words-list`. social: RESOLVED for realtime_applications at 530b82e -- `realtime-applications-ci.yml` job `lint-blocking` with the new `working-directory`, `codespell-toml`, `bandit-config`, `bandit-exclude` inputs (same config/excludes as its pre-commit hooks); `[tool.vulture]` excludes added to its pyproject (vulture `.` then reports 0, 30 before), a repo-root `.yamllint` copied from the project's because the shared yamllint step runs from the root (default config fails the root files); locally bandit 0, vulture 0, codespell 0, interrogate 87.4% >= 80, yamllint 0. production_scrapers and dashboard stay DEFERRED: production_scrapers' vulture runs through its own baseline script (raw `vulture .` reports 54), and dashboard's bandit is advisory (`|| true`, 14 findings) by the repo's own choice. Earlier note, superseded: `lint-blocking.yml` runs every tool from the repo root with no working-directory, bandit-config or exclude input, and social has no root pyproject; measured locally against the three projects it would fail on bandit (107 / 29 / 14 issues without each project's `-c pyproject.toml` excludes), vulture (56 / 30 lines) and codespell (no root config). Each project already runs these tools blocking in pre-commit and realtime's CI lint job. verified by: the local tool runs above, yamllint.]

- **Original id:** A-28
- **Where:** autopsia, llm_bench, social `.github/workflows/*`
- **Finding:** autopsia calls lint-advisory but not lint-blocking; llm_bench and social call neither.
- **Proposed fix:** Add lint-blocking (and lint-advisory) for parity.

### ADOPT-29 (Low) -- Stale second clone of autopsia (2026-09-08, 15 days behind Redline/autopsia); greps and...

**Disposition:** RESOLVED -- the owner approved deleting the stale clone; re-verified clean (no changes, stash or unpushed commits; HEAD contained in Redline/autopsia's origin; only caches ignored) and removed Machine learning/autopsia on 2026-09-25. Agent's verification: [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: WON'T FIX -- verified stale and safe, left in place for the owner: `C:\Users\Admin\Machine learning\autopsia` is at `ad16b72b` (2026-09-08), `git status --porcelain` empty, no stash, one branch `master` 75 behind its own `origin/master` with nothing ahead (`git log --branches --not --remotes` empty), `ad16b72b` is contained in `origin/master` and is an ancestor of `Redline/autopsia` HEAD `ed27ed51`; ignored content is only `__pycache__`/`.pytest_cache`. The editable install resolves to `Redline/autopsia` (`pip show autopsia`), so imports do not hit the stale clone; greps under `Machine learning\` still can. Recommendation to the owner: delete or rename it (e.g. `autopsia.stale-2026-09-08`); nothing would be lost. Not deleted, per the brief; verified by: the read-only git commands above]

- **Original id:** A-29
- **Where:** `C:\Users\Admin\Machine learning\autopsia`
- **Finding:** Stale second clone of autopsia (2026-09-08, 15 days behind `Redline/autopsia`); greps and editable installs can hit it.
- **Proposed fix:** Remove or rename after confirming no unpushed work.

### ADOPT-30 (Info) -- Jobs are not starting: "recent account payments have failed or your spending limit need...

**Disposition:** RESOLVED -- GitHub Actions runs again on py-ci-shared (self-CI green on 6a8e382 across Python 3.9-3.13, Windows and macOS); the billing block was lifted outside this round.

- **Original id:** A-30
- **Where:** GitHub Actions (seen on dash_app_core run for 8b779da)
- **Finding:** Jobs are not starting: "recent account payments have failed or your spending limit needs to be increased". None of the CI-side wiring bugs (A-01, A-03) can surface as red builds until this clears.
- **Proposed fix:** Resolve billing, then expect A-01/A-03 to fail.
