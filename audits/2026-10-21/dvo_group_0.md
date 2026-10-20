Findings: 46 (default_via_or scan, group g0, 24 files under src/py_ci_shared; several lines carry more than one scanner entry, each listed separately below).
REAL: 0 | FALSE-POSITIVE: 43 | INTENTIONAL: 3 | REAL-UNVERIFIED: 0

### DVO-1 (Low) -- _consumers.py:117 git error text falls back to stdout, then to a return-code message
**Disposition:** FALSE-POSITIVE -- both operands are text from one subprocess; `git_text(stderr) or git_text(stdout)` is an alternative source and the final `or` only supplies a message when both streams are empty.
**Fix:** none

### DVO-2 (Low) -- _core/config.py:93 `", ".join(gates) or "none"` in the error message
**Disposition:** FALSE-POSITIVE -- the left operand is a computed join of gate names, not a caller value; "none" is display text for an empty list.
**Fix:** none

### DVO-3 (Low) -- _core/config.py:103 `start or Path.cwd()` in find_repo_root
**Disposition:** FALSE-POSITIVE -- `start` is `Optional[Path]` (`_core/config.py:101`) and a `Path` object is always truthy, so only None reaches the fallback. Callers: `pytest_plugin.py:147` passes a Path; `cli.py:64` passes no argument.
**Fix:** none

### DVO-4 (Low) -- adoption_matrix.py:263 `spec.get("rev") or spec.get("tag") or spec.get("branch")`
**Disposition:** FALSE-POSITIVE -- the operands are alternative pin sources in documented precedence (rev, then tag, then branch); an empty string is not a valid pin, so treating it as unset is correct. Caller is `_source_table_pin` itself, reading a `[tool.*]` table from pyproject.
**Fix:** none

### DVO-5 (Low) -- adoption_matrix.py:263 `spec.get("tag") or spec.get("branch")`
**Disposition:** FALSE-POSITIVE -- same expression as above; alternative source, not a default.
**Fix:** none

### DVO-6 (Low) -- adoption_matrix.py:775 `not self.pins_agree or bool(self.moving_pins) or ...` in RepoReport
**Disposition:** FALSE-POSITIVE -- every operand is a boolean; the result is a bool and no fallback value is substituted.
**Fix:** none

### DVO-7 (Low) -- adoption_matrix.py:775 `bool(self.moving_pins) or bool(self.silent_skips)`
**Disposition:** FALSE-POSITIVE -- boolean operands only.
**Fix:** none

### DVO-8 (Low) -- adoption_matrix.py:775 `bool(self.silent_skips) or bool(self.stale_pins)`
**Disposition:** FALSE-POSITIVE -- boolean operands only.
**Fix:** none

### DVO-9 (Low) -- adoption_matrix.py:775 `bool(self.stale_pins) or bool(self.unresolvable_pins)`
**Disposition:** FALSE-POSITIVE -- boolean operands only.
**Fix:** none

### DVO-10 (Low) -- adoption_matrix.py:850 `", ".join(...) or "none"` for distinct refs
**Disposition:** FALSE-POSITIVE -- the left operand is a computed join of the ref list; "none" is display text for an empty set.
**Fix:** none

### DVO-11 (Low) -- adoption_matrix.py:913 `any_finding or bool(found)` in _finding_lines
**Disposition:** FALSE-POSITIVE -- boolean accumulation of a flag; no value is replaced.
**Fix:** none

### DVO-12 (Low) -- adoption_matrix.py:913 `bool(found) or bool(extra)`
**Disposition:** FALSE-POSITIVE -- boolean operands only.
**Fix:** none

### DVO-13 (Low) -- adoption_matrix.py:350 `f"action:{sub or 'root'}"` for `uses:` references
**Disposition:** INTENTIONAL -- `sub` is `(m.group(1) or "").strip("/")`, so an empty value means a local/root `uses:` reference (for example `uses: ./`); the label "root" is a display name only and changes no pin lookup beyond the label.
**Fix:** none

### DVO-14 (Low) -- audit_disposition_parity.py:196 `migrations_dir or (repo_root / "supabase" / "migrations")`
**Disposition:** FALSE-POSITIVE -- the parameter is `Optional[Path]` (`audit_disposition_parity.py:180`); a Path is always truthy, so only None takes the default. The only caller in this file, `find_unsupported_dispositions`'s wrapper at line 231, passes `migrations_dir=migrations_dir` which defaults to None.
**Fix:** none

### DVO-15 (Low) -- baseline_trend.py:199 `args.directories or list(DEFAULT_DIRECTORIES)`
**Disposition:** FALSE-POSITIVE -- `--dir` is `action="append"` with no default (`baseline_trend.py:177-182`), so argparse yields None when absent and a non-empty list otherwise; an empty list cannot be produced from the command line.
**Fix:** none

### DVO-16 (Low) -- baseline_trend.py:201 `args.unmoved or args.stale_days is not None`
**Disposition:** FALSE-POSITIVE -- boolean operands only (`--unmoved` is store_true; the other side is a comparison).
**Fix:** none

### DVO-17 (Low) -- config_getattr_default_parity.py:99 `name in receiver_names or (bool(receiver_suffixes) and name.endswith(...))`
**Disposition:** FALSE-POSITIVE -- boolean membership and suffix test; no default value is substituted.
**Fix:** none

### DVO-18 (Low) -- dart_scanners.py:369 `", ".join(others) or "no Text"`
**Disposition:** FALSE-POSITIVE -- computed join is display text inside a message; "no Text" describes an empty list.
**Fix:** none

### DVO-19 (Low) -- docs_inventory_parity.py:297 `target.endswith("/") or "*" in target or bool(re.search(...))` in _looks_like_path
**Disposition:** FALSE-POSITIVE -- boolean predicate; no default substituted.
**Fix:** none

### DVO-20 (Low) -- docs_inventory_parity.py:297 `"*" in target or bool(re.search(...))`
**Disposition:** FALSE-POSITIVE -- boolean predicate.
**Fix:** none

### DVO-21 (Low) -- edge_function_hygiene.py:191 `statuses or ['(no status, so 200)']` in a message
**Disposition:** FALSE-POSITIVE -- the branch already requires `not statuses or any(...)` (line 188); the `or` only picks the text shown to the reader when the catch block has no status code.
**Fix:** none

### DVO-22 (Low) -- gate_integrity.py:247 `step.get("id") or step.get("name") or f"step{index}"`
**Disposition:** FALSE-POSITIVE -- these are alternative labels for a workflow step (id, then name, then position); used only for the reported label, so an empty id correctly falls through to name and then to position.
**Fix:** none

### DVO-23 (Low) -- gate_integrity.py:247 `step.get("name") or f"step{index}"`
**Disposition:** FALSE-POSITIVE -- same label chain as above.
**Fix:** none

### DVO-24 (Low) -- gate_integrity.py:451 `_invocation_in(wrapper, command) or wrapper in command`
**Disposition:** FALSE-POSITIVE -- both operands are used only for truthiness in a test; the fallback is a plain substring check of the same name.
**Fix:** none

### DVO-25 (Low) -- guard_population.py:103 `unmatched_close or ch == ")"`
**Disposition:** FALSE-POSITIVE -- boolean state latch in the shell reader.
**Fix:** none

### DVO-26 (Low) -- guard_population.py:179 `shutil.which("bash") or "bash"`
**Disposition:** FALSE-POSITIVE -- `shutil.which` returns None or a path string; the fallback is the bare command name, which is the intended PATH lookup.
**Fix:** none

### DVO-27 (Low) -- guard_without_fixture.py:60 `baseline_dir or DEFAULT_BASELINE_DIR`
**Disposition:** FALSE-POSITIVE -- `baseline_dir` is `Optional[str]` (line 54); an empty string would resolve to the repo root itself, never a meaningful baseline directory, so mapping it to the default is safe. Caller not read beyond the signature.
**Fix:** none

### DVO-28 (Low) -- latched_availability_flags.py:100 `aliases or ImportAliases()` in _is_broad
**Disposition:** FALSE-POSITIVE -- `ImportAliases` (`_core/aliases.py:56`) defines neither `__len__` nor `__bool__`, so an instance is always truthy and only None takes the fallback. The caller of `_is_broad` was not read; the verdict does not depend on it.
**Fix:** none

### DVO-29 (Low) -- module_cache_thread_safety.py:183 `"element" in kinds or (...) or (...)` in _is_racy
**Disposition:** FALSE-POSITIVE -- boolean predicate over a set of mutation kinds.
**Fix:** none

### DVO-30 (Low) -- module_cache_thread_safety.py:183 `"insert" in kinds and bool(kinds & {...})`
**Disposition:** FALSE-POSITIVE -- boolean predicate.
**Fix:** none

### DVO-31 (Low) -- module_cache_thread_safety.py:183 `"rebind" in kinds and len(kinds) > 1`
**Disposition:** FALSE-POSITIVE -- boolean predicate.
**Fix:** none

### DVO-32 (Low) -- phantom_markdown_links.py:50 `bool(_SCHEME_RE.match(target)) or target.startswith("//")`
**Disposition:** FALSE-POSITIVE -- boolean predicate for external links.
**Fix:** none

### DVO-33 (Low) -- polars_null_equality.py:112 `_method_call(left, "min") or _method_call(left, "max")`
**Disposition:** FALSE-POSITIVE -- `_method_call` returns `Optional[ast.expr]` (`polars_null_equality.py:69`); an AST node is always truthy, so only None falls through.
**Fix:** none

### DVO-34 (Low) -- polars_null_equality.py:113 `_method_call(right, "max") or _method_call(right, "min")`
**Disposition:** FALSE-POSITIVE -- same as line 112: AST node or None.
**Fix:** none

### DVO-35 (Low) -- resource_leak_guard.py:100 `" ".join(child.cmdline()) or str(child.name())`
**Disposition:** FALSE-POSITIVE -- alternative source: when the process has no command line (empty list, which psutil returns for some processes), the process name is the description.
**Fix:** none

### DVO-36 (Low) -- skip_as_pass.py:106 `baseline_dir or DEFAULT_BASELINE_DIR`
**Disposition:** FALSE-POSITIVE -- `baseline_dir` is `Optional[str]` (line 100); an empty string is not a meaningful directory and would resolve to the repo root. Caller not read beyond the signature.
**Fix:** none

### DVO-37 (Low) -- sql_verify.py:220 `getattr(exc, 'pgcode', None) or 'none'` in a message
**Disposition:** FALSE-POSITIVE -- SQLSTATE is a string or None; the "none" text is display only in the error detail.
**Fix:** none

### DVO-38 (Low) -- sql_verify.py:352 `m.group(1) or f"#{len(numbers)}"` in _prepare_ready
**Disposition:** FALSE-POSITIVE -- `_PYFORMAT_PARAM` is `%\((\w+)\)s|%s` (`sql_verify.py:255`); group 1 is None for the positional `%s` form and is never an empty string because `\w+` needs one character, so the fallback is the intended positional numbering.
**Fix:** none

### DVO-39 (Low) -- stub_signature_guard.py:79 `fnmatch(dotted, p) or fnmatch(last segment, p)`
**Disposition:** FALSE-POSITIVE -- boolean pattern match over two name forms.
**Fix:** none

### DVO-40 (Low) -- unsafe_deserialization.py:175 `per_name or kind == "per_name"` in _admits_whole_modules
**Disposition:** FALSE-POSITIVE -- boolean flag latch.
**Fix:** none

### DVO-41 (Low) -- unsafe_deserialization.py:176 `module_only or kind == "module_only"`
**Disposition:** FALSE-POSITIVE -- boolean flag latch.
**Fix:** none

### DVO-42 (Low) -- unsafe_deserialization.py:179 `resolves or _resolves_module(node, module)`
**Disposition:** FALSE-POSITIVE -- `_resolves_module` returns bool (`unsafe_deserialization.py:155`); boolean latch.
**Fix:** none

### DVO-43 (Low) -- unsafe_deserialization.py:298 `_kw(node, "Loader") or (node.args[1] if len(node.args) > 1 else None)`
**Disposition:** FALSE-POSITIVE -- `_kw` returns `Optional[ast.expr]` (`unsafe_deserialization.py:106`) and the second operand is an AST node; AST nodes are always truthy, so the fallback is taken only when there is no `Loader=` keyword. The parentheses in the source make the precedence the intended one.
**Fix:** none

### DVO-44 (Low) -- vendored_internal_imports.py:144 `", ".join(standalone_names) or "the vendored package"`
**Disposition:** FALSE-POSITIVE -- display text for a computed join; no value is replaced.
**Fix:** none

### DVO-45 (Low) -- vulture_warn.py:81 `options.src_path or os.environ.get("PY_CI_SHARED_SRC_PATH")`
**Disposition:** INTENTIONAL -- the env-var fallback is the documented contract: the `--src-path` help text (`vulture_warn.py:73`) names `env: PY_CI_SHARED_SRC_PATH`, and an empty flag value is treated as unset, which then reaches the "skipping" message at line 82-84 rather than scanning everything.
**Fix:** none

### DVO-46 (Low) -- vulture_warn.py:85 `options.whitelist or os.environ.get("PY_CI_SHARED_VULTURE_WHITELIST")`
**Disposition:** INTENTIONAL -- same documented env fallback, per the `--whitelist` help text (`vulture_warn.py:74`).
**Fix:** none
