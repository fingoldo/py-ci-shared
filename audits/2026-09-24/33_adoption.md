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

### Reusable workflows / composite actions

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

**Disposition:** OPEN

- **Original id:** A-01
- **Where:** Redline/autopsia `.github/workflows/ci.yml:42`, `tests/conftest.py:58-59`
- **Finding:** CI installs `.[test,llm]` only; py-ci-shared is in `[dev]`, so it is never installed. `pytest_addoption` imports `py_ci_shared.content_hash_version_bump_gate` / `uncalled_functions` unconditionally, so the CI test job cannot start, and 11 test files import py_ci_shared at module level. Masked right now because Actions jobs are billing-blocked (A-30).
- **Proposed fix:** Put a git-pinned py-ci-shared into the `test` extra (or a CI install step) at the same SHA as the workflows.

### ADOPT-2 (High) -- [dev] lists bare "py-ci-shared" with no direct URL

**Disposition:** OPEN

- **Original id:** A-02
- **Where:** Redline/autopsia `pyproject.toml:118,691`
- **Finding:** `[dev]` lists bare `"py-ci-shared"` with no direct URL. The name is not on PyPI (`pip index versions` finds nothing), so `pip install -e .[dev]` fails on a clean machine, and anyone registering that name on PyPI would be installed (dependency confusion).
- **Proposed fix:** `py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@<sha>`.

### ADOPT-3 (High) -- py-ci-shared is not a dependency, yet tests/test_meta/test_no_top_level_side_effects.py...

**Disposition:** OPEN

- **Original id:** A-03
- **Where:** dash_app_core `pyproject.toml:51-56`, `.github/workflows/ci.yml:43`
- **Finding:** py-ci-shared is not a dependency, yet `tests/test_meta/test_no_top_level_side_effects.py:13` and `test_the_suite_tests_this_checkout.py:23` import it at module level (collection error in CI), `test_code_audit_baseline.py:24` and `test_shared_checks_wired.py:208` `importorskip` it (silent skip), `conftest.py:18-21` swallows ImportError.
- **Proposed fix:** Add pinned py-ci-shared to `[dev]`; drop the importorskip/except once installed.

### ADOPT-4 (High) -- On ImportError these gates print "SKIPPED" and sys.exit(0): a missing install reads as...

**Disposition:** OPEN

- **Original id:** A-04
- **Where:** polyvocab_app `tool/check_shared_scanners.py:~50-54`, `scripts/check_l10n.py:60-64`; flutter_app_core `tool/check_shared_scanners.py:45-49`
- **Finding:** On ImportError these gates print "SKIPPED" and `sys.exit(0)`: a missing install reads as a pass. noema_app's equivalents (`tool/check-arb.py:53-58`, `check-audit-rounds.py:31-36`, `check-baselined-rules.py`) correctly `return 1`.
- **Proposed fix:** Exit 1 like noema_app.

### ADOPT-5 (High) -- _SHARED_AVAILABLE=False on ImportError silently drops 7 Dart scans from SCANS; the ratc...

**Disposition:** OPEN

- **Original id:** A-05
- **Where:** flutter_app_core `tool/meta/scanners.py:521-523,589`; polyvocab_app `tool/meta/scanners.py:525-527,587`
- **Finding:** `_SHARED_AVAILABLE=False` on ImportError silently drops 7 Dart scans from `SCANS`; the ratchet then runs fewer rules and still passes.
- **Proposed fix:** Fail on import failure (noema_app's `tool/meta/scanners.py` imports unguarded).

### ADOPT-6 (Med) -- collect_ignore of the meta tests whenever import py_ci_shared fails

**Disposition:** OPEN

- **Original id:** A-06
- **Where:** pyutilz `tests/conftest.py:9-16`
- **Finding:** `collect_ignore` of the meta tests whenever `import py_ci_shared` fails. Justified for the 3.8 legs, but applied on every interpreter, so a broken install on 3.9+ silently drops those gates.
- **Proposed fix:** Condition on `sys.version_info < (3, 9)`; fail otherwise.

### ADOPT-7 (Med) -- pytest.importorskip("py_ci_shared..."): whole gate files turn SKIPPED when the package...

**Disposition:** OPEN

- **Original id:** A-07
- **Where:** mlframe `tests/test_meta/test_shared_checks_wired.py:26`, `test_shared_uncalled_functions.py:23`, `test_discovery_algo_version_bumped.py:16`, `test_code_audit_tests_baseline.py:37`; pyutilz `tests/test_meta/test_shared_checks_wired.py:18`, `test_gate_integrity.py:31`, `test_docs_inventory_parity.py:24`, `test_prose_numeric_claims.py:26`, `test_gpu_timing_synchronize.py:27`, `test_code_audit_baseline.py:319`, `test_code_audit_tests_baseline.py:112`, `tests/test_typing_lint_hygiene_audit_20260903.py:184,195`; glossum `tests/test_meta/test_shared_effect_assertion_parity.py:27`; llm_bench same file `:28`; autopsia `tests/test_meta/test_unasserted_effects.py:71`; social `upwork/new_scraper/{dashboard/tests,production_scrapers/tests/test_meta,realtime_applications/tests/test_meta}/test_unasserted_effects.py:69`; dash_app_core (A-03)
- **Finding:** `pytest.importorskip("py_ci_shared...")`: whole gate files turn SKIPPED when the package is absent or too old for the module (the typical pin-lag case). Inconsistent with glossum `test_dependency_pinning.py:203-206`, which `pytest.fail`s on the same condition.
- **Proposed fix:** One shared helper (e.g. `py_ci_shared.require(module)`) that fails under `CI=true` and skips only locally; replace every importorskip site.

### ADOPT-8 (Med) -- Black-pin cross-check reads ../py-ci-shared/.github/workflows/black-filtered.yml from a...

**Disposition:** OPEN

- **Original id:** A-08
- **Where:** glossum_backend_scripts `tests/test_meta/test_dependency_pinning.py:212-214`
- **Finding:** Black-pin cross-check reads `../py-ci-shared/.github/workflows/black-filtered.yml` from a sibling checkout and skips if absent, so it always skips in CI, and locally it compares against whatever the sibling is at, not the pin.
- **Proposed fix:** Export `BLACK_VERSION` from `py_ci_shared.tool_versions` next to `RUFF_VERSION` and read that.

### ADOPT-9 (Med) -- mypy_gate --min-files 150 dashboard \/\/ true: result and population floor both discard...

**Disposition:** OPEN

- **Original id:** A-09
- **Where:** social `.pre-commit-config.yaml:669`
- **Finding:** `mypy_gate --min-files 150 dashboard \|\| true`: result and population floor both discarded; a no-op labelled as a gate.
- **Proposed fix:** Blocking with a baseline, or `advisory_warn`-style warn mode; not `\|\| true`.

### ADOPT-10 (Med) -- mypy_gate --min-files floors hand-set per repo with no recorded measurement; mlframe an...

**Disposition:** OPEN

- **Original id:** A-10
- **Where:** autopsia `.pre-commit-config.yaml:69` (`--min-files 1200`), glossum `:295` (250), pyutilz `:323` + `.github/workflows/mypy-full.yml:70` (200), social `:452` (250), `:1011` (280), `:669` (150)
- **Finding:** `mypy_gate --min-files` floors hand-set per repo with no recorded measurement; mlframe and llm_bench use `mypy-full.yml` only, dash_app_core has neither.
- **Proposed fix:** Derive floors from a committed count, or document the measurement at each call; wire mypy_gate uniformly.

### ADOPT-11 (Med) -- One job installs the package at a49421c (152 behind) but clones unpinned master for con...

**Disposition:** OPEN

- **Original id:** A-11
- **Where:** llm_bench `.github/workflows/ci.yml:119` vs `:141`
- **Finding:** One job installs the package at `a49421c` (152 behind) but clones unpinned master for configs: ruff base config and gate code from different versions.
- **Proposed fix:** One SHA for both.

### ADOPT-12 (Med) -- CI installs @v1 (moving tag) while declared deps pin f103a36: local and CI run differen...

**Disposition:** OPEN

- **Original id:** A-12
- **Where:** social `production-scrapers-ci.yml:80,151,194`, `realtime-applications-ci.yml:120` vs `production_scrapers/requirements.txt:56`, `realtime_applications/pyproject.toml:426`
- **Finding:** CI installs `@v1` (moving tag) while declared deps pin `f103a36`: local and CI run different gate code.
- **Proposed fix:** One pin; CI installs from the declared requirement.

### ADOPT-13 (Med) -- Five different py-ci-shared SHAs in one repo (41cbadc, 64e2b6b, 915217a, 7195776, f5028...

**Disposition:** OPEN

- **Original id:** A-13
- **Where:** mlframe `.github/workflows/ci.yml:259,416,502,633,639,673,690,697,703`, `docs.yml:35`, `mypy-full.yml:40`, `black-filtered.yml:32`, `deep-nightly.yml:85,179,245`, `codecov-full.yml:240`, `numba-coverage.yml:216`, `macos-abort-probe.yml:188`, `requirements-dev.txt:58`
- **Finding:** Five different py-ci-shared SHAs in one repo (41cbadc, 64e2b6b, 915217a, 7195776, f50288e); several commented `# v1` though 19-62 commits apart; `install-pyutilz@7195776` is commented `v1.4.0` but is v1.3.4+1.
- **Proposed fix:** One pin for every `uses:` ref and the requirement, bumped together; correct the comments. glossum's `test_ci_workflow_config.py:86` already checks ref consistency and could move into `ci_workflow_gate`.

### ADOPT-14 (Med) -- Two versions of the same upload-codecov action in one repo.

**Disposition:** OPEN

- **Original id:** A-14
- **Where:** pyutilz `.github/workflows/ci.yml:104` (`51d618a`, v1.3.5) vs `codecov-full.yml:197` (`f50288e`, v1.3.4)
- **Finding:** Two versions of the same `upload-codecov` action in one repo.
- **Proposed fix:** Same pin.

### ADOPT-15 (Med) -- Gate package floats on master while workflows are ~165-173 commits old; tool_versions.R...

**Disposition:** OPEN

- **Original id:** A-15
- **Where:** algopacksimple `.github/workflows/ci.yml:82` (floating master) vs `ci.yml:120,124,133`, `black.yml:25` (v1.2.0-v1.3.0)
- **Finding:** Gate package floats on master while workflows are ~165-173 commits old; `tool_versions.RUFF_VERSION` and the workflow's ruff can disagree.
- **Proposed fix:** Pin package and workflows to one SHA.

### ADOPT-16 (Med) -- Pinned to v1.2.0 (~173 behind); no meta-test gates at all, only the three lint workflows.

**Disposition:** OPEN

- **Original id:** A-16
- **Where:** claude-usage-notifier, claude_notifier `.github/workflows/ci.yml:38,44,52`
- **Finding:** Pinned to v1.2.0 (~173 behind); no meta-test gates at all, only the three lint workflows.
- **Proposed fix:** Bump; adopt the baseline set (A-27).

### ADOPT-17 (Med) -- Unpinned git installs of master: any py-ci-shared push can break or silently change the...

**Disposition:** OPEN

- **Original id:** A-17
- **Where:** flutter_app_core `ci.yml:27`, polyvocab_app `ci.yml:278`, algopacksimple `ci.yml:82`, glossum `pyproject.toml:199`, llm_bench `pyproject.toml:65`, pyutilz `requirements-dev.txt:13`
- **Finding:** Unpinned git installs of master: any py-ci-shared push can break or silently change these repos' gates. `git_dependency_pins` is exactly this check, and glossum/llm_bench/pyutilz run it, so it either skips dev extras / requirements-dev or the entries are baselined.
- **Proposed fix:** Pin SHAs; make `git_dependency_pins` cover dev extras and requirements-dev files.

### ADOPT-18 (Low) -- The moving tag v1 is 20 commits behind master, so @v1 repos run older workflows than SH...

**Disposition:** OPEN

- **Original id:** A-18
- **Where:** autopsia, dash_app_core, glossum, pyutilz, social (`@v1`)
- **Finding:** The moving tag `v1` is 20 commits behind master, so `@v1` repos run older workflows than SHA-pinned repos; the tag lags releases.
- **Proposed fix:** Move `v1` on every release, or pin SHAs everywhere.

### ADOPT-19 (Low) -- Local clone 4 commits behind origin/master; hooks in repos that use an editable sibling...

**Disposition:** OPEN

- **Original id:** A-19
- **Where:** local `C:\Users\Admin\Machine learning\py-ci-shared`
- **Finding:** Local clone 4 commits behind origin/master; hooks in repos that use an editable sibling install (algopacksimple, social, noema_app) run stale gates.
- **Proposed fix:** Pull; extend `pinned_tool_versions` to compare the installed py-ci-shared version against the repo's pin.

### ADOPT-20 (Low) -- extend = "../py-ci-shared/configs/ruff-base.toml" sibling path; ruff errors on any mach...

**Disposition:** OPEN

- **Original id:** A-20
- **Where:** flutter_uptime_monitor `pyproject.toml:18`
- **Finding:** `extend = "../py-ci-shared/configs/ruff-base.toml"` sibling path; ruff errors on any machine without that sibling. dash_app_core and autopsia use `$PY_CI_SHARED_DIR`.
- **Proposed fix:** Use `$PY_CI_SHARED_DIR`.

### ADOPT-21 (Low) -- Hook id pinned-tool-versions (python -m py_ci_shared.pinned_tool_versions) defined twic...

**Disposition:** OPEN

- **Original id:** A-21
- **Where:** mlframe `.pre-commit-config.yaml:47` and `:112`
- **Finding:** Hook id `pinned-tool-versions` (`python -m py_ci_shared.pinned_tool_versions`) defined twice; runs twice per commit.
- **Proposed fix:** Remove one, keep the longer comment.

### ADOPT-22 (Med) -- Local copy of py_ci_shared.index_coverage (same class and 9 functions, cosmetic diffs o...

**Disposition:** OPEN

- **Original id:** A-22
- **Where:** social `upwork/new_scraper/realtime_applications/scripts/_index_coverage.py` (used by `check_indexes.py:32`, `tests/test_audit_2026_09_08_index_coverage.py:33`)
- **Finding:** Local copy of `py_ci_shared.index_coverage` (same class and 9 functions, cosmetic diffs only), while `production_scrapers/scripts/check_schema_drift.py:54` imports the shared one. Fixes to one will not reach the other.
- **Proposed fix:** Import `py_ci_shared.index_coverage`; delete `_index_coverage.py`.

### ADOPT-23 (Med) -- Hand-copied scanner module, already drifted: flutter_app_core's _IMPORT regex (~line 29...

**Disposition:** OPEN

- **Original id:** A-23
- **Where:** flutter_app_core `tool/meta/scanners.py` vs polyvocab_app `tool/meta/scanners.py` (~600 lines each)
- **Finding:** Hand-copied scanner module, already drifted: flutter_app_core's `_IMPORT` regex (~line 294) matches `import\|export` (audit C03-21 fix) and dedups test roots; polyvocab_app `:289` is import-only, so barrel-export cycles are invisible there. `py_ci_shared.import_layering:47` already parses both. noema_app (131 lines) is the thin-wrapper shape.
- **Proposed fix:** Move `scan_import_cycles` and the other generic scans into `py_ci_shared.dart_scanners`; reduce both files to noema-style wrappers.

### ADOPT-24 (Med) -- Same bug classes implemented twice with no cross-reference: additive_epsilon_denominato...

**Disposition:** OPEN

- **Original id:** A-24
- **Where:** pyutilz `src/pyutilz/dev/code_audit/*` vs py_ci_shared
- **Finding:** Same bug classes implemented twice with no cross-reference: `additive_epsilon_denominator`/`epsilon_padded_denominators`, `vacuous_loop_assertion`/`vacuous_loop_assertions`, `nondiscriminating_test`/`nondiscriminating_shapes`, `source_text_assertions`/`source_text_claims`, `near_duplicate_function_body`+`duplicate_function_body`/`drifted_duplicate_functions`, `reexport_patch_target`+`patch_target_is_a_reexport`/`inert_patch_targets`, `undeclared_imports`/`unresolved_imports`, `comment_names_missing_symbol`+`stale_source_citations`/`phantom_code_references`, `getattr_literal_on_known_dataclass`/(remote) `config_getattr_default_parity`. Rule equivalence NOT verified; these are independent implementations of the same class.
- **Proposed fix:** Per pair: run both on one corpus and diff the hit sets, pick an owner, make the other delegate.

### ADOPT-25 (Med) -- Modules adopted by no consumer: baseline_trend, checkpoint_isolation, config_drift_chec...

**Disposition:** OPEN

- **Original id:** A-25
- **Where:** all
- **Finding:** Modules adopted by no consumer: `baseline_trend`, `checkpoint_isolation`, `config_drift_check` (plus its `config-drift-check.yml`), `setup_env`, `sql_verify`, `vulture_warn` (referenced by `advisory_warn`, so possibly reached indirectly). Expected-unused tooling: `worktree_hygiene`, `install_safe_hook`, `safe_precommit`, `_mutation_worker` (internal to `mutation_teeth`). New on remote, unadopted: `env_flag_parsing`, `config_getattr_default_parity`, `survivorship_scoring`.
- **Proposed fix:** Wire `checkpoint_isolation` (mlframe), `sql_verify`/`config_drift_check` (glossum/social), `baseline_trend` (all ratchet users), or document why each is unused; label tooling modules in the README.

### ADOPT-26 (Med) -- 28 modules with exactly one consumer; generic ones worth rolling out: fail_open_handler...

**Disposition:** OPEN

- **Original id:** A-26
- **Where:** see matrix
- **Finding:** 28 modules with exactly one consumer; generic ones worth rolling out: `fail_open_handlers`, `nondiscriminating_shapes`, `unread_init_params`, `discarded_model_copy`, `runtime_registry_mutation`, `unresolved_imports` (mlframe only); `mutation_teeth`, `teeth_sweep`, `ignore_ratchet`, `git_changed_lines`, `source_text_ban`, `config_call_site_parity`, `audit_path_references` (social only); `gate_population_canary`, `save_failure_markers`, `env_example_round_trip`, `dataclass_case_completeness` (glossum only). Legitimately narrow (DB/LLM/Supabase specific): `alembic_concurrently`, `sqlalchemy_text_binds`, `statement_compilation`, `db_transaction_completeness`, `embedded_postgres`, `index_coverage`, `sql_verifier_coverage`, `deletion_gates`, `llm_call_archive_gate`, `prompt_field_parity`, `edge_function_hygiene`.
- **Proposed fix:** Roll the generic ones out to mlframe, pyutilz, glossum, social, autopsia, llm_bench, dash_app_core.

### ADOPT-27 (Med) -- audit_round_format (required in every Python project) missing in pyutilz, llm_bench, da...

**Disposition:** OPEN

- **Original id:** A-27
- **Where:** per-repo gaps in the widely adopted set
- **Finding:** `audit_round_format` (required in every Python project) missing in pyutilz, llm_bench, dash_app_core, algopacksimple, both claude notifiers; `effect_assertion_parity`, `import_side_effects`, `value_bearing_asserts` missing in algopacksimple and the notifiers; `code_audit_meta` and `loc_budget` missing in autopsia; `pinned_tool_versions` missing in llm_bench and algopacksimple; `gate_integrity` missing in llm_bench.
- **Proposed fix:** Adopt the common baseline set in each Python repo.

### ADOPT-28 (Low) -- autopsia calls lint-advisory but not lint-blocking; llm_bench and social call neither.

**Disposition:** OPEN

- **Original id:** A-28
- **Where:** autopsia, llm_bench, social `.github/workflows/*`
- **Finding:** autopsia calls lint-advisory but not lint-blocking; llm_bench and social call neither.
- **Proposed fix:** Add lint-blocking (and lint-advisory) for parity.

### ADOPT-29 (Low) -- Stale second clone of autopsia (2026-09-08, 15 days behind Redline/autopsia); greps and...

**Disposition:** OPEN

- **Original id:** A-29
- **Where:** `C:\Users\Admin\Machine learning\autopsia`
- **Finding:** Stale second clone of autopsia (2026-09-08, 15 days behind `Redline/autopsia`); greps and editable installs can hit it.
- **Proposed fix:** Remove or rename after confirming no unpushed work.

### ADOPT-30 (Info) -- Jobs are not starting: "recent account payments have failed or your spending limit need...

**Disposition:** OPEN

- **Original id:** A-30
- **Where:** GitHub Actions (seen on dash_app_core run for 8b779da)
- **Finding:** Jobs are not starting: "recent account payments have failed or your spending limit needs to be increased". None of the CI-side wiring bugs (A-01, A-03) can surface as red builds until this clears.
- **Proposed fix:** Resolve billing, then expect A-01/A-03 to fail.
