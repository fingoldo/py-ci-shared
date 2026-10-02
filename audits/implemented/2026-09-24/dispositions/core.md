# Dispositions: shared `_core` + pilot gates (naive_utcnow, private_imports, value_bearing_asserts)

Scope: `src/py_ci_shared/_core/` (new), `baseline_ratchet.py` (MT-1, atomic save), and three pilot gates migrated onto
the core. Other gates carrying the same systemic findings (MP-1/MP-2/TZ-4 sites in other modules, ARCH-14..17,
ARCH-21, ARCH-22) are migrated by the follow-up agents; this file covers only the pilot sites and the shared core.

### MP-1
**Disposition:** RESOLVED -- `_core.read_source` decodes `.py` files like the interpreter (`tokenize.detect_encoding`: BOM, PEP 263 cookie, UTF-8) and strips the BOM; naive_utcnow and private_imports now parse through `_core.scan_python`, so a BOM file is checked, not dropped. Other MP-1 sites (marker_runner_coverage, meta_private_imports, module_reload_safety, optional_truthiness, phantom_code_references, pytest_markers, prompt_field_parity) are left to the migration agents. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_a_bom_file_is_checked_not_dropped, tests/test_private_imports.py::TestAuditRegressions::test_unparsable_and_bom_files, tests/test_core_source.py::TestReadSource::test_a_bom_is_stripped

### MP-2
**Disposition:** RESOLVED -- `_core.scan_python` records each unreadable or unparsable file as a `SourceProblem` (path, line, kind, message) and never skips it. `find_naive_utcnow` lists it as `path:line: unparsable: ...`, `find_private_cross_package_imports` as `(path, "<unparsed>")`, and both `assert_*` entry points fail on it. The old test that required a silent skip (tests/test_naive_utcnow.py:78) now requires the file to be reported. regression test: tests/test_naive_utcnow.py::TestScoping::test_an_unparseable_file_is_reported_and_does_not_stop_the_walk, tests/test_naive_utcnow.py::TestScoping::test_an_unparseable_file_fails_the_entry_point

### MP-3
**Disposition:** RESOLVED -- naive_utcnow matches every `.utcnow`/`.utcfromtimestamp` ATTRIBUTE, called or not (`Field(default_factory=datetime.utcnow)`), and reports a call once. `ImportAliases` resolves `arrow`/`pendulum` (whose `utcnow()` returns an aware value) so those are not flagged. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_uncalled_references_and_utcfromtimestamp_are_caught, tests/test_naive_utcnow.py::TestAuditRegressions::test_aware_libraries_are_resolved_through_aliases_and_not_flagged

### MP-4
**Disposition:** RESOLVED -- for naive_utcnow and private_imports (module_reload_safety is left to migration): a missing root raises `_core.CorpusError`; `assert_no_naive_utcnow` and `assert_no_private_cross_package_imports` take `min_files=1`, which counts PARSED files. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_an_empty_root_fails_the_floor, tests/test_naive_utcnow.py::TestAuditRegressions::test_a_missing_root_raises, tests/test_private_imports.py::TestAuditRegressions::test_the_floor_and_a_missing_root

### SUITE-20
**Disposition:** RESOLVED -- `assert_no_naive_utcnow(..., min_files=1)` is new; an empty root now fails. `test_a_clean_tree_passes` still passes because its tree contains one parsed file. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_an_empty_root_fails_the_floor

### SUITE-21
**Disposition:** RESOLVED -- the `chr(108)+...` spelling is replaced by a plain `node.lineno`. No scanner in this repo flags `lineno`, so there was no scanner to fix. regression test: tests/test_naive_utcnow.py::TestAuditRegressions::test_line_numbers_are_read_plainly

### MP-26
**Disposition:** RESOLVED -- private_imports resolves relative imports against the importer's package (`_core.resolve_relative`/`package_of`, same for `__init__.py`). It also judges `module.alias` for `from pkg.a import _impl`. When the module itself is already private, only the module is reported, so existing allowlists keep matching. regression test: tests/test_private_imports.py::TestAuditRegressions::test_a_private_name_from_a_public_module_is_flagged, tests/test_private_imports.py::TestAuditRegressions::test_relative_imports_are_resolved, tests/test_private_imports.py::TestAuditRegressions::test_a_relative_sibling_import_is_allowed

### MP-27
**Disposition:** RESOLVED -- importer paths go through `_core.relative_posix`, which returns the absolute POSIX path when the file is not under `repo_root`, so it no longer raises `ValueError`. regression test: tests/test_private_imports.py::TestAuditRegressions::test_src_outside_repo_root_does_not_raise

### SUITE-15
**Disposition:** RESOLVED -- mutant P7 (dropping the `ast.Import` branch) is now killed by a fixture whose only reach is a plain `import pkg.metrics._core`, with a public-import control. regression test: tests/test_private_imports.py::TestAuditRegressions::test_a_plain_import_alone_is_flagged

### TZ-4
**Disposition:** RESOLVED -- for value_bearing_asserts (uncalled_functions, unread_init_params, unresolved_imports and vacuous_loop_assertions are left to migration): files are parsed through `_core.scan_python`, so a BOM is handled, and an unparsable file is listed by `find_value_bearing_asserts` and fails `assert_no_value_bearing_asserts`. A floor `min_files=1` on parsed files is added. regression test: tests/test_value_bearing_asserts.py::TestAuditRegressions::test_a_bom_file_is_scanned, tests/test_value_bearing_asserts.py::TestAuditRegressions::test_an_unparsable_file_is_listed_and_fails, tests/test_value_bearing_asserts.py::TestAuditRegressions::test_the_file_floor

### TZ-15
**Disposition:** RESOLVED -- the baseline is now the multiset `_core.Baseline`, so two identical asserts need two entries. Keys hold the full expression with no 90-char truncation. Old baselines keep working: JSON lists are read as multisets, and a pre-fix truncated key still matches when only that key is present. regression test: tests/test_value_bearing_asserts.py::TestAuditRegressions::test_duplicate_asserts_are_counted_not_collapsed, tests/test_value_bearing_asserts.py::TestAuditRegressions::test_long_expressions_are_keyed_in_full_and_legacy_keys_still_match, tests/test_core_baseline.py::TestMultiset::test_a_duplicate_finding_is_not_absorbed_by_one_entry

### TZ-16
**Disposition:** RESOLVED -- opt-in `strict=True` on `is_narrowing_assert` / `find_value_bearing_asserts` / `assert_no_value_bearing_asserts` treats bare `Name`/`Attribute`/`Subscript` truthiness as a value check. The default is unchanged, so consumers see no new failures. regression test: tests/test_value_bearing_asserts.py::TestAuditRegressions::test_strict_mode_treats_bare_truthiness_as_a_value_check

### TZ-17
**Disposition:** RESOLVED -- for value_bearing_asserts (uncalled_functions is left to migration): a missing baseline FAILS and names `--refresh-value-asserts-baseline` / `PY_CI_SHARED_REFRESH=value-asserts`. It is written only on refresh (`refresh=True`, the pytest option via `request=`, the env var, or argv), and never from a walk that failed its floor or had unparsed files. The old test that required seed-and-skip was re-framed. regression test: tests/test_value_bearing_asserts.py::TestTheRatchet::test_a_missing_baseline_fails_and_is_written_only_on_refresh, tests/test_value_bearing_asserts.py::TestTheRatchet::test_refresh_via_env_var_as_under_xdist, tests/test_core_baseline.py::TestMissingAndRefresh::test_a_missing_baseline_fails_naming_the_refresh_command

### MT-1
**Disposition:** RESOLVED -- `baseline_ratchet.Baseline.enforce` (used by mutation_teeth) and `_core.Baseline.enforce` both reject any entry whose note starts with `NEEDS-JUSTIFICATION`. A refresh followed by a normal run now fails until a human writes the reason. regression test: tests/test_core_baseline.py::TestUnjustified::test_baseline_ratchet_rejects_the_marker_too, tests/test_core_baseline.py::TestUnjustified::test_needs_justification_entries_fail_a_normal_run, tests/test_value_bearing_asserts.py::TestAuditRegressions::test_needs_justification_entries_are_rejected

## _core API

`from py_ci_shared._core import ...`. The package is stdlib-only, and pytest is imported lazily, only inside `BaselineOutcome.raise_for_pytest`.

- `read_source(path) -> str`: interpreter-exact decoding with the BOM stripped. Raises `SourceReadError(path, message, line)`.
- `parse_file(path) -> ast.Module`, `parse_source(path) -> (source, tree)`: cached on (resolved path, mtime_ns, size). Raise `SourceParseError` (`.path`, `.line`, `.message`). Trees are shared, so do not mutate them. `clear_parse_cache()`.
- `iter_files(root, patterns=("*.py",), *, exclude=DEFAULT_EXCLUDE, include_untracked=True, use_git=None) -> list[Path]`: uses `git ls-files -z --cached [--others --exclude-standard]` inside a work tree and falls back to a pruned walk outside one. Exclusion is matched against root-relative parts. The result is sorted. A missing root raises `CorpusError`. `DEFAULT_EXCLUDE` is the one canonical skip set; a gate adds its own scope names with `DEFAULT_EXCLUDE | {...}`.
- `scan_python(dir_or_files, *, min_files=1, root=None, patterns=..., exclude=..., use_git=None) -> ScanResult`. `ScanResult` has `.files` (list of `ParsedFile(path, rel, tree, source)`; iterating the result yields them), `.unparsed` (list of `SourceProblem(path, rel, line, kind, message)`), `.parsed_count`, `.unparsed_findings(rule)`, `.check_floor()` (raises `EmptyScanError`; the floor counts PARSED files), `.check_unparsed()` (raises `UnparsedFilesError`) and `.assert_ok(allow_unparsed=False)`. Both errors subclass `AssertionError`.
- `Finding(path, line, rule, message, key="")`: frozen. The default key is `rule::path::message` with no line number. `.render()`.
- `Baseline(path, *, gate, refresh_command, new_note="")`. `.enforce(findings_or_keys, *, refresh, describe=None, guidance="") -> BaselineOutcome` with `.new/.stale/.unjustified/.missing/.refreshed/.ok/.message` and `.raise_for_pytest(fail_on_stale=True)`. It also has `.load() -> (Counter, notes)`, `.save(counts, notes)` and `.regenerate(found)`. Behaviour: multiset; a missing file fails; `NEEDS-JUSTIFICATION` notes fail; writes are atomic (`atomic_write_text`), sorted, LF and UTF-8 in the format `{"schema":1,"gate":..,"entries":{key:{"count":n,"note":..}}}`. Readers also accept a JSON list, `{key: note}`, `{"accepted": {...}}`, `{key: count}` and orjson output.
- `refresh_requested(flag, request_or_config=None) -> bool`: checks the pytest option or `--py-ci-refresh`, then env `PY_CI_SHARED_REFRESH` (comma list: the full flag, the flag without `--`, the short name such as `value-asserts`, or `all`), then `sys.argv`. `register_refresh_options(parser, flags=())` goes in a conftest's `pytest_addoption`. The existing per-module `register_refresh_option` functions are unchanged and should become thin wrappers during migration.
- `ImportAliases.from_tree(tree, *, package=None)`, `.qualified_name(node) -> str | None`: resolves `import a as b`, `from a import b as c`, relative imports (given the module's package), attribute chains and a Call's callee. `resolve_relative(module, level, package)`, `package_of(path, src_root, root_package)`, `module_of(...)`, `relative_posix(path, root)` (never raises).

Idiom for an AST gate:

```python
from py_ci_shared._core import DEFAULT_EXCLUDE, Baseline, Finding, ImportAliases, refresh_requested, scan_python

def assert_no_x(root, *, min_files=1, baseline_path=None, request=None, skip_dir_names=()):
    import pytest
    scan = scan_python(root, min_files=min_files, exclude=DEFAULT_EXCLUDE | frozenset(skip_dir_names))
    findings = [Finding(f.rel, n.lineno, "x", ast.unparse(n)) for f in scan for n in ast.walk(f.tree)
                if isinstance(n, ast.Call) and ImportAliases.from_tree(f.tree).qualified_name(n) == "importlib.reload"]
    scan.assert_ok()                       # floor on parsed files + unparsed files fail (never skipped)
    if baseline_path is None:
        if findings: pytest.fail("\n".join(f.render() for f in findings))
        return
    Baseline(baseline_path, gate="x", refresh_command="pytest --refresh-x-baseline").enforce(
        findings, refresh=refresh_requested("--refresh-x-baseline", request)).raise_for_pytest()
```

In a real gate, build `ImportAliases` once per file, outside the node loop. If the gate wants to report the floor and unparsed files together with its findings in a single `pytest.fail`, use `check_floor()` in a try block (see the three pilots), not `assert_ok()`.

## Not done here

- The remaining ~95 gates are not migrated, and the other sites for MP-1/MP-2/MP-4/TZ-4/TZ-17 (module_reload_safety, uncalled_functions and others) are still open for the migration agents.
- The per-module `register_refresh_option` / `_refresh_requested` copies (code_audit_meta and five others) are not yet wrappers over `_core.refresh`.
- There is no pytest11 plugin entry point yet, so `--py-ci-refresh` exists only after a conftest calls `register_refresh_options`. The env var works without it.
- mypy was run with the repo config (python_version 3.10). The installed mypy cannot target 3.9, so 3.9 compatibility was checked by construction instead: `from __future__ import annotations`, no runtime `X | Y`, no `match`, and runtime aliases use `typing.Union`.
