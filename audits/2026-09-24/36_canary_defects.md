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
