# Changelog

Milestones only; the commit log has the detail. Versions are the release tags (`vX.Y.Z`).

## 1.18.0 (unreleased)

- `uncalled_functions` follows import aliases across files (a re-export module `from ._impl import _h as h`, loaded by a
  consumer as `from pkg.shared import h as _probe`), and a name a function imports locally stays the imported function
  even when an `except ImportError: f = None` fallback also assigns it. Both were reported as dead code.
- `content_hash_version_bump_gate` keys its history by `str(version)`: an int version constant crashed the re-pin in
  `json.dump(sort_keys=True)` and never matched the rule that refuses reusing an old version for new content.
- **Gates walk each tree once.** `_core.nodes_of(tree, *types)` returns a tree's nodes of those types in `ast.walk` order from one
  cached walk, and `_core.tree_memo` keeps any per-tree derived value; both live as long as the parse cache. `ImportAliases.from_tree`,
  `unresolved_imports` and `marker_runner_coverage` use them, and every gate walks with `_core.walk` (the `ast.walk` order, about 1.5x
  faster). On mlframe's meta suite: the marker check 70 s -> 29 s for the first marker and 5.5 s for each further one, the import check
  143 s -> 34 s. Mutation harness version 14.
- `effect_assertion_parity`, `vacuous_loop_assertions`, `inert_patch_targets` and `unread_init_params` read their node types
  through `nodes_of`; `vacuous_loop_assertions` builds its per-function maps only for functions with an assert-only loop, and
  `inert_patch_targets` skips the per-function `global` walk in modules that declare none. On mlframe: 50 -> 20 s, 31 -> 15 s,
  21 -> 14 s, 17 -> 13 s; findings unchanged.
- **A refresh only shrinks a baseline.** It drops entries that no longer fire and lowers counts; adding an entry,
  raising a count or seeding a missing baseline with findings fails and names them unless growth is opted into
  (`--py-ci-refresh-grow`, `PY_CI_SHARED_REFRESH_ALLOW_GROW=1`, `py-ci-shared refresh --grow`, `grow=True`).
- **New gate `complexity_ratchet`.** No new function over ruff's C901 limit, and the ones over it may not grow; the
  number is ruff's mccabe count computed from the AST (identical to ruff on every function in this repo).
- **`source_text_claims` judges arbitrary file reads.** Text read from a path it cannot place is a claim when an
  assertion takes a substring's position in it or searches it for a code-like literal; a `return` of a content
  check over source, and an `assert` over a name bound to one, are claims too.
- `adoption_matrix --resolve-in` counts how many releases each fixed pin is behind and fails a stale one (`--allow-behind N`).
- `py-ci-shared new-gate <name>` scaffolds a gate: module on `_core`, failing test skeleton, canary, registry entry, README row.
- `corpus_drift` counts every corpus-bindable finder over real consumer repos and fails a night whose counts jump, drop to zero or start to error.
- Scheduled workflows: `consumer-pins.yml` (daily adoption matrix over the consumers in `configs/consumers.toml`) and `corpus-drift.yml` (nightly).
- `function_complexity` keeps its API and baseline format but measures through `complexity_ratchet` and refreshes shrink-only.
- This repo's full-suite CI run enforces coverage `fail_under = 89`.

## 1.17.0

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
  build requires); invalid TOML fails. A requirements file is read as pip reads it (comments, continuations,
  `-r`/`-c` includes, `-e` and bare `git+` lines). `prompt_field_parity.string_literals` is strict by default, and the
  `llm_call_archive_gate` finders raise on unparsable files.
- The package ships `py.typed`, and `tool_versions.BLACK_VERSION` names the black `black-filtered.yml` runs.
  `lint-blocking.yml` takes `working-directory`, `codespell-toml`, `bandit-config` and `bandit-exclude`, so it can
  lint a monorepo subproject.
- **orjson is no longer a dependency**: baselines go through the stdlib `json` module (`_core.load_json` /
  `dump_json`), with the on-disk format unchanged.
- **Refresh works under pytest-xdist.** Refresh requests travel through the `PY_CI_SHARED_REFRESH` environment
  variable, which workers inherit, instead of `sys.argv`, which they do not.
- **Some baseline and allowlist keys changed** (`alembic_concurrently`, `dart_scanners`, `marker_runner_coverage`,
  `value_bearing_asserts`, `db_transaction_completeness`, `fail_open_handlers`, `discarded_model_copy`,
  `gate_integrity`, `machine_specific_paths`, whose keys now carry a digest of the flagged path instead of the path):
  refresh or rewrite those once.
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
- `adoption_matrix` reports, across consumer repos, where each pins py-ci-shared (and whether the pins agree or
  move), which modules each runs, and where a gate skips itself when the package is missing.
- Pre-commit hooks for `run-all`, `pinned_tool_versions`, `mypy_gate` and `worktree_hygiene`.
- New gates: `atomic_write_staging`, `clock_day_boundary`, `coverage_config_parity`, `hash_key_determinism`,
  `import_cycles`, `local_copy_report`, `no_xfail_to_defer`, `numba_seed_range`, `pickle_state_completeness`
  (static, plus the runtime `assert_pickle_round_trips`), `plotly_annotation_loop`, `polars_null_equality` (advisory),
  `pytest_addopts_path_runs`, `reiterated_iterable_params`, `rollback_then_continue`, `sentinel_or_fallback`,
  `stale_source_citations`, `stdlib_json_ban` (opt-in), `swallowed_exceptions`, and the opt-in pytest plugin
  `resource_leak_guard` (`resource_leak_guard = true` in `[tool.py_ci_shared]`).
- `dart_scanners` takes over the eight Dart scans the Flutter repos copied (file size, empty catch, source-text tests, import/export cycles, assertion-free tests, timed dismissal, double error reports, unused test seams) plus `dart_files_under`/`dart_reader`/`package_name`; `arb_checks.find_dead_keys` also counts `l10n .key` split across lines and `?.key` calls.

Release and CI:

- The package version is the release version (it was `0.1.0` through `v1.16.1`). `release.yml` checks the tag
  against it, then moves `v1` to the new tag.
- The reusable workflows fetch configs, `RUFF_VERSION` and the package at their own release
  (`py-ci-shared-ref` input), not at master. They declare `permissions: contents: read`, and lint-advisory's
  tools are installed at exact versions.
- `install-pyutilz` defaults to a pinned pyutilz commit instead of the branch tip.
- This repo's CI tests Python 3.9 to 3.13, installs the dev extra without a fallback, measures coverage and runs
  its own gates on itself with `py-ci-shared run-all`.
