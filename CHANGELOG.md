# Changelog

Milestones only; the commit log has the detail. Versions are the release tags (`vX.Y.Z`).

## 1.17.0 (unreleased)

Stricter by default. Every item below can turn a green consumer run red on upgrade, each for a defect that was
being passed silently. The [README](README.md#baselines-refresh-and-what-now-fails) lists the affected gates.

- **A missing baseline fails.** A baselined gate no longer seeds its baseline on first run and passes; it fails
  and names its refresh command. Refresh once with `pytest --py-ci-refresh=<gate>`,
  `PY_CI_SHARED_REFRESH=<gate>` or `py-ci-shared refresh <gate>`. A refresh never writes after a failed floor or an
  unparsable file, and a `NEEDS-JUSTIFICATION` note fails until someone writes the reason.
- **An unparsable or unreadable file is a finding.** AST gates read sources the way the interpreter does (BOM,
  PEP 263 cookie) and report a file they cannot parse instead of skipping it. `allow_unparsed=True` exists where
  that is expected.
- **An empty scan fails.** File floors count parsed files; a missing root raises. Gates that had no floor now take
  `min_files=1` (`module_reload_safety`, `vacuous_loop_assertions`, `config_call_site_parity`, `unresolved_imports`,
  `phantom_markdown_links`, `pytest_markers`, `env_flag_parsing`).
- `git_dependency_pins` parses the TOML and checks every dependency string (single-line arrays, extras, groups,
  build requires); invalid TOML fails. `prompt_field_parity.string_literals` is strict by default, and the
  `llm_call_archive_gate` finders raise on unparsable files.
- **orjson is no longer a dependency**: baselines go through the stdlib `json` module (`_core.load_json` /
  `dump_json`), with the on-disk format unchanged.
- **Refresh works under pytest-xdist.** Refresh requests travel through the `PY_CI_SHARED_REFRESH` environment
  variable, which workers inherit, instead of `sys.argv`, which they do not.
- **Some baseline and allowlist keys changed** (`alembic_concurrently`, `dart_scanners`, `marker_runner_coverage`,
  `value_bearing_asserts`, `db_transaction_completeness`, `fail_open_handlers`, `discarded_model_copy`,
  `gate_integrity`): refresh or rewrite those once.
- `mutation_teeth` fails on a scope with zero mutants unless `allow_empty=True` (`HARNESS_VERSION` 11);
  `teeth_sweep` reports unrunnable cases as `ERRORED`; `worktree_hygiene` never calls staged or ignored work
  `REMOVABLE`.

New:

- `[tool.py_ci_shared]` in a consumer's `pyproject.toml` enables gates with their arguments. The package now
  ships a pytest plugin (`pytest11` entry point `py_ci_shared`) that turns each into a test item, times it
  against its runtime budget and adds `--py-ci-refresh`. A repo without the table sees no change.
- The `py-ci-shared` command: `run`, `run-all`, `refresh`, `list`, `config-path`, `tool`.
- `py_ci_shared.registry` lists every module; the README gate catalogue is generated from it.
- The ruff configs ship as package data (`py-ci-shared config-path ruff-base`).
- Pre-commit hooks for `run-all`, `pinned_tool_versions`, `mypy_gate` and `worktree_hygiene`.
- New gates: `atomic_write_staging`, `clock_day_boundary`, `coverage_config_parity`, `hash_key_determinism`,
  `import_cycles`, `local_copy_report`, `no_xfail_to_defer`, `numba_seed_range`, `pickle_state_completeness`
  (static, plus the runtime `assert_pickle_round_trips`), `plotly_annotation_loop`, `polars_null_equality` (advisory),
  `pytest_addopts_path_runs`, `reiterated_iterable_params`, `rollback_then_continue`, `sentinel_or_fallback`,
  `stale_source_citations`, `stdlib_json_ban` (opt-in), `swallowed_exceptions`, and the opt-in pytest plugin
  `resource_leak_guard` (`resource_leak_guard = true` in `[tool.py_ci_shared]`).

Release and CI:

- The package version is the release version (it was `0.1.0` through `v1.16.1`). `release.yml` checks the tag
  against it, then moves `v1` to the new tag.
- The reusable workflows fetch configs, `RUFF_VERSION` and the package at their own release
  (`py-ci-shared-ref` input), not at master. They declare `permissions: contents: read`, and lint-advisory's
  tools are installed at exact versions.
- `install-pyutilz` defaults to a pinned pyutilz commit instead of the branch tip.
- This repo's CI tests Python 3.9 to 3.13, installs the dev extra without a fallback, measures coverage and runs
  its own gates on itself with `py-ci-shared run-all`.
