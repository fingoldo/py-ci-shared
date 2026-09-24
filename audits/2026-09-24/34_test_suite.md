# Audit: the repo's own test suite and self-CI

Scope: `tests/` (104 files, 1515 `def test_` functions), `.github/workflows/self-ci.yml`, `pyproject.toml` pytest/coverage config, `WRITING_TESTS.md`. Read-only: no source edits, no commits. Mutation checks ran against scratchpad copies loaded through a `-p mutplug` pytest plugin that swaps `py_ci_shared.<module>` in `sys.modules` before collection (a `PYTHONPATH` override alone cannot work, because every test file runs `sys.path.insert(0, <repo>/src)`).

## 1. Module to test mapping

113 modules under `src/py_ci_shared` (excluding `__init__`). 96 have a dedicated `tests/test_<module>.py`.

No dedicated test file, but covered from a grouped file:

| Module | Covered from | Notes |
|---|---|---|
| dataclass_case_completeness, llm_call_archive_gate, save_failure_markers | test_llm_preservation_gates.py (19 tests total, shared by 3 modules) | about 6 tests per module |
| fail_message_quality, pydantic_field_bounds, sql_verifier_coverage | test_single_copy_rules.py (16 tests for 3 modules) | pydantic part skips when pydantic is missing |
| import_side_effects (332 LOC, 51 branches), meta_private_imports | test_v113_rules.py (23 tests for 3+ modules) | import_side_effects has about 8 tests for 51 branches |
| bandit_warn | test_gate_config_honesty.py (referenced only) | no behavioural test of bandit_warn's own logic |
| config_drift_check | test_toml_compat.py (3 tests) | the network fetch and the drift compare have no test |
| format_warn | test_format_warn_ignore_subset.py (1 test) | tests the ruff config subset, not format_warn's logic |
| _mutation_worker | test_mutation_harness_version_is_bumped.py (1 test) | version-bump guard only; the worker loop has no test |
| tool_versions, _toml_compat | constant/shim; covered indirectly | fine |

No test at all (zero references anywhere in tests/): **setup_env** (143 LOC, a `[project.scripts]` entry point), **advisory_warn** (69 LOC), **vulture_warn** (68 LOC).

Thin relative to size (tests vs branch nodes): import_side_effects (about 8 / 51), runtime_registry_mutation (6 / 38), unread_init_params (6 / 34), tracker_summary_parity (6 / 39), identity_comparisons (4 / 27), private_imports (4 / 20), sqlalchemy_text_binds (4 / 10), discarded_model_copy (5 / 29), safe_precommit (3 own tests, and those skip in CI, see F-06), teeth_sweep (4 / 21), prompt_field_parity (12 / 67 with 14 public functions), audit_round_format (15 tests for 15 public functions and 57 branches), config_call_site_parity (25 / 134), inert_patch_targets (18 / 100), source_text_claims (32 / 115).

## 2. Dogfooding

Self-applied today: ruff (self-ci step + two reusable-workflow integration jobs), mypy on `src/py_ci_shared`, black-filtered (integration job), mypy-beachhead, lint-advisory (cannot fail), mypy-full (advisory), `git_dependency_pins` on its own pyproject (test_git_dependency_pins.py:127), `pinned_tool_versions` on its own pins/workflows (test_pinned_tool_versions.py:77-95), a hand-written Python-floor check (test_python_floor_compatibility.py), and a weak `ci_workflow_gate` check on lint-advisory.yml.

Not self-applied though directly applicable to this repo: `ci_workflow_timeout_gate` (own workflows), `entry_points_resolvable` (3 console scripts), `version_consistency`, `private_imports`, `import_layering`, `uncalled_functions`, `function_length`, `loc_budget` (mutation_teeth.py is 2099 LOC, over the >1k-LOC house rule), `fail_open_handlers`, `identity_comparisons`, `naive_utcnow`, `optional_truthiness`, `unresolved_imports`, `value_bearing_asserts` / `vacuous_loop_assertions` / `nondiscriminating_shapes` / `effect_assertion_parity` over tests/, `pytest_markers`, `phantom_markdown_links` and `phantom_code_references` / `doc_identifier_parity` over README.md and WRITING_TESTS.md, `repo_hygiene`, `hook_hygiene` over .pre-commit-hooks.yaml, `package_doctests`, `audit_round_format` / `audit_wave_filenames` / `audit_path_references` over its own `audits/` (the house rule says audit_round_format is wired in every Python project), `code_audit_meta`, `gate_population_canary` over its own tests, `mutation_teeth` / `teeth_sweep`. lint-blocking.yml and docs.yml are not exercised (documented in self-ci.yml).

## 3. Test teeth

Sampled 15 gates for a positive case, a negative control and edge cases (from test names and bodies):

| Gate | Positive | Negative control | Edge / empty-scan |
|---|---|---|---|
| identity_comparisons | yes | yes | empty scan yes; no `%` concat, no bytes case |
| naive_utcnow | yes | yes (comment, docstring, replacement) | skip dirs yes; no empty-scan guard exists in the gate |
| function_length | yes | yes | boundary exactly at limit yes; async not tested |
| private_imports | yes | yes (sibling) | plain `import x._y` not tested |
| sqlalchemy_text_binds | yes | yes | min_files yes; indented comment, pycache, suffix not tested |
| optional_truthiness | yes | yes (4 negatives) | baseline, empty-scan yes |
| value_bearing_asserts | yes | yes | baseline, floor yes |
| phantom_markdown_links | yes | yes | untracked venv yes |
| ci_workflow_timeout_gate | yes | yes | reusable jobs, step-level yes |
| pinned_tool_versions | yes | yes | marker suffix yes |
| version_consistency | yes | yes | one-source refusal yes |
| alembic_concurrently | yes | yes | empty dir yes, baseline stale yes |
| checkpoint_isolation | yes | yes | repo root itself yes |
| unread_init_params | yes | yes | stale allowlist and empty scan yes |
| runtime_registry_mutation | yes | yes | shadowing yes |

All 15 have both polarities. The gaps are in secondary branches, which the mutation runs confirm.

Manual mutation: 5 gates, 35 mutants, 12 survived; 3 of those are equivalent, so **9 real survivors**. Per-mutant logs: `scratchpad/mut/mutants/<module>/<id>/pytest.log`, harness `scratchpad/mut/run.py`.

| Id | Gate | Mutation | Result |
|---|---|---|---|
| I1-I3, I5-I7 | identity_comparisons | drop IsNot / JoinedStr / Attribute side / min_files / AnnAssign / right-hand side | killed |
| I4 | identity_comparisons | `(ast.Add, ast.Mod)` -> `(ast.Add,)` | **survived** |
| F1, F3, F5-F8 | function_length | `>`->`>=` limit, no nested walk, no min guard, no shrink lock, off-by-one, no gone-entry | killed |
| F2 | function_length | drop `ast.AsyncFunctionDef` | **survived** |
| F4 | function_length | `write_length_baseline` `v > limit` -> `v >= limit` | **survived** |
| S2, S4, S6 | sqlalchemy_text_binds | lookbehind removed, comment skip removed, min_files dropped | killed |
| S1 | sqlalchemy_text_binds | lookbehind `[:\w]` -> `[:]` (`x:a::int` now flagged) | **survived** |
| S3 | sqlalchemy_text_binds | `line.lstrip().startswith("#")` -> `line.startswith("#")` | **survived** |
| S5 | sqlalchemy_text_binds | `__pycache__` not skipped | **survived** |
| S7 | sqlalchemy_text_binds | suffix filter off (scans every file) | **survived** |
| N1, N2, N4, N5 | naive_utcnow | only Name receiver, skip on filename only, need >1 offender, lineno lost | killed |
| N3 | naive_utcnow | drop `"build"` from default skip dirs | **survived** |
| P1, P3, P5, P6 | private_imports | dunder only, subpackage flagged, prefix exemption off, stale allowlist ignored | killed |
| P7 | private_imports | `elif isinstance(node, ast.Import)` -> `elif False` (plain `import pkg._x` ignored) | **survived** (real false negative) |
| P2, P4, P8 | private_imports | relative level, bare package, first segment | survived, equivalent (filtered later by the package-prefix check or by `owning_package` returning None) |

Unmutated baselines: all 5 files green (4/8/8/14/4 passed).

## 4 and 5. Portability, hygiene, coverage, meta-tests

See the findings table. In short: there is no coverage config at all; there is no test that every module has a test file, a README entry, or that every CLI resolves; self-ci runs only Python 3.11 although the floor is 3.9.

## Findings

### SUITE-1 (High) -- CI tests only Python 3.11 while requires-python = ">=3.9"

**Disposition:** OPEN

- **Original id:** F-01
- **Where:** .github/workflows/self-ci.yml:42
- **Finding:** CI tests only Python 3.11 while `requires-python = ">=3.9"`. The floor test's own docstring says the two 3.9/3.10 breakages were found by mlframe's shards, not by this repo.
- **Proposed fix:** Add 3.9 and 3.13/3.14 to the matrix (at least on ubuntu).
- **Verified:** yes (read)

### SUITE-2 (High) -- The floor-guard test itself does import tomllib at module level, so it fails at collect...

**Disposition:** OPEN

- **Original id:** F-02
- **Where:** tests/test_python_floor_compatibility.py:17
- **Finding:** The floor-guard test itself does `import tomllib` at module level, so it fails at collection on 3.9/3.10, the very interpreters it guards. It stays invisible only because of F-01.
- **Proposed fix:** `from py_ci_shared._toml_compat import tomllib`.
- **Verified:** yes (read)

### SUITE-3 (Med) -- The tomllib ban matches only the exact line import tomllib; import tomllib as t and fro...

**Disposition:** OPEN

- **Original id:** F-03
- **Where:** tests/test_python_floor_compatibility.py:59
- **Finding:** The tomllib ban matches only the exact line `import tomllib`; `import tomllib as t` and `from tomllib import loads` pass.
- **Proposed fix:** Walk the AST for Import/ImportFrom of `tomllib`.
- **Verified:** yes (read)

### SUITE-4 (High) -- There is no coverage measurement, no fail_under and no pytest config (no testpaths, -ra...

**Disposition:** OPEN

- **Original id:** F-04
- **Where:** pyproject.toml (no `[tool.pytest.ini_options]`, no `[tool.coverage.*]`); self-ci.yml:59
- **Finding:** There is no coverage measurement, no `fail_under` and no pytest config (no `testpaths`, `-ra`, `strict-markers`, timeout). The self-CI cannot notice untested code (for example setup_env).
- **Proposed fix:** Add a pytest-cov run with `[tool.coverage.report] fail_under` derived from the current measurement, `addopts = "-ra --strict-markers"`, and `testpaths = ["tests"]`.
- **Verified:** yes (grep)

### SUITE-5 (High) -- uv pip install -e ".[dev]" \/\/ uv pip install -e 

**Disposition:** OPEN

- **Original id:** F-05
- **Where:** .github/workflows/self-ci.yml:49
- **Finding:** `uv pip install -e ".[dev]" \|\| uv pip install -e .` silently falls back to no dev extras (for example when the pyutilz git fetch fails). Tests then importorskip-skip (pydantic, pre_commit, pyutilz) or fail for unrelated reasons, and the job can go green with the skips unreported (`-v` without `-rs`).
- **Proposed fix:** Drop the fallback; fail the install step. Add `-rs` and a skip-count ceiling.
- **Verified:** yes (read)

### SUITE-6 (Med) -- pytest.importorskip("pre_commit"), but pre-commit is not in the dev extra, so the whole...

**Disposition:** OPEN

- **Original id:** F-06
- **Where:** tests/test_safe_precommit.py:17
- **Finding:** `pytest.importorskip("pre_commit")`, but `pre-commit` is not in the `dev` extra, so the whole file (and safe_precommit, a console script) is skipped in CI. The same applies to numpy (test_hash_fed_by_array_copy.py:115) and sqlalchemy (test_statement_compilation.py:111).
- **Proposed fix:** Add pre-commit, numpy and sqlalchemy to `dev`, or a CI-only extra.
- **Verified:** yes (read deps + importorskip)

### SUITE-7 (High) -- setup_env is a [project.scripts] entry point (py-ci-setup-env) with 143 LOC and zero te...

**Disposition:** RESOLVED -- new tests/test_setup_env.py covers every platform branch (home directory, subprocess and platform replaced, nothing real touched), re-runs, awkward paths, failure exits and the `py-ci-setup-env` entry point; regression test: tests/test_setup_env.py::test_the_console_script_entry_point_resolves_to_main

- **Original id:** F-07
- **Where:** src/py_ci_shared/setup_env.py
- **Finding:** setup_env is a `[project.scripts]` entry point (`py-ci-setup-env`) with 143 LOC and zero test references.
- **Proposed fix:** Add tests/test_setup_env.py (behaviour and an entry-point smoke test).
- **Verified:** yes (grep: 0 refs)

### SUITE-8 (Med) -- No test references either module.

**Disposition:** OPEN

- **Original id:** F-08
- **Where:** src/py_ci_shared/advisory_warn.py, vulture_warn.py
- **Finding:** No test references either module.
- **Proposed fix:** Add tests covering the exit-code and advisory behaviour.
- **Verified:** yes (grep)

### SUITE-9 (Med) -- Covered only nominally (referenced, or 1 to 3 unrelated tests); their own branches (7,...

**Disposition:** OPEN

- **Original id:** F-09
- **Where:** src/py_ci_shared/bandit_warn.py, format_warn.py, config_drift_check.py, _mutation_worker.py
- **Finding:** Covered only nominally (referenced, or 1 to 3 unrelated tests); their own branches (7, 6, 11, 19) have no behavioural tests.
- **Proposed fix:** Give each a dedicated test file.
- **Verified:** yes (grep + counts)

### SUITE-10 (Med) -- 9 gates share 3 grouped files at about 5 to 8 tests each

**Disposition:** OPEN

- **Original id:** F-10
- **Where:** tests/test_v113_rules.py, test_llm_preservation_gates.py, test_single_copy_rules.py
- **Finding:** 9 gates share 3 grouped files at about 5 to 8 tests each. import_side_effects (332 LOC, 51 branch nodes) is the thinnest. A module-to-test-file meta-test would also not see these.
- **Proposed fix:** Split into per-module files, or record the mapping in a meta-test (F-12).
- **Verified:** yes

### SUITE-11 (Med) -- Thin tests relative to branches: runtime_registry_mutation 6/38, unread_init_params 6/3...

**Disposition:** OPEN

- **Original id:** F-11
- **Where:** see section 1
- **Finding:** Thin tests relative to branches: runtime_registry_mutation 6/38, unread_init_params 6/34, tracker_summary_parity 6/39, identity_comparisons 4/27, private_imports 4/20, discarded_model_copy 5/29, teeth_sweep 4/21.
- **Proposed fix:** Add edge-case tests, prioritised by mutation survivors.
- **Verified:** yes (AST counts)

### SUITE-12 (Med) -- No meta-test asserts that every module has a test file, every gate has a README entry,...

**Disposition:** OPEN

- **Original id:** F-12
- **Where:** tests/ (absent)
- **Finding:** No meta-test asserts that every module has a test file, every gate has a README entry, or every CLI entry point resolves. 66 of 113 modules are not named in README.md at all (for example identity_comparisons, naive_utcnow, function_length, private_imports, mypy_gate, source_text_claims).
- **Proposed fix:** Add tests/test_package_inventory.py: module->test map with an explicit grouped-file allowlist, module->README (reuse `docs_inventory_parity`), and `entry_points_resolvable` over pyproject.
- **Verified:** yes (grep)

### SUITE-13 (Med) -- Many of the repo's own gates are not run on itself (full list in section 2): ci_workflo...

**Disposition:** OPEN

- **Original id:** F-13
- **Where:** .github/workflows/self-ci.yml
- **Finding:** Many of the repo's own gates are not run on itself (full list in section 2): ci_workflow_timeout_gate, entry_points_resolvable, version_consistency, private_imports, uncalled_functions, function_length/loc_budget, fail_open_handlers, identity_comparisons, naive_utcnow, unresolved_imports, test-quality gates over tests/, phantom_markdown_links/doc_identifier_parity over README/WRITING_TESTS, audit_round_format and siblings over audits/, gate_population_canary, hook_hygiene over .pre-commit-hooks.yaml.
- **Proposed fix:** Add tests/test_self_gates.py that invokes each `assert_*` on REPO_ROOT, with baselines where there is debt.
- **Verified:** yes (grep of tests for REPO_ROOT usage)

### SUITE-14 (Low) -- The dogfood test pytest.skips if lint-advisory.yml is missing (it always exists in this...

**Disposition:** OPEN

- **Original id:** F-14
- **Where:** tests/test_ci_workflow_gate.py:145-156
- **Finding:** The dogfood test `pytest.skip`s if lint-advisory.yml is missing (it always exists in this repo) and asserts only `len(steps) >= 1`.
- **Proposed fix:** Remove the skip; assert the exact reviewed step set.
- **Verified:** yes (read)

### SUITE-15 (Med) -- Mutant P7 survives: removing the ast.Import branch (so import pkg.sub._priv is never re...

**Disposition:** RESOLVED -- mutant P7 (dropping the `ast.Import` branch) is now killed by a fixture whose only reach is a plain `import pkg.metrics._core`, with a public-import control. regression test: tests/test_private_imports.py::TestAuditRegressions::test_a_plain_import_alone_is_flagged

- **Original id:** F-15
- **Where:** tests/test_private_imports.py
- **Finding:** Mutant P7 survives: removing the `ast.Import` branch (so `import pkg.sub._priv` is never reported) leaves all 4 tests green. This is a real false-negative class.
- **Proposed fix:** Add a test with a plain `import pkg.a._b` from a foreign package.
- **Verified:** yes (mutation)

### SUITE-16 (Med) -- Mutant F2 survives: async functions dropped from measurement

**Disposition:** RESOLVED -- added an `async def` over the limit (kills mutant F2) and a baseline write with functions at, over and under the limit asserting only the one strictly over is written (kills mutant F4); regression test: test_function_length.py::TestAuditRegressions::test_an_async_function_over_the_limit_is_measured_and_reported, test_the_baseline_write_excludes_a_function_exactly_at_the_limit

- **Original id:** F-16
- **Where:** tests/test_function_length.py
- **Finding:** Mutant F2 survives: async functions dropped from measurement. Mutant F4 survives: `write_length_baseline` boundary `>`->`>=` (functions exactly at the limit get baselined).
- **Proposed fix:** Add an `async def` over the limit, and a baseline-write test with one function at exactly `limit`.
- **Verified:** yes (mutation)

### SUITE-17 (Med) -- Survivors: S3 (an indented # :a::text comment line is not tested), S5 (__pycache__ skip...

**Disposition:** RESOLVED -- one case per surviving mutant: S1 `x:a::int` is not a bind (with `(:a::int)` as positive control), S3 an indented `# :a::text` line is skipped, S5 a `__pycache__` file is not scanned, S7 `.sql`/`.txt` are not scanned by default but `.sql` is when asked; regression test: tests/test_sqlalchemy_text_binds.py::test_a_word_before_the_colon_is_not_a_bind, tests/test_sqlalchemy_text_binds.py::test_an_indented_comment_line_and_a_sql_comment_are_skipped, tests/test_sqlalchemy_text_binds.py::test_pycache_and_other_suffixes_are_not_scanned_by_default

- **Original id:** F-17
- **Where:** tests/test_sqlalchemy_text_binds.py
- **Finding:** Survivors: S3 (an indented `# :a::text` comment line is not tested), S5 (`__pycache__` skip), S7 (suffix filter; a `.sql` or `.txt` file would be scanned), S1 (lookbehind `\w`: `x:a::int` must not be a bind).
- **Proposed fix:** Add one case per behaviour.
- **Verified:** yes (mutation)

### SUITE-18 (Low) -- Survivor I4: a "%s" % x constant is not tested as stringish.

**Disposition:** RESOLVED -- added a `"SELECT %s" % "x"` module constant compared with `is` (reported) next to an `int % int` constant (not reported), which kills the mutant that drops `ast.Mod`; regression test: test_identity_comparisons.py::test_a_percent_formatted_constant_is_stringish

- **Original id:** F-18
- **Where:** tests/test_identity_comparisons.py
- **Finding:** Survivor I4: a `"%s" % x` constant is not tested as stringish.
- **Proposed fix:** Add a `%`-formatted module constant compared with `is`.
- **Verified:** yes (mutation)

### SUITE-19 (Low) -- Survivor N3: the default skip-dir list (build) is not pinned; only user-given skip dirs...

**Disposition:** RESOLVED -- naive_utcnow's default skip list is now `_core.DEFAULT_EXCLUDE` (migrated by the core agent). A test pins the list against the canonical set and names the directories that matter, and a test parametrized over every entry proves each one is skipped while a sibling source directory is still scanned, so dropping any entry (not only `build`) fails a named test. regression test: tests/test_naive_utcnow.py::TestDefaultSkipDirsArePinned::test_the_default_list_is_the_shared_canonical_set, tests/test_naive_utcnow.py::TestDefaultSkipDirsArePinned::test_each_default_skip_dir_is_skipped_and_a_sibling_is_not

- **Original id:** F-19
- **Where:** tests/test_naive_utcnow.py
- **Finding:** Survivor N3: the default skip-dir list (`build`) is not pinned; only user-given skip dirs are tested.
- **Proposed fix:** Parametrise over `_DEFAULT_SKIP_DIRS`.
- **Verified:** yes (mutation)

### SUITE-20 (Med) -- assert_no_naive_utcnow has no empty-scan guard (no min_files), unlike every sibling gate

**Disposition:** RESOLVED -- `assert_no_naive_utcnow(..., min_files=1)` is new; an empty root now fails. `test_a_clean_tree_passes` still passes because its tree contains one parsed file. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_an_empty_root_fails_the_floor

- **Original id:** F-20
- **Where:** src/py_ci_shared/naive_utcnow.py:73
- **Finding:** `assert_no_naive_utcnow` has no empty-scan guard (no `min_files`), unlike every sibling gate. A wrong root passes green. The test `test_a_clean_tree_passes` enshrines this.
- **Proposed fix:** Add `min_files` and a test that an empty root fails.
- **Verified:** yes (read)

### SUITE-21 (Low) -- getattr(node, chr(108)+chr(105)+...) spells "lineno" through chr() concatenation, appar...

**Disposition:** RESOLVED -- the `chr(108)+...` spelling is replaced by a plain `node.lineno`. No scanner in this repo flags `lineno`, so there was no scanner to fix. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_line_numbers_are_read_plainly

- **Original id:** F-21
- **Where:** src/py_ci_shared/naive_utcnow.py:69
- **Finding:** `getattr(node, chr(108)+chr(105)+...)` spells `"lineno"` through chr() concatenation, apparently to dodge a text scanner. This is unreadable and hides from grep.
- **Proposed fix:** Use `node.lineno`, and fix the scanner that forced this.
- **Verified:** yes (read)

### SUITE-22 (Low) -- Real git commit in tmp repos inherits the developer's global git config (commit.gpgsign...

**Disposition:** OPEN

- **Original id:** F-22
- **Where:** tests/test_version_tag_currency.py:31, tests/test_stale_comment_age.py:33 (and others using `git commit`)
- **Finding:** Real `git commit` in tmp repos inherits the developer's global git config (`commit.gpgsign`, hooks, `init.templateDir`). Only test_git_changed_lines isolates it (`_git_env`). The tests can fail or prompt on a dev machine.
- **Proposed fix:** Use a shared fixture that sets `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_NOSYSTEM` and `-c commit.gpgsign=false`.
- **Verified:** yes (grep)

### SUITE-23 (Low) -- Only 10 calls pass timeout=; git and python subprocesses have no deadline, and there is...

**Disposition:** OPEN

- **Original id:** F-23
- **Where:** tests/ (37 `subprocess.run` calls)
- **Finding:** Only 10 calls pass `timeout=`; git and python subprocesses have no deadline, and there is no pytest-timeout. A hung child hangs until the 15-minute job timeout.
- **Proposed fix:** Add `timeout=` or a pytest-timeout default in config.
- **Verified:** yes (grep)

### SUITE-24 (Low) -- The fake reader sleeps 30 s in a background thread that is left running after the test...

**Disposition:** RESOLVED -- `_WarmRunner` now reads replies through one pump thread per process into a queue, and the test's fake reader blocks on an `Event` that the test sets in `finally`, then asserts the reader thread has exited; regression test: test_mutation_teeth.py::TestAnUnmeasuredMutantIsNotReportedAsMeasured::test_the_warm_reader_gives_up_rather_than_blocking_forever

- **Original id:** F-24
- **Where:** tests/test_mutation_teeth.py:590
- **Finding:** The fake reader sleeps 30 s in a background thread that is left running after the test (leaked thread; it delays interpreter exit when it is last).
- **Proposed fix:** Use an Event the test sets in teardown.
- **Verified:** yes (read)

### SUITE-25 (Low) -- The real-server tests skip unless Postgres binaries are on PATH or PG_BIN; the GitHub r...

**Disposition:** OPEN

- **Original id:** F-25
- **Where:** tests/test_embedded_postgres.py:74,84
- **Finding:** The real-server tests skip unless Postgres binaries are on PATH or PG_BIN; the GitHub runners have them under /usr/lib/postgresql but not on PATH, so they likely always skip in CI.
- **Proposed fix:** Set PG_BIN in self-ci on ubuntu.
- **Verified:** partial (not run in CI)

### SUITE-26 (Low) -- Every file does sys.path.insert(0, <repo>/src)

**Disposition:** OPEN

- **Original id:** F-26
- **Where:** tests/*.py (about 100 files)
- **Finding:** Every file does `sys.path.insert(0, <repo>/src)`. This duplicates the editable install, shadows any installed build (so CI never tests the built wheel), and blocks PYTHONPATH-based mutation tooling.
- **Proposed fix:** Use a conftest or `pythonpath = ["src"]` in pytest config, or rely on the install.
- **Verified:** yes (grep)

### SUITE-27 (Low) -- No shared fixtures (git repo factory, write helper), so each file re-implements _write/...

**Disposition:** OPEN

- **Original id:** F-27
- **Where:** tests/ (absent conftest.py)
- **Finding:** No shared fixtures (git repo factory, write helper), so each file re-implements `_write`/git-init helpers. This caused the inconsistent git isolation in F-22.
- **Proposed fix:** Add a conftest with shared fixtures.
- **Verified:** yes

### SUITE-28 (Info) -- Portability is otherwise good: all tests use tmp_path (one tempfile.TemporaryDirectory...

**Disposition:** OPEN

- **Original id:** F-28
- **Where:** self-ci.yml (Windows/macOS legs)
- **Finding:** Portability is otherwise good: all tests use tmp_path (one `tempfile.TemporaryDirectory` in test_mutation_teeth.py:70), explicit `encoding="utf-8"`, no `shell=True`, and a 3-OS matrix.
- **Proposed fix:** none
- **Verified:** yes
