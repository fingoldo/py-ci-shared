# py-ci-shared

[![CI](https://github.com/fingoldo/py-ci-shared/workflows/CI/badge.svg)](https://github.com/fingoldo/py-ci-shared/actions/workflows/self-ci.yml)
[![Config drift check](https://github.com/fingoldo/py-ci-shared/actions/workflows/config-drift-check.yml/badge.svg)](https://github.com/fingoldo/py-ci-shared/actions/workflows/config-drift-check.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Shared CI/lint tooling for fingoldo Python projects (`mlframe`, `pyutilz`, and future repos). Single source of truth for the CI/lint conventions that used to be independently duplicated (and drift) across each project's `.github/workflows/`, `scripts/`, and `pyproject.toml`.

Two things live here, each solving a different half of the duplication:

1. **Reusable GitHub Actions workflows** (`.github/workflows/*.yml`, invoked via `workflow_call`) for the pieces of CI that are identical in *behavior* across repos: the blocking ruff gate, the filtered Black check, the mypy strict-mode-beachhead pattern, the mypy-full advisory pass, the advisory lint bundle (codespell/yamllint/bandit/actionlint/vulture/pip-audit), and the MkDocs docs build/deploy.
2. **An installable package** (`py_ci_shared`, installed with `pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@v1.17.0"`) for the pieces that are identical in *code*: over a hundred gates (listed in the [Gate catalogue](#gate-catalogue)), the command-line tools such as `black_filtered_apply` and the warn-only pre-commit wrappers, a pytest plugin that runs the gates a repo enables in `[tool.py_ci_shared]`, and the `py-ci-shared` command.

Plus `configs/ruff-base.toml`: the shared `[tool.ruff.lint] select`/`ignore` superset, pulled into each consuming repo's own `pyproject.toml` via ruff's native `extend` mechanism (a real config-merge, not copy-paste) — see below.

**Deliberately NOT here:** anything whose shared surface is small relative to the parametrization cost (`sklearn-matrix-ci.yml`, `gpu-matrix.yml`, `release.yml`, `numba-coverage.yml`'s hardcoded test-path lists), and anything inherently project-specific (vulture whitelists, per-repo meta-test suites, the `test`/`build` jobs in each repo's own `ci.yml`). Those stay local to each repo.

## Writing tests: [WRITING_TESTS.md](WRITING_TESTS.md)

A convention for every project that consumes this package, derived from what a mutation sweep of a
3,676-test suite actually found: five shapes of test that a one-step change to the code slips past,
and the habit that closes each. Written that way in the first place, a sweep has nothing to report --
which matters because a sweep costs hours and the habits cost nothing.

## Why a separate repo, not part of `pyutilz`

`pyutilz` is a runtime dependency (real library code other repos `import`). Bundling CI tooling into it would couple tooling-script releases to runtime-code releases, and `pyutilz`'s own meta-tests (import-cycle checks, docstring-coverage snapshots, etc.) are specific to *its own* codebase structure — not generic/reusable against arbitrary consumer repos. Keeping this concern separate keeps both repos' release history clean.

## Using the reusable workflows

A consuming repo's own workflow file becomes a thin wrapper:

```yaml
# .github/workflows/black-filtered.yml
name: Black
on:
  push: {branches: [main, master]}
  pull_request: {branches: [main, master]}
jobs:
  black:
    uses: fingoldo/py-ci-shared/.github/workflows/black-filtered.yml@v1
    with:
      check-path: src/mlframe
```

See each workflow file's header comment for its full input list. Available workflows: `ruff-blocking.yml`, `black-filtered.yml`, `mypy-beachhead.yml`, `mypy-full.yml`, `lint-blocking.yml`, `lint-advisory.yml`, `docs.yml`.

**Use the moving `@v1` tag (2026-08-22 policy change).** `v1` always points at the latest `v1.x`
release, so cutting a release here propagates to every consumer at once — no per-repo SHA bump.
Pushing a `vX.Y.Z` tag runs `.github/workflows/release.yml`, which moves `v1` to it (see
[Pinning and releases](#pinning-and-releases)); nobody re-points it by hand.

This replaces the previous "pin every consumer to an exact tag/SHA" rule, which did not survive
contact with reality: consumers drifted onto *different* pins of the same workflow (`algopacksimple`
held three distinct SHAs across its own workflow files, `llm_bench` two), and the manual bump was
skipped often enough that most satellites sat many releases behind. The old rule's stated benefit —
"a behavior change is a reviewable diff, not an invisible side effect" — assumed CI actually runs
and gates on every consumer; for private repos out of free GitHub Actions minutes that review gate
was fictional, so the pin bought toil without buying safety.

Still pin to a full SHA when *you* are not the owner of the upstream: the threat a SHA pin defends
against is an upstream maintainer moving a tag under you, which does not apply to a first-party
repo (whoever could move this tag could equally push to the consumer directly).

`@main` specifically never resolves at all — this repo's default branch is `master` — and GitHub
does not fall back to the actual default branch when a `uses:` ref doesn't resolve; a stale/wrong
ref fails the whole calling workflow at parse time with "reference to workflow should be either a
valid branch, tag, or commit" and zero jobs ever run, which is easy to lose time to since the error
never shows up in any job log.

## Using the installable package

```bash
pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git"
```

To hack on this repo itself, install a local clone in editable mode instead, so edits under `src/py_ci_shared` take effect without reinstalling. The `[dev]` extra pulls in everything the test suite imports (`pytest`, `black`, `ruff`, `pydantic`, `pyutilz`, `pre-commit`, `numpy`, `sqlalchemy`, `pytest-cov`, `pytest-timeout` ...), so no test skips for a missing package; drop it for a runtime-only install:

```bash
git clone https://github.com/fingoldo/py-ci-shared.git
cd py-ci-shared
pip install -e ".[dev]"
```

Quote the argument: unquoted `[dev]` is glob syntax in most shells (and `pip install -e .[dev]` fails outright in zsh). On Windows, call the interpreter explicitly (`python.exe -m pip install -e ".[dev]"`) so the install lands in the environment you expect. The console scripts (`py-ci-shared`, `safe-precommit`, `py-ci-install-safe-hook`, `py-ci-setup-env`) are on `PATH` right after this, and `python -m py_ci_shared.setup_env` will point `PY_CI_SHARED_DIR` at the clone.

Then, in place of the old `python scripts/black_filtered_apply.py ...`:

```bash
python -m py_ci_shared.black_filtered_apply --config pyproject.toml --check src/mlframe
```

### pytest-randomly and thinc: "Seed must be between 0 and 2**32 - 1"

On an interpreter that has both pytest-randomly and spaCy (which brings thinc), roughly half of all tests error at setup
and teardown with `ValueError: Seed must be between 0 and 2**32 - 1`, in any repository. pytest-randomly (4.0.1 through
5.0.0, checked 2026-09-24) seeds each test with `randomly_seed + crc32(node id)`, which passes 2**32 about half the time;
it reduces the value for its own numpy call but hands it raw to every `pytest_randomly.random_seeder` entry point, and
thinc registers `fix_random_seed`, which calls `numpy.random.seed` with it. Upgrading pytest-randomly does not help, and
`-p no:randomly` only hides it by throwing away test-order randomisation.

`py_ci_shared.randomly_seed_guard.bound_randomly_reseeders()` reduces the seed the same way pytest-randomly already does
for numpy, so ordering and per-test determinism are unchanged. The package's pytest plugin calls it in `pytest_configure`;
a repository that does not load the plugin calls it from its own `conftest.py`:

```python
from py_ci_shared.randomly_seed_guard import bound_randomly_reseeders

def pytest_configure(config):
    bound_randomly_reseeders()
```

glossum, pyutilz and mlframe each carry an older copy of this fix in their `conftest.py` (a wrapper around
`thinc.api.fix_random_seed`); they work, and can be replaced by the call above.

`format_warn.py` is already fully generic. `bandit_warn.py` and `vulture_warn.py` take `--src-path src/mlframe` (required; the `PY_CI_SHARED_SRC_PATH` environment variable is the fallback), and `vulture_warn.py` also takes `--whitelist scripts/vulture_whitelist.py` (optional; `PY_CI_SHARED_VULTURE_WHITELIST` is its fallback). Pass them as the hook's `args:` in `.pre-commit-config.yaml`: pre-commit has no per-hook `env:` key, so the environment variables only help when the caller's shell exports them. `vulture_warn` does nothing at import and exits 2 on a bad command line.

## Gate catalogue

Every public module, generated from [`py_ci_shared.registry`](src/py_ci_shared/registry.py) by
`py-ci-shared list --markdown`; `tests/test_package_inventory.py` fails when this table, the registry, the
modules on disk or their test files disagree. `kind` is `gate` (an `assert_*` function you call from a test or
enable in `[tool.py_ci_shared]`), `cli` (run with `py-ci-shared tool <name>`) or `library` (scanners you wrap).
`mutation_teeth` and `teeth_sweep` are explained in [WRITING_TESTS.md](WRITING_TESTS.md).

<!-- gate-catalogue:start (generated by `py-ci-shared list --markdown`; do not edit by hand) -->

| module | kind | since | entry | what it checks |
|---|---|---|---|---|
| [`adoption_matrix`](src/py_ci_shared/adoption_matrix.py) | cli | 1.17.0 | `main` | Which consumer repos run which modules, whether their py-ci-shared pins agree or move, and where a gate skips itself |
| [`advisory_warn`](src/py_ci_shared/advisory_warn.py) | cli | 1.0.0 | `main` | Warn-only advisory-taste check for the pre-commit hook |
| [`alembic_concurrently`](src/py_ci_shared/alembic_concurrently.py) | gate | 1.8.0 | `assert_concurrently_is_in_autocommit_blocks` | An Alembic migration runs ``CONCURRENTLY`` only inside ``autocommit_block()`` |
| [`arb_checks`](src/py_ci_shared/arb_checks.py) | gate | 1.3.6 | `assert_arb_catalogues_are_sound` | Shared checks over Flutter ``.arb`` localization catalogues |
| [`atomic_write_staging`](src/py_ci_shared/atomic_write_staging.py) | gate | 1.17.0 | `assert_atomic_write_staging` | Write-then-rename staging that does not stage what it renames, and cache/baseline files rewritten in place |
| [`audit_disposition_parity`](src/py_ci_shared/audit_disposition_parity.py) | gate | 1.4.0 | `assert_dispositions_name_real_artefacts` | A finding marked RESOLVED names artefacts that exist |
| [`audit_path_references`](src/py_ci_shared/audit_path_references.py) | gate | 1.17.0 | `assert_no_open_round_paths` | Code never pins the path of an OPEN audit round |
| [`audit_round_format`](src/py_ci_shared/audit_round_format.py) | gate | 1.9.0 | `assert_tracker_statuses_countable` (+3) | An audit tracker and its round files stay countable by machine |
| [`audit_wave_filenames`](src/py_ci_shared/audit_wave_filenames.py) | gate | 1.7.0 | `assert_no_new_audit_wave_filenames` | A test file is named after what it covers, not after the audit wave that produced it |
| [`bandit_warn`](src/py_ci_shared/bandit_warn.py) | cli | 1.0.0 | `main` | Warn-only security-lint check for the pre-commit hook |
| [`baseline_hygiene`](src/py_ci_shared/baseline_hygiene.py) | gate | 1.3.6 | `assert_baseline_is_honest` | A baseline of accepted violations stays a decision, not a landfill |
| [`baseline_ratchet`](src/py_ci_shared/baseline_ratchet.py) | library | 1.3.6 |  | Shared ratchet for structural checks with a pre-existing backlog |
| [`baseline_trend`](src/py_ci_shared/baseline_trend.py) | cli | 1.3.6 | `main` | How each baselined rule's count has moved over time, read out of git history |
| [`black_filtered_apply`](src/py_ci_shared/black_filtered_apply.py) | cli | 1.0.0 | `main` | Apply Black's reformatting to a file, EXCEPT for opcodes matching excluded classes |
| [`changelog_promise_parity`](src/py_ci_shared/changelog_promise_parity.py) | gate | 1.3.1 | `assert_changelog_bullets_satisfy_pattern` | Shared checks for the "a CHANGELOG bullet promises something, does the promise ever get kept" consistency pattern |
| [`checkout_resolution`](src/py_ci_shared/checkout_resolution.py) | gate | 1.7.0 | `assert_modules_resolve_to_checkout` (+1) | The suite examines the code in THIS checkout, not a copy installed somewhere else |
| [`checkpoint_isolation`](src/py_ci_shared/checkpoint_isolation.py) | gate | 1.4.0 | `assert_outside` | Assert that no test reads or writes the on-disk state a real run of the program uses |
| [`ci_test_dir_reachability`](src/py_ci_shared/ci_test_dir_reachability.py) | gate | 1.3.1 | `assert_every_test_subdir_reachable` | Every subdirectory under a consumer repo's ``tests/`` must be reachable by at least one CI job -- or be explicitly whitelisted as intentionally |
| [`ci_workflow_gate`](src/py_ci_shared/ci_workflow_gate.py) | gate | 1.3.0 | `assert_continue_on_error_is_reviewed` | Every ``continue-on-error |
| [`ci_workflow_paths`](src/py_ci_shared/ci_workflow_paths.py) | gate | 1.3.6 | `assert_workflow_paths_exist` | A CI workflow does not name paths that do not exist, and declares its permissions |
| [`ci_workflow_timeout_gate`](src/py_ci_shared/ci_workflow_timeout_gate.py) | gate | 1.3.1 | `assert_all_jobs_have_timeout` | Every job in a CI workflow file declares ``timeout-minutes`` |
| [`clock_day_boundary`](src/py_ci_shared/clock_day_boundary.py) | gate | 1.17.0 | `assert_no_clock_day_boundary` | A test that reads the real clock and shifts it by part of a day fails for part of every day |
| [`code_audit_meta`](src/py_ci_shared/code_audit_meta.py) | gate | 1.1.0 | `assert_no_new_code_audit_findings` | Shared harness for the "code-audit baseline" meta-test pattern |
| [`complexity_ratchet`](src/py_ci_shared/complexity_ratchet.py) | gate | 1.18.0 | `assert_complexity_does_not_grow` | No NEW function over the cyclomatic-complexity limit (ruff C901), and the ones already over it may not grow |
| [`conceded_defect_pins`](src/py_ci_shared/conceded_defect_pins.py) | library | 1.17.0 |  | Tests that say the behaviour is wrong and then pin it exactly |
| [`config_call_site_parity`](src/py_ci_shared/config_call_site_parity.py) | gate | 1.3.0 | `assert_every_cfg_get_call_resolves_to_a_schema_field` (+4) | Shared checks for the "``cfg().get(section, key, default, type_)`` call-site vs Pydantic schema" consistency pattern |
| [`config_drift_check`](src/py_ci_shared/config_drift_check.py) | cli | 1.1.1 | `main` | Reports [tool.ruff]/[tool.mypy] config divergence across consumer repos |
| [`config_getattr_default_parity`](src/py_ci_shared/config_getattr_default_parity.py) | gate | 1.17.0 | `assert_getattr_defaults_match_schema` | ``getattr(cfg, "field", <literal>)`` whose literal disagrees with the field's own default |
| [`content_hash_version_bump_gate`](src/py_ci_shared/content_hash_version_bump_gate.py) | gate | 1.3.1 | `assert_version_bumped_with_content` | Shared harness for the "N files feed a version/cache-key constant that must be bumped by hand whenever those files change" meta-test pattern |
| [`corpus_drift`](src/py_ci_shared/corpus_drift.py) | cli | 1.18.0 | `main` | Per-gate finding counts over real consumer repos, and the nightly jumps, drops to zero and new errors between them |
| [`coverage_config_parity`](src/py_ci_shared/coverage_config_parity.py) | gate | 1.17.0 | `assert_coverage_config_parity` | Coverage config that a CI run inherits without meaning to: a whole-suite ``fail_under`` on a narrow run, and njit bodies no run can see |
| [`dart_scanners`](src/py_ci_shared/dart_scanners.py) | library | 1.3.6 |  | Shared structural scanners over Dart/Flutter source |
| [`dataclass_case_completeness`](src/py_ci_shared/dataclass_case_completeness.py) | gate | 1.5.0 | `assert_every_dataclass_has_a_case` | Every dataclass of a given kind has a test case, or an exemption with a reason |
| [`db_transaction_completeness`](src/py_ci_shared/db_transaction_completeness.py) | gate | 1.17.0 | `assert_no_new_incomplete_transaction` | A function that runs a statement on a database-handle parameter must also close the transaction it opened -- a ``commit()`` or ``rollback()`` on |
| [`deferred_drift`](src/py_ci_shared/deferred_drift.py) | gate | 1.12.0 | `assert_deferred_lists_not_grown` | Deferred-debt lists in a meta-test directory may not grow, and a list that shrank must say so |
| [`deletion_gates`](src/py_ci_shared/deletion_gates.py) | library | 1.4.0 |  | Assert that something an audit deliberately REMOVED has not come back |
| [`discarded_model_copy`](src/py_ci_shared/discarded_model_copy.py) | gate | 1.17.0 | `assert_no_discarded_model_copy` | A config rebuilt with ``model_copy(update=...)`` into a local must reach something, or the override is lost |
| [`disposition_test_references`](src/py_ci_shared/disposition_test_references.py) | gate | 1.17.0 | `assert_disposition_tests_exist` | A test an audit disposition names must exist |
| [`doc_identifier_parity`](src/py_ci_shared/doc_identifier_parity.py) | gate | 1.17.0 | `assert_doc_identifiers_exist` | A flag or identifier a document names in backticks must exist somewhere in the code |
| [`docs_inventory_parity`](src/py_ci_shared/docs_inventory_parity.py) | gate | 1.3.6 | `assert_no_inventory_drift` | A documented inventory is computed from the thing it documents |
| [`drifted_duplicate_functions`](src/py_ci_shared/drifted_duplicate_functions.py) | gate | 1.4.0 | `assert_no_drifted_duplicate_functions` | Copies of one function that have drifted apart |
| [`edge_function_hygiene`](src/py_ci_shared/edge_function_hygiene.py) | gate | 1.3.6 | `assert_edge_functions_are_sound` | A serverless edge function is not a hole in the product's own back end |
| [`effect_assertion_parity`](src/py_ci_shared/effect_assertion_parity.py) | gate | 1.4.0 | `assert_effects_are_asserted` | A side effect the code performs must be one some test actually inspects |
| [`embedded_postgres`](src/py_ci_shared/embedded_postgres.py) | cli | 1.17.0 | `main` | Run a command against a throwaway local Postgres, and find the main checkout's ``.env`` from a worktree |
| [`entry_points_resolvable`](src/py_ci_shared/entry_points_resolvable.py) | gate | 1.3.1 | `assert_all_entry_points_resolvable` | Every ``[project.scripts]``/``[project.entry-points.*]`` entry in pyproject.toml actually resolves (module imports, attribute exists) |
| [`env_example_round_trip`](src/py_ci_shared/env_example_round_trip.py) | gate | 1.17.0 | `assert_env_example_loads` | Every value a `.env.example` documents can actually be loaded by the settings class |
| [`env_flag_parsing`](src/py_ci_shared/env_flag_parsing.py) | gate | 1.17.0 | `assert_env_flags_use_one_parser` | Boolean environment flags read by hand, each with its own idea of what "on" means |
| [`epsilon_padded_denominators`](src/py_ci_shared/epsilon_padded_denominators.py) | gate | 1.4.0 | `assert_no_epsilon_padded_power_denominators` | An additive epsilon must not guard a denominator whose magnitude falls off geometrically |
| [`fail_message_quality`](src/py_ci_shared/fail_message_quality.py) | gate | 1.12.0 | `assert_fail_messages_actionable` | Every ``pytest.fail`` message in a meta-test directory tells the reviewer what to do |
| [`fail_open_handlers`](src/py_ci_shared/fail_open_handlers.py) | gate | 1.17.0 | `assert_no_new_fail_open_handlers` | Fail-open exception handlers in gate code |
| [`format_warn`](src/py_ci_shared/format_warn.py) | cli | 1.0.0 | `main` | Warn-only formatting / lint check for the pre-commit hook |
| [`function_complexity`](src/py_ci_shared/function_complexity.py) | gate | 1.18.0 | `assert_complexity_does_not_grow` | C901 complexity ratchet with the original function_complexity API (limit 25); measured by complexity_ratchet |
| [`function_length`](src/py_ci_shared/function_length.py) | gate | 1.17.0 | `assert_functions_do_not_grow` | No NEW long function, and the long ones already there may not grow |
| [`gate_config_honesty`](src/py_ci_shared/gate_config_honesty.py) | gate | 1.17.0 | `assert_gates_honest` | A gate runs its tool the way the project configured it, and a "blocking" gate can block |
| [`gate_integrity`](src/py_ci_shared/gate_integrity.py) | gate | 1.3.6 | `assert_narrowings_declared` (+2) | A gate that is DECLARED blocking must actually be able to block |
| [`gate_population_canary`](src/py_ci_shared/gate_population_canary.py) | gate | 1.17.0 | `assert_every_gate_declares_its_population` (+2) | A pattern-matching gate must prove it still looks at something, and still matches it |
| [`git_changed_lines`](src/py_ci_shared/git_changed_lines.py) | library | 1.4.0 |  | The line ranges a diff actually touched, per file |
| [`git_dependency_pins`](src/py_ci_shared/git_dependency_pins.py) | gate | 1.3.0 | `assert_all_git_dependencies_pinned` (+2) | Every git-URL dependency in pyproject.toml is pinned to a full commit SHA, not floating on a branch/tag |
| [`gpu_timing_sync`](src/py_ci_shared/gpu_timing_sync.py) | gate | 1.3.6 | `assert_no_unsynchronized_gpu_timings` | A wall-clock measurement taken around GPU work must synchronize the device before the timer stops |
| [`guard_population`](src/py_ci_shared/guard_population.py) | gate | 1.3.6 | `assert_guards_examine_something` | A guard script is actually looking at something |
| [`hardcoded_token_ceilings`](src/py_ci_shared/hardcoded_token_ceilings.py) | gate | 1.17.0 | `assert_no_hardcoded_token_ceilings` | LLM output ceilings written as a number someone chose by eye |
| [`hash_fed_by_array_copy`](src/py_ci_shared/hash_fed_by_array_copy.py) | gate | 1.4.0 | `assert_no_hash_fed_by_array_copy` | An array must not be copied just to be hashed |
| [`hash_key_determinism`](src/py_ci_shared/hash_key_determinism.py) | gate | 1.17.0 | `assert_hash_keys_are_deterministic` | JSON serialised for a hash, a cache key or a dedup comparison without sorting its keys |
| [`hook_hygiene`](src/py_ci_shared/hook_hygiene.py) | gate | 1.3.6 | `assert_hooks_are_honest` | A git hook fails loudly, stages nothing of its own, and runs what CI runs |
| [`identity_comparisons`](src/py_ci_shared/identity_comparisons.py) | gate | 1.17.0 | `assert_no_identity_comparisons` | A string constant is compared by value, not by identity |
| [`ignore_ratchet`](src/py_ci_shared/ignore_ratchet.py) | gate | 1.17.0 | `assert_ignore_list_only_shrinks` | The codes a blocking lint gate IGNORES may only shrink |
| [`import_cycles`](src/py_ci_shared/import_cycles.py) | gate | 1.17.0 | `assert_no_import_cycles` | Module-level import cycles, and the cycles that only load when one particular side is imported first |
| [`import_layering`](src/py_ci_shared/import_layering.py) | gate | 1.3.6 | `assert_layering` | Declared architectural layers are not violated by an import |
| [`import_side_effects`](src/py_ci_shared/import_side_effects.py) | gate | 1.13.0 | `assert_no_new_import_time_env_mutations` (+1) | Importing a module has no side effects |
| [`index_coverage`](src/py_ci_shared/index_coverage.py) | library | 1.4.0 |  | Is an expected index already served by a live one under a different name? |
| [`inert_patch_targets`](src/py_ci_shared/inert_patch_targets.py) | library | 1.4.0 |  | Find test fixtures that RESET a module attribute the module does not have |
| [`install_safe_hook`](src/py_ci_shared/install_safe_hook.py) | cli | 1.1.0 | `main` | Point the generated git hook(s) at ``safe_precommit`` instead of raw ``pre_commit``, so plain ``git commit`` / ``git merge`` transparently survive |
| [`kwarg_forwarding`](src/py_ci_shared/kwarg_forwarding.py) | library | 1.17.0 |  | Optional arguments a wrapper, a caller or a same-class delegate drops on the way to the function it reaches |
| [`latched_availability_flags`](src/py_ci_shared/latched_availability_flags.py) | gate | 1.4.0 | `assert_no_latched_availability_flags` | A broad ``except`` must not cache a process-lifetime "unavailable" verdict |
| [`lf_file_writes`](src/py_ci_shared/lf_file_writes.py) | gate | 1.17.0 | `assert_no_crlf_writes` | Text-mode writes that put CRLF into a file that must stay LF |
| [`llm_call_archive_gate`](src/py_ci_shared/llm_call_archive_gate.py) | gate | 1.5.0 | `assert_every_llm_call_is_archived` | Every paid LLM call goes through the place that keeps its raw answer |
| [`loc_budget`](src/py_ci_shared/loc_budget.py) | gate | 1.3.0 | `assert_no_new_oversized_file` | Shared harness for the "no file over N LOC" meta-test pattern |
| [`local_copy_report`](src/py_ci_shared/local_copy_report.py) | gate | 1.17.0 | `assert_local_copies_do_not_grow` | Local meta-tests that duplicate a central gate |
| [`machine_specific_paths`](src/py_ci_shared/machine_specific_paths.py) | gate | 1.17.0 | `assert_no_machine_specific_paths` | Paths and connection strings that only exist on the machine that wrote them |
| [`marker_runner_coverage`](src/py_ci_shared/marker_runner_coverage.py) | gate | 1.17.0 | `assert_every_marked_test_is_selected` | A test carrying a marker must be SELECTED by some runner, or it never runs |
| [`meta_private_imports`](src/py_ci_shared/meta_private_imports.py) | gate | 1.13.0 | `assert_no_private_meta_imports` | A meta-test does not import a private name of the package it polices, unless a permitted entry says why |
| [`module_cache_thread_safety`](src/py_ci_shared/module_cache_thread_safety.py) | gate | 1.17.0 | `assert_thread_safe_module_caches` | Module-level caches mutated with no lock, and lazy ``from X import`` inside joblib-dispatched workers |
| [`module_reload_safety`](src/py_ci_shared/module_reload_safety.py) | gate | 1.7.0 | `assert_no_reloads_in_code` (+1) | No module is reloaded or dropped from ``sys.modules`` without a restore in the same scope |
| [`mutation_teeth`](src/py_ci_shared/mutation_teeth.py) | gate | 1.4.0 | `assert_no_new_surviving_mutant` (+1) | A test that claims to pin a defect must be able to FAIL |
| [`mypy_gate`](src/py_ci_shared/mypy_gate.py) | cli | 1.3.6 | `main` | Run mypy as a gate that requires COMPLETION, not merely a zero exit code |
| [`naive_utcnow`](src/py_ci_shared/naive_utcnow.py) | gate | 1.4.1 | `assert_no_naive_utcnow` | No production module builds a timestamp with `datetime.utcnow()` |
| [`no_xfail_to_defer`](src/py_ci_shared/no_xfail_to_defer.py) | gate | 1.17.0 | `assert_no_xfail_to_defer` | xfail and skip may mark a limit outside the repository, never park a bug inside it |
| [`nondiscriminating_shapes`](src/py_ci_shared/nondiscriminating_shapes.py) | library | 1.17.0 |  | Assertion shapes that pass whether or not the property under test holds |
| [`numba_seed_range`](src/py_ci_shared/numba_seed_range.py) | gate | 1.17.0 | `assert_numba_seeds_fit_int64` | A full-64-bit seed handed to numba, which types seeds as int64 and rejects half of them |
| [`optional_truthiness`](src/py_ci_shared/optional_truthiness.py) | gate | 1.4.0 | `assert_optionals_test_for_none` | An optional parameter tested for truth rather than for absence |
| [`order_losing_filters`](src/py_ci_shared/order_losing_filters.py) | gate | 1.17.0 | `assert_no_order_losing_filters` | Row selections by an index-built mask whose positional twin returns the rows in index order |
| [`package_doctests`](src/py_ci_shared/package_doctests.py) | gate | 1.12.0 | `assert_package_doctests_pass` | The doctests a package ships actually run, and there are some to run |
| [`phantom_code_references`](src/py_ci_shared/phantom_code_references.py) | gate | 1.3.6 | `assert_no_phantom_code_references` (+1) | A comment that names a test file, a class or a function must name one that exists |
| [`phantom_markdown_links`](src/py_ci_shared/phantom_markdown_links.py) | gate | 1.3.1 | `assert_no_phantom_markdown_links` | Every markdown-link target in a repo's .md files resolves to a real file |
| [`pickle_state_completeness`](src/py_ci_shared/pickle_state_completeness.py) | gate | 1.17.0 | `assert_no_pickle_state_gaps` (+1) | Runtime caches and live handles that a pickle round trip (joblib fan-out, a saved model) cannot carry |
| [`pinned_tool_versions`](src/py_ci_shared/pinned_tool_versions.py) | cli | 1.4.1 | `main` | Fail when a repo's ruff pin, or the ruff its interpreter runs, differs from the shared version |
| [`plotly_annotation_loop`](src/py_ci_shared/plotly_annotation_loop.py) | gate | 1.17.0 | `assert_no_plotly_annotation_loops` | plotly ``fig.add_annotation``/``add_shape`` called once per item in a loop: O(n^2) in the number of items |
| [`polars_null_equality`](src/py_ci_shared/polars_null_equality.py) | gate | 1.17.0 | `assert_polars_null_equality` | Advisory: polars comparisons whose null handling silently changes the answer |
| [`printed_advice`](src/py_ci_shared/printed_advice.py) | gate | 1.17.0 | `assert_printed_advice_registered` | Log and error messages that advise an action, each mapped to a test that follows the advice |
| [`private_imports`](src/py_ci_shared/private_imports.py) | gate | 1.8.0 | `assert_no_private_cross_package_imports` | Production code does not import another package's underscore-prefixed module |
| [`prompt_field_parity`](src/py_ci_shared/prompt_field_parity.py) | library | 1.5.0 |  | A field a prompt or schema asks the model for is read by something, and stored |
| [`prose_numeric_claims`](src/py_ci_shared/prose_numeric_claims.py) | gate | 1.3.6 | `assert_numeric_claims_match` | A counted fact stated in prose is computed from the repo, not typed by hand |
| [`pydantic_field_bounds`](src/py_ci_shared/pydantic_field_bounds.py) | gate | 1.12.0 | `assert_field_bounds_enforced` | A pydantic field's declared bound or ``Literal`` set actually rejects a value outside it |
| [`pytest_addopts_path_runs`](src/py_ci_shared/pytest_addopts_path_runs.py) | gate | 1.17.0 | `assert_path_runs_select_tests` | A hook or CI step that names test paths, and runs none of them because ``addopts`` still deselects them |
| [`pytest_markers`](src/py_ci_shared/pytest_markers.py) | gate | 1.7.0 | `assert_markers_registered` | Every pytest marker a test suite uses is registered |
| [`randomly_seed_guard`](src/py_ci_shared/randomly_seed_guard.py) | library | 1.17.0 |  | Runtime helper: bounds pytest-randomly's per-test seed to 32 bits before third-party reseeders see it |
| [`readme_env_var_parity`](src/py_ci_shared/readme_env_var_parity.py) | gate | 1.3.0 | `assert_readme_documents_every_env_var` (+1) | Every environment variable production code reads via ``os.environ.get(...)``/``os.getenv(...)``/``os.environ[...]`` is documented in the project's |
| [`reiterated_iterable_params`](src/py_ci_shared/reiterated_iterable_params.py) | gate | 1.17.0 | `assert_no_reiterated_iterable_params` | A parameter typed ``Iterable`` (or ``Iterator``/``Generator``) consumed more than once |
| [`repo_hygiene`](src/py_ci_shared/repo_hygiene.py) | gate | 1.3.6 | `assert_repo_hygiene` | The repository tracks nothing it generates, and carries the files its gates need |
| [`resource_leak_guard`](src/py_ci_shared/resource_leak_guard.py) | library | 1.17.0 |  | Opt-in pytest plugin: a test that leaks a child process, a thread, a socket, ``logging.disable`` or an env var errors at teardown |
| [`resource_release_paths`](src/py_ci_shared/resource_release_paths.py) | gate | 1.4.0 | `assert_released_on_every_path` | A resource a module OWNS is released on the failure path too |
| [`rollback_then_continue`](src/py_ci_shared/rollback_then_continue.py) | gate | 1.17.0 | `assert_no_rollback_then_continue` | ``rollback()`` in a loop's ``except`` handler, then on to the next item as if only that item failed |
| [`runtime_registry_mutation`](src/py_ci_shared/runtime_registry_mutation.py) | gate | 1.17.0 | `assert_writes_have_replay` | Runtime writes to a module-level registry |
| [`safe_precommit`](src/py_ci_shared/safe_precommit.py) | cli | 1.1.0 | `main` | Drop-in ``pre-commit`` replacement that survives concurrent-session stash-restore races |
| [`save_failure_markers`](src/py_ci_shared/save_failure_markers.py) | gate | 1.5.0 | `assert_markers_are_fatal` | Every save-failure marker a writer emits is one the success decision recognises |
| [`sentinel_or_fallback`](src/py_ci_shared/sentinel_or_fallback.py) | gate | 1.17.0 | `assert_no_sentinel_or_fallback` | ``value or fallback`` on a setting whose falsy values are meaningful: ``max_tokens=0``, ``timeout=0``, ``seed=0`` |
| [`setup_env`](src/py_ci_shared/setup_env.py) | cli | 1.3.0 | `main` | Persist ``PY_CI_SHARED_DIR`` at the OS/user level so ruff's ``extend = "$PY_CI_SHARED_DIR/configs/ruff-base.toml"`` (see README's "Using the |
| [`source_text_ban`](src/py_ci_shared/source_text_ban.py) | library | 1.4.0 |  | Find tests that assert on a module's SOURCE TEXT instead of running it |
| [`source_text_claims`](src/py_ci_shared/source_text_claims.py) | gate | 1.11.0 | `assert_no_new_source_text_claims` | Tests that assert on SOURCE TEXT instead of running the code |
| [`spec_bound_doubles`](src/py_ci_shared/spec_bound_doubles.py) | gate | 1.4.0 | `assert_doubles_are_spec_bound` | A test double reaching duck-typed production code must be spec-bound |
| [`sql_function_privileges`](src/py_ci_shared/sql_function_privileges.py) | gate | 1.3.6 | `assert_definer_functions_are_locked_down` | A ``SECURITY DEFINER`` SQL function is not left executable by every logged-in user |
| [`sql_verifier_coverage`](src/py_ci_shared/sql_verifier_coverage.py) | gate | 1.12.0 | `assert_verifier_covers_statements` | Every SQL statement a package defines is on its SQL verifier's list, and the list names nothing that has gone |
| [`sql_verify`](src/py_ci_shared/sql_verify.py) | library | 1.4.0 |  | Execute every SQL statement a project ships against a real PostgreSQL server |
| [`sqlalchemy_text_binds`](src/py_ci_shared/sqlalchemy_text_binds.py) | gate | 1.8.0 | `assert_no_colon_cast_binds` | No SQLAlchemy ``text()`` bind parameter is followed directly by a ``::`` cast |
| [`stale_comment_age`](src/py_ci_shared/stale_comment_age.py) | gate | 1.3.6 | `assert_no_stale_todos` | A TODO or a commented-out call does not quietly become permanent |
| [`stale_source_citations`](src/py_ci_shared/stale_source_citations.py) | gate | 1.17.0 | `assert_no_stale_source_citations` | ``file.py:NNN`` citations that no longer point where they say |
| [`statement_compilation`](src/py_ci_shared/statement_compilation.py) | gate | 1.17.0 | `assert_no_mocked_statement_constructors` | A test that mocks the statement constructor never compiles the statement |
| [`stdlib_json_ban`](src/py_ci_shared/stdlib_json_ban.py) | gate | 1.17.0 | `assert_no_stdlib_json` | Opt-in: no module imports the standard-library ``json``; JSON goes through ``orjson`` (or a project shim over it) |
| [`survivorship_scoring`](src/py_ci_shared/survivorship_scoring.py) | gate | 1.17.0 | `assert_no_survivorship_scoring` | A metric scored only on the rows where the prediction happened to be finite |
| [`swallowed_exceptions`](src/py_ci_shared/swallowed_exceptions.py) | gate | 1.17.0 | `assert_no_swallowed_exceptions` | Exception handlers that swallow a broad or I/O failure and carry on with the state the failure left behind |
| [`teeth_sweep`](src/py_ci_shared/teeth_sweep.py) | cli | 1.4.0 | `main` | Does anything actually FAIL if an audit fix is undone? Mutate, run the suite, restore |
| [`test_partition_reachability`](src/py_ci_shared/test_partition_reachability.py) | gate | 1.3.6 | `assert_partitions_reachable` | Every declared test partition is actually selected by some runner |
| [`timezone_honest`](src/py_ci_shared/timezone_honest.py) | gate | 1.4.1 | `assert_timezone_honest` | Non-test code states its time frame in the expression, and the scan cannot miss a directory |
| [`tool_versions`](src/py_ci_shared/tool_versions.py) | library | 1.4.1 |  | The linter versions every consuming repo runs, defined in one place |
| [`tracker_summary_parity`](src/py_ci_shared/tracker_summary_parity.py) | gate | 1.17.0 | `assert_tracker_summaries_agree` | A tracker's summary tables and headings agree with the rows they summarise |
| [`uncalled_functions`](src/py_ci_shared/uncalled_functions.py) | gate | 1.4.0 | `assert_no_new_uncalled_function` | A function that production code never calls |
| [`unread_init_params`](src/py_ci_shared/unread_init_params.py) | gate | 1.17.0 | `assert_no_unread_init_params` | Constructor parameters nothing ever reads |
| [`unresolved_imports`](src/py_ci_shared/unresolved_imports.py) | gate | 1.4.0 | `assert_all_from_imports_resolve` | Every ``from X import Y`` must name something X actually defines |
| [`vacuous_loop_assertions`](src/py_ci_shared/vacuous_loop_assertions.py) | gate | 1.17.0 | `assert_no_new_floorless_loop` | A ``for`` loop whose body is only conditional assertions, with nothing elsewhere asserting the loop actually ran, is a test that passes on zero |
| [`value_bearing_asserts`](src/py_ci_shared/value_bearing_asserts.py) | gate | 1.12.0 | `assert_no_value_bearing_asserts` | No guard that checks a VALUE may ride on an ``assert`` in production code |
| [`version_consistency`](src/py_ci_shared/version_consistency.py) | gate | 1.8.0 | `assert_versions_agree` | A package reports one version wherever it states one |
| [`version_tag_currency`](src/py_ci_shared/version_tag_currency.py) | gate | 1.3.6 | `assert_version_is_tagged` | A package's declared version, its tags and its consumers' pins agree |
| [`vulture_warn`](src/py_ci_shared/vulture_warn.py) | cli | 1.0.0 | `main` | Warn-only dead-code check for the pre-commit hook |
| [`worktree_hygiene`](src/py_ci_shared/worktree_hygiene.py) | cli | 1.17.0 | `main` | Which worktrees, branches and leftover directories can go, and which still hold work |

<!-- gate-catalogue:end -->

Notes on the 1.17.0 additions:

- `resource_leak_guard` is a pytest plugin, not a gate, and is opt-in: it is not a `pytest11` entry point. Load it
  with `resource_leak_guard = true` in `[tool.py_ci_shared]` (the `py_ci_shared` plugin then registers it) or with
  `-p py_ci_shared.resource_leak_guard` / `pytest_plugins` in the root conftest. A test that leaves a child process,
  a non-daemon thread, a socket, `logging.disable` or an `os.environ` change behind errors at teardown; allow one
  with `@pytest.mark.leak_guard_allow("<kind>:<glob>")` or the `leak_guard_allow` ini list, skip one with
  `@pytest.mark.no_leak_guard`. The socket check needs `psutil`; without it sockets are not measured and child
  processes are read from `multiprocessing.active_children()`.
- `stdlib_json_ban` is opt-in: it only runs where a repo enables it, for codebases that route JSON through `orjson`.
- `polars_null_equality` is advisory by default (`advisory=True` warns and never fails); pass `advisory = false`
  in its table to make it blocking.
- New AST gates end in `py_ci_shared._gate_run`, the shared `assert_*` tail (file floor, unparsed files, then a
  baseline ratchet or a zero-tolerance fail), so a new gate writes only its finder.

## Configuring gates: `[tool.py_ci_shared]`

Instead of one hand-written meta-test per gate, a repo can enable gates in its `pyproject.toml`. The
installed package registers a pytest plugin (`pytest11` entry point `py_ci_shared`); it does nothing until
this table exists.

```toml
[tool.py_ci_shared]
enable = ["naive_utcnow"]            # gates run with their defaults
budget = "warn"                      # past a gate's runtime budget: "warn" (default), "fail" or "off"

[tool.py_ci_shared.gates.function_length]    # a table enables the gate; its keys are the entry function's kwargs
files = ["src/**/*.py"]
baseline_path = "tests/baselines/function_length.json"
budget_s = 20                        # reserved key: this gate's budget, overriding the registry's

[tool.py_ci_shared.gates.rounds_format]      # a second run of one module, under its own name
module = "audit_round_format"
entry = "assert_rounds_countable"
audits_dir = "audits"
```

- Reserved keys: `module` (default: the table name), `entry` (default: the module's first `assert_*`),
  `budget_s`, `enabled = false`. Every other key is passed as a keyword argument.
- Paths are relative to the repo root. A string under `root`, `repo_root`, `path`, `tracker` or any key ending
  in `_dir`, `_path` or `_root` becomes a path; a list under `files`, `roots`, `md_files`, `scan_roots`,
  `package_roots`, `test_files`, `paths` becomes a list of paths with globs expanded (`**` is recursive). A
  `repo_root`/`root`/`repo`/`project_root` parameter you leave out is set to the repo root.
- An unknown key fails with the entry function's signature, rather than being ignored.

With the table in place:

- `pytest` with no path arguments adds one item per enabled gate, `pyproject.toml::<gate>`. Selecting paths
  (`pytest tests/test_x.py`) leaves them out; `--py-ci-gates=on|off` forces either way.
- `py-ci-shared run-all` runs the same gates without pytest's collection, for a pre-commit hook or a CI step.
- Each gate is timed. Past its budget the pytest item warns (`PytestWarning`) and `run-all` prints a `BUDGET`
  line; with `budget = "fail"` both fail.

## Command line

```bash
py-ci-shared list [--markdown]         # every registered module; --markdown prints the catalogue above
py-ci-shared run-all                   # every gate in [tool.py_ci_shared]; exit 0 pass, 1 findings, 2 config error
py-ci-shared run function_length       # the named gates only
py-ci-shared refresh function_length   # rewrite that gate's baseline, then run it ("all" for every gate)
py-ci-shared config-path ruff-base     # installed path of the shipped ruff config
py-ci-shared tool worktree_hygiene -h  # a module's own command line (same as python -m py_ci_shared.worktree_hygiene)
py-ci-shared new-gate my_gate --summary "..."  # scaffold a gate in a py-ci-shared checkout (see CLAUDE.md)
```

The older console scripts (`safe-precommit`, `py-ci-install-safe-hook`, `py-ci-setup-env`) and every
`python -m py_ci_shared.<module>` command still work. The pre-commit manifest (`.pre-commit-hooks.yaml`)
publishes `py-ci-shared-run-all`, `pinned-tool-versions`, `worktree-hygiene` and `mypy-gate` hooks next to
`mypy-full-manual`.

## Baselines, refresh and what now fails

A baseline accepts the findings a repo already has so a gate can start blocking new ones. In 1.17.0 the
baselined gates moved onto one implementation (`py_ci_shared._core.Baseline`: multiset, sorted, atomic write),
and three things that used to pass quietly now fail:

1. **A missing baseline fails** and names its refresh command; nothing is written. Written only on refresh:
   `code_audit_meta`, `content_hash_version_bump_gate`, `audit_wave_filenames`, `alembic_concurrently`,
   `readme_env_var_parity`, `source_text_claims`, `value_bearing_asserts`, `uncalled_functions`,
   `deferred_drift`, `db_transaction_completeness`, `fail_open_handlers`, `function_length`, `loc_budget`,
   `import_side_effects`. A walk that failed its file floor or could not parse a file never writes a baseline.
2. **A file that cannot be read or parsed is a finding**, not a skip. A BOM, a PEP 263 cookie and CRLF are
   decoded the way the interpreter does. Gates that must tolerate it take `allow_unparsed=True`
   (`uncalled_functions`, `unread_init_params`, `vacuous_loop_assertions`) or `tolerate_unimportable=True`
   (`package_doctests`).
3. **An empty scan fails**: the `min_files` floors count parsed files, and a missing root raises.

A baseline entry whose note starts with `NEEDS-JUSTIFICATION` (what a refresh writes for a new entry) fails a
normal run until someone replaces the note with a reason.

Refresh, three equivalent ways, all of which reach pytest-xdist workers:

```bash
pytest --py-ci-refresh=function_length          # comma list of gate names or flags; bare --py-ci-refresh = all
PY_CI_SHARED_REFRESH=value-asserts pytest ...    # the env var: short names, full flags or "all"
py-ci-shared refresh function_length
```

The per-gate flags (`--refresh-code-audit-baseline`, `--refresh-value-asserts-baseline`, and the new
`--refresh-db-transaction-baseline`, `--refresh-fail-open-baseline`, `--refresh-function-length-baseline` ...)
work where a conftest registers them, with `py_ci_shared._core.register_refresh_options(parser, flags)` in
`pytest_addoption`; `PY_CI_SHARED_REFRESH` and `--py-ci-refresh` work without any registration. Use `=`: `--py-ci-refresh tests/x.py` would read the path as the gate list.

A refresh only **shrinks** a baseline: it drops entries that no longer fire and lowers counts and ceilings. When the
scan finds something the baseline does not accept (a new entry, a higher count, or any finding while the baseline
does not exist yet), the refresh writes the removals, fails, and names each refused entry. Growing it is a
deliberate opt-in, three equivalent ways:

```bash
pytest --py-ci-refresh=function_length --py-ci-refresh-grow
PY_CI_SHARED_REFRESH=function-length PY_CI_SHARED_REFRESH_ALLOW_GROW=1 pytest ...
py-ci-shared refresh function_length --grow
```

or `grow=True` on `_core.Baseline.regenerate/enforce`, `baseline_ratchet.Baseline.regenerate` and the gates' `write_*`
helpers. Gates that keep their own file shapes use `_core.write_ratchet` for the same rule. Two writers are
exempt on purpose: `mutation_teeth` writes every new survivor with a `NEEDS-JUSTIFICATION` note that fails the next
run anyway, and `content_hash_version_bump_gate` pins one version to one hash, which has nothing to grow.

Keys changed in 1.17.0 for some gates, so their baselines or allowlists need one update after upgrading:
`alembic_concurrently`; `dart_scanners` (keys are a hash of the finding without its line number, so an edit
above a finding keeps its key); `marker_runner_coverage` (`known` keys are `file::test`; old file keys go
stale); `value_bearing_asserts` (full expression, counted as a multiset; old truncated keys still match);
`db_transaction_completeness` and `fail_open_handlers` (keys use qualified names); `discarded_model_copy`
(`allowed` keys are `path::qualname`); `gate_integrity` (workflow keys include the job and the step).

Other behaviour changes in 1.17.0:

- `ci_workflow_timeout_gate` wants `timeout-minutes` on the job itself; a step-level timeout does not count.
- `mutation_teeth` fails when a scope produces zero mutants (pass `allow_empty=True` where that is expected);
  `HARNESS_VERSION` is 11, so cached results from older harnesses are not reused.
- `teeth_sweep` reports a case it could not run as `ERRORED`, never as a pass.
- `phantom_markdown_links` resolves a link relative to the file that contains it.
- `gate_integrity`: a repo without a venue (no pre-commit config, no workflows) passes `None`; a path that does
  not exist raises.
- `fail_message_quality` matches its action-word pattern case-sensitively.
- `gate_config_honesty` no longer counts "report" as an advisory word.
- `entry_points_resolvable` accepts a bare module as an entry point (the `pytest11` form).
- `uncalled_functions` never writes a missing baseline: register its refresh flag or set
  `PY_CI_SHARED_REFRESH=uncalled-functions`.
- `worktree_hygiene` exits 2 on a bad ref and never marks staged or ignored work `REMOVABLE`.
- New keyword arguments: `pydantic_field_bounds` `allow_inconclusive=`, `meta_private_imports`
  `recursive=`, `find_truthiness_tests(repo_root=)`, and `module_reload_safety.assert_no_reloads_in_code`
  with a statement-text allowlist.
- `stale_comment_age` dates lines with `git blame`, so it needs full history: check out with
  `fetch-depth: 0`. On a shallow clone it fails with that instruction rather than dating every line to the
  shallow boundary.

## Pinning and releases

Workflows: `uses: fingoldo/py-ci-shared/.github/workflows/<name>.yml@v1`, or a full SHA with the exact tag
as a comment (`@<sha>  # v1.17.0`) when you need a frozen pipeline. Do not mix the two in one repo, and never
write `# v1` next to a SHA: the comment then lies as soon as `v1` moves. Either way the workflow now fetches
this repo's configs and `RUFF_VERSION` at the commit the workflow itself was loaded from
(`github.job_workflow_sha`), not at `master`, so a pin pins everything.

Package: `pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@v1.17.0"`. The
version in `pyproject.toml` and `py_ci_shared.__version__` is the release it becomes when tagged.

Releasing: bump `version` in `pyproject.toml` and `__version__` in `src/py_ci_shared/__init__.py`
(`tests/test_release_version.py` holds them equal and ahead of the newest tag), commit, then push a
`vX.Y.Z` tag. `.github/workflows/release.yml` checks the tag equals the declared version and is on
`master`, runs the test suite, moves `v1` to the tag and creates the GitHub release.

## Code-audit baseline meta-test (`code_audit_meta`)

Shared harness for the "run `pyutilz.dev.code_audit.run_all()` against this repo's own source,
gate on a committed baseline JSON" pattern used by every consumer (glossum_backend_scripts,
llm_bench, realtime_applications, production_scrapers, pyutilz itself, mlframe, algopacksimple).
`pyutilz` and `pytest` are imported lazily inside the functions, not at module level,
so this stays consistent with the rest of the package being otherwise dependency-free — every
real caller already depends on `pyutilz` directly (it's what's being scanned).

In a consuming repo's `tests/test_meta/test_code_audit_baseline.py` (or an equivalent root-level
file for flat-layout repos):

```python
from pathlib import Path
from py_ci_shared.code_audit_meta import assert_no_new_code_audit_findings

import mypackage

def test_no_new_code_audit_findings():
    assert_no_new_code_audit_findings(
        root=Path(mypackage.__file__).resolve().parent,
        baseline_path=Path(__file__).resolve().parent / "_code_audit_baseline.json",
        exclude_dirs=frozenset({"tests", "docs", "legacy"}),  # repo-specific, merged with the built-in cache/vcs defaults
    )
```

And in the same directory's `conftest.py` (or the repo's root conftest.py if there's only one),
register the refresh flag so pytest accepts `--refresh-code-audit-baseline`:

```python
from py_ci_shared.code_audit_meta import register_refresh_option

def pytest_addoption(parser):
    register_refresh_option(parser)
```

## LOC-budget baseline meta-test (`loc_budget`)

Shared harness for the "no production file over N lines" meta-test pattern (realtime_applications
and mlframe each independently wrote a near-identical ~75-line version of this). Same API shape as
`code_audit_meta`: a committed baseline JSON (path -> LOC at capture time) grandfathers existing
oversized files, a `--refresh-loc-budget-baseline` flag reseeds it after an intentional split, and
a small per-file growth slack lets trivial edits to an already-oversized file through while a real
expansion still trips the gate.

```python
from pathlib import Path
from py_ci_shared.loc_budget import assert_no_new_oversized_file

def test_no_new_file_over_1k_loc():
    assert_no_new_oversized_file(
        files=my_production_py_files(),  # project-specific: whatever "production" means for this layout
        root=Path(__file__).resolve().parents[2],
        baseline_path=Path(__file__).resolve().parent / "_loc_over_1k_baseline.json",
    )
```

And register `--refresh-loc-budget-baseline` in `conftest.py` via `py_ci_shared.loc_budget.register_refresh_option`,
same as `code_audit_meta`'s flag above.

## Git-dependency pin check (`git_dependency_pins`)

Fails if any `pyproject.toml` git-URL dependency (`name @ git+https://host/path@ref`) isn't pinned
to a full 40-hex-character commit SHA — a branch name, tag, or short SHA all mean a fresh install
can silently resolve to a different commit than the one actually developed/tested against. No
baseline/refresh mechanism: for a **third-party** git dependency the SHA pin is the only defence,
so there's no legitimate grandfathered case.

```python
from pathlib import Path
from py_ci_shared.git_dependency_pins import assert_all_git_dependencies_pinned

def test_all_git_dependencies_pinned():
    assert_all_git_dependencies_pinned(Path(__file__).resolve().parents[2] / "pyproject.toml")
```

**First-party exemption** (`allow_unpinned_url_prefixes`, added 2026-08-22): a satellite that
depends on another repo *the same owner controls* may declare it without a SHA and let its
committed lockfile (`uv.lock`) pin the resolved commit instead. Reproducibility is unchanged — the
lock is the pin — while bumping becomes `uv lock --upgrade-package <name>` rather than
hand-copying a 40-hex string into every satellite:

```python
    assert_all_git_dependencies_pinned(
        Path(__file__).resolve().parents[2] / "pyproject.toml",
        allow_unpinned_url_prefixes=("git+https://github.com/fingoldo/",),
    )
```

Only exempt a URL that a lockfile actually covers, and only for first-party upstreams; the
allowlist defaults to empty, so third-party deps keep the strict behaviour.

## CI continue-on-error gate check (`ci_workflow_gate`)

Fails if a CI workflow file has a `continue-on-error: true` step/job that isn't on an explicit,
by-name reviewed-advisory allowlist. Catches the case where a lint/security gate step is *supposed*
to block on failure but a copy-pasted or refactored `continue-on-error: true` silently turns it
into a no-op — the job still shows green even though the gate step itself failed. Deliberately
line-based (matches a step/job to the nearest preceding `name:` line above it), not a full YAML
parser, to avoid a new PyYAML dependency for a check this structurally simple.

```python
from pathlib import Path
from py_ci_shared.ci_workflow_gate import assert_continue_on_error_is_reviewed

def test_ci_continue_on_error_steps_are_reviewed():
    assert_continue_on_error_is_reviewed(
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml",
        reviewed_advisory_steps={"Run ruff", "Run black --check", "Run bandit security scan", "Run mypy"},
    )
```

## CI test-dir reachability check (`ci_test_dir_reachability`)

Fails if any `tests/<subdir>` in a consuming repo is never invoked (directly, or via a bare
`pytest tests/` not `--ignore`'d for that subdir) by any CI workflow. Catches a whole test category
silently going CI-blind: a new test subdirectory added and never wired into a workflow's `pytest`
invocation still runs fine locally, so nobody notices merges are no longer gated on it. Generalized
from a check first written directly in a consuming repo (glossum_backend_scripts) after that repo's
own audit surfaced the pattern. Text-based, matching this package's other `ci_*` checks.

```python
from pathlib import Path
from py_ci_shared.ci_test_dir_reachability import assert_every_test_subdir_reachable

def test_every_test_subdir_reachable_by_some_ci_job():
    assert_every_test_subdir_reachable(
        repo_root=Path(__file__).resolve().parents[2],
        workflows_dir=Path(__file__).resolve().parents[2] / ".github" / "workflows",
        intentionally_unreached={"live"},  # e.g. a paid-API tier deliberately excluded from CI
    )
```

## Config call-site vs schema parity (`config_call_site_parity`)

Shared engine for the "`cfg().get(section, key, default, type_)` call site agrees with the
Pydantic schema" meta-test pattern — independently built twice in this ecosystem, catching real
bugs both times: a call site reading a `(section, key)` that doesn't exist in the schema (silently
unreadable — an unknown key is stripped on load, so the call always falls through to its own
hardcoded default), a schema field with zero reader anywhere (a decorative knob), and two call
sites reading the SAME `(section, key)` with a different hardcoded default/type. Assumes a
2-level Pydantic schema (a top-level model whose fields are themselves sub-models, one per config
section) and a `cfg()`/`_cfg()`-named accessor (optionally attribute-qualified, or bound to a
local name first). Each consuming repo supplies its own schema class and, where it has one, its
own whitelist of fields consumed a different way the AST heuristic can't see.

```python
from pathlib import Path
from py_ci_shared.config_call_site_parity import (
    assert_every_cfg_get_call_resolves_to_a_schema_field,
    assert_every_schema_field_has_a_reader,
    assert_no_divergent_cfg_get_call_site_defaults,
    assert_call_site_defaults_match_schema_defaults,
)
from my_project.live_config import AppConfig

ROOT = Path(__file__).resolve().parents[2]
FILES = list(my_production_py_files())  # project-specific, same set used by other meta-tests

def test_every_cfg_get_call_resolves_to_a_schema_field():
    assert_every_cfg_get_call_resolves_to_a_schema_field(ROOT, FILES, AppConfig)

def test_every_schema_field_has_a_reader():
    assert_every_schema_field_has_a_reader(ROOT, FILES, AppConfig, known_indirect_readers={...})

def test_no_divergent_cfg_get_call_site_defaults():
    # Pass default_type_repr if the accessor has its own type_ default (e.g. `type_: type
    # = int`) -- a call site that omits type_ then agrees with one passing it explicitly.
    assert_no_divergent_cfg_get_call_site_defaults(ROOT, FILES, default_type_repr="int")

def test_call_site_defaults_match_schema_defaults():
    assert_call_site_defaults_match_schema_defaults(
        ROOT, FILES, AppConfig, min_checked=50,
        # A call site whose default is DELIBERATELY different from the schema's normal
        # value (a safety-net fallback, e.g. variant_count=1 vs the schema's normal 3
        # for the case where config resolution itself somehow fails) -- not a bug.
        known_intentional_mismatches={("evaluator", "variant_count"): "safety-net single-call fallback, see pipeline/evaluate.py"},
    )
```

Also in this module: `assert_no_module_scope_frozen_cli_defaults` — fails if a `cfg().get(...)`
read sits at MODULE scope (not inside a function/closure) and its result feeds an
`argparse.add_argument(..., default=...)` value. `cfg().get(...)` is normally re-read on every
call so a live config edit takes effect within the reload interval; reading it once at import
time freezes that ONE CLI flag's effective value for the process lifetime while every sibling
knob stays hot-reloadable — invisible in a diff (the code looks like every other `cfg().get(...)`
call site) and only shows up as "I edited config.toml and nothing happened" for that one flag.

```python
from py_ci_shared.config_call_site_parity import assert_no_module_scope_frozen_cli_defaults

def test_no_module_scope_frozen_cli_defaults():
    assert_no_module_scope_frozen_cli_defaults(
        ROOT, FILES,
        known_intentional_freezes={("traffic", "batch_size"): "reviewed, deliberately frozen at startup"},
    )
```

## CHANGELOG promise / fix-sensor cross-walk (`changelog_promise_parity`)

Shared engine for "a CHANGELOG bullet claims something (a fix was made, a follow-up will
happen) — does the claim actually hold." Generalizes two independently-built checks: mlframe's
"every `fix(...)`-tagged bullet must also cite a regression test/sensor" (self-contained
satisfaction — the sensor reference lives in the SAME bullet), and production_scrapers's
"every bullet promising deferred follow-up ('flagged for the final disposition report', 'tracked
under...') must actually be resolved" (cross-document satisfaction — the resolution lives in a
DIFFERENT file, e.g. a `DISPOSITION.md`). Both checks are one call into
`assert_changelog_bullets_satisfy_pattern`, differing only in which satisfaction mode(s) are
wired up.

```python
from pathlib import Path
import re
from py_ci_shared.changelog_promise_parity import (
    assert_changelog_bullets_satisfy_pattern,
    DEFAULT_PROMISE_PATTERN,
)

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
DISPOSITION = Path(__file__).resolve().parents[2] / "DISPOSITION.md"

# mlframe-style: every fix(...)-tagged bullet in a dated audit-cycle section must cite a sensor,
# self-contained within the bullet's own text. Soft threshold tolerates some doc-only fixes.
_AUDIT_SECTION = re.compile(r"^##\s+\d{4}-\d{2}-\d{2}.*?(audit cycle|wave[ -]?\d+)", re.IGNORECASE | re.MULTILINE)
_FIX_BULLET = re.compile(r"(fix\([^)]*\)|\bbug\b|\bregression\b)", re.IGNORECASE)
_SENSOR_REF = re.compile(r"(test_[a-zA-Z0-9_]+\.py|tests/[\w/]+\.py|sensor[: ])", re.IGNORECASE)

def test_each_fix_bullet_cites_a_sensor():
    assert_changelog_bullets_satisfy_pattern(
        CHANGELOG, _FIX_BULLET, _SENSOR_REF,
        section_pattern=_AUDIT_SECTION, max_unsatisfied_fraction=0.15, label="fix bullet",
    )

# production_scrapers-style: every promise ("flagged for the final disposition report", "tracked
# under...") must be resolved -- either mentioned later in CHANGELOG.md itself, or its title
# appearing in DISPOSITION.md. Strict (default 0.0) -- every promise must be kept.
def test_changelog_promises_resolved():
    assert_changelog_bullets_satisfy_pattern(
        CHANGELOG, DEFAULT_PROMISE_PATTERN,
        other_resolution_paths=[DISPOSITION], label="promise",
    )
```

## README env-var documentation parity (`readme_env_var_parity`)

Fails if production code reads an environment variable (`os.environ.get(...)` / `os.getenv(...)`)
that isn't documented in the project's README table — an operator can't set a variable they don't
know exists, and a required-but-undocumented var that fails closed when unset fails *silently*.
Two entry points: `assert_readme_documents_every_env_var` (hard-fail on any gap — use once a repo
is already at zero gap) and `assert_no_new_undocumented_env_vars` (baseline/grandfather style,
same shape as `code_audit_meta`/`loc_budget`, for a repo adopting this with existing
undocumented-var debt — only a NEW gap fails).

```python
from pathlib import Path
from py_ci_shared.readme_env_var_parity import assert_readme_documents_every_env_var

def test_readme_documents_every_env_var():
    assert_readme_documents_every_env_var(
        files=my_production_py_files(),
        readme_path=Path(__file__).resolve().parents[2] / "README.md",
        third_party_vars=frozenset({"HF_HOME", "ANTHROPIC_API_KEY"}),
    )
```

Or, for a repo with existing debt (register `--refresh-readme-env-var-baseline` in `conftest.py`
via `readme_env_var_parity.register_refresh_option`, same as the other baseline-style checks):

```python
from py_ci_shared.readme_env_var_parity import assert_no_new_undocumented_env_vars

def test_no_new_undocumented_env_vars():
    assert_no_new_undocumented_env_vars(
        files=my_production_py_files(),
        readme_path=Path(__file__).resolve().parents[2] / "README.md",
        baseline_path=Path(__file__).resolve().parent / "_readme_env_var_baseline.json",
    )
```

## `.env.example` values load (`env_example_round_trip`)

Fails if a value `.env.example` documents cannot be loaded by the project's pydantic-settings class. A
name-parity check passes while the documented VALUE breaks startup: glossum's example showed
`CORS_ORIGINS=http://localhost:3000,http://localhost:8080`, pydantic-settings JSON-decoded the list field before
its comma-splitting validator ran, and copying the example raised `SettingsError`.

Each documented value, commented out (`# NAME=VALUE`) or not, is put alone into a cleared environment on top of
`base_env` (the minimum the class builds from), and the class is constructed with `_env_file=None`. Any exception
is a failure. Dotenv inline comments are stripped; placeholders (`...`, `<...>`, `your-...`, `changeme`, empty) are
skipped; a floor fails the check when fewer than `min_values` values were tried. One value per trial, so a
cross-field validator does not fire on an unrelated line.

```python
from pathlib import Path
from py_ci_shared.env_example_round_trip import assert_env_example_loads
from myapp.config import Settings

def test_every_documented_value_loads():
    assert_env_example_loads(Settings, Path(".env.example"), base_env={"DATABASE_URL": "postgresql://x"}, min_values=10)
```

## Content-hash / version-bump gate (`content_hash_version_bump_gate`)

Fails if a set of tracked source files changed content but a version constant that's supposed to
be bumped in lockstep (a prompt version, a cache-key version, a schema/serialization version) was
NOT — the classic "forgot to bump the version" bug, where a stale cached/persisted artifact
silently stays valid under the OLD version even though the code that produces it has since
changed. A version bump is self-certifying: if the version constant differs from the baseline's
pinned value, that's accepted as deliberate and the baseline is silently re-pinned — no separate
refresh flag needed for the normal "I bumped it" workflow. Register `--refresh-content-hash-version-baseline`
in `conftest.py` via `content_hash_version_bump_gate.register_refresh_option`, same as the other
baseline-style checks, for bootstrapping a missing/corrupted baseline only.

```python
from pathlib import Path
from py_ci_shared.content_hash_version_bump_gate import assert_version_bumped_with_content
from myproject.prompt_version import USER_PROMPT_VERSION

_SOURCE_FILES = [
    Path(__file__).resolve().parents[2] / "prompt_builder" / f
    for f in ("word_count.py", "truncation.py", "user_prompt.py")
]

def test_user_prompt_version_bumped_when_prompt_builder_changes():
    assert_version_bumped_with_content(
        files=_SOURCE_FILES,
        version=USER_PROMPT_VERSION,
        baseline_path=Path(__file__).resolve().parent / "_user_prompt_version_baseline.json",
    )
```

## Surviving concurrent-session commits (`safe_precommit`)

`pre-commit` stashes a repo's unstaged tracked-file changes to a patch file before running hooks
(so hooks see only staged content), then tries to restore that patch afterward. When multiple git
sessions share one working copy (parallel agents, multiple terminals), another session's commit
can advance `HEAD` while ours is stashed; the restore-patch was computed against the OLD `HEAD`
blob and no longer matches the new one, so `git apply` fails even after pre-commit's own
checkout-and-retry, and the uncaught error aborts the ENTIRE commit — even though every real hook
(mypy, tests, lint...) already ran and passed. The files actually being committed are unaffected;
only some OTHER unstaged edit (not part of this commit) couldn't be silently restored.

`py_ci_shared.safe_precommit` is a drop-in `pre-commit` replacement that patches this one failure
mode to a warning (the un-restorable patch is preserved on disk, never silently dropped) instead
of aborting. One-time per clone, after `pre-commit install`:

```bash
python -m py_ci_shared.install_safe_hook
```

This rewrites the generated `.git/hooks/pre-commit` to invoke `python -m
py_ci_shared.safe_precommit hook-impl ...` instead of `python -mpre_commit hook-impl ...`, so plain
`git commit` gets the patched behavior automatically — no wrapper command to remember, and it
works on any machine as soon as `pip install -e ".[dev]"` (or however the consuming repo installs
this package) makes `py_ci_shared` importable in that Python. Idempotent; safe to re-run any time,
including after `pre-commit install` regenerates the hook file (which resets this override).

Re-running `python -m py_ci_shared.install_safe_hook` after each `pre-commit install` (or `pre-commit autoupdate`) is the only maintenance this needs.

## Checks derived from the 2026-09-02 Flutter audit round

Eleven modules added after a full audit of two Flutter repositories (`polyvocab_app`,
`flutter_app_core`) filed 271 findings, of which 154 turned out to be statically detectable and
only three had been caught by a blocking gate. Each module is one finding class, generalised to
whatever language the repository is written in; the docstring of each names the findings it would
have caught. They are consumed exactly like the checks above -- a `find_*` function returning a
list of problem strings, and an `assert_*` wrapper that imports `pytest` lazily -- so a Dart or
TypeScript repository with no pytest harness can call the `find_*` half from a plain script.

| Module | Answers |
|---|---|
| `sql_verify` | Does PostgreSQL actually accept every SQL statement this project ships? A unit suite drives its loaders through a fake cursor, which accepts any string at all -- so an interpolated projection with a stray comma, or a `WHERE job_uid` against a table keyed on `uid`, ships green. Provides the harness (`dsn_from_env`, `check`, `check_loader`, `run_checks`) and leaves the statement inventory to the consumer. `psycopg2` is imported lazily, so this package stays dependency-free. Run it **pre-push**, with `--skip-without-db` so a checkout with no database can still push. |
| `sql_function_privileges` | Is a `SECURITY DEFINER` function still executable by every signed-in user? PostgreSQL grants `EXECUTE` to `PUBLIC` by default; on Supabase that is one HTTP call from any session. This is the check that would have caught the round's only P0 (a function that granted the caller a permanent admin plan). Also enforces `SET search_path`. |
| `ci_workflow_paths` | Does a workflow name a `working-directory` or run a script that does not exist? Optionally: is there a top-level `permissions:`, and is every third-party action pinned to a SHA? |
| `hook_hygiene` | Does a git hook skip a missing guard silently, stage files the author did not, decide a verdict by grepping a tool's human-readable output, or run guards CI never runs? |
| `repo_hygiene` | Are generated artefacts tracked, required config files missing, or does a numeric CI gate pass when its input fails to parse? |
| `timezone_honest` | Does non-test code carry a naive `datetime.now()` / `date.today()` (ruff's `DTZ` rules)? What it adds over `ruff check --select DTZ .` is refusing the three ways that command reports clean over code it never read: a directory listed in `[tool.ruff] exclude` that holds Python and is not scanned, a scan path that does not exist (ruff says "All checks passed!" and exits 0), and `force-exclude` silently dropping explicit paths. Allowances are `(path, rule)` mapped to a reason, and a stale one fails. |
| `test_partition_reachability` | Is a declared test tag, Playwright project or standalone script selected by any runner, or does a permanent `test.skip` sit at the top of a spec? |
| `baseline_hygiene` | Does every accepted baseline entry carry a human reason, is any entry stale, and does any contain an absolute path? Also exposes `body_asserts_only_absence_of_crash` for assertion scanners. |
| `import_layering` | Does the layer that exists to be reusable import the product it was extracted from? Rules are `from_glob !-> to_glob`; both relative and `package:` imports resolve to the same repo-relative path. |
| `stale_comment_age` | How old is that TODO? `git blame` decides; an issue reference exempts a line. Also catches commented-out calls. |
| `arb_checks` | Flutter `.arb` catalogues: key parity, ICU plural per locale's own CLDR categories, a `{count}` outside a plural, informal register (advisory), dead keys. |
| `dart_scanners` | Fifteen structural scanners over Dart source (painters and animations, repaint isolation, hardcoded UI strings, tappable/semantics hygiene, non-directional layout, parse/serialise/catch, provider state, file size, empty catch, source-text tests, import/export cycles, assertion-free tests, timed dismissal, double error reports, unused test seams) and the file listing/reader/pubspec helpers, returning the `{key: description}` shape a repo's own baseline ratchet already consumes. |
| `edge_function_hygiene` | Serverless functions: a catch that answers 200, an uncapped request body, a secret compared with `===`, an IP in a log line, the forgeable first `x-forwarded-for` hop. |
| `guard_population` | Runs each guard's own file-selection command and fails when it matches nothing -- the failure mode where a guard has been passing for weeks without examining a single file. |
| `version_tag_currency` | Does the declared version have a tag, is that tag reachable from HEAD, and how many releases behind is a consumer's pin? |
| `mutation_teeth` | Can a test that claims to pin a defect actually FAIL? Mutates the code at token level, runs the tests that claim to cover it, and reports the mutants nothing caught. Runs on a copy of the working tree, caches on the target's transitive import closure, and refuses to report a result when pytest exits for a reason other than pass or fail. |
| `git_changed_lines` | The line ranges a diff actually touched, per file, from `--unified=0` -- so a check can be scoped to the lines a commit changed rather than a whole module. |
| `pinned_tool_versions` | Do the ruff CI runs, a repo's own `ruff==` pin and the ruff its interpreter has all agree? The CI version is defined once, as `RUFF_VERSION` in `tool_versions`, and `ruff-blocking.yml`, `lint-advisory.yml` and `self-ci.yml` read it at run time instead of each carrying a literal. Wire `python -m py_ci_shared.pinned_tool_versions` as a `language: system` hook ahead of the ruff hook; it names both versions and the fix. It exists because the literal was copied by hand and drifted both ways: one consumer's pin moved ahead of the workflows it calls, others ran a newer interpreter ruff than their pin. |

### Consuming these from a Dart repository

There is no pytest harness in a Flutter repo, but there is already Python on the hook and in CI, so
the whole cost is one script that calls the `find_*` halves and exits non-zero:

```python
# tool/check_shared_scanners.py
from pathlib import Path
from py_ci_shared.sql_function_privileges import find_unlocked_definer_functions
from py_ci_shared.arb_checks import find_plural_problems

REPO = Path(__file__).resolve().parents[1]
problems = find_unlocked_definer_functions(REPO / "supabase" / "migrations")
problems += find_plural_problems({"en": ARB / "app_en.arb", "ru": ARB / "app_ru.arb"})
if problems:
    raise SystemExit("\n".join(problems))
```

Install it the same way the Python consumers do: `pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared@<sha>"`.

## LLM-output preservation gates

Static checks that an answer the model was asked for, and paid for, is not thrown away. Written for glossum
and autopsia, which store to PostgreSQL and to JSONL/JSON files respectively, so nothing in them assumes
either store. The runtime halves (the attempt archive, the sentinel sweep, the blast-radius diff) live in
`pyutilz.dev`, because they import `pyutilz.llm` or touch a live store.

- `llm_call_archive_gate` -- every paid call goes through the place that keeps its raw answer:
  `find_direct_sdk_calls` (`.messages.create`, `.chat.completions.create`, `.generate_content`),
  `find_unwrapped_providers` (a provider class or an unwrapping factory used outside the wrapping
  factory), `find_generate_calls_without_archive` (a module calling `generate*` that never names the
  archive), and `assert_every_llm_call_is_archived`, which takes an `allowed` file -> reason mapping and
  fails for a stale entry.
- `prompt_field_parity` -- a field a prompt or dict schema asks for is read by something and stored:
  keys from prose fences (`keys_in_source`) and directly from dict schemas (`keys_in_schema`); consumers
  with benchmark/gold modules excluded; run-time keys only through a real accessor; storage as SQL DDL
  plus binds or as a file writer's keys; and the gate's own blind spots as checks (`invisible_keys`,
  `undemonstrated_fields`, `structural_names_prompted`). Baselining is the caller's.
- `save_failure_markers` -- every `<name>_failed` marker a writer emits is fatal per the caller's
  predicate or listed as non-fatal with a reason; stale and wrongly-listed entries fail.
- `dataclass_case_completeness` -- every `@dataclass` matching a name pattern has a test case or an
  exemption with a reason; stale names fail.

## Fail-open handlers in gate code (`fail_open_handlers`)

A gate that keeps a candidate whenever evaluating it raises is switched off for exactly the candidates that fail. `find_fail_open_handlers(files, repo_root)` reports four shapes: an `except` handler that appends the loop's own element to a kept list (`admit_on_error`), an `except` that returns `True` in a function named like a decision (`gate_returns_true`), a fallback assignment logged only at DEBUG/INFO (`quiet_substitution`, where a `# best-effort: <reason>` comment on the `except` line marks a fallback that cannot change a result), and a reject guarded by `isfinite(x) and x >= thr`, which NaN and inf skip (`finite_guarded_reject`).

```python
from py_ci_shared.fail_open_handlers import assert_no_new_fail_open_handlers

def test_no_new_fail_open_handlers():
    assert_no_new_fail_open_handlers(files=GATE_FILES, repo_root=REPO_ROOT, baseline_path=BASELINE)
```

The baseline is a ratchet keyed `path::function::rule` and counted per key; a key listed `n` times (`key`, `key#2`, ...) accepts `n` findings, and an entry with nothing left to accept fails as stale.

## Assertion shapes that cannot fail (`nondiscriminating_shapes`)

`shape_reasons(func)` returns the slugs a test function exhibits: `wide-literal-range` (`assert 0 < rmse < 100`, literal bounds spanning 20x, or from <= 0 to >= 10; `0 <= p <= 1` is exempt), `envelope-assert` (`pred.min() > 0.5 * y.min()`), `median-roundtrip` (a median absolute error in a round-trip / inverse test) and `late-skip` (a `pytest.skip` after the test computed something, outside an environment probe). `SHAPE_HELP` maps each slug to the fix. The consuming repository chooses the scope and the baseline.

## Runtime writes to a module registry (`runtime_registry_mutation`)

A registry filled at import time is rebuilt by every interpreter; an entry added from inside a function exists only in the process that ran it, so a pickled object naming it fails to load anywhere else. `find_runtime_registry_writes(files, repo_root)` reports every function-scope write (`X[k] = v`, `setdefault`, `update`, `pop`, `del`) to a module-level dict whose name contains `REGISTRY`, defined in any scanned file (writes usually go through an import). Helpers used as a module-scope decorator or called in a module-scope statement run at import and are exempt.

```python
from py_ci_shared.runtime_registry_mutation import assert_writes_have_replay

def test_runtime_registry_writes_have_a_replay():
    assert_writes_have_replay(files=SRC_FILES, repo_root=REPO_ROOT, replay_writers={"reregister_plugins": "called from __setstate__"})
```

Each `replay_writers` entry needs a reason (how its entries are replayed on load, or why no pickled object names them); an entry that no longer writes a registry fails as stale.

## Config copies nobody receives (`discarded_model_copy`)

`cfg2 = cfg.model_copy(update={...})` leaves the caller's `cfg` untouched, so a copy that is never returned, stored on an object or container, or passed to a call is an override every later consumer misses. `find_discarded_model_copies(files, repo_root)` reports each such local, following plain aliases (`base = copy; run(base)` counts as used).

```python
from py_ci_shared.discarded_model_copy import assert_no_discarded_model_copy

def test_no_discarded_model_copy():
    assert_no_discarded_model_copy(files=SRC_FILES, repo_root=REPO_ROOT, allowed={})
```

`allowed` maps a function name to the reason its copy is intentionally local; stale and empty-reason entries fail.

## Constructor parameters nothing reads (`unread_init_params`)

A stored-and-never-read `__init__` parameter still appears in `get_params()`, in a repr and in a grid search, so setting it changes nothing and reports nothing. `find_unread_init_params(files, repo_root)` reports each one. A parameter counts as read when the `__init__` body does anything with it beyond a plain store (the attribute may be renamed on the way in: `self._p = p`), or when the attribute it lands in is read anywhere in the scanned files, through any receiver, `getattr(x, "p")` or a string literal (a `get_params` list, a state key) included.

```python
from py_ci_shared.unread_init_params import assert_no_unread_init_params

def test_no_unread_constructor_parameters():
    assert_no_unread_init_params(files=SRC_FILES, repo_root=REPO_ROOT, allowlist={"random_state": "sklearn meta-parameter"})
```

`allowlist` maps a parameter name to the reason it is accepted unread; stale and empty-reason entries fail.

## Boolean env flags parsed by hand (`env_flag_parsing`)

`not os.environ.get(NAME)` turns the flag on for `NAME=0`; `lower() in {"1","true","yes"}` ignores `on`; `== "1"` ignores everything else. One package carried all three, so an operator who wrote `FLAG=0` got the opposite of what they asked for from one switch and the right answer from the next. `find_hand_parsed_env_flags(files, repo_root, prefixes)` reports every read of a prefixed variable used as a boolean - a truth test, `not`, a comparison with a string literal, or membership in a literal collection - so they can move to one shared `env_flag(name, default)`. Value reads (numbers, paths, backend names) never appear in those contexts and are not reported.

```python
from py_ci_shared.env_flag_parsing import assert_env_flags_use_one_parser

def test_env_flags_go_through_env_flag():
    assert_env_flags_use_one_parser(files=SRC_FILES, repo_root=REPO_ROOT, prefixes=("MYPROJ_",), allowed={})
```

`allowed` maps a variable to the reason it is read directly (a presence test for a numeric override, say); stale and empty-reason entries fail.

## getattr fallbacks that contradict the config (`config_getattr_default_parity`)

`getattr(cfg, "field", <literal>)` is read defensively so a duck-typed config still works, and that literal becomes the stand-in's effective default. When it disagrees with the schema's, the setting means two things depending on which object the caller passed: one gate read `reject_on_alpha_drift` as False while the config declared True, so a duck-typed config kept the specs the real one dropped. `find_getattr_default_mismatches(files, repo_root, schema_classes)` reports each such site with both values. Fields no schema declares, required fields, `default_factory` fields and fields two schemas disagree about are skipped, and `receiver_names` / `receiver_suffixes` pick which objects count, so a repository with several configs does not read a shared field name off the wrong one.

```python
from py_ci_shared.config_getattr_default_parity import assert_getattr_defaults_match_schema

def test_getattr_defaults_match_the_config():
    assert_getattr_defaults_match_schema(files=SRC_FILES, repo_root=REPO_ROOT, schema_classes=[MyConfig], allowed={})
```

`allowed` maps a field to the reason its sites may differ; stale and empty-reason entries fail.

## Metrics scored on survivors only (`survivorship_scoring`)

`rmse(y[finite], pred[finite])` measures the model where it produced a number at all, and the rows it dropped are the ones it failed on. The deployed predictor has no such option: it fills them or falls back, so a gate written this way under-rejects the collapse it exists to catch. `find_survivorship_scoring(files, repo_root)` reports every metric call whose two arguments are indexed by the same `isfinite(<prediction>)` mask, unless the enclosing function fills the dropped rows or reports the dropped fraction as part of its verdict. Counting the finite rows to reject below a floor is not a remedy: the survivors are still scored alone.

```python
from py_ci_shared.survivorship_scoring import assert_no_survivorship_scoring

def test_metrics_are_not_scored_on_survivors_only():
    assert_no_survivorship_scoring(files=SRC_FILES, repo_root=REPO_ROOT, allowed={})
```

`allowed` maps `path::function` to the reason that site scores survivors on purpose; stale and empty-reason entries fail.

## Tests that concede a defect and pin it (`conceded_defect_pins`)

A test whose docstring or leading comment concedes the behaviour is wrong - "does not perfectly reconstruct", "is a no-op", "lossy by design", "known defect" - while its body pins that behaviour with exact equality turns the defect into a contract: the fix turns it red and gets reverted. `find_conceded_defect_pins(files, repo_root)` lists them. The same words appear in honest tests (a docstring explaining why a value is lossy while asserting a bound), so this is a reading list with a count ratchet, not a per-site failure. A deliberately pinned defect lives in a test named `test_known_defect_<id>_...`, which the scan accepts.

```python
from py_ci_shared.conceded_defect_pins import find_conceded_defect_pins

def test_conceded_defect_pins_do_not_grow():
    assert len(find_conceded_defect_pins(TEST_FILES, REPO_ROOT)) <= RECORDED_COUNT
```

## Advice in messages must be tested (`printed_advice`)

A log line or error that tells the reader to act - "raise mi_sample_n or lower mi_nbins", "pass it via base_candidates=[...]", "Set drop_invalid_rows=True" - is read only after something went wrong, so advice naming a renamed knob, or one that does not change what it promises, goes unnoticed. `find_printed_advice(files, repo_root)` returns every such message literal passed to a logging call, `warnings.warn`, `print` or an exception, keyed `<path>::<enclosing function>#<n>` so the key survives line moves. Keep a table from each key to the test that follows the advice and observes its effect, and fail on unregistered or stale keys.

```python
from py_ci_shared.printed_advice import find_printed_advice

def test_every_printed_advice_has_a_test():
    assert {a.key for a in find_printed_advice(SOURCE_FILES, REPO_ROOT)} == set(PRINTED_ADVICE_TESTS)
```

## Row selections that lose the index order (`order_losing_filters`)

`frame.iloc[idx]` returns rows in `idx` order; its polars twin written as `mask[idx] = True; frame.filter(mask)` returns them in frame order. The branches agree only while `idx` is sorted, so a shuffled or time-reversed index silently pairs rows with the wrong targets. `find_order_losing_filters(files, repo_root)` flags a function that selects rows positionally (`.iloc[idx]`, `.take(idx)`, `.gather(idx)`) and also by a boolean mask built from that same index (`m[idx] = True`, `np.isin(x, idx)`, `.is_in(idx)`), keyed `path::function::mask`. A mask with no positional twin is not flagged.

```python
from py_ci_shared.order_losing_filters import find_order_losing_filters

def test_no_order_losing_row_filters():
    assert not find_order_losing_filters(SOURCE_FILES, REPO_ROOT)
```

## Arguments a wrapper drops (`kwarg_forwarding`)

Three finders for optional arguments lost between a wrapper and what it wraps, each keyed so a repository can allow-list the deliberate ones with a reason:

- `find_dropped_variant_params(files, root, delegates=...)`: a variant (`fit_stacked` of `fit`, or any pair in `delegates={"path::variant": "path::base"}`) that neither forwards nor `**`-passes an optional parameter of its base.
- `find_available_but_not_passed(files, root, delegates=...)`: a call that omits an optional `None`-default parameter `p` while the caller has a parameter named `p`.
- `find_delegate_state_loss(files, root, methods=...)`: a method that builds `obj = SameClass(...)` and calls `obj.m()` without copying a private attribute that `m` (or anything it calls with the object) reads, that nothing on its path sets, and that some code injects onto the object from outside. Methods bound after the class body (`Cls.m = f`) and functions annotated `self: "Cls"` count as methods.

```python
from py_ci_shared.kwarg_forwarding import find_dropped_variant_params

def test_variants_forward_their_bases_options():
    assert not find_dropped_variant_params(SOURCE_FILES, REPO_ROOT, delegates=DELEGATES)
```

## Using the shared ruff config

Ruff natively supports `extend = "<path>"` pointing at another ruff config file — a real merge (select/ignore/per-file-ignores/pep8-naming all combine), not copy-paste. Since 1.17.0 both configs also ship inside the installed package: `py-ci-shared config-path ruff-base` prints the real path, so `export PY_CI_SHARED_DIR="$(dirname "$(dirname "$(py-ci-shared config-path ruff-base)")")"` (the package directory, which holds `configs/`) works without a clone. Ruff needs a real filesystem path, resolved at ruff-invocation time, and it DOES expand `~` and environment variables in that path (docs.astral.sh/ruff/settings), so consuming repos point at an env var instead of a fixed relative location:

```toml
# consuming repo's pyproject.toml
[tool.ruff]
extend = "$PY_CI_SHARED_DIR/configs/ruff-base.toml"
target-version = "py39"          # each repo's own minimum-Python floor
exclude = [...]                  # each repo's own dir excludes

[tool.ruff.lint]
extend-select = [...]            # optional: rules on TOP of the shared select
extend-ignore = [...]            # optional: rules ignored on TOP of the shared ignore

[tool.ruff.lint.per-file-ignores]
"src/<pkg>/**" = [...]           # each repo's own path-specific ignores (these MERGE with the base)

[tool.ruff.lint.mccabe]
max-complexity = <N>             # each repo's OWN measured threshold -- never copy another repo's number
```

`PY_CI_SHARED_DIR` just needs to point at SOME real `py-ci-shared` checkout — it no longer has to be a sibling of the consuming repo (that was the prior convention through 2026-07-22; a repo move or an unusual checkout layout would silently break the relative path). Clone this repo anywhere and point the var at it:

```bash
git clone https://github.com/fingoldo/py-ci-shared.git ~/dev/py-ci-shared
```

Then set `PY_CI_SHARED_DIR` **persistently at the OS/user level**, not just in a shell rc file — a GUI-launched editor (VS Code opened from the Dock/Start Menu rather than `code .` from a terminal) doesn't always inherit shell-only exports, especially on macOS, and would silently lint against the unmerged base config instead of the full ruleset. Either do it by hand (`setx PY_CI_SHARED_DIR <path>` on Windows; a `launchctl setenv` + LaunchAgent plist on macOS; a `~/.config/environment.d/*.conf` entry on Linux) or run the bundled helper once, which does the OS-appropriate thing for you:

```bash
pip install -e ~/dev/py-ci-shared
python -m py_ci_shared.setup_env
```

Restart your terminal/IDE afterward so the new value is picked up.

CI resolves `PY_CI_SHARED_DIR` itself — see `ruff-blocking.yml` / `lint-advisory.yml`'s "Resolve PY_CI_SHARED_DIR" step, which fetches this repo at the exact commit the reusable workflow was loaded from (`github.job_workflow_sha`), so the config and `RUFF_VERSION` match the pin — a calling repo's own workflow doesn't need to do anything extra.

**CRITICAL:** never invoke ruff with `--select <subset>` in a blocking gate — it REPLACES the effective rule set instead of narrowing the extended config, silently dropping the whole shared ignore list and breaking RUF100's own "is this noqa still needed" determination. Always use `--ignore <code>` to ADD to the resolved ignore list. See `configs/ruff-base.toml`'s header comment and the `mlframe`/`pyutilz` `CLAUDE.md` files for the incident this rule postdates (2026-07-09).

## Worktree hygiene report (`worktree_hygiene`)

Reports which linked worktrees, leftover directories and local branches hold nothing that is not already
somewhere durable, and which still carry unsaved work. Reports only: it never removes anything.

```
python -m py_ci_shared.worktree_hygiene /path/to/repo           # text
python -m py_ci_shared.worktree_hygiene /path/to/repo --json    # machine-readable
```

A multi-session project accumulates worktrees faster than anyone retires them. Measured 2026-09-15 across
eight repos here: ~200 worktrees and ~100 local branches were removable, and six directories under
`mlframe/.claude/worktrees` each still held a 100 MB copy of the tree while appearing in no git listing at
all, because the registration was already gone.

The verdict per path answers "does this hold anything not already saved", which is three questions, not one:

* **Identical upstream.** Landing a file copies it; the original stays behind and reads as dirty forever.
  Both line-ending spellings are compared, or a byte comparison lies on Windows.
* **Committed at some point.** A copy left on an old commit differs from today's default branch in thousands
  of files while holding nothing new: every blob is reachable from some ref. This is what separates a stale
  copy from unsaved work, and it is the check a human skips.
* **Neither** -- real uncommitted work. Reported as `REVIEW` with the paths as evidence, never as removable.

A deletion is not unsaved work (what it removes is still in the ref), and a worktree whose HEAD no remote
branch contains is `REVIEW` even when its working tree is clean. Dot-directories beside the worktrees are
left alone: a shared tool cache lives there on purpose (`.mlframe_mypy_cache_shared`).

An unregistered directory is judged by walking its files, not by asking git: `git -C` inside one resolves to
the enclosing repository and answers about that instead, reporting the directory clean whatever it holds.

## Keeping this repo in sync with consumers

`configs/consumers.toml` lists every consumer (owner/repo, branch, private, corpus). Two scheduled workflows read it:

- `consumer-pins.yml` (daily): shallow-clones each consumer and runs `adoption_matrix --resolve-in .`, failing on
  disagreeing, moving or stale pins ("N releases behind vX.Y.Z") and silent skips. The matrix is in the job summary
  and the `adoption-matrix` artifact. Private repos need the `CONSUMER_READ_TOKEN` secret (a fine-grained token with
  contents: read on them); without it they are skipped with a warning.
- `corpus-drift.yml` (nightly): `python -m py_ci_shared.corpus_drift` runs every corpus-bindable `find_*` over the
  `corpus = true` repos and compares the counts with the last successful run's snapshot artifact. It fails when a
  count grows by more than 20% and more than 5, drops to zero, or a finder starts to raise; run it by hand with
  `accept: true` to take a reviewed change as the new baseline.


A weekly scheduled workflow (`config-drift-check.yml`, running `py_ci_shared.config_drift_check`) fetches both consumer repos' `pyproject.toml` and reports (informationally, never failing the run) any divergence in their `[tool.ruff]`/`[tool.mypy]` fields that are meant to stay in sync — trigger it on demand via `workflow_dispatch`. It does not replace opening a matching PR when you change something here that consuming repos should also pick up — `git grep py-ci-shared` in each consumer finds every reference point.
