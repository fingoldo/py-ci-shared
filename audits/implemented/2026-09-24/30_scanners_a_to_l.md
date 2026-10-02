# Audit: scanner correctness, modules a..l

Read-only audit of `src/py_ci_shared/[a-l]*.py` (skipping mutation_teeth.py and _mutation_worker.py): 61 modules, about 11.5k LOC.

- No source was edited and nothing was committed.
- Repros are in the session scratchpad (`g1`..`g5`), run with Python 3.14.3.
- Verified column: **repro** = shown by a scratch script; **read-only** = confirmed only by reading the code.
- Paths are relative to `src/py_ci_shared/`.

## Cross-cutting classes

1. **BOM fail-open.** Many modules read with `encoding="utf-8"` and then call `ast.parse` on the text, which raises on a leading U+FEFF. Most modules catch that and skip the file silently; llm_call_archive_gate and deletion_gates crash instead. Affected: G3-04/05/18, G4-01/10, G5-13/14/19/22/27/29/49, G2-19, G1-03.
2. **Pass when nothing was scanned.** A missing dir, a typo'd path, or an empty file list makes the gate return clean. Affected: G1-25/32/36, G2-12, G3-01/20, G4-06/21/32, G5-48, and loc_budget.
3. **A missing baseline seeds itself and skips (green) instead of failing.** Affected: code_audit_meta G2-30, content_hash G2-34, deferred_drift G3-13, loc_budget.
4. **Refresh flags are read from `sys.argv`, which is `['-c']` in xdist workers**, so refresh is silently ignored under `-n`. Affected: G2-33, G3-14, G5-25.
5. **Unparseable (SyntaxError) files are skipped with no report.** Affected: G1-08/18, G4-09/15/41, G5-42.
6. **Test gaps.** No test file exists for dataclass_case_completeness, fail_message_quality, advisory_warn, bandit_warn or config_drift_check. Two tests enshrine fail-open behaviour: `test_edge_function_hygiene.py:119` and `test_deferred_drift.py:43`. One test enshrines a weak check: `test_ci_workflow_timeout_gate::test_step_level_timeout_still_satisfies_the_check`.

## Findings

### GA-1 (High) -- =1 is treated as covering the whole one category, but in ru/uk/pl one also contains 21,...

**Disposition:** RESOLVED -- exact selectors now cover a category only when the locale's category is a finite integer set they enumerate (`FINITE_CATEGORY_VALUES`: `one`={1} for en/de/es/it/nl/tr/cs/pl, {0,1} for fr/pt, ar zero/one/two); ru/uk `one` is never covered by `=1`; regression test: test_exact_one_does_not_cover_russian_one_which_also_holds_21, test_exact_one_covers_polish_one_but_french_one_also_needs_zero

- **Original id:** G1-01
- **Where:** arb_checks.py:76,168
- **Finding:** `=1` is treated as covering the whole `one` category, but in ru/uk/pl `one` also contains 21, 31..., and in fr it contains 0
- **Failing input:** ru `{count, plural, =1{..} few{..} many{..} other{..}}` passes, but 21 renders `other`
- **Proposed fix:** Map `=N` to a category only for locales where that category is exactly {N}
- **Verified:** repro

### GA-2 (Med) -- The branch-head regex also scans branch bodies, so it finds fake branches

**Disposition:** RESOLVED -- `_plural_branches` finds the `{arg, plural,` head and reads selectors only at its top level with a depth counter, so text inside a body is never a branch; regression test: test_a_word_ending_in_one_inside_a_body_is_not_a_branch

- **Original id:** G1-02
- **Where:** arb_checks.py:70,121
- **Finding:** The branch-head regex also scans branch bodies, so it finds fake branches
- **Failing input:** `other{someone {name}}` yields branches {other, one}, which masks a missing `one`
- **Proposed fix:** Parse top-level heads with a depth counter
- **Verified:** repro

### GA-3 (Med) -- ARB files are read as utf-8, so a BOM causes a JSONDecodeError crash

**Disposition:** RESOLVED -- ARB files are read with `_core.read_source` (utf-8-sig for non-Python files); regression test: test_a_bom_arb_file_is_read

- **Original id:** G1-03
- **Where:** arb_checks.py:92
- **Finding:** ARB files are read as utf-8, so a BOM causes a JSONDecodeError crash
- **Failing input:** ARB file with a BOM
- **Proposed fix:** Read with `utf-8-sig`
- **Verified:** repro

### GA-4 (Low) -- The ICU # placeholder is not accepted (false positive)

**Disposition:** RESOLVED -- a branch containing the ICU `#` counts as carrying the number; regression test: test_the_icu_hash_placeholder_counts_as_the_number

- **Original id:** G1-04
- **Where:** arb_checks.py:176-181
- **Finding:** The ICU `#` placeholder is not accepted (false positive)
- **Failing input:** `one{# day} other{# days}`
- **Proposed fix:** Accept `#`
- **Verified:** repro

### GA-5 (Low) -- A missing template locale raises a bare KeyError

**Disposition:** RESOLVED -- a missing template locale raises `TemplateLocaleError` (a `KeyError` subclass, so existing handlers still work) naming the locale and the catalogue keys; regression test: test_a_missing_template_locale_names_the_catalogues

- **Original id:** G1-05
- **Where:** arb_checks.py:97
- **Finding:** A missing template locale raises a bare KeyError
- **Failing input:** `{"en_US":..}` with the default `template_locale="en"`
- **Proposed fix:** Fail with a clear message
- **Verified:** read-only

### GA-6 (Low) -- The "plural" in value substring test skips the counted-phrase rule

**Disposition:** RESOLVED -- plural detection matches `,\s*plural\s*,` instead of the substring `plural`, in both the counted-phrase rule and the branch check; regression test: test_the_word_plural_in_text_does_not_exempt_a_counted_phrase

- **Original id:** G1-06
- **Where:** arb_checks.py:141
- **Finding:** The `"plural" in value` substring test skips the counted-phrase rule
- **Failing input:** `"{count} plural forms"`
- **Proposed fix:** Match `,\s*plural\s*,`
- **Verified:** read-only

### GA-7 (Med) -- Only a positional str constant is detected; f-strings, concatenation, variables and sql...

**Disposition:** RESOLVED -- every string that flows into a call argument or keyword (constants, f-string parts, concatenations, `sqltext=`, and names bound to such strings anywhere in the file) is searched for CONCURRENTLY; nested calls are judged on their own; regression test: test_every_spelling_of_the_sql_is_seen, test_a_non_concurrent_computed_sql_is_not_flagged

- **Original id:** G1-07
- **Where:** alembic_concurrently.py:31
- **Finding:** Only a positional str constant is detected; f-strings, concatenation, variables and `sqltext=` are missed
- **Failing input:** `op.execute(f"CREATE INDEX CONCURRENTLY {n}")` gives []
- **Proposed fix:** Walk every Constant/JoinedStr under the call, including keywords
- **Verified:** repro

### GA-8 (Med) -- Unparseable or non-UTF8 migrations are skipped (fail-open)

**Disposition:** RESOLVED -- migrated to `_core.scan_python`; unreadable/unparsable migrations appear in `find_unguarded_concurrently` output as `file:line: unparsable: ...` and fail `assert_concurrently_is_in_autocommit_blocks`; regression test: test_an_unparsable_migration_is_reported_not_skipped

- **Original id:** G1-08
- **Where:** alembic_concurrently.py:64-65
- **Finding:** Unparseable or non-UTF8 migrations are skipped (fail-open)
- **Failing input:** unclosed-paren migration gives set()
- **Proposed fix:** Report parse failures
- **Verified:** repro

### GA-9 (Low) -- async with autocommit_block() is not a guard (false positive)

**Disposition:** RESOLVED -- `async with ...autocommit_block()` is a guard; regression test: test_async_with_and_bare_name_autocommit_block_are_guards

- **Original id:** G1-09
- **Where:** alembic_concurrently.py:47
- **Finding:** `async with autocommit_block()` is not a guard (false positive)
- **Failing input:** async env migration
- **Proposed fix:** Handle AsyncWith
- **Verified:** repro

### GA-10 (Low) -- The guard is lexical: a def nested in the with but called later passes; a bare-Name aut...

**Disposition:** RESOLVED -- the guard stack resets at every def/async def/lambda/class; a bare-name `autocommit_block()` is accepted; `postgresql_concurrently` counts when truthy or computed; regression test: test_a_def_nested_in_the_block_is_not_guarded_by_it, test_async_with_and_bare_name_autocommit_block_are_guards, test_every_spelling_of_the_sql_is_seen

- **Original id:** G1-10
- **Where:** alembic_concurrently.py:34-39,49
- **Finding:** The guard is lexical: a def nested in the `with` but called later passes; a bare-Name `autocommit_block()` and `postgresql_concurrently=1` are missed
- **Failing input:** `with c.autocommit_block(): def later(): op.execute("DROP INDEX CONCURRENTLY x")`
- **Proposed fix:** Reset the guard at FunctionDef; accept Name and truthy values
- **Verified:** repro

### GA-11 (Low) -- Baseline key file:line breaks on any edit above the call

**Disposition:** RESOLVED -- the baseline is now a `_core.Baseline` (multiset, missing file fails, refresh via `--refresh-alembic-concurrently-baseline` / `PY_CI_SHARED_REFRESH`) keyed on file name plus the normalised call source, never the line; existing JSON-list baselines still load (their line keys go stale once and are rewritten by a refresh); regression test: test_baseline_keys_survive_an_edit_above_the_call, test_a_missing_baseline_fails_and_a_refresh_writes_it

- **Original id:** G1-11
- **Where:** alembic_concurrently.py:66
- **Finding:** Baseline key `file:line` breaks on any edit above the call
- **Failing input:** insert an import line
- **Proposed fix:** Key on file plus normalised call text
- **Verified:** read-only

### GA-12 (Med) -- The verdict regex misses common spellings; PARTIALLY RESOLVED parses as PARTIALLY

**Disposition:** RESOLVED -- the disposition line regex accepts any list marker, bold before or after the colon, and any case; the verdict is read against a longest-first alias table (`PARTIALLY RESOLVED` -> PARTIAL, `won't fix`/curly apostrophe/`WONTFIX` -> WON'T FIX), with the old upper-case-run fallback for unknown verdicts; new public `parse_disposition`; audit files are read with `_core.read_source` (BOM stripped, undecodable file reported instead of lossy decode); regression test: test_common_disposition_spellings_are_read, test_multi_word_verdicts_are_normalised, test_a_deferred_spelled_in_lower_case_is_still_not_checked, test_a_bom_audit_file_is_read

- **Original id:** G1-12
- **Where:** audit_disposition_parity.py:44
- **Finding:** The verdict regex misses common spellings; `PARTIALLY RESOLVED` parses as `PARTIALLY`
- **Failing input:** `**Disposition**: RESOLVED`, `Disposition: Resolved` give None
- **Proposed fix:** Loosen the markup, add `re.I`, normalise multi-word verdicts
- **Verified:** repro

### GA-13 (Med) -- Backslash paths are ignored; absolute or .

**Disposition:** RESOLVED -- backticked paths may use backslashes (normalised to `/`); absolute, drive-letter and `..` paths are reported as unverifiable (outside the repository) instead of being resolved against the real filesystem, unless listed in `ignore_paths`; regression test: test_a_backslash_path_is_normalised_and_checked, test_a_path_outside_the_repository_is_not_checked_against_the_filesystem

- **Original id:** G1-13
- **Where:** audit_disposition_parity.py:46-48
- **Finding:** Backslash paths are ignored; absolute or `..` paths are checked outside the repo
- **Failing input:** `` `lib\foo\bar.dart` `` is not extracted; `` `/etc/hosts.txt` `` is extracted and checked against the real filesystem
- **Proposed fix:** Normalise `\`; reject absolute and `..` paths
- **Verified:** repro

### GA-14 (Low) -- A reversed migration range expands to nothing

**Disposition:** RESOLVED -- a migration range expands from min to max whatever the order; regression test: test_a_reversed_migration_range_expands

- **Original id:** G1-14
- **Where:** audit_disposition_parity.py:50,66
- **Finding:** A reversed migration range expands to nothing
- **Failing input:** `migrations 039-034`
- **Proposed fix:** `range(min, max+1)`
- **Verified:** repro

### GA-15 (Low) -- The :\d+ strip is dead code (the regex cannot capture :)

**Disposition:** RESOLVED -- the finding understated it: because the backtick regex could not match `:`, a backticked `lib/app.dart:219` was never extracted at all, so a missing file with a line reference passed. The backtick regex now accepts an optional `:line`/`:start-end` suffix outside the captured path, and the dead strip is removed; regression test: test_a_line_reference_inside_backticks_does_not_hide_the_path

- **Original id:** G1-15
- **Where:** audit_disposition_parity.py:60
- **Finding:** The `:\d+` strip is dead code (the regex cannot capture `:`)
- **Failing input:** n/a
- **Proposed fix:** Remove it, or allow `:\d+` in the regex
- **Verified:** read-only

### GA-16 (Med) -- The ratchet key splits on the first :; with root=None on Windows every key becomes C:<v...

**Disposition:** RESOLVED -- hits are `RoundHit(rel, line, value)` records and the ratchet key is built from `(rel, value)` directly, never by splitting a rendered string on `:`; regression test: test_two_files_with_the_same_literal_are_two_keys_even_without_root

- **Original id:** G1-16
- **Where:** audit_path_references.py:151
- **Finding:** The ratchet key splits on the first `:`; with root=None on Windows every key becomes `C:<value>`, so one `known` entry masks all files
- **Failing input:** two files with the same literal collapse to one key
- **Proposed fix:** Build keys from (rel, value)
- **Verified:** repro

### GA-17 (Med) -- Round names in os.path.join/Path args, + concatenation and f-strings are missed

**Disposition:** RESOLVED -- `os.path.join`/`Path(...)`-family arguments are path segments like the parts of an `a / b` chain (so `implemented` beside them still exempts), and outermost `+` concatenations and f-strings are folded (`{}` for computed pieces) and checked both for `audits/<round>` and for an own open round segment next to `audits` or an audit-file segment; regression test: test_a_round_built_in_pieces_is_reported, test_a_closed_or_data_round_built_in_pieces_is_not

- **Original id:** G1-17
- **Where:** audit_path_references.py:41-83,111
- **Finding:** Round names in os.path.join/Path args, `+` concatenation and f-strings are missed
- **Failing input:** `os.path.join(ROOT,"audits","2026-09-03","x")`, `f"{R}/2026-09-03/x.md"` give []
- **Proposed fix:** Treat join/Path args as path segments; fold BinOp and JoinedStr
- **Verified:** repro

### GA-18 (Low) -- Parse failures are skipped; a NUL byte raises ValueError (py<3.12)

**Disposition:** RESOLVED -- migrated to `_core.scan_python`; unparsable files (including NUL bytes on every Python version) are reported as `path:line: unparsable: ...` and always fail the assert; the `min_files` floor counts parsed files; regression test: test_an_unparsable_file_is_reported_and_fails_the_assert

- **Original id:** G1-18
- **Where:** audit_path_references.py:116
- **Finding:** Parse failures are skipped; a NUL byte raises ValueError (py<3.12)
- **Failing input:** a .py file with a syntax error
- **Proposed fix:** Report parse failures
- **Verified:** read-only

### GA-19 (Med) -- A row shorter than the status column is dropped, neither open nor closed

**Disposition:** RESOLVED -- a tracker row shorter than the status column is kept with an empty status, so it counts as open instead of disappearing; regression test: test_a_row_shorter_than_the_status_column_is_an_open_row

- **Original id:** G1-19
- **Where:** audit_round_format.py:233
- **Finding:** A row shorter than the status column is dropped, neither open nor closed
- **Failing input:** `\| A-2 \|` under an `ID\|Status` header
- **Proposed fix:** Treat it as open with an empty status
- **Verified:** repro

### GA-20 (Med) -- The status mention check is case-sensitive

**Disposition:** RESOLVED -- the status mention check is case-insensitive and the bold form is matched in any case, so `| Resolved |` is reported as not bold and `**Deferred**` as a word outside the list; regression test: test_a_status_mention_in_any_case_must_be_the_bold_upper_form

- **Original id:** G1-20
- **Where:** audit_round_format.py:52
- **Finding:** The status mention check is case-sensitive
- **Failing input:** `\| Resolved \| P1 \|` gives no problem
- **Proposed fix:** Add `re.I`
- **Verified:** repro

### GA-21 (Low) -- finding_problems re-reads every sibling .md for each file: O(n^2)

**Disposition:** RESOLVED -- `finding_problems` takes an optional `dir_cache`; `assert_rounds_countable` shares one, so each round's siblings are read once per round instead of once per file; regression test: test_sibling_files_are_read_once_per_round_not_once_per_file

- **Original id:** G1-21
- **Where:** audit_round_format.py:117
- **Finding:** `finding_problems` re-reads every sibling .md for each file: O(n^2)
- **Failing input:** a 50-file round means 2500 reads
- **Proposed fix:** Cache per directory
- **Verified:** read-only

### GA-22 (Low) -- is_whole_file_read rejects any [ in the expression

**Disposition:** RESOLVED -- `is_whole_file_read` judges the expression tree: method calls on the reader result are unwrapped, only a subscript applied to the result narrows it, and a subscript inside the reader's own arguments does not; regression test: test_a_subscript_inside_the_reader_call_is_still_a_whole_file_read

- **Original id:** G1-22
- **Where:** audit_round_format.py:326
- **Finding:** `is_whole_file_read` rejects any `[` in the expression
- **Failing input:** `"x" not in src(FILES[0])`
- **Proposed fix:** Look for `[` only after the reader call's closing paren
- **Verified:** repro

### GA-23 (Low) -- ~~~ fences are ignored; a BOM hides the header row

**Disposition:** RESOLVED -- `without_fenced_blocks` handles both backtick and tilde fences and closes a fence only on its own marker; every Markdown/verifier read goes through `_core.read_source` (BOM stripped); regression test: test_a_tilde_fence_hides_a_quoted_heading, test_a_bom_does_not_hide_the_tracker_header_row

- **Original id:** G1-23
- **Where:** audit_round_format.py:86
- **Finding:** `~~~` fences are ignored; a BOM hides the header row
- **Failing input:** a tilde-fenced `### X-1 (P2)`
- **Proposed fix:** Handle `~~~`; read with `utf-8-sig`
- **Verified:** read-only

### GA-24 (Low) -- min_trackers counts undated directories that the check then ignores

**Disposition:** RESOLVED -- the `min_trackers` count applies the same dated-directory filter as the check; regression test: test_min_trackers_counts_only_dated_rounds

- **Original id:** G1-24
- **Where:** audit_round_format.py:300
- **Finding:** `min_trackers` counts undated directories that the check then ignores
- **Failing input:** only `audits/misc/TRACKER.md`
- **Proposed fix:** Apply the `_DATED` filter to the count
- **Verified:** read-only

### GA-25 (Med) -- A missing or empty tests_dir passes; patterns are case-sensitive

**Disposition:** RESOLVED -- enumeration moved to `_core.iter_files` (a missing tests dir raises `CorpusError`); the assert fails when fewer than `min_files` (default 1) test files were scanned; stem patterns match case-insensitively; the baseline is a `_core.Baseline` (missing file fails, refresh via `--refresh-audit-wave-filenames-baseline`/`PY_CI_SHARED_REFRESH`), `write_baseline` is atomic; regression test: test_a_missing_or_empty_tests_dir_is_not_a_pass, test_patterns_ignore_case, test_a_missing_baseline_file_fails_and_a_refresh_writes_it

- **Original id:** G1-25
- **Where:** audit_wave_filenames.py:47,80
- **Finding:** A missing or empty tests_dir passes; patterns are case-sensitive
- **Failing input:** `Path("nope")` gives []; `test_Wave97_x.py` is missed
- **Proposed fix:** Fail when nothing was scanned; add `re.I`
- **Verified:** repro

### GA-26 (Med) -- A trailing --src-path raises IndexError, so exit 1 blocks the commit (the docstring say...

**Disposition:** RESOLVED -- both wrappers are now `main(argv)` functions; the shared `bandit_warn.pop_option` bounds-checks (a trailing `--src-path` warns and is ignored, exit 0) and accepts `--src-path=X`; advisory_warn reuses it; regression test: test_a_trailing_src_path_does_not_crash_the_hook (both test files), test_pop_option_reads_both_forms_and_a_missing_value, test_equals_form_scopes_the_scan

- **Original id:** G1-26
- **Where:** bandit_warn.py:26; advisory_warn.py:24
- **Finding:** A trailing `--src-path` raises IndexError, so exit 1 blocks the commit (the docstring says "never blocks")
- **Failing input:** `bandit_warn.py a.py --src-path`
- **Proposed fix:** Bounds-check; accept `--src-path=X`
- **Verified:** repro

### GA-27 (Med) -- --src-path is not backslash-normalised, so it silently scans nothing; a substring prefi...

**Disposition:** RESOLVED -- `select_src_files` normalises backslashes and `./` on both sides and matches whole `/`-separated segments (so `src/pkg` never matches `src/pkg_other`); when .py files were staged and none matched, a warning names the src path; regression test: test_a_backslash_src_path_matches_and_a_sibling_prefix_does_not, test_a_windows_src_path_runs_bandit, test_staged_files_outside_the_src_path_warn_that_nothing_was_scanned, test_a_sibling_prefix_is_not_the_src_path

- **Original id:** G1-27
- **Where:** bandit_warn.py:34; advisory_warn.py:32
- **Finding:** `--src-path` is not backslash-normalised, so it silently scans nothing; a substring prefix matches `src/pkg_other`
- **Failing input:** `--src-path 'src\pkg' 'src\pkg\a.py'` exits 0 and bandit never runs
- **Proposed fix:** Normalise both sides; prefix match on `/`; warn when 0 files match
- **Verified:** repro

### GA-28 (Low) -- pip-audit runs (network) even when no .py is staged

**Disposition:** RESOLVED -- advisory_warn returns before any tool (pip-audit included) when no staged source file is in scope; regression test: test_no_staged_source_file_runs_nothing_not_even_pip_audit, test_a_staged_source_file_runs_ruff_twice_and_pip_audit_once

- **Original id:** G1-28
- **Where:** advisory_warn.py:57
- **Finding:** pip-audit runs (network) even when no .py is staged
- **Failing input:** a README-only commit
- **Proposed fix:** Skip when there are no files
- **Verified:** read-only

### GA-29 (Med) -- The {"entries":[...]} shape is read as the single key entries, so real entries are neve...

**Disposition:** RESOLVED -- `_entries` unwraps `accepted` or `entries` whether it holds a list or a map, reads the note out of `_core.Baseline` `{"count", "note"}` records, and reads a bare top-level list; the file is read with `_core.read_source`; regression test: test_the_entries_list_and_core_record_shapes_are_checked

- **Original id:** G1-29
- **Where:** baseline_hygiene.py:58-64
- **Finding:** The `{"entries":[...]}` shape is read as the single key `entries`, so real entries are never checked
- **Failing input:** `{"entries":["a.dart",...]}` gives []
- **Proposed fix:** Handle the entries list/dict
- **Verified:** repro

### GA-30 (Med) -- The absolute-path regex misses /opt, /tmp, /root, /github/workspace, UNC paths and othe...

**Disposition:** RESOLVED -- a key with any leading `/`, `\` (UNC) or drive letter is flagged; inside prose, any drive letter, a UNC share or a well-known root (/opt, /tmp, /root, /github/workspace, /var, /usr, ...) is flagged, while `read/write` or an `/api/v1` route in a note is not; regression test: test_every_absolute_key_is_flagged, test_a_relative_key_with_slashes_in_its_note_is_not_absolute

- **Original id:** G1-30
- **Where:** baseline_hygiene.py:41
- **Finding:** The absolute-path regex misses /opt, /tmp, /root, /github/workspace, UNC paths and other drive letters
- **Failing input:** key `/opt/app/src/x.py`
- **Proposed fix:** Flag any leading `/`, `\\` or `X:[\\/]`
- **Verified:** repro

### GA-31 (Low) -- Words are counted with [A-Za-z], so a non-Latin note fails

**Disposition:** RESOLVED -- note words are counted with a Unicode letter class; regression test: test_a_note_in_another_script_counts_its_words

- **Original id:** G1-31
- **Where:** baseline_hygiene.py:113
- **Finding:** Words are counted with `[A-Za-z]`, so a non-Latin note fails
- **Failing input:** a Cyrillic note
- **Proposed fix:** Use `[^\W\d_]{2,}`
- **Verified:** repro

### GA-32 (Med) -- No floor on found: a broken scanner returning {} exits 0

**Disposition:** RESOLVED -- `Baseline.enforce` takes `min_found` (explicit floor) and, by default, fails when the scan returns nothing while the baseline accepts entries (every entry stale at once reads as a scan that stopped matching; regenerating records a genuine full payoff); `fail_on_empty_scan=False` opts out for scans that can legitimately clear everything; `run_rules` takes per-rule `min_found`; the baseline file is read as utf-8-sig; regression test: test_an_empty_scan_against_a_non_empty_baseline_fails, test_min_found_is_a_floor_on_the_scan, test_run_rules_passes_per_rule_floors, test_an_empty_scan_may_opt_out_of_the_floor (test_paid_debt_is_reported_but_does_not_fail re-framed to a partial payoff)

- **Original id:** G1-32
- **Where:** baseline_ratchet.py:104-127
- **Finding:** No floor on `found`: a broken scanner returning {} exits 0
- **Failing input:** a glob that stops matching
- **Proposed fix:** Add `min_found`, or fail when every entry is stale
- **Verified:** read-only

### GA-33 (Low) -- A raising scan aborts run_rules; scans without a rule are never enforced

**Disposition:** RESOLVED -- `run_rules` catches an exception per scan, reports it under the rule's label, fails the run and continues with the other rules; a scan registered without a rule is reported and fails; regression test: test_a_raising_scan_fails_its_rule_and_the_others_still_run, test_a_scan_without_a_rule_is_reported_and_fails

- **Original id:** G1-33
- **Where:** baseline_ratchet.py:155
- **Finding:** A raising scan aborts `run_rules`; scans without a rule are never enforced
- **Failing input:** a scan raising OSError
- **Proposed fix:** Catch per scan; report unmatched scans
- **Verified:** read-only

### GA-34 (Med) -- git log --follow crosses renames, but git show sha:<current path> fails before the rena...

**Disposition:** RESOLVED -- `history` reads `git log --follow --name-status` and shows each commit at the path the file had in that commit, so points before a rename are kept (deletions skipped); regression test: test_history_survives_a_rename

- **Original id:** G1-34
- **Where:** baseline_trend.py:73-82
- **Finding:** `git log --follow` crosses renames, but `git show sha:<current path>` fails before the rename, so points are dropped
- **Failing input:** a baseline renamed mid-history
- **Proposed fix:** Read the path per commit via `--name-status`
- **Verified:** read-only

### GA-35 (Low) -- Bare {key:note} counts as None; "moved" compares only the endpoints; a git failure exits 0

**Disposition:** RESOLVED -- `count_entries` counts a bare `{key: note}` map when its keys read as paths/finding keys (a stray `{"something": "else"}` is still None), sums `_core.Baseline` `count` records, strips a BOM; "moved" compares min and max over every point (a count that moved and returned is shown as `~~`, not listed as unmoved); `_git` raises `GitError` on a non-zero exit and `main` returns 2; regression test: test_a_bare_key_note_map_and_the_core_multiset_are_counted, test_a_count_that_moved_and_came_back_is_not_unmoved, test_a_git_failure_exits_non_zero, test_an_empty_repo_still_reports_no_baselines

- **Original id:** G1-35
- **Where:** baseline_trend.py:43-49,100
- **Finding:** Bare `{key:note}` counts as None; "moved" compares only the endpoints; a git failure exits 0
- **Failing input:** `count_entries('{"src/a.py":"n"}')` gives None
- **Proposed fix:** Count non-`_` keys; use min/max; exit non-zero on git failure
- **Verified:** repro (first case)

### GA-36 (Med) -- Excluded dir names are matched against absolute ancestors, so a checkout under build/ s...

**Disposition:** RESOLVED -- directory roots are enumerated with `_core.iter_files`, which matches excluded names against root-relative parts (explicit file roots use CWD-relative parts); a missing root or a directory root with no .py file raises `DiscoveryError`, and `--check` exits with that message instead of "All 0 files ... clean"; regression test: test_a_checkout_under_a_build_directory_is_still_scanned, test_a_missing_root_is_an_error, test_a_directory_with_no_py_file_is_an_error, test_main_check_fails_on_a_missing_root_instead_of_all_clean

- **Original id:** G1-36
- **Where:** black_filtered_apply.py:61,65
- **Finding:** Excluded dir names are matched against absolute ancestors, so a checkout under `build/` scans 0 files and `--check` prints "All 0 files clean"
- **Failing input:** root `<tmp>/build/repo`; root `nope`
- **Proposed fix:** Match only relative parts; fail on 0 files or a missing root
- **Verified:** repro

### GA-37 (Med) -- File-wide """ parity counts quotes inside strings, so every later fix is rejected and -...

**Disposition:** RESOLVED -- the multi-line-string state per line comes from tokenizer STRING (and 3.12+ FSTRING_START/END) spans, with the parity toggle only as a fallback for untokenizable files; a hunk is judged by the state before it and the state after it, so a quoted delimiter no longer freezes the rest of the file; regression test: test_a_quoted_delimiter_does_not_open_a_string, test_a_fix_after_a_quoted_delimiter_is_applied, test_a_fix_inside_a_real_multiline_string_region_is_still_rejected

- **Original id:** G1-37
- **Where:** black_filtered_apply.py:163
- **Finding:** File-wide `"""` parity counts quotes inside strings, so every later fix is rejected and `--check` passes unformatted code
- **Failing input:** `Q = '"""'` then `x  =  1`
- **Proposed fix:** Use tokenize STRING spans
- **Verified:** repro

### GA-38 (Low) -- No --stdin-filename (force-exclude and .pyi mode are ignored); a trailing --config rais...

**Disposition:** RESOLVED -- every file is piped with `--stdin-filename <path>` (force-exclude and .pyi mode apply; an echoed or empty excluded output returns the source unchanged, CRLF echo on Windows included); a trailing `--config` exits with a clear message, `--config=PATH` is accepted, and `--check --write` is rejected as mutually exclusive; `main` takes an optional argv; regression test: test_force_exclude_applies_to_a_piped_file, test_a_stub_is_formatted_in_pyi_mode, test_a_trailing_config_is_a_clear_error, test_check_and_write_together_are_rejected, test_config_equals_form_is_read

- **Original id:** G1-38
- **Where:** black_filtered_apply.py:73,274
- **Finding:** No `--stdin-filename` (force-exclude and .pyi mode are ignored); a trailing `--config` raises IndexError; `--check --write` silently drops `--write`
- **Failing input:** `--config` as the last arg
- **Proposed fix:** Pass `--stdin-filename`; bounds-check
- **Verified:** read-only

### GB-1 (High) -- The pytest\s+ regex matches pip install pytest pytest-cov as a pathless run, so every s...

**Disposition:** RESOLVED -- `pytest` counts only as a command: the first word of a shell command (split on `;`, `&&`, `||`, `|`), optionally behind `-`/`run:`, env assignments, `python -m`, `uv/poetry/pipenv/hatch/pdm run`, `coverage run -m`, `timeout N` and similar wrappers; `pip install pytest pytest-cov` and step names are not invocations; regression test: test_pip_install_pytest_is_not_a_pathless_run, test_wrapped_commands_are_invocations

- **Original id:** G2-01
- **Where:** ci_test_dir_reachability.py:71
- **Finding:** The `pytest\s+` regex matches `pip install pytest pytest-cov` as a pathless run, so every subdir counts as reached
- **Failing input:** `pip install pytest pytest-cov` + `pytest tests/other` makes tests/gpu reachable
- **Proposed fix:** Count pytest only as a command
- **Verified:** repro

### GB-2 (High) -- YAML comments are scanned

**Disposition:** RESOLVED -- YAML comments (a `#` at line start or after whitespace, outside quotes) are stripped before scanning; workflows are read with `_core.read_source` (BOM stripped); regression test: test_a_commented_out_run_is_not_an_invocation, test_a_bom_workflow_is_read

- **Original id:** G2-02
- **Where:** ci_test_dir_reachability.py:49-71
- **Finding:** YAML comments are scanned
- **Failing input:** `# - run: pytest -x`
- **Proposed fix:** Strip comments first
- **Verified:** repro

### GB-3 (Med) -- Substring match: tests/unit is found in tests/unit_slow

**Disposition:** RESOLVED -- a positional target reaches a subdir only on a path-segment boundary (equal, ancestor, or a file/node id inside it), never by substring; regression test: test_a_sibling_with_a_longer_name_does_not_reach_the_shorter_one

- **Original id:** G2-03
- **Where:** ci_test_dir_reachability.py:122
- **Finding:** Substring match: `tests/unit` is found in `tests/unit_slow`
- **Failing input:** `pytest tests/unit_slow`
- **Proposed fix:** Boundary-aware match
- **Verified:** repro

### GB-4 (Med) -- The space form --ignore tests/gpu is not recognised and counts as an invocation

**Disposition:** RESOLVED -- `--ignore X` / `--ignore-glob X` are read as ignores exactly like the `=` form, and their value is never a positional target; regression test: test_the_space_form_of_ignore_is_an_ignore_not_a_target

- **Original id:** G2-04
- **Where:** ci_test_dir_reachability.py:32,119
- **Finding:** The space form `--ignore tests/gpu` is not recognised and counts as an invocation
- **Failing input:** `pytest tests/ --ignore tests/gpu`
- **Proposed fix:** Parse both forms
- **Verified:** repro

### GB-5 (Med) -- An ignore in one job applies globally (false positive)

**Disposition:** RESOLVED -- the check now builds one `PytestInvocation(paths, ignores)` per command and a subdir is reachable when ANY single invocation collects it under its own ignores; regression test: test_an_ignore_in_one_job_does_not_hide_a_subdir_another_job_runs

- **Original id:** G2-05
- **Where:** ci_test_dir_reachability.py:127-137
- **Finding:** An ignore in one job applies globally (false positive)
- **Failing input:** job a ignores gpu, job b runs `pytest tests/`
- **Proposed fix:** Evaluate ignores per invocation
- **Verified:** repro

### GB-6 (Low) -- \s+ crosses newlines, so the next line is read as pytest's args

**Disposition:** RESOLVED -- invocations are parsed line by line (after folding only explicit backslash continuations), so a following line is never read as pytest's arguments; regression test: test_the_next_line_is_not_read_as_pytest_arguments

- **Original id:** G2-06
- **Where:** ci_test_dir_reachability.py:71
- **Finding:** `\s+` crosses newlines, so the next line is read as pytest's args
- **Failing input:** `run: pytest` followed by `python x.py`
- **Proposed fix:** Use `[ \t]+`
- **Verified:** read-only

### GB-7 (High) -- A job without a name inherits the previous step's name, so allowlisted names mask other...

**Disposition:** RESOLVED -- `find_continue_on_error_steps` reads jobs and steps by YAML indentation: each job and each step is its own scope whose name is its own `name` key (resolved when the scope closes, so a later `name:` still applies); a job-level flag reports the job's name or id, an unnamed step falls back only to its own job's name, and a previous step's name can never leak into another job or step; regression test: test_a_job_without_a_name_does_not_inherit_the_previous_step_name, test_a_name_after_the_flag_still_names_the_step

- **Original id:** G2-07
- **Where:** ci_workflow_gate.py:72-79
- **Finding:** A job without a name inherits the previous step's name, so allowlisted names mask other jobs
- **Failing input:** job a `Run ruff`, job b `continue-on-error: true`
- **Proposed fix:** Reset the name per job/step
- **Verified:** repro

### GB-8 (Med) -- with: name: (artifact input) is taken as the step name

**Disposition:** RESOLVED -- only a `name` key at the step's own key column names it; `with: name:` (deeper) is ignored; regression test: test_an_action_input_called_name_is_not_the_step_name

- **Original id:** G2-08
- **Where:** ci_workflow_gate.py:44
- **Finding:** `with: name:` (artifact input) is taken as the step name
- **Failing input:** upload-artifact with `name: cov`
- **Proposed fix:** Restrict to step/job indent
- **Verified:** repro

### GB-9 (Med) -- Misses - continue-on-error: true and quoted 'true'

**Disposition:** RESOLVED -- the flag is recognised on the step's `-` line and as quoted `'true'`/`"true"` in any case, still not for templated values or `'false'`; comments are stripped quote-aware; composite actions (no `jobs:`) are read for `steps:` anywhere; workflows are read with `_core.read_source`; regression test: test_the_flag_on_the_dash_line_and_quoted_is_found, test_quoted_false_and_a_templated_value_are_not_found, test_a_composite_action_without_jobs_is_read, test_a_bom_workflow_is_read

- **Original id:** G2-09
- **Where:** ci_workflow_gate.py:49
- **Finding:** Misses `- continue-on-error: true` and quoted `'true'`
- **Failing input:** `  - continue-on-error: true`
- **Proposed fix:** Loosen the regex
- **Verified:** repro

### GB-10 (High) -- Any indented uses: exempts the job as a reusable-workflow call, which covers most real...

**Disposition:** RESOLVED -- `uses:` exempts a job only at the job's own key level (new `collect_jobs` returns `JobTimeout(job_id, line, has_timeout, reusable)`); a step's `uses: actions/checkout@v4` no longer exempts the job; regression test: test_a_step_uses_does_not_exempt_the_job

- **Original id:** G2-10
- **Where:** ci_workflow_timeout_gate.py:47,94
- **Finding:** Any indented `uses:` exempts the job as a reusable-workflow call, which covers most real jobs
- **Failing input:** `- name: co` / `uses: actions/checkout@v4`, no timeout, gives []
- **Proposed fix:** Match `uses:` only at job-key indent
- **Verified:** repro

### GB-11 (Med) -- A step-level timeout satisfies the job check (enshrined by a test)

**Disposition:** RESOLVED -- only a job-level `timeout-minutes` satisfies the check; the test that enshrined the step-level reading is re-framed to the correct behaviour; regression test: test_step_level_timeout_does_not_satisfy_the_job_check

- **Original id:** G2-11
- **Where:** ci_workflow_timeout_gate.py:96
- **Finding:** A step-level timeout satisfies the job check (enshrined by a test)
- **Failing input:** one step with `timeout-minutes: 5`
- **Proposed fix:** Require it at job indent; update the test
- **Verified:** repro

### GB-12 (Med) -- jobs:  # comment finds no jobs, so the result is []

**Disposition:** RESOLVED -- the `jobs:` header accepts a trailing comment (as do job headers, which may also be quoted), comment-only lines are skipped, and `assert_all_jobs_have_timeout` fails when no job is found; the workflow is read with `_core.read_source`; regression test: test_a_commented_jobs_header_is_read_and_zero_jobs_fails_the_assert

- **Original id:** G2-12
- **Where:** ci_workflow_timeout_gate.py:39
- **Finding:** `jobs:  # comment` finds no jobs, so the result is []
- **Failing input:** `jobs: # all`
- **Proposed fix:** Allow a trailing comment; fail on zero jobs
- **Verified:** repro

### GB-13 (Low) -- The last job's block runs to EOF

**Disposition:** RESOLVED -- a job's block ends at the next line at or above the job level, so a top-level section after `jobs:` is not part of the last job; regression test: test_the_last_job_ends_at_the_next_top_level_section

- **Original id:** G2-13
- **Where:** ci_workflow_timeout_gate.py:92
- **Finding:** The last job's block runs to EOF
- **Failing input:** a top-level section after `jobs`
- **Proposed fix:** End the block at the end of the jobs section
- **Verified:** read-only

### GB-14 (Med) -- permissions: read-all / {} are reported missing (false positive)

**Disposition:** RESOLVED -- any top-level `permissions:` (block, `read-all`, `write-all`, `{}`) counts as declared; a job-level one still does not; regression test: test_any_top_level_permissions_value_is_declared, test_a_nested_permissions_key_is_not_top_level

- **Original id:** G2-14
- **Where:** ci_workflow_paths.py:50
- **Finding:** `permissions: read-all` / `{}` are reported missing (false positive)
- **Failing input:** `permissions: read-all`
- **Proposed fix:** Accept any `^permissions:`
- **Verified:** repro

### GB-15 (Med) -- working-directory is never reset between steps/jobs (false negative)

**Disposition:** RESOLVED -- lines are assigned to their job and step by indentation; a `working-directory` applies only to its own step, a job's `defaults.run.working-directory` to that job, a top-level one to the workflow, and a step's key is honoured even when it follows the `run:`; regression test: test_a_working_directory_does_not_leak_into_the_next_job, test_a_working_directory_does_not_leak_into_the_next_step, test_job_defaults_and_a_later_working_directory_key_apply_to_their_own_scope

- **Original id:** G2-15
- **Where:** ci_workflow_paths.py:82,89
- **Finding:** `working-directory` is never reset between steps/jobs (false negative)
- **Failing input:** job a sets `tests`; job b runs `python unit/x.py`
- **Proposed fix:** Reset per step/job
- **Verified:** repro

### GB-16 (Low) -- A docker://…@sha256: digest is flagged as mutable

**Disposition:** RESOLVED -- `docker://image@sha256:<64 hex>` is pinned; a `docker://image:tag` gets its own mutable-tag message; regression test: test_a_docker_digest_is_pinned_and_a_docker_tag_is_not

- **Original id:** G2-16
- **Where:** ci_workflow_paths.py:120-126
- **Finding:** A `docker://…@sha256:` digest is flagged as mutable
- **Failing input:** `uses: docker://alpine@sha256:…`
- **Proposed fix:** Treat digests as pinned
- **Verified:** repro

### GB-17 (Low) -- The script regex is applied to name/description lines

**Disposition:** RESOLVED -- the script regex runs only on `run:` content (inline values and block-scalar bodies), never on `name:`/`description:` lines; comments are stripped quote-aware; workflows are read with `_core.read_source`; problem paths are POSIX; regression test: test_a_script_named_in_a_step_name_is_not_a_run, test_a_bom_workflow_is_read

- **Original id:** G2-17
- **Where:** ci_workflow_paths.py:92
- **Finding:** The script regex is applied to name/description lines
- **Failing input:** `name: runs python tools/old.py`
- **Proposed fix:** Limit to `run:` content
- **Verified:** read-only

### GB-18 (Low) -- Dead repo_root recheck

**Disposition:** RESOLVED -- the dead second `repo_root` existence recheck is removed (the bases loop already includes `repo_root`); covered by the existing test_missing_run_script_is_flagged / test_existing_run_script_passes; regression test: test_missing_run_script_is_flagged

- **Original id:** G2-18
- **Where:** ci_workflow_paths.py:104
- **Finding:** Dead repo_root recheck
- **Failing input:** n/a
- **Proposed fix:** Remove it
- **Verified:** read-only

### GB-19 (High) -- BOM or SyntaxError files are silently skipped

**Disposition:** RESOLVED -- call sites are read through `_core.scan_python`/`parse_file` (BOM stripped); new `scan_cfg_get_calls` returns `(calls, unparsed)`, `find_cfg_get_calls` and `find_module_scope_frozen_cli_defaults` raise `UnparsedFilesError` and every `assert_*` fails naming each unreadable/unparsable file; the resolver records files it could not parse; regression test: test_a_bom_file_is_read_and_a_syntax_error_fails, test_frozen_cli_default_with_keywords_and_a_syntax_error

- **Original id:** G2-19
- **Where:** config_call_site_parity.py:91-94,170,580
- **Finding:** BOM or SyntaxError files are silently skipped
- **Failing input:** `﻿x = cfg().get('a','b')`
- **Proposed fix:** `utf-8-sig`; fail on parse errors
- **Verified:** repro

### GB-20 (Med) -- Keyword section=/key= calls are skipped

**Disposition:** RESOLVED -- `section`/`key` are read positionally or as keywords (`section=`, `key=`), in the call-site scan and in the frozen-CLI scan; regression test: test_keyword_section_and_key_are_found, test_frozen_cli_default_with_keywords_and_a_syntax_error

- **Original id:** G2-20
- **Where:** config_call_site_parity.py:113-119
- **Finding:** Keyword `section=`/`key=` calls are skipped
- **Failing input:** `cfg().get(section='a', key='b')`
- **Proposed fix:** Use `_arg_node(..., kw)`
- **Verified:** repro

### GB-21 (Med) -- Binding only via Assign; AnnAssign, walrus and with ..

**Disposition:** RESOLVED -- a name is bound to the accessor by `=`, an annotated assignment, a walrus or `with cfg() as c`; regression test: test_every_binding_form_is_followed

- **Original id:** G2-21
- **Where:** config_call_site_parity.py:98-102
- **Finding:** Binding only via Assign; AnnAssign, walrus and `with ... as` are missed
- **Failing input:** `c: Config = cfg(); c.get('a','b')`
- **Proposed fix:** Handle those forms
- **Verified:** repro

### GB-22 (Low) -- Bound names are file-wide, not scoped (false positive)

**Disposition:** RESOLVED -- bindings are resolved per scope with Python's lookup rules (innermost function scope that binds the name decides; parameters, other assignments, loop targets and imports shadow; class scopes are skipped for nested functions; `global`/`nonlocal` defer outward); regression test: test_a_bound_name_is_scoped_like_python_scopes_it

- **Original id:** G2-22
- **Where:** config_call_site_parity.py:97-111
- **Finding:** Bound names are file-wide, not scoped (false positive)
- **Failing input:** `def g(c): c.get('x','y')`
- **Proposed fix:** Scope per function
- **Verified:** repro

### GB-23 (Med) -- _const_in_file takes the first assignment and ignores AnnAssign

**Disposition:** RESOLVED -- `_const_in_file` takes the LAST top-level binding (`=`, multi-target `=`, annotated assignment; an augmented assignment or a non-literal last binding makes it unresolved); regression test: test_the_last_binding_and_an_annotated_one_resolve

- **Original id:** G2-23
- **Where:** config_call_site_parity.py:194-199
- **Finding:** `_const_in_file` takes the first assignment and ignores AnnAssign
- **Failing input:** `X=1;X=2` resolves to 1; `Y: int=5` is unresolved
- **Proposed fix:** Take the last binding; handle AnnAssign
- **Verified:** repro

### GB-24 (Med) -- Relative imports are unresolved, so defaults are silently skipped

**Disposition:** RESOLVED -- `from .x import Y` / `from . import m` resolve against the importing file's package (relative to the resolver root) via `_core.resolve_relative`; regression test: test_a_relative_import_and_import_a_dot_b_resolve

- **Original id:** G2-24
- **Where:** config_call_site_parity.py:228
- **Finding:** Relative imports are unresolved, so defaults are silently skipped
- **Failing input:** `from .consts import BATCH`
- **Proposed fix:** Resolve relative to the package
- **Verified:** read-only

### GB-25 (Low) -- import a.b binds b (wrong)

**Disposition:** RESOLVED -- `import a.b` binds `a` (target `a`), `import a.b as c` binds `c` (target `a.b`), and `a.b.NAME` chains of any depth are resolved through the module path; regression test: test_a_relative_import_and_import_a_dot_b_resolve

- **Original id:** G2-25
- **Where:** config_call_site_parity.py:234
- **Finding:** `import a.b` binds `b` (wrong)
- **Failing input:** `import pkg.consts; consts.X`
- **Proposed fix:** Bind the first component
- **Verified:** read-only

### GB-26 (Low) -- 1 == True == 1.0 counts as agreement

**Disposition:** RESOLVED -- `to_hashable` tags bools and floats with their type (tuples too), so `True`, `1` and `1.0` are different defaults; regression test: test_true_one_and_one_point_zero_are_different_defaults

- **Original id:** G2-26
- **Where:** config_call_site_parity.py:455,503
- **Finding:** `1 == True == 1.0` counts as agreement
- **Failing input:** default True vs 1
- **Proposed fix:** Include the type in `to_hashable`
- **Verified:** repro

### GB-27 (Low) -- sorted() on mixed-type keys raises TypeError

**Disposition:** RESOLVED -- dict items and set members are ordered by `repr` of their hashable form, so mixed-type keys no longer raise; regression test: test_mixed_type_dict_keys_do_not_crash

- **Original id:** G2-27
- **Where:** config_call_site_parity.py:310,312
- **Finding:** `sorted()` on mixed-type keys raises TypeError
- **Failing input:** `{1:'a','b':2}`
- **Proposed fix:** Sort by repr
- **Verified:** read-only

### GB-28 (Low) -- The min_checked guard is a bare assert (dropped under -O)

**Disposition:** RESOLVED -- the `min_checked` guard is a `pytest.fail`, not a bare assert (the test that caught `AssertionError` is re-framed to `pytest.fail.Exception`); regression test: test_the_min_checked_guard_survives_python_dash_o

- **Original id:** G2-28
- **Where:** config_call_site_parity.py:506
- **Finding:** The `min_checked` guard is a bare assert (dropped under `-O`)
- **Failing input:** `python -O -m pytest`
- **Proposed fix:** Use `pytest.fail`
- **Verified:** read-only

### GB-29 (Low) -- relative_to raises outside root; every assert re-parses all files; resolve runs twice

**Disposition:** RESOLVED -- paths are made relative with `_core.relative_posix` (a file outside root gets its absolute POSIX path instead of raising); parses go through the `_core` parse cache so repeated asserts do not re-parse unchanged files; each default is resolved once per call site in the divergence check; regression test: test_a_file_outside_root_does_not_raise

- **Original id:** G2-29
- **Where:** config_call_site_parity.py:122,367,442,452,455,495
- **Finding:** `relative_to` raises outside root; every assert re-parses all files; resolve runs twice
- **Failing input:** a file outside root
- **Proposed fix:** Cache parses; handle outside-root files
- **Verified:** read-only

### GB-30 (Med) -- A missing baseline is seeded and the test skipped (green in CI; also writes into the tree)

**Disposition:** RESOLVED -- a missing baseline now fails (naming the refresh command) and writes nothing; the baseline is written only on an explicit refresh, atomically (`_core.atomic_write_text`); `_refresh_requested`/`register_refresh_option` are thin wrappers over `_core.refresh_requested`/`register_refresh_options` (option, `--py-ci-refresh`, `PY_CI_SHARED_REFRESH=code-audit`, xdist-safe); existing tests that relied on seeding were re-framed to seed through a refresh request; regression test: test_a_missing_baseline_fails_and_writes_nothing_until_a_refresh, test_register_refresh_option_also_registers_the_shared_generic_flag

- **Original id:** G2-30
- **Where:** code_audit_meta.py:202-207
- **Finding:** A missing baseline is seeded and the test skipped (green in CI; also writes into the tree)
- **Failing input:** delete the baseline
- **Proposed fix:** Fail unless an explicit refresh is requested
- **Verified:** read-only

### GB-31 (Low) -- An empty snippet falls back to a line{n} key

**Disposition:** RESOLVED -- a finding with an empty snippet is fingerprinted from its detail (and `function`, when a scanner provides one), so its key survives relocation; only a finding with neither falls back to `line{n}`; regression test: test_an_empty_snippet_is_keyed_by_detail_not_line

- **Original id:** G2-31
- **Where:** code_audit_meta.py:117
- **Finding:** An empty snippet falls back to a `line{n}` key
- **Failing input:** a finding with snippet ""
- **Proposed fix:** Use a fingerprint of the detail and function
- **Verified:** read-only

### GB-32 (Med) -- The hash concatenates contents without separators; an empty file list gives a constant...

**Disposition:** RESOLVED -- each file contributes a machine-independent label (its path relative to the files' common directory) and its length before its bytes, so byte moves between files and renames change the hash; `content_hash([])` raises and the assert fails on an empty file list; new baselines carry `hash_format: 2`, and a baseline without it is still compared with the legacy hash so upgrading does not fail every consumer; regression test: test_moving_bytes_between_files_changes_the_hash, test_the_hash_does_not_depend_on_where_the_checkout_lives, test_an_empty_file_list_is_rejected, test_a_legacy_baseline_still_gates

- **Original id:** G2-32
- **Where:** content_hash_version_bump_gate.py:91
- **Finding:** The hash concatenates contents without separators; an empty file list gives a constant hash
- **Failing input:** A="ab",B="c" hashes the same as A="a",B="bc"
- **Proposed fix:** Hash len+path+content; fail on empty
- **Verified:** repro

### GB-33 (Med) -- Refresh via sys.argv is ignored under xdist

**Disposition:** RESOLVED -- refresh detection is `_core.refresh_requested` (new `request=` kwarg, `--py-ci-refresh`, env `PY_CI_SHARED_REFRESH=content-hash-version`, argv last), and `register_refresh_option` is a thin wrapper over `_core.register_refresh_options`; regression test: test_refresh_is_detected_from_the_env_under_xdist, test_register_refresh_option_registers_the_generic_flag_too

- **Original id:** G2-33
- **Where:** content_hash_version_bump_gate.py:77-80
- **Finding:** Refresh via sys.argv is ignored under xdist
- **Failing input:** `pytest -n 4 --refresh-...`
- **Proposed fix:** Use `request.config`
- **Verified:** read-only

### GB-34 (Med) -- A missing baseline seeds and skips; any version change, including a revert, self-certif...

**Disposition:** RESOLVED -- a missing baseline fails and is written only by an explicit refresh (atomically); a bump is re-pinned only outside CI (`write_on_bump`, default "env CI unset"), in CI an unrecorded bump fails until the re-pinned baseline is committed; the baseline keeps a `history` of version -> hash and a version already pinned to different content (A -> B -> A with B's content) is rejected; existing seeding tests re-framed to seed through a refresh; regression test: test_a_missing_baseline_fails_and_a_refresh_seeds_it, test_a_bump_in_ci_is_not_written_and_fails_until_committed, test_reverting_to_an_old_version_with_new_content_fails

- **Original id:** G2-34
- **Where:** content_hash_version_bump_gate.py:129,140-147
- **Finding:** A missing baseline seeds and skips; any version change, including a revert, self-certifies and writes the baseline in CI
- **Failing input:** version A to B to A
- **Proposed fix:** Fail on missing; do not write in CI
- **Verified:** read-only

### GB-35 (Low) -- A title containing * is not a bullet; matching is a case-sensitive substring

**Disposition:** RESOLVED -- bullet and title patterns take the title up to the first closing `**` (so `- **a*b title**` is a bullet), and cross-document matching compares `normalize_title` forms (case-folded, `*`/backtick/underscore markup removed, whitespace collapsed) on both sides; files are read with `_core.read_source`; regression test: test_a_title_containing_an_asterisk_is_still_a_bullet, test_a_title_is_matched_across_case_markup_and_wrapping, test_a_bom_changelog_is_read

- **Original id:** G2-35
- **Where:** changelog_promise_parity.py:34,37
- **Finding:** A title containing `*` is not a bullet; matching is a case-sensitive substring
- **Failing input:** `- **a*b title**`
- **Proposed fix:** `\*\*(.+?)\*\*`; normalise
- **Verified:** repro

### GB-36 (Low) -- A missing section or trigger produces a permanent skip

**Disposition:** RESOLVED -- new kwargs `require_section` (a missing/renamed heading fails), `min_triggered` (too few triggering bullets fails) and `require_resolution_paths` (a missing resolution document fails) turn the skips into failures once a repo's convention is established; the defaults keep the documented "not yet applicable" skip for repos that have not adopted the convention; regression test: test_a_missing_section_or_trigger_can_be_required, test_a_required_resolution_document_must_exist

- **Original id:** G2-36
- **Where:** changelog_promise_parity.py:139,144
- **Finding:** A missing section or trigger produces a permanent skip
- **Failing input:** a renamed heading
- **Proposed fix:** `require_section` / min count
- **Verified:** read-only

### GB-37 (Low) -- copytree into an existing dir raises; the str-prefix check accepts copy2; the return co...

**Disposition:** RESOLVED -- a copy left in the workdir by an earlier call is removed before a fresh `copytree` (a merge would keep stale files, `dirs_exist_ok` alone would too); containment is checked on path components (`relative_to`), so `copy2` is not `copy`; the probe's pytest return code is checked and a failed probe is reported with the tail of its stdout/stderr; regression test: test_a_second_call_with_the_same_workdir_takes_a_fresh_copy, test_a_sibling_directory_sharing_the_prefix_is_not_the_copy, test_a_probe_that_fails_is_reported_with_its_output

- **Original id:** G2-37
- **Where:** checkout_resolution.py:74,106
- **Finding:** copytree into an existing dir raises; the str-prefix check accepts `copy2`; the return code is ignored
- **Failing input:** a second call with the same workdir
- **Proposed fix:** `dirs_exist_ok`; `is_relative_to`
- **Verified:** read-only

### GB-38 (Low) -- Relative constants resolve against the CWD, not repo_root

**Disposition:** RESOLVED -- a relative constant is resolved against `repo_root` (where a real run from the checkout would put it), not the current working directory, in both `assert_outside` and `offending_state_paths`; regression test: test_a_relative_constant_is_judged_against_the_checkout_not_the_cwd

- **Original id:** G2-38
- **Where:** checkpoint_isolation.py:46,65
- **Finding:** Relative constants resolve against the CWD, not repo_root
- **Failing input:** CWD=/tmp
- **Proposed fix:** Resolve against repo_root
- **Verified:** read-only

### GB-39 (Low) -- A field missing in one repo is not reported as drift; a list-valued field raises TypeEr...

**Disposition:** RESOLVED -- a field present in one repo and absent in another is reported as divergence (shown as `<missing>`; absent everywhere is not), values are compared by canonical JSON so list/table fields no longer raise `TypeError`; `main` takes injectable `consumers`/`fetch` so it is testable offline; new test file tests/test_config_drift_check.py; regression test: test_a_field_missing_in_one_repo_is_divergence, test_a_list_valued_field_is_compared_not_crashed_on, test_main_reports_divergence_without_failing

- **Original id:** G2-39
- **Where:** config_drift_check.py
- **Finding:** A field missing in one repo is not reported as drift; a list-valued field raises TypeError; no test file
- **Failing input:** n/a
- **Proposed fix:** Treat missing as divergent; hash via repr
- **Verified:** read-only

### GC-1 (High) -- A missing functions dir returns [] (a test enshrines it)

**Disposition:** RESOLVED -- a missing functions dir now yields a "this check examined nothing" problem (and fails the assert); files enumerated through `_core.iter_files`, read through `_core.read_source` (BOM stripped, unreadable files reported); the old no-op test was re-framed; regression test: test_edge_function_hygiene.py::TestAssert::test_missing_directory_is_a_problem_not_a_pass

- **Original id:** G3-01
- **Where:** edge_function_hygiene.py:88
- **Finding:** A missing functions dir returns [] (a test enshrines it)
- **Failing input:** `Path("nope")`
- **Proposed fix:** Return a problem; invert the test
- **Verified:** repro

### GC-2 (High) -- Misses the psycopg cursor pattern

**Disposition:** RESOLVED -- `cur = conn.cursor()`, `with conn.cursor() as cur` (and `conn.cursor().execute`) are aliased to their handle, so a statement on the cursor needs the handle's commit/rollback; regression test: test_db_transaction_completeness.py::TestCursorsAndQualnames::test_execute_on_an_assigned_cursor_counts_for_its_handle

- **Original id:** G3-02
- **Where:** db_transaction_completeness.py:138-149
- **Finding:** Misses the psycopg cursor pattern
- **Failing input:** `cur=conn.cursor(); cur.execute('x')`, `with conn.cursor() as cur`
- **Proposed fix:** Alias cursors to their handle
- **Verified:** repro

### GC-3 (Med) -- The key has no class, so same-named methods collide

**Disposition:** RESOLVED -- the baseline key uses the function's qualname (`Class.method`, `outer.<locals>.inner`); `IncompleteTransaction` gained a `qualname` attribute, `function` stays the bare name; regression test: test_db_transaction_completeness.py::TestCursorsAndQualnames::test_same_named_methods_in_two_classes_get_distinct_keys

- **Original id:** G3-03
- **Where:** db_transaction_completeness.py:98,199
- **Finding:** The key has no class, so same-named methods collide
- **Failing input:** `A.run` / `B.run` both give `x.py::run::db`
- **Proposed fix:** Use the qualname
- **Verified:** repro

### GC-4 (Med) -- BOM, non-UTF8 and SyntaxError files are silently skipped; no min_files

**Disposition:** RESOLVED -- files are parsed through `_core.scan_python` (BOM and PEP 263 decoding), the assert fails on unparsed files and on fewer than `min_files` (default 1) parsed files, and the baseline moved to `_core.Baseline` (missing file fails; refresh via `--refresh-db-transaction-baseline`/`PY_CI_SHARED_REFRESH`); regression test: test_db_transaction_completeness.py::TestCorpusIntegrity::test_an_unparsable_file_fails_the_gate

- **Original id:** G3-04
- **Where:** db_transaction_completeness.py:166-173
- **Finding:** BOM, non-UTF8 and SyntaxError files are silently skipped; no min_files
- **Failing input:** BOM file
- **Proposed fix:** `utf-8-sig`; report; `min_files`
- **Verified:** repro

### GC-5 (Med) -- A BOM file is silently skipped

**Disposition:** RESOLVED -- files are enumerated with `_core.iter_files` and parsed with `_core.scan_python` (BOM stripped); the assert fails on unparsed files instead of dropping them; regression test: test_drifted_duplicate_functions.py::test_a_bom_file_is_compared

- **Original id:** G3-05
- **Where:** drifted_duplicate_functions.py:85
- **Finding:** A BOM file is silently skipped
- **Failing input:** BOM `a.py` drifted from `b.py`
- **Proposed fix:** `utf-8-sig`
- **Verified:** repro

### GC-6 (Med) -- async def is not collected

**Disposition:** RESOLVED -- `async def` is collected; async and sync copies stay distinct because the kind is part of the compared dump; regression test: test_drifted_duplicate_functions.py::test_async_copies_that_drifted_are_reported

- **Original id:** G3-06
- **Where:** drifted_duplicate_functions.py:89
- **Finding:** `async def` is not collected
- **Failing input:** two drifted `async def f`
- **Proposed fix:** Include AsyncFunctionDef
- **Verified:** repro

### GC-7 (Med) -- The group key includes defaults, so a drifted default is never compared

**Disposition:** RESOLVED -- the group key is parameter names and kinds only; defaults moved into the compared dump, so a drifted default is reported; regression test: test_drifted_duplicate_functions.py::test_a_drifted_default_is_compared_not_split

- **Original id:** G3-07
- **Where:** drifted_duplicate_functions.py:90
- **Finding:** The group key includes defaults, so a drifted default is never compared
- **Failing input:** `g(x,n=5)` vs `g(x,n=10)`
- **Proposed fix:** Key on names and kinds only
- **Verified:** repro

### GC-8 (Low) -- Only tree.body is scanned; no stale-allow check; no min_files

**Disposition:** RESOLVED -- module-scope functions inside `if`/`try`/`with`/loops are collected, an `allow` entry that no longer names a drifted group fails, and the assert has `min_files` (default 1) on parsed files; regression test: test_drifted_duplicate_functions.py::test_a_fallback_def_under_try_is_collected, test_a_stale_allow_entry_fails, test_an_empty_corpus_fails

- **Original id:** G3-08
- **Where:** drifted_duplicate_functions.py:88
- **Finding:** Only `tree.body` is scanned; no stale-allow check; no min_files
- **Failing input:** a fallback def in try/except
- **Proposed fix:** Walk compound statements; check stale allows
- **Verified:** read-only

### GC-9 (Med) -- A nested function's copy is reported under both outer and inner; allowing inner still f...

**Disposition:** RESOLVED -- copies are collected from each function's own scope (nested defs, lambdas and classes excluded) and reported under the qualname of that scope, so an inner copy is reported once and allowing the inner function clears it; regression test: test_discarded_model_copy.py::test_a_nested_copy_is_reported_once_under_its_own_scope, test_allowing_the_inner_function_clears_the_nested_finding

- **Original id:** G3-09
- **Where:** discarded_model_copy.py:93-97
- **Finding:** A nested function's copy is reported under both outer and inner; allowing inner still fails outer
- **Failing input:** outer/inner model_copy
- **Proposed fix:** Report per own scope
- **Verified:** repro

### GC-10 (Med) -- AnnAssign and walrus copies are missed

**Disposition:** RESOLVED -- `name: T = x.model_copy(update=...)` and `(name := x.model_copy(update=...))` are collected, and annotated/walrus aliases are followed in the escape check; regression test: test_discarded_model_copy.py::test_annotated_and_walrus_copies_are_found

- **Original id:** G3-10
- **Where:** discarded_model_copy.py:95
- **Finding:** AnnAssign and walrus copies are missed
- **Failing input:** `c: X = cfg.model_copy(update=..)`
- **Proposed fix:** Handle them
- **Verified:** repro

### GC-11 (Low) -- Passing the copy to a log call counts as an escape

**Disposition:** RESOLVED -- display-only callees (`print`, `repr`/`str`/`len`..., and `debug/info/warning/error/exception/critical/log` on a logger-named receiver) are not an escape; regression test: test_discarded_model_copy.py::test_logging_the_copy_is_not_an_escape

- **Original id:** G3-11
- **Where:** discarded_model_copy.py:62-63
- **Finding:** Passing the copy to a log call counts as an escape
- **Failing input:** `log.debug('%s', c5); return cfg`
- **Proposed fix:** Keep a sink-only callee list
- **Verified:** repro

### GC-12 (Low) -- relative_to raises; allowed is keyed by bare name, so it applies repo-wide

**Disposition:** RESOLVED -- rel paths come from `_core.relative_posix` (never raises); `allowed` keys are `path::qualname` and a bare-name key is rejected with a message; the existing test was re-framed to the scoped keys; parsing moved to `_core.scan_python` and unparsed files fail the assert; regression test: test_discarded_model_copy.py::test_allowed_is_scoped_to_one_file, test_a_file_outside_the_root_does_not_raise

- **Original id:** G3-12
- **Where:** discarded_model_copy.py:92,110-111
- **Finding:** `relative_to` raises; `allowed` is keyed by bare name, so it applies repo-wide
- **Failing input:** `allowed={"build":..}`
- **Proposed fix:** Key by `path::func`
- **Verified:** read-only

### GC-13 (Med) -- A missing baseline is rewritten and skipped; the write happens before min_lists (a test...

**Disposition:** RESOLVED -- a missing baseline now fails (nothing is written); only a refresh writes it, atomically via `_core.atomic_write_text`, and `min_lists` is checked before any write; the enshrining test was re-framed; regression test: test_deferred_drift.py::TestTheRatchet::test_a_missing_baseline_fails_and_is_not_written, test_the_floor_is_checked_before_a_refresh_writes

- **Original id:** G3-13
- **Where:** deferred_drift.py:60-62
- **Finding:** A missing baseline is rewritten and skipped; the write happens before `min_lists` (a test enshrines it)
- **Failing input:** rm baseline.json
- **Proposed fix:** Fail unless refreshing
- **Verified:** read-only

### GC-14 (Low) -- Refresh via sys.argv is ignored under xdist

**Disposition:** RESOLVED -- the refresh decision goes through `_core.refresh_requested(refresh_flag, request)` (pytest option, `PY_CI_SHARED_REFRESH` env inherited by xdist workers, then argv); new `refresh=`/`request=` kwargs; regression test: test_deferred_drift.py::TestTheRatchet::test_the_env_refresh_reaches_xdist_workers

- **Original id:** G3-14
- **Where:** deferred_drift.py:60
- **Finding:** Refresh via sys.argv is ignored under xdist
- **Failing input:** `-n 4 --refresh-debt-baseline`
- **Proposed fix:** Use `request.config`
- **Verified:** read-only

### GC-15 (Med) -- Imports under module-level try/if are missed; relative level is ignored

**Disposition:** RESOLVED -- `imported_top_level` walks every statement that runs at import (module-level `if`/`try`/`with`/loop bodies, not def/class bodies), and relative imports (`level > 0`) are not reported as top-level packages; regression test: test_deletion_gates.py::TestAuditRegressions::test_imports_under_module_level_try_and_if_count, test_a_relative_import_is_not_a_top_level_package

- **Original id:** G3-15
- **Where:** deletion_gates.py:74-78
- **Finding:** Imports under module-level try/if are missed; relative `level` is ignored
- **Failing input:** `try: import torch`; `from .numpy_helpers import y` gives {'numpy_helpers'}
- **Proposed fix:** Walk module scope; handle level
- **Verified:** repro

### GC-16 (Low) -- Attribute exceptions count as 0

**Disposition:** RESOLVED -- `except requests.Timeout` is matched: a bare name matches the last component of a dotted exception, a dotted name matches only that spelling; regression test: test_deletion_gates.py::TestAuditRegressions::test_an_attribute_exception_is_counted

- **Original id:** G3-16
- **Where:** deletion_gates.py:93
- **Finding:** Attribute exceptions count as 0
- **Failing input:** `except requests.Timeout:`
- **Proposed fix:** Match `.attr`
- **Verified:** repro

### GC-17 (Low) -- A module function shadows a same-named method

**Disposition:** RESOLVED -- `function_parameters` accepts a qualified name (`A.run`), and a bare name naming functions with different qualnames raises an ambiguity error instead of answering for the first match; regression test: test_deletion_gates.py::TestAuditRegressions::test_a_module_function_does_not_shadow_a_method

- **Original id:** G3-17
- **Where:** deletion_gates.py:57-62
- **Finding:** A module function shadows a same-named method
- **Failing input:** `def run(x)` + `A.run(self, sql_file)`
- **Proposed fix:** Qualified names; raise on ambiguity
- **Verified:** repro

### GC-18 (Low) -- A BOM makes the gate crash with SyntaxError

**Disposition:** RESOLVED -- `deletion_gates.parse` uses `_core.parse_file` and `dataclass_case_completeness` uses `_core.scan_python`, both BOM-safe (the dataclass assert also fails on unparsable files); regression test: test_deletion_gates.py::TestAuditRegressions::test_a_bom_file_parses, test_dataclass_case_completeness.py::TestFind::test_a_bom_file_is_read

- **Original id:** G3-18
- **Where:** deletion_gates.py:48; dataclass_case_completeness.py:40
- **Finding:** A BOM makes the gate crash with SyntaxError
- **Failing input:** BOM file
- **Proposed fix:** `utf-8-sig`
- **Verified:** repro

### GC-19 (Med) -- Aliased @dataclass is not recognised; no test file

**Disposition:** RESOLVED -- decorators are resolved through `_core.ImportAliases` (`dataclass as dc`, `import dataclasses as dcs`, with or without arguments); new tests/test_dataclass_case_completeness.py; regression test: test_dataclass_case_completeness.py::TestFind::test_every_spelling_is_found

- **Original id:** G3-19
- **Where:** dataclass_case_completeness.py:28-32
- **Finding:** Aliased `@dataclass` is not recognised; no test file
- **Failing input:** `from dataclasses import dataclass as dc`
- **Proposed fix:** Resolve aliases; add tests
- **Verified:** repro

### GC-20 (Med) -- Empty inputs pass; same-named classes merge

**Disposition:** RESOLVED -- `min_files` (default 1, counted on parsed files) makes empty inputs fail; same-named classes are kept apart (`find_dataclass_sites`), and when a name is declared twice each must be covered as `path::Name`; regression test: test_dataclass_case_completeness.py::TestAssert::test_empty_inputs_fail, test_same_named_classes_need_one_entry_each

- **Original id:** G3-20
- **Where:** dataclass_case_completeness.py:46-56
- **Finding:** Empty inputs pass; same-named classes merge
- **Failing input:** `assert_every_dataclass_has_a_case([], [])`
- **Proposed fix:** `min_files`; key by path
- **Verified:** repro

### GC-21 (Med) -- Rules are per file; a nested helper gets name lib, so the public array-cap rule is skipped

**Disposition:** RESOLVED -- the function name is the first directory under the functions dir, and the body-cap and public array-cap rules are judged over every file of that function; regression test: test_edge_function_hygiene.py::TestFunctionScopedRules::test_nested_helper_belongs_to_its_public_function

- **Original id:** G3-21
- **Where:** edge_function_hygiene.py:98,113-121
- **Finding:** Rules are per file; a nested helper gets name `lib`, so the public array-cap rule is skipped
- **Failing input:** `pub/lib/h.ts`
- **Proposed fix:** Name = first part under the dir; apply rules per function dir
- **Verified:** repro

### GC-22 (Med) -- The IP-log regex stops at the first ); plain args are missed

**Disposition:** RESOLVED -- console calls are lexed with a string/template-aware scanner that finds the real closing paren; each argument and each `${...}` is judged separately, so bare `ip` arguments and interpolations after nested calls are caught; regression test: test_edge_function_hygiene.py::TestIpLogging::test_interpolation_after_a_nested_call_is_flagged

- **Original id:** G3-22
- **Where:** edge_function_hygiene.py:56
- **Finding:** The IP-log regex stops at the first `)`; plain args are missed
- **Failing input:** ``console.log(`${String(x)} ${ip}`)``, `console.log("ip", ip)`
- **Proposed fix:** Balanced parens; bare `\bip\b`
- **Verified:** repro

### GC-23 (Low) -- Only new Response( is recognised

**Disposition:** RESOLVED -- `Response.json(` is recognised alongside `new Response(`; regression test: test_edge_function_hygiene.py::TestResponseJsonAndPresenceChecks::test_response_json_in_a_catch_is_flagged

- **Original id:** G3-23
- **Where:** edge_function_hygiene.py:103
- **Finding:** Only `new Response(` is recognised
- **Failing input:** `catch(e){return Response.json({ok:true})}`
- **Proposed fix:** Add `Response.json`
- **Verified:** repro

### GC-24 (Low) -- An emptiness check is flagged as timing-unsafe

**Disposition:** RESOLVED -- a comparison whose other operand is `""`, `''`, a backtick pair, `null` or `undefined` is a presence check and is skipped; regression test: test_edge_function_hygiene.py::TestResponseJsonAndPresenceChecks::test_emptiness_check_is_not_a_secret_comparison

- **Original id:** G3-24
- **Where:** edge_function_hygiene.py:123-131
- **Finding:** An emptiness check is flagged as timing-unsafe
- **Failing input:** `apiKey === ""`
- **Proposed fix:** Skip ""/null/undefined
- **Verified:** repro

### GC-25 (Med) -- uv run python tool.py --x marks the project's own flags foreign

**Disposition:** RESOLVED -- fenced command lines are resolved through runners (`uv run`, `poetry run`, `pdm run`, `hatch run` run the project's program; `uvx`, `npx`, `pipx run` run a separate tool): only the runner's own options are foreign when it runs `python script.py` or a non-external program, `python -m <external>` stays foreign; regression test: test_doc_identifier_parity.py::test_a_runner_does_not_make_our_own_script_flags_foreign

- **Original id:** G3-25
- **Where:** doc_identifier_parity.py:67-71
- **Finding:** `uv run python tool.py --x` marks the project's own flags foreign
- **Failing input:** a misspelled own flag in a fence
- **Proposed fix:** Look through uv/uvx/npx to the real program
- **Verified:** repro

### GC-26 (Low) -- ls-files quotes non-ASCII paths, so they drop out; relative_to raises

**Disposition:** RESOLVED -- `default_corpus_files` enumerates through `_core.iter_files` (`git ls-files -z`, surrogate-escaped, pruned walk fallback), doc paths go through `_core.relative_posix` (never raises), docs are read with `_core.read_source` (BOM stripped, undecodable doc reported); regression test: test_doc_identifier_parity.py::test_a_non_ascii_untracked_file_is_in_the_default_corpus, test_a_doc_outside_the_root_does_not_raise

- **Original id:** G3-26
- **Where:** doc_identifier_parity.py:96,122
- **Finding:** ls-files quotes non-ASCII paths, so they drop out; `relative_to` raises
- **Failing input:** `déjà.py`
- **Proposed fix:** `-z`; catch the error
- **Verified:** read-only

### GC-27 (Med) -- Any pkg[extra] is treated as a self-reference

**Disposition:** RESOLVED -- a `pkg[extra]` spec is followed as a group reference only when `pkg` is the project's own `[project].name` (or, for a caller with no name, when every extra is a declared group); `requests[socks]` resolves to the package `requests`; `resolve_extras_group` gained a `project_name=` kwarg; regression test: test_docs_inventory_parity.py::TestAuditRegressions::test_another_packages_extras_are_that_package, test_a_bullet_omitting_an_extras_package_is_reported

- **Original id:** G3-27
- **Where:** docs_inventory_parity.py:34,70
- **Finding:** Any `pkg[extra]` is treated as a self-reference
- **Failing input:** `requests[socks]` is lost
- **Proposed fix:** Follow only the project's own name
- **Verified:** repro

### GC-28 (Med) -- Tokens keep .py or a trailing period (false positive)

**Disposition:** RESOLVED -- documented words drop a trailing full stop and a `.py`/`.pyi` suffix before the stem comparison; module enumeration moved to `_core.iter_files`; regression test: test_docs_inventory_parity.py::TestAuditRegressions::test_a_module_named_with_its_suffix_or_a_full_stop_is_documented

- **Original id:** G3-28
- **Where:** docs_inventory_parity.py:192,201
- **Finding:** Tokens keep `.py` or a trailing period (false positive)
- **Failing input:** `` `pythonlib.py` ``, "see utils."
- **Proposed fix:** Strip the suffix and punctuation
- **Verified:** repro

### GC-29 (Low) -- every_declared is recomputed per group (O(n^2)); name(args) markers are misparsed; non-...

**Disposition:** RESOLVED -- every group is resolved once and `every_declared` is hoisted out of the per-group loop; marker declarations split on `:` or `(`; docs and pyproject are read with `_core.read_source` (BOM stripped) and an undecodable doc is reported as a problem instead of raising; regression test: test_docs_inventory_parity.py::TestAuditRegressions::test_a_marker_with_arguments_is_declared_by_its_name, test_a_non_utf8_doc_is_reported_not_raised

- **Original id:** G3-29
- **Where:** docs_inventory_parity.py:127,305
- **Finding:** `every_declared` is recomputed per group (O(n^2)); `name(args)` markers are misparsed; non-UTF8 crashes
- **Failing input:** `markers=["foo(x): d"]`
- **Proposed fix:** Hoist; split on `[:(]`
- **Verified:** read-only

### GC-30 (Med) -- Table-row dispositions, backslash paths and parametrised ids are missed

**Disposition:** RESOLVED -- a markdown table row with a `Disposition` cell is a paragraph of its own, `\` separators are normalised to `/`, and parametrised ids (`test_x[case]`, `::test_x[case]`) resolve to their test name; regression test: test_disposition_test_references.py::test_a_table_row_disposition_is_checked, test_a_backslash_path_is_normalised, test_a_parametrised_id_names_its_test

- **Original id:** G3-30
- **Where:** disposition_test_references.py:28
- **Finding:** Table-row dispositions, backslash paths and parametrised ids are missed
- **Failing input:** `\| Disposition: fixed, test `test_ghost` \|`
- **Proposed fix:** Allow cells; normalise; allow `[..]`
- **Verified:** repro

### GC-31 (Low) -- .pyi is truncated to .py; ::Class::method is not class-scoped

**Disposition:** RESOLVED -- the path regex requires `.py` to end the path (a `.pyi` is no longer truncated), and `::Class::method` paths and backticked `Class::member` names check the member against that class's own body; test files are parsed with `_core.parse_file` and an unparsable one is reported instead of contributing no names; regression test: test_disposition_test_references.py::test_class_member_references_are_class_scoped, test_a_pyi_path_is_not_read_as_a_py_file, test_an_unparsable_test_file_is_reported

- **Original id:** G3-31
- **Where:** disposition_test_references.py:29,86
- **Finding:** `.pyi` is truncated to `.py`; `::Class::method` is not class-scoped
- **Failing input:** `test_b` only in TestB passes
- **Proposed fix:** `\.py\b`; check class members
- **Verified:** read-only

### GC-32 (Med) -- Comment stripping runs inside strings, so a URL eats the rest of the line and hides the...

**Disposition:** RESOLVED -- comment stripping is a small Dart lexer that skips single/double/triple/raw string literals (with `${}` interpolation) and handles nested block comments, keeping newlines so line numbers match; regression test: test_dart_scanners.py::TestCommentStrippingAndKeys::test_a_url_in_a_string_does_not_hide_the_rest_of_the_line, test_real_comments_are_still_stripped

- **Original id:** G3-32
- **Where:** dart_scanners.py:67-69
- **Finding:** Comment stripping runs inside strings, so a URL eats the rest of the line and hides the next finding
- **Failing input:** `Text('Visit https://x.com now');`
- **Proposed fix:** A lexer aware of string literals
- **Verified:** repro

### GC-33 (Med) -- Keys are file#ordinal, so the ratchet cannot see a fixed finding being swapped for a ne...

**Disposition:** RESOLVED -- keys are `rel#<sha1 of the description without its line number>[#n]`, assigned once per scanner (O(n)); a changed finding gets a new key while an edit above it keeps the key; consumer baselines need one refresh (for phase 3: note in README/CHANGELOG); regression test: test_dart_scanners.py::TestCommentStrippingAndKeys::test_a_changed_finding_gets_a_new_key, test_an_edit_above_a_finding_keeps_its_key

- **Original id:** G3-33
- **Where:** dart_scanners.py:61-64
- **Finding:** Keys are `file#ordinal`, so the ratchet cannot see a fixed finding being swapped for a new one; key counting is O(n^2)
- **Failing input:** `Text('Hello')` changed to `Text('Goodbye')` keeps `k.dart#0`
- **Proposed fix:** Include a content hash; keep a per-file counter
- **Verified:** repro

### GC-34 (Low) -- The "try" in window substring matches entry/retry; prefs reached through an attribute a...

**Disposition:** RESOLVED -- the jsonDecode rule looks for a real `try {` block (`\btry\s*\{`), and preferences writes are matched through dotted/call receivers (`widget.prefs.set...`, `ref.read(x).prefs.set...`); regression test: test_dart_scanners.py::TestTryAndPrefsRegressions::test_a_word_containing_try_is_not_a_try_block, test_prefs_reached_through_an_attribute_are_checked

- **Original id:** G3-34
- **Where:** dart_scanners.py:431,463
- **Finding:** The `"try" in window` substring matches `entry`/`retry`; prefs reached through an attribute are missed
- **Failing input:** `final entry=1; jsonDecode(s)`
- **Proposed fix:** `\btry\s*\{`; allow dotted receivers
- **Verified:** repro

### GD-1 (High) -- A BOM makes ast.parse fail and the file is silently skipped

**Disposition:** RESOLVED -- every listed site now parses through `_core` (`scan_python`/`parse_file`, BOM stripped, PEP 263 honoured): epsilon_padded_denominators, fail_message_quality, fail_open_handlers and function_length report unparsable files as gate failures; gate_population_canary reports an unparsable gate; effect_assertion_parity reports `<path>::<unparsable>` entries; regression test: test_epsilon_padded_denominators.py::test_a_bom_file_is_scanned, test_fail_message_quality.py::TestTheScan::test_a_bom_file_is_read, test_function_length.py::TestAuditRegressions::test_a_bom_file_is_measured, test_gate_population_canary.py::test_a_bom_gate_is_read_and_an_unparsable_one_is_reported, test_effect_assertion_parity.py::TestAuditRegressions::test_a_bom_module_is_read_and_an_unparsable_one_is_reported, test_fail_open_handlers.py::test_empty_files_unparsable_files_and_a_missing_baseline_fail

- **Original id:** G4-01
- **Where:** epsilon_padded_denominators.py:134; fail_message_quality.py:54; fail_open_handlers.py:186; function_length.py:45; gate_population_canary.py:67; effect_assertion_parity.py:110,246,314,368,402,475,558
- **Finding:** A BOM makes `ast.parse` fail and the file is silently skipped
- **Failing input:** BOM `y = k/(r**d+1e-12)` gives []
- **Proposed fix:** `utf-8-sig`; report parse failures
- **Verified:** repro

### GD-2 (High) -- Pre-commit hook args: are never inspected

**Disposition:** RESOLVED -- a hook's command is `entry` plus its `args` (shell-quoted), used both for narrowings and for the completion check; regression test: test_gate_integrity.py::TestAuditRegressions::test_hook_args_are_inspected, test_completion_reads_hook_args

- **Original id:** G4-02
- **Where:** gate_integrity.py:102 (also 244)
- **Finding:** Pre-commit hook `args:` are never inspected
- **Failing input:** `args: [--ignore=C901]` is undeclared
- **Proposed fix:** Join entry and args
- **Verified:** repro

### GD-3 (High) -- Decorator patches are credited to the LAST params, not the leading ones in bottom-up order

**Disposition:** RESOLVED -- decorator patches are mapped bottom-up onto the LEADING parameters (after self/cls), every injecting patch takes a slot whether or not it patches an effect, and `new=` patches (and `patch.dict`/`patch.multiple`) take none; regression test: test_effect_assertion_parity.py::TestAuditRegressions::test_a_decorator_patch_binds_the_leading_parameter, test_stacked_patches_map_bottom_up_and_new_injects_nothing

- **Original id:** G4-03
- **Where:** effect_assertion_parity.py:186
- **Finding:** Decorator patches are credited to the LAST params, not the leading ones in bottom-up order
- **Failing input:** `@patch("m.commit") def t(mock_commit, tmp_path)` aliases tmp_path
- **Proposed fix:** Map bottom-up onto the leading params, counting all patches
- **Verified:** repro

### GD-4 (High) -- Relative imports are ignored, so modules drop out of the map (fail-open)

**Disposition:** RESOLVED -- relative imports are resolved against the importing module's package (for every name a file is known by) when building the import map's edges; regression test: test_effect_assertion_parity.py::TestAuditRegressions::test_a_relative_import_in_a_package_init_is_an_edge

- **Original id:** G4-04
- **Where:** effect_assertion_parity.py:482
- **Finding:** Relative imports are ignored, so modules drop out of the map (fail-open)
- **Failing input:** `pkg/__init__: from . import writer` means writer is never checked
- **Proposed fix:** Resolve relative imports
- **Verified:** repro

### GD-5 (High) -- Misses pow, math.pow, /=, np.divide and 3+ term sums

**Disposition:** RESOLVED -- `pow(r, d)`/`math.pow` count as powers, `x /= <padded power>` and `np.divide`/`true_divide`/`torch.div`/`operator.truediv` second arguments are checked, and a denominator's `+` chain is flattened so a pad anywhere in a 3+ term sum is seen; regression test: test_epsilon_padded_denominators.py::test_the_other_spellings_of_a_padded_power_are_reported, test_their_safe_counterparts_are_not

- **Original id:** G4-05
- **Where:** epsilon_padded_denominators.py:88,103
- **Finding:** Misses `pow`, `math.pow`, `/=`, `np.divide` and 3+ term sums
- **Failing input:** `pow(r,d)+1e-12` gives []
- **Proposed fix:** Add those forms; flatten Add chains
- **Verified:** repro

### GD-6 (Med) -- Zero files scanned still passes

**Disposition:** RESOLVED -- the assert has `min_files` (default 1, counted on parsed files) and fails on unparsed files; a missing root raises `CorpusError` through `_core.iter_files`; regression test: test_epsilon_padded_denominators.py::test_zero_files_and_unparsable_files_fail_the_assertion, test_a_missing_root_raises

- **Original id:** G4-06
- **Where:** epsilon_padded_denominators.py:128
- **Finding:** Zero files scanned still passes
- **Failing input:** `roots=[Path("typo")]`
- **Proposed fix:** Fail on 0 parsed files or on parse failures
- **Verified:** read-only

### GD-7 (Med) -- OR is a verb under IGNORECASE; nouns like set/see/use also match

**Disposition:** RESOLVED -- `ACTIONABLE_RE` is case-sensitive and no longer lists `OR`: a capitalised fix verb counts anywhere, a lower-case one only where a sentence or clause starts, so `wrong or missing`, `the set of keys` and `no use for` are not instructions; regression test: test_fail_message_quality.py::TestThePattern::test_nouns_and_conjunctions_are_not_instructions, test_instructions_are

- **Original id:** G4-07
- **Where:** fail_message_quality.py:25
- **Finding:** `OR` is a verb under IGNORECASE; nouns like set/see/use also match
- **Failing input:** `'value was wrong or missing'` passes
- **Proposed fix:** Drop IGNORECASE; drop OR
- **Verified:** repro

### GD-8 (Med) -- Aliased/imported fail and reason= are unaudited

**Disposition:** RESOLVED -- `fail` calls are resolved with `_core.ImportAliases` (`from pytest import fail`, `import pytest as pt`), and the message is read from the first argument or `reason=`/`msg=`; regression test: test_fail_message_quality.py::TestTheScan::test_an_imported_fail_is_audited, test_a_reason_keyword_is_read

- **Original id:** G4-08
- **Where:** fail_message_quality.py:33
- **Finding:** Aliased/imported `fail` and `reason=` are unaudited
- **Failing input:** `from pytest import fail; fail('x')`
- **Proposed fix:** Resolve aliases; read the kwarg
- **Verified:** repro

### GD-9 (Low) -- Parse failures are skipped; reports use path.name

**Disposition:** RESOLVED -- files are parsed with `_core.scan_python`; an unparsable file is reported as a problem with its line, and paths are relative (to `root=`, the meta dir in the assert, else the files' common directory) instead of `path.name`; regression test: test_fail_message_quality.py::TestTheScan::test_a_parse_failure_is_reported_with_its_relative_path

- **Original id:** G4-09
- **Where:** fail_message_quality.py:55
- **Finding:** Parse failures are skipped; reports use `path.name`
- **Failing input:** a syntax-error file
- **Proposed fix:** Report; use relpath
- **Verified:** read-only

### GD-10 (Med) -- A BOM drops the first assignment

**Disposition:** RESOLVED -- the file is decoded with `_core.read_source` (BOM stripped), so the first assignment is read; regression test: test_env_example_round_trip.py::test_a_bom_does_not_drop_the_first_assignment

- **Original id:** G4-10
- **Where:** env_example_round_trip.py:50
- **Finding:** A BOM drops the first assignment
- **Failing input:** `﻿FIRST=1`
- **Proposed fix:** `utf-8-sig`
- **Verified:** repro

### GD-11 (Med) --  # inside quotes is truncated

**Disposition:** RESOLVED -- a quoted value is read up to its closing quote before any comment handling, so `X="a #b" # note` is `a #b`; unquoted values still lose a whitespace-preceded `#` comment; regression test: test_env_example_round_trip.py::test_a_hash_inside_quotes_is_part_of_the_value

- **Original id:** G4-11
- **Where:** env_example_round_trip.py:54
- **Finding:** ` #` inside quotes is truncated
- **Failing input:** `X="a #b"` gives `"a`
- **Proposed fix:** Parse quotes first
- **Verified:** repro

### GD-12 (Low) -- export X= and X = v are skipped

**Disposition:** RESOLVED -- the assignment pattern accepts `export NAME=`, `NAME = value` and the commented-out forms of both; regression test: test_env_example_round_trip.py::test_export_and_spaced_assignments_are_read

- **Original id:** G4-12
- **Where:** env_example_round_trip.py:41
- **Finding:** `export X=` and `X = v` are skipped
- **Failing input:** `export Y=2`
- **Proposed fix:** Loosen the regex
- **Verified:** repro

### GD-13 (Low) -- AliasChoices/AliasPath are dropped

**Disposition:** RESOLVED -- `AliasChoices` contributes every choice and `AliasPath` its first element as environment names (duck-typed, no pydantic import); regression test: test_env_example_round_trip.py::test_alias_choices_and_alias_path_are_env_names

- **Original id:** G4-13
- **Where:** env_example_round_trip.py:66
- **Finding:** `AliasChoices`/`AliasPath` are dropped
- **Failing input:** `validation_alias=AliasChoices("DB_URL")`
- **Proposed fix:** Expand the choices
- **Verified:** read-only

### GD-14 (Med) -- Same qualname overwrites (property setter, overload)

**Disposition:** RESOLVED -- two definitions under one qualname (property getter/setter, overloads) report the longest instead of the last; regression test: test_function_length.py::TestAuditRegressions::test_a_short_setter_does_not_hide_a_long_getter

- **Original id:** G4-14
- **Where:** function_length.py:35
- **Finding:** Same qualname overwrites (property setter, overload)
- **Failing input:** a 200-line getter + a 2-line setter give 2
- **Proposed fix:** Keep the max, or disambiguate
- **Verified:** repro

### GD-15 (Low) -- SyntaxError is skipped; relative_to raises

**Disposition:** RESOLVED -- files are parsed with `_core.scan_python` (relative paths never raise); an unparsable file is reported as a failure of the gate (and blocks a refresh) instead of being skipped; a missing baseline fails, and `refresh=`/`--refresh-function-length-baseline`/`PY_CI_SHARED_REFRESH` rewrites it atomically; regression test: test_function_length.py::TestAuditRegressions::test_an_unparsable_file_fails_the_gate, test_a_missing_baseline_fails_and_a_refresh_creates_it

- **Original id:** G4-15
- **Where:** function_length.py:46
- **Finding:** SyntaxError is skipped; `relative_to` raises
- **Failing input:** a broken 600-line function
- **Proposed fix:** Report parse failures
- **Verified:** read-only

### GD-16 (Low) -- The refresh advice and write_length_baseline disagree for n <= limit

**Disposition:** RESOLVED -- a baselined function that fell to or under the limit is told to "remove the entry (a refresh drops it)", matching `write_length_baseline`; a shrink still over the limit keeps the "refresh to lock the gain in" advice; regression test: test_function_length.py::TestAuditRegressions::test_advice_for_a_baselined_function_now_within_the_limit_is_to_remove_it

- **Original id:** G4-16
- **Where:** function_length.py:58
- **Finding:** The refresh advice and `write_length_baseline` disagree for n <= limit
- **Failing input:** n/a
- **Proposed fix:** Say "remove the entry"
- **Verified:** read-only

### GD-17 (Med) -- No boundary after the flag: --skip-without-db matches --skip=

**Disposition:** RESOLVED -- the flag regex requires the flag name to end (`(?![\w-])`), so `--ignore-missing-imports`, `--skip-without-db`, `--selection` are not narrowings while `--skip=`/`--ignore x` still are; regression test: test_gate_integrity.py::TestAuditRegressions::test_a_longer_flag_sharing_a_prefix_is_not_the_narrowing, test_the_real_flag_is_still_found_after_the_boundary_fix

- **Original id:** G4-17
- **Where:** gate_integrity.py:61
- **Finding:** No boundary after the flag: `--skip-without-db` matches `--skip=`
- **Failing input:** `--ignore-missing-imports`
- **Proposed fix:** Add `(?![\w-])`
- **Verified:** repro

### GD-18 (Med) -- The comment promises short flags but none are listed

**Disposition:** RESOLVED -- short flags are read per command for the program it runs (through env assignments, runners and `python -m`): bandit `-s/-x/-t/-l../-i..`, pytest `-m/-k`, with quoted values kept whole (`-m=not slow`); the same letters on another tool are ignored; regression test: test_gate_integrity.py::TestAuditRegressions::test_tool_short_flags_are_narrowings_for_their_tool_only

- **Original id:** G4-18
- **Where:** gate_integrity.py:43
- **Finding:** The comment promises short flags but none are listed
- **Failing input:** `bandit -ll -x tests`, `pytest -m "not slow"`
- **Proposed fix:** Add short flags per tool
- **Verified:** read-only

### GD-19 (Med) -- Workflow keys lack job/step, so copies collapse

**Disposition:** RESOLVED -- workflows are read as YAML: keys are `<file>::<job>::<step label>::run::<knob>` (label = id/name/`step<n>`, duplicates get `#2`) and `<file>::<job>[::<step>]::with::<input>=<value>`, so the same flag in two jobs is two declarations; the old `::run::` test key was re-framed; regression test: test_gate_integrity.py::TestAuditRegressions::test_workflow_keys_carry_job_and_step

- **Original id:** G4-19
- **Where:** gate_integrity.py:126
- **Finding:** Workflow keys lack job/step, so copies collapse
- **Failing input:** two jobs with `--ignore=C901`
- **Proposed fix:** Add job id and step
- **Verified:** read-only

### GD-20 (Med) -- Config narrowing keys are incomplete

**Disposition:** RESOLVED -- the config keys now include bandit `skips`/`exclude_dirs`/`tests`, mypy `ignore_errors`/`ignore_missing_imports`/`disable_error_code`/`follow_imports`, coverage `omit`, pytest `norecursedirs`/`testpaths`, ruff `extend-per-file-ignores`, and a pytest `addopts` string is read like a command line; regression test: test_gate_integrity.py::TestAuditRegressions::test_more_config_keys_are_narrowings

- **Original id:** G4-20
- **Where:** gate_integrity.py:91
- **Finding:** Config narrowing keys are incomplete
- **Failing input:** `[tool.bandit] skips=["B101"]`
- **Proposed fix:** Extend the list
- **Verified:** read-only

### GD-21 (Med) -- A missing pre-commit file or workflows dir gives {} and passes

**Disposition:** RESOLVED -- a given pre-commit/workflows/pyproject path that does not exist raises `FileNotFoundError` (the assert turns it into a failure); a repository without a venue passes `None`; the tests that used absent paths as "no venue" were re-framed to `None`; regression test: test_gate_integrity.py::TestAuditRegressions::test_a_missing_venue_path_raises_instead_of_passing

- **Original id:** G4-21
- **Where:** gate_integrity.py:97
- **Finding:** A missing pre-commit file or workflows dir gives {} and passes
- **Failing input:** `.github/workflow` typo
- **Proposed fix:** Fail on a missing path
- **Verified:** read-only

### GD-22 (Med) -- The completion check is a raw substring of entry

**Disposition:** RESOLVED -- the completion check matches by program tokens per command (`python3 -m mypy`, `.venv/bin/python -m mypy`, `mypy`, `uv run mypy` are all `mypy`; `mypyc_helper` is not); regression test: test_gate_integrity.py::TestAuditRegressions::test_completion_is_matched_by_program_not_substring, test_completion_wrapper_and_unrelated_tools_pass

- **Original id:** G4-22
- **Where:** gate_integrity.py:246
- **Finding:** The completion check is a raw substring of entry
- **Failing input:** `python3 -m mypy` vs `python -m mypy`
- **Proposed fix:** Tool-aware match
- **Verified:** read-only

### GD-23 (Low) -- The cov parity regex scans comments and skips templated values

**Disposition:** RESOLVED -- coverage parity strips YAML/shell comments before matching, and a non-numeric value (`${{ env.MIN }}`) is reported as uncomparable instead of skipped; regression test: test_gate_integrity.py::TestAuditRegressions::test_coverage_parity_skips_comments_and_flags_templated_values

- **Original id:** G4-23
- **Where:** gate_integrity.py:282
- **Finding:** The cov parity regex scans comments and skips templated values
- **Failing input:** `--cov-fail-under=${{ env.MIN }}`
- **Proposed fix:** Skip comments; flag non-numeric values
- **Verified:** read-only

### GD-24 (Low) -- Trigger paths-ignore is reported as a narrowing

**Disposition:** RESOLVED -- only job `with:` and step `run`/`with:` are read, so trigger filters (`on: push: paths-ignore`) are no longer reported; regression test: test_gate_integrity.py::TestAuditRegressions::test_trigger_paths_ignore_is_not_a_narrowing

- **Original id:** G4-24
- **Where:** gate_integrity.py:63
- **Finding:** Trigger `paths-ignore` is reported as a narrowing
- **Failing input:** `paths-ignore: ['docs/**']`
- **Proposed fix:** Restrict to `with:`
- **Verified:** read-only

### GD-25 (Low) -- Top-level pre-commit exclude:/files: are ignored

**Disposition:** RESOLVED -- the pre-commit config's top-level `files:`/`exclude:` are narrowings keyed `pre-commit::<top-level>::<key>=<value>`; regression test: test_gate_integrity.py::TestAuditRegressions::test_top_level_precommit_scoping_is_a_narrowing

- **Original id:** G4-25
- **Where:** gate_integrity.py:72
- **Finding:** Top-level pre-commit `exclude:`/`files:` are ignored
- **Failing input:** `exclude: ^tests/`
- **Proposed fix:** Inspect top-level keys
- **Verified:** read-only

### GD-26 (Med) -- The config flag is searched across the whole multi-line run

**Disposition:** RESOLVED -- the config flag is checked on the same shell command (line, `&&`/`;`/`|` split) that runs the tool, so a `-c` on a `python -c` line no longer satisfies bandit; regression test: test_gate_config_honesty.py::TestAuditRegressions::test_the_config_flag_must_be_on_the_bandit_line

- **Original id:** G4-26
- **Where:** gate_config_honesty.py:89
- **Finding:** The config flag is searched across the whole multi-line run
- **Failing input:** `python -c ..` + `bandit -r src`
- **Proposed fix:** Check the same line as bandit
- **Verified:** repro

### GD-27 (Med) -- scope is ignored for workflow steps; duplicate step names collide

**Disposition:** RESOLVED -- `scope` filters workflow steps too (run text, name, step or job `working-directory`), and repeated step labels in a job get `#2`, `#3` instead of overwriting; regression test: test_gate_config_honesty.py::TestAuditRegressions::test_scope_applies_to_workflow_steps, test_duplicate_step_names_do_not_collide

- **Original id:** G4-27
- **Where:** gate_config_honesty.py:62
- **Finding:** `scope` is ignored for workflow steps; duplicate step names collide
- **Failing input:** two steps named `Lint`
- **Proposed fix:** Apply `_mentions`; index labels
- **Verified:** repro

### GD-28 (Low) -- Always-zero idioms are missed

**Disposition:** RESOLVED -- `|| :`, `|| exit 0`, `|| echo ...` join `|| true`/`; exit 0`, and a `set +e` line disarms every gate tool run after it in the script; regression test: test_gate_config_honesty.py::TestAuditRegressions::test_other_always_zero_idioms_are_reported, test_set_plus_e_in_a_step_is_reported

- **Original id:** G4-28
- **Where:** gate_config_honesty.py:31
- **Finding:** Always-zero idioms are missed
- **Failing input:** `bandit -r src \|\| :`, `\|\| exit 0`, `set +e`
- **Proposed fix:** Extend `_ALWAYS_ZERO`
- **Verified:** repro

### GD-29 (Low) -- Advisory words match as substrings

**Disposition:** RESOLVED -- advisory words are matched as whole words of the id/alias/name/label, and "report" is no longer an advisory word (a "Coverage report" step is still the coverage gate); `non-blocking`/`optional` were added; regression test: test_gate_config_honesty.py::TestAuditRegressions::test_advisory_words_match_whole_words_only, test_a_name_that_says_advisory_is_exempt

- **Original id:** G4-29
- **Where:** gate_config_honesty.py:104
- **Finding:** Advisory words match as substrings
- **Failing input:** step "Coverage report" with `\|\| true` is exempt
- **Proposed fix:** Match whole words in the id
- **Verified:** read-only

### GD-30 (Low) -- Stale detection is string-based, so a label rename churns entries

**Disposition:** RESOLVED -- inherent to keying `known` on the problem text (it carries the gate's label); documented in `assert_gates_honest`'s docstring that a rename reports the old entry as no longer reproducing and the new one as new, and the behaviour is pinned by a test; regression test: test_gate_config_honesty.py::TestAuditRegressions::test_a_renamed_step_moves_its_known_entry

- **Original id:** G4-30
- **Where:** gate_config_honesty.py:115
- **Finding:** Stale detection is string-based, so a label rename churns entries
- **Failing input:** rename a step
- **Proposed fix:** Inherent; document it
- **Verified:** read-only

### GD-31 (Med) -- hasattr on a dotted attr or an extras suffix gives a false positive; an empty attr passes

**Disposition:** RESOLVED -- the attribute is resolved one `getattr` at a time (`JSONDecoder.decode`), an extras suffix (`[cli]`) is stripped before resolving, an empty module or attribute (`json:`) is a violation, a plugin entry point may name a whole module (`pytest11 = "pkg.plugin"`, which the spec allows) while a console script still needs `module:attr`, and `[project.gui-scripts]` is checked too; regression test: test_entry_points_resolvable.py::TestAttributeResolution::test_a_dotted_attribute_is_walked, test_an_extras_suffix_is_not_part_of_the_attribute, test_an_empty_side_is_a_violation, test_entry_points_resolvable.py::test_a_plugin_entry_point_may_name_a_whole_module

- **Original id:** G4-31
- **Where:** entry_points_resolvable.py:59
- **Finding:** `hasattr` on a dotted attr or an extras suffix gives a false positive; an empty attr passes
- **Failing input:** `json:JSONDecoder.decode`, `json:`
- **Proposed fix:** getattr chain; strip extras; fail on empty
- **Verified:** repro

### GD-32 (Low) -- 0 specs passes

**Disposition:** RESOLVED -- `assert_all_entry_points_resolvable` takes `min_entries` (default 1) and fails when fewer specs are declared (a `[project.script]` typo); the test that passed on an empty project was re-framed to a real entry point; regression test: test_entry_points_resolvable.py::TestAssertAllEntryPointsResolvable::test_zero_entry_points_fail

- **Original id:** G4-32
- **Where:** entry_points_resolvable.py:31
- **Finding:** 0 specs passes
- **Failing input:** typo `[project.script]`
- **Proposed fix:** `min_entries`
- **Verified:** read-only

### GD-33 (Med) -- Any from X import name is exempt as a local module; ast.Import aliases are ignored

**Disposition:** RESOLVED -- a `from X import name` binding is exempt only when `name` is a module FILE of the repository (resolved relative or from the root/src/parent-of-package), and `import pkg.mod as alias` / `import mod` bindings of non-driver modules are exempt too; regression test: test_effect_assertion_parity.py::TestAuditRegressions::test_an_object_imported_from_a_first_party_module_is_not_a_module, test_an_imported_first_party_module_is_still_exempt_by_either_import_form

- **Original id:** G4-33
- **Where:** effect_assertion_parity.py:118
- **Finding:** Any `from X import name` is exempt as a local module; `ast.Import` aliases are ignored
- **Failing input:** `from pkg.db import conn; conn.commit()` gives set()
- **Proposed fix:** Exempt only real module files
- **Verified:** repro

### GD-34 (Med) -- No stale check on accepted entries; an empty map passes

**Disposition:** RESOLVED -- `assert_effects_are_asserted` fails on accepted entries no longer found, and on an import map with fewer than `min_modules` (default 1) modules; regression test: test_effect_assertion_parity.py::TestAuditRegressions::test_the_ratchet_fails_on_a_stale_entry_and_an_empty_map

- **Original id:** G4-34
- **Where:** effect_assertion_parity.py:616
- **Finding:** No stale check on accepted entries; an empty map passes
- **Failing input:** a fixed effect keeps its key
- **Proposed fix:** Fail on accepted - found; min population
- **Verified:** read-only

### GD-35 (Med) -- Any sqlite3.connect in a test file credits all effects

**Disposition:** RESOLVED -- a real-database run is credited per test function: one function must both run against a real database (its own driver `connect`, or a real-database fixture from conftest or the same file) and call into the module under test (resolved with `_core.ImportAliases`); regression test: test_effect_assertion_parity.py::TestAuditRegressions::test_an_unrelated_sqlite_fixture_does_not_credit_the_module, test_a_test_that_uses_the_database_and_the_module_is_credited

- **Original id:** G4-35
- **Where:** effect_assertion_parity.py:407
- **Finding:** Any `sqlite3.connect` in a test file credits all effects
- **Failing input:** an unrelated sqlite fixture
- **Proposed fix:** Scope the credit per test function
- **Verified:** read-only

### GD-36 (Low) -- Any importing test without a driver patch excuses a module

**Disposition:** RESOLVED -- a self-connecting module is excused only by an importing test that does not patch the driver AND calls into the module; importing a constant no longer excuses it; regression test: test_effect_assertion_parity.py::TestAuditRegressions::test_importing_a_constant_does_not_excuse_a_self_connecting_module

- **Original id:** G4-36
- **Where:** effect_assertion_parity.py:595
- **Finding:** Any importing test without a driver patch excuses a module
- **Failing input:** `from mod import TABLE`
- **Proposed fix:** Require a call into the module
- **Verified:** read-only

### GD-37 (Low) -- A multi-package src layout gives an empty map

**Disposition:** RESOLVED -- a src directory with several packages maps every one of them (the src prefix is stripped for all); the test that enshrined the empty map was re-framed; regression test: test_effect_assertion_parity.py::TestASrcLayoutResolvesWithoutBeingTold::test_two_packages_under_src_are_both_mapped

- **Original id:** G4-37
- **Where:** effect_assertion_parity.py:514
- **Finding:** A multi-package src layout gives an empty map
- **Failing input:** `src/a`, `src/b`
- **Proposed fix:** Map all packages
- **Verified:** read-only

### GD-38 (Low) -- A global functools.cache goes stale on edits and grows without bound

**Disposition:** RESOLVED -- both process-wide `functools.cache` memos are gone; parsing goes through `_core.parse_file` (keyed on path, mtime_ns and size, one entry per path) and import records are memoised per `build_import_map` call only; the parse-once test was re-framed to count per-call import reads; regression test: test_effect_assertion_parity.py::TestAuditRegressions::test_an_edit_between_calls_is_seen, TestANestedCheckoutIsNotThisRepository::test_one_file_is_parsed_once_however_many_names_reach_it

- **Original id:** G4-38
- **Where:** effect_assertion_parity.py:440,550
- **Finding:** A global `functools.cache` goes stale on edits and grows without bound
- **Failing input:** edit between calls
- **Proposed fix:** Key on mtime
- **Verified:** read-only

### GD-39 (Med) -- Misses keep.append((spec,0)), log.log(DEBUG,..), not isnan guards and module-level code

**Disposition:** RESOLVED -- `admit_on_error` matches an append argument built from the loop variable (`keep.append((spec, 0))`), `log.log(DEBUG|INFO, ...)` counts as a quiet log and `log.log(WARNING, ...)` as a loud one, `not isnan(x)`/`not isinf(x)` count as finite guards, and module-level code is scanned as `<module>`; regression test: test_fail_open_handlers.py::test_admitting_a_tuple_built_from_the_element_is_found, test_log_dot_log_is_judged_by_its_level, test_a_not_isnan_guarded_reject_is_found, test_module_level_code_is_scanned

- **Original id:** G4-39
- **Where:** fail_open_handlers.py:115,93,136
- **Finding:** Misses `keep.append((spec,0))`, `log.log(DEBUG,..)`, `not isnan` guards and module-level code
- **Failing input:** the repro shapes
- **Proposed fix:** Extend the detectors
- **Verified:** repro

### GD-40 (Low) -- Scope is the bare name, so A.check/B.check collide

**Disposition:** RESOLVED -- the scope uses the qualified function name (`A.check`, `outer.<locals>.inner`), so baselining one class's method no longer excuses another's; regression test: test_fail_open_handlers.py::test_same_named_methods_have_distinct_scopes

- **Original id:** G4-40
- **Where:** fail_open_handlers.py:59
- **Finding:** Scope is the bare name, so `A.check`/`B.check` collide
- **Failing input:** two classes
- **Proposed fix:** Use the qualname
- **Verified:** repro

### GD-41 (Low) -- Parse errors are skipped; empty files passes; relative_to sits outside the try

**Disposition:** RESOLVED -- files are parsed with `_core.scan_python` (paths via `relative_posix`, never raising); the assert fails on unparsed files, on fewer than `min_files` parsed files, and on a missing baseline; a refresh (`refresh=`, `--refresh-fail-open-baseline`, `PY_CI_SHARED_REFRESH`) writes the counted scopes atomically, keeping notes; regression test: test_fail_open_handlers.py::test_empty_files_unparsable_files_and_a_missing_baseline_fail, test_a_refresh_writes_counted_scopes_that_then_pass, test_a_file_outside_the_root_does_not_raise

- **Original id:** G4-41
- **Where:** fail_open_handlers.py:188
- **Finding:** Parse errors are skipped; empty files passes; `relative_to` sits outside the try
- **Failing input:** `files=[]`
- **Proposed fix:** Min-scanned; report errors
- **Verified:** read-only

### GD-42 (Med) -- Top-level names only (misses try/if defs, flags imports); a str _CANARY is iterated per...

**Disposition:** RESOLVED -- module-level names are collected from every statement that runs at import (under `if`/`try`/`with`/loops) and include import bindings (`from _shared import _candidate_files`); a `str`/`bytes` `_CANARY` raises a `TypeError` instead of yielding one canary per character; the gate module is parsed with `_core.parse_file` (BOM-safe) and an unparsable gate is reported, not treated as undeclared; regression test: test_gate_population_canary.py::test_a_population_declared_under_try_or_imported_counts, test_a_string_canary_is_rejected_not_iterated, test_a_bom_gate_is_read_and_an_unparsable_one_is_reported

- **Original id:** G4-42
- **Where:** gate_population_canary.py:71,136,145
- **Finding:** Top-level names only (misses try/if defs, flags imports); a str `_CANARY` is iterated per character
- **Failing input:** `_CANARY='...'` gives 29 one-character canaries
- **Proposed fix:** Include ImportFrom and nested bindings; reject str
- **Verified:** repro

### GD-43 (Low) -- initdb output goes to DEVNULL and the log is deleted; wrong arch wheel; fetch(dest) rmt...

**Disposition:** RESOLVED -- initdb/pg_ctl output goes to log files and a failure raises `RuntimeError` carrying their tail and the server log's before the data directory is deleted; the wheel tag follows `platform.machine()` as well as the OS (`wheel_platform`); `fetch(dest)` replaces only the entries the wheel brings (bin/, lib/, ...) instead of `rmtree(dest.parent)`; the CLI drops only the leading `--` separator; regression test: test_embedded_postgres.py::test_a_failing_setup_command_raises_with_its_output, test_the_wheel_follows_the_cpu_as_well_as_the_os, test_fetch_into_a_custom_dest_leaves_its_parent_alone, test_the_cli_keeps_a_double_dash_that_belongs_to_the_command

- **Original id:** G4-43
- **Where:** embedded_postgres.py:77,126,153
- **Finding:** initdb output goes to DEVNULL and the log is deleted; wrong arch wheel; `fetch(dest)` rmtrees `dest.parent`; the CLI drops every `--`
- **Failing input:** `fetch(~/tools/bin)` deletes `~/tools`
- **Proposed fix:** Keep the log; use `platform.machine()`; remove only the unpacked dir; drop only the first `--`
- **Verified:** read-only

### GD-44 (Low) -- No test_fail_message_quality.py; format_warn skips silently on an over-long Windows com...

**Disposition:** RESOLVED -- added tests/test_fail_message_quality.py; `format_warn` splits the staged-file list into chunks of at most 8000 argument characters and runs every tool per chunk, so a ~2000-file commit no longer exceeds the Windows command-line limit; regression test: test_format_warn.py::test_main_runs_every_tool_on_every_chunk_within_the_command_limit

- **Original id:** G4-44
- **Where:** format_warn.py:54; tests/
- **Finding:** No test_fail_message_quality.py; format_warn skips silently on an over-long Windows command line
- **Failing input:** about 2000 staged files
- **Proposed fix:** Add tests; chunk the args
- **Verified:** read-only

### GE-1 (Med) -- covers() ignores schema/table

**Disposition:** RESOLVED -- `covers()` requires the same table, and the same schema when both statements name one (an unqualified name still matches, as it resolves through search_path); a three-part target takes the schema from the second-last part; regression test: test_index_coverage.py::TestAuditRegressions::test_an_index_on_another_table_does_not_cover

- **Original id:** G5-01
- **Where:** index_coverage.py:72
- **Finding:** `covers()` ignores schema/table
- **Failing input:** an index on t2 covers one on t1
- **Proposed fix:** Compare (schema, table)
- **Verified:** repro

### GE-2 (Med) -- The cast strip ::[\w ]+ eats IS NOT NULL

**Disposition:** RESOLVED -- the cast strip removes only the type token (including multi-word types and typmods), so `x::int IS NOT NULL` keeps `IS NOT NULL` and no longer equals `x::int IS NULL`; regression test: test_index_coverage.py::TestAuditRegressions::test_a_cast_strip_keeps_the_rest_of_the_predicate

- **Original id:** G5-02
- **Where:** index_coverage.py:201
- **Finding:** The cast strip `::[\w ]+` eats `IS NOT NULL`
- **Failing input:** `x::int IS NULL` covers `IS NOT NULL`
- **Proposed fix:** Strip only the type token
- **Verified:** repro

### GE-3 (Med) -- UNIQUE is not tracked

**Disposition:** RESOLVED -- `Index.unique` is parsed; a UNIQUE expectation is covered only by a UNIQUE live index on exactly the same key columns and predicate; regression test: test_index_coverage.py::TestAuditRegressions::test_a_non_unique_index_does_not_cover_a_unique_one

- **Original id:** G5-03
- **Where:** index_coverage.py:61,47
- **Finding:** UNIQUE is not tracked
- **Failing input:** a non-unique index covers a UNIQUE one
- **Proposed fix:** Add a `unique` field
- **Verified:** repro

### GE-4 (Low) -- INCLUDE is ignored

**Disposition:** RESOLVED -- `Index.include` is parsed, and an expectation's INCLUDE columns must be key or INCLUDE columns of the live index; regression test: test_index_coverage.py::TestAuditRegressions::test_include_columns_of_the_expectation_must_be_carried

- **Original id:** G5-04
- **Where:** index_coverage.py:170
- **Finding:** INCLUDE is ignored
- **Failing input:** `(x)` covers `(x) INCLUDE (y)`
- **Proposed fix:** Parse INCLUDE
- **Verified:** repro

### GE-5 (Low) -- NULLS FIRST/LAST is dropped

**Disposition:** RESOLVED -- `Index.nulls_first` records each key's effective NULLS placement (explicit, else Postgres' default: FIRST for DESC); orders must agree on direction and NULLS placement, or be a full reversal of both for a btree; regression test: test_index_coverage.py::TestAuditRegressions::test_nulls_ordering_is_part_of_the_order

- **Original id:** G5-05
- **Where:** index_coverage.py:54,149
- **Finding:** NULLS FIRST/LAST is dropped
- **Failing input:** `x DESC NULLS LAST` covers `x DESC`
- **Proposed fix:** Keep the nulls order
- **Verified:** repro

### GE-6 (Low) -- Statement split ignores quotes and block comments

**Disposition:** RESOLVED -- `statements()` is a small lexer: `--` and nested `/* */` comments are removed, and `;`/`--` inside single-quoted strings, quoted identifiers and dollar-quoted bodies are text; regression test: test_index_coverage.py::TestAuditRegressions::test_the_split_respects_quotes_and_block_comments, test_a_dollar_quoted_body_is_one_statement

- **Original id:** G5-06
- **Where:** index_coverage.py:220
- **Finding:** Statement split ignores quotes and block comments
- **Failing input:** a commented-out definition wins
- **Proposed fix:** Quote/comment-aware tokenizer
- **Verified:** repro

### GE-7 (Med) -- A sync anywhere in the region (even before the GPU work) suppresses it

**Disposition:** RESOLVED -- a synchronize counts only when it finishes after the last timed GPU (or injected) call in the region, ordered by source end position; a sync before the kernel no longer suppresses the finding; regression test: test_gpu_timing_sync.py::test_a_sync_before_the_gpu_work_does_not_count

- **Original id:** G5-07
- **Where:** gpu_timing_sync.py:207
- **Finding:** A sync anywhere in the region (even before the GPU work) suppresses it
- **Failing input:** sync, matmul, read timer
- **Proposed fix:** Require a sync after the last GPU call
- **Verified:** repro

### GE-8 (Med) -- A call in the chain means the sync is not recognised (false positive)

**Disposition:** RESOLVED -- a sync is judged on the called attribute/name itself, so a call earlier in the chain (`torch.cuda.current_stream().synchronize()`) is recognised; regression test: test_gpu_timing_sync.py::test_a_sync_reached_through_a_call_is_recognised

- **Original id:** G5-08
- **Where:** gpu_timing_sync.py:126
- **Finding:** A call in the chain means the sync is not recognised (false positive)
- **Failing input:** `torch.cuda.current_stream().synchronize()`
- **Proposed fix:** Test `func.attr`
- **Verified:** repro

### GE-9 (Med) -- timeit.default_timer and aliased timers are missed

**Disposition:** RESOLVED -- timer reads are also resolved through `_core.ImportAliases` against the qualified set (`time.perf_counter[_ns]`, `time.time[_ns]`, `time.monotonic[_ns]`, `time.process_time`, `timeit.default_timer`), `default_timer` joins the name sets, and the text prefilter also admits files importing from time/timeit; regression test: test_gpu_timing_sync.py::test_aliased_and_timeit_timers_are_timers

- **Original id:** G5-09
- **Where:** gpu_timing_sync.py:47
- **Finding:** `timeit.default_timer` and aliased timers are missed
- **Failing input:** `from time import perf_counter as pc`
- **Proposed fix:** Add them; resolve aliases
- **Verified:** repro

### GE-10 (Low) -- Module-level regions are not scanned

**Disposition:** RESOLVED -- the module body is scanned as `<module>` (no injected parameters), so a benchmark script's top-level timed region is checked; regression test: test_gpu_timing_sync.py::test_module_level_benchmark_is_scanned

- **Original id:** G5-10
- **Where:** gpu_timing_sync.py:196
- **Finding:** Module-level regions are not scanned
- **Failing input:** a benchmark script
- **Proposed fix:** Scan the module body
- **Verified:** repro

### GE-11 (Low) -- The sync regex matches _sync_to_disk

**Disposition:** RESOLVED -- any `synchroniz*`/`synchronis*` word still counts, but a bare `sync` word counts only alone or beside a device word (gpu, cuda, device, stream, torch, cupy, event, all), so `_sync_to_disk` is not a device barrier while `_gpu_sync` is; regression test: test_gpu_timing_sync.py::test_a_file_flush_named_sync_is_not_a_device_sync

- **Original id:** G5-11
- **Where:** gpu_timing_sync.py:58
- **Finding:** The sync regex matches `_sync_to_disk`
- **Failing input:** the repro
- **Proposed fix:** Tighten the stems
- **Verified:** repro

### GE-12 (Low) -- _iter_stmt_blocks is dead

**Disposition:** RESOLVED -- `_iter_stmt_blocks` (unreferenced anywhere in src/ or tests/) is deleted; `_own_stmt_blocks` carries its docstring; regression test: n/a (dead-code removal; test_gpu_timing_sync.py passes against the module without it)

- **Original id:** G5-12
- **Where:** gpu_timing_sync.py:164
- **Finding:** `_iter_stmt_blocks` is dead
- **Failing input:** n/a
- **Proposed fix:** Delete it
- **Verified:** read-only

### GE-13 (Low) -- BOM/SyntaxError files are skipped; no min_files

**Disposition:** RESOLVED -- files are parsed with `_core.scan_python` (BOM-safe); `assert_no_unsynchronized_gpu_timings` fails on unparsable files and has `min_files` (default 1, parsed files); regression test: test_gpu_timing_sync.py::test_bom_and_unparsable_files_and_the_floor

- **Original id:** G5-13
- **Where:** gpu_timing_sync.py:260-269
- **Finding:** BOM/SyntaxError files are skipped; no min_files
- **Failing input:** BOM file
- **Proposed fix:** `utf-8-sig`; report
- **Verified:** repro

### GE-14 (Med) -- BOM files are dropped from both collection and scan

**Disposition:** RESOLVED -- both constant collection and the comparison scan parse through `_core.scan_python` (BOM-safe); the assert fails on unparsable files and counts parsed files for `min_files`; regression test: test_identity_comparisons.py::test_bom_and_unparsable_files

- **Original id:** G5-14
- **Where:** identity_comparisons.py:34
- **Finding:** BOM files are dropped from both collection and scan
- **Failing input:** BOM `a is SQL`
- **Proposed fix:** `utf-8-sig`
- **Verified:** repro

### GE-15 (Low) -- Tuple, if/try and class constants are missed

**Disposition:** RESOLVED -- constants are collected from tuple/list targets paired with tuple values, from module-level `if`/`try`/`with`/loop bodies, and from class bodies (matched on `Cls.X`/`self.X`/`obj.X` attribute reads); a `+`/`%` of an already-known string constant counts too; regression test: test_identity_comparisons.py::test_tuple_if_try_and_class_constants_are_found

- **Original id:** G5-15
- **Where:** identity_comparisons.py:46-50
- **Finding:** Tuple, if/try and class constants are missed
- **Failing input:** `A,B='a','b'`
- **Proposed fix:** Walk compound statements
- **Verified:** repro

### GE-16 (Low) -- Constant names are global across files (false positive)

**Disposition:** RESOLVED -- a bare name is resolved per module: defined as a string in the same file, or imported (resolved with `_core.ImportAliases`) from a scanned module that defines it as a string; module-level names are no longer global across files (class-level constants read through an unresolvable instance still match any scanned class, documented); the explicit `names=` argument keeps its global meaning; regression test: test_identity_comparisons.py::test_a_same_named_sentinel_elsewhere_is_not_a_string_constant

- **Original id:** G5-16
- **Where:** identity_comparisons.py:54
- **Finding:** Constant names are global across files (false positive)
- **Failing input:** same-named sentinel
- **Proposed fix:** Key by (module, name)
- **Verified:** read-only

### GE-17 (Med) -- Name constructors, hashlib.new arg 2 and data= kwargs are missed

**Disposition:** RESOLVED -- constructors are resolved through `_core.ImportAliases` (`from hashlib import sha256`, `blake2b as b2`, `import hashlib as hl`), `hashlib.new(name, data)` reads its SECOND argument, and a `data=` keyword is read on any constructor; the constructor set gained sha224/sha384/sha3_*; regression test: test_hash_fed_by_array_copy.py::test_every_way_of_reaching_a_hash_constructor_is_reported

- **Original id:** G5-17
- **Where:** hash_fed_by_array_copy.py:69-77
- **Finding:** Name constructors, `hashlib.new` arg 2 and `data=` kwargs are missed
- **Failing input:** `sha256(a.tobytes())`
- **Proposed fix:** Handle them
- **Verified:** repro

### GE-18 (Low) -- tobytes(order='F') is flagged, but the suggested fix changes the digest

**Disposition:** RESOLVED -- only `tobytes()` or an explicit `order="C"` counts; `tobytes(order="F"/"A"/"K")` serialises in another order, so the buffer rewrite would change the digest and it is no longer reported; regression test: test_hash_fed_by_array_copy.py::test_other_orders_and_non_data_positions_are_not_reported

- **Original id:** G5-18
- **Where:** hash_fed_by_array_copy.py:66
- **Finding:** `tobytes(order='F')` is flagged, but the suggested fix changes the digest
- **Failing input:** the repro
- **Proposed fix:** Require no keywords
- **Verified:** repro

### GE-19 (Low) -- BOM files are skipped; no min_files

**Disposition:** RESOLVED -- files are enumerated with `_core.iter_files` and parsed with `_core.scan_python` (BOM-safe); the assert fails on unparsable files and has `min_files` (default 1, parsed files); regression test: test_hash_fed_by_array_copy.py::test_bom_unparsable_and_empty_corpora

- **Original id:** G5-19
- **Where:** hash_fed_by_array_copy.py:111
- **Finding:** BOM files are skipped; no min_files
- **Failing input:** BOM file
- **Proposed fix:** `utf-8-sig`
- **Verified:** repro

### GE-20 (Med) -- Nested functions are reported twice

**Disposition:** RESOLVED -- each function is scanned over its own scope only (nested defs, classes and lambdas excluded, `global` read per scope), and findings carry the qualified name, so a nested probe is reported once, under `outer.<locals>.inner`; regression test: test_latched_availability_flags.py::test_a_nested_probe_is_reported_once_under_its_own_function, test_an_outer_global_does_not_reach_into_a_nested_function

- **Original id:** G5-20
- **Where:** latched_availability_flags.py:105-121
- **Finding:** Nested functions are reported twice
- **Failing input:** inner in outer
- **Proposed fix:** Restrict to own scope
- **Verified:** repro

### GE-21 (Low) -- TryStar is not handled

**Disposition:** RESOLVED -- `ast.TryStar` handlers are scanned like `ast.Try` ones; regression test: test_latched_availability_flags.py::test_except_star_is_a_broad_handler_too

- **Original id:** G5-21
- **Where:** latched_availability_flags.py:110
- **Finding:** TryStar is not handled
- **Failing input:** `except* Exception:`
- **Proposed fix:** Accept TryStar
- **Verified:** repro

### GE-22 (Low) -- builtins.Exception is not treated as broad; BOM files are skipped

**Disposition:** RESOLVED -- handler types are resolved with `_core.ImportAliases`, so `builtins.Exception`, `builtins.BaseException` and aliases (`from builtins import Exception as E`) are broad, alone or in a tuple, while `requests.Timeout` is not; files go through `_core.iter_files`/`scan_python` (BOM-safe) and the assert fails on unparsable files and has `min_files`; regression test: test_latched_availability_flags.py::test_builtins_and_aliased_exception_are_broad, test_bom_unparsable_and_empty_corpora

- **Original id:** G5-22
- **Where:** latched_availability_flags.py:79-82,101
- **Finding:** `builtins.Exception` is not treated as broad; BOM files are skipped
- **Failing input:** `except builtins.Exception:`
- **Proposed fix:** Handle `.attr`; `utf-8-sig`
- **Verified:** read-only / repro (BOM)

### GE-23 (Med) -- Class bodies, decorators and defaults (all run at import) are skipped

**Disposition:** RESOLVED -- the import-time walk descends into class bodies (plus class decorators, bases and keywords) and into function/lambda decorators and default values, while function and lambda bodies stay excluded; regression test: test_import_side_effects.py::TestWhatRunsAtImport::test_a_class_body_decorator_and_default_run_at_import

- **Original id:** G5-23
- **Where:** import_side_effects.py:47
- **Finding:** Class bodies, decorators and defaults (all run at import) are skipped
- **Failing input:** `class T: os.environ['A']='1'`
- **Proposed fix:** Descend into them
- **Verified:** repro

### GE-24 (Low) -- An aliased environ is missed

**Disposition:** RESOLVED -- `environ`, `putenv` and `unsetenv` are resolved through `_core.ImportAliases` (`from os import environ as E`, `import os as o`, `from os import putenv as p`); reads stay unreported; regression test: test_import_side_effects.py::TestWhatRunsAtImport::test_an_aliased_environ_is_resolved

- **Original id:** G5-24
- **Where:** import_side_effects.py:50
- **Finding:** An aliased environ is missed
- **Failing input:** `from os import environ as E`
- **Proposed fix:** Resolve aliases
- **Verified:** repro

### GE-25 (Med) -- Refresh via sys.argv is ignored under xdist

**Disposition:** RESOLVED -- both `import_side_effects` and `loc_budget` decide a refresh with `_core.refresh_requested` (pytest option through the new `request=` kwarg, `PY_CI_SHARED_REFRESH` inherited by xdist workers, then argv), and both take `refresh=`; `loc_budget.register_refresh_option` is a thin wrapper over `register_refresh_options`; regression test: test_import_side_effects.py::TestTheScan::test_the_refresh_reaches_an_xdist_worker_through_the_environment, test_loc_budget.py::TestAuditRegressions::test_the_env_refresh_reaches_an_xdist_worker

- **Original id:** G5-25
- **Where:** import_side_effects.py:124; loc_budget.py:75
- **Finding:** Refresh via sys.argv is ignored under xdist
- **Failing input:** `-n 1 --refresh-loc-budget-baseline`
- **Proposed fix:** `request.config`
- **Verified:** repro

### GE-26 (Med) -- With first_party, a violation is attributed to the innermost frame, so a first-party re...

**Disposition:** RESOLVED -- the probe records, per violation, every non-plumbing module on the stack up to the import frame that is running it; with `first_party`, a violation counts when any module in that chain is first-party (a first-party top level calling `requests.get()` counts, a dependency's own import-time effect still does not) and is attributed to the innermost first-party module; `ProbeResult.chains` exposes the chain; regression test: test_import_side_effects.py::TestFirstPartyAttribution::test_a_first_party_call_through_a_dependency_counts, test_a_dependency_s_own_import_time_effect_does_not

- **Original id:** G5-26
- **Where:** import_side_effects.py:292,318
- **Finding:** With first_party, a violation is attributed to the innermost frame, so a first-party `requests.get` is recorded as urllib3 and filtered
- **Failing input:** a first-party swallowed request
- **Proposed fix:** Any first-party frame on the stack counts
- **Verified:** read-only

### GE-27 (Low) -- BOM/SyntaxError test files are skipped

**Disposition:** RESOLVED -- test files are enumerated with `_core.iter_files` and parsed with `_core.scan_python` (BOM-safe); an unparsable test file fails the gate; a given baseline path that does not exist fails instead of accepting nothing; new tests/test_import_side_effects.py; regression test: test_import_side_effects.py::TestTheScan::test_a_bom_test_file_is_scanned, test_an_unparsable_test_file_fails_the_gate, test_a_missing_baseline_fails

- **Original id:** G5-27
- **Where:** import_side_effects.py:93-95
- **Finding:** BOM/SyntaxError test files are skipped
- **Failing input:** BOM test_x.py
- **Proposed fix:** `utf-8-sig`; report
- **Verified:** repro

### GE-28 (Med) -- bound walks the whole module, so function locals count as module attributes

**Disposition:** RESOLVED -- `bound`/`defined` come from module-scope statements only (the body and its `if`/`try`/`with`/loop bodies, including for/with/except/walrus targets) plus names a function declares `global` and assigns; a function local no longer makes `m.NAME = 2` look legitimate; dynamic forwarding detection still looks everywhere; regression test: test_inert_patch_targets.py::TestAuditRegressions::test_a_function_local_is_not_a_module_attribute, test_module_scope_bindings_under_if_try_with_for_and_global_count

- **Original id:** G5-28
- **Where:** inert_patch_targets.py:158-168
- **Finding:** `bound` walks the whole module, so function locals count as module attributes
- **Failing input:** `def f(): NAME=1`; `m.NAME=2` passes
- **Proposed fix:** Module-scope bindings only
- **Verified:** repro

### GE-29 (Low) -- A BOM module is dropped from the index

**Disposition:** RESOLVED -- `module_index` enumerates with `_core.iter_files` and parses with `_core.scan_python` (BOM-safe); unparsable modules can be collected through the new `unparsed=` kwarg, and an unparsable test file is reported by `scan` as an `<unparsable>` finding; regression test: test_inert_patch_targets.py::TestAuditRegressions::test_a_bom_module_is_indexed, test_an_unparsable_test_file_is_a_finding, test_an_unparsable_module_is_collected

- **Original id:** G5-29
- **Where:** inert_patch_targets.py:217
- **Finding:** A BOM module is dropped from the index
- **Failing input:** `pkg/bom.py`
- **Proposed fix:** `utf-8-sig`
- **Verified:** repro

### GE-30 (Low) -- setattr, AnnAssign and tuple targets are missed

**Disposition:** RESOLVED -- `setattr(m, "X", v)`, annotated (`m.X: int = v`), augmented and tuple/starred-unpacked attribute targets are all read as module attribute sets; regression test: test_inert_patch_targets.py::TestAuditRegressions::test_other_ways_of_setting_an_attribute_are_seen, test_setattr_of_an_existing_name_is_fine

- **Original id:** G5-30
- **Where:** inert_patch_targets.py:321
- **Finding:** setattr, AnnAssign and tuple targets are missed
- **Failing input:** `setattr(m,'ZZZ',1)`
- **Proposed fix:** Handle them
- **Verified:** repro

### GE-31 (Low) -- If recurses twice: O(2^depth)

**Disposition:** RESOLVED -- an `if` branch is walked once and its hits filtered by the guard, instead of walking it twice per level (O(2^depth)); regression test: test_inert_patch_targets.py::TestAuditRegressions::test_deeply_nested_ifs_are_walked_once

- **Original id:** G5-31
- **Where:** inert_patch_targets.py:331-337
- **Finding:** `If` recurses twice: O(2^depth)
- **Failing input:** 20 nested ifs means about 1M visits
- **Proposed fix:** Recurse once
- **Verified:** read-only

### GE-32 (Low) -- Duplicate helpers kept only for a parity test

**Disposition:** RESOLVED -- `_forwards_dynamically` and `_module_level_names` (kept only for a parity test against `_module_facts`) are deleted along with that parity test; `_module_facts` is the one implementation and is exercised through `module_index` by every test in the file; regression test: test_inert_patch_targets.py::TestAuditRegressions::test_module_scope_bindings_under_if_try_with_for_and_global_count

- **Original id:** G5-32
- **Where:** inert_patch_targets.py:106,149
- **Finding:** Duplicate helpers kept only for a parity test
- **Failing input:** n/a
- **Proposed fix:** Delete them
- **Verified:** read-only

### GE-33 (Med) -- Depends on the user's diff prefix config

**Disposition:** RESOLVED -- `git diff` runs with explicit `--src-prefix=a/ --dst-prefix=b/`, so `diff.noprefix`/`diff.mnemonicPrefix` cannot change the header paths, and only the `b/` destination prefix is stripped; regression test: test_git_changed_lines.py::test_the_users_diff_prefix_config_does_not_change_the_paths

- **Original id:** G5-33
- **Where:** git_changed_lines.py:134
- **Finding:** Depends on the user's diff prefix config
- **Failing input:** `diff.noprefix` / `diff.mnemonicPrefix`
- **Proposed fix:** Pass explicit `--src/dst-prefix`
- **Verified:** repro

### GE-34 (Low) -- Untracked-file line count off by one

**Disposition:** RESOLVED -- an untracked file's line count is the number of newlines plus one only for an unterminated last line (bytes, no decode), and an empty file contributes no range; regression test: test_git_changed_lines.py::test_an_untracked_file_has_exactly_its_lines, test_an_empty_untracked_file_has_no_lines

- **Original id:** G5-34
- **Where:** git_changed_lines.py:155
- **Finding:** Untracked-file line count off by one
- **Failing input:** `"e\n"` gives `range(1,3)`
- **Proposed fix:** `len(splitlines())`
- **Verified:** repro

### GE-35 (Low) -- No --no-textconv

**Disposition:** RESOLVED -- `--no-textconv` is passed, so a configured textconv driver cannot replace the file's lines with its rendering (verified: with the driver the old command reported hunks at 2/5/8 for a one-line change); regression test: test_git_changed_lines.py::test_a_textconv_driver_does_not_rewrite_the_diff

- **Original id:** G5-35
- **Where:** git_changed_lines.py:101
- **Finding:** No `--no-textconv`
- **Failing input:** a textconv driver
- **Proposed fix:** Add it
- **Verified:** read-only

### GE-36 (Med) -- Extras and uv/poetry source tables are not scanned

**Disposition:** RESOLVED -- the PEP 508 pattern accepts an extras suffix on the name (`foo[extra] @ git+...`), and `[tool.uv.sources]` plus poetry `dependencies`/`dev-dependencies`/`group.*.dependencies` git tables are checked (only a full-SHA `rev` pins; `branch`/`tag`/nothing is reported as `name: branch=main`), honouring `allow_unpinned_url_prefixes`; regression test: test_git_dependency_pins.py::TestAuditRegressions::test_a_dependency_with_extras_is_scanned, test_uv_and_poetry_source_tables_are_scanned

- **Original id:** G5-36
- **Where:** git_dependency_pins.py:41
- **Finding:** Extras and uv/poetry source tables are not scanned
- **Failing input:** `foo[extra] @ git+...@main`
- **Proposed fix:** Allow extras; parse the tables
- **Verified:** repro

### GE-37 (Low) -- Uppercase SHAs are reported as unpinned

**Disposition:** RESOLVED -- a 40-hex SHA is recognised in either case, and pins are lower-cased before agreement is judged; regression test: test_git_dependency_pins.py::TestAuditRegressions::test_an_uppercase_sha_is_a_full_pin, test_git_dependency_pins_agree.py::TestAuditRegressions::test_an_uppercase_sha_is_the_same_commit

- **Original id:** G5-37
- **Where:** git_dependency_pins.py:42
- **Finding:** Uppercase SHAs are reported as unpinned
- **Failing input:** a 40-hex uppercase SHA
- **Proposed fix:** `re.I`
- **Verified:** repro

### GE-38 (Low) -- No left boundary on the package name

**Disposition:** RESOLVED -- the package name in every pin pattern needs a left boundary (`(?<![\w.-])`), and the SHA a right one; regression test: test_git_dependency_pins_agree.py::TestAuditRegressions::test_a_longer_name_ending_in_the_package_is_not_its_pin

- **Original id:** G5-38
- **Where:** git_dependency_pins.py:171
- **Finding:** No left boundary on the package name
- **Failing input:** `notpyutilz.git@..`
- **Proposed fix:** Lookbehind
- **Verified:** repro

### GE-39 (Med) -- _checkout_root climbs to any .git, including the consuming repo when a .venv is inside it

**Disposition:** RESOLVED -- `_checkout_root` stops at a `site-packages`/`dist-packages` directory and accepts a `.git` only when that checkout tracks the package's file (`git ls-files --error-unmatch`), so a venv inside the consuming repo, or an untracked copy under some repo, reads as a non-checkout install; regression test: test_git_dependency_pins_agree.py::TestCheckoutOwnership::test_a_package_installed_into_a_venv_inside_a_repo_is_not_that_repo, test_an_untracked_package_under_a_repo_is_not_its_checkout

- **Original id:** G5-39
- **Where:** git_dependency_pins.py:224-256
- **Finding:** `_checkout_root` climbs to any .git, including the consuming repo when a `.venv` is inside it
- **Failing input:** `repo/.venv/.../pyutilz`
- **Proposed fix:** Stop at site-packages; verify ownership
- **Verified:** read-only

### GE-40 (Low) -- Unreadable files are skipped; min_pins=0 with no pins raises StopIteration

**Disposition:** RESOLVED -- files are read with `_core.read_source`; `pinned_shas(unreadable=...)` collects files it cannot read and `assert_pins_agree` fails on them; with `min_pins=0` and no pins it returns `""` instead of raising `StopIteration`; relative paths use `relative_posix`; regression test: test_git_dependency_pins_agree.py::TestAuditRegressions::test_an_unreadable_file_fails, test_no_pins_with_min_pins_zero_returns_empty

- **Original id:** G5-40
- **Where:** git_dependency_pins.py:184-187,207
- **Finding:** Unreadable files are skipped; `min_pins=0` with no pins raises StopIteration
- **Failing input:** `assert_pins_agree([], "x", min_pins=0)`
- **Proposed fix:** Report; return early
- **Verified:** read-only

### GE-41 (Med) -- Counts are keyed by exact code, so a prefix ignore reads as 0

**Disposition:** RESOLVED -- `ruff_counts` counts every finding whose code starts with an ignored code, so a prefix (`E`, `PLR09`) or `ALL` reads the findings it hides instead of 0; regression test: test_ignore_ratchet.py::test_a_prefix_ignore_counts_every_code_under_it

- **Original id:** G5-41
- **Where:** ignore_ratchet.py:80,91
- **Finding:** Counts are keyed by exact code, so a prefix ignore reads as 0
- **Failing input:** `ignore="E"` with 500 E501 says remove it
- **Proposed fix:** Aggregate by prefix
- **Verified:** repro

### GE-42 (Low) -- Syntax-error items (code None) are dropped

**Disposition:** RESOLVED -- a ruff item with no rule code (older ruff) or `invalid-syntax` (current ruff) makes `ruff_counts` raise, naming the files, since the ignored codes cannot be counted in a file ruff could not parse; the yaml/baseline readers use `_core.read_source` and the baseline writer `atomic_write_text`; regression test: test_ignore_ratchet.py::test_a_file_ruff_cannot_parse_raises

- **Original id:** G5-42
- **Where:** ignore_ratchet.py:79
- **Finding:** Syntax-error items (code None) are dropped
- **Failing input:** an unparseable file
- **Proposed fix:** Fail on None
- **Verified:** read-only

### GE-43 (Med) -- Common staging-sweep forms are missed

**Disposition:** RESOLVED -- staging sweeps are matched anywhere on a line (after `&&`, `;`, `then`, ...), with `git -C dir`/`-c k=v` before the subcommand, combined flags carrying A or u (`-Av`), `git add .`/`:/`/`*`, and `git commit -a`/`-am`/`--all`; `git add -p` and explicit paths are not flagged; hook files are read with `_core.read_source` and an unreadable one is reported; regression test: test_hook_hygiene.py::TestAuditRegressions::test_every_staging_sweep_is_flagged, test_targeted_staging_is_not_flagged

- **Original id:** G5-43
- **Where:** hook_hygiene.py:56
- **Finding:** Common staging-sweep forms are missed
- **Failing input:** `git add .`, `git add -Av`, `git -C . add -A`, `git commit -a`
- **Proposed fix:** Extend the regex
- **Verified:** repro

### GE-44 (Low) -- Block ends at the first fi; split("else") splits substrings

**Disposition:** RESOLVED -- the guard's `if` block is closed by the `fi` at its own depth (nested `if ... fi` counted), and its else branch is found by an `else` line at that depth, not by splitting on the substring "else"; regression test: test_hook_hygiene.py::TestAuditRegressions::test_a_nested_if_does_not_end_the_guard_block_early, test_a_nested_else_is_not_the_guards_else, test_the_word_elsewhere_is_not_an_else

- **Original id:** G5-44
- **Where:** hook_hygiene.py:112-118
- **Finding:** Block ends at the first `fi`; `split("else")` splits substrings
- **Failing input:** nested if; "elsewhere"
- **Proposed fix:** Depth tracking; split on the line
- **Verified:** read-only

### GE-45 (Low) -- lines.index picks the first duplicate; "SKIPPED" in a comment exempts the script

**Disposition:** RESOLVED -- the self-assert exemption only counts on a non-comment line that says it (echo/printf/die/fail/warn/exit), so `# SKIPPED legacy` no longer exempts a guard; `lines.index(line)` was replaced by the enumerate index (on inspection the first selection line is always the first occurrence of its text, since the scan returns at the first match, so that half could not misfire in the current flow; the index is now positional regardless); guard scripts are read with `_core.read_source` and an unreadable one is reported; regression test: test_guard_population.py::TestAuditRegressions::test_a_comment_saying_skipped_does_not_exempt_the_guard, test_an_echoed_skipped_still_exempts_it

- **Original id:** G5-45
- **Where:** guard_population.py:126,188
- **Finding:** `lines.index` picks the first duplicate; "SKIPPED" in a comment exempts the script
- **Failing input:** `# SKIPPED legacy`
- **Proposed fix:** enumerate; restrict to echo/exit lines
- **Verified:** read-only

### GE-46 (Med) -- Python imports never match, yet the file counts as examined

**Disposition:** RESOLVED -- `.py` files are read with `ast` (through `_core.parse_source`): `import a.b`, `from pkg import mod`, `from pkg.mod import x` and relative `from ..providers import p` are resolved to the repo-relative module file (package roots and `src/` honoured, third-party/stdlib skipped), so Python imports are judged by the same rules; an unparsable file is reported instead of counted as examined-clean; regression test: test_import_layering.py::TestAuditRegressions::test_python_imports_across_the_boundary_are_flagged, test_python_imports_inside_the_layer_or_external_pass, test_an_unparsable_python_file_is_reported

- **Original id:** G5-46
- **Where:** import_layering.py:46-49
- **Finding:** Python imports never match, yet the file counts as examined
- **Failing input:** `from ..providers import p`
- **Proposed fix:** ast-based Python imports, or refuse .py rules
- **Verified:** repro

### GE-47 (Low) -- Matches in comments; rglob walks .git, node_modules and .venv

**Disposition:** RESOLVED -- `//` and `/* */` comments are stripped outside string literals before the import regexes run (newlines kept, so line numbers hold), and files are enumerated with `_core.iter_files` (`DEFAULT_EXCLUDE` plus `.dart_tool`, pruned, git-aware) instead of `rglob`; regression test: test_import_layering.py::TestAuditRegressions::test_imports_named_in_comments_are_not_imports, test_a_real_import_after_a_block_comment_keeps_its_line, test_excluded_directories_are_not_walked

- **Original id:** G5-47
- **Where:** import_layering.py:48,111
- **Finding:** Matches in comments; `rglob` walks .git, node_modules and .venv
- **Failing input:** `// see from '../../providers/x'`
- **Proposed fix:** Skip comments; prune dirs
- **Verified:** read-only

### GE-48 (Med) -- A missing scanned dir gives 0 files and passes

**Disposition:** RESOLVED -- `python_files` enumerates through `_core.iter_files`, which raises `CorpusError` for a scanned entry that does not exist; the assert turns that into a failure and also fails when fewer than `min_files` (default 1) files parsed; new tests/test_llm_call_archive_gate.py; regression test: test_llm_call_archive_gate.py::TestTheCorpus::test_a_missing_scanned_dir_fails, test_an_empty_scan_fails

- **Original id:** G5-48
- **Where:** llm_call_archive_gate.py:57
- **Finding:** A missing scanned dir gives 0 files and passes
- **Failing input:** `scanned=["srcx"]`
- **Proposed fix:** Fail on empty
- **Verified:** read-only

### GE-49 (Low) -- BOM/non-UTF8 crashes; each file is parsed 3-4 times

**Disposition:** RESOLVED -- each file is parsed once through `_core.parse_source` (BOM-safe, cached; the three checks reuse the tree), and an unparsable file is reported by the assert instead of crashing it; the `_*_lines` helpers accept a tree as well as source text; regression test: test_llm_call_archive_gate.py::TestTheCorpus::test_a_bom_file_is_read_and_a_broken_one_is_reported

- **Original id:** G5-49
- **Where:** llm_call_archive_gate.py:82
- **Finding:** BOM/non-UTF8 crashes; each file is parsed 3-4 times
- **Failing input:** BOM file
- **Proposed fix:** Parse once with `utf-8-sig`
- **Verified:** repro

### GE-50 (Low) -- The default SDK set misses .responses.create, .messages.stream and .chat.completions.pa...

**Disposition:** RESOLVED -- the default SDK set adds `responses.create`/`responses.stream`, `messages.stream`, `completions.parse` and `generate_content_async`; the unwrapping factory is resolved through `_core.ImportAliases`, so `import pyutilz.llm.factory as f; f.get_llm_provider()` and `from pyutilz.llm import factory` are caught while a same-named factory from another module is not; regression test: test_llm_call_archive_gate.py::TestTheShapes::test_newer_sdk_payload_calls_are_direct_calls, test_the_unwrapping_factory_is_found_through_any_import, test_a_same_named_factory_from_elsewhere_is_not

- **Original id:** G5-50
- **Where:** llm_call_archive_gate.py:44,89
- **Finding:** The default SDK set misses `.responses.create`, `.messages.stream` and `.chat.completions.parse`; module-alias factory calls are missed
- **Failing input:** `import pyutilz.llm.factory as f; f.get_llm_provider()`
- **Proposed fix:** Extend; resolve Import aliases
- **Verified:** read-only

### GE-51 (Low) -- A missing baseline re-seeds and skips (green)

**Disposition:** RESOLVED -- a missing baseline fails ("does not exist ... Create it with --refresh-loc-budget-baseline") instead of re-seeding and skipping; only a refresh writes it (atomically); the tests that relied on first-run seeding now seed with `refresh=True`; regression test: test_loc_budget.py::TestAuditRegressions::test_a_missing_baseline_fails_instead_of_reseeding

- **Original id:** G5-51
- **Where:** loc_budget.py:184
- **Finding:** A missing baseline re-seeds and skips (green)
- **Failing input:** move the baseline
- **Proposed fix:** Fail unless refreshing
- **Verified:** read-only

### GE-52 (Low) -- _loc returns 0 on OSError and raises on non-UTF8; files=[] passes

**Disposition:** RESOLVED -- lines are counted from `_core.read_source` (BOM-safe, interpreter decoding); an unreadable or undecodable file fails the gate instead of counting 0 or raising, the assert has `min_files` (default 1), and relative paths never raise; regression test: test_loc_budget.py::TestAuditRegressions::test_an_unreadable_file_fails_rather_than_counting_zero, test_no_files_fails, test_a_bom_and_a_missing_final_newline_count_true_lines

- **Original id:** G5-52
- **Where:** loc_budget.py:81
- **Finding:** `_loc` returns 0 on OSError and raises on non-UTF8; `files=[]` passes
- **Failing input:** an unreadable file
- **Proposed fix:** Report; `min_files`
- **Verified:** read-only

### GE-53 (Low) -- git rev-parse --git-dir ignores core.hooksPath

**Disposition:** RESOLVED -- the hooks directory comes from `git rev-parse --git-path hooks`, which honours `core.hooksPath` (and a linked worktree's shared hooks), resolved against the working directory when relative; regression test: test_install_safe_hook.py::test_a_configured_hooks_path_is_patched

- **Original id:** G5-53
- **Where:** install_safe_hook.py:40
- **Finding:** `git rev-parse --git-dir` ignores `core.hooksPath`
- **Failing input:** hooksPath set
- **Proposed fix:** Use `git rev-parse --git-path hooks`
- **Verified:** read-only

### GE-54 (Low) -- read_text/write_text use the locale encoding and rewrite line endings

**Disposition:** RESOLVED -- the hook is read and written as bytes and only the invocation bytes are replaced, so its encoding and line endings are preserved exactly (no locale codec, no LF->CRLF translation); regression test: test_install_safe_hook.py::test_encoding_and_line_endings_are_preserved

- **Original id:** G5-54
- **Where:** install_safe_hook.py:54-66
- **Finding:** `read_text`/`write_text` use the locale encoding and rewrite line endings
- **Failing input:** non-ASCII hook on Windows
- **Proposed fix:** Explicit utf-8; `newline=""`
- **Verified:** read-only
