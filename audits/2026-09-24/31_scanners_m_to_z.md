# Audit: scanner correctness, modules m..z and the mutation harness

Merged from four sub-audits (m-p, r-s, t-z, mutation_teeth group). No source edits were made.
Repro scripts live in the session scratchpad (`repro.py`, `rs/`, `tz/`, `r1..r6.py`).

## Cross-cutting

- **BOM files silently skipped** (MP-1, RS-1, TZ-4, MT-3): reading with `utf-8` keeps U+FEFF, `ast.parse` raises, the `except SyntaxError` drops the file. Fix once in a shared reader (`utf-8-sig`).
- **Parse failures fail open** (MP-2, RS-2, TZ-4): unparsable files are skipped silently; `min_*` floors count files passed in, not files parsed.
- **Missing baseline seeds and skips** (TZ-17, RS-4): deleting the baseline makes CI permanently green.
- **Refresh flag read from `sys.argv`** (RS-6): ignored under xdist or `pytest.main`.
- **Tests lock in gaps**: `test_naive_utcnow.py:78` (MP-2), `test_marker_runner_coverage.py:135-141` (MP-10), `test_pytest_markers.py:92-94` (MP-38), `test_sql_verify.py:101` (RS-45), readme baseline test line 228 (RS-5).


`_toml_compat.py`: no findings.

Test gaps: `test_mutation_teeth.py` covers none of MT-1, BOM, zero mutants, generator/empty `lines`, `sweep_files(jobs>1)`, twin fan-out, `wider_net_note` replay, fd-1 worker writes. `_mutation_worker` has no test file; `test_teeth_sweep.py` covers only `_apply`.

#### Proposed split of mutation_teeth.py (2099 LOC → 5 modules, re-exported)

1. `_mutation_model.py` (~230): HARNESS_VERSION, _UNJUSTIFIED, REFRESH_FLAG, _COPY_IGNORE, _CONTAINER_SAMPLE, MutationHarnessError, Mutant, MutationRun, (de)serialisers.
2. `_mutation_operators.py` (~720): operator tables, range/span helpers, candidate generators, `generate_mutants`.
3. `_mutation_fingerprint.py` (~180): `_first_party_imports`, `fingerprint`, plugin cache.
4. `_mutation_runner.py` (~360): `_pytest_env`, crash classification, `_run_pytest`, `_WarmRunner`, `_classify*`.
5. `mutation_teeth.py` (~600): sweep, `find_surviving_mutants` (with extracted `_scope_key`, `_load_cached_run`, `_store_cached_run`), refresh option, `sweep_files`, public asserts, `__all__`.

Caveats: look up `_run_pytest`/`_WarmRunner`/`_classify*` through the `mutation_teeth` namespace so existing monkeypatches keep working; add the new files to `tests/test_mutation_harness_version_is_bumped.py:27`; the move re-keys the code_audit baseline.

## Findings

### MP-1 (High) -- BOM files are dropped: ast.parse rejects U+FEFF and the SyntaxError is swallowed

**Disposition:** RESOLVED -- `_core.read_source` decodes `.py` files like the interpreter (`tokenize.detect_encoding`: BOM, PEP 263 cookie, UTF-8) and strips the BOM; naive_utcnow and private_imports now parse through `_core.scan_python`, so a BOM file is checked, not dropped. Other MP-1 sites (marker_runner_coverage, meta_private_imports, module_reload_safety, optional_truthiness, phantom_code_references, pytest_markers, prompt_field_parity) are left to the migration agents. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_a_bom_file_is_checked_not_dropped, tests/test_private_imports.py::TestAuditRegressions::test_unparsable_and_bom_files, tests/test_core_source.py::TestReadSource::test_a_bom_is_stripped

- **Where:** marker_runner_coverage.py:80-86, meta_private_imports.py:57, module_reload_safety.py:109-117, naive_utcnow.py:65, optional_truthiness.py:86, private_imports.py:72, phantom_code_references.py:95, pytest_markers.py:109, prompt_field_parity.py:93
- **Finding:** BOM files are dropped: `ast.parse` rejects U+FEFF and the SyntaxError is swallowed
- **Failing input:** BOM file with `datetime.utcnow()` → `[]`
- **Proposed fix:** read `utf-8-sig`; unparsable files are findings
- **Verified:** yes-repro

### MP-2 (High) -- any SyntaxError/decode error is a silent skip; newer syntax than the interpreter passes

**Disposition:** RESOLVED -- `_core.scan_python` records each unreadable or unparsable file as a `SourceProblem` (path, line, kind, message) and never skips it. `find_naive_utcnow` lists it as `path:line: unparsable: ...`, `find_private_cross_package_imports` as `(path, "<unparsed>")`, and both `assert_*` entry points fail on it. The old test that required a silent skip (tests/test_naive_utcnow.py:78) now requires the file to be reported. regression test: tests/test_naive_utcnow.py::TestScoping::test_an_unparseable_file_is_reported_and_does_not_stop_the_walk, tests/test_naive_utcnow.py::TestScoping::test_an_unparseable_file_fails_the_entry_point

- **Where:** same sites
- **Finding:** any SyntaxError/decode error is a silent skip; newer syntax than the interpreter passes
- **Failing input:** `def (:` + real offender
- **Proposed fix:** report or fail on unparsable files
- **Verified:** yes-read

### MP-3 (Med) -- only .utcnow() calls matched; default_factory=datetime.utcnow and utcfromtimestamp missed

**Disposition:** RESOLVED -- naive_utcnow matches every `.utcnow`/`.utcfromtimestamp` ATTRIBUTE, called or not (`Field(default_factory=datetime.utcnow)`), and reports a call once. `ImportAliases` resolves `arrow`/`pendulum` (whose `utcnow()` returns an aware value) so those are not flagged. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_uncalled_references_and_utcfromtimestamp_are_caught, tests/test_naive_utcnow.py::TestAuditRegressions::test_aware_libraries_are_resolved_through_aliases_and_not_flagged

- **Where:** naive_utcnow.py:44-51
- **Finding:** only `.utcnow()` calls matched; `default_factory=datetime.utcnow` and `utcfromtimestamp` missed
- **Failing input:** `Field(default_factory=datetime.utcnow)` → `[]`
- **Proposed fix:** match the Attribute, called or not
- **Verified:** yes-repro

### MP-4 (Med) -- no file-count floor; missing root passes

**Disposition:** RESOLVED -- for naive_utcnow and private_imports (module_reload_safety is left to migration): a missing root raises `_core.CorpusError`; `assert_no_naive_utcnow` and `assert_no_private_cross_package_imports` take `min_files=1`, which counts PARSED files. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_an_empty_root_fails_the_floor, tests/test_naive_utcnow.py::TestAuditRegressions::test_a_missing_root_raises, tests/test_private_imports.py::TestAuditRegressions::test_the_floor_and_a_missing_root

- **Where:** naive_utcnow.py:61, module_reload_safety.py:129, private_imports.py:68
- **Finding:** no file-count floor; missing root passes
- **Failing input:** `assert_no_unpaired_reloads(Path("nonexistent"))` passes
- **Proposed fix:** `min_files`
- **Verified:** yes-repro

### MP-5 (Med) -- class-level markers, AnnAssign/class pytestmark, from pytest import mark missed

**Disposition:** RESOLVED -- marker_runner_coverage resolves markers through `_core.ImportAliases` (`from pytest import mark`, `import pytest as pt`, called marks, module-level `x = pytest.mark.y` names), reads module and class `pytestmark` as Assign or AnnAssign, and applies `Test*` class decorators to their methods (keys `file::Class::test`). The file is also parsed via `_core.scan_python` (BOM read, unparsable file reported as `<file>::<unparsed>`, `marked_tests` raises). regression test: tests/test_marker_runner_coverage.py::TestAuditRegressions::test_class_markers_class_pytestmark_annassign_and_from_pytest_import_mark, tests/test_marker_runner_coverage.py::TestAuditRegressions::test_a_bom_file_is_read_and_an_unparsable_one_is_reported

- **Where:** marker_runner_coverage.py:95-100
- **Finding:** class-level markers, AnnAssign/class `pytestmark`, `from pytest import mark` missed
- **Failing input:** repro: only test_a found
- **Proposed fix:** walk ClassDef decorators, AnnAssign, aliases
- **Verified:** yes-repro

### MP-6 (High) -- positional tests (no /, no .py) not seen as path → "reaches everything"

**Disposition:** RESOLVED -- every non-option positional after `pytest` is a path (`pytest tests`), numeric option values are not, value-taking long options (`--cov`, `--durations`...) consume their value, and the invocation is cut at the first shell separator (`&&`, `;`, `|`, redirects). regression test: tests/test_marker_runner_coverage.py::TestAuditRegressions::test_a_bare_positional_directory_is_a_path_not_everything, tests/test_marker_runner_coverage.py::TestAuditRegressions::test_a_directory_outside_the_marked_file_does_not_reach_it

- **Where:** marker_runner_coverage.py:117
- **Finding:** positional `tests` (no `/`, no `.py`) not seen as path → "reaches everything"
- **Failing input:** `pytest tests -m 'not integration'` → `((), ...)`
- **Proposed fix:** any non-option positional is a path
- **Verified:** yes-repro

### MP-7 (Med) -- ./tests not normalised; cd pkg && pytest tests/ never matches

**Disposition:** RESOLVED -- paths are `posixpath.normpath`-ed (`./tests/` -> `tests`) and a `cd dir &&` before the command is kept as `Runner.cwd`; paths are joined to it when `repo_root/dir` exists (otherwise the command runs from an enclosing directory whose `dir` is the root, the monorepo-hook shape the existing tests pin). regression test: tests/test_marker_runner_coverage.py::TestAuditRegressions::test_dot_slash_and_cd_are_normalised

- **Where:** marker_runner_coverage.py:118,167
- **Finding:** `./tests` not normalised; `cd pkg && pytest tests/` never matches
- **Failing input:** `Runner(paths=("./tests",))` → False
- **Proposed fix:** normpath; per-runner cwd
- **Verified:** yes-repro

### MP-8 (Med) -- first -m used, docstring says last wins; -m=/-mexpr not parsed

**Disposition:** RESOLVED -- `-m`/`-k` are read from the token stream, the LAST one wins as the docstring says, and `-m expr`, `-m=expr` and `-mexpr` all parse. regression test: tests/test_marker_runner_coverage.py::TestAuditRegressions::test_the_last_dash_m_wins_and_every_spelling_parses

- **Where:** marker_runner_coverage.py:119
- **Finding:** first `-m` used, docstring says last wins; `-m=`/`-mexpr` not parsed
- **Failing input:** `-m integration -m 'not integration'`
- **Proposed fix:** `findall()[-1]`
- **Verified:** yes-repro

### MP-9 (Med) -- node-id runner reaches whole file; -k ignored

**Disposition:** RESOLVED -- a `file::node` argument reaches only that node (and a class node its methods); a whole-file entry carries its member tests and is reached only when every member is. `-k` is evaluated with pytest's substring semantics over file, class, test and marker names. regression test: tests/test_marker_runner_coverage.py::TestAuditRegressions::test_a_node_id_runner_reaches_only_that_test, tests/test_marker_runner_coverage.py::TestAuditRegressions::test_dash_k_is_applied

- **Where:** marker_runner_coverage.py:167
- **Finding:** node-id runner reaches whole file; `-k` ignored
- **Failing input:** `tests/test_a.py::test_x` → True
- **Proposed fix:** compare node id to `test.key`
- **Verified:** yes-repro

### MP-10 (Med) -- ratchet key is the file: new tests in a known file excused; node-id known reported stale

**Disposition:** RESOLVED -- the ratchet key is the reported `<file>::<test>` (split on `": "`, not the first colon); a legacy bare-file `known` entry no longer excuses the whole file and is reported stale with a migration hint. The old shrink-only test used file keys and was re-framed to node keys. regression test: tests/test_marker_runner_coverage.py::TestAuditRegressions::test_the_ratchet_is_keyed_by_test_not_by_file, tests/test_marker_runner_coverage.py::test_assert_is_shrink_only

- **Where:** marker_runner_coverage.py:211-212
- **Finding:** ratchet key is the file: new tests in a known file excused; node-id `known` reported stale
- **Failing input:** two unselected tests, `known=["tests/test_a.py"]` passes
- **Proposed fix:** key by `file::name`, migrate
- **Verified:** yes-repro

### MP-11 (Low) -- eval of marker expr; exception = "selects" (fail-open)

**Disposition:** RESOLVED -- `eval` is replaced by a recursive-descent parser for pytest's `and`/`or`/`not`/parentheses grammar; an unparsable `-m`/`-k` raises `ValueError`, and `find_unselected_marked_tests` reports the runner as `<label>::<bad expression>` and counts it as selecting nothing. The test that pinned the fail-open was re-framed. regression test: tests/test_marker_runner_coverage.py::test_an_unparsable_expression_is_rejected_not_read_as_selecting, tests/test_marker_runner_coverage.py::TestAuditRegressions::test_expression_parser_matches_pytest_grammar

- **Where:** marker_runner_coverage.py:159-161
- **Finding:** `eval` of marker expr; exception = "selects" (fail-open)
- **Failing input:** `-m "slow andd integration"` → True
- **Proposed fix:** small parser; report unparseable
- **Verified:** yes-read

### MP-12 (Med) -- aliased reload, importlib as il, sys as _sys missed; substring prefilter drops files

**Disposition:** RESOLVED -- `reload_primitive(node, aliases)` resolves `from importlib import reload`, `importlib as il`, `sys as _sys` and `from sys import modules` through `_core.ImportAliases`; the substring prefilter is gone (files come from `_core.scan_python`, which also reads BOM files and reports unparsable ones as sites). regression test: tests/test_module_reload_safety.py::TestAuditRegressions::test_aliased_primitives_are_found, tests/test_module_reload_safety.py::TestAuditRegressions::test_bom_and_unparsable_files

- **Where:** module_reload_safety.py:49-58, 31
- **Finding:** aliased `reload`, `importlib as il`, `sys as _sys` missed; substring prefilter drops files
- **Failing input:** `from importlib import reload; reload(sys)`
- **Proposed fix:** resolve aliases, widen prefilter
- **Verified:** yes-repro

### MP-13 (High) -- any sys.modules[...] = x counts as restore, including installing a fake

**Disposition:** RESOLVED -- a `sys.modules[...] = v`, `sys.modules.update(v)` or `mod.__dict__.update(v)` counts as a restore only when `v` uses a SNAPSHOT name, one bound in the enclosing functions from a `sys.modules`, `__dict__` or `vars(...)` read; installing a fake is a plain write. regression test: tests/test_module_reload_safety.py::TestAuditRegressions::test_installing_a_fake_is_not_a_restore

- **Where:** module_reload_safety.py:64
- **Finding:** any `sys.modules[...] = x` counts as restore, including installing a fake
- **Failing input:** `sys.modules["foo"]=object(); importlib.reload(sys)`
- **Proposed fix:** only snapshot-sourced or finally/finalizer restores
- **Verified:** yes-repro

### MP-14 (Med) -- innermost-scope only (FP); conftest/usefixtures fixtures unseen (FP); one autouse resto...

**Disposition:** RESOLVED -- every enclosing function is searched (not just the innermost), restoring fixtures from `conftest.py` files between the test file and the tests root are visible, `usefixtures` marks on functions, classes and module `pytestmark` count as requests, and an autouse restore applies only to its scope (a class's autouse to that class, a module's to the file, a conftest's to its directory). regression test: tests/test_module_reload_safety.py::TestAuditRegressions::test_an_enclosing_function_s_finally_pairs_a_nested_reload, tests/test_module_reload_safety.py::TestAuditRegressions::test_conftest_and_usefixtures_fixtures_are_seen, tests/test_module_reload_safety.py::TestAuditRegressions::test_an_autouse_restore_in_one_class_does_not_clear_the_file

- **Where:** module_reload_safety.py:98-104, 150
- **Finding:** innermost-scope only (FP); conftest/usefixtures fixtures unseen (FP); one autouse restore clears whole file (FN)
- **Failing input:** nested inner() reload with outer finally → flagged
- **Proposed fix:** walk enclosing scopes, include conftest, scope autouse
- **Verified:** yes-repro/yes-read

### MP-15 (Low) -- any __dict__.update, addfinalizer, subprocess.run counts as restore

**Disposition:** RESOLVED -- `addfinalizer(f)` counts only when `f` (a lambda, a def in the file, or `functools.partial(sys.modules.update, snapshot)`) itself restores or reloads; `__dict__.update` needs a snapshot argument; `subprocess.run` beside an in-process reload no longer counts (it isolates nothing), so the parametrized case that pinned it was removed and the autouse case now restores for real. regression test: tests/test_module_reload_safety.py::TestAuditRegressions::test_look_alike_restores_do_not_count, tests/test_module_reload_safety.py::TestAuditRegressions::test_real_finalizer_and_dict_restores_count

- **Where:** module_reload_safety.py:70-73
- **Finding:** any `__dict__.update`, `addfinalizer`, `subprocess.run` counts as restore
- **Failing input:** `request.addfinalizer(close_db)`
- **Proposed fix:** finalizer must reference module/snapshot
- **Verified:** yes-read

### MP-16 (Low) -- allowlist keyed on (path, line): drift, no stale check, missing roots skipped

**Disposition:** RESOLVED -- `find_reloads_in_code` accepts `(path, statement text)` allowlist entries (whitespace-collapsed source of the primitive, stable under edits above it) as well as the older `(path, line)`; the new `assert_no_reloads_in_code` also fails on entries that match no site; a missing root raises `CorpusError` instead of being skipped. regression test: tests/test_module_reload_safety.py::TestAuditRegressions::test_production_allowlist_by_statement_text_with_stale_check, tests/test_module_reload_safety.py::TestAuditRegressions::test_a_missing_root_raises

- **Where:** module_reload_safety.py:156-174
- **Finding:** allowlist keyed on (path, line): drift, no stale check, missing roots skipped
- **Failing input:** new reload lands on old line
- **Proposed fix:** key by statement text; fail stale/missing
- **Verified:** yes-read

### MP-17 (Med) -- success line + nonzero exit reported clean

**Disposition:** RESOLVED -- `check_mypy_output` fails a run that printed the success line but exited nonzero. The parametrized test that expected `SUCCESS` to pass at exit 1/2 pinned the defect and was re-framed. regression test: tests/test_mypy_gate.py::TestAuditRegressions::test_a_success_line_with_a_nonzero_exit_is_not_clean

- **Where:** mypy_gate.py:52-73
- **Finding:** success line + nonzero exit reported clean
- **Failing input:** `check_mypy_output("Success: ...", 2)` → None
- **Proposed fix:** require returncode 0
- **Verified:** yes-repro

### MP-18 (Low) -- --min-files w/o value IndexError; --min-files=200 passed to mypy; locale decoding on Wi...

**Disposition:** RESOLVED -- `--min-files` is consumed by an argparse pre-parser (`_split_args`, `parse_known_args`, `allow_abbrev=False`): both `--min-files N` and `--min-files=N` are removed before mypy runs, and a dangling flag is a usage error (exit 2) instead of an IndexError. mypy's output is decoded as UTF-8 with `errors="replace"` and the child gets `PYTHONIOENCODING=utf-8`. regression test: tests/test_mypy_gate.py::TestAuditRegressions::test_min_files_is_consumed_in_both_spellings, tests/test_mypy_gate.py::TestAuditRegressions::test_a_dangling_min_files_is_a_usage_error_not_an_index_error, tests/test_mypy_gate.py::TestAuditRegressions::test_main_decodes_as_utf8_and_never_forwards_min_files

- **Where:** mypy_gate.py:80-85
- **Finding:** `--min-files` w/o value IndexError; `--min-files=200` passed to mypy; locale decoding on Windows
- **Failing input:** `main(["--min-files"])`
- **Proposed fix:** argparse; utf-8 errors=replace
- **Verified:** yes-read

### MP-19 (Med) -- _ENV_PROBE substring (os in loss); any enclosing Try exempts

**Disposition:** RESOLVED -- the environment probe matches whole identifier PARTS (Name ids and attribute names split on `_` and case, plus the `HAS_*` convention) against a set of probe words, so `loss` no longer matches `os`; only an `except` handler exempts a skip, a `try` body does not. regression test: tests/test_nondiscriminating_shapes.py::TestAuditRegressions::test_an_identifier_containing_os_is_not_an_environment_probe, tests/test_nondiscriminating_shapes.py::TestAuditRegressions::test_real_probes_are_still_recognised, tests/test_nondiscriminating_shapes.py::TestAuditRegressions::test_a_skip_in_a_try_body_is_not_exempt_but_one_in_an_except_is

- **Where:** nondiscriminating_shapes.py:34,114-123
- **Finding:** `_ENV_PROBE` substring (`os` in `loss`); any enclosing Try exempts
- **Failing input:** `loss=...; if loss is None: pytest.skip()` → `[]`
- **Proposed fix:** `\b` identifiers; only ExceptHandler bodies
- **Verified:** yes-repro

### MP-20 (Low) -- reversed ranges, AnnAssign/walrus/with, from pytest import skip missed

**Disposition:** RESOLVED -- `100 > x > 0` (Gt/GtE chains) is read as the same range reversed; AnnAssign, walrus and `with f() as x` count as computing; `shape_reasons(func, aliases=ImportAliases.from_tree(module))` recognises `from pytest import skip` / `import pytest as pt`. regression test: tests/test_nondiscriminating_shapes.py::TestAuditRegressions::test_reversed_wide_ranges_are_found, tests/test_nondiscriminating_shapes.py::TestAuditRegressions::test_a_reversed_narrow_range_is_not_flagged, tests/test_nondiscriminating_shapes.py::TestAuditRegressions::test_annassign_walrus_and_with_count_as_computing, tests/test_nondiscriminating_shapes.py::TestAuditRegressions::test_from_pytest_import_skip_is_resolved_with_aliases

- **Where:** nondiscriminating_shapes.py:50,97,91
- **Finding:** reversed ranges, AnnAssign/walrus/with, `from pytest import skip` missed
- **Failing input:** `assert 100 > rmse > 0` → `[]`
- **Proposed fix:** handle Gt/GtE, AnnAssign, aliases
- **Verified:** yes-repro

### MP-21 (High) -- _unimportable discarded; walk_packages(onerror=lambda _: None) hides subpackages

**Disposition:** RESOLVED -- `walk_packages(onerror=...)` now records every subpackage that fails to import during the walk (`<name>: cannot walk (import failed)`), and `assert_package_doctests_pass` fails on any unimportable module with examples or unwalkable subpackage, unless named in the new `tolerate_unimportable` (dotted-boundary prefixes), whose stale entries also fail. regression test: tests/test_package_doctests.py::TestAuditRegressions::test_a_module_with_examples_that_will_not_import_fails_the_assert, tests/test_package_doctests.py::TestAuditRegressions::test_a_subpackage_that_fails_during_the_walk_is_reported, tests/test_package_doctests.py::TestAuditRegressions::test_a_stale_tolerance_fails

- **Where:** package_doctests.py:91
- **Finding:** `_unimportable` discarded; `walk_packages(onerror=lambda _: None)` hides subpackages
- **Failing input:** module with `>>>` raising ImportError → green
- **Proposed fix:** fail on unimportable with tolerate list
- **Verified:** yes-read

### MP-22 (Low) -- plain prefix: pkg.io skips pkg.iostats

**Disposition:** RESOLVED -- `skip_prefixes` match on a dotted boundary (`name == p or name.startswith(p + ".")`); a prefix ending in `.` keeps raw-prefix matching for callers who want it. regression test: tests/test_package_doctests.py::TestAuditRegressions::test_skip_prefixes_match_on_a_dotted_boundary

- **Where:** package_doctests.py:66
- **Finding:** plain prefix: `pkg.io` skips `pkg.iostats`
- **Failing input:** skip_prefixes=("pkg.io",)
- **Proposed fix:** `== p or startswith(p+".")`
- **Verified:** yes-read

### MP-23 (Med) -- if not x, IfExp, while, assert, comprehension ifs missed

**Disposition:** RESOLVED -- truth tests are collected from `if`, `while`, conditional expressions, `assert`, comprehension filters, `and`/`or` and `not`, unwrapping `not` and nested bool ops. The file is parsed via `_core.parse_file` (BOM handled; an unparsable file becomes an `unparsable` finding that fails the assert). regression test: tests/test_optional_truthiness.py::TestAuditRegressions::test_not_ifexp_while_assert_and_comprehension_filters_are_tests_for_truth, tests/test_optional_truthiness.py::TestAuditRegressions::test_bom_and_unparsable_files

- **Where:** optional_truthiness.py:97-108
- **Finding:** `if not x`, IfExp, while, assert, comprehension ifs missed
- **Failing input:** repro m.py lines 3/5/7
- **Proposed fix:** unwrap Not; include those tests
- **Verified:** yes-repro

### MP-24 (Med) -- key uses path.name; no stale check; line-number keys

**Disposition:** RESOLVED -- `find_truthiness_tests(path, repo_root=...)` reports repo-relative paths (the assert passes its `repo_root`, which it previously ignored); duplicates are kept; the baseline is compared as a multiset on the line-free key (`finding_key`), so a line shift does not invalidate it and an entry that matches nothing fails as stale. regression test: tests/test_optional_truthiness.py::TestAuditRegressions::test_findings_are_repo_relative_and_duplicates_are_kept, tests/test_optional_truthiness.py::TestAuditRegressions::test_a_baseline_entry_survives_a_line_shift_and_a_stale_one_fails

- **Where:** optional_truthiness.py:104,127-132
- **Finding:** key uses `path.name`; no stale check; line-number keys
- **Failing input:** two `utils.py`, one baselined
- **Proposed fix:** repo-relative key, stale check
- **Verified:** yes-read

### MP-25 (Low) -- walk descends into nested defs; Annotated not unwrapped

**Disposition:** RESOLVED -- each function is scanned over its own body only; a nested def or lambda inherits the enclosing optionals minus the names it rebinds as parameters, and `Annotated[T, ...]` is unwrapped to `T`. regression test: tests/test_optional_truthiness.py::TestAuditRegressions::test_a_nested_def_rebinding_the_name_is_judged_on_its_own_parameter, tests/test_optional_truthiness.py::TestAuditRegressions::test_annotated_is_unwrapped

- **Where:** optional_truthiness.py:97
- **Finding:** walk descends into nested defs; `Annotated` not unwrapped
- **Failing input:** nested `def g(limit: list)`
- **Proposed fix:** stop at nested defs
- **Verified:** yes-read

### MP-26 (Med) -- from pkg.a import _impl and relative imports missed

**Disposition:** RESOLVED -- private_imports resolves relative imports against the importer's package (`_core.resolve_relative`/`package_of`, same for `__init__.py`). It also judges `module.alias` for `from pkg.a import _impl`. When the module itself is already private, only the module is reported, so existing allowlists keep matching. regression test: tests/test_private_imports.py::TestAuditRegressions::test_a_private_name_from_a_public_module_is_flagged, tests/test_private_imports.py::TestAuditRegressions::test_relative_imports_are_resolved, tests/test_private_imports.py::TestAuditRegressions::test_a_relative_sibling_import_is_allowed

- **Where:** private_imports.py:42-48,77-80
- **Finding:** `from pkg.a import _impl` and relative imports missed
- **Failing input:** repro: 2 of 3 missed
- **Proposed fix:** judge `module.alias`; resolve relatives
- **Verified:** yes-repro

### MP-27 (Low) -- relative_to ValueError when src outside root

**Disposition:** RESOLVED -- importer paths go through `_core.relative_posix`, which returns the absolute POSIX path when the file is not under `repo_root`, so it no longer raises `ValueError`. regression test: tests/test_private_imports.py::TestAuditRegressions::test_src_outside_repo_root_does_not_raise

- **Where:** private_imports.py:81
- **Finding:** `relative_to` ValueError when src outside root
- **Failing input:** src outside repo
- **Proposed fix:** fallback
- **Verified:** yes-read

### MP-28 (Low) -- non-recursive glob; parse errors silent; import_module("pkg._x") missed

**Disposition:** RESOLVED -- `assert_no_private_meta_imports` enumerates `test_*.py` recursively through `_core.iter_files` (kwarg `recursive=False` keeps the old view; a missing dir raises `CorpusError`); `private_meta_imports` parses through `_core.scan_python`, so a BOM file is checked and an unparsable one is returned as `<stem>::<unparsed>` and fails the assert; `imported_names` also returns the literal target of `importlib.import_module(...)` / `__import__(...)`, resolved through `ImportAliases`. regression test: tests/test_meta_private_imports.py::TestAuditRegressions::test_a_meta_test_in_a_subdirectory_is_scanned, tests/test_meta_private_imports.py::TestAuditRegressions::test_an_unparsable_meta_test_fails_instead_of_being_skipped, tests/test_meta_private_imports.py::TestAuditRegressions::test_import_module_with_a_literal_private_name_is_caught, tests/test_meta_private_imports.py::TestAuditRegressions::test_a_bom_file_is_parsed

- **Where:** meta_private_imports.py:84,57-59
- **Finding:** non-recursive glob; parse errors silent; `import_module("pkg._x")` missed
- **Failing input:** `meta/sub/test_x.py`
- **Proposed fix:** rglob, report parse failures
- **Verified:** yes-read

### MP-29 (Med) -- # inside a string treated as comment (FP)

**Disposition:** RESOLVED -- Python comments come from `tokenize` COMMENT tokens, so a `#` inside a string literal is not a comment. Files are read through `_core.parse_source`/`read_source` (BOM handled); an unreadable or unparsable file is reported as `<rel>:<line>: unparsable: ...` instead of contributing nothing. regression test: tests/test_phantom_code_references.py::TestAuditRegressions::test_a_hash_inside_a_string_is_not_a_comment, tests/test_phantom_code_references.py::TestAuditRegressions::test_bom_and_unparsable_files

- **Where:** phantom_code_references.py:181-182
- **Finding:** `#` inside a string treated as comment (FP)
- **Failing input:** `"http://a#`Ghost()`"`
- **Proposed fix:** tokenize COMMENT tokens
- **Verified:** yes-repro

### MP-30 (Med) -- docstring parity flips on SQL = """ literals

**Disposition:** RESOLVED -- documentation lines are the AST spans of bare string statements (docstrings and attribute docstrings), not a triple-quote parity count, so an assigned `SQL = """..."""` literal is code and cannot flip the state of later lines. regression test: tests/test_phantom_code_references.py::TestAuditRegressions::test_an_assigned_triple_quoted_literal_does_not_flip_docstring_parity

- **Where:** phantom_code_references.py:166-180
- **Finding:** docstring parity flips on `SQL = """` literals
- **Failing input:** SQL block in repro
- **Proposed fix:** tokenize/AST spans
- **Verified:** yes-read

### MP-31 (Med) -- declared head → Class.member never checked; a.b.c skipped

**Disposition:** RESOLVED -- `python_declarations` records each class's members (methods, class attributes, nested classes, `self.x` assignments), inherits them from bases declared in the same files, and marks a class with any undeclared base as open (`Class.*`). A `Class.member` reference to a closed repo class must name a declared member; `a.b.c` is now tokenised and judged on its first member. The existing test that accepted `Foo.anything` pinned the defect and was re-framed. regression test: tests/test_phantom_code_references.py::TestAuditRegressions::test_members_of_repo_classes_are_checked_and_open_classes_are_not, tests/test_phantom_code_references.py::TestAuditRegressions::test_a_three_part_dotted_name_is_judged_on_its_first_member, tests/test_phantom_code_references.py::test_a_backticked_name_that_is_declared_passes_and_an_undeclared_one_fails

- **Where:** phantom_code_references.py:226-231
- **Finding:** declared head → `Class.member` never checked; `a.b.c` skipped
- **Failing input:** `` `Foo.renamed_method()` ``
- **Proposed fix:** check members of repo classes
- **Verified:** yes-repro

### MP-32 (Low) -- test files matched by basename over rglob incl .venv

**Disposition:** RESOLVED -- test files are indexed with `_core.iter_files` (git-tracked plus untracked-not-ignored, `DEFAULT_EXCLUDE` pruned, so `.venv` copies never count); a token with a directory must match that repo-relative path (or a path ending in it), a bare name matches a basename. regression test: tests/test_phantom_code_references.py::TestAuditRegressions::test_test_files_are_matched_by_path_and_excluded_dirs_do_not_count

- **Where:** phantom_code_references.py:205,217
- **Finding:** test files matched by basename over rglob incl .venv
- **Failing input:** `tests/unit/test_foo.py` claim
- **Proposed fix:** full path, tracked files
- **Verified:** yes-read

### MP-33 (Med) -- also resolves vs repo root (FN); /x.md joined to drive root (FP)

**Disposition:** RESOLVED -- a target resolves only the way a renderer resolves it: relative to the referencing file's directory, and a leading `/` relative to `repo_root` (not the drive root); the fallback to the repo root that hid `docs/guide.md -> README.md` is gone. regression test: tests/test_phantom_markdown_links.py::TestAuditRegressions::test_a_link_resolves_against_its_own_directory_not_the_repo_root, tests/test_phantom_markdown_links.py::TestAuditRegressions::test_a_leading_slash_is_the_repo_root_not_the_drive_root

- **Where:** phantom_markdown_links.py:80
- **Finding:** also resolves vs repo root (FN); `/x.md` joined to drive root (FP)
- **Failing input:** `docs/guide.md` → `[a](README.md)` passes
- **Proposed fix:** resolve relative to file; `/` = repo root
- **Verified:** yes-repro

### MP-34 (Low) -- anchors, queries, <..>, reference links, other extensions skipped; fences scanned

**Disposition:** RESOLVED -- links and images with any extension (or a directory) are checked; `#fragment`/`?query` are dropped before the check, `<...>` targets, titles, `%20` and reference definitions (`[id]: path`, footnotes excluded) are handled; fenced blocks and code spans are skipped; any `scheme:` is external. The file is read via `_core.read_source` and an unreadable one is reported. regression test: tests/test_phantom_markdown_links.py::TestAuditRegressions::test_fragments_queries_angle_brackets_titles_and_other_extensions, tests/test_phantom_markdown_links.py::TestAuditRegressions::test_fenced_code_and_code_spans_are_not_links, tests/test_phantom_markdown_links.py::TestAuditRegressions::test_an_unreadable_file_is_reported

- **Where:** phantom_markdown_links.py:39
- **Finding:** anchors, queries, `<..>`, reference links, other extensions skipped; fences scanned
- **Failing input:** `[c](missing.md#sec)`
- **Proposed fix:** strip fragment, widen, skip fences
- **Verified:** yes-repro

### MP-35 (High) -- required non-str fields get string sentinels → ValidationError read as "enforced"; stil...

**Disposition:** RESOLVED -- probes start from a VALID instance: `_valid_base` fills every required field with typed candidates (Literal members, values inside the field's own bounds, then generic scalars), advancing only the fields a `ValidationError` blames; a rejection counts as "enforced" only when the error's `loc` names the probed field. A probe that cannot be made conclusive is no longer counted as audited; it is collected via the new `inconclusive=` list, shown in the floor failure, and fails with `assert_field_bounds_enforced(..., allow_inconclusive=False)`. regression test: tests/test_pydantic_field_bounds.py::TestAuditRegressions::test_a_required_int_field_does_not_make_every_probe_look_enforced, tests/test_pydantic_field_bounds.py::TestAuditRegressions::test_required_bounded_and_literal_fields_are_filled_validly, tests/test_pydantic_field_bounds.py::TestAuditRegressions::test_an_unfillable_model_is_inconclusive_and_fails_the_floor

- **Where:** pydantic_field_bounds.py:43-44,67-77,93
- **Finding:** required non-str fields get string sentinels → ValidationError read as "enforced"; still counted as audited
- **Failing input:** `Unenf` with required `n: int` → `(1, [])`
- **Proposed fix:** probe valid kwargs first; inconclusive otherwise; check error `loc`
- **Verified:** yes-repro

### MP-36 (Med) -- only first bound probed; conint/Interval skipped

**Disposition:** RESOLVED -- every bound of a field is its own probe (`ge=0, le=1` is two), and bounds are read from any metadata object carrying `gt`/`ge`/`lt`/`le`, so `conint`/`confloat` (`annotated_types.Interval`) are probed. regression test: tests/test_pydantic_field_bounds.py::TestAuditRegressions::test_every_bound_is_probed, tests/test_pydantic_field_bounds.py::TestAuditRegressions::test_conint_interval_is_probed

- **Where:** pydantic_field_bounds.py:90
- **Finding:** only first bound probed; `conint`/Interval skipped
- **Failing input:** `ge=0, le=1` never tests le
- **Proposed fix:** probe all bounds, unpack Interval
- **Verified:** yes-repro

### MP-37 (Low) -- fractional bound on int field rejected by int parsing

**Disposition:** RESOLVED -- for an `int`/`Optional[int]` field the violating value is an integer (`ge b -> ceil(b)-1`, `gt -> floor(b)`, `le -> floor(b)+1`, `lt -> ceil(b)`), so int parsing can never be what rejects the probe. pydantic 2.13 itself now refuses `int` with `lt=0.5` at schema build, so the fractional case is pinned on the helper, with an integral-bound model as the end-to-end check. regression test: tests/test_pydantic_field_bounds.py::TestAuditRegressions::test_a_fractional_bound_on_an_int_field_uses_an_integer_violation

- **Where:** pydantic_field_bounds.py:57-58
- **Finding:** fractional bound on int field rejected by int parsing
- **Failing input:** `n: int = Field(0, lt=0.5)`
- **Proposed fix:** typed violating value
- **Verified:** yes-read

### MP-38 (Med) -- [tool.pytest], pytest.toml, root conftest, non-literal addinivalue_line missed → false...

**Disposition:** RESOLVED -- `registered_markers` also reads pytest 9's native `[tool.pytest].markers`, `pytest.toml`/`.pytest.toml` (`[pytest].markers`) and the repo-root `conftest.py`; `conftest_registrations` resolves `addinivalue_line` values that are module-level string constants (or tuples of them), `for` loop variables over literals, and f-string/`%`/`+` heads that hold the name, and a registration it still cannot resolve is named in the failure message. Conftests are parsed via `_core.parse_source` (BOM handled; an unparsable one raises). The test that used `[tool.pytest]` as its unreadable table pinned the gap and now uses a truly moved table. regression test: tests/test_pytest_markers.py::TestAuditRegressions::test_native_toml_tables_and_pytest_toml_register, tests/test_pytest_markers.py::TestAuditRegressions::test_the_root_conftest_and_non_literal_registrations_count, tests/test_pytest_markers.py::TestAuditRegressions::test_an_unresolvable_registration_is_named_in_the_failure, tests/test_pytest_markers.py::TestAuditRegressions::test_bom_and_unparsable_conftests

- **Where:** pytest_markers.py:62-69,73,105
- **Finding:** `[tool.pytest]`, `pytest.toml`, root conftest, non-literal `addinivalue_line` missed → false "unregistered"
- **Failing input:** repro r9
- **Proposed fix:** read those sources
- **Verified:** yes-repro

### MP-39 (Low) -- configparser error swallowed

**Disposition:** RESOLVED -- `ini_markers` no longer swallows `configparser.Error`: a duplicate option or section in the pytest section raises `ValueError` naming the file, since pytest cannot load it either; a duplicate in another tool's section is re-read with `strict=False` so the markers are still found; any other parse error raises. regression test: tests/test_pytest_markers.py::TestAuditRegressions::test_a_duplicate_markers_option_in_the_pytest_section_is_surfaced, tests/test_pytest_markers.py::TestAuditRegressions::test_a_duplicate_in_another_tool_s_section_does_not_hide_the_markers

- **Where:** pytest_markers.py:90-93
- **Finding:** configparser error swallowed
- **Failing input:** tox.ini duplicate `markers`
- **Proposed fix:** strict=False or surface
- **Verified:** yes-read

### MP-40 (Low) -- pin regex needs "ruff==x"; pre-commit regex order/quote sensitive

**Disposition:** RESOLVED -- `pinned_version` parses pyproject as TOML and reads every string value as a requirement (PEP 503 name match, single or double quotes, extras, markers, spaces around `==`); a comment no longer counts, and a non-TOML fragment is read string by string. `precommit_ruff_revs` parses the config with `yaml.safe_load` (PyYAML is a hard dependency), so key order, quoting, flow style and `.git`/`git@` URLs all work; invalid YAML raises `ValueError`, which the CLI reports as a problem. regression test: tests/test_pinned_tool_versions.py::TestAuditRegressions::test_every_pin_spelling_is_read, tests/test_pinned_tool_versions.py::TestAuditRegressions::test_a_pin_in_a_comment_or_of_another_dist_is_not_read, tests/test_pinned_tool_versions.py::TestAuditRegressions::test_pre_commit_revs_are_read_in_any_order_or_quoting, tests/test_pinned_tool_versions.py::TestAuditRegressions::test_other_repos_are_ignored_and_invalid_yaml_is_an_error

- **Where:** pinned_tool_versions.py:37,55
- **Finding:** pin regex needs `"ruff==x"`; pre-commit regex order/quote sensitive
- **Failing input:** `['ruff==0.6.0']` → None
- **Proposed fix:** tomllib + packaging; yaml
- **Verified:** yes-repro

### MP-41 (Med) -- $defs names reported as fields

**Disposition:** RESOLVED -- `keys_in_schema` treats `$defs`, `definitions`, `patternProperties` and `dependentSchemas` as name-to-schema maps: it searches only their values, so definition names are no longer reported as fields. regression test: tests/test_prompt_field_parity.py::TestAuditRegressions::test_defs_names_are_definitions_not_fields

- **Where:** prompt_field_parity.py:130-132
- **Finding:** `$defs` names reported as fields
- **Failing input:** `$defs: {Inner: ...}` → Inner
- **Proposed fix:** recurse into values only
- **Verified:** yes-repro

### MP-42 (Med) -- DDL types lack INT/BOOL/FLOAT/CHAR/quoted names

**Disposition:** RESOLVED -- the new `ddl_columns` parses each `CREATE TABLE (...)` body by top-level commas (comments stripped, strings skipped): every column definition counts whatever its type (`INT`, `BOOL`, `FLOAT`, `CHAR(n)`, `TINYINT`...), with `"quoted"`, backtick or bracket names, and table constraints are skipped. It also reads `ALTER TABLE ... ADD [COLUMN] [IF NOT EXISTS]`. `persisted_names_sql` uses it. regression test: tests/test_prompt_field_parity.py::TestAuditRegressions::test_ddl_columns_of_any_type_and_quoted_names

- **Where:** prompt_field_parity.py:233
- **Finding:** DDL types lack INT/BOOL/FLOAT/CHAR/quoted names
- **Failing input:** `score INT` → set()
- **Proposed fix:** generic `\w+ type` or sqlglot
- **Verified:** yes-repro

### MP-43 (Med) -- unparsable prompt module contributes nothing (fail-open); unguarded non-UTF8 reads crash

**Disposition:** RESOLVED -- every file-reading function (`prompt_keys`, `consumed_names`, `accessor_keys`, `persisted_names_*`, `declared_scalar_fields`) reads through `_core.parse_source`/`read_source`, so a BOM is handled, and raises `UnparsedFilesError` naming each unreadable or unparsable file instead of dropping it or crashing with a bare `UnicodeDecodeError`. Directories are enumerated with `_core.iter_files`. `string_literals(src, strict=True)` raises on bad source. regression test: tests/test_prompt_field_parity.py::TestAuditRegressions::test_an_unparsable_prompt_module_raises_instead_of_contributing_nothing, tests/test_prompt_field_parity.py::TestAuditRegressions::test_non_utf8_files_raise_a_located_error_not_a_bare_decode_error, tests/test_prompt_field_parity.py::TestAuditRegressions::test_a_bom_prompt_module_is_read

- **Where:** prompt_field_parity.py:93-95,153
- **Finding:** unparsable prompt module contributes nothing (fail-open); unguarded non-UTF8 reads crash
- **Failing input:** 3.13 syntax on 3.11 CI
- **Proposed fix:** raise/report
- **Verified:** yes-read

### MP-44 (Low) -- Sequence[, Mapping[, frozenset[, Optional[list] treated as scalar

**Disposition:** RESOLVED -- container detection parses the annotation: `Optional`/`Union`/`Annotated`/`ClassVar`... are unwrapped, and a member whose origin is any concrete or abstract container (`Sequence`, `Mapping`, `frozenset`, `Iterable`, `deque`, bare `list`...) makes the field a container. The old prefix test missed these and also caught names such as `listing_id`. regression test: tests/test_prompt_field_parity.py::TestAuditRegressions::test_abstract_and_optional_containers_are_not_scalars

- **Where:** prompt_field_parity.py:278
- **Finding:** `Sequence[`, `Mapping[`, `frozenset[`, `Optional[list]` treated as scalar
- **Failing input:** `tags: Sequence[str]`
- **Proposed fix:** parse annotation origin
- **Verified:** yes-read

### MP-45 (Low) -- float(group(1)) crashes on groupless pattern, None group, 1.2k

**Disposition:** RESOLVED -- `find_stale_claims` compiles the pattern and reports (never raises) a pattern that is invalid or does not have exactly one capture group, an occurrence whose group did not take part, and a capture that is not a number. `parse_stated_number` reads thousands separators and `k`/`M`/`B` suffixes (`1.2k` is 1200). Files are read via `_core.read_source` (BOM stripped; an undecodable file is reported). regression test: tests/test_prose_numeric_claims.py::TestAuditRegressions::test_a_pattern_without_exactly_one_group_is_a_problem_not_a_crash, tests/test_prose_numeric_claims.py::TestAuditRegressions::test_a_group_that_did_not_participate_is_a_problem, tests/test_prose_numeric_claims.py::TestAuditRegressions::test_suffixed_numbers_are_read, tests/test_prose_numeric_claims.py::TestAuditRegressions::test_a_non_numeric_capture_is_reported

- **Where:** prose_numeric_claims.py:99
- **Finding:** `float(group(1))` crashes on groupless pattern, None group, `1.2k`
- **Failing input:** `r"\d+ tests"`
- **Proposed fix:** validate `groups==1`; report bad parse
- **Verified:** yes-read

### RS-1 (High) -- BOM files dropped; sql_verifier_coverage.py:63 crashes

**Disposition:** RESOLVED -- every listed module now reads through `_core` (`read_source`/`parse_file`/`scan_python`, BOM stripped, interpreter-exact decoding): readme_env_var_parity, resource_release_paths, runtime_registry_mutation, source_text_claims, spec_bound_doubles, sql_verifier_coverage (a BOM verifier no longer crashes `verifier_lists`) and statement_compilation, plus survivorship_scoring, source_text_ban, save_failure_markers and sqlalchemy_text_binds; regression test: tests/test_readme_env_var_parity.py::TestReadForms::test_a_bom_file_is_read, tests/test_resource_release_paths.py::TestFindUnprotectedReleases::test_a_bom_file_is_parsed, tests/test_runtime_registry_mutation.py::test_a_file_outside_the_root_and_a_bom_file, tests/test_source_text_claims.py::TestResolutionFixturesAndClosures::test_a_bom_file_is_read_and_a_broken_one_raises, tests/test_spec_bound_doubles.py::TestFindUnboundDoubles::test_a_bom_file_is_parsed, tests/test_sql_verifier_coverage.py::test_bom_files_are_read_on_both_sides, tests/test_statement_compilation.py::test_a_file_outside_root_and_a_bom_file_are_checked

- **Where:** readme_env_var_parity.py:131, resource_release_paths.py:71,90, runtime_registry_mutation.py:114, source_text_claims.py:272-283, spec_bound_doubles.py:70, sql_verifier_coverage.py:42, statement_compilation.py:103
- **Finding:** BOM files dropped; sql_verifier_coverage.py:63 crashes
- **Failing input:** BOM + `os.getenv('BOMVAR')` → set()
- **Proposed fix:** `utf-8-sig`
- **Verified:** yes-repro

### RS-2 (High) -- parse errors fail open; floors count inputs not parsed files

**Disposition:** RESOLVED -- no module in the set skips an unparsable file any more: the find_* functions raise `SourceError`/`UnparsedFilesError` (an `allow_unparsed=True` kwarg opts out where a set is returned), the assert_* functions list the file as a problem, and every floor (`min_files`/`min_subjects`) counts PARSED files; tests that pinned the silent skip were re-framed; regression test: tests/test_readme_env_var_parity.py::TestUnparsedAndFloor::test_the_asserts_fail_on_an_unparsable_file_and_an_empty_corpus, tests/test_runtime_registry_mutation.py::test_an_unparsable_file_fails_and_the_floor_counts_parsed_files, tests/test_source_text_claims.py::TestTheRatchet::test_an_unparsable_or_undecodable_file_fails_and_the_floor_counts_checked_files, tests/test_resource_release_paths.py::test_the_assert_reports_an_unparsable_file_and_a_file_outside_the_root, tests/test_statement_compilation.py::test_an_empty_corpus_and_an_unparsable_file_are_reported, tests/test_sql_verifier_coverage.py::test_an_unparsable_file_fails_instead_of_being_skipped, tests/test_survivorship_scoring.py::test_an_unparsable_file_fails_and_the_floor_counts_parsed_files

- **Where:** same + runtime_registry_mutation.py:139, source_text_claims.py:317
- **Finding:** parse errors fail open; floors count inputs not parsed files
- **Failing input:** syntax-error file passes `min_files=1`
- **Proposed fix:** fail on unparsable; floor on parsed
- **Verified:** yes-repro

### RS-3 (Med) -- from os import environ/getenv, os as _os, os.environ["X"], getenv(key=) missed

**Disposition:** RESOLVED -- env reads are matched by import-resolved name (`_core.ImportAliases`): `from os import environ/getenv`, `import os as _os`, `os.environ["X"]` (load context only; a write is not a read), `setdefault`/`pop`, `"X" in os.environ` and `getenv(key="X")` are found, and an unrelated local `environ`/`getenv` is not; regression test: tests/test_readme_env_var_parity.py::TestReadForms::test_aliases_subscripts_and_keywords, tests/test_readme_env_var_parity.py::TestReadForms::test_an_unrelated_get_or_getenv_is_not_a_read

- **Where:** readme_env_var_parity.py:36-37
- **Finding:** `from os import environ/getenv`, `os as _os`, `os.environ["X"]`, `getenv(key=)` missed
- **Failing input:** 5 forms → set()
- **Proposed fix:** resolve aliases, Subscript, kwargs
- **Verified:** yes-repro

### RS-4 (Med) -- missing baseline seeds and skips

**Disposition:** RESOLVED -- `assert_no_new_undocumented_env_vars` enforces through `_core.Baseline`: a missing baseline fails and is written only by an explicit refresh (the tests that pinned auto-seeding were re-framed). source_text_claims' baseline got the same treatment (see RS-36); regression test: tests/test_readme_env_var_parity.py::TestAssertNoNewUndocumentedEnvVars::test_a_missing_baseline_fails_instead_of_seeding

- **Where:** readme_env_var_parity.py:224, source_text_claims.py:363
- **Finding:** missing baseline seeds and skips
- **Failing input:** delete baseline in CI
- **Proposed fix:** seed only on refresh; fail when absent
- **Verified:** yes-read

### RS-5 (Low) -- baseline never tightens (test pins it)

**Disposition:** RESOLVED -- a baselined var that is now documented or no longer read fails as stale until a refresh prunes it, so the baseline only shrinks; the test that pinned the silent pass was re-framed; regression test: tests/test_readme_env_var_parity.py::TestAssertNoNewUndocumentedEnvVars::test_documenting_a_grandfathered_var_is_stale_until_the_baseline_shrinks

- **Where:** readme_env_var_parity.py:232
- **Finding:** baseline never tightens (test pins it)
- **Failing input:** documented X stays excused
- **Proposed fix:** fail on stale entries
- **Verified:** yes-read

### RS-6 (Low) -- refresh from sys.argv misses xdist/pytest.main

**Disposition:** RESOLVED -- the refresh is read with `_core.refresh_requested` (pytest option via the new `request=` kwarg, `PY_CI_SHARED_REFRESH`, then `sys.argv`), and `register_refresh_option` is a thin wrapper over `register_refresh_options`; regression test: tests/test_readme_env_var_parity.py::TestAssertNoNewUndocumentedEnvVars::test_refresh_flag_reseeds_via_the_pytest_option

- **Where:** readme_env_var_parity.py:224
- **Finding:** refresh from `sys.argv` misses xdist/`pytest.main`
- **Failing input:** `pytest.main([...])`
- **Proposed fix:** `config.getoption`
- **Verified:** yes-read

### RS-7 (Med) -- .coverage substring flags .coveragerc; /build/ flags packages named build

**Disposition:** RESOLVED -- generated-artefact patterns are matched on path components (new public `matches_generated_pattern`): `name/` is a directory at any depth, `/name/` only at the root, `a/b` a trailing component sequence, and a slash-less pattern the file name; `.coveragerc` and `src/pkg/build/` are no longer flagged, `.coverage.<suffix>` parallel files now are; regression test: tests/test_repo_hygiene.py::TestGeneratedPatternsMatchComponents::test_coveragerc_is_not_coverage_output, tests/test_repo_hygiene.py::TestGeneratedPatternsMatchComponents::test_a_package_named_build_below_the_root_is_not_build_output

- **Where:** repo_hygiene.py:81,123
- **Finding:** `.coverage` substring flags `.coveragerc`; `/build/` flags packages named build
- **Failing input:** tracked `.coveragerc`
- **Proposed fix:** anchor on components/basename
- **Verified:** yes-repro

### RS-8 (Med) -- ls-files without -z: non-ASCII paths quoted, rules miss them

**Disposition:** RESOLVED -- `_tracked_files` runs `git ls-files -z` and decodes with surrogateescape, so non-ASCII paths are not C-quoted and every rule sees them; the text-file walk now uses `_core.git_listing`; regression test: tests/test_repo_hygiene.py::TestGeneratedPatternsMatchComponents::test_a_non_ascii_path_is_matched_and_reported_verbatim

- **Where:** repo_hygiene.py:102,262
- **Finding:** `ls-files` without `-z`: non-ASCII paths quoted, rules miss them
- **Failing input:** `audits/проба.py`
- **Proposed fix:** `-z` + surrogateescape
- **Verified:** yes-repro

### RS-9 (Med) -- ${COV:-} accepted as guard

**Disposition:** RESOLVED -- only `${VAR:?...}` or `${VAR:-<non-empty default>}` count as expansion guards; `${VAR:-}` and `: "${VAR:-}"` do not; regression test: tests/test_repo_hygiene.py::TestNumericGuardForms::test_an_empty_default_is_not_a_guard

- **Where:** repo_hygiene.py:95
- **Finding:** `${COV:-}` accepted as guard
- **Failing input:** `X="${COV:-}"` then compare
- **Proposed fix:** only `:?` or non-empty default before compare
- **Verified:** yes-repro

### RS-10 (Low) -- test -n "$COV" not a guard (FP)

**Disposition:** RESOLVED -- `test -n/-z`, `[[ -n/-z ]]` and `[ "$X" = "" ]`/`==` are accepted guards; regression test: tests/test_repo_hygiene.py::TestNumericGuardForms::test_test_dash_n_is_a_guard

- **Where:** repo_hygiene.py:91-97
- **Finding:** `test -n "$COV"` not a guard (FP)
- **Failing input:** `test -n "$COV" \|\| exit 1`
- **Proposed fix:** add to template
- **Verified:** yes-repro

### RS-11 (Low) -- per-line simple $X compares only

**Disposition:** RESOLVED -- the comparison regex accepts modified expansions (`${COV%\%}`, `${X#..}`, `${X:-..}`), the number on either side (`80 -gt "$X"`, `80 > $X`), `test` and `[[ ]]`; the check stays per line, with the guard searched across the whole run block; regression test: tests/test_repo_hygiene.py::TestNumericGuardForms::test_modified_expansions_and_reversed_operands_are_compared

- **Where:** repo_hygiene.py:86-90
- **Finding:** per-line simple `$X` compares only
- **Failing input:** `echo "${COV%\%} < 80" \| bc`
- **Proposed fix:** widen regex
- **Verified:** yes-read

### RS-12 (Med) -- only arg-less .dispose()

**Disposition:** RESOLVED -- a release is any `Call` whose callee attribute is the release name, whatever its arguments (`await e.dispose(close=False)`); regression test: tests/test_resource_release_paths.py::TestFindUnprotectedReleases::test_a_release_with_arguments_is_a_release

- **Where:** resource_release_paths.py:79
- **Finding:** only arg-less `.dispose()`
- **Failing input:** `await e.dispose(close=False)`
- **Proposed fix:** match `func.attr`
- **Verified:** yes-repro

### RS-13 (Low) -- substring protection; redispose() exempts module

**Disposition:** RESOLVED -- protection is decided on Call nodes, not unparsed substrings: a release inside a `finally`/`with` body protects releases of the SAME receiver only, so `fh.redispose()` or a protected `other.dispose()` no longer exempt the module; regression test: tests/test_resource_release_paths.py::TestFindUnprotectedReleases::test_protection_is_by_call_name_and_receiver_not_substring

- **Where:** resource_release_paths.py:60,63
- **Finding:** substring protection; `redispose()` exempts module
- **Failing input:** `with open(): fh.redispose()`
- **Proposed fix:** Call node match, same receiver
- **Verified:** yes-repro

### RS-14 (Low) -- constructor alias missed

**Disposition:** RESOLVED -- constructors are matched by the spelled name and the last segment of the import-resolved name (`create_async_engine as cae`), while a local function merely named like the alias is not; regression test: tests/test_resource_release_paths.py::TestSubjects::test_an_aliased_constructor_makes_a_subject

- **Where:** resource_release_paths.py:48
- **Finding:** constructor alias missed
- **Failing input:** `create_async_engine as cae`
- **Proposed fix:** resolve asnames
- **Verified:** yes-read

### RS-15 (Med) -- decorator-factory inner deco flagged (FP)

**Disposition:** RESOLVED -- functions are visited with their enclosing chain, and a function nested in an import-time helper inherits the exemption (a decorator factory's inner `deco`); a same-shaped factory never applied at import is still flagged; regression test: tests/test_runtime_registry_mutation.py::test_a_decorator_factory_inner_function_is_import_time

- **Where:** runtime_registry_mutation.py:127
- **Finding:** decorator-factory inner `deco` flagged (FP)
- **Failing input:** `def register(n): def deco(fn): _REGISTRY[n]=fn`
- **Proposed fix:** inherit exemption for nested
- **Verified:** yes-repro

### RS-16 (High) -- any name called at module scope (incl

**Disposition:** RESOLVED -- calls under `if __name__ == "__main__":` are not import-time, and a helper is matched to its defining module (a local name to the same file, an imported name through `_core.ImportAliases` to the module it comes from, an attribute call on a module-level object to methods only), so a same-named function elsewhere is no longer exempt; regression test: tests/test_runtime_registry_mutation.py::test_a_main_guard_call_is_not_import_time, tests/test_runtime_registry_mutation.py::test_a_helper_is_matched_to_the_module_it_is_imported_from

- **Where:** runtime_registry_mutation.py:84-88,127
- **Finding:** any name called at module scope (incl. `__main__` guard) exempts that name everywhere
- **Failing input:** `def main(): _REGISTRY[...]=1`
- **Proposed fix:** exclude `__main__`; per-module match
- **Verified:** yes-repro

### RS-17 (Med) -- collections.defaultdict/OrderedDict not recognised

**Disposition:** RESOLVED -- a registry's value is recognised by the import-resolved callee's last segment (`collections.defaultdict`, `collections.OrderedDict`, `OrderedDict as OD`, `Counter`, `ChainMap`, weak dicts); regression test: tests/test_runtime_registry_mutation.py::test_collections_dict_factories_are_registries

- **Where:** runtime_registry_mutation.py:61
- **Finding:** `collections.defaultdict/OrderedDict` not recognised
- **Failing input:** `collections.defaultdict(list)`
- **Proposed fix:** `_callee_name`
- **Verified:** yes-repro

### RS-18 (Low) -- registries under try/if; mod._REGISTRY[k]=; aliases missed

**Disposition:** RESOLVED -- module-level registries are collected inside `if`/`try`/`with`/`for` blocks, and writes through `mod._REGISTRY[k]` or an import alias (`from reg import X_REGISTRY as R`) are found; regression test: tests/test_runtime_registry_mutation.py::test_registries_under_try_and_writes_through_a_module_or_alias

- **Where:** runtime_registry_mutation.py:53,101-104
- **Finding:** registries under try/if; `mod._REGISTRY[k]=`; aliases missed
- **Failing input:** `reg._REGISTRY[k]=v`
- **Proposed fix:** walk compound stmts; Attribute targets
- **Verified:** yes-read

### RS-19 (Low) -- relative_to crash outside root

**Disposition:** RESOLVED -- files are scanned with `_core.scan_python(root=repo_root)`, whose `rel` falls back to the absolute POSIX path for a file outside the root instead of raising; regression test: tests/test_runtime_registry_mutation.py::test_a_file_outside_the_root_and_a_bom_file

- **Where:** runtime_registry_mutation.py:117
- **Finding:** `relative_to` crash outside root
- **Failing input:** `/tmp/x.py`
- **Proposed fix:** fallback
- **Verified:** yes-read

### RS-20 (Med) -- patch applied although target check found missing attrs

**Disposition:** RESOLVED -- `_verify_patch_target` returns the missing attributes and `patch_stash_restore` returns False without patching when ANY attribute the copy calls is missing (it used to patch unless only `_git_apply` was absent); regression test: tests/test_safe_precommit.py::test_the_patch_is_not_applied_when_an_attribute_it_calls_is_missing

- **Where:** safe_precommit.py:150
- **Finding:** patch applied although target check found missing attrs
- **Failing input:** pre-commit without `_CHECKOUT_CMD`
- **Proposed fix:** return False when missing
- **Verified:** yes-read

### RS-21 (Low) -- failed restore leaves concurrent edits only in patch file

**Disposition:** RESOLVED -- a failed restore is logged at ERROR, printed to stderr as a banner with the exact `git apply --3way` command, and the patch is copied to `.git/safe-precommit-unrestored/` so it survives pre-commit's cache cleanup; a clean restore prints nothing and keeps no copy; regression test: tests/test_safe_precommit.py::test_a_failed_restore_is_loud_and_keeps_a_copy_under_the_git_dir, tests/test_safe_precommit.py::test_a_clean_restore_keeps_no_copy_and_prints_nothing

- **Where:** safe_precommit.py:101-115
- **Finding:** failed restore leaves concurrent edits only in patch file
- **Failing input:** concurrent edit during hooks
- **Proposed fix:** louder signal
- **Verified:** yes-read

### RS-22 (Med) -- rf"/F", .extend, +=, dynamic f-strings missed; comments matched

**Disposition:** RESOLVED -- the default detection reads the AST: strings/f-strings with any prefix (`rf"`, `F"`) passed to `.append/.extend/.insert/.add`, added with `+=`, or given as `marker=`; comments and docstrings never count; a marker whose name is interpolated is reported under its template (`{kind}_failed`) and must be listed in `non_fatal` with a reason. Explicit regex `patterns` still work and now run over the source with comments blanked; `DEFAULT_MARKER_PATTERNS` was widened to the same shapes; regression test: tests/test_save_failure_markers.py::test_every_emission_shape_is_found, tests/test_save_failure_markers.py::test_comments_docstrings_and_non_emissions_are_not_markers, tests/test_save_failure_markers.py::test_a_dynamic_marker_must_be_listed_with_a_reason

- **Where:** save_failure_markers.py:31-32
- **Finding:** `rf"`/`F"`, `.extend`, `+=`, dynamic f-strings missed; comments matched
- **Failing input:** repro M1
- **Proposed fix:** AST or tolerant regex; skip comments
- **Verified:** yes-repro

### RS-23 (Med) -- missing root → {} passes

**Disposition:** RESOLVED -- files are enumerated with `_core.iter_files`, so a missing root raises `CorpusError`; `assert_markers_are_fatal` takes `min_markers` (default 1) and fails when fewer markers are found; regression test: tests/test_save_failure_markers.py::test_a_missing_root_raises_and_too_few_markers_fail

- **Where:** save_failure_markers.py:40,65
- **Finding:** missing root → `{}` passes
- **Failing input:** `Path("nope")`
- **Proposed fix:** `min_markers`, fail on missing root
- **Verified:** yes-repro

### RS-24 (Low) -- strict utf-8 read crashes scan

**Disposition:** RESOLVED -- files are read through `_core.scan_python` (interpreter-exact decoding, BOM stripped); an undecodable or unparsable file is reported (the assert lists it, `find_emitted_markers` raises `UnparsedFilesError` unless `allow_unparsed=True`) instead of crashing the scan with a raw `UnicodeDecodeError`; regression test: tests/test_save_failure_markers.py::test_an_undecodable_file_is_reported_not_a_crash

- **Where:** save_failure_markers.py:43
- **Finding:** strict utf-8 read crashes scan
- **Failing input:** cp1251 file
- **Proposed fix:** errors=replace or report
- **Verified:** yes-read

### RS-25 (Low) -- re-run after moving clone keeps stale export; no tests

**Disposition:** RESOLVED -- the profile export is tagged, and a re-run REPLACES the tagged line with the new path (no-op when unchanged); an untagged export the user wrote is left alone with a stderr notice; regression test: tests/test_setup_env.py::TestShellProfile::test_a_rerun_after_moving_the_clone_replaces_the_stale_line, tests/test_setup_env.py::TestShellProfile::test_a_user_written_export_is_left_alone

- **Where:** setup_env.py:106
- **Finding:** re-run after moving clone keeps stale export; no tests
- **Failing input:** move clone, re-run
- **Proposed fix:** replace tagged line
- **Verified:** yes-read

### RS-26 (Low) -- value not XML-escaped / shell-quoted; decode error uncaught

**Disposition:** RESOLVED -- the value is XML-escaped in the LaunchAgent plist, `shlex.quote`d in the profile export, and has backslash and `$` escaped in `environment.d`; the profile is read and written as bytes with surrogateescape (a non-UTF-8 profile round-trips instead of raising), and `main` also catches `UnicodeError`; regression test: tests/test_setup_env.py::TestPlatforms::test_macos_plist_is_valid_xml_with_the_exact_value, tests/test_setup_env.py::TestShellProfile::test_the_value_is_shell_quoted, tests/test_setup_env.py::TestPlatforms::test_linux_environment_d_escapes_dollar_and_backslash, tests/test_setup_env.py::TestShellProfile::test_a_profile_that_is_not_utf8_is_updated_byte_for_byte

- **Where:** setup_env.py:70,109
- **Finding:** value not XML-escaped / shell-quoted; decode error uncaught
- **Failing input:** path with `&`/`"`/`$`
- **Proposed fix:** escape, shlex.quote
- **Verified:** yes-read

### RS-27 (High) -- SECURITY DEFINER after $$ body never seen

**Disposition:** RESOLVED -- the check now lexes each migration into statements (comments dropped, string and dollar-quoted literal contents blanked) and looks for `SECURITY DEFINER` / `SET search_path` in the whole CREATE statement, so options written after the `$$` body are seen and words inside the body are not; regression test: tests/test_sql_function_privileges.py::TestLexing::test_security_definer_after_the_body_is_seen

- **Where:** sql_function_privileges.py:83-88
- **Finding:** `SECURITY DEFINER` after `$$` body never seen
- **Failing input:** `... $$ LANGUAGE plpgsql SECURITY DEFINER;`
- **Proposed fix:** scan options after body
- **Verified:** yes-repro

### RS-28 (High) -- quoted identifiers (pg_dump) / multi-line headers invisible

**Disposition:** RESOLVED -- identifiers accept pg_dump quoting (`"public"."Quoted"`, case kept; unquoted folds to lower case) and the statement-based lexer makes multi-line headers visible; regression test: tests/test_sql_function_privileges.py::TestLexing::test_pg_dump_quoted_identifiers_and_multi_line_headers

- **Where:** sql_function_privileges.py:48
- **Finding:** quoted identifiers (pg_dump) / multi-line headers invisible
- **Failing input:** `"public"."quoted"()`
- **Proposed fix:** accept quotes, multi-line
- **Verified:** yes-repro

### RS-29 (High) -- commented-out REVOKE counts

**Disposition:** RESOLVED -- `--` and nested `/* */` comments and string literals are removed before any REVOKE/GRANT is matched; regression test: tests/test_sql_function_privileges.py::TestLexing::test_a_commented_out_revoke_does_not_count

- **Where:** sql_function_privileges.py:96,129
- **Finding:** commented-out REVOKE counts
- **Failing input:** `-- REVOKE ... FROM PUBLIC;`
- **Proposed fix:** strip comments
- **Verified:** yes-repro

### RS-30 (High) -- schema ignored; DROP+CREATE ordering ignored

**Disposition:** RESOLVED -- functions are keyed by (schema, name), unqualified names resolve through the file's `SET search_path`, and statements are replayed in order: DROP then CREATE restarts from the default PUBLIC grant, CREATE OR REPLACE keeps the ACL, a later GRANT undoes a REVOKE; regression test: tests/test_sql_function_privileges.py::TestReplay::test_a_revoke_in_another_schema_does_not_cover_this_one, tests/test_sql_function_privileges.py::TestReplay::test_drop_and_create_resets_to_the_default_public_grant

- **Where:** sql_function_privileges.py:56,99
- **Finding:** schema ignored; DROP+CREATE ordering ignored
- **Failing input:** `other.shadow` REVOKE covers `private.shadow`
- **Proposed fix:** key (schema,name); ordered replay
- **Verified:** yes-repro/yes-read

### RS-31 (Med) -- search_path checked on every definition, not last (FP); O(n·m) re-reads

**Disposition:** RESOLVED -- `search_path` is judged once, on the replayed final state of each function (last CREATE plus any `ALTER FUNCTION ... SET search_path`), and every file is read once; regression test: tests/test_sql_function_privileges.py::TestReplay::test_search_path_is_judged_on_the_last_definition_only

- **Where:** sql_function_privileges.py:166-179
- **Finding:** search_path checked on every definition, not last (FP); O(n·m) re-reads
- **Failing input:** fixed in 002, still flagged from 001
- **Proposed fix:** last header per name
- **Verified:** yes-repro

### RS-32 (Low) -- REVOKE w/o parens, ON ALL FUNCTIONS IN SCHEMA, default privileges missed (FP)

**Disposition:** RESOLVED -- REVOKE/GRANT without an argument list, several targets in one statement, `ON ALL FUNCTIONS IN SCHEMA` (existing functions only), `ALTER DEFAULT PRIVILEGES ... ON FUNCTIONS` (functions created later) and `GRANT OPTION FOR` (no effect on EXECUTE) are modelled; regression test: tests/test_sql_function_privileges.py::TestRevokeForms::test_revoke_on_all_functions_in_schema, tests/test_sql_function_privileges.py::TestRevokeForms::test_default_privileges_cover_functions_created_later

- **Where:** sql_function_privileges.py:56
- **Finding:** REVOKE w/o parens, `ON ALL FUNCTIONS IN SCHEMA`, default privileges missed (FP)
- **Failing input:** `REVOKE EXECUTE ON ALL FUNCTIONS ...`
- **Proposed fix:** extend regexes
- **Verified:** yes-read

### RS-33 (Med) -- quote-led lines skipped incl

**Disposition:** RESOLVED -- on a file that parses, the check runs on Call nodes and skips only bare-string statement ranges (docstrings) and comments, so a quote-led continuation line of a multi-line `assert` is caught while a string literal or trailing comment holding the text is not; an unparsable file falls back to the old line heuristic; regression test: tests/test_source_text_ban.py::TestCallsNotLines::test_a_multi_line_assert_continuation_is_caught, tests/test_source_text_ban.py::TestCallsNotLines::test_the_pattern_inside_a_string_or_trailing_comment_is_not_a_call

- **Where:** source_text_ban.py:105
- **Finding:** quote-led lines skipped incl. assert continuations
- **Failing input:** multi-line `assert (...)`
- **Proposed fix:** skip only docstring ranges
- **Verified:** yes-repro

### RS-34 (Med) -- non-Python literal on line exempts; getsource as gs, open(__file__).read() missed

**Disposition:** RESOLVED -- the non-Python-literal exemption is checked only on the path being read (the receiver of `.read_text()`, the argument of `open()`/`ast.parse()`), `getsource` is resolved through imports (`getsource as gs`), and `open(__file__).read()` counts as a read. The module stays (consumers call `offending_lines`); source_text_claims remains the AST-based superset; regression test: tests/test_source_text_ban.py::TestCallsNotLines::test_a_non_python_literal_elsewhere_on_the_line_does_not_exempt, tests/test_source_text_ban.py::TestCallsNotLines::test_getsource_under_an_alias_is_caught, tests/test_source_text_ban.py::TestCallsNotLines::test_open_dunder_file_read_is_caught

- **Where:** source_text_ban.py:110
- **Finding:** non-Python literal on line exempts; `getsource as gs`, `open(__file__).read()` missed
- **Failing input:** `"x.json" in Path(__file__).read_text()`
- **Proposed fix:** scope NON_PY to path arg; consider deprecating vs source_text_claims
- **Verified:** yes-repro

### RS-35 (High) -- fixtures, getsource as gs, dis.get_instructions, closures missed

**Disposition:** RESOLVED -- reader and `dis` calls resolve through `_core.ImportAliases` (`getsource as gs`, `from dis import get_instructions`), nested functions inherit the enclosing function's path and source-text names (closures), and a same-file `@pytest.fixture` that returns or yields source taints every test parameter of that name; a local function merely named like an alias is not a reader. Cross-file (conftest) fixtures remain out of reach of a per-file walk; regression test: tests/test_source_text_claims.py::TestResolutionFixturesAndClosures::test_an_aliased_getsource_is_a_claim, tests/test_source_text_claims.py::TestResolutionFixturesAndClosures::test_dis_functions_imported_by_name, tests/test_source_text_claims.py::TestResolutionFixturesAndClosures::test_a_same_file_fixture_that_returns_source_taints_the_test, tests/test_source_text_claims.py::TestResolutionFixturesAndClosures::test_a_closure_sees_the_outer_source_text

- **Where:** source_text_claims.py:144,146,196
- **Finding:** fixtures, `getsource as gs`, `dis.get_instructions`, closures missed
- **Failing input:** repro C1: 4 of 5 missed
- **Proposed fix:** alias resolution, fixture taint, closure taint
- **Verified:** yes-repro

### RS-36 (Med) -- key rel::func::kind: more claims of same kind never new

**Disposition:** RESOLVED -- the baseline is a `_core.Baseline` multiset: each `rel::function::kind` key accepts the number of claims it counted, so extra claims of the same kind are new and fewer are stale; a missing baseline fails and is written only by a refresh (`refresh_requested`, new `request=` kwarg); legacy JSON-list baselines still load; unreadable/unparsable files fail and the floor counts checked files. The test that pinned auto-seeding was re-framed; regression test: tests/test_source_text_claims.py::TestTheRatchet::test_more_claims_of_the_same_key_are_new, tests/test_source_text_claims.py::TestTheRatchet::test_a_missing_baseline_fails_and_only_a_refresh_writes_it

- **Where:** source_text_claims.py:73-75
- **Finding:** key `rel::func::kind`: more claims of same kind never new
- **Failing input:** 1 baselined → 6 pass
- **Proposed fix:** count per key
- **Verified:** yes-read

### RS-37 (Med) -- m = AsyncMock(); f(m), tuple unpack, patch() as s, spec=None missed

**Disposition:** RESOLVED -- each scope is walked in source order tracking names bound to a bare mock: a bare mock reaching an entry point through ANY name (`m = AsyncMock(); claim_lock(m)`), tuple unpacking (`client, session = AsyncMock(), AsyncMock()`), `with patch(...) as session` without a truthy spec/autospec/new, and `spec=None`/`spec_set=None` are flagged; mock classes resolve through imports; unparsable files raise/are reported instead of being skipped (the test pinning the skip was re-framed); regression test: tests/test_spec_bound_doubles.py::TestFindUnboundDoubles::test_a_bare_mock_reaching_the_entry_point_through_any_name, tests/test_spec_bound_doubles.py::TestFindUnboundDoubles::test_tuple_unpacking_patch_as_and_spec_none, tests/test_spec_bound_doubles.py::TestIsBareMock::test_a_falsy_spec_is_bare

- **Where:** spec_bound_doubles.py:81-89
- **Finding:** `m = AsyncMock(); f(m)`, tuple unpack, `patch() as s`, `spec=None` missed
- **Failing input:** repro S1
- **Proposed fix:** track bare-mock names
- **Verified:** yes-repro

### RS-38 (Med) -- startswith w/o boundary (prose FPs); comment-led SQL missed; chained targets

**Disposition:** RESOLVED -- a constant is SQL when, after leading `--`/`/* */` comments and an opening parenthesis, its first WORD is a statement keyword (`"Withdrawal failed"` no longer matches `WITH`, comment-led SQL now does), and chained targets `A = B = "SELECT ..."` define every name; regression test: tests/test_sql_verifier_coverage.py::test_prose_starting_with_a_keyword_prefix_is_not_sql, tests/test_sql_verifier_coverage.py::test_comment_led_and_parenthesised_sql_is_sql, tests/test_sql_verifier_coverage.py::test_chained_targets_define_every_name

- **Where:** sql_verifier_coverage.py:50
- **Finding:** `startswith` w/o boundary (prose FPs); comment-led SQL missed; chained targets
- **Failing input:** `MSG="Withdrawal failed"`
- **Proposed fix:** strip comments, `\b` regex
- **Verified:** yes-repro

### RS-39 (Low) -- pkg.__init__.X key; no dedicated test file

**Disposition:** RESOLVED -- `pkg/__init__.py` constants are keyed `pkg.NAME`; a dedicated tests/test_sql_verifier_coverage.py now exists; regression test: tests/test_sql_verifier_coverage.py::test_a_package_init_constant_is_keyed_by_the_package

- **Where:** sql_verifier_coverage.py:45
- **Finding:** `pkg.__init__.X` key; no dedicated test file
- **Failing input:** `pkg/__init__.py: Q=...`
- **Proposed fix:** strip `.__init__`
- **Verified:** yes-read

### RS-40 (High) -- check() commits every statement: writes persist

**Disposition:** RESOLVED -- `check()` never commits: the connection is rolled back in a `finally` after every statement, passed or failed, and `run_checks` rolls back before closing; regression test: tests/test_sql_verify.py::TestCheck::test_a_passing_write_is_rolled_back_never_committed

- **Where:** sql_verify.py:95
- **Finding:** `check()` commits every statement: writes persist
- **Failing input:** `UPDATE t SET a=1 RETURNING id`
- **Proposed fix:** always rollback
- **Verified:** yes-read

### RS-41 (Med) -- fetchall() on no-result statement → FAIL

**Disposition:** RESOLVED -- rows are fetched only when `cur.description` is set, so a statement with no result set is a pass with 0 rows; regression test: tests/test_sql_verify.py::TestCheck::test_a_statement_without_a_result_set_is_a_pass

- **Where:** sql_verify.py:94
- **Finding:** `fetchall()` on no-result statement → FAIL
- **Failing input:** `INSERT INTO t VALUES (1)`
- **Proposed fix:** fetch only if `cur.description`
- **Verified:** yes-read

### RS-42 (Med) -- psycopg2 with connect() does not close; no connect_timeout

**Disposition:** RESOLVED -- the working connection is opened with `connect_timeout` and wrapped in `contextlib.closing` (psycopg2's `with conn:` does not close); regression test: tests/test_sql_verify.py::TestRunChecks::test_the_working_connection_is_closed_and_rolled_back

- **Where:** sql_verify.py:176
- **Finding:** psycopg2 `with connect()` does not close; no connect_timeout
- **Failing input:** any run
- **Proposed fix:** `contextlib.closing`, timeout
- **Verified:** yes-read

### RS-43 (Med) -- every connect error → SKIPPED, exit 0 with --skip-without-db; +driver DSN not normalised

**Disposition:** RESOLVED -- only an unreachable server (OSError / OperationalError without an auth, role, database or DSN marker) is SKIPPED; a refused configuration exits 1 even with `--skip-without-db`. Any `postgres(ql)+<driver>://` DSN, env or explicit, is normalised; regression test: tests/test_sql_verify.py::TestRunChecks::test_a_refused_configuration_fails_even_with_skip_without_db

- **Where:** sql_verify.py:167-172
- **Finding:** every connect error → SKIPPED, exit 0 with `--skip-without-db`; `+driver` DSN not normalised
- **Failing input:** `postgresql+psycopg2://...`
- **Proposed fix:** skip only network errors
- **Verified:** yes-read

### RS-44 (Low) -- psycopg2 imported before DSN check

**Disposition:** RESOLVED -- psycopg2 is imported after the DSN check, so a checkout without a database or the driver can still skip; regression test: tests/test_sql_verify.py::TestRunChecks::test_no_driver_is_needed_to_skip_without_a_dsn

- **Where:** sql_verify.py:160
- **Finding:** psycopg2 imported before DSN check
- **Failing input:** env without psycopg2
- **Proposed fix:** import later
- **Verified:** yes-read

### RS-45 (Low) -- params={} always passed; % literal behaviour undetermined

**Disposition:** RESOLVED -- without params the statement is executed with no parameter argument (psycopg2 only %-formats when a mapping is passed, so a literal `%` is sent verbatim); the test that pinned `{}` was re-framed; regression test: tests/test_sql_verify.py::TestCheck::test_no_params_executes_without_a_parameter_mapping

- **Where:** sql_verify.py:93
- **Finding:** `params={}` always passed; `%` literal behaviour undetermined
- **Failing input:** `SELECT 'a%'`
- **Proposed fix:** pass None
- **Verified:** no

### RS-46 (Low) -- Python slices flagged; quoted casts missed; comments/docstrings scanned

**Disposition:** RESOLVED -- in `.py` files only string literals are scanned (docstrings excluded, line numbers mapped into multi-line strings), so Python code, slices and comments can never match; other suffixes are scanned as SQL text minus `#` lines and `--` comments; a quoted cast type (`:a::"MyEnum"`) is matched; a missing root and an unparsable file are reported; regression test: tests/test_sqlalchemy_text_binds.py::test_in_python_only_string_literals_are_scanned, tests/test_sqlalchemy_text_binds.py::test_a_quoted_type_is_a_cast, tests/test_sqlalchemy_text_binds.py::test_a_missing_root_and_an_unparsable_file_fail

- **Where:** sqlalchemy_text_binds.py:29,36
- **Finding:** Python slices flagged; quoted casts missed; comments/docstrings scanned
- **Failing input:** `xs[:n::step]`
- **Proposed fix:** scan string literals only
- **Verified:** yes-repro

### RS-47 (High) -- blame failure → {} → all skipped; shallow clone makes gate a no-op

**Disposition:** RESOLVED -- a failed `git blame` is reported per file ("could not be dated") instead of returning `{}`, a line blame returned no date for is reported, a shallow clone (`rev-parse --is-shallow-repository`) or a root outside git fails the gate, and a missing scan directory is reported (the test that pinned the silent skip was re-framed). Only tracked files are enumerated (via `_core.iter_files`), since an untracked file has no age; regression test: tests/test_stale_comment_age.py::TestHistoryProblems::test_a_shallow_clone_fails_instead_of_passing, tests/test_stale_comment_age.py::TestHistoryProblems::test_a_blame_failure_is_reported

- **Where:** stale_comment_age.py:107,162
- **Finding:** blame failure → `{}` → all skipped; shallow clone makes gate a no-op
- **Failing input:** `fetch-depth: 1`
- **Proposed fix:** fail on blame error; detect shallow
- **Verified:** yes-read

### RS-48 (High) -- trailing # TODO never checked

**Disposition:** RESOLVED -- comments are extracted with `tokenize` for Python and with a quote-aware line scanner for the other languages, and the TODO rule runs on the comment text, so a trailing `x = 1 # TODO fix` is checked and a `# TODO` inside a string is not; regression test: tests/test_stale_comment_age.py::TestTrailingAndReferences::test_a_trailing_todo_is_checked

- **Where:** stale_comment_age.py:38
- **Finding:** trailing `# TODO` never checked
- **Failing input:** `x = 1  # TODO fix`
- **Proposed fix:** tokenize comments
- **Verified:** yes-repro

### RS-49 (High) -- issue-ref regex on whole line exempts foo(x), UTF-8

**Disposition:** RESOLVED -- the issue reference must follow the marker directly (`TODO(x)`, `TODO: #12`, `TODO ABC-12`, `TODO - https://...`); `foo(x)` elsewhere in the comment no longer exempts it, and encoding/standard prefixes such as `UTF-8`, `SHA-256`, `RFC-...` are not tracker keys; regression test: tests/test_stale_comment_age.py::TestTrailingAndReferences::test_a_call_elsewhere_in_the_comment_is_not_an_issue_reference

- **Where:** stale_comment_age.py:41,152
- **Finding:** issue-ref regex on whole line exempts `foo(x)`, `UTF-8`
- **Failing input:** `# TODO: call foo(x) later`
- **Proposed fix:** ref only right after TODO
- **Verified:** yes-repro

### RS-50 (Med) -- commented-out code needs ;/,: Python dead calls missed

**Disposition:** RESOLVED -- in `#`-comment languages (py, sh, yaml) a commented-out bare call needs no `;`/`,` terminator; `//` languages keep requiring one, and the prose-block filter still applies; regression test: tests/test_stale_comment_age.py::TestTrailingAndReferences::test_a_python_dead_call_needs_no_terminator

- **Where:** stale_comment_age.py:40
- **Finding:** commented-out code needs `;`/`,`: Python dead calls missed
- **Failing input:** `# foo(bar)`
- **Proposed fix:** no terminator for `#` langs
- **Verified:** yes-repro

### RS-51 (Low) -- SHA-256 repos (64 hex) rejected

**Disposition:** RESOLVED -- the blame header accepts 7..64 hex digits, so SHA-256 repositories are dated; regression test: tests/test_stale_comment_age.py::TestBlameMechanics::test_a_sha256_repository_is_dated

- **Where:** stale_comment_age.py:113
- **Finding:** SHA-256 repos (64 hex) rejected
- **Failing input:** sha256 repo
- **Proposed fix:** `{7,64}`
- **Verified:** yes-repro

### RS-52 (Low) -- one -L per candidate (cmdline limit); locale decoding

**Disposition:** RESOLVED -- candidate lines are merged into ranges and blamed in batches of at most 200 `-L` ranges per call; git output is decoded as UTF-8 (errors replaced) instead of the locale code page; regression test: tests/test_stale_comment_age.py::TestBlameMechanics::test_many_candidates_are_batched_and_all_dated, tests/test_stale_comment_age.py::TestBlameMechanics::test_non_ascii_author_and_text_are_decoded

- **Where:** stale_comment_age.py:103,106
- **Finding:** one `-L` per candidate (cmdline limit); locale decoding
- **Failing input:** 2k TODOs
- **Proposed fix:** batch; utf-8
- **Verified:** yes-read

### RS-53 (High) -- mock.patch and aliases not in _PATCH_NAMES

**Disposition:** RESOLVED -- a patch call is recognised by its import-resolved name (`_core.ImportAliases`): `mock.patch`, `unittest.mock.patch`, `m.patch` via `from unittest import mock as m`, `patch as p`, `mock.patch.object`, `monkeypatch.setattr` all count, while an HTTP client's `.patch()` does not; regression test: tests/test_statement_compilation.py::test_aliased_and_module_spellings_are_in_the_population, tests/test_statement_compilation.py::test_an_unrelated_patch_method_is_not_a_patch_call

- **Where:** statement_compilation.py:33,109
- **Finding:** `mock.patch` and aliases not in `_PATCH_NAMES`
- **Failing input:** `with mock.patch("pkg.mod.insert")`
- **Proposed fix:** suffix match, aliases
- **Verified:** yes-repro

### RS-54 (High) -- routing exemption starts at def line, so stacked @patch flagged

**Disposition:** RESOLVED -- the routing exemption starts at the first decorator's line, so a stacked `@patch` above `@pytest.mark.routing` is covered; regression test: tests/test_statement_compilation.py::test_a_stacked_patch_above_the_routing_mark_is_exempt

- **Where:** statement_compilation.py:89
- **Finding:** routing exemption starts at def line, so stacked `@patch` flagged
- **Failing input:** `@patch` over `@pytest.mark.routing`
- **Proposed fix:** start at min decorator lineno
- **Verified:** yes-repro

### RS-55 (Med) -- new_callable and autospec=False count as autospecced

**Disposition:** RESOLVED -- only `autospec`/`spec`/`spec_set` with a truthy value (or a `create_autospec` replacement) exempt a patch; `new_callable=MagicMock`, `autospec=False` and `spec=None` are flagged; regression test: tests/test_statement_compilation.py::test_only_a_truthy_autospec_keeps_the_signature

- **Where:** statement_compilation.py:34,65
- **Finding:** `new_callable` and `autospec=False` count as autospecced
- **Failing input:** `new_callable=MagicMock`
- **Proposed fix:** require truthy value
- **Verified:** yes-repro

### RS-56 (Low) -- last-name match flags HTTP mocks; module pytestmark ignored

**Disposition:** RESOLVED -- the full target is kept: a method on a CapWords class (`httpx.AsyncClient.delete`, `patch.object(Session, "delete")`) and targets under HTTP/OS modules (`excluded_prefixes`) are not constructors, a new `module_prefixes` kwarg scopes the check, and a module- or class-level `pytestmark` carrying `routing` exempts its tests; regression test: tests/test_statement_compilation.py::test_http_and_method_targets_are_not_constructors, tests/test_statement_compilation.py::test_a_module_or_class_pytestmark_routing_exempts

- **Where:** statement_compilation.py:31,54
- **Finding:** last-name match flags HTTP mocks; module `pytestmark` ignored
- **Failing input:** `patch("httpx.AsyncClient.delete")`
- **Proposed fix:** configurable module prefix
- **Verified:** yes-repro

### RS-57 (Low) -- no floor; relative_to crash

**Disposition:** RESOLVED -- files are scanned with `_core.scan_python` (BOM-safe, relative paths via `relative_posix`, so a file outside root no longer raises); unparsable files are reported and a `min_files` floor (default 1) reports an empty corpus; regression test: tests/test_statement_compilation.py::test_an_empty_corpus_and_an_unparsable_file_are_reported, tests/test_statement_compilation.py::test_a_file_outside_root_and_a_bom_file_are_checked

- **Where:** statement_compilation.py:101-105,107
- **Finding:** no floor; `relative_to` crash
- **Failing input:** `test_files=[]`
- **Proposed fix:** floor, fallback
- **Verified:** yes-read

### RS-58 (Low) -- no tests for BOM, aliases, decorator factories, post-body DEFINER, quoted ids, trailing...

**Disposition:** RESOLVED -- regression tests now exist for each named gap (BOM, aliases, decorator factories, post-body DEFINER, quoted identifiers, trailing TODOs, `mock.patch`), `setup_env` has tests/test_setup_env.py, and the tests that pinned questionable behaviour (auto-seeded baselines, silent skips of unparsable files and missing dirs, `params={}`, commit-per-statement) were re-framed; regression test: tests/test_sql_function_privileges.py::TestLexing::test_security_definer_after_the_body_is_seen, tests/test_sql_function_privileges.py::TestLexing::test_pg_dump_quoted_identifiers_and_multi_line_headers, tests/test_runtime_registry_mutation.py::test_a_decorator_factory_inner_function_is_import_time, tests/test_stale_comment_age.py::TestTrailingAndReferences::test_a_trailing_todo_is_checked, tests/test_statement_compilation.py::test_aliased_and_module_spellings_are_in_the_population, tests/test_setup_env.py::test_the_console_script_entry_point_resolves_to_main

- **Where:** tests
- **Finding:** no tests for BOM, aliases, decorator factories, post-body DEFINER, quoted ids, trailing TODOs, `mock.patch`; `setup_env` untested; some tests pin questionable behaviour
- **Failing input:** —
- **Proposed fix:** one regression test per repro
- **Verified:** yes-read

### TZ-1 (High) -- cat-file -e = object exists, not reachable: staged-only work counts as committed → REMO...

**Disposition:** RESOLVED -- worktree_hygiene judges content by REACHABILITY (`reachable_objects`: `git rev-list --objects --branches --tags --remotes [refs/stash]`, worktree HEADs deliberately not roots), not by `cat-file -e` existence; it also judges the STAGED blob of every index change (`ls-files -s`), so staged-only work and a staged edit whose working file was reverted are unsaved. Proven with real repos in tmp_path; regression test: tests/test_worktree_hygiene.py::TestAuditRegressions::test_staged_only_work_is_unsaved_even_though_its_blob_exists, tests/test_worktree_hygiene.py::TestAuditRegressions::test_staged_content_differing_from_the_working_file_is_judged, tests/test_worktree_hygiene.py::TestAuditRegressions::test_staged_content_already_on_a_ref_is_saved

- **Where:** worktree_hygiene.py:117-119, 141
- **Finding:** `cat-file -e` = object exists, not reachable: staged-only work counts as committed → REMOVABLE
- **Failing input:** `git add new.py` in wt
- **Proposed fix:** reachability check
- **Verified:** yes-repro

### TZ-2 (High) -- ignored files (.env, data) never unsaved

**Disposition:** RESOLVED -- `git status` runs with `--ignored=matching`; an ignored file is judged like an untracked one and an ignored directory (`data/`) is expanded file by file, while SKIP_PARTS caches (`__pycache__`, `.venv`...) are still skipped. regression test: tests/test_worktree_hygiene.py::TestAuditRegressions::test_an_ignored_file_is_unsaved_work, tests/test_worktree_hygiene.py::TestAuditRegressions::test_an_ignored_cache_directory_is_still_skipped

- **Where:** worktree_hygiene.py:130
- **Finding:** ignored files (`.env`, data) never unsaved
- **Failing input:** ignored `.env`
- **Proposed fix:** `--ignored=matching` + filter
- **Verified:** yes-repro

### TZ-3 (High) -- failed git cherry → every branch "spare"

**Disposition:** RESOLVED -- `verify_ref` (`rev-parse --verify --quiet <ref>^{commit}`) runs first in `branches_without_unique_commits` and `worktree_findings` and raises `GitQueryError`; the CLI prints it and exits 2; a branch whose own `git cherry` fails is never called spare. regression test: tests/test_worktree_hygiene.py::TestAuditRegressions::test_a_bad_ref_raises_instead_of_calling_every_branch_spare, tests/test_worktree_hygiene.py::TestAuditRegressions::test_the_cli_reports_a_bad_ref_with_exit_2

- **Where:** worktree_hygiene.py:214, 219
- **Finding:** failed `git cherry` → every branch "spare"
- **Failing input:** `ref="origin/nope"` → `['master']`
- **Proposed fix:** `rev-parse --verify` first
- **Verified:** yes-repro

### TZ-4 (High) -- parse/decode failures dropped; BOM; uncalled_functions loses call sites → FPs

**Disposition:** RESOLVED -- for value_bearing_asserts (uncalled_functions, unread_init_params, unresolved_imports and vacuous_loop_assertions are left to migration): files are parsed through `_core.scan_python`, so a BOM is handled, and an unparsable file is listed by `find_value_bearing_asserts` and fails `assert_no_value_bearing_asserts`. A floor `min_files=1` on parsed files is added. regression test: tests/test_value_bearing_asserts.py::TestAuditRegressions::test_a_bom_file_is_scanned, tests/test_value_bearing_asserts.py::TestAuditRegressions::test_an_unparsable_file_is_listed_and_fails, tests/test_value_bearing_asserts.py::TestAuditRegressions::test_the_file_floor

- **Where:** uncalled_functions.py:80-84; unread_init_params.py:101-104; unresolved_imports.py:49-53, 220-223; vacuous_loop_assertions.py:155-162; value_bearing_asserts.py:67-70
- **Finding:** parse/decode failures dropped; BOM; uncalled_functions loses call sites → FPs
- **Failing input:** BOM assert file → `([], 0)`
- **Proposed fix:** utf-8-sig, fail on unparsed
- **Verified:** yes-repro

### TZ-5 (Med) -- __getattr__ substring anywhere marks module dynamic

**Disposition:** RESOLVED -- a module counts as dynamic only on AST evidence at module scope: a module-level `def __getattr__`, `globals()[...]=`/`vars()[...]=`/`globals().update(...)` outside a function, an import-time `exec`, or `setattr(sys.modules[...], ...)`. A class's `__getattr__` method, or the words in a comment, no longer switch the check off for the whole module. regression test: tests/test_unresolved_imports.py::TestAuditRegressions::test_a_class_getattr_does_not_make_the_module_dynamic, tests/test_unresolved_imports.py::TestAuditRegressions::test_a_module_getattr_or_import_time_globals_write_is_dynamic

- **Where:** unresolved_imports.py:33, 54
- **Finding:** `__getattr__` substring anywhere marks module dynamic
- **Failing input:** class method `__getattr__`
- **Proposed fix:** AST module-level only
- **Verified:** yes-repro

### TZ-6 (Med) -- pkg prefix matches pkg_other

**Disposition:** RESOLVED -- `resolvable_prefixes` match on a dotted boundary (`target == p or target.startswith(p + ".")`), so `pkg` no longer judges `pkg_other`. regression test: tests/test_unresolved_imports.py::TestAuditRegressions::test_a_prefix_is_matched_on_a_dotted_boundary

- **Where:** unresolved_imports.py:187
- **Finding:** `pkg` prefix matches `pkg_other`
- **Failing input:** `from pkg_other import X`
- **Proposed fix:** `== p or startswith(p+".")`
- **Verified:** yes-repro

### TZ-7 (Med) -- type X=, with/for/walrus/match/global bindings missed (FP); walk over-binds nested loca...

**Disposition:** RESOLVED -- `_bound_names` walks the MODULE scope only: statements and compound bodies (`if`/`try`/`with`/`for`/`match`...), never function or class bodies, so a local inside a guarded def is no longer a module name. It also binds `with ... as`, `for` targets, walrus, `except ... as`, match captures, `type X = ...` (3.12+) and names a function declares `global`. regression test: tests/test_unresolved_imports.py::TestAuditRegressions::test_every_module_level_binding_counts, tests/test_unresolved_imports.py::TestAuditRegressions::test_a_type_alias_binds_its_name, tests/test_unresolved_imports.py::TestAuditRegressions::test_a_local_inside_a_guarded_def_is_not_a_module_name

- **Where:** unresolved_imports.py:145-153
- **Finding:** `type X=`, with/for/walrus/match/global bindings missed (FP); walk over-binds nested locals (FN)
- **Failing input:** `type Alias = int`
- **Proposed fix:** handle nodes; stop at defs
- **Verified:** yes-repro

### TZ-8 (Low) -- only missing[0] reported

**Disposition:** RESOLVED -- the problem line names every missing name of the import (`does not define 'A', 'B'`), not only the first. regression test: tests/test_unresolved_imports.py::TestAuditRegressions::test_every_missing_name_is_reported

- **Where:** unresolved_imports.py:201
- **Finding:** only `missing[0]` reported
- **Failing input:** `from pkg.a import A, B`
- **Proposed fix:** report all
- **Verified:** yes-read

### TZ-9 (Low) -- suppress(ImportError), tuple raises, BaseException guards unseen (FP)

**Disposition:** RESOLVED -- guard detection is one shared predicate for `_guarded_import_ids` and `_absence_is_expected`: `except` types are read through tuples and attributes and include `BaseException`, `with (contextlib.)suppress(ImportError | ModuleNotFoundError | Exception | BaseException)` tolerates the failure, and `pytest.raises((ImportError, ...))` asserts it. A `with` of anything else still leaves the import judged. regression test: tests/test_unresolved_imports.py::TestAuditRegressions::test_every_guard_shape_is_recognised, tests/test_unresolved_imports.py::TestAuditRegressions::test_an_unguarded_twin_is_still_reported

- **Where:** unresolved_imports.py:244, 272
- **Finding:** `suppress(ImportError)`, tuple raises, BaseException guards unseen (FP)
- **Failing input:** `with suppress(ImportError): ...`
- **Proposed fix:** accept them
- **Verified:** yes-read

### TZ-10 (Med) -- floor satisfied by assert in another floorless loop with same var

**Disposition:** RESOLVED -- a floor assert must not sit inside a loop that does not also enclose the judged loop (`_enclosing_loops`): an assert inside a sibling floorless loop could run zero times too, so two `for x in ...` loops no longer vouch for each other, while a floor in an enclosing loop still counts. regression test: tests/test_vacuous_loop_assertions.py::TestAuditRegressions::test_an_assert_in_another_floorless_loop_is_not_a_floor, tests/test_vacuous_loop_assertions.py::TestAuditRegressions::test_a_floor_in_an_enclosing_loop_still_counts

- **Where:** vacuous_loop_assertions.py:112, 124
- **Finding:** floor satisfied by assert in another floorless loop with same var
- **Failing input:** two `for x` loops
- **Proposed fix:** exclude asserts in other loops
- **Verified:** yes-repro

### TZ-11 (Med) -- nested function loops reported twice; duplicate keys

**Disposition:** RESOLVED -- each function is walked over its own body only (a nested def/lambda/class is not entered), so a loop in a nested function is reported once, under the function that owns it, and floors are looked up in that function. regression test: tests/test_vacuous_loop_assertions.py::TestAuditRegressions::test_a_nested_function_s_loop_is_reported_once_under_its_owner

- **Where:** vacuous_loop_assertions.py:165-168
- **Finding:** nested function loops reported twice; duplicate keys
- **Failing input:** outer/inner
- **Proposed fix:** don't descend into nested defs
- **Verified:** yes-repro

### TZ-12 (Med) -- continue/pass/raise/pytest.fail/with subtests bodies not assert-only

**Disposition:** RESOLVED -- a loop body counts as verify-only when every statement is a check (`assert`, `raise`, a bare `pytest.fail(...)`) or flow control (`continue`/`pass`/`break`, string statements), possibly under `if`/`else` or inside a `with` block (`subtests.test(...)`), and at least one check is present, so a body with no check at all is still not the shape. regression test: tests/test_vacuous_loop_assertions.py::TestAuditRegressions::test_flow_control_raise_fail_and_subtests_bodies_are_assert_only, tests/test_vacuous_loop_assertions.py::TestAuditRegressions::test_a_body_without_any_check_is_still_not_the_shape

- **Where:** vacuous_loop_assertions.py:89-101
- **Finding:** `continue`/`pass`/`raise`/`pytest.fail`/`with subtests` bodies not assert-only
- **Failing input:** `if not i: continue; assert i>0`
- **Proposed fix:** treat as assert-only
- **Verified:** yes-repro

### TZ-13 (Low) -- [*m] treated non-empty

**Disposition:** RESOLVED -- a literal iterable is known non-empty only when it has an element that is not `*`-unpacked; `[*m]` is as empty as `m`. regression test: tests/test_vacuous_loop_assertions.py::TestAuditRegressions::test_an_unpacked_literal_is_not_a_non_empty_one

- **Where:** vacuous_loop_assertions.py:146
- **Finding:** `[*m]` treated non-empty
- **Failing input:** `for q in [*m]`
- **Proposed fix:** reject Starred
- **Verified:** yes-repro

### TZ-14 (Low) -- cwd-relative keys; crash outside root

**Disposition:** RESOLVED -- files are resolved before keying and keyed by `_core.relative_posix` against the resolved `repo_root`, so a key is the same from any working directory and a file outside the root gets its absolute POSIX path instead of raising. Parsing goes through `_core.scan_python` (BOM handled); an unparsable test file raises `UnparsedFilesError` (opt out with `allow_unparsed=True`), which also fails `assert_no_new_floorless_loop`. regression test: tests/test_vacuous_loop_assertions.py::TestAuditRegressions::test_keys_do_not_depend_on_the_working_directory, tests/test_vacuous_loop_assertions.py::TestAuditRegressions::test_bom_and_unparsable_files

- **Where:** vacuous_loop_assertions.py:163
- **Finding:** cwd-relative keys; crash outside root
- **Failing input:** relative files from other cwd
- **Proposed fix:** resolve + fallback
- **Verified:** yes-read

### TZ-15 (Med) -- identical asserts collapse into one key; 90-char truncation collisions

**Disposition:** RESOLVED -- the baseline is now the multiset `_core.Baseline`, so two identical asserts need two entries. Keys hold the full expression with no 90-char truncation. Old baselines keep working: JSON lists are read as multisets, and a pre-fix truncated key still matches when only that key is present. regression test: tests/test_value_bearing_asserts.py::TestAuditRegressions::test_duplicate_asserts_are_counted_not_collapsed, tests/test_value_bearing_asserts.py::TestAuditRegressions::test_long_expressions_are_keyed_in_full_and_legacy_keys_still_match, tests/test_core_baseline.py::TestMultiset::test_a_duplicate_finding_is_not_absorbed_by_one_entry

- **Where:** value_bearing_asserts.py:80-83, 104
- **Finding:** identical asserts collapse into one key; 90-char truncation collisions
- **Failing input:** two `assert n > 0`
- **Proposed fix:** multiset ratchet, full key
- **Verified:** yes-repro

### TZ-16 (Low) -- bare Name/Attribute/Subscript always narrowing

**Disposition:** RESOLVED -- opt-in `strict=True` on `is_narrowing_assert` / `find_value_bearing_asserts` / `assert_no_value_bearing_asserts` treats bare `Name`/`Attribute`/`Subscript` truthiness as a value check. The default is unchanged, so consumers see no new failures. regression test: tests/test_value_bearing_asserts.py::TestAuditRegressions::test_strict_mode_treats_bare_truthiness_as_a_value_check

- **Where:** value_bearing_asserts.py:43
- **Finding:** bare Name/Attribute/Subscript always narrowing
- **Failing input:** `assert self.enabled`
- **Proposed fix:** optional strict mode
- **Verified:** yes-read

### TZ-17 (Med) -- missing baseline seeds and skips

**Disposition:** RESOLVED -- for value_bearing_asserts (uncalled_functions is left to migration): a missing baseline FAILS and names `--refresh-value-asserts-baseline` / `PY_CI_SHARED_REFRESH=value-asserts`. It is written only on refresh (`refresh=True`, the pytest option via `request=`, the env var, or argv), and never from a walk that failed its floor or had unparsed files. The old test that required seed-and-skip was re-framed. regression test: tests/test_value_bearing_asserts.py::TestTheRatchet::test_a_missing_baseline_fails_and_is_written_only_on_refresh, tests/test_value_bearing_asserts.py::TestTheRatchet::test_refresh_via_env_var_as_under_xdist, tests/test_core_baseline.py::TestMissingAndRefresh::test_a_missing_baseline_fails_naming_the_refresh_command

- **Where:** uncalled_functions.py:192; value_bearing_asserts.py:105
- **Finding:** missing baseline seeds and skips
- **Failing input:** delete baseline
- **Proposed fix:** seed on refresh only
- **Verified:** yes-read

### TZ-18 (Med) -- self-recursion / same-name locals count as calls

**Disposition:** RESOLVED -- `_referenced_names` is scope-aware: a function's load of its OWN name (self-recursion) and a load of a name the function or an enclosing one binds locally (parameter, assignment, loop/with/except target, local import or def, unless declared `global`/`nonlocal`) no longer count as calls to the module function. Decorators, defaults and annotations are still read in the enclosing scope. regression test: tests/test_uncalled_functions.py::TestAuditRegressions::test_self_recursion_is_not_a_call, tests/test_uncalled_functions.py::TestAuditRegressions::test_a_same_name_local_is_not_a_call

- **Where:** uncalled_functions.py:122-124
- **Finding:** self-recursion / same-name locals count as calls
- **Failing input:** `def f(n): return f(n-1)`
- **Proposed fix:** exclude own body
- **Verified:** yes-repro

### TZ-19 (Low) -- defs under module if/try not judged; no file floor

**Disposition:** RESOLVED -- functions defined under a module-level `if`/`else`, `try`/`except`/`finally`, `with` or loop are judged as module functions (never those inside classes or other functions), and `assert_no_new_uncalled_function` takes `min_files=1`, which counts PARSED files (`_core.scan_python`). regression test: tests/test_uncalled_functions.py::TestAuditRegressions::test_defs_under_module_if_and_try_are_judged, tests/test_uncalled_functions.py::TestAuditRegressions::test_the_file_floor

- **Where:** uncalled_functions.py:98, 150
- **Finding:** defs under module if/try not judged; no file floor
- **Failing input:** try/except-defined f
- **Proposed fix:** recurse; `min_files`
- **Verified:** yes-read

### TZ-20 (Med) -- self.x: T = p and chained assigns treated as used

**Disposition:** RESOLVED -- `_store_targets` treats `self.x: T = p` (AnnAssign) and chained `self.a = self.b = p` as plain stores: every stored attribute is recorded, and a parameter counts as used only by loads beyond those stores. regression test: tests/test_unread_init_params.py::TestAuditRegressions::test_an_annotated_store_is_a_plain_store, tests/test_unread_init_params.py::TestAuditRegressions::test_a_chained_store_is_a_plain_store_into_both_attributes

- **Where:** unread_init_params.py:70-74
- **Finding:** `self.x: T = p` and chained assigns treated as used
- **Failing input:** `self.p: int = p`
- **Proposed fix:** handle AnnAssign, multi-target
- **Verified:** yes-repro

### TZ-21 (Low) -- any string constant counts as read (__slots__)

**Disposition:** RESOLVED -- string constants that declare names rather than read them (`__slots__` and `__all__` entries, module/class/function docstrings) no longer count as reads; any other string literal (a `get_params` key list, a config dict) still does. regression test: tests/test_unread_init_params.py::TestAuditRegressions::test_a_slots_or_all_entry_or_a_docstring_is_not_a_read

- **Where:** unread_init_params.py:92
- **Finding:** any string constant counts as read (`__slots__`)
- **Failing input:** `__slots__ = ("alpha",)`
- **Proposed fix:** exclude slots/docstrings
- **Verified:** yes-read

### TZ-22 (Low) -- relative_to crash

**Disposition:** RESOLVED -- paths go through `_core.relative_posix`, so a file outside `repo_root` (a site-packages copy) is reported by its absolute POSIX path instead of raising `ValueError`. Files are parsed via `_core.scan_python` (BOM handled); an unparsable file raises `UnparsedFilesError` from `find_unread_init_params` (opt out with `allow_unparsed=True`) and fails `assert_no_unread_init_params`, whose `min_files` now counts parsed files. regression test: tests/test_unread_init_params.py::TestAuditRegressions::test_a_file_outside_the_repo_root_does_not_raise, tests/test_unread_init_params.py::TestAuditRegressions::test_bom_and_unparsable_files

- **Where:** unread_init_params.py:105
- **Finding:** `relative_to` crash
- **Failing input:** site-packages path
- **Proposed fix:** fallback
- **Verified:** yes-read

### TZ-23 (Med) -- test.describe.skip, test.fixme, xit, xdescribe missed

**Disposition:** RESOLVED -- the disabled-test pattern now covers `test.describe.skip`, `test.fixme`, `test.describe.fixme` (with `.serial`/`.parallel`), `xit`, `xdescribe`, `xtest` and `xcontext`, still requiring a title as first argument so a conditional platform guard is not flagged. regression test: tests/test_test_partition_reachability.py::TestAuditRegressions::test_every_disabled_test_spelling_is_found, tests/test_test_partition_reachability.py::TestAuditRegressions::test_a_conditional_fixme_is_still_a_guard

- **Where:** test_partition_reachability.py:56
- **Finding:** `test.describe.skip`, `test.fixme`, `xit`, `xdescribe` missed
- **Failing input:** `test.describe.skip(...)`
- **Proposed fix:** widen regex
- **Verified:** yes-repro

### TZ-24 (Med) -- --project="Mobile Chrome" → "Mobile"

**Disposition:** RESOLVED -- `--project` values are captured double-quoted, single-quoted or bare, with `=` or a space, so `--project="Mobile Chrome"` is the whole name. regression test: tests/test_test_partition_reachability.py::TestAuditRegressions::test_a_quoted_project_name_with_a_space_is_read_whole

- **Where:** test_partition_reachability.py:50
- **Finding:** `--project="Mobile Chrome"` → "Mobile"
- **Failing input:** quoted project
- **Proposed fix:** quoted capture
- **Verified:** yes-repro

### TZ-25 (Med) -- any playwright test line without --project disables check

**Disposition:** RESOLVED -- runner text is read as logical lines: shell `\` continuations are joined and `#` comment lines dropped, so only a real `playwright test` invocation with no `--project` of its own disables the check. regression test: tests/test_test_partition_reachability.py::TestAuditRegressions::test_a_continuation_or_a_comment_does_not_disable_the_check

- **Where:** test_partition_reachability.py:52
- **Finding:** any `playwright test` line without `--project` disables check
- **Failing input:** `\` continuation
- **Proposed fix:** join continuations, strip comments
- **Verified:** yes-read

### TZ-26 (Med) -- absolute parts: any ancestor tests skips all

**Disposition:** RESOLVED -- `skip_dir_names` are matched against the parts BELOW each script dir (`path.relative_to(d).parts[:-1]`), so a checkout or script dir living under a directory named `tests` is no longer skipped whole. regression test: tests/test_test_partition_reachability.py::TestAuditRegressions::test_skip_dir_names_are_matched_below_the_script_dir_only

- **Where:** test_partition_reachability.py:130
- **Finding:** absolute `parts`: any ancestor `tests` skips all
- **Failing input:** `REPO/"tests"/"e2e"` → `[]`
- **Proposed fix:** relative parts
- **Verified:** yes-repro

### TZ-27 (Med) -- missing runner/tag/config/spec paths → clean

**Disposition:** RESOLVED -- `assert_partitions_reachable` fails, listing them, when any given runner, tag file, config, index, script dir or spec dir does not exist; the `find_*` functions raise `FileNotFoundError` instead of returning an empty, passing result. Files are read via `_core.read_source`. regression test: tests/test_test_partition_reachability.py::TestAuditRegressions::test_missing_paths_fail_instead_of_passing_clean

- **Where:** test_partition_reachability.py:60-71, 77, 105
- **Finding:** missing runner/tag/config/spec paths → clean
- **Failing input:** typo'd tag file
- **Proposed fix:** fail on missing paths
- **Verified:** yes-read

### TZ-28 (Low) -- quoted/short tag flags, 4-space tags unparsed (fail-open)

**Disposition:** RESOLVED -- `dart_test.yaml` is parsed with `yaml.safe_load` (any indentation, map or list); tag flags accept quoted values, `=`/space, boolean selector lists (`'a || b'`), and dart's `-t`/`-x` short flags, the latter only on `dart test`/`flutter test` lines so `docker build -t` is not read as a tag. regression test: tests/test_test_partition_reachability.py::TestAuditRegressions::test_quoted_and_short_tag_flags_and_deeper_indentation

- **Where:** test_partition_reachability.py:48-49, 43
- **Finding:** quoted/short tag flags, 4-space tags unparsed (fail-open)
- **Failing input:** `--exclude-tags "benchmark"`
- **Proposed fix:** widen
- **Verified:** yes-read

### TZ-29 (Low) -- substring script match

**Disposition:** RESOLVED -- a script counts as referenced only when its file name appears as a whole name (no word, `.` or `-` character on either side), so `prerun.py` no longer references `run.py`. regression test: tests/test_test_partition_reachability.py::TestAuditRegressions::test_a_script_name_inside_a_longer_name_is_not_a_reference

- **Where:** test_partition_reachability.py:135
- **Finding:** substring script match
- **Failing input:** `prerun.py` covers `run.py`
- **Proposed fix:** word boundary
- **Verified:** yes-read

### TZ-30 (Low) -- empty/stale allowlist reasons accepted

**Disposition:** RESOLVED -- an `allowed` entry with an empty reason fails, and so does one that excused nothing on this run (stale). `test_assert_passes` carried an allowance for a tag that was never unreachable; its runner now excludes that tag so the allowance is doing work. regression test: tests/test_test_partition_reachability.py::TestAuditRegressions::test_empty_and_stale_allowlist_reasons_fail, tests/test_test_partition_reachability.py::TestAssert::test_assert_passes

- **Where:** test_partition_reachability.py:169-189
- **Finding:** empty/stale allowlist reasons accepted
- **Failing input:** `{"bench": ""}`
- **Proposed fix:** reject
- **Verified:** yes-read

### TZ-31 (Med) -- guard counts headers in fences; rejects **Findings**

**Disposition:** RESOLVED -- the `min_summaries` floor counts summary tables with the check's own header rule (`summary_table_count`) over the tracker with fenced blocks blanked, so a fenced example header no longer satisfies it and a `| **Findings** |` header is no longer rejected. regression test: tests/test_tracker_summary_parity.py::TestAuditRegressions::test_a_fenced_example_header_does_not_satisfy_the_floor_and_bold_headers_count

- **Where:** tracker_summary_parity.py:162
- **Finding:** guard counts headers in fences; rejects `**Findings**`
- **Failing input:** fenced example header
- **Proposed fix:** count after stripping fences
- **Verified:** yes-read

### TZ-32 (Low) -- non-backticked rows skipped; empty cells crash

**Disposition:** RESOLVED -- a summary row naming its round file without backticks is matched and checked; a row naming no round file, an empty row and a row with fewer cells than the header needs are each reported as a problem instead of being skipped or raising `IndexError`. regression test: tests/test_tracker_summary_parity.py::TestAuditRegressions::test_a_row_without_backticks_is_checked_and_a_short_row_is_reported_not_a_crash

- **Where:** tracker_summary_parity.py:100-102, 92
- **Finding:** non-backticked rows skipped; empty cells crash
- **Failing input:** `\| round1.md \| 5 \|`
- **Proposed fix:** flag; guard index
- **Verified:** yes-read

### TZ-33 (Low) -- status column assumed first; other headers counted as findings

**Disposition:** RESOLVED -- inside a `### <file>` section, a row directly followed by a separator row is the table header whatever its words: it is skipped, and it names the status column (`Status`/`Disposition`/`State`, else the first). Every other row counts as a finding. regression test: tests/test_tracker_summary_parity.py::TestAuditRegressions::test_the_status_column_is_found_by_header_and_any_header_row_is_skipped

- **Where:** tracker_summary_parity.py:50
- **Finding:** status column assumed first; other headers counted as findings
- **Failing input:** `\| State \| ID \|`
- **Proposed fix:** positional header skip
- **Verified:** yes-read

### TZ-34 (Med) -- ./scripts vs scripts string compare; ruff.toml not read (fail-open)

**Disposition:** RESOLVED -- scan paths, `not_code` names and exclude entries are normalised (`posixpath.normpath`, `\` to `/`, `./scripts/` is `scripts`), a scan path also covers directories below it, and the exclude list is read from every ruff config ruff itself honours: `[tool.ruff]` in pyproject plus the top level of `ruff.toml` and `.ruff.toml`. regression test: tests/test_timezone_honest.py::TestAuditRegressions::test_dot_slash_and_trailing_slash_spellings_are_the_same_directory, tests/test_timezone_honest.py::TestAuditRegressions::test_a_ruff_toml_exclude_is_read

- **Where:** timezone_honest.py:136
- **Finding:** `./scripts` vs `scripts` string compare; ruff.toml not read (fail-open)
- **Failing input:** `scan_paths=("./scripts",)`
- **Proposed fix:** normalise; read ruff.toml
- **Verified:** yes-read

### TZ-35 (Low) -- only first component vs test dirs

**Disposition:** RESOLVED -- a finding is out of scope when ANY directory component of its path is a test dir name (`src/pkg/tests/test_a.py`), not only the first; the file name itself is never matched. regression test: tests/test_timezone_honest.py::TestAuditRegressions::test_a_nested_tests_directory_is_out_of_scope_but_a_file_named_tests_is_not

- **Where:** timezone_honest.py:116
- **Finding:** only first component vs test dirs
- **Failing input:** `src/pkg/tests/test_a.py`
- **Proposed fix:** any component
- **Verified:** yes-read

### TZ-36 (Low) -- locale decoding; syntax-error files silently skipped (hypothesis)

**Disposition:** RESOLVED -- the hypothesis was verified with ruff 0.16.1: `--select DTZ` still prints a syntax-error file as `bad.py:2:7: invalid-syntax: ...`, which the old parser dropped while it returned the other files' findings. Any located diagnostic that is not a DTZ finding now raises `RuntimeError` ("could not check"), and ruff's output is decoded as UTF-8 (`errors="replace"`) instead of the locale code page. regression test: tests/test_timezone_honest.py::TestAuditRegressions::test_a_syntax_error_file_is_an_error_not_a_clean_skip, tests/test_timezone_honest.py::TestAuditRegressions::test_a_non_ascii_path_is_decoded_as_utf8

- **Where:** timezone_honest.py:93-98
- **Finding:** locale decoding; syntax-error files silently skipped (hypothesis)
- **Failing input:** Cyrillic filename
- **Proposed fix:** utf-8; fail on non-DTZ lines
- **Verified:** no

### TZ-37 (Low) -- missing/unmatched source dropped silently

**Disposition:** RESOLVED -- `version_sources(..., problems=[...])` records every source the caller named that is missing, unreadable, or states no version (including a dynamic or absent `[project].version` when `pyproject=True`), and `assert_versions_agree` fails listing them before comparing, so a typo'd path no longer just shortens the comparison. Files are read as `utf-8-sig`. The one-source test now expects this clearer message, and the floor is still tested on its own. regression test: tests/test_version_consistency.py::TestAuditRegressions::test_a_missing_or_unmatched_requested_source_is_reported, tests/test_version_consistency.py::TestAuditRegressions::test_a_missing_pyproject_version_is_reported_when_pyproject_is_requested, tests/test_version_consistency.py::test_a_comparison_of_one_is_refused

- **Where:** version_consistency.py:44
- **Finding:** missing/unmatched source dropped silently
- **Failing input:** typo file + pyproject
- **Proposed fix:** fail per requested source
- **Verified:** yes-read

### TZ-38 (Med) -- wasted subprocess; any nonzero = "not ancestor"; missing git crash

**Disposition:** RESOLVED -- one `git merge-base --is-ancestor` run (the duplicate call is gone): exit 0 is an ancestor, exit 1 is not, any other exit is reported as "cannot determine" (with a `fetch-depth: 0` hint in a shallow clone), and a missing git binary or a failed `git tag` becomes a reported problem (`VersionCheckError`) instead of a crash or a false "no matching tag". regression test: tests/test_version_tag_currency.py::TestAuditRegressions::test_a_git_failure_is_cannot_determine_not_not_an_ancestor, tests/test_version_tag_currency.py::TestAuditRegressions::test_missing_git_is_reported_not_raised

- **Where:** version_tag_currency.py:90-95
- **Finding:** wasted subprocess; any nonzero = "not ancestor"; missing git crash
- **Failing input:** shallow clone
- **Proposed fix:** distinguish rc 1; "cannot determine"
- **Verified:** yes-read

### TZ-39 (Med) -- unanchored DOTALL regex: core: inside app_core:, path deps steal refs, quoted refs skipped

**Disposition:** RESOLVED -- `pinned_ref` parses the consumer pubspec with `yaml.safe_load` and reads `git.ref` of exactly the named package under `dependencies`/`dev_dependencies`/`dependency_overrides` (an override to a path or hosted source clears it), so `core:` no longer matches inside `app_core:`, a path dependency cannot take another package's ref, and quoted refs are read. regression test: tests/test_version_tag_currency.py::TestAuditRegressions::test_the_pin_is_read_from_the_named_dependency_only

- **Where:** version_tag_currency.py:113
- **Finding:** unanchored DOTALL regex: `core:` inside `app_core:`, path deps steal refs, quoted refs skipped
- **Failing input:** pubspec repro
- **Proposed fix:** parse YAML
- **Verified:** yes-repro

### TZ-40 (Low) -- first version = anywhere; suffixes truncated

**Disposition:** RESOLVED -- `declared_version` parses the manifest: TOML reads `[project].version` (or `[tool.poetry].version`), not the first `version =` of any table; YAML reads the top-level `version`. A prerelease suffix is kept and only build metadata (`+45`) is dropped. Prerelease tags (`v0.6.0-beta.1`) are listed and sort before their release. regression test: tests/test_version_tag_currency.py::TestAuditRegressions::test_the_project_version_is_read_not_the_first_version_key, tests/test_version_tag_currency.py::TestAuditRegressions::test_a_prerelease_needs_its_own_tag_and_sorts_before_its_release

- **Where:** version_tag_currency.py:36-37
- **Finding:** first `version =` anywhere; suffixes truncated
- **Failing input:** `[tool.foo] version` first
- **Proposed fix:** tomllib
- **Verified:** yes-read

### TZ-41 (Med) -- trailing --src-path IndexError blocks commit; = form unparsed

**Disposition:** RESOLVED -- vulture_warn is now a `main(argv)` behind `if __name__ == "__main__"` (it no longer parses `sys.argv` at import) with an argparse pre-parser: `--src-path X` and `--src-path=X` (and `--whitelist`) both work, and a trailing flag is a usage error (exit 2, naming the flag) instead of an IndexError traceback. regression test: tests/test_vulture_warn.py::TestAuditRegressions::test_both_flag_spellings_are_parsed, tests/test_vulture_warn.py::TestAuditRegressions::test_a_trailing_flag_is_a_usage_error_not_an_index_error, tests/test_vulture_warn.py::TestAuditRegressions::test_importing_the_module_runs_nothing

- **Where:** vulture_warn.py:32, 43
- **Finding:** trailing `--src-path` IndexError blocks commit; `=` form unparsed
- **Failing input:** `--src-path` last
- **Proposed fix:** argparse
- **Verified:** yes-read

### TZ-42 (Med) -- env path \ not normalised; substring match

**Disposition:** RESOLVED -- `in_scope` compares normalised POSIX paths on a component boundary (`src\mlframe` from the env matches `src/mlframe/x.py`; `src/mlframe` does not match `src/mlframe_extra/x.py` or `tests/src/mlframe/x.py`). regression test: tests/test_vulture_warn.py::TestAuditRegressions::test_scope_is_a_normalised_path_prefix_not_a_substring

- **Where:** vulture_warn.py:48
- **Finding:** env path `\` not normalised; substring match
- **Failing input:** `src\mlframe`
- **Proposed fix:** normalise, path prefix
- **Verified:** yes-read

### TZ-43 (Low) -- any nonzero = findings (missing vulture)

**Disposition:** RESOLVED -- only vulture's exit 3 (dead code found) prints the findings warning; any other nonzero exit (1 invalid input or missing module, 2 bad arguments) prints that the files were NOT checked. The hook still exits 0 (warn-only). regression test: tests/test_vulture_warn.py::TestAuditRegressions::test_exit_3_is_findings_and_other_nonzero_is_a_scan_that_did_not_happen

- **Where:** vulture_warn.py:56
- **Finding:** any nonzero = findings (missing vulture)
- **Failing input:** vulture absent
- **Proposed fix:** rc 3 only
- **Verified:** yes-read

### TZ-44 (Low) -- ~4 processes per file

**Disposition:** RESOLVED -- blob ids are computed in-process (`hashlib`, `blob <len>\0` header, sha1 or sha256 per `rev-parse --show-object-format`) and reachability is one set built once per report, so no git process is spawned per file. regression test: tests/test_worktree_hygiene.py::TestAuditRegressions::test_blob_ids_match_git_hash_object, tests/test_worktree_hygiene.py::TestAuditRegressions::test_no_git_process_is_spawned_per_file

- **Where:** worktree_hygiene.py:140, 162
- **Finding:** ~4 processes per file
- **Failing input:** 10k-file orphan
- **Proposed fix:** `--stdin-paths`, `--batch-check`
- **Verified:** yes-read

### TZ-45 (Low) -- rename's old path token cut by [3:]

**Disposition:** RESOLVED -- `_status_entries` parses `-z` output as records and consumes the source-path token that follows an R/C entry, instead of reading it as an entry cut by `[3:]`. regression test: tests/test_worktree_hygiene.py::TestAuditRegressions::test_a_rename_source_is_not_read_as_an_entry

- **Where:** worktree_hygiene.py:133
- **Finding:** rename's old path token cut by `[3:]`
- **Failing input:** `git mv a.py bb.py`
- **Proposed fix:** skip token after R/C
- **Verified:** yes-read

### TZ-46 (Low) -- no tests for vulture_warn/tool_versions; no BOM/non-UTF8/missing-baseline cases

**Disposition:** RESOLVED -- new test files tests/test_vulture_warn.py and tests/test_tool_versions.py (version shape, the shared constant used by `TOOLS`, the repo's own pin read through TOML), plus new ones for modules that had none (tests/test_meta_private_imports.py, tests/test_pydantic_field_bounds.py). BOM, non-UTF-8/unparsable and missing-baseline cases were added to every migrated module this agent owns (marker_runner_coverage, meta_private_imports, module_reload_safety, optional_truthiness, phantom_code_references, phantom_markdown_links, prompt_field_parity, prose_numeric_claims, pytest_markers, timezone_honest, uncalled_functions, unread_init_params, unresolved_imports, vacuous_loop_assertions, version_consistency). regression test: tests/test_vulture_warn.py::TestAuditRegressions::test_exit_3_is_findings_and_other_nonzero_is_a_scan_that_did_not_happen, tests/test_tool_versions.py::test_ruff_version_is_an_exact_release, tests/test_uncalled_functions.py::TestTheRatchet::test_a_missing_baseline_fails_and_is_written_only_on_refresh, tests/test_unresolved_imports.py::TestAuditRegressions::test_bom_and_unparsable_files

- **Where:** tests
- **Finding:** no tests for vulture_warn/tool_versions; no BOM/non-UTF8/missing-baseline cases
- **Failing input:** —
- **Proposed fix:** regression test per finding
- **Verified:** yes-read

### MT-1 (High) -- NEEDS-JUSTIFICATION: never rejected on the next run

**Disposition:** RESOLVED -- `baseline_ratchet.Baseline.enforce` (used by mutation_teeth) and `_core.Baseline.enforce` both reject any entry whose note starts with `NEEDS-JUSTIFICATION`. A refresh followed by a normal run now fails until a human writes the reason. regression test: tests/test_core_baseline.py::TestUnjustified::test_baseline_ratchet_rejects_the_marker_too, tests/test_core_baseline.py::TestUnjustified::test_needs_justification_entries_fail_a_normal_run, tests/test_value_bearing_asserts.py::TestAuditRegressions::test_needs_justification_entries_are_rejected

- **Where:** mutation_teeth.py:1993-2035; baseline_ratchet.py:98-127
- **Finding:** `NEEDS-JUSTIFICATION:` never rejected on the next run
- **Failing input:** refresh then run → passes
- **Proposed fix:** fail on `_UNJUSTIFIED` notes (ideally in `Baseline.enforce`)
- **Verified:** yes-repro

### MT-2 (High) -- zero mutants (unparsable file, empty scope) passes

**Disposition:** RESOLVED -- `generate_mutants` parses the target once and raises `MutationHarnessError` for a file that does not parse; `find_surviving_mutants` raises when the scope yields no mutant to run, unless the new `allow_empty=True` is passed. The refusal comes before any pytest run is paid for; regression test: test_mutation_teeth_regressions.py::TestZeroMutantsIsNotAPass::test_an_unparsable_target_raises, ::test_an_empty_scope_raises_unless_allowed, ::test_a_scope_with_mutants_runs_normally

- **Where:** mutation_teeth.py:2006-2035
- **Finding:** zero mutants (unparsable file, empty scope) passes
- **Failing input:** `def g(:` → 0 mutants, green
- **Proposed fix:** raise unless `allow_empty`
- **Verified:** yes-repro

### MT-3 (High) -- BOM: AST operators return []

**Disposition:** RESOLVED -- targets are read through `_core.read_source` (interpreter decoding, BOM stripped) by `_mutation_model.read_target`, which records the BOM and encoding; every mutant, restore and revert is written with `write_target`, which re-adds the BOM and re-encodes; regression test: test_mutation_teeth_regressions.py::TestBomAndEncodings::test_a_bom_file_gets_the_same_mutants, ::test_a_mutant_is_written_back_with_its_bom, ::test_a_file_without_a_bom_is_written_without_one

- **Where:** mutation_teeth.py:848 (1513, 2067)
- **Finding:** BOM: AST operators return `[]`
- **Failing input:** 4 mutants vs 1 with BOM
- **Proposed fix:** utf-8-sig, re-add BOM on write
- **Verified:** yes-repro

### MT-4 (Med) -- generator lines consumed by fingerprint; whole file swept, cached under narrow key

**Disposition:** RESOLVED -- `_normalise_lines` materialises `lines` once at the top of `find_surviving_mutants`; the same list feeds the fingerprint scope and `generate_mutants`, so a generator scopes the sweep and caches under its own key; regression test: test_mutation_teeth_regressions.py::TestTheLineScope::test_a_generator_is_read_once_and_scopes_the_sweep, ::test_a_generator_run_is_cached_under_its_own_scope

- **Where:** mutation_teeth.py:1670 → 1760, 863
- **Finding:** generator `lines` consumed by fingerprint; whole file swept, cached under narrow key
- **Failing input:** generator range → 3 mutants not 1
- **Proposed fix:** `list(lines)` once
- **Verified:** yes-repro

### MT-5 (Med) -- lines=[] = whole file, same cache key as full run

**Disposition:** RESOLVED -- `None` means the whole file and `[]` selects nothing, in both `generate_mutants` and the fingerprint scope (`_scope_of` keeps them distinct); an empty scope then hits the MT-2 refusal unless `allow_empty`; regression test: test_mutation_teeth_regressions.py::TestTheLineScope::test_an_empty_scope_and_the_whole_file_key_differently, ::test_an_empty_scope_generates_nothing

- **Where:** mutation_teeth.py:860-863, 879; 1670
- **Finding:** `lines=[]` = whole file, same cache key as full run
- **Failing input:** `lines=[]`
- **Proposed fix:** None vs empty distinct
- **Verified:** yes-repro

### MT-6 (Med) -- twin verdicts not fanned out; accepted twins read stale

**Disposition:** RESOLVED -- only one representative per byte-identical mutated file is run, and `_merge_verdicts` fans its verdict (survivor, gap, inconclusive, kill, crash, killer category) to every twin, so each twin's baseline key is reported and counted; regression test: test_mutation_teeth_regressions.py::TestTwinsShareOneVerdict::test_a_surviving_twin_reports_both_keys, ::test_a_killed_twin_kills_both

- **Where:** mutation_teeth.py:1767-1771, 1815-1830
- **Finding:** twin verdicts not fanned out; accepted twins read stale
- **Failing input:** byte-identical mutants
- **Proposed fix:** copy verdict to all twins
- **Verified:** yes-read

### MT-7 (High) -- worker reply desync after stray fd-1 output

**Disposition:** RESOLVED -- the worker moves the protocol to a private dup of fd 1 and points fd 1 at the null device (`_claim_protocol_channel`), so `os.write(1, ...)` and child processes cannot reach the channel; every request carries an `id` and `_WarmRunner` skips any line that is not the reply to its own request; regression test: test_mutation_worker.py::TestTheProtocolChannelIsPrivate::test_a_raw_fd_1_write_cannot_forge_a_reply, ::test_a_failing_run_still_reports_its_own_code, test_mutation_teeth_regressions.py::TestWarmRunnerState::test_a_reply_to_another_request_is_skipped

- **Where:** mutation_teeth.py:1356-1361; _mutation_worker.py:136
- **Finding:** worker reply desync after stray fd-1 output
- **Failing input:** `addopts=-s` + `os.write(1, ...)`
- **Proposed fix:** request ids; dup2 protocol fd
- **Verified:** yes-repro

### MT-8 (High) -- sweep_files(jobs>1) extra sandboxes not removed → FileExistsError on 2nd file

**Disposition:** RESOLVED -- extra sandboxes for `jobs>1` are created by `_run_partitions` in a `mkdtemp` directory owned by that call and removed in its `finally`, so a second file in `sweep_files` no longer hits `FileExistsError` and nothing is left in the temp dir; regression test: test_mutation_teeth_regressions.py::TestExtraSandboxes::test_sweep_files_with_jobs_cleans_up_and_handles_a_second_file

- **Where:** mutation_teeth.py:1781-1784
- **Finding:** `sweep_files(jobs>1)` extra sandboxes not removed → FileExistsError on 2nd file
- **Failing input:** 2 targets, jobs=2
- **Proposed fix:** per-call mkdtemp, cleanup
- **Verified:** yes-repro

### MT-9 (Low) -- extra sandboxes copied from live repo, no cold baseline

**Disposition:** RESOLVED -- extra sandboxes are copied from the verified sandbox, not the live repo, and each runs its own cold unmutated baseline (`_sweep_partition(verify_baseline=True)`) and refuses if it fails; regression test: test_mutation_teeth_regressions.py::TestExtraSandboxes::test_extras_are_copied_from_the_verified_sandbox_and_verified, ::test_an_extra_sandbox_whose_baseline_fails_is_refused

- **Where:** mutation_teeth.py:1727, 1783
- **Finding:** extra sandboxes copied from live repo, no cold baseline
- **Failing input:** repo edited mid-sweep
- **Proposed fix:** copy from verified sandbox
- **Verified:** yes-read

### MT-10 (Med) -- warm-path crash kills undercounted

**Disposition:** RESOLVED -- the worker's `_FirstFailure` plugin classifies the first failure's crash line with the shared `is_crash_message` and replies `crash`; the warm path counts a crash from that flag or from exit codes 2-5, and the cold path counts 2-5 or a crash line, so warm and cold agree; regression test: test_mutation_worker.py::TestCrashesAreToldApart::test_a_type_error_is_a_crash_and_an_assertion_is_not, ::test_the_plugin_reads_the_crash_line, test_mutation_teeth_regressions.py::TestWarmCrashesAreCounted::test_a_type_error_kill_in_the_warm_worker_is_a_crash, ::test_an_assertion_kill_is_not

- **Where:** mutation_teeth.py:1579-1584
- **Finding:** warm-path crash kills undercounted
- **Failing input:** TypeError kill
- **Proposed fix:** worker returns exception type
- **Verified:** yes-read

### MT-11 (Low) -- dead AssertionError entry

**Disposition:** RESOLVED -- the dead `AssertionError` entry is removed; the crash list now lives in `_mutation_worker._CRASH_EXCEPTIONS` (re-exported by `mutation_teeth`) and assertions are decided first by `is_crash_message`; regression test: test_mutation_teeth_regressions.py::TestWarmCrashesAreCounted::test_the_crash_list_has_no_dead_entry

- **Where:** mutation_teeth.py:1170 vs 1195
- **Finding:** dead `AssertionError` entry
- **Failing input:** n/a
- **Proposed fix:** remove
- **Verified:** yes-read

### MT-12 (Med) -- worker not restarted after timeout; timeout paid twice

**Disposition:** RESOLVED -- `_WarmRunner.run` sets `last_timed_out`; a warm timeout is recorded INCONCLUSIVE without a cold re-run, and the worker is replaced with `restart()` for the next mutant; a worker that died is restarted too, after the cold fallback; regression test: test_mutation_teeth_regressions.py::TestTimeoutsAreInconclusive::test_a_warm_timeout_is_not_re_run_cold_and_the_worker_is_replaced, ::test_a_dead_worker_falls_back_cold_and_is_restarted

- **Where:** mutation_teeth.py:1350-1355, 1340
- **Finding:** worker not restarted after timeout; timeout paid twice
- **Failing input:** early infinite loop
- **Proposed fix:** restart; warm timeout = inconclusive
- **Verified:** yes-read

### MT-13 (Med) -- wider-net timeout recorded as survivor

**Disposition:** RESOLVED -- `_wider_net` returns "no answer" for a warm timeout (and restarts the worker) or a cold timeout, and the mutant is recorded INCONCLUSIVE instead of a survivor; regression test: test_mutation_teeth_regressions.py::TestTimeoutsAreInconclusive::test_a_wider_net_timeout_is_inconclusive_not_a_survivor, ::test_a_cold_wider_net_timeout_is_inconclusive_too

- **Where:** mutation_teeth.py:1607-1609
- **Finding:** wider-net timeout recorded as survivor
- **Failing input:** hang
- **Proposed fix:** inconclusive
- **Verified:** yes-read

### MT-14 (Med) -- cache drops wider_net_note

**Disposition:** RESOLVED -- `_store_cached_run` writes `wider_net_note` and `_load_cached_run` replays it, so a replay still says WIDER NET UNUSED; regression test: test_mutation_teeth_regressions.py::TestTheCache::test_the_wider_net_note_is_replayed, ::test_a_run_without_a_note_replays_without_one

- **Where:** mutation_teeth.py:1871-1896 vs 1682-1705
- **Finding:** cache drops `wider_net_note`
- **Failing input:** fallback + replay
- **Proposed fix:** store/replay note
- **Verified:** yes-read

### MT-15 (Med) -- unlocked RMW cache with fixed .tmp name

**Disposition:** RESOLVED -- the cache read-modify-write runs under `_cache_lock` (an exclusive `msvcrt`/`fcntl` lock on a sibling `.lock` file, stdlib only, with a timeout) and writes through `_core.atomic_write_text` (unique `mkstemp` file + `os.replace`); a lock timeout skips the store with a warning; regression test: test_mutation_teeth_regressions.py::TestTheCache::test_concurrent_writers_keep_every_entry, ::test_the_lock_excludes_a_second_holder

- **Where:** mutation_teeth.py:1867-1902
- **Finding:** unlocked RMW cache with fixed `.tmp` name
- **Failing input:** 2 xdist workers
- **Proposed fix:** mkstemp + file lock
- **Verified:** yes-read

### MT-16 (Med) -- node-id test paths hashed as placeholder → stale replay

**Disposition:** RESOLVED -- `fingerprint` hashes the file part of a node id (`tests/x.py::test_f` -> `tests/x.py`, `_test_file_of`) and walks its conftests; regression test: test_mutation_teeth_regressions.py::TestTheFingerprintCoversWhatPytestRuns::test_a_node_id_hashes_its_file, ::test_an_unrelated_file_does_not_move_a_node_id_key

- **Where:** mutation_teeth.py:1011-1020, 1046-1051
- **Finding:** node-id test paths hashed as placeholder → stale replay
- **Failing input:** `tests/x.py::test_f`
- **Proposed fix:** split `::`
- **Verified:** yes-repro

### MT-17 (Low) -- only test_*.py in fingerprint

**Disposition:** RESOLVED -- a test directory expands by the repo's `python_files` (read from `pytest.ini`, `pyproject.toml`, `tox.ini` or `setup.cfg` in pytest's order; default `test_*.py *_test.py`); regression test: test_mutation_teeth_regressions.py::TestTheFingerprintCoversWhatPytestRuns::test_the_default_python_files_include_suffix_style, ::test_a_configured_python_files_is_honoured

- **Where:** mutation_teeth.py:1017
- **Finding:** only `test_*.py` in fingerprint
- **Failing input:** `foo_test.py`
- **Proposed fix:** honour python_files
- **Verified:** yes-read

### MT-18 (Low) -- string >= instead of ancestry

**Disposition:** RESOLVED -- the conftest walk uses `Path.is_relative_to(repo_root)`, so a test outside the repo never walks outside conftests whatever their names sort to, and the repo's own conftest still counts; regression test: test_mutation_teeth_regressions.py::TestTheFingerprintCoversWhatPytestRuns::test_conftests_outside_the_repo_are_never_walked

- **Where:** mutation_teeth.py:1022
- **Finding:** string `>=` instead of ancestry
- **Failing input:** `../other/test_x.py`
- **Proposed fix:** `is_relative_to`
- **Verified:** yes-read

### MT-19 (Med) -- "dropped a not" eats a char: unparsable mutant counted as kill

**Disposition:** RESOLVED -- "dropped a `not`" removes the token and only the whitespace up to the next token on the same line (`not(x)` -> `(x)`), and the operator is in `_SYNTAX_RISKY_PREFIXES`, so an unparsable result is never emitted; regression test: test_mutation_teeth_regressions.py::TestOperatorsProduceTheMutantTheyName::test_dropping_a_not_before_a_paren_keeps_the_paren, ::test_dropping_a_spaced_not_removes_the_space

- **Where:** mutation_teeth.py:749, 335
- **Finding:** "dropped a not" eats a char: unparsable mutant counted as kill
- **Failing input:** `return not(x)` → `return x)`
- **Proposed fix:** token-precise removal; syntax-risky
- **Verified:** yes-repro

### MT-20 (Med) -- emptying string: bytes type change / no-op on empty literals

**Disposition:** RESOLVED -- `_string_emptied` evaluates the literal: bytes empty to `b""`, str to `""`, and a literal whose value is already empty (`r''`, `u''`) gets no mutant; regression test: test_mutation_teeth_regressions.py::TestOperatorsProduceTheMutantTheyName::test_emptying_bytes_keeps_them_bytes, ::test_an_empty_literal_is_not_emptied

- **Where:** mutation_teeth.py:765-774
- **Finding:** emptying string: bytes type change / no-op on empty literals
- **Failing input:** `b'ab'`, `r''`
- **Proposed fix:** keep prefix; skip empty
- **Verified:** yes-repro

### MT-21 (Low) -- min description says "max becomes min" (re-keys baseline)

**Disposition:** RESOLVED -- `min` now describes itself as "min becomes max"; `HARNESS_VERSION` bumped to 11 (this and the other generation changes re-key baselines and caches), and the version gate's baseline re-pinned; regression test: test_mutation_teeth_regressions.py::TestOperatorsProduceTheMutantTheyName::test_min_and_max_describe_their_own_direction, test_mutation_harness_version_is_bumped.py::test_the_harness_version_tracks_the_harness

- **Where:** mutation_teeth.py:303
- **Finding:** `min` description says "max becomes min" (re-keys baseline)
- **Failing input:** `min(a,b)`
- **Proposed fix:** fix + bump HARNESS_VERSION
- **Verified:** yes-repro

### MT-22 (Low) -- sampled_containers wrong counts; AST operators skip sampling

**Disposition:** RESOLVED -- `_container_rows` samples ROWS (a dict key and its value together) and reports `(rows kept, rows in the table)`; the sampling is applied in `generate_mutants` to every operator's candidates, not only token ones; regression test: test_mutation_teeth_regressions.py::TestSamplingCountsRowsAndCoversEveryOperator::test_a_six_entry_dict_reports_six_rows, ::test_a_small_table_is_not_sampled, ::test_ast_operators_are_sampled_too, ::test_a_key_and_its_value_are_kept_or_dropped_together

- **Where:** mutation_teeth.py:921, 450-453
- **Finding:** `sampled_containers` wrong counts; AST operators skip sampling
- **Failing input:** 6-entry dict "3 of 12"
- **Proposed fix:** count rows; apply to all sources
- **Verified:** yes-repro

### MT-23 (Low) -- non-UTF8 → UnicodeDecodeError not MutationHarnessError

**Disposition:** RESOLVED -- `read_target` turns `SourceReadError`/`OSError` into `MutationHarnessError`; a declared PEP 263 encoding is honoured and round-trips byte-exact; regression test: test_mutation_teeth_regressions.py::TestBomAndEncodings::test_undecodable_bytes_are_a_harness_error, ::test_a_declared_encoding_is_honoured_and_round_trips

- **Where:** mutation_teeth.py:848, 1513
- **Finding:** non-UTF8 → UnicodeDecodeError not MutationHarnessError
- **Failing input:** latin-1 file
- **Proposed fix:** wrap
- **Verified:** yes-repro

### MT-24 (Low) -- unclosed open() handles (ResourceWarning; Windows locks)

**Disposition:** RESOLVED -- no bare `open()` remains in the harness: reads go through `read_target`/`_core.parse_file`, writes through `write_target` (`Path.write_bytes`) and the cache through `atomic_write_text`; regression test: test_mutation_teeth_regressions.py::TestBomAndEncodings::test_no_file_handle_is_leaked

- **Where:** mutation_teeth.py:848, 932, 1513, ... 2086
- **Finding:** unclosed `open()` handles (ResourceWarning; Windows locks)
- **Failing input:** r2.py
- **Proposed fix:** Path read/write or `with`
- **Verified:** yes-repro

### MT-25 (Med) -- no check the mutated module is imported from sandbox (editable install → all survive)

**Disposition:** RESOLVED -- the warm baseline run asks the worker where the target's dotted names (`_module_names`) were actually loaded from; if they came from outside the sandbox (editable install, PYTHONPATH to the checkout) the sweep raises `MutationHarnessError`. A cold-only run (`use_warm_worker=False`) does one warm probe for the same check; regression test: test_mutation_teeth_regressions.py::TestTheImportMustComeFromTheSandbox::test_end_to_end_a_shadowing_copy_is_detected, ::test_a_foreign_origin_is_refused, ::test_the_sandbox_origin_is_accepted, ::test_a_cold_only_run_checks_too, ::test_module_names_never_include_a_bare_name_inside_a_package, test_mutation_worker.py::TestModuleOrigin

- **Where:** mutation_teeth.py:1134-1136; _mutation_worker.py:95
- **Finding:** no check the mutated module is imported from sandbox (editable install → all survive)
- **Failing input:** src layout w/o pythonpath
- **Proposed fix:** worker reports `__file__`; raise
- **Verified:** no

### MT-26 (Med) -- timeout kills only direct child; Windows grandchildren leak; rmtree errors ignored

**Disposition:** RESOLVED -- cold runs and the warm worker start in their own process group/session; a timeout kills the whole tree (`taskkill /T /F` on Windows, `killpg` elsewhere, stdlib only) via `run_with_deadline`/`_kill_tree`; sandboxes are removed by `_remove_tree`, which clears read-only bits, retries and warns about what it could not remove instead of `ignore_errors=True`; regression test: test_mutation_teeth_regressions.py::TestTimeoutsKillTheTree::test_a_grandchild_dies_with_its_parent, ::test_a_finished_run_returns_its_output, ::test_a_sandbox_that_cannot_be_removed_is_reported, ::test_a_read_only_file_does_not_stop_removal

- **Where:** mutation_teeth.py:1274-1283, 1847, 2099
- **Finding:** timeout kills only direct child; Windows grandchildren leak; rmtree errors ignored
- **Failing input:** test spawns server
- **Proposed fix:** process-group tree kill; log
- **Verified:** yes-read

### MT-27 (Low) -- last_timings/last_failed not reset on early return

**Disposition:** RESOLVED -- `_WarmRunner.run` resets `last_failed`, `last_timings`, `last_crash`, `last_timed_out` and `last_origin` before anything can return early; regression test: test_mutation_teeth_regressions.py::TestWarmRunnerState::test_a_dead_worker_resets_the_last_run

- **Where:** mutation_teeth.py:1340-1361, 1562-1569
- **Finding:** `last_timings`/`last_failed` not reset on early return
- **Failing input:** worker dies
- **Proposed fix:** reset at top
- **Verified:** yes-read

### MT-28 (Low) -- 6-7 ast.parse per target; O(tokens×ranges)

**Disposition:** RESOLVED -- `generate_mutants` parses once and passes the tree to every operator (each still accepts source alone); exclusion containment uses merged intervals with `bisect` (`_Intervals`), and sampled-row and line lookups use `bisect`; regression test: test_mutation_teeth_regressions.py::TestParsedOnce::test_the_target_is_parsed_once, ::test_interval_containment_matches_a_scan

- **Where:** mutation_teeth.py:381, 433, 506, 552, 604, 810, 1215, 697, 716
- **Finding:** 6-7 `ast.parse` per target; O(tokens×ranges)
- **Failing input:** large modules
- **Proposed fix:** parse once; bisect
- **Verified:** yes-read

### MT-29 (Low) -- non-range lines entries dropped from key but crash later

**Disposition:** RESOLVED -- `_normalise_lines` rejects any non-`range` entry with `TypeError` before the fingerprint or any copy; regression test: test_mutation_teeth_regressions.py::TestTheLineScope::test_a_non_range_entry_is_rejected_up_front

- **Where:** mutation_teeth.py:1670 vs 881
- **Finding:** non-range `lines` entries dropped from key but crash later
- **Failing input:** `lines=[(1,5)]`
- **Proposed fix:** validate up front
- **Verified:** yes-read

### MT-30 (Low) -- parent __init__.py not in import closure

**Disposition:** RESOLVED -- `_first_party_imports` adds every package `__init__.py` between a reached module and the repo root (and follows their imports); regression test: test_mutation_teeth_regressions.py::TestTheFingerprintCoversWhatPytestRuns::test_a_package_init_is_in_the_closure

- **Where:** mutation_teeth.py:941-975
- **Finding:** parent `__init__.py` not in import closure
- **Failing input:** edit `pkg/__init__.py`
- **Proposed fix:** add parents
- **Verified:** yes-read

### MT-31 (Med) -- refresh writes baseline before inconclusive/gap/truncation checks

**Disposition:** RESOLVED -- `assert_no_new_surviving_mutant` checks inconclusive mutants, coverage gaps and truncation before a refresh and refuses to rewrite the baseline from a partial run; a complete run still writes the keys with the `NEEDS-JUSTIFICATION:` marker. The refresh flag now goes through `_core.refresh_requested` (option, `PY_CI_SHARED_REFRESH`, argv), and a `_core.Baseline` is accepted as well as a `baseline_ratchet.Baseline`; regression test: test_mutation_teeth_regressions.py::TestTheRatchet::test_a_partial_run_never_rewrites_the_baseline, ::test_a_complete_run_refreshes_with_the_marker, ::test_a_core_baseline_is_accepted_and_needs_justification

- **Where:** mutation_teeth.py:1994-1999 vs 2006-2015
- **Finding:** refresh writes baseline before inconclusive/gap/truncation checks
- **Failing input:** refresh with `limit=5`
- **Proposed fix:** refuse partial refresh
- **Verified:** yes-read

### MT-32 (Low) -- truncated run passes with warning (documented)

**Disposition:** RESOLVED -- new keyword `fail_on_truncation=False` on `assert_no_new_surviving_mutant`: a truncated run fails when it is set and warns otherwise, as documented; regression test: test_mutation_teeth_regressions.py::TestTheRatchet::test_truncation_fails_only_when_asked

- **Where:** mutation_teeth.py:2006-2011
- **Finding:** truncated run passes with warning (documented)
- **Failing input:** `limit=10`
- **Proposed fix:** optional strict
- **Verified:** yes-read

### W-1 (Med) -- cwd/env/sys.path/argv not restored between runs → false kills

**Disposition:** RESOLVED -- the worker snapshots cwd, `os.environ`, `sys.path` and `sys.argv` at startup and restores them (plus `importlib.invalidate_caches()`) before every run (`_ProcessState`). Every warm SURVIVOR was already re-checked cold; a warm kill that a cold run would not make comes from state outside these four and the module purge, which this does not claim to cover; regression test: test_mutation_worker.py::TestProcessStateIsRestoredBetweenRuns::test_cwd_env_path_and_argv_do_not_leak_into_the_next_run, ::test_restore_undoes_each_change

- **Where:** _mutation_worker.py:111-160
- **Finding:** cwd/env/sys.path/argv not restored between runs → false kills
- **Failing input:** `os.chdir` in test
- **Proposed fix:** snapshot/restore; cold re-check sample
- **Verified:** yes-read

### W-2 (Low) -- namespace paths re-resolved per mutant

**Disposition:** RESOLVED -- namespace-package verdicts are memoised in `_IS_LOCAL_NAMESPACE`, keyed on the root and the `__path__` entries; regression test: test_mutation_worker.py::TestNamespaceVerdictsAreCached::test_a_namespace_package_is_resolved_once

- **Where:** _mutation_worker.py:83-91
- **Finding:** namespace paths re-resolved per mutant
- **Failing input:** many namespace pkgs
- **Proposed fix:** cache
- **Verified:** yes-read

### TS-1 (High) -- pytest exit code/stderr ignored: usage/conftest errors → every case "NO TEETH"

**Disposition:** RESOLVED -- `outcome_of` decides from the exit code: 0/1 are verdicts (an exit 1 naming no test still fails), anything else is "PYTEST DID NOT RUN" and the case is ERRORED, never NO TEETH; `_suite_command` adds `-n`, `--no-cov` and `-p no:anyio` only when that plugin is installed; regression test: test_teeth_sweep_regressions.py::TestTheExitCodeDecides, ::TestPluginFlagsOnlyWhenInstalled::test_a_real_run_without_xdist_is_not_read_as_green, ::test_no_plugin_no_flag, ::test_installed_plugins_get_their_flags, ::TestOneBadCaseDoesNotAbortTheSweep::test_a_case_whose_suite_did_not_run_is_errored_not_toothless

- **Where:** teeth_sweep.py:291-304, 254-281
- **Finding:** pytest exit code/stderr ignored: usage/conftest errors → every case "NO TEETH"
- **Failing input:** repo without xdist
- **Proposed fix:** use returncode; add flags only if plugin installed
- **Verified:** yes-read

### TS-2 (Med) -- timeout leaves xdist workers alive on Windows

**Disposition:** RESOLVED -- `_run_suite` runs through `_mutation_runner.run_with_deadline`, which starts pytest in its own process group and kills the whole tree (xdist workers included) on the 900 s timeout; regression test: test_teeth_sweep_regressions.py::TestTimeoutsKillTheTree::test_the_suite_runs_under_a_tree_killing_deadline, test_mutation_teeth_regressions.py::TestTimeoutsKillTheTree::test_a_grandchild_dies_with_its_parent

- **Where:** teeth_sweep.py:291-303
- **Finding:** timeout leaves xdist workers alive on Windows
- **Failing input:** hanging mutation
- **Proposed fix:** tree kill
- **Verified:** yes-read

### TS-3 (Low) -- one bad case aborts whole sweep

**Disposition:** RESOLVED -- each case runs in `_sweep_case` inside a per-case `try`; a failing case is reported ERRORED (exit 1) and the sweep continues, the target still restored; `_apply` reports an undecodable target as not applied; regression test: test_teeth_sweep_regressions.py::TestOneBadCaseDoesNotAbortTheSweep::test_the_next_case_still_runs, ::test_a_latin_1_target_is_not_applied_rather_than_crashing

- **Where:** teeth_sweep.py:242, 344-347
- **Finding:** one bad case aborts whole sweep
- **Failing input:** latin-1 target
- **Proposed fix:** per-case catch
- **Verified:** yes-read

### TS-4 (Low) -- read-back check vacuous for empty/duplicated repl

**Disposition:** RESOLVED -- `_apply` compares the bytes read back with the exact expected bytes, and refuses an empty needle and a no-op substitution; regression test: test_teeth_sweep_regressions.py::TestTheReadBackIsExact::test_an_empty_replacement_is_verified, ::test_a_write_that_did_not_land_is_caught_even_when_the_replacement_already_exists, ::test_a_no_op_substitution_is_refused

- **Where:** teeth_sweep.py:249
- **Finding:** read-back check vacuous for empty/duplicated repl
- **Failing input:** `new=""`
- **Proposed fix:** compare exact bytes
- **Verified:** yes-read

### TS-5 (Low) -- CRLF old becomes \r\r\n

**Disposition:** RESOLVED -- `old`/`new` are normalised to LF before being converted to the file's line ending, so a CRLF needle matches CRLF and LF files without producing `\r\r\n`; regression test: test_teeth_sweep_regressions.py::TestCrlfCases::test_a_crlf_needle_matches_a_crlf_file, ::test_a_crlf_needle_matches_an_lf_file

- **Where:** teeth_sweep.py:244
- **Finding:** CRLF `old` becomes `\r\r\n`
- **Failing input:** CRLF case JSON
- **Proposed fix:** normalise first
- **Verified:** yes-read
