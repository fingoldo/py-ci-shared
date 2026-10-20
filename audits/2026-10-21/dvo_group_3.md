# default_via_or audit, group 3 (2026-10-21)

Findings: 45 across 24 files in src/py_ci_shared (group g3).
REAL: 0 | FALSE-POSITIVE: 39 | INTENTIONAL: 6

Method: each flagged line read with its surrounding code; callers grepped inside src/py_ci_shared. No code was run.

### DVO-135 (Low) -- _core/source.py:103 SyntaxError.msg fallback in parse error
**Disposition:** FALSE-POSITIVE -- `exc.msg` is a display string from SyntaxError and is never empty in practice; `or "invalid syntax"` only fills the message in the error text.
**Fix:** none

### DVO-136 (Low) -- _scaffold.py:263 empty summary replaced by TODO placeholder
**Disposition:** INTENTIONAL -- `new_gate(summary="")` is the documented default and the `TODO` line is the scaffold's placeholder; the only caller, `cli.py:141`, passes `args.summary or ""`, so an empty summary is always meant to become the placeholder.
**Fix:** none

### DVO-137 (Low) -- api_floor.py:72 second regex tried when first misses
**Disposition:** FALSE-POSITIVE -- right operand is an alternative pattern (`~=` form), not a default; the left operand is a `re.Match` or None.
**Fix:** none

### DVO-138 (Low) -- api_floor.py:313 explicit target falls back to requires-python
**Disposition:** FALSE-POSITIVE -- `target` is `Optional[str]` (line 307); empty string is not a meaningful floor, so the alternative source (`requires-python` of pyproject) is the intended second source.
**Fix:** none

### DVO-139 (Low) -- ci_install_covers_conftest.py:444 empty version list becomes [None]
**Disposition:** INTENTIONAL -- `_versions` returning no matrix values means "run once, unversioned"; `[None]` is that sentinel, the same role it plays for a job with no matrix.
**Fix:** none

### DVO-140 (Low) -- ci_install_covers_conftest.py:730 no testpaths resolve, fall back to run cwd
**Disposition:** INTENTIONAL -- inside `if not targets:`; when no ini testpath exists the run's own working directory is the documented search root for pytest, so `[run.cwd.resolve()]` is the intended fallback.
**Fix:** none

### DVO-141 (Low) -- ci_install_covers_conftest.py:830 boolean confidence OR
**Disposition:** FALSE-POSITIVE -- both operands are booleans; `or` is a union of two detection signals, not a default.
**Fix:** none

### DVO-142 (Low) -- ci_install_covers_conftest.py:648 job defaults then workflow defaults for working-directory
**Disposition:** FALSE-POSITIVE -- `_working_directory` returns a str ("" when unset, line 625-627); the right operand is the workflow-level value, an alternative source consulted only when the job sets none.
**Fix:** none

### DVO-143 (Low) -- ci_install_covers_conftest.py:486 step working-directory falls back to workdir then "."
**Disposition:** FALSE-POSITIVE -- an empty `working-directory:` in a workflow YAML means unset in GitHub Actions; `"."` is the equivalent cwd, so the fallback is the same meaning.
**Fix:** none

### DVO-144 (Low) -- ci_install_covers_entry_imports.py:256 job defaults then workflow defaults for working-directory
**Disposition:** FALSE-POSITIVE -- same pattern and same helper as ci_install_covers_conftest.py:648 (`_working_directory` returns "" when unset); the right operand is an alternative source.
**Fix:** none

### DVO-145 (Low) -- cli.py:113 default entry name in catalogue
**Disposition:** FALSE-POSITIVE -- `default_entry` is `Optional[str]` from `entries[0]` (registry.py:68), and entry names are never empty; the `or` is the display placeholder for gates without an entry, with parentheses making the precedence correct.
**Fix:** none

### DVO-146 (Low) -- cli.py:59 stream encoding fallback to ascii
**Disposition:** FALSE-POSITIVE -- `stream.encoding` is an encoding name or None; an empty encoding is not a valid value, so "ascii" fills only the missing case.
**Fix:** none

### DVO-177 (Low) -- commit_metadata.py:172 rev_range default to upstream range
**Disposition:** FALSE-POSITIVE -- `rev_range` is a git revision-range string; "" is not a meaningful range, and the documented default (line 171, "commits ahead of the upstream") applies; the caller in `main` (line 207) also treats "" as unset.
**Fix:** none

### DVO-178 (Low) -- commit_metadata.py:207 CLI rev_range then config then default
**Disposition:** FALSE-POSITIVE -- `args.rev_range` (CLI) and `config.get("rev_range")` are string sources; "" is not a valid revision range, so falling through to the next source is intended.
**Fix:** none

### DVO-179 (Low) -- commit_metadata.py:207 config rev_range then default
**Disposition:** FALSE-POSITIVE -- same as 207a; config value is a revision-range string where "" means unset.
**Fix:** none

### DVO-147 (Low) -- constant_relations.py:181 toml root defaults to "."
**Disposition:** FALSE-POSITIVE -- `Path("") == Path(".")`, so `root or "."` yields the identical path; behaviour is unchanged for an empty root.
**Fix:** none

### DVO-148 (Low) -- coverage_config_parity.py:90 pytest testpaths default set
**Disposition:** INTENTIONAL -- `_testpaths` (line 81) returns the configured testpaths, and when pyproject declares none the set `{"tests", "test"}` is the fallback used by `_narrow_findings` at line 332; the empty set means "not configured", so the default is the designed behaviour.
**Fix:** none

### DVO-149 (Low) -- coverage_config_parity.py:293 numba source dirs default to repo root
**Disposition:** INTENTIONAL -- `source_dirs` defaults to `()` (line 342 signature) and `_collect` forwards it unchanged (line 333); an empty sequence means "no source dirs given", so scanning the repo root is the documented default.
**Fix:** none

### DVO-150 (Low) -- coverage_config_parity.py:201 stripped path defaults to "."
**Disposition:** FALSE-POSITIVE -- `"".strip("./")` gives an empty path meaning the working directory, which `"."` names exactly; no behaviour change.
**Fix:** none

### DVO-151 (Low) -- coverage_config_parity.py:285 NUMBA_DISABLE_JIT in step or invocation env
**Disposition:** FALSE-POSITIVE -- both operands are booleans from `_env_true`; `or` combines two env sources.
**Fix:** none

### DVO-152 (Low) -- dup_baseline_percent_key.py:91 baseline_dir default
**Disposition:** FALSE-POSITIVE -- `baseline_dir` is an Optional path string (line 79); an empty directory name is not a meaningful baseline location, so the default `DEFAULT_BASELINE_DIR` applies. No caller in the package passes "".
**Fix:** none

### DVO-153 (Low) -- git_child_env.py:162 baseline_dir default
**Disposition:** FALSE-POSITIVE -- same as dup_baseline_percent_key-91: `Optional[str]` path, "" not meaningful, default is `DEFAULT_BASELINE_DIR` (`_text_ratchet.py:23`).
**Fix:** none

### DVO-154 (Low) -- gpu_timing_sync.py:152 sync-word check
**Disposition:** FALSE-POSITIVE -- boolean `or` between "a single word" and "a device word intersects"; no default value.
**Fix:** none

### DVO-155 (Low) -- gpu_timing_sync.py:264 GPU calls then injected calls as the timed work
**Disposition:** FALSE-POSITIVE -- `gpu_calls` is a list; the right operand is an alternative source of timed work (injected timer-visible calls, only when `gpu_aware`), used when no GPU call was found in the region, not a default for a falsy-but-meaningful value.
**Fix:** none

### DVO-156 (Low) -- import_side_effects.py:421 attributed chain name or "?"
**Disposition:** FALSE-POSITIVE -- `_attributed` returns a name or None for display; "?" is the message placeholder and an empty name is not meaningful.
**Fix:** none

### DVO-157 (Low) -- marker_runner_coverage.py:448 boolean OR in evaluator
**Disposition:** FALSE-POSITIVE -- `_evaluate` of an `or` node over boolean subtrees; not a default.
**Fix:** none

### DVO-158 (Low) -- marker_runner_coverage.py:486 runner paths default to "."
**Disposition:** INTENTIONAL -- a runner with no paths runs from its cwd, which `"."` names; `_anchored` (line 480) relies on this to produce a root-relative path for every runner, and the empty tuple is never meaningful as a path list.
**Fix:** none

### DVO-159 (Low) -- patch_target_read_at_call.py:140 dynamic_namespace flag accumulation
**Disposition:** FALSE-POSITIVE -- boolean accumulation (`flag or node.id in set`); not a default.
**Fix:** none

### DVO-160 (Low) -- persisted_negative_probe.py:147 qualified name or dotted name
**Disposition:** FALSE-POSITIVE -- `aliases.qualified_name` returns a str or None; `_dotted` is an alternative resolver for the same call target.
**Fix:** none

### DVO-161 (Low) -- persisted_negative_probe.py:115 qualified name or dotted name for handler types
**Disposition:** FALSE-POSITIVE -- same resolver fallback as persisted_negative_probe-147.
**Fix:** none

### DVO-162 (Low) -- registry.py:65 tests default to convention path
**Disposition:** FALSE-POSITIVE -- `tests` defaults to `()` (registry.py:60 field default); an empty tuple means "no explicit files", and the convention path is the designed fallback.
**Fix:** none

### DVO-163 (Low) -- secret_assertion_operands.py:233 module-level label
**Disposition:** FALSE-POSITIVE -- `qual` is a function qualname or None/""; `"<module>"` is the label for module-level code, a display value.
**Fix:** none

### DVO-164 (Low) -- single_shot_timing_assertion.py:325 scale factor from either operand
**Disposition:** FALSE-POSITIVE -- `factor` (line 308-313) returns only a strictly positive float or None, so `0.0` is never returned and `or` cannot skip a meaningful zero; the right operand is the other side of the comparison.
**Fix:** none

### DVO-165 (Low) -- single_shot_timing_assertion.py:277 calibrated helper detection
**Disposition:** FALSE-POSITIVE -- boolean `or` between two detection branches.
**Fix:** none

### DVO-166 (Low) -- single_shot_timing_assertion.py:319 plain operand detection
**Disposition:** FALSE-POSITIVE -- boolean `or` between two symmetric shapes of a multiplication.
**Fix:** none

### DVO-167 (Low) -- single_shot_timing_assertion.py:395 exempt propagation into class body
**Disposition:** FALSE-POSITIVE -- `exempt` is a bool and `_marked` returns a bool; `or` propagates the exemption.
**Fix:** none

### DVO-168 (Low) -- source_text_ban.py:133 mention check
**Disposition:** FALSE-POSITIVE -- boolean `or` between a path regex and name regexes.
**Fix:** none

### DVO-169 (Low) -- source_text_ban.py:179 offending line check
**Disposition:** FALSE-POSITIVE -- boolean `or` between two pattern sets.
**Fix:** none

### DVO-170 (Low) -- statement_columns_exist_in_ddl.py:440 open table flag
**Disposition:** FALSE-POSITIVE -- boolean `or` (`open_table` already a bool from `_create_columns`, plus an INHERITS check).
**Fix:** none

### DVO-171 (Low) -- statement_columns_exist_in_ddl.py:594 summary text "none"
**Disposition:** FALSE-POSITIVE -- display placeholder for an empty join; not a value passed in.
**Fix:** none

### DVO-172 (Low) -- statement_columns_exist_in_ddl.py:908 "its files" placeholder in message
**Disposition:** FALSE-POSITIVE -- display placeholder in a finding message when no creating file is recorded.
**Fix:** none

### DVO-173 (Low) -- statement_compilation.py:84 object placeholder in dotted name
**Disposition:** FALSE-POSITIVE -- `_dotted(first)` is None when the receiver is not a name chain; `"<object>"` is the display placeholder.
**Fix:** none

### DVO-174 (Low) -- unresolved_imports.py:429 bare except counts as import-failure handler
**Disposition:** FALSE-POSITIVE -- boolean `or` between "no exception type" and "type names intersect the import error set".
**Fix:** none

### DVO-175 (Low) -- version_tag_currency.py:273 lock ref placeholder in message
**Disposition:** FALSE-POSITIVE -- display placeholder ("no ref") in a message string; the value is not used for logic.
**Fix:** none

### DVO-176 (Low) -- version_tag_currency.py:282 resolved placeholder in message
**Disposition:** FALSE-POSITIVE -- display placeholder ("nothing") in a message string; the value is not used for logic.
**Fix:** none
