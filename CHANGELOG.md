# Changelog

Milestones only; the commit log has the detail. Versions are the release tags (`vX.Y.Z`).

## Unreleased

- `lint-advisory.yml` gains `pip-audit-uv-lock` (and `pip-audit-skip-packages`): it exports the calling repo's `uv.lock` and audits the exact locked versions
  with `--no-deps --disable-pip`. Without it the job resolved the project itself and failed whenever a dependency was git-sourced (mlframe's `pyutilz`), so the
  scan ran on nothing; against mlframe's lock it audited 366 dependencies and reported 31 advisories in 10 packages.

- **Behaviour change**: `module_reload_safety.find_unpaired_reloads` no longer counts a second `importlib.reload` as a restore, whether in an
  `addfinalizer` callable or in a `finally`. Reloading again mints a third set of class objects instead of putting the originals back, so a
  model pickled afterwards carries them by value (dill `_create_type`) and a restricted loader refuses it; mlframe's `lgb_shim` test did exactly
  this and failed two unrelated round-trip tests in a shared worker. What to do: take `saved = dict(module.__dict__)` before the reload and write
  it back with `module.__dict__.update(saved)` (clear first if names were added), or restore through a fixture that does.

- Thirteen new gates from the mlframe audit of 2026-10-04, each with canaries and a suppression comment that needs a reason:
  `cancellation_prone_moments` (variance, skewness and kurtosis from raw power sums, which cancels on large-offset data),
  `unsafe_deserialization` (a `find_class` that allows whole modules or gadget modules, `torch.load` without `weights_only`, `np.load(allow_pickle=True)`,
  unverified pickle and joblib loads, unsafe `yaml.load`), `unread_function_params` (`sample_weight`, `seed`, `n_jobs` and similar accepted and never read),
  `hardcoded_seed_in_library` (an int literal seed in a function that has its own `random_state` or `seed`), `constant_fallback_cache_key` (a digest or key
  builder that returns a constant from an `except`, so distinct inputs collide), `ci_default_branch_never_cancelled` (`cancel-in-progress` that can be true on a
  push to the default branch), `ci_install_covers_entry_imports` (a workflow that installs fewer extras than its entry module imports; plus
  `assert_entry_imports_without_extras`), `optional_imports_guarded` (a module-level import of a distribution that only an extra declares),
  `single_shot_timing_assertion` (a speedup or ceiling asserted from one measurement per side, or a skip placed after the timing), `cuda_kernel_integer_width`
  (`long` in a CUDA kernel source, 32 bits under Windows NVRTC), `id_keyed_cache_validates_identity` (a cache keyed by `id(obj)` that never checks identity),
  `persisted_negative_probe` (a failed hardware probe written to disk as a negative verdict). New library module `pytest_known_gap` (`known_gap(reason,
  gap_closed)`: xfail while a gap is open, fail when it closes).

- **Behaviour change**: `no_xfail_to_defer` follows `known_gap(...)` calls (from `py_ci_shared.pytest_known_gap` or the modules named in
  `known_gap_modules`, default `tests._known_gap`): the reason is judged like a `pytest.xfail` reason, and an unguarded call whose `gap_closed` is a falsy
  literal (a gap that can never close, so can never fail) is a finding. What to do: give each `known_gap` reason a tracked issue or an external component, and
  make `gap_closed` a measured condition.
- New opt-in gate `env_write_restore` (`assert_env_write_restore(root, baseline_path=...)`): a test function that writes `os.environ` (assignment, `del`, `update`/`setdefault`/`pop`, `os.putenv`) and never restores it leaks the setting into every later test in the process, so the victim is a different innocent test each run and passes alone (mlframe: three `MLFRAME_*` strict-GPU flags set directly made a bit-identity test fail 4 runs in 5). The import-time half stays in `import_side_effects`. A write is accepted inside `patch.dict` / `monkeypatch.context`, in a function with a `try` whose `finally` writes the environment, in a yield-fixture that writes again after the yield, in a `setUp` whose class tears it down, with `addfinalizer`/`addCleanup`, or in a function named as a restorer. Findings are keyed `rule::path::function: statement` (no line numbers) and ride the ratchet `Baseline`; seed a first baseline with `grow=True`.
- **Behaviour change**: `sql_verifier_coverage` now recognises a statement written `(` + a `--` comment + the keyword (a parenthesised subquery constant with a leading comment); it was invisible to the gate, so a verifier list could omit it unnoticed. What to do: list each newly found name in `STATEMENTS`, or exclude it with a reason.
- New opt-in gate `psycopg2_param_arity` (`assert_psycopg2_param_arity(root)`): a `cur.execute(sql, params)` whose statement resolves to text (a literal, a `+` chain, a module-level constant) and whose parameters are a literal tuple, list or dict must give one value per `%s` and a key for every `%(name)s`. `?`/`$1` statements (DuckDB, sqlite3) are skipped.
- New opt-in gate `module_state_test_reset` (`assert_module_state_test_reset(root, tests_root)`): a module-level dict/list/set that a function mutates and that no test file mentions has no test-side reset, so one test's entries leak into the next.
- `sql_verify` gains the helpers every project's verifier used to copy: `normalise_psycopg2_idioms` (`VALUES %s`, `IN %s`, `{name}` templates, a leading `SET`, `REFRESH`), `to_positional` and `without_session_prefix`.
- **Behaviour change** (audit 2026-10-03): `sql_verify` no longer reads the `%s` inside a doubled `%%` as a placeholder (`ILIKE '%%suspended%%'` was prepared with an invented parameter and failed with `could not determine data type of parameter $2`). What to do: nothing; a statement that failed only because of this now passes.
- New opt-in gate `script_entry_points` (`assert_script_entry_points(root, script_dirs, probe_dirs=...)`) for packages of flat top-level scripts:
  an entry point (`if __name__ == "__main__":`) that a sibling imports by bare name, at any depth, must register itself with
  `sys.modules.setdefault("<name>", sys.modules["__main__"])` (otherwise the import executes the file a second time with its own globals);
  scripts and probes must not put the working directory on `sys.path`; two script directories must not define the same module name.
  `double_execution_findings(...)` is the opt-in behavioural half: one subprocess per exposed entry point loads it as `__main__` without its work
  block and asserts `import_module(name)` returns the same object (timeout, and a skip table in which every skip carries a reason).
- New opt-in gate `ci_pin_version_skew` (`assert_installed_matches_ci_pin`, `find_pin_skew_findings`): compares the `# vX.Y.Z` comment on the py-ci-shared SHA
  pins in a consumer's workflows with the installed `py_ci_shared.__version__`. Installed older than a pin is a finding; installed newer is one unless
  `tolerate_ahead=True`; a SHA pin without a version comment, mixed pins across workflows and (with `known_releases`) a pin more than `max_lag` releases behind are
  reported too. Offline: it cannot check that a SHA is the commit its comment names.
- New opt-in gate `statement_real_engine_coverage`: every SQL statement constant a package defines (found by `sql_verifier_coverage.sql_constants`) must be referenced by name from
  a test of the real-engine tier, which the caller defines with `engine_test_dirs` and/or `engine_markers`; one level of `import ... as` alias is followed. A statement without
  one is a finding, ratcheted by a shrink-only baseline of `module.NAME -> reason`. `sql_constants` gained an optional `use_git`.

- **Behaviour change** (audit 2026-10-03, SQL-26): `sql_verifier_coverage` reads more of what a package sends. A constant built with `+`
  (`_CTE + "SELECT ..."`, resolving module-level names bound to strings; an operand the module does not bind leaves the statement seen by
  its literal), one that starts `REFRESH`, and one led by a session setting (`SET LOCAL TimeZone = 'UTC'; INSERT ...`) are now SQL
  constants, so a verifier whose `STATEMENTS` list omits them fails the "not checked" direction, and a list that names them no longer fails the
  "constant that no longer exists" direction. What to do: list each newly found name in `STATEMENTS`, or exclude it with a reason, and make the
  verifier PREPARE it (a `REFRESH` through a read of the view, a `SET` prefix stripped first). On the audit's two consumers this found
  seven statements in `production_scrapers` and four in `realtime_applications` that no list had ever carried.
- The pytest plugin accepts the `[tool.py_ci_shared.secret_shapes]` table that `tracked_secret_shapes` documents as its config; it used to reject it as
  an unknown key and fail the collection of every test in a repo that followed the documentation.

- `resource_leak_guard` no longer reports a connection the test already closed: a socket in FIN_WAIT1/2, TIME_WAIT, CLOSING or LAST_ACK stays
  listed until the peer finishes the teardown (seconds, against a remote database), so every test that closed its connection properly failed at teardown.

- New gate `schema_snapshot_parity`: a `schema.sql` that promises to provision a database from scratch is applied twice on a private
  throwaway Postgres (`embedded_postgres`) and its catalogue compared with a committed, versioned production snapshot: tables and
  columns missing, types (timezone-ness included), nullability, generated/identity, extras (allowances need a reason, stale ones are
  findings), optionally index names. No server means NOT CHECKED, loudly, never a pass. `refresh` writes the snapshot through a
  read-only DSN named by an environment variable.

- New gate `ddl_lock_safety` (`assert_ddl_files_lock_safe`, `find_ddl_lock_findings`): hand-applied `.sql` files that change a live table must bound the lock wait (`SET [LOCAL] lock_timeout` before the first `ALTER TABLE`; `0` and `DEFAULT` do not count), add `CHECK`/`FOREIGN KEY` constraints `NOT VALID`, build indexes `CONCURRENTLY` (and never inside an explicit `BEGIN` block), and not rewrite the table (volatile `ADD COLUMN ... DEFAULT`, `serial`, `GENERATED ... STORED`, `ALTER COLUMN ... TYPE`) unless the file says `-- rewrite-ok: <reason>`. A paste-ready `--   command` comment header counts as a command. Small tables are allowed through a caller-supplied `tiny_tables` mapping with a reason each. Opt-in: nothing calls it until a consumer's test does, so it cannot turn a green consumer red; a baseline lets a repo adopt it over the files it already has.
- New opt-in gate `statement_columns_exist_in_ddl` (`assert_statement_columns_exist_in_ddl`, `find_unknown_statement_columns`, `statement_column_report`;
  needs the `sql` extra): every column a module-level SQL constant names (`INSERT INTO t (cols)`, `ON CONFLICT (cols)` and `DO UPDATE SET`,
  `UPDATE t SET c`, `EXCLUDED.c`, `alias.c` bound through FROM/JOIN to a table) must exist in the tables the project's DDL files define
  (`CREATE TABLE`, `ADD`/`DROP`/`RENAME COLUMN`, `DROP`/`RENAME TABLE`), read offline. It is the offline half of the 2026-10-04 CORR-26 incident, a column
  named in code before its additive migration was applied. A statement the gate cannot read (f-string field, `{name}` field, unparsable) or that
  touches no table the DDL defines is NOT CHECKED and counted in the report, never clean; baseline-able and shrink-only because a project is
  legitimately ahead of its DDL for a window. `sql_verifier_coverage` gained `statement_constants` (the constants with their text); its own behaviour
  is unchanged.

## 1.21.1

New gate `connection_liveness_kwargs` (`assert_every_connection_has_liveness_kwargs`): every call that opens a network database
connection (`psycopg2.connect`, the psycopg2 pools, `psycopg`, `psycopg_pool`) must set the four TCP keepalive keywords, literally, through a
shared `**KEEPALIVES` mapping the scan can read, or in a DSN literal; a tunnel that drops silently otherwise leaves the client waiting for the
two-hour OS timer. The mapping is also exported as `connection_liveness_kwargs.KEEPALIVES`.
New gate `tracked_secret_shapes`: tracked (index) files holding a string of a secret shape the consumer lists in
`[tool.py_ci_shared.secret_shapes]`, with a pre-commit entry point (`python -m py_ci_shared.tracked_secret_shapes`); the matched
text is never printed and no baseline is accepted.

Git hooks for this repository: a `.pre-commit-config.yaml` (ruff, filtered black, actionlint, secret scan, mypy and the self-gate
tests on push) so a push can no longer fail the self-gates that CI enforces. `tests/test_release_version.py` scratch
repositories no longer inherit the hook's `GIT_*` variables.

New gate `destructive_tests_throwaway_only` (`assert_destructive_tests_are_throwaway_only`): a test file that writes SQL
(INSERT/UPDATE/DELETE/TRUNCATE/DROP/ALTER/CREATE in a string literal) and reaches a database (a connect call, a `real_database` or
`integration` mark, or a DSN read) must take its DSN from a throwaway accessor, never `live_dsn` or a `DATABASE_*` variable, and every
pre-commit hook or workflow step naming it must start `python -m py_ci_shared.embedded_postgres run`. Test roots may be several packages
with the configs at the repository root; a reasoned per-file allowlist covers rollback-only tests. `embedded_postgres` gains
`require_loopback_dsn` and `throwaway_dsn_from_env`, which refuse a non-loopback, hostless or unparseable DSN with
`NotAThrowawayServerError` without echoing it.
New gate `unresolved_module_attributes` (`assert_no_unresolved_module_attributes`): an attribute read on an imported first-party module
(`import m; m.NAME`, `import a.b as x`, `from pkg import submodule`) must name something the module defines. The sibling of
`unresolved_imports`, which sees only `from X import Y`. Targets are parsed, never imported; `resolve_roots` lists the directories
where bare module names are found. Modules with a `__getattr__`, a `__class__` swap, `globals()[...] =` or `exec` are skipped and reported.
New gate `cross_package_private_names`: a package must not import or reach an underscore-private NAME inside another package's
module when the modules are imported by bare name (`from show_top_jobs import _get_dsn`, `import top_jobs_query as tq; tq._X`).
It follows aliases and `from pkg import module as x`, skips `if TYPE_CHECKING:` blocks, names the public alias when one exists,
lists a module name found in several packages as skipped (never as a pass), and takes an `allowed` map with mandatory reasons
that may only shrink. `private_imports` only sees underscore MODULES.
New gate `unexecuted_function_bodies`: reads the coverage.py JSON report of the UNIT run (`pytest --cov=pkg --cov-report=json`) and
reports every function or method whose body statements have no executed line (a function every test stubs, as `outcome_column_exists`
was), as a shrink-only ratchet; a missing, empty, stale or mismatching report is an error. CLI: `python -m py_ci_shared.unexecuted_function_bodies`.

## 1.21.0

Eight checks from the 2026-10-03 Upwork dashboard audit, each proven on the real defect it is named for:

- New gates `stub_signature_parity` (a monkeypatched or `mock.patch`ed stub must accept every parameter of the callable it
  replaces; static scan, plus the opt-in run-time plugin `stub_signature_guard`) and `wrapper_protocol_parity` (a wrapper that
  copies some of a protocol's attributes, e.g. `cache_clear`, from its inner callable must set all the inner one has, e.g.
  `refresh`; plus the run-time `protocol_attributes.assert_satisfies_protocol`).
- New gate `secret_assertion_operands`, and the opt-in pytest plugins `secret_safe_test_output` (credentials redacted from
  every report, `os.environ` restored) and `offline_suite_without_credentials` (credential variables and `.env` loading
  blanked for every test without the `real_database` marker). Plugins are switched on in `[tool.py_ci_shared]`
  (`PLUGIN_KEYS`: `resource_leak_guard`, `stub_signature_guard`, `secret_safe_test_output`, `offline_suite_without_credentials`)
  or by `-p py_ci_shared.<name>`. Three of this repo's own tests that printed the whole environment on failure fold to a bool.
- New gate `constant_relations`: a TOML registry of declared relations between numeric constants and config defaults across
  packages, evaluated by a walked `ast` grammar (no eval); an unresolvable reference fails.
- `sql_verify` is now a gate: `FragmentMatrix` / `assert_fragment_matrix` build every fragment of a dict into every template and
  fail on a statement that does not parse, keeps an unfilled `{slot}`, or names a table alias out of scope where it lands.
  New extras `sql` (sqlglot) and `pglast`.
- New gates `connect_error_echo` (an `except` around a database connect or DSN resolve must not format the exception into
  output: libpq echoes the connection string) and `drifted_duplicate_literals` (one set of numbers restated in several
  modules of a package; baseline entries need a reason).

## 1.20.0

- `import_side_effects`: an import-time `os.environ` write is reported by key and value length, never by value. A
  module that loads a `.env` at import made a failing gate print every DSN and API key in full.
- **Behaviour change** (audit 2026-10-03 remainders, N-19, WF-11): a baseline that exists but cannot be read is
  reported as "baseline X is unreadable (...); fix or delete it" by every refresh, never as "does not exist" (the core
  `Baseline` refresh, `mutation_teeth`, `function_length`, `ignore_ratchet`, `import_side_effects`, `audit_wave_filenames`,
  `fail_open_handlers`, `loc_budget`, `phantom_code_references`, `vacuous_loop_assertions`); the file is left as it is.
  Floors where a gate passed on nothing: `phantom_code_references.assert_no_phantom_code_references`,
  `doc_identifier_parity.assert_doc_identifiers_exist` and `timezone_honest.assert_timezone_honest` take `min_files=1`;
  `test_partition_reachability` fails a runner path that holds no runner text and names an unparsable tags file.
  `effect_assertion_parity` reports an unparsable `test_*.py` that `build_import_map` had dropped. `gate_integrity` names
  an unparsable pre-commit or workflow file (it raised a bare yaml error), and `pytest_addopts_path_runs` no longer
  reports the clean files' runs when another test file is unparsable.
- `local_copy_report` also judges every `conftest.py` by content (`CONFTEST_SIGNATURES`: a hand-rolled autouse stream
  guard is a copy of `resource_leak_checks`); `include_conftest=False` turns it off (audit 2026-10-03 G-7).
- `stale_comment_age`: a code-shaped comment in an AST analyser that names a call shape the file matches by a string
  literal (`# self.stats.setdefault("k", 0)` above `attr == "setdefault"`) is a label, not commented-out code; and
  `python -m py_ci_shared.stale_comment_age --stale-warning-summary REPO ...` prints per-repo counts of comments going
  stale plus a `stale-comment early warnings: N` total for a job that checks consumers out (audit 2026-10-03 G-12).
- **Behaviour change** (audit 2026-10-03, G-3): `optional_truthiness` also follows optional numbers carried on `self`
  (`self.x`, `getattr(self, "x")`, a local bound from either) and one hop of argument forwarding, for budget-like
  names (`BOUND_NAMES`). On by default (`follow_attributes=True`); mlframe master gets 7 new findings (three zero-budget
  bugs plus the sites 1256fff4d fixes). Fix them with `is not None`, or pass `follow_attributes=False` while you do.
- New gates (audit 2026-10-03 G-1, G-2, G-4, G-5): `hook_attestation` (a `commit-msg` trailer and a pushed-range check
  for commits that skipped the hooks), `sibling_floor_skew` (a sibling floor in `pyproject.toml` above the revision CI
  installs), `api_floor` (vermin against `requires-python`, guard-aware; `pip install py-ci-shared[api]`) and
  `committed_line_endings` (bare CR, mixed and CRLF line ends in the committed blobs).
- **Behaviour change** (audit 2026-10-03, new code): gates added since v1.17.0 got stricter where they failed open.
  `function_complexity` fails on an unparsable file and refuses a refresh while one exists (`allow_unparsed=True` to
  tolerate it; its entries are then kept), as `complexity_ratchet` did; both name an unreadable baseline. `adoption_matrix`
  with `--resolve-in` fails a pin to a tag or SHA that does not exist (`unresolvable-pin`). `corpus_drift compare` fails
  when a repo of last night's snapshot is missing tonight (`repo-gone`) and when a finder errors from its first night.
  `standard_stream_restore` now judges only RESTORES of a saved stream (through `import sys as x` and `setattr` too),
  guarded only by an identity check that encloses or precedes the restore: CLI-wide `sys.stdout = TextIOWrapper(...)`
  is no longer reported, and `allow` accepts `path::function`. `nondiscriminating_shapes` raises on an unknown
  `extra_shapes` slug and no longer lets `exists`/`supported` on a computed name or a trivial companion assert hide a
  late skip or a median round-trip. `config_getattr_default_parity` raises `TypeError` for a non-dataclass in
  `dataclass_classes`. `ci_install_covers_conftest` reads setuptools dynamic dependencies, requires `pytest_plugins`
  and `-p` plugins, reports a tox/nox/`make test` job as unevaluated, and no longer lets the interpreter running it
  choose the rule (an import with no table mapping is `ci-install-unmapped` everywhere). Re-run the gates after
  upgrading and fix, allow or acknowledge what they name.
- **Behaviour change** (audit 2026-10-03, core): `[tool.py_ci_shared]` is validated more strictly. A string `enable`,
  a gate enabled twice (dashes and underscores count alike), a non-numeric or non-positive `budget_s`, a non-string
  `entry`/`module` and a glob that matches no file are configuration errors (exit 2) where they used to run wrongly or
  crash. Fix the table the message names.
- **Behaviour change**: inside a git work tree, a failed or timed-out `git ls-files` now fails the gate (`CorpusError`)
  instead of silently walking the directory, ignored files and submodules included. `PY_CI_SHARED_GIT_TIMEOUT_S`
  raises the 120 s git timeout. Every git call goes through one runner (`_core.git`).
- **Behaviour change**: the pytest plugin stops with a usage error when the nearest `pyproject.toml` has a
  `[tool.py_ci_shared]` table but pytest's rootdir is another directory (the gates used to vanish from the run), and
  when `--py-ci-gates=on` finds no table. Pass `--rootdir`.
- **Behaviour change**: a baseline count that is not a whole number >= 1 is a `BaselineError` naming the file.
- `resource_leak_guard` now catches env leaks in the first test of a session without psutil and, on Python 3.9, in
  tests that import a stdlib module; the header says when psutil is missing. `version_tag_currency` no longer takes
  tag `10.2.3` for version `0.2.3`, and counts only releases of the pin's own spelling as "behind". The CLI escapes
  characters the console cannot print instead of crashing, and `run naive-utcnow` finds `naive_utcnow`.
- **Behaviour change** (audit 2026-10-03, gaps G-6..G-14): `resource_leak_guard` also checks `streams`, `cwd`,
  `sys_path` and `warnings` (module `resource_leak_checks`); a test that leaves `sys.stdout` swapped, the working
  directory changed, `sys.path` edited or a warning filter added now errors at teardown. Restore it in the test, or
  allow it (`stream:<name>`, `cwd`, `sys_path:<glob>`, `warnings`), or narrow `leak_guard_checks`. New gates
  `vendored_internal_imports`, `consumer_import_census`, `external_fact_tables`, `commit_metadata` (forbidden trailers
  configurable, none by default), `closed_audit_rounds`, `workflow_runner_labels` (`--fix`), and the
  `required_check_contexts` CLI. `assert_no_stale_todos` warns by default 7 days before a comment goes stale
  (`warn_days=0` restores the old silence; the advisory never fails a run).

- New gate `ci_install_covers_conftest`: a workflow job that runs pytest must install every third-party package its
  `conftest.py` files import at collection (module level and session hooks), read statically from the job's pip/uv
  installs, `-r` files, `pyproject.toml` extras, `uv.lock`, local composite actions and shell scripts, with
  `sys.version_info` guards and `python_version` markers evaluated per matrix Python. It flags pyutilz's
  `numba-coverage.yml` before e6db9af (no py-ci-shared for `tests/conftest.py`), the class behind three collection
  failures in four days.
- New CLI `ci_health` and daily workflow `ci-health.yml`: for every repo in `configs/consumers.toml`, how many days each
  GitHub Actions workflow on its CI branch (and its scheduled runs) has been continuously red, counted from the first
  failure after the last success; cancelled and skipped runs do not count. A workflow red for more than
  `--max-red-days` (default 2) fails the job. A run GitHub refused to start because the account's payment failed or
  its spending limit was hit is reported as `billing`, apart from code failures, and never fails it. Private repos
  need `CONSUMER_READ_TOKEN`, else they are skipped with a warning, as in `consumer-pins.yml`.
- `consumer-pins.yml` runs `adoption_matrix --allow-behind 2`: a consumer's fixed pin may lag the latest release by up
  to 2 tags before the daily job fails (with the default 0, every release turned every consumer red until it bumped).
- `stale_comment_age`: opt-in early warning. `assert_no_stale_todos(..., warn_days=7)` emits one `UserWarning` per TODO
  or commented-out call that crosses `max_age_days` within the next 7 days, naming `file:line` and the date it goes
  stale, and returns those lines; it never fails on them. `find_comments_going_stale` returns the same list.
  `warn_days=0` (the default) changes nothing for existing callers. Both functions and `find_stale_comments` take
  `now=` to freeze the clock.

- **Behaviour change** (reusable workflows): `ruff-blocking.yml`, `lint-advisory.yml` and `black-filtered.yml` no
  longer fall back to master when `py-ci-shared-ref` cannot be fetched; they retry three times and fail. Pass a ref
  that exists. New input `force-remote-fetch` (self-ci only).
- **Behaviour change** (`lint-advisory.yml`): pip-audit audits the calling project (`.`), or the files in the new
  `pip-audit-requirements` input; before, it audited its own tool environment and never the project.
- **Behaviour change** (`ci_health`): a billing-refused run no longer relabels an older code-failure streak as
  `billing`; such workflows are now red. `--only` with an unknown name exits 2. Per-request timeouts, `--deadline`
  (600 s), `--jobs` (8) and per-consumer progress on stderr.
- `release.yml`: refuses a tag that is not the newest release of its major, requires self-ci green for the tagged
  commit, runs the whole suite, is re-runnable, and has a `rollback-to` dispatch (CLAUDE.md "Rolling back a release").

## 1.19.0

- `unresolved_imports` resolves `from X import *` exactly instead of treating the star-importing module as unknowable: the exported set is X's literal `__all__` (else its public names), followed through chains. A name a facade built on `from .core import *` does not carry (mlframe: `from mlframe.metrics import show_plots_unless_agg`, which raised ImportError at run time) is now reported. A star from outside the parsed roots, or from a module with `__getattr__`/`globals()` tricks, stays unjudged.
- `nondiscriminating_shapes`: two false positives removed. `late-skip` no longer flags the write-the-baseline-on-first-run skip (`if not BASELINE.exists(): write(); pytest.skip(...)`, used by dozens of meta-gates): an `.exists()` check is a filesystem-state probe, not the data deciding. `median-roundtrip` now fires only when the median is the test's SOLE verdict; a median canary next to `assert_allclose` or another assert is a precondition, not the pass/fail criterion.
- `nondiscriminating_shapes`: the environment-probe vocabulary of `late-skip` also recognises `supported` (`not callbacks_supported()`), `vram` and `major`/`minor` (a compute-capability or version tuple). Whole identifier parts only, so `majority_share` and `n_supporters` stay data and are still flagged.
- New gate `standard_stream_restore`: code that swaps `sys.stdout`/`sys.stderr` restores them only while its own stream is
  still installed, so a nested swap cannot put back a stream somebody else has already replaced.
- `corpus_drift.finders()` imports every gate module; a gate whose third-party dependency is missing now raises
  `MissingDependencyError` naming each module and package (`snapshot` exits 2 and writes nothing) instead of dying with a bare
  `ModuleNotFoundError`. The nightly workflow installs pytest, `config-drift-check.yml` installs the package (it printed
  `No module named 'py_ci_shared'` on every run while showing green, because the pipe through `tee` hid the exit code and
  the step had no pipefail), and a workflow test requires every job that runs `python -m py_ci_shared.*` to install it first.
- `unresolved_imports.ModuleIndex` indexes one file per helper call (the complexity ratchet had flagged `__init__`).

## 1.18.0

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
