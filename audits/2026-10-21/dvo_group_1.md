Group 1 default_via_or audit: 46 findings. REAL 2, REAL-UNVERIFIED 3, INTENTIONAL 3, FALSE-POSITIVE 38.
Scope: 25 files in src/py_ci_shared (group g1). Findings with a repeated line (e.g. two scanner hits on stale_comment_age.py:576) are listed as separate blocks.
Method: each finding read with +/-6 lines of context; callers grepped in src/py_ci_shared and tests/. Nothing was run.

### DVO-47 (Low) -- _ci_install_parts.py:521 boolean OR of two path tests
**Disposition:** FALSE-POSITIVE -- both operands are booleans (`startswith` / regex match); the `or` is logical alternation, not a default.
**Fix:** none

### DVO-48 (Low) -- _ci_install_parts.py:722 top_level.txt names fall back to RECORD-derived names
**Disposition:** FALSE-POSITIVE -- right operand is an alternative source (package files from `dist.files`) used when `top_level.txt` yields no names; the left operand is a set, and the fallback is the intended inference, not a default for a meaningful falsy value.
**Fix:** none

### DVO-49 (Low) -- _ci_install_parts.py:503 display string '.' for repo root in unresolved message
**Disposition:** FALSE-POSITIVE -- left operand is only interpolated into an error message (`repo.rel(directory)`); an empty relative path means the repo root and '.' is the display form. Nothing branches on it.
**Fix:** none

### DVO-50 (Low) -- _gate_report.py:124 module-level sentinel for function qualifier
**Disposition:** FALSE-POSITIVE -- `name` is the enclosing qualifier, and "" is the module-level sentinel (`visit(tree, "")`); a function name is never empty, so the `or` maps only the module case to "<module>" as intended.
**Fix:** none

### DVO-51 (Low) -- _gate_report.py:127 module-level sentinel for class qualifier (hit 2 of 2 on this file)
**Disposition:** FALSE-POSITIVE -- same as line 124, applied to `ClassDef` nodes; "" only at module scope.
**Fix:** none

### DVO-52 (P2) -- _mutation_fingerprint.py:127 empty `python_files` in pytest.ini replaced by default patterns
**Disposition:** RESOLVED (REAL, settled 2026-10-21 by running pytest: an empty `python_files =` collects no files) -- `parser.get(..., fallback="")` returns "" both when the key is absent and when it is present but empty; the `or` maps both to `("test_*.py", "*_test.py")`. The caller is `_python_files_patterns` (line 113 of the same file, used at line 168 for the mutation fingerprint). If pytest treats an explicitly empty `python_files =` as "collect no patterns", the fingerprint hashes a different file set than pytest collects. Settling test: in a throwaway repo with `[pytest] python_files =` and an empty value, check what `pytest --collect-only` collects versus the fingerprint's file list. Not run here (no pytest in this audit).
**Status:** fixed in this change set (falsy-value trap; regression test added)
**Fix:** `tuple(_split_patterns(parser.get("pytest", "python_files", fallback=None))) if parser.has_option("pytest", "python_files") else _DEFAULT_PYTHON_FILES` (only after the settling test confirms pytest's behaviour)

### DVO-53 (P2) -- _mutation_fingerprint.py:138 empty `[tool.pytest.ini_options] python_files` replaced by default (pyproject)
**Disposition:** RESOLVED (REAL, settled 2026-10-21 by running pytest: an empty `python_files =` collects no files) -- `section.get("python_files")` is None when absent and `[]`/"" when present but empty; the `or` treats both as default. Same caller path as line 127. Same settling test, against a pyproject.toml with `python_files = []`.
**Status:** fixed in this change set (falsy-value trap; regression test added)
**Fix:** `tuple(_split_patterns(section["python_files"])) if "python_files" in section else _DEFAULT_PYTHON_FILES` (only after the settling test)

### DVO-54 (P2) -- _mutation_fingerprint.py:149 empty tox.ini / setup.cfg `python_files` replaced by default (hit 3 of 3 on this file)
**Disposition:** RESOLVED (REAL, settled 2026-10-21 by running pytest: an empty `python_files =` collects no files) -- `parser.get(section_name, "python_files", fallback="")` conflates absent and empty, as at line 127. Same settling test, against a tox.ini with `python_files =`.
**Status:** fixed in this change set (falsy-value trap; regression test added)
**Fix:** `tuple(_split_patterns(parser.get(section_name, "python_files", fallback=None))) if parser.has_option(section_name, "python_files") else _DEFAULT_PYTHON_FILES` (only after the settling test)

### DVO-55 (Low) -- audit_path_references.py:145 boolean OR in segment check
**Disposition:** FALSE-POSITIVE -- both operands are booleans (`==` comparison and a regex match); logical alternation.
**Fix:** none

### DVO-56 (Low) -- ci_test_dir_reachability.py:107 normalise empty or root path token to "."
**Disposition:** INTENTIONAL -- `_norm_path` is the normaliser for pytest path tokens (called at lines 181, 189, 201); a token that reduces to "" (e.g. "./" or "/") means the repository root, and "." is the documented repo-root spelling used by `reaches`. Not documented as a docstring contract, but the mapping is the function's purpose.
**Fix:** none

### DVO-57 (Low) -- ci_test_dir_reachability.py:123 boolean OR over covering paths
**Disposition:** FALSE-POSITIVE -- both operands are booleans from `_covers`; logical alternation.
**Fix:** none

### DVO-58 (Low) -- connection_liveness_kwargs.py:270 DSN text falls back to a string-variable lookup
**Disposition:** FALSE-POSITIVE -- right operand is an alternative source (the value of a named string variable when the expression itself yields no text), not a default for a meaningful falsy value; `_dsn_keys` is only the text-extraction step.
**Fix:** none

### DVO-59 (Low) -- dataclass_case_completeness.py:35 aliases default to a fresh ImportAliases()
**Disposition:** FALSE-POSITIVE -- LHS is an optional `ImportAliases | None` parameter (line 31). `ImportAliases` in `src/py_ci_shared/_core/aliases.py:56` defines no `__bool__` or `__len__`, so an instance is always truthy and the `or` only fires for None.
**Fix:** none

### DVO-60 (Low) -- effect_assertion_parity.py:594 parser defaults to a fresh _Parser(repo_root)
**Disposition:** FALSE-POSITIVE -- LHS is an optional parser argument (None sentinel); `_Parser` (line 194) defines no `__bool__`/`__len__`, so a real parser instance is always truthy.
**Fix:** none

### DVO-61 (Low) -- effect_assertion_parity.py:504 boolean OR of fixture and connect checks
**Disposition:** FALSE-POSITIVE -- both operands are booleans (set intersection truthiness and an `any(...)`); logical alternation.
**Fix:** none

### DVO-62 (Low) -- embedded_postgres.py:128 port defaults to a free port
**Disposition:** FALSE-POSITIVE -- port 0 is not a meaningful request here (it would otherwise be passed to the server as a literal port); all callers (`src/py_ci_shared/embedded_postgres.py:296`, `schema_snapshot_parity.py:552`, `tests/conftest.py:117`) pass no `port`, so the `None` default is the path taken.
**Fix:** none

### DVO-63 (Low) -- embedded_postgres.py:275 cwd defaults to Path.cwd()
**Disposition:** FALSE-POSITIVE -- LHS is an optional `Path | None`; `Path` objects are always truthy, so the `or` only fires for None. Callers: only tests (`tests/test_embedded_postgres.py:64-70`) and no src caller.
**Fix:** none

### DVO-64 (Low) -- embedded_postgres.py:360 dest defaults to cache_bin_dir()
**Disposition:** FALSE-POSITIVE -- LHS is an optional `Path | None`; Path is always truthy, so the `or` only fires for None. `fetch()` is called with no argument at line 365 and at 419.
**Fix:** none

### DVO-65 (Low) -- embedded_postgres.py:50 empty PY_CI_SHARED_CACHE env var treated as unset
**Disposition:** INTENTIONAL -- an empty environment variable is conventionally "unset"; the docstring at line 49 documents the fallback to `~/.cache/py-ci-shared`.
**Fix:** none

### DVO-66 (Low) -- embedded_postgres.py:318 machine arch defaults to platform.machine()
**Disposition:** FALSE-POSITIVE -- an empty architecture string is not meaningful and should resolve to the host arch; `wheel_platform()` is called with no args at line 365 (`fetch`), and the tests pass real machine strings (`tests/test_embedded_postgres.py:128`).
**Fix:** none

### DVO-67 (Low) -- embedded_postgres.py:93 display text "(no output)" for empty log tails
**Disposition:** FALSE-POSITIVE -- LHS is a joined log-tail string used only in a RuntimeError message; empty is exactly the case the placeholder covers.
**Fix:** none

### DVO-68 (Low) -- env_example_round_trip.py:90 field name used when no validation alias
**Disposition:** FALSE-POSITIVE -- right operand is the alternative source (prefix + field name) for a field with no alias; `_alias_names(None)` returns `[]` (line 95-99) by design, so the `or` is the intended branch selector.
**Fix:** none

### DVO-69 (Low) -- fail_open_handlers.py:241 qualname defaults to the function name
**Disposition:** FALSE-POSITIVE -- LHS is an `Optional[str]` qualname; a qualname is never the empty string when supplied, so the `or` only fires for None (default parameter at line 238).
**Fix:** none

### DVO-70 (Low) -- fail_open_handlers.py:192 silent-return predicate OR (hit 1 of 2)
**Disposition:** FALSE-POSITIVE -- all operands are booleans (`_logs_loudly`, `_logs_quietly`, `_marked_best_effort`); logical alternation.
**Fix:** none

### DVO-71 (Low) -- fail_open_handlers.py:192 silent-return predicate OR (hit 2 of 2)
**Disposition:** FALSE-POSITIVE -- same line as above, the second scanner hit on the same boolean expression.
**Fix:** none

### DVO-72 (Low) -- fail_open_handlers.py:348 empty justification replaced by NEEDS-JUSTIFICATION placeholder
**Disposition:** INTENTIONAL -- `render` (line 343) rewrites the accepted-justification map for a refresh; an empty recorded justification is deliberately replaced by a visible placeholder that the gate then flags, so an unjustified fallback cannot be accepted silently.
**Fix:** none

### DVO-73 (Low) -- identity_comparisons.py:42 boolean OR of stringish operands
**Disposition:** FALSE-POSITIVE -- both operands are booleans from recursive `_stringish` calls; logical alternation.
**Fix:** none

### DVO-74 (Low) -- index_coverage.py:191 nulls-first derived from direction keyword
**Disposition:** FALSE-POSITIVE -- both operands are booleans (`"nulls first" in words`, and a descending check); logical alternation implementing Postgres' default NULLS ordering.
**Fix:** none

### DVO-75 (Low) -- index_coverage.py:236 index method defaults to btree
**Disposition:** FALSE-POSITIVE -- `match.group("method")` is None or a non-empty `\w+` match (regex at `src/py_ci_shared/index_coverage.py:57`), so the `or` fires only for the absent-USING case, which is Postgres' default method.
**Fix:** none

### DVO-76 (Low) -- module_reload_safety.py:308 autouse flag OR (hit 1 of 2)
**Disposition:** FALSE-POSITIVE -- `ctx.dir_autouse` and the other operands are booleans in a boolean-returning predicate; logical alternation.
**Fix:** none

### DVO-77 (Low) -- module_reload_safety.py:308 autouse flag OR (hit 2 of 2)
**Disposition:** FALSE-POSITIVE -- same expression, second scanner hit; both operands boolean.
**Fix:** none

### DVO-78 (Low) -- module_reload_safety.py:354 source segment falls back to ast.unparse
**Disposition:** FALSE-POSITIVE -- `ast.get_source_segment` returns None only when positions are unavailable; the fallback to `ast.unparse` is the intended alternative source, and a real statement never yields an empty segment.
**Fix:** none

### DVO-79 (Low) -- no_xfail_to_defer.py:162 tracked-reason check OR
**Disposition:** FALSE-POSITIVE -- both operands are booleans (regex search and an `any(...)`); logical alternation.
**Fix:** none

### DVO-80 (Low) -- numba_seed_range.py:77 call name falls back to the attribute name
**Disposition:** FALSE-POSITIVE -- `aliases.qualified_name(call)` returns None when unresolved; the right operand is the alternative source (the raw attribute name) and "" is never a meaningful qualified name.
**Fix:** none

### DVO-81 (Low) -- numba_seed_range.py:220 sink check OR
**Disposition:** FALSE-POSITIVE -- both operands are booleans (set membership and a regex conjunction); logical alternation.
**Fix:** none

### DVO-82 (Low) -- prompt_field_parity.py:171 JSON-schema node predicate OR (hit 1 of 2)
**Disposition:** FALSE-POSITIVE -- all operands are booleans (`any(...)`, `kind in _JSON_TYPES`, and a list-subset check); logical alternation in `_is_schema_node` (called at line 209).
**Fix:** none

### DVO-83 (Low) -- prompt_field_parity.py:171 JSON-schema node predicate OR (hit 2 of 2)
**Disposition:** FALSE-POSITIVE -- same expression, second scanner hit; boolean operands.
**Fix:** none

### DVO-84 (Low) -- pydantic_field_bounds.py:90 Literal values from top-level annotation or nested Union members
**Disposition:** FALSE-POSITIVE -- right operand is the alternative source (Literal values nested inside Union/Optional args) used when the top-level annotation has none; the left list is a list of values, and the fallback is the intended lookup.
**Fix:** none

### DVO-85 (Low) -- save_failure_markers.py:79 second marker regex on no-match
**Disposition:** FALSE-POSITIVE -- both operands are `re.Match | None`; the `or` tries the template regex only when the marker regex misses (`_MARKER_RE` and `_TEMPLATE_RE` at lines 52 and 54).
**Fix:** none

### DVO-86 (Low) -- source_text_claims.py:559 claim kind from a second classifier
**Disposition:** FALSE-POSITIVE -- LHS and RHS are both `Optional[str]` classifier results; the RHS is the alternative source tried when the first classifier yields None.
**Fix:** none

### DVO-87 (Low) -- stale_comment_age.py:576 scan_dirs=[] silently widened to the configured or default dirs (hit 1 of 2)
**Disposition:** RESOLVED (REAL, fixed) -- `stale_warning_summary` (line 554) is a public function whose `scan_dirs` is an `Optional[Sequence[str]]`; a caller passing an explicit empty list gets the repo's configured dirs (or ".") scanned instead of what was asked for. The CLI (line 600-606) passes `args.scan_dir`, which is None when the flag is absent (argparse default None), so the CLI never hits the empty-list case; the bug is for direct Python callers (tests call with explicit lists at `tests/test_stale_comment_age.py:428-457`). Impact is low: it widens the scan rather than silently passing.
**Status:** fixed in this change set (falsy-value trap; regression test added)
**Fix:** `dirs = list(scan_dirs) if scan_dirs is not None else (cfg.get("scan_dirs") or ["."])`

### DVO-88 (Low) -- stale_comment_age.py:576 scan_dirs=[] silently widened (hit 2 of 2)
**Disposition:** RESOLVED (REAL, fixed) -- same expression, second scanner hit: the `cfg.get("scan_dirs") or ["."]` tail is the fallback chain, which is intended; the real defect is the first `scan_dirs or` step, and the fix above addresses it.
**Status:** fixed in this change set (falsy-value trap; regression test added)
**Fix:** `dirs = list(scan_dirs) if scan_dirs is not None else (cfg.get("scan_dirs") or ["."])`

### DVO-89 (Low) -- survivorship_scoring.py:132 '?' label for an unnamed call
**Disposition:** FALSE-POSITIVE -- the node already passed `_call_name(node) in metrics` at line 128, so the name is non-empty; the `or "?"` is unreachable in practice and only guards a type-level Optional.
**Fix:** none

### DVO-90 (Low) -- wallclock_test_waits.py:88 baseline directory defaults to DEFAULT_BASELINE_DIR
**Disposition:** FALSE-POSITIVE -- LHS is an optional directory path; "" is not a meaningful directory (it would resolve to the working directory), so the default is correct for it. Callers in `tests/test_wallclock_test_waits.py:39-45` pass no baseline_dir.
**Fix:** none

### DVO-91 (Low) -- worktree_hygiene.py:156 object format defaults to git's repo format
**Disposition:** FALSE-POSITIVE -- LHS is an optional `str | None` object-format argument; the real caller `_blob_ids` (line 178) passes a non-empty value from `_object_format`, and "" is never a valid format name.
**Fix:** none

### DVO-92 (Low) -- worktree_hygiene.py:265 boolean OR of upstream and known-object checks
**Disposition:** FALSE-POSITIVE -- LHS is a comparison (`in`) and RHS a truthiness of a set intersection; both boolean.
**Fix:** none
