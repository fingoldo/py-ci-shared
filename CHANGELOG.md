# Changelog

Milestones only; the commit log has the detail. Versions are the release tags (`vX.Y.Z`).

## Unreleased (next: 1.23.3)

- `stale_comment_age.assert_no_stale_todos` takes `allow_unparsed` (default `True`: an unreadable file is still scanned with a lossy decode,
  as before; `False` reports it as a problem and checks nothing in it) and `min_files` (default `0`: a scan that read fewer files than the floor
  reports it). `test_partition_reachability.assert_partitions_reachable` takes `allow_unparsed` (default `False`: an unreadable runner or spec
  file fails with its name, as it always did; `True` skips it) and `min_files` (default `0`). Defaults keep every existing call's behaviour.
- `repo_hygiene.assert_repo_hygiene` takes `min_files` (default `0`): a pass that read fewer text files than the floor reports it. Pass it to opt in.
- `gate_contract.NEVER_PARSES` and `NO_CORPUS` name the corpus entries that cannot take `allow_unparsed` or a floor, each with its reason.

## 1.23.2

- New library `contract_ledger` (`src/py_ci_shared/contract_ledger.py`): the shared mechanism behind the 2026-10-21 contract ratchets in
  autopsia. A committed JSON ledger lists every value a contract surface can emit (a response literal, an HTTP status and its
  Retry-After behaviour, a file that stores user text); `ledger_gaps` and `ledger_entry_problems` report the values with no entry or a
  malformed one; `assert_fixture_fresh` fails a stale committed fixture; `store_writers` runs a probe and lists every file it created or grew.
  Additive: no existing gate changes.

## 1.23.1

- Behaviour change: `.github/actions/install-pyutilz` without a `pyutilz-ref` input installs the pyutilz commit the calling repository's own `pyproject.toml` pins, not py-ci-shared's pin. A repository with no pin, or with two different pins, now fails with a message; set the input to the commit you mean.
- Behaviour change: the eight lint tools `lint-blocking.yml` runs (actionlint-py, zizmor, codespell, yamllint, bandit, vulture, interrogate, deptry) now default to the exact versions in `tool_versions.py` when a consumer leaves its `*-version` input empty, instead of the newest PyPI release at run time. A consumer that wants a different version sets the input. Each version is at least two weeks old at the time it is chosen.
- Behaviour change: `phantom_code_references` no longer reports nine classes of false positive (Python protocol names, standard vocabulary
  codes, errno constants, external data file names, one-character symbols, names the repo spells out as strings, bare `self.x` attributes,
  `table.column` pairs spelled in SQL). On the autopsia tree 901 findings become 494. A consumer baseline recorded against the old scanner
  now has entries that no longer fire: rebuild it from the new scanner (`PYTHONPATH=. python tests/test_meta/<test>.py`), do not hand-edit it.

## 1.23.0

- Security audit of 2026-10-20 (`audits/2026-10-20/`, 39 findings, all dispositioned in `audits/TRACKER.md`). The consumer-visible changes:
  - **Behaviour change:** a `[tool.py_ci_shared]` corpus entry that resolves outside the repository root (an absolute path, or `..`) is now a `ConfigError`.
    Move the file inside the repository or drop the entry; a gate used to read it silently.
  - **Behaviour change:** `iter_files` skips symlinks. A tracked link to a file outside the checkout used to be read as a corpus file; a gate that counted
    such links will see fewer files.
  - **Behaviour change:** `load_consumers` rejects a `name` or `repo` that holds a separator, a `.`/`..` component, `?` or `#`.
  - **Behaviour change:** `mypy_gate` fails when mypy runs longer than 30 minutes, `black_filtered_apply` raises after 10 minutes, and the warn-only runners
    (`advisory_warn`, `bandit_warn`, `format_warn`, `vulture_warn`) report `skipped` after 10 minutes (15 for pip-audit), instead of hanging the hook.
  - **Behaviour change:** ruff, bandit, vulture, pip-audit and black run as `python -I -m <tool>`, so `PYTHONPATH`, the user site and the working directory are
    not on their `sys.path`. mypy is left as it was: a repository-local mypy plugin must be importable from the working directory.
  - `env_example_round_trip` and `corpus_drift` no longer print a committed value or the text of an exception; they name the field and the exception type.
  - `release.yml` keeps no credential in `.git/config` (the token reaches only the tag push), and the rollback job runs only from `master`.
  - The gate cache moved from the shared temp directory to `~/.cache/py_ci_shared/gate_cache`; `api_floor` keeps vermin's output per file digest in
    `~/.cache/py_ci_shared/vermin` (a warm run on this repository: 0.8 s, was 17 s).
  - `env_write_restore` accepts `# env-write-ok: <reason>` on a deliberate write.
  - Four regexes that backtracked on crafted input (the Dart empty-catch body was exponential) are linear.
- The test suite runs on the machine's spare cores by default: `tests/conftest.py` passes `worker_budget.available_workers()` to pytest-xdist when it is installed
  (`pytest-xdist` is now in the `dev` extra). `PY_CI_WORKERS=1` gives one worker. The CPU share (rounded half up) is then capped so each worker has 4 GiB of
  free memory, never below one: a worker that starts without it pages, and the next worker's DLL loads fail with an error that names no memory.
  The memory per worker is set per repository: 4 GiB by default, 2 GiB for this suite (`available_workers(ram_per_worker_bytes=...)`).
- The tests that start a Postgres server (`xdist_group("postgres")`) run on one worker under xdist, so the session has one server, not one per worker. The
  mode is added to the arguments the workers start from (see the `pytest_cmdline_main` note in `tests/conftest.py`). Tests that need an isolated schema take
  a `fresh_database()` on the session server (`py_ci_shared.embedded_postgres`). The lifecycle test (`test_a_real_server_starts...`) keeps its own start.
- Fixed: `tests/conftest.py` called `bound_randomly_reseeders()` without the session config, so the skip for a session without pytest-randomly never ran
  there and every pytest start imported thinc, torch and cupy (about 7 s). Every nested pytest run of `test_scaffold` paid that three times: 137 s to 51 s.
- New: `tests/test_pytest_startup_is_light.py` starts a real pytest without pytest-randomly and fails if thinc, torch or cupy get imported (the bug the next entry fixes).
- `test_run_hands_the_command_its_dsn` checks the handoff with a stand-in DSN instead of starting a server (26 s to under 1 s).
- Fixed: a Postgres server whose test process was killed (a crashed xdist worker, a timeout) outlived it. `embedded_postgres` now stops and deletes the
  data directories of dead owners at the next start (`owner.pid`); live owners are left alone. `psutil` is in the dev extra.
- Fixed: the mutation worker turned a KeyboardInterrupt in a request into an error reply and went on serving; an interrupt now ends the worker.
- `mutation_teeth` is under the 1000-line budget: the per-mutant verdict (`_judge`, `_primary_verdict`, `_check_unmutated`, `_group_twins`, `_wider_net`, `_module_names`,
  `PathArg`) moved to `_mutation_sweep.py`; `mutation_teeth` re-exports what tests and callers use. Harness version 17.
- `exc.line or 1` in 25 modules now maps only a missing line to 1 (`exc.line if exc.line is not None else 1`); a line 0 is no longer rewritten.
- Refactor: the mutation sweep's per-mutant verdict is split into helpers (`_judge`, `_primary_verdict`, `_check_unmutated`, `_group_twins`, `_sweep_in_sandbox`),
  clearing two C901 findings. Decisions and budget accounting are unchanged; the harness version is 16 because its tracked files changed.
- `embedded_postgres` logs a warning when psutil is missing, so the reaper's no-op is visible.
- `stale_comment_age`: `warn_days=0` is a zero-day window, no longer rewritten to 7 (a sentinel `or` that the self-audit reports as P1).
- Handlers that substitute a fallback value log it at WARNING (27 self-audit findings): `_mutation_worker`, `cli`, `closed_audit_rounds`, `commit_metadata`, `consumer_import_census`, `dart_scanners`, `epsilon_padded_denominators`, `hash_fed_by_array_copy`, `mutation_teeth`, `required_check_contexts`, `workflow_runner_labels`, `_ci_install_parts`, `adoption_matrix`, `corpus_drift`, `git_dependency_pins`, `offline_suite_without_credentials`, `phantom_code_references`, `resource_leak_checks`, `schema_snapshot_parity`, `sibling_floor_skew`, `baseline_trend`, `required_check_contexts`. Unreadable-file fallbacks that are deliberately best-effort carry a `# best-effort:` reason.
- Registry: `_mutation_sweep` is declared internal to mutation_teeth.
- Gate results are cached on disk: a PASSED result is not re-run while nothing in the repository changed (the key is the gate, its arguments, the package version and a digest of the tree; FAILED, refresh and network gates always run; `PY_CI_SHARED_NO_CACHE=1` turns it off). The repeat of the package's own gates: 33 s to 12 s.
- `stale_source_citations` on the mlframe tree: 192 s to 57 s with the same findings. The token list of a file is computed once (three passes read it), files
  without a `.py:` text are not tokenized (no citation pattern can match them), statement spans, docstring lines and split source lines are computed once per tree.
- Self gate: `comment_repo_paths` runs on this repository (roots `tests/`, `.github/`; code and config files only). Its roots now accept the path form that config tables produce. About 50 illustrative or stale path references in docstrings and comments were reworded.
- Fixed: `test_cwd_sys_path_and_warnings` depended on `warnings.simplefilter("ignore")` changing the filters, which it does not when the session already ignores
  warnings (`pytest -W ignore`); the probe now installs a filter no caller has.

## 1.22.13

- New opt-in gate `import_time_global_side_effects`: a module-level statement (outside `if __name__ == "__main__":` and any function) that changes
  process-global state for everything imported after it: `logging.disable`, `basicConfig(force=True)`, root-logger handler/level surgery, a blanket
  `warnings` ignore, assignment to `sys.stdout`/`stderr`/`stdin`, `os.chdir`, `locale.setlocale`, `signal.signal`. `imported_by="tests"` limits the
  check to modules a test imports (the precise form for a scripts directory). Found when a test importing a CLI script dropped every INFO/WARNING
  record in all xdist workers (78 failures in the Glossum backend).
- Fixed: `guard_population` on Windows under Python 3.9 sliced `Path.parents` while finding Git's bash, which 3.9 cannot, so every guard
  raised `TypeError`. Found by running 3.9 on Windows locally (self-ci runs 3.9 on Linux only).

## 1.22.12

`lock_held_across_io` honours a reviewed marker: a call that must stay under a cross-process file lock carries
`# lock-io-ok: <reason>` on its own line, with a non-empty reason. A marker without a reason does not suppress the finding.

Four new gates for concurrency and test-quality defects, and one new rule in `fail_open_handlers`: `lock_held_across_io`,
`check_then_act_outside_lock`, `no_source_text_tests` and `shared_mutable_placeholder`.

Behaviour change: `fail_open_handlers` has a fifth rule, `silent_default_return`, an `except` handler whose whole body is one
`return` and that logs nothing. A gate already on `@v1` that scans a file with such a handler now reports it. What to do: log
the failure at WARNING (or raise), or mark the `except` line `# best-effort: <reason>` when the fallback cannot change a result,
then refresh the baseline if the handler is accepted.

Behaviour change: `code_audit_meta.assert_no_new_code_audit_findings` fails when a pyutilz code-audit scanner raises, instead
of counting the crashed scanner as "no findings" (it calls `pyutilz.dev.code_audit.registry.run_all_checked`). What to do: fix
or exclude the scanner the failure names. With a pyutilz older than `run_all_checked` the gate warns and keeps the old
behaviour; upgrade pyutilz (the `[dev]` extra and `install-pyutilz` now pin `ec8f2bd`) to get the check.

- New gate `comment_repo_paths`: a comment (`#` in shell/YAML/TOML/INI/Dockerfile/`#!` scripts, `//` in Dart/JS/TS,
  Python comments and docstrings) that names a path under `tool/`, `tools/`, `scripts/`, `.githooks/` or
  `.github/scripts/` must name a file or directory that exists under the repo root. Glob and template tokens and
  past-tense lines ("renamed", "removed", "no longer"...) are skipped; an unparsable `.py` is reported. Opt-in.
- New gate `nginx_location_headers`: a `server`/`location`/`if` block with its own `add_header` must repeat every header
  an enclosing block sets, since nginx then drops the inherited set; an `include` where headers matter is reported
  unless `resolve_includes=True` reads it; `required=` names headers every `server`/`location` must end up with.
  Unbalanced braces are an unparsed file, not a pass; `.conf.template` files with `${VAR}` parse. Opt-in.
  `exempt={selector: reason}` exempts a `location` (selector exactly as written) from the dropped-header and `required`
  checks; it must then carry `required_in_exempt`. An empty reason or a selector matching no location is a finding.
- New gate `validation_errors_echo_input`: a pydantic validator (`field_validator`/`validator`/`model_validator`/
  `root_validator`), or a helper it passes the value to, that raises `ValueError`/`TypeError`/`AssertionError`/
  `PydanticCustomError` (or asserts) with a message reaching the validated value (`len(value)` and the field name are
  fine), and a `RequestValidationError` handler that returns `exc.errors()` without dropping `input` for every error. Both
  put the rejected value into the 422 detail that clients log (autopsia, 2026-10-15). Keys are `path::function::shape`;
  shrink-only baseline. Opt-in.
- `phantom_code_references.count_claim_mismatches` also counts a bullet list naming one thing per item
  (`#   NAME - text`, `# - NAME: text`, `// * NAME - text`) under a claim line ending in `:`, and accepts any
  "<number> <word(s)>:" claim ("Two substitutions:") for such a list. Numbered lists are counted as before.

**Behaviour change**: `phantom_code_references` (`find_phantom_code_references`, `count_claim_mismatches`) now reads `#`
lines of `.sh`/`.bash`/`.zsh`/`.yaml`/`.yml`/`.toml`/`.cfg`/`.ini`/`.conf`/`.template` files, `Dockerfile*` and
extensionless `#!` scripts as comments (before, only `//`/`*` lines of non-Python files were read), and a bullet list
under a count claim can now disagree with it. A consumer that passes such files can see new findings. What to do: fix
the comment (a backticked name that does not exist, or a count that does not match its list), or keep those files out
of the `files` you pass.

`dart_scanners.scan_tappable_semantics` can tell two real tap-target exemptions from a missing `MinTapTargetBox`. Both are
checked, so neither lets an ordinary button opt out.

- `target_wrappers=[...]` (keyword argument): project widgets or `Widget name(...)` helpers that guarantee a compact WCAG 2.5.8
  AA target. A tappable inside one counts as wrapped only when the wrapper's own definition, found in the scanned files, sets
  both `minWidth` and `minHeight` to at least 24 (or to a `minTapTarget` constant). A named wrapper that is missing, or that
  does not constrain both axes, is a finding of its own.
- `// tap-target: inline-text - <reason>` on the comment lines directly above a tappable: WCAG 2.5.8's inline-target exemption.
  It holds only when the tappable's `child:` holds text alone (`Text`, `RichText`, `TextSpan`, semantics and text styling). A
  marker with no reason, or on a tappable whose child holds any other widget (`Icon`, `Padding`, `Container`...), is reported
  as an invalid exemption. The marker does not touch the role rule.

Nothing changes for a consumer that passes no `target_wrappers` and writes no marker. The source-text helpers moved to the
private `_dart_source` module and are re-imported by `dart_scanners`.

## 1.22.10

Regression fix in the opt-in `wallclock_test_waits` gate. 1.22.9 scanned a file under `test`/`tests` only when its name was
`test_*.py`, `*_test.py` or `*_test.dart`, so helpers there (`tests/conftest.py`, `tests/support/*.py`, `test/support/*.dart`,
`test/helpers.dart`) were skipped and a real `time.sleep(` or `Future.delayed(const Duration(milliseconds` in them was missed.
Now every `.py` and `.dart` file inside a `test`/`tests` directory is scanned. Outside one nothing is scanned, so exploratory
scripts stay out. The comment-line and docstring exclusions are unchanged.

Behaviour change (opt-in gate only): a wall-clock wait in a test helper is now a finding. What to do: if the gate is enabled and
it reports a helper's wait, remove the wait or record it in `tool/meta/baselines/wallclock_test_waits.json` with a note.

## 1.22.9

Two false-positive fixes in opt-in ratchet gates. Both gates are opt-in (`[tool.py_ci_shared] enable = [...]`), so a consumer
that has not enabled them sees no change.

- `guard_without_fixture`: a `tool/check*` guard named by a CI workflow (`.github/**/*.yml`, `*.yaml`) or a git hook (`.githooks/*`)
  is covered, not reported. Before, only `test/` and `tests/` counted, so a guard run only by CI was flagged. A guard named nowhere
  is still reported.
- `wallclock_test_waits`: only a `test_*.py`, `*_test.py` or `*_test.dart` file inside a `test`/`tests` directory is scanned, so a
  `test_*.py` exploratory script outside one is no longer a finding. A line whose stripped text starts with `#` is a comment, not a
  wait, and a line inside a Python module, class or function docstring is not a wait either. Dart files get no docstring check.

Behaviour change (opt-in gates only): both gates report fewer findings. An accepted baseline entry that no longer fires is reported as
stale and does not fail; prune it with the gate's refresh command. What to do: nothing, unless you enabled one of these gates and want
the stale entries removed.

## Unreleased

- **Behaviour change** `git_child_env` (Dart): the call body was read from the closing quote of `'git'`, not from the call's `(`, so it
  ended at the first nested call's `)` or, with none, held no arguments at all; a scrubbed `environment:` map literal was reported as "passes no
  environment:". The body is now the whole argument list. A scrubbed `environment:` now also needs `includeParentEnvironment: false`: Dart
  merges `environment:` into the parent's environment otherwise, so the hook's `GIT_DIR` reaches the child anyway. Over noema_app,
  polyvocab_app and flutter_app_core this adds no finding. What to do: pass `includeParentEnvironment: false` next to the scrubbed map.

- **Behaviour change** `dart_scanners.scan_tappable_semantics` finds a spinner's labelling `Semantics(label:)`, a GestureDetector's
  announcing `Semantics(button: true / checked: / ...)` and a tappable's `MinTapTargetBox` by walking out through the widget's ENCLOSING calls
  (bracket nesting over string-masked source) instead of a fixed 400/600-character window. A label far above on the widget's own ancestor now
  counts (noema_app leaf_controls, polyvocab_app landing_navbar); a `Semantics` that is a closed SIBLING above the widget no longer does.
  `scan_hardcoded_ui_strings` skips a `label:` literal whose innermost enclosing call is in `non_ui_label_calls` (default `("retryTransient",)`,
  a retry log label that is never rendered). Over noema_app, polyvocab_app and flutter_app_core this removes three false positives and adds
  none. What to do: regenerate the `tappable-semantics` and `hardcoded-ui-strings` baselines so the entries that no longer fire drop out.

- `uncalled_functions` gains named exemption categories, declared once for every consumer: `CATEGORIES` (PUBLIC_API, FRAMEWORK_HOOK,
  PLUGIN_HOOK, SUPERSEDED, RETAINED_KERNEL, OPT_IN_SLOW, OFFLINE_HELPER, TEST_UTILITY, each with its meaning), an `Exemption(category, reason)`
  per name passed as `exempt=`, and `reexported_public_api(files, root)`, which lists what a package `__init__` re-exports (`from x import f` and
  `__all__`). `assert_no_new_uncalled_function(..., public_api_from_exports=True)` exempts those names as PUBLIC_API without a hand list.
  An exemption with an unknown category or an empty reason fails the gate. `ignore` keeps working for bare names. Additive: no consumer's
  green gate turns red.

- New gate `emitted_vocabulary_literals` (`assert_emitted_vocabulary_literals`, `EmitSite`): every value a backend passes to a closed-vocabulary
  field at its emit sites (a callee name plus the keyword and/or positional slots) is a member of the vocabulary it publishes, and every published
  member is emitted somewhere (a class default of that field counts). Arguments resolve statically: literals, both arms of a conditional, module- and
  class-level string constants (also imported, absolute or relative), locals and tuples unpacked from a same-module function's returns. An argument
  it cannot resolve is a finding unless its source text is listed in `pass_through`. Generalized from Autopsia's `test_emitted_vocabulary_literals`
  (a `rebased` kind reached a client's screen untranslated); over Autopsia's tree with that test's two configurations it reports nothing, and one
  planted literal at a `ModeratorDisclosureOut(kind=...)` site is reported along with the published member it displaced.

- `version_tag_currency.find_lock_pin_problems` / `assert_lock_agrees_with_pin`: a Flutter app's `pubspec.yaml` git pin of a package, its
  `pubspec.lock` and the package's remote name one commit (the lock's `ref` is the pinned ref; its `resolved-ref` is what that ref names on the
  remote today, via `git ls-remote`, with an annotated tag peeled). The build honours the lock, so a ref moved without `flutter pub get`, or a tag
  re-pointed after it, ships the old code under a green check. An unreachable remote raises `VersionCheckError` rather than passing;
  `check_remote=False` checks the first half offline. Generalized from noema_app's `tool/check-core-pin-agrees.sh`.

- Five new **opt-in** ratchet gates, none in the default set: a repo enables each by name in `[tool.py_ci_shared] enable`, and
  only NEW violations fail; existing ones are recorded in the repo's `tool/meta/baselines/<gate>.json` with a note each.
  `skip_as_pass` (a tool/scripts/.githooks script that prints SKIPPED and exits 0), `wallclock_test_waits` (`Future.delayed` of a
  `Duration`, `time.sleep` inside a test), `guard_without_fixture` (a `tool/check*` guard no test names), `git_child_env` (a test's git
  spawn that does not scrub `GIT_*` from its environment) and `dup_baseline_percent_key` (a duplicate-body pair listed twice because only
  its similarity percentage moved). Enabling them in a consumer that already has violations needs its baseline first.

- `corpus-drift.yml` installs the `sql` extra, so `statement_columns_exist_in_ddl` runs in the nightly corpus scan instead of raising ImportError for sqlglot.

- `lint-advisory.yml` gains `pip-audit-ignore-vulns`: advisory ids pip-audit skips (`--ignore-vuln` each), for risks a repo has accepted and written
  down. The list is the baseline, so a vulnerability that is not in it still appears in the report instead of being buried among the known ones.

- `.github/scripts/release_guard.py prepare X.Y.Z [--write | --check]` brings every place that names a release (pyproject, `__version__`, the README install tag and gate catalogue, the reusable workflows' default ref, the `since` of modules not yet published) to one version before the tag is pushed; two tags (v1.22.0, v1.22.2) failed the Release run's `verify` job for want of it.

- New gate `free_text_in_get_route` (`assert_free_text_in_get_route`): a FastAPI/Starlette `@x.get("/path")` route must not take free text in its path or its query string.
  A parameter counts as free text when it is a `str` named like one (`term`, `query`, `q`, `text`, `search`, `condition`, `complaint`, `chief_complaint`, `symptoms`,
  `description`, `note`, `comment`, `message`, `prompt`, `question`; configurable) or carries `Query`/`Path(max_length>=100)` unless its name says it is an identifier.
  A URL is written to every access log, proxy and browser history, so text a clinician or customer typed belongs in a POST body. Run over seven codebases it reported
  nothing in five and seven routes in one backend (three clinical lookups, three vocabulary typeaheads and a reviewer name); a route that must stay GET goes in the baseline.

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
