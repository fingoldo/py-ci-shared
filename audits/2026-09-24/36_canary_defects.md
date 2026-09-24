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
