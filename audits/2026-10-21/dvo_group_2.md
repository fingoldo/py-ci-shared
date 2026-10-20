# default_via_or audit, group g2 (25 files, 45 findings)

Verdicts: REAL 1 | FALSE-POSITIVE 39 | INTENTIONAL 5 (REAL-UNVERIFIED 0)
Scope: src/py_ci_shared, read-only. Callers grepped in src/py_ci_shared; line numbers are in the named file.

### DVO-93 (Low) -- _core/baseline.py:249 refresh_command "" replaced by generic hint
**Disposition:** INTENTIONAL -- `Baseline.__init__` (signature default `refresh_command: str = ""`) treats an empty hint as "not given"; every caller (e.g. api_floor.py:343, alembic_concurrently.py:160, baseline_ratchet.py:207) passes a non-empty command, so the generic hint only applies when none is supplied.
**Fix:** none

### DVO-94 (Low) -- _import_graph.py:184 problem summary falls back to "missing or empty" text
**Disposition:** FALSE-POSITIVE -- the left operand is a `"; ".join` of rendered problem strings; the right operand is a message used only when no problems were rendered, not a default for a meaningful value. Caller is read_declared itself, which raises with this text.
**Fix:** none

### DVO-95 (Low) -- _import_graph.py:260 empty top_level.txt falls back to RECORD-derived names
**Disposition:** INTENTIONAL -- an empty `top_level.txt` means no declared top-level names, so the code deliberately derives them from `dist.files` (the set-literal fallback). This is the installed-distribution reader's design; the derived set is the alternative source, not a default for a value the caller meant.
**Fix:** none

### DVO-96 (Low) -- _import_graph.py:118 `guarded or _suppresses_import_error(node)`
**Disposition:** FALSE-POSITIVE -- left operand is a bool flag carried down the AST visit; the right operand is a boolean predicate. No meaningful falsy value is replaced.
**Fix:** none

### DVO-97 (Low) -- _mutation_runner.py:213 empty failures list shows "the directory still exists"
**Disposition:** FALSE-POSITIVE -- `failures` is a list of error strings; empty means no specific error, and the right operand is only the warning text in the same function (`warnings.warn`). Nothing is coerced.
**Fix:** none

### DVO-98 (Low) -- _mutation_sweep.py:96 `not passed and (returncode in ... or _killed_by_crash(stdout))`
**Disposition:** FALSE-POSITIVE -- both operands are booleans (a tuple membership test and a predicate call); no value is defaulted. Called from _primary_verdict in the same file.
**Fix:** none

### DVO-99 (Low) -- _mutation_sweep.py:106 `confirm.returncode in ... or _killed_by_crash(confirm.stdout)`
**Disposition:** FALSE-POSITIVE -- boolean disjunction of a membership test and a predicate; the return value is `False, <bool>`. No falsy-to-default mapping.
**Fix:** none

### DVO-100 (Low) -- baseline_hygiene.py:125 empty note after `#` takes the preceding heading text
**Disposition:** FALSE-POSITIVE -- the right operand is an alternative source (the accumulated comment heading), not a default for a meaningful falsy note; an empty note is not a value anyone sets on purpose here.
**Fix:** none

### DVO-101 (Low) -- cancellation_prone_moments.py:216 `bool(names) or _contains(term, _is_power_sum_call)`
**Disposition:** FALSE-POSITIVE -- `bool(...)` on the left makes both operands booleans; the right is a predicate. No value replaced.
**Fix:** none

### DVO-102 (Low) -- cancellation_prone_moments.py:197 `_mentions(node, first) or _contains(node, _is_first_sum_call)`
**Disposition:** FALSE-POSITIVE -- both operands are boolean predicates over the AST; the function returns a bool.
**Fix:** none

### DVO-103 (Low) -- cancellation_prone_moments.py:70 `isinstance(f, ast.Constant) or _centred(f)`
**Disposition:** FALSE-POSITIVE -- left is an isinstance bool, right a predicate; used only inside `any(...)`.
**Fix:** none

### DVO-104 (Low) -- ci_health.py:388 consumer token chain `consumer_token or GH_TOKEN or GITHUB_TOKEN`
**Disposition:** FALSE-POSITIVE -- each operand is an environment token string; an empty string means "unset" and the next token source is the intended alternative (token precedence chain). Caller: default_api, invoked from main at ci_health.py:510-511.
**Fix:** none

### DVO-105 (2nd) -- ci_health.py:388 (Low) -- `(GH_TOKEN or "").strip() or (GITHUB_TOKEN or "").strip()`
**Disposition:** FALSE-POSITIVE -- same token-precedence chain as the line above (second env source after GH_TOKEN); operands are strings, the right one is an alternative source.
**Fix:** none

### DVO-106 (Low) -- ci_health.py:515 `at = now or datetime.now(timezone.utc)`
**Disposition:** FALSE-POSITIVE -- `now` is an optional datetime; datetime objects are always truthy, so only None triggers the fallback.
**Fix:** none

### DVO-107 (Low) -- ci_health.py:187 workflow name or path defaults to "?"
**Disposition:** FALSE-POSITIVE -- the values are display strings from the GitHub API payload (`workflow.get("name")`); an empty name is best shown as "?" and no logic depends on the empty value.
**Fix:** none

### DVO-108 (Low) -- ci_health.py:261 `r.error or over_threshold(r, max_red_days)`
**Disposition:** FALSE-POSITIVE -- used only as a truthiness test inside `any(...)` to choose the exit code; the left operand (an error message or None) is never returned as a value.
**Fix:** none

### DVO-109 (Low) -- ci_workflow_gate.py:110 step name falls back to job name or a placeholder
**Disposition:** FALSE-POSITIVE -- `step.name` and `job_name` are optional strings; empty means unnamed and the next label is the intended alternative for the report line.
**Fix:** none

### DVO-110 (Low) -- ci_workflow_gate.py:117 job name falls back to job ident
**Disposition:** FALSE-POSITIVE -- `job.name` is an optional string; an unnamed job is correctly labelled by its YAML identifier. Right operand is an alternative source.
**Fix:** none

### DVO-111 (Low) -- constant_fallback_cache_key.py:108 `bool(_NAME.search(...)) or any(_DECORATOR.search(...))`
**Disposition:** FALSE-POSITIVE -- both operands are boolean predicates (regex match wrapped in bool, and an any()); no value defaulted.
**Fix:** none

### DVO-112 (Low) -- destructive_tests_throwaway_only.py:161 `self.connects or bool(self.markers) or self.dsn_evidence`
**Disposition:** FALSE-POSITIVE -- all three operands are evidence flags used in a boolean predicate (`return bool(...) and (...)`); the result is a bool.
**Fix:** none

### DVO-113 (Low) -- external_fact_tables.py:211 `day = today or _dt.datetime.now(...).date()`
**Disposition:** FALSE-POSITIVE -- `today` is `Optional[date]`; date objects are always truthy, so only None selects the wall-clock default. Callers: find_external_fact_table_problems is called at line 271 with today from its own signature (default None).
**Fix:** none

### DVO-114 (Low) -- external_fact_tables.py:224 `day = today or _dt.datetime.now(...).date()`
**Disposition:** FALSE-POSITIVE -- same as line 211; `Optional[date]` parameter where a date is always truthy.
**Fix:** none

### DVO-115 (Low) -- gate_config_honesty.py:86 `hook.get('alias') or hook.get('id')`
**Disposition:** FALSE-POSITIVE -- the pre-commit hook key; an empty alias means "no alias" and the hook id is the alternative identifier. Used only as a dict key label.
**Fix:** none

### DVO-116 (Low) -- ignore_ratchet.py:90 `json.loads(out.stdout or "[]")`
**Disposition:** FALSE-POSITIVE -- `out.stdout` is the ruff JSON output; empty stdout (no findings under `--exit-zero`) is replaced by `"[]"`, which is the valid empty-result JSON with identical meaning.
**Fix:** none

### DVO-117 (Low) -- lock_held_across_io.py:94 `aliases.qualified_name(call.func) or _last_segment(call.func)`
**Disposition:** FALSE-POSITIVE -- `qualified_name` returns `Optional[str]` (None when unresolved); the fallback is the bare last segment, an alternative name, and the result is used only as a label.
**Fix:** none

### DVO-118 (Low) -- lock_held_across_io.py:80 `segment in lock_names or _named_like_a_lock(segment)`
**Disposition:** FALSE-POSITIVE -- both operands are boolean (membership test and name predicate) inside `bool(segment) and (...)`.
**Fix:** none

### DVO-119 (Low) -- offline_suite_without_credentials.py:160 blank `offline_credentials_marker` becomes DEFAULT_MARKER
**Disposition:** INTENTIONAL -- an empty pytest marker name is invalid, so an unset/blank ini value is deliberately mapped to `DEFAULT_MARKER` ("real_database"), which is also the `addini` default at line 180. Documented by that default; caller is the pytest config hook path in this module.
**Fix:** none

### DVO-120 (Low) -- offline_suite_without_credentials.py:189 same blank-marker fallback in pytest_configure
**Disposition:** INTENTIONAL -- identical contract to line 160: a blank ini value means "use the documented default marker" (addini default at line 180). The marker must be non-empty to register.
**Fix:** none

### DVO-121 (Low) -- package_doctests.py:72 `bool(parts and parts.intersection(...)) or any(_under(name, p) ...)`
**Disposition:** FALSE-POSITIVE -- both operands are boolean predicates; the function returns a bool used to skip modules.
**Fix:** none

### DVO-122 (Low) -- protocol_attributes.py:33 `what or getattr(obj, "__qualname__", None) or repr(obj)` (left operand)
**Disposition:** FALSE-POSITIVE -- `what` is an optional label string passed by callers; the chain only builds an error message label, so an empty label correctly falls through to the qualified name.
**Fix:** none

### DVO-123 (2nd) -- protocol_attributes.py:33 (Low) -- `getattr(obj, "__qualname__", None) or repr(obj)`
**Disposition:** FALSE-POSITIVE -- label text only; `__qualname__` is a non-empty str for named callables and repr is the alternative. Nothing about the assertion depends on it.
**Fix:** none

### DVO-124 (Low) -- pytest_markers.py:259 `tests = tests_dir or repo_root / "tests"`
**Disposition:** FALSE-POSITIVE -- `tests_dir` is `Path | None`; Path objects are always truthy, so only None triggers the default directory.
**Fix:** none

### DVO-125 (Low) -- pytest_markers.py:276 same `tests_dir or repo_root / "tests"` in find_unregistered_markers
**Disposition:** FALSE-POSITIVE -- `Path | None` parameter; Path is always truthy.
**Fix:** none

### DVO-126 (Low) -- pytest_markers.py:299 same `tests_dir or repo_root / "tests"` in assert_markers_registered
**Disposition:** FALSE-POSITIVE -- `Path | None` parameter; Path is always truthy.
**Fix:** none

### DVO-127 (Low) -- readme_env_var_parity.py:54 `(aliases or ImportAliases()).qualified_name(node)`
**Disposition:** FALSE-POSITIVE -- `aliases` is `Optional[ImportAliases]`; the fallback builds an empty alias table only when None is passed. Even if ImportAliases defined a falsy state, the fallback yields an equivalent empty table.
**Fix:** none

### DVO-128 (Low) -- schema_snapshot_parity.py:542 `bin_dir = pg_bin or find_pg_bin()`
**Disposition:** FALSE-POSITIVE -- `pg_bin` is an optional path (None when not supplied); a Path is always truthy, and the call resolves the binary directory only when none is given.
**Fix:** none

### DVO-129 (Low) -- sibling_floor_skew.py:264 `GITHUB_TOKEN or CONSUMER_READ_TOKEN`
**Disposition:** FALSE-POSITIVE -- two environment token strings in precedence order; empty means unset and the second token is the alternative source. Called from the GitHub version reader in this module.
**Fix:** none

### DVO-130 (Low) -- sibling_floor_skew.py:336 `resolver or (lambda s, o, r: version_at(...))`
**Disposition:** FALSE-POSITIVE -- `resolver` is an optional callable; functions are always truthy, so only None selects the default lambda. Caller: find_sibling_floor_skew at line 371 passes resolver through.
**Fix:** none

### DVO-131 (Low) -- sibling_floor_skew.py:181 `spec.get("rev") or spec.get("tag") or spec.get("branch")`
**Disposition:** FALSE-POSITIVE -- uv source ref keys are alternatives (rev, tag, branch); an empty rev means "not pinned by rev", and the next key is the intended alternative source, not a default for a meaningful value.
**Fix:** none

### DVO-132 (2nd) -- sibling_floor_skew.py:181 (Low) -- `spec.get("tag") or spec.get("branch")`
**Disposition:** FALSE-POSITIVE -- same alternative-key chain as the line above.
**Fix:** none

### DVO-133 (Low) -- sibling_floor_skew.py:404 `siblings=args.sibling or DEFAULT_SIBLINGS`
**Disposition:** INTENTIONAL -- `--sibling` is `action="append", default=[]` (line 397) whose help text reads "default: the consumer list"; an empty list means "use the default sibling list" by the CLI contract.
**Fix:** none

### DVO-134 (P2) -- sql_verifier_coverage.py:216 empty literal exclusion reason replaced by its unparsed AST
**Disposition:** REAL -- `_string_value(reason)` returns `""` for an empty string literal, which is falsy, so `or ast.unparse(reason)` stores `"''"` (the source text of the literal) as the reason. Callers of verifier_lists (line 236, in the check that rejects thin reasons at line 246, `len(reason.strip()) < min_reason_len`) therefore see a 2-character reason that can pass the length check, letting an empty exclusion reason escape the "thin reason" gate. Only non-string reasons (None from `_string_value`) should fall back to unparsing.
**Status:** fixed in this change set (falsy-value trap; regression test added)
**Fix:** `_string_value(reason) if _string_value(reason) is not None else ast.unparse(reason)` (replaces the right-hand side of the assignment at line 216)
