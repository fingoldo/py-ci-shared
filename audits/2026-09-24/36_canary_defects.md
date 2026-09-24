# Audit: defects found by the gate-teeth canary run and the self-gate run

Found while resolving INFRA-2/INFRA-3 (tests/test_gate_teeth.py) and ARCH D1 (tests/test_self_gates.py).

## Findings

### CANARY-1 (Med) -- env_flag_parsing skips a BOM file

**Disposition:** RESOLVED -- env_flag_parsing reads every file through `_core.scan_python` (interpreter-exact decoding, BOM stripped), so a BOM file is scanned like a plain one; the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestEnvFlagParsing::test_a_bom_file_is_read_like_a_plain_one, tests/test_gate_teeth.py::test_gate_bites_its_canary[env_flag_parsing-bom]

- **Finding:** find_hand_parsed_env_flags reads with encoding=utf-8, so a BOM file fails to parse and is dropped

### CANARY-2 (Med) -- env_flag_parsing passes an unparsable file

**Disposition:** RESOLVED -- `assert_env_flags_use_one_parser` reports every unreadable or unparsable file by path (the `except SyntaxError: continue` is gone) and its `min_files` floor now counts PARSED files; the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestEnvFlagParsing::test_an_unparsable_file_fails_by_name, tests/test_gate_floors_and_parse_errors.py::TestEnvFlagParsing::test_the_floor_counts_parsed_files, tests/test_gate_teeth.py::test_gate_bites_its_canary[env_flag_parsing-unparsable]

- **Finding:** SyntaxError is caught and the file skipped

### CANARY-3 (Med) -- git_dependency_pins passes an unparsable pyproject.toml

**Disposition:** RESOLVED -- git_dependency_pins parses the file once with tomllib; invalid TOML raises `PyprojectParseError` (a `_core.SourceParseError`) from `find_unpinned_git_dependencies`, and `assert_all_git_dependencies_pinned` fails naming the file; the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestGitDependencyPins::test_an_unparsable_pyproject_fails_naming_the_file, tests/test_gate_teeth.py::test_gate_bites_its_canary[git_dependency_pins-unparsable]

- **Finding:** _source_table_violations swallows the TOML parse error and returns []

### CANARY-4 (Med) -- git_dependency_pins misses a single-line dependency array

**Disposition:** RESOLVED -- the line-anchored regex over raw text is replaced by matching each string of the parsed TOML document, so single-line arrays, optional dependencies, dependency groups and build requires are all scanned; the canary's only unpinned dependency is now a single-line optional-dependencies array; regression test: tests/test_gate_floors_and_parse_errors.py::TestGitDependencyPins::test_a_single_line_dependency_array_is_scanned, tests/test_gate_floors_and_parse_errors.py::TestGitDependencyPins::test_optional_and_group_dependencies_are_scanned, tests/test_gate_teeth.py::test_gate_bites_its_canary[git_dependency_pins-violation]

- **Finding:** dependencies = ["x @ git+...@main"] returns [] because the regex is anchored at line start

### CANARY-5 (Med) -- module_reload_safety.assert_no_reloads_in_code passes an empty corpus

**Disposition:** RESOLVED -- `module_reload_safety.assert_no_reloads_in_code` takes `min_files=1` (keyword) and fails when fewer files parsed across the roots; the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestFloors::test_module_reload_safety, tests/test_gate_teeth.py::test_gate_bites_its_canary[module_reload_safety-empty]

- **Finding:** no floor on parsed files

### CANARY-6 (Med) -- vacuous_loop_assertions.assert_no_new_floorless_loop passes an empty corpus

**Disposition:** RESOLVED -- `vacuous_loop_assertions.assert_no_new_floorless_loop` takes `min_files=1` (keyword) on parsed test files; the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestFloors::test_vacuous_loop_assertions, tests/test_gate_teeth.py::test_gate_bites_its_canary[vacuous_loop_assertions-empty]

- **Finding:** no floor on parsed files

### CANARY-7 (Med) -- config_call_site_parity.assert_no_divergent_cfg_get_call_site_defaults passes an empty corpus

**Disposition:** RESOLVED -- `config_call_site_parity.assert_no_divergent_cfg_get_call_site_defaults` and the three sibling asserts built on the same call-site scan take `min_files=1` (keyword) on parsed files; the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestFloors::test_config_call_site_parity, tests/test_gate_teeth.py::test_gate_bites_its_canary[config_call_site_parity-empty]

- **Finding:** no floor on parsed files

### CANARY-8 (Med) -- unresolved_imports.assert_all_from_imports_resolve passes an empty corpus

**Disposition:** RESOLVED -- `unresolved_imports.assert_all_from_imports_resolve` takes `min_files=1` (keyword) on files parsed under the scan roots; the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestFloors::test_unresolved_imports, tests/test_gate_teeth.py::test_gate_bites_its_canary[unresolved_imports-empty]

- **Finding:** no floor on parsed files

### CANARY-9 (Med) -- phantom_markdown_links.assert_no_phantom_markdown_links passes an empty corpus

**Disposition:** RESOLVED -- `phantom_markdown_links.assert_no_phantom_markdown_links` takes `min_files=1` (keyword) on Markdown files that could be read; the canary's link target became a .txt so its empty corpus really holds no Markdown; the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestFloors::test_phantom_markdown_links, tests/test_gate_teeth.py::test_gate_bites_its_canary[phantom_markdown_links-empty]

- **Finding:** no floor on scanned files

### CANARY-10 (Med) -- pytest_markers.assert_markers_registered passes an empty corpus

**Disposition:** RESOLVED -- `pytest_markers.assert_markers_registered` takes `min_files=1` (keyword) on Python files under the tests directory (honouring `exclude`); the strict xfail is removed; regression test: tests/test_gate_floors_and_parse_errors.py::TestFloors::test_pytest_markers, tests/test_gate_teeth.py::test_gate_bites_its_canary[pytest_markers-empty]

- **Finding:** no floor on parsed files

### CANARY-11 (Low) -- 22 fail_open_handlers baseline entries carry NEEDS-JUSTIFICATION placeholders

**Disposition:** RESOLVED -- 21 of the 22 placeholders were detector false positives: each handler appends a report of the failure (an f-string or a record built from the caught exception, or into a `problems`/`violations`/`unparsed` list), never the element itself; `admit_on_error` now exempts a message string, a record constructed from the caught exception, and lists named for problems/violations/unparsed/unreadable, while `(spec, exc)`, `spec` and `Wrapped(spec)` in a keep list are still found. The one real admission, `spec_bound_doubles.files_driving`, keeps an unreadable file on purpose so the parse that follows reports it, and its baseline note says so; no placeholder remains; regression test: tests/test_fail_open_handlers.py::test_reporting_the_error_is_not_admit_on_error, tests/test_fail_open_handlers.py::test_keeping_the_element_next_to_the_error_is_still_admit_on_error, tests/test_self_gates.py::test_gate_passes_on_this_repo

- **Finding:** the self-gate baseline accepts existing handlers in this repo without a reason; each needs a reason or a fix

### CANARY-12 (Med) -- _core parse cache serves a stale tree after a same-size rewrite within one mtime tick

**Disposition:** RESOLVED -- the cache is keyed on a blake2b digest of the file's bytes, so correctness no longer depends on mtime resolution; regression tests: test_same_size_edit_with_the_same_mtime_is_seen (fails on the old key), test_an_unchanged_file_with_a_touched_mtime_keeps_its_tree

- **Finding:** keyed on (mtime_ns, size); found by the NEW-13..24 agent as a flaky test

### CANARY-13 (Low) -- test_docs_inventory_parity imports tomllib unguarded

**Disposition:** RESOLVED -- imports tomllib through py_ci_shared._toml_compat; regression test: tests/test_python_floor_compatibility.py tomllib ban

- **Finding:** fails to import on Python 3.9/3.10, found by the self-CI floor matrix

### CANARY-14 (Low) -- phantom_markdown_links and tracker_summary_parity import private helpers of sibling modules

**Disposition:** RESOLVED -- the helpers are public (tracked_files, table_cells) with the private names kept as aliases for existing importers; regression test: tests/test_self_gates.py private_imports

- **Finding:** private_imports self-gate: repo_hygiene._tracked_files and audit_round_format._table_cells

### CANARY-15 (Low) -- resource_release_paths narrows with assert isinstance, which python -O strips

**Disposition:** RESOLVED -- an explicit isinstance check raising TypeError; regression test: tests/test_self_gates.py value_bearing_asserts

- **Finding:** value_bearing_asserts self-gate at resource_release_paths.py:80

### CANARY-16 (Med) -- printed_advice skips BOM and unparsable files and has no floor

**Disposition:** RESOLVED -- find_printed_advice reads through _core.scan_python (BOM handled, unparsable files raise UnparsedFilesError unless allow_unparsed, EmptyScanError below min_files); added assert_printed_advice_registered for the key-to-test table the docstring describes, registered the gate and gave it a canary; regression tests: test_a_bom_file_is_scanned_like_a_plain_one, test_an_unparsable_file_is_reported_not_skipped, test_an_empty_corpus_fails_the_floor, test_assert_registered_fails_on_missing_stale_and_empty_entries

- **Finding:** a gate added to master during this round read with encoding=utf-8 and `continue`d on SyntaxError, the fail-open class the round removed everywhere else; found by the package inventory and teeth tests after the rebase.

### CANARY-17 (Low) -- test_setup_env's shell round trip ran through the stubbed subprocess.run

**Disposition:** RESOLVED -- the test keeps the original subprocess.run as _REAL_RUN before the `runs` fixture patches it, so the POSIX branch executes sh for real; the quoting was confirmed separately by a sh round trip; regression test: TestShellProfile::test_the_value_is_shell_quoted (failed on ubuntu and macos CI, passed on Windows where the branch is skipped)

- **Finding:** CI on Linux and macOS failed with `'' == '/home/u/a&b ...'` because the fixture's fake run returned empty stdout.

### CANARY-18 (Med) -- embedded_postgres cannot start as an unprivileged user on Linux

**Disposition:** RESOLVED -- the server is started with -k <throwaway data dir> on non-Windows, so the socket and its lock live in the temporary directory; regression tests: test_a_real_server_starts_accepts_a_connection_and_is_gone_after, test_run_hands_the_command_its_dsn (now executed in CI)

- **Finding:** the server used the packaged socket directory /var/run/postgresql and died with 'could not create lock file ... Permission denied' once CI provided PG_BIN


### CANARY-19 (Low) -- the leak-guard plugin test counted a message pytest prints twice under -rA

**Disposition:** RESOLVED -- the test counts `ERROR at teardown of test_leaks_env` headers, one per reported error; regression test: test_the_table_loads_the_resource_leak_guard_only_when_asked

- **Finding:** the error text appears in the error block and in the short summary, so the 'loaded once' assertion failed on every CI runner


### CANARY-20 (Low) -- printed_advice fix landed without black and with unescaped match= patterns

**Disposition:** RESOLVED -- formatted with the pinned black and raw match patterns; checked with the pinned ruff before pushing

- **Finding:** the black-filtered and ruff-blocking (RUF043) CI jobs failed


### CANARY-21 (Med) -- kwarg_forwarding skipped unreadable and unparsable files and was unregistered

**Disposition:** RESOLVED -- the three finders read through _core.scan_python (BOM handled, UnparsedFilesError unless allow_unparsed=True, min_files=1 floor raising EmptyScanError), signatures gain only keyword arguments; registered as a library with a canary and its pre-existing mypy errors fixed; regression test: test_kwarg_forwarding.py::test_a_bom_file_is_scanned_like_a_plain_one, test_an_unparsable_file_is_reported_not_skipped, test_allow_unparsed_keeps_the_findings_of_the_files_that_parse, test_an_empty_corpus_fails_the_floor; test_gate_teeth.py canary kwarg_forwarding

- **Finding:** ast.parse(read_text(encoding='utf-8')) with a bare skip on SyntaxError/UnicodeDecodeError/ValueError: a BOM file, a broken file or a file outside the root dropped its wrappers silently, and an empty corpus passed; the module was missing from registry.toml, so CI's inventory check failed


### CANARY-22 (Med) -- order_losing_filters skipped unreadable and unparsable files, had no assert entry and was unregistered

**Disposition:** RESOLVED -- reads through _core.scan_python with the same allow_unparsed / min_files contract, gains assert_no_order_losing_filters for the allow table its docstring describes (unlisted, stale and reasonless entries fail), registered as a gate with a canary; regression test: test_order_losing_filters.py::test_a_bom_file_is_scanned_like_a_plain_one, test_an_unparsable_file_is_reported_not_skipped, test_an_empty_corpus_fails_the_floor, test_assert_fails_on_unlisted_stale_and_reasonless_entries; test_gate_teeth.py canary order_losing_filters

- **Finding:** the same fail-open read as printed_advice before c732ca3: a BOM or a syntax error hid every filter in the file


### CANARY-23 (Low) -- randomly_seed_guard unregistered

**Disposition:** RESOLVED -- registered as a library (a runtime helper called from pytest_configure) and given a reasoned EXEMPT entry in test_gate_teeth.py, since it reads no corpus; regression test: test_package_inventory.py and test_gate_teeth.py::test_every_scanning_gate_has_a_canary_or_a_reasoned_exemption

- **Finding:** the module shipped without a registry.toml entry or README catalogue row, which fails the package inventory check on master


### CANARY-24 (Med) -- phantom_code_references flagged real dotted paths of installed dependencies

**Disposition:** RESOLVED -- a dotted name whose head the repo does not declare is real when it resolves by import (longest importable prefix, then getattr; anything the import raises counts as unresolved; cached per name), after the repo-declared check; baseline entries now match on file + name (`<rel>::<token>`, or a full violation line with its line number and wording ignored); regression test: test_phantom_code_references.py::TestExternalDottedNames (os.path.join and email.mime.text.MIMEText pass, os.path.joinn and nosuchpkg.x.y are flagged, a repo-declared renamed member is still flagged, an import raising SystemExit is unresolved), test_baseline_entries_match_on_file_and_name_not_line_or_wording

- **Finding:** since 46dc50a the member check covers 3+ part names but resolved them only against the repo's own declarations, so a backticked `pyutilz.llm.get_llm_provider` counted as phantom (autopsia's gate grew by about 108 findings), while a misspelled stdlib member such as `os.path.joinn` passed because the stdlib head alone carried the claim; baseline entries included line and message wording


### CANARY-25 (Med) -- test_partition_reachability read a subshell's closing paren as part of a project name

**Disposition:** RESOLVED -- an unquoted flag value stops at whitespace, quotes and the shell metacharacters ( ) ; | & < > and backtick; quoted names with spaces are still read whole; regression test: test_test_partition_reachability.py::test_an_unquoted_name_stops_at_a_shell_metacharacter (the exact polyvocab_app line), test_a_subshell_selection_still_reports_a_project_nobody_runs (negative control)

- **Finding:** polyvocab_app ci.yml:531 `(cd e2e && npx playwright test --project=chromium-desktop)` was read as project `chromium-desktop)`, so the real chromium-desktop project was reported as never run


### CANARY-26 (Med) -- gates handed a list of directories treated each directory as a file

**Disposition:** RESOLVED -- _core.scan_python, which every _gate_run gate reads through, now takes a directory, an iterable of files, or an iterable mixing files and directories: each directory entry is enumerated with iter_files under the same patterns and exclusions, its findings keep paths relative to that directory (so a list reports what one call per directory reports), a file named twice is parsed once, and a missing entry raises CorpusError; signatures unchanged; regression test: test_core_scan.py::TestMixedCorpus, test_swallowed_exceptions.py::TestAListOfDirectories (two directories equal two calls, a mixed list, a missing entry raises), test_atomic_write_staging.py::TestAListOfDirectories

- **Finding:** scan_python's iterable branch passed every entry straight to the parser, so swallowed_exceptions, atomic_write_staging and the other gates typed `Iterable[Union[str, Path]]` reported a directory entry as an unreadable file; autopsia had to call each gate once per directory


### CANARY-27 (Med) -- effect_assertion_parity reported SELECT-only reads as unchecked database effects

**Disposition:** RESOLVED -- an execute-like call (execute, executemany, execute_values, execute_batch, exec_driver_sql) whose statement is a SQL literal (plain, concatenated, or wrapped in text()/SQL()) that only reads is not an effect: SELECT, SHOW, VALUES, TABLE, DESCRIBE, EXPLAIN without ANALYZE and `WITH ... SELECT`, after comments and quoted values are stripped; a CTE that writes, `SELECT ... INTO` and any write keyword still count (row locks `FOR UPDATE` do not), a sqlalchemy select(...) construct is a read, and a statement built at run time (f-string, variable) keeps the old behaviour; regression test: test_effect_assertion_parity.py::TestAReadIsNotAnEffect (SELECT, commented select, CTE SELECT, FOR UPDATE, text(), select() pass; CTE INSERT, data-modifying CTE, SELECT INTO, INSERT, CREATE, f-string and variable SQL are reported; a SELECT beside an UPDATE still reports)

- **Finding:** every execute() counted as an effect, so a module that only queried was told to assert on a database write it never makes


### CANARY-28 (Med) -- git_dependency_pins could no longer read a requirements file

**Disposition:** RESOLVED -- find_unpinned_git_dependencies reads a pip requirements file (`.txt`/`.in`, or a file whose first content line is not TOML) as pip does: comments stripped (a `#` that starts the line or follows whitespace, so a `.git#egg=` fragment stays), backslash continuations joined, `-r`/`-c` includes followed relative to the including file and each file read once, `-e` and a bare `git+URL#egg=name` line read as a git dependency; a `.toml` file is still parsed as TOML and an invalid one still fails; regression test: test_git_dependency_pins.py::TestRequirementsFiles (a pinned file with comments, markers, includes and an editable line is clean; an unpinned line in the file or its include, and a bare URL with no ref, are reported; an include cycle is read once; an invalid pyproject still fails)

- **Finding:** since 1.17.0 the input was always parsed as TOML, so `requirements-dev.txt` raised TOMLDecodeError and both mlframe and pyutilz had to check their git lines by hand. Real trees: mlframe and pyutilz requirements-dev.txt went from a parse failure to 0 unpinned (their pyproject results are unchanged: mlframe `pyutilz: branch=master`, pyutilz none)


### CANARY-29 (Med) -- audit_round_format read status words inside other words as status mentions

**Disposition:** RESOLVED -- status_problems counts a cell as naming a status only when the upper-case word stands alone (not preceded or followed by a letter, digit, `_`, `-` or apostrophe), or when the whole cell is one status word in another case, bold or not (`Resolved`, `**Partial**`), which is still reported as a miswritten status; regression test: test_audit_round_format.py::test_a_status_word_inside_another_word_or_prose_is_not_a_status_mention (`docs`, `gap-closed`, `partial_fit`, `A fixed PIT seed` pass), test_a_standalone_status_word_outside_the_bold_form_is_still_reported, test_a_status_mention_in_any_case_must_be_the_bold_upper_form

- **Finding:** the mention regex became case-insensitive with no word boundary, so `docs` matched DOC and `partial_fit` PARTIAL; four mlframe trackers failed. Real trees with mlframe's 19 status words: mlframe 5 problems before, 0 after; pyutilz 0 and 0


### CANARY-30 (Med) -- identity_comparisons reported `is None` and enum-member identity as string identity

**Disposition:** RESOLVED -- a comparison with `None`, `True`, `False` or `...` on either side is never reported; class-level assignments of a class deriving from an Enum base (`Enum`, `IntEnum`, `StrEnum`, `Flag`, `IntFlag`, `ReprEnum`) are members, not string constants; and a comparison whose other side is a dotted name rooted at an import of a module outside the scan (`inspect.Parameter.VAR_KEYWORD`) is not reported through the instance-attribute fallback; a module-level string sentinel compared with `is` is still reported; regression test: test_identity_comparisons.py::test_singletons_enum_members_and_external_names_are_not_string_identity, test_a_string_sentinel_default_is_still_reported. Also covers the glossum/social report `self.profile_path is None` flagged because a pydantic model declares `profile_path: str = "..."` (merged)

- **Finding:** an attribute read through an instance was matched against every class-level string in the corpus, so `self.score is None`, `p.kind is inspect.Parameter.VAR_KEYWORD` and `outcome is IterationOutcome.BREAK` counted. Real trees: mlframe 10 before, 0 after; pyutilz 1 and 1 (`env_file is _UNSET_ENV_FILE` against `_UNSET_ENV_FILE = "<unset>"`, a real string-sentinel identity check)


### CANARY-31 (Med) -- gpu_timing_sync reported a region that synchronizes before the timer stops

**Disposition:** RESOLVED -- a GPU-rooted blocking device-to-host copy (`cp.asnumpy(...)`) is a synchronization, not timed work, so a region ending `deviceSynchronize(); cp.asnumpy(d_R)` is synchronized; work launched after the last blocking copy is still reported; regression test: test_gpu_timing_sync.py::test_a_blocking_device_to_host_copy_after_a_synchronize_ends_the_region, test_a_kernel_after_the_last_blocking_copy_is_still_reported

- **Finding:** `cp.asnumpy` counted as the last GPU work, after the synchronize, so mlframe `_auto_tune_sweeps_b.py:408` was reported. Real trees: mlframe 3 before, 2 after (`_pairs_score.py:655` injected callable, `_screen_predictors.py:384` `cp.random.seed`, both outside this finding); pyutilz 0 and 0


### CANARY-32 (Med) -- marker_runner_coverage read shell variables, Actions expressions and a Python argument list as runner paths

**Disposition:** RESOLVED -- a `${{ ... }}` expression is one opaque word; a positional word holding a shell variable or Actions expression is not a path and narrows nothing (`tests/f.py::$t` keeps its file); pytest-split's `--splits`/`--group`/`--splitting-algorithm`, `--durations-path` and `--timeout-method` take a value; a here-document body is data for its command, not shell lines, and a body fed to `python` contributes the pytest invocations its argument lists spell (`subprocess.call([sys.executable, "-m", "pytest", "-m", "gpu", ...])`); regression test: test_marker_runner_coverage.py::TestShellAndActionsWords (sharding values and template tokens are not paths, a Python heredoc's argument list is one invocation with `-m gpu`, a non-Python heredoc is not a command, a sharded runner selects while a deselecting one still reports)

- **Finding:** `$SHARD_GROUP`, `${{`, `matrix.group` and `}}` were read as test paths, so every sharded runner reached nothing, and the gpu-matrix runner's argument list parsed as the expression `,`. Real trees: mlframe unselected slow/gpu/fuzz 337/174/2 before, 0/0/0 after; pyutilz 0/0/0 and 0/0/0


### CANARY-33 (Med) -- vacuous_loop_assertions ignored guard asserts and flagged constant iterables

**Disposition:** RESOLVED -- a floor may assert any way the iterable can be shown non-empty: the iterable, what it passes through unchanged in size (`sorted(x)`, `x.items()`, `dict(x)`, `x.splitlines()`), either side of a concatenation or union (`a + b`, `{**a, **b}`, `a | b`), every argument of a `zip`, or the value a local name was bound to once (`lines = src.splitlines()` after `assert "x" in src`); identifiers match as names, not substrings; a loop over a constant non-empty collection needs no floor (`range` over integer literals that yields something, a dict literal, `dict(k=v)`, a non-empty string, `.items()` of one, or a local bound once to one); negative scans without a guard, `range(0)`, `range(n)`, a zip with one side guarded and a loop under `if injected:` are still reported; regression test: test_vacuous_loop_assertions.py::TestFloorsAndConstantIterables

- **Finding:** 1.17.0 rightly stopped letting asserts in unrelated loops vouch for each other, which exposed that guard asserts on the parts of the iterable never counted and constant iterables other than literal tuples were flagged. Real trees: mlframe 26 before, 15 after; pyutilz 49 before, 36 after. The remaining ones are unguarded (zip over two computed arrays, loops over computed dicts, source scans with no floor); one became visible: mlframe `test_mrmr_hermite_injection.py:121` loops under `if injected:`, which the old substring match excused through an unrelated `injected_names`


### CANARY-34 (Low) -- stale_comment_age read prose with parentheses as commented-out code

**Disposition:** RESOLVED -- already fixed by a2f452c (a bare word, a space and a parenthesis is a glossed heading; in Python files the comment body must parse as one statement, a bare expression must be a call); verified here with the ad2 shapes and the glossum/social shape `# Vowels (Spanish has 5 pure vowels)` (merged); regression test: test_stale_comment_age.py::test_prose_with_parentheses_is_not_commented_out_code, test_commented_out_calls_are_still_code

- **Finding:** `# identity (NaN-aware)` and `# F_q(s) per (q, s)` were candidates. Real trees (commented-out-code candidates before dating): 03cfff1 mlframe 75, pyutilz 47, glossum 115; at a2f452c mlframe 37, pyutilz 46, glossum 5, all remaining ones code-shaped (`print(...)`, `ensure_installed(...)`, three shape labels such as `# isinstance(X, T)` above the branch that matches it)


### CANARY-35 (Med) -- readme_env_var_parity counted environment writes as reads

**Disposition:** RESOLVED -- `os.environ.setdefault(...)` and `os.environ.pop(...)` used as a statement (the value discarded) write the environment and are not reads; the same calls whose value is used still are; assignments and `del` through `os.environ[...]` and a `{**os.environ, "X": ...}` literal were already not reads; regression test: test_readme_env_var_parity.py::test_environment_writes_are_not_reads. Also covers the glossum report of `PYTHONIOENCODING` (merged): its read was `os.environ.setdefault("PYTHONIOENCODING", "utf-8")` in scripts/_refsuite_run.py; its `PGPASSWORD` is a real read (`os.environ["PGPASSWORD"]` in scripts/_audit_quality.py:11) and stays reported

- **Finding:** thread-count defaults set by benchmarks became documentation debt. Real trees (names read): mlframe 269 before, 259 after; pyutilz 21 and 21


### CANARY-36 (Low) -- optional_truthiness flagged guards whose zero case a sibling comparison spells out

**Disposition:** RESOLVED -- a truth test of an optional number is not reported when a sibling operand of the same boolean compares that name with a number and agrees with the truth test at 0: `not n or n <= 0` (0 goes where the comparison sends it), `n and n > 0`, `0 >= n or not n`; `not n or n > 5`, `m and m >= 0` and `n if n else 3` are still reported; `n is None or n < 1` was never a truth test; regression test: test_optional_truthiness.py::test_a_truth_test_whose_zero_case_a_sibling_comparison_spells_out_is_not_reported, test_a_sibling_comparison_that_disagrees_at_zero_does_not_excuse_it

- **Finding:** the `<= 0` co-check collapses 0 with None on purpose. Real trees: mlframe 30 before, 26 after; pyutilz 4 and 4. `x if x else default` stays reported: it sends a deliberate 0 to the default, which is the bug class


### CANARY-37 (Med) -- a one-or-many path parameter configured with a directory string became a one-file list

**Disposition:** RESOLVED -- _core.config reads an annotation that names `Path` outside a collection (`Union[str, Path, Iterable[...]]`) as one path or many: a plain string stays one path (a directory the gate enumerates), a glob or a list becomes a list; a list-only parameter still gets a list; regression test: test_cli.py::TestResolveKwargs::test_a_one_or_many_parameter_keeps_a_plain_string_as_one_directory, test_the_real_gate_reads_a_tests_root_string_as_a_directory

- **Finding:** `tests_root = "tests"` became `[repo/tests]` and no_xfail_to_defer and clock_day_boundary reported `tests:1: unreadable` (social/llm_bench adopters worked around it with a glob)


### CANARY-38 (High) -- a consumer's refresh-option registration stopped pytest once the plugin was installed

**Disposition:** RESOLVED -- already fixed by d9b0b8a (_core.refresh._add treats argparse.ArgumentError, raised for a clash across option groups, as already registered); verified here with a consumer-shaped conftest calling three gates' register_refresh_option (one twice) under `-p py_ci_shared.pytest_plugin`: 03cfff1 exits with `argparse.ArgumentError: argument --py-ci-refresh: conflicting option string`, the tree passes and `--py-ci-refresh` still reaches the test; regression test: test_core_refresh.py (d9b0b8a)

- **Finding:** every consumer using the documented helpers broke on upgrade to 1.17.0


### CANARY-39 (Med) -- import_cycles reported `from . import sibling` as a cycle through the parent package

**Disposition:** RESOLVED -- a from-import out of an ancestor package that asks for no name the package binds (every name is a submodule, reached as its own edge) is no edge to the ancestor, like `import a.b.c` inside `a.b.x`; `from . import NAME` of a name the package binds stays an edge; regression test: test_import_cycles.py::test_a_sibling_reached_through_the_package_is_not_a_cycle (checked against a real interpreter), test_a_name_the_parent_binds_imported_relatively_is_still_a_cycle

- **Finding:** `pkg/sub/b.py: from . import a as av` gave `pkg.sub -> pkg.sub.b -> pkg.sub` although `import pkg.sub.b` loads. Real trees: mlframe 18 cycles before, 17 after; glossum 5 and 3; pyutilz 0 and 0


### CANARY-40 (Med) -- gate_commands ignored a workflow-level working directory

**Disposition:** RESOLVED -- a job without its own `defaults.run.working-directory` is scoped by the workflow's `defaults.run.working-directory`; regression test: test_gate_config_honesty.py::test_a_workflow_level_working_directory_scopes_every_job_without_its_own (a job's own default still wins)

- **Finding:** every step of a workflow scoped only at workflow level was dropped by `scope=`, so marker_runner_coverage reported nightly tests as never run (social realtime_applications)


### CANARY-41 (Med) -- private_imports treated a module's private helper as foreign to its sibling modules

**Disposition:** RESOLVED -- a private name imported from a plain module (`from pkg.db.models import _helper`, `pkg/db/models.py` a file) belongs to the package holding that module, so modules of `pkg.db` and below share it; a module in another package is still reported, and a private name of a package (`from pkg.metrics import _core`) keeps the package as owner; regression test: test_private_imports.py::test_a_private_helper_of_a_module_is_shared_with_its_siblings_not_with_other_packages

- **Finding:** the owner was the module itself, contradicting the module doc; glossum 250 pairs before, 82 after (the 168 sibling pairs the adopter counted)


### CANARY-42 (Med) -- coverage_config_parity missed a --cov-config passed through a shell variable or array

**Disposition:** RESOLVED -- an invocation expanding `$name`/`${name[@]}` has its own config when that variable or array is assigned `--cov-config`/`--rcfile`/`--cov-fail-under` in the same step (`name=(...)` across lines, `name+=`, `export name="..."`); an array holding only `--cov` still reports; regression test: test_coverage_config_parity.py::test_whole_suite_own_config_or_non_blocking_runs_are_not_flagged (three array/variable cases), test_narrow_coverage_run_inheriting_fail_under_is_flagged (config in a different array)

- **Finding:** `--cov` was followed through the array, `--cov-config` was not (glossum)


### CANARY-43 (Low) -- the pinned black version was not importable

**Disposition:** RESOLVED -- tool_versions.BLACK_VERSION = "26.5.1", the version black-filtered.yml runs; regression test: test_pinned_tool_versions.py::test_black_version_is_the_one_the_shared_workflow_runs (every `black==` in the workflow equals it)

- **Finding:** only RUFF_VERSION was exported, so a consumer could not hold its own `black==` pin to the shared workflow's


### CANARY-44 (Low) -- the package shipped no py.typed

**Disposition:** RESOLVED -- src/py_ci_shared/py.typed added and listed in package data; regression test: test_package_inventory.py::test_the_package_is_marked_typed, test_package_data_ships_the_configs

- **Finding:** consumers' mypy reported `import-untyped` on every py_ci_shared import


### CANARY-45 (Med) -- source_text_claims read a deserialised report keyed by .py paths as source text

**Disposition:** RESOLVED -- the value of a deserialiser (`json.loads`/`json.load`, `orjson.loads`, `tomllib`/`tomli` `load(s)`, `yaml.safe_load`/`load`/`safe_load_all`) is data, so a read wrapped in one neither taints a name nor makes a helper a source reader; a helper returning a `.py` file's text is still a reader; regression test: test_source_text_claims.py::test_a_deserialised_report_keyed_by_py_paths_is_data_not_source

- **Finding:** llm_bench `test_branch_coverage_ratchet.py` had 4 claims over a coverage.json; 4 before, 0 after


### CANARY-46 (Low) -- config default parity compared a default before the call's own numeric cast

**Disposition:** RESOLVED -- config_call_site_parity compares a call-site default as the accessor returns it: `get(..., 7, float)` is 7.0 and `get(..., 7.0, int)` is 7, for both the schema check and the cross-site check; bool and str casts, and non-numeric values, are left alone; regression test: test_config_call_site_parity.py::test_an_int_default_cast_to_float_by_the_call_equals_a_float_schema_default, test_an_int_default_without_a_float_cast_or_a_different_number_still_disagrees

- **Finding:** `get(..., 7, float)` was reported against a schema default of 7.0 (social realtime_applications wrote floats to pass)


### CANARY-47 (Med) -- lint-blocking.yml could not lint a monorepo subproject

**Disposition:** RESOLVED -- new inputs `working-directory` (default "."; codespell, bandit, vulture, interrogate and deptry run there), `codespell-toml` (default "pyproject.toml"), `bandit-config` (-c) and `bandit-exclude` (-x); actionlint, zizmor and yamllint stay at the repository root; defaults keep every existing caller's behaviour; regression test: test_reusable_workflows.py::test_lint_blocking_lints_a_subproject_where_it_lives_and_the_workflows_at_the_root

- **Finding:** the workflow ran every tool from the repository root with a fixed `--toml pyproject.toml` and no bandit config or exclusions (blocked social ADOPT-28)


### CANARY-48 (Med) -- machine_specific_paths baseline keys carried the absolute path they flag

**Disposition:** RESOLVED -- a finding's baseline key is `rule::file::<sha256 of the matched text, 16 hex>`, so a refreshed baseline names no machine path and passes baseline_hygiene; the report still shows the path; existing baselines re-key on the next refresh; regression test: test_machine_specific_paths.py::test_a_refreshed_baseline_carries_no_absolute_path_and_passes_baseline_hygiene (a changed path is still new)

- **Finding:** the key was `rule::file::<matched path>`, which baseline_hygiene rejects by design, so the gate could not be baselined (glossum)

### CANARY-49 (Med) -- self-CI could not install the dev extra once sqlalchemy 2.1.0 was released

**Disposition:** RESOLVED -- the dev extra excludes sqlalchemy 2.1.0 (`>=2.0,!=2.1.0`), whose sdist pyproject declares a duplicate normalized extra that uv refuses to build; stale_comment_age.py reformatted with the pinned black; verified by the next self-CI run

- **Finding:** every test job and mypy-full failed at `uv pip install -e .[dev]` with `duplicate normalized extra name mssql-pymssql`; black-filtered flagged stale_comment_age.py.

