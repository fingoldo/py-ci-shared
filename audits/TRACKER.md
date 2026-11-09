# Findings tracker

One row per finding, all rounds. The disposition and its reasoning live in the finding itself; this
table exists so the state of a round is readable without opening five files.

## Round 2026-11-10 - full agent audit of py_ci_shared (dimensions 40-52, dimensions 40-52 complete)

57 findings from 13 reports in `2026-11-10/`. Every row is OPEN: the dispositions proposed in each report are not yet applied.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 40-F1 | P2 | The gate cache reads only the config's kwargs for the network flag, so a network gate's PASS is cached and replayed | RESOLVED: the runner merges the gate's signature defaults under the configured kwargs before the cache key is built; `test_a_network_gate_whose_network_flag_is_only_a_signature_default_is_never_cached` pins it |
| 40-F2 | P2 | The cache key has no interpreter or installed-tool identity, so a verdict from one Python or tool version replays under another | PARTIALLY RESOLVED: the key now carries the interpreter implementation and full version (`_interpreter_identity`, pinned by `test_a_verdict_from_another_interpreter_is_not_replayed`); installed-tool versions (ruff, mypy, black) are still not in the key, OPEN for that half |
| 40-F3 | P3 | Several gates skip a file that cannot be parsed or decoded, so its findings are never produced | OPEN |
| 40-F4 | P3 | stale_source_citations drops every extra-glob file it cannot read, so stale citations in that file pass | OPEN |
| 41-F1 | P3 | printed_advice treats the noun "supply" as an instruction | RESOLVED: the `supply` alternative needs an article or "more" before its parameter; `test_supply_as_a_noun_is_not_advice_but_supply_with_a_parameter_is` pins both sides |
| 41-F2 | P3 | systemexit_in_library attributes `os._exit()` to SystemExit in its message | RESOLVED: `_mechanism` gives `os._exit()` its own explanation (the process ends at once, no finally or atexit); `test_an_os_exit_finding_names_the_process_mechanism_not_systemexit` pins it |
| 42-F12 | P2 | A baseline in list form, or with a non-object `accepted`, reads as empty in baseline_ratchet, so an empty scan passes | RESOLVED: non-object baseline or accepted raises ValueError; test_a_list_shaped_baseline_is_refused_not_read_as_empty |
| 42-F13 | P2 | A refresh after a rename deletes the human note of the renamed entry before the growth refusal, and the re-added entry comes back with no note | OPEN |
| 42-F14 | P3 | baseline_ratchet.Baseline.load uses plain json.load, so a duplicate key is last-wins silently; 42-F8 fixed this only in _core | RESOLVED: `Baseline.load` uses the duplicate-key-rejecting hook of `_core.baseline`; `test_a_duplicate_key_in_the_baseline_is_an_error_not_a_silent_last_wins` pins it |
| 43-F1 | P3 | Five modules are `kind = "gate"` and `cli = true`; the registry, README and docstrings define `kind = "cli"` as the command-line tool | RESOLVED: the rule is written down in the registry docstring and the README (a module with `assert_*` entries and a `main` is a gate with `cli = true`); the five stay `gate` because they have entries a consumer calls |
| 43-F2 | P3 | CHANGELOG has no heading for the tags v1.22.1 and v1.22.6 | OPEN |
| 43-F3 | P3 | CLAUDE.md points at `audits/2026-11-07/51`, which is not a file | RESOLVED: CLAUDE.md now names `audits/2026-11-07/51_security_supply_chain.md` |
| 43-F4 | P3 | Two docstrings cite audit folders that exist only in other repositories | OPEN |
| 44-F1 | P3 | env_flag_parsing: the NotIn membership shape and the case-insensitive comparison are untested; both mutations survive | RESOLVED: `test_a_negated_membership_in_boolean_words_is_judged_like_a_positive_one` and `test_an_upper_case_boolean_word_is_judged_case_insensitively`; the mutation runs were not repeated after the tests were added |
| 44-F2 | P3 | test_a_stable_old_file_is_served_without_reading_it_again asserts a property the fast path alone provides, and passes with the fast path disabled | RESOLVED: the test also asserts the file is in the fast-path table `_FAST`, which only the stat path fills; the teeth check is by construction (no mutation run), see the test's comment |
| 45-F9 | P2 | `_split_command` with `posix=False` keeps quote characters, so a quoted executable path in BLACK_CMD fails on Windows | RESOLVED: quote pair stripped per token on Windows; test_a_quoted_windows_executable_path_is_one_argument_without_its_quotes |
| 45-F10 | P3 | `datetime.fromisoformat` rejects a trailing `Z` on Python 3.9 and 3.10, so the coverage staleness check is silently skipped there | RESOLVED: Z read as +00:00 before fromisoformat; test pins the expression only (gate path not exercised) - follow-up |
| 46-F15 | P2 | ci-green and the release wait loop die on one transient GitHub API error instead of reporting pending | RESOLVED: `wait_for_ci` retries a 5xx or network error three times, then returns pending with "GitHub API unreachable: re-run this job"; a 4xx still raises; `test_wait_for_ci_survives_a_transient_api_error_and_reports_pending_when_it_persists` and `test_wait_for_ci_still_raises_on_a_client_error` pin both |
| 46-F16 | P2 | rollback fails open: when the releases API is unreadable, the "must be a published release" check is skipped silently | OPEN |
| 46-F17 | P3 | when the releases API is unreadable, prepare and the verify job fall back to local tags and blame the wrong version | OPEN |
| 46-F18 | P3 | pinned tool versions have no automated update channel: dependabot covers only github-actions | OPEN |
| 46-F19 | P3 | the mypy pin is written three times and only one copy is tested against tool_versions | RESOLVED: `test_the_reusable_mypy_workflows_default_to_the_pinned_mypy` holds the two reusable workflows' `mypy-version` defaults to `MYPY_VERSION` (the self-ci pin was already tested) |
| 46-F20 | P3 | self-ci's black smoke run is unpinned, while black-filtered.yml pins the same invocation | RESOLVED: self-ci's smoke step pins `uvx black==$BLACK_VERSION`; `test_self_ci_never_runs_an_unpinned_black` guards it |
| 46-F21 | P3 | the Codecov composite action has no caller in this repository, and its CODECOV_TOKEN secret is referenced by no workflow | DOC: kept. The action is a consumer-facing composite: the 2026-09-24 adoption matrix records mlframe and pyutilz calling `action:upload-codecov` (`audits/implemented/2026-09-24/adoption_matrix_2026-09-24.md`), and their CODECOV_TOKEN is their own secret. Deleting it would break both |
| 47-F6 | P1 | The documented upgrade path (`@v1`) is reported as a failing `moving-pin` by the consumer-pins job | OPEN |
| 47-F7 | P1 | The waiver file has no waiver rows, so the "PARTLY RESOLVED" disposition of 47-F1 is not true in the repo | OPEN |
| 47-F8 | P2 | Running the matrix without `--resolve-in` turns the stale-pin, unresolvable-pin and waiver checks off silently, with exit 0 | PARTIALLY RESOLVED: the run always names the checks that are off (`test_running_without_resolve_in_says_which_checks_are_off`); the exit code stays 0 because failing would turn green consumers red, a behaviour change that needs the owner's call |
| 47-F9 | P2 | Private consumers skipped for want of a token are missing from the adoption matrix and its job summary | RESOLVED: the repos file lists skipped consumers in `[[skipped]]` tables, and `_consumers not-checked` appends a "Not checked" table to the matrix report and job summary (`test_a_skipped_private_consumer_is_named_in_the_report_and_still_not_a_repo`) |
| 47-F10 | P2 | A SHA-pinned `uses:` with no `py-ci-shared-ref` input is not reported, though README says it gets the previous release's configs | RESOLVED (report-only): `python -m py_ci_shared.adoption_sha_ref` flags a between-release SHA pin with no matching `py-ci-shared-ref` and runs in consumer-pins.yml; release-tag commits are exempt; failing it stays a later decision, once consumers carry the input (`tests/test_adoption_sha_ref.py`) |
| 47-F11 | P3 | Matrix failure messages do not say what the consumer should do, and the job summary omits two failure kinds | RESOLVED: each of the four messages ends with the action to take, and the summary names unresolvable pins and bad pin comments; `tests/test_adoption_matrix.py` and `tests/test_adoption_pin_comment.py` updated to the new text |
| 47-F12 | P3 | The lag gate of `ci_pin_version_skew` is silently off unless a caller passes `known_releases`, and no consumer file in the workspace passes it | RESOLVED: without release tags the gate records a note that the lag check was NOT run (a pass note, no new failure); `test_without_known_releases_the_gate_records_that_the_lag_check_did_not_run` pins it |
| 49-F1 | P2 | The 49-F8 fix is incomplete: 14 sites still hard-code a `handler at line N` that points at the wrong line | RESOLVED: the 14 warnings drop the hard-coded file and line (the logger name already names the module); `test_fallback_warnings_do_not_hard_code_a_line_number` scans `src/` and fails on any `handler at line N` |
| 49-F2 | P2 | A malformed function-length baseline is reported as a stale entry, so the developer deletes the wrong thing | RESOLVED: `assert_functions_do_not_grow` checks the baseline is a `{"path::function": int}` object and fails naming the shape; `test_a_list_shaped_baseline_is_named_as_malformed_not_as_a_stale_entry` pins it |
| 49-F3 | P2 | Under GitHub Actions a FAIL or ERROR row emits no `::error` annotation; only budget, note and skip rows annotate | RESOLVED: `_print_result` emits `::error title=py-ci-shared <gate> FAIL|ERROR::<first message line>` under Actions; `test_a_failed_or_errored_gate_gets_an_error_annotation_under_actions` pins it |
| 49-F4 | P2 | The `budget` rounding says `over its 0s budget` for any `budget_s` under 0.5 | RESOLVED: budget printed with :g; test_a_sub_second_budget_is_not_printed_as_zero_seconds |
| 49-F5 | P3 | `run-all` writes `gate configuration invalid` and `gate raised` log lines to stderr with no gate name, duplicating the row below them | RESOLVED: both lines are debug now; the ERROR row carries the message; `test_a_misconfigured_gate_is_reported_in_its_row_not_as_a_gate_less_warning` pins a quiet log |
| 49-F6 | P3 | Absolute machine paths in ordinary messages: a developer's home path, or a CI runner's path | RESOLVED: the config and cli messages say `pyproject.toml`, and the baseline messages (function length, function complexity, complexity ratchet, survivor journal) name the file; `test_the_refresh_message_names_the_baseline_not_its_absolute_path` pins it |
| 49-F7 | P3 | Nested parentheses in the fix hints, and an unexplained `limit 150` | RESOLVED: the hints no longer nest a parenthesis inside another; the length message says where the limit is set; `test_a_stale_entry_hint_has_no_nested_parentheses_and_the_limit_is_explained` pins both |
| 49-F8 | P3 | `refresh` of a missing baseline reads `+0 -0 entries`, which is the same text as a no-op refresh | RESOLVED: a first-time write reads `created <file> with N entries`; `tests/test_entry_delta.py` pins it |
| 49-F9 | P3 | `bandit_warn`, `advisory_warn` and `format_warn` run by hand with a directory argument exit 0 silently, and a positional path is ignored without a word | RESOLVED: a directory argument prints that it was not checked (`bandit_warn` covers `advisory_warn`, which imports its resolver); `tests/test_warn_hooks_directory_argument.py` pins it, and a README-only commit stays quiet |
| 49-F10 | P3 | The `bandit_warn` banner asserts a CI step that a consumer may not have | RESOLVED: the banner no longer claims a CI step or an artifact; `test_the_banner_does_not_claim_a_ci_artifact_a_consumer_may_not_have` pins it |
| 49-F11 | P3 | The `ERROR` row for a gate with a fix-hint `e.g. ... = ...` placeholder is correct; the `ERROR` for `bandit_warn` named as a gate is not explained | OPEN |
| 50-F1 | P2 | Loop whose step can be zero or None never advances | OPEN |
| 50-F2 | P2 | Counter or state advanced before the write that it counts | OPEN |
| 50-F3 | P1 | Raw UPDATE/DELETE executed without a row-count check | OPEN |
| 50-F4 | P3 | Per-item log line inside a network page loop with no throttle | DEFERRED: a new gate, which waits on the owner's decision on the 50-series new-gate programme (50-F3, see its decision row); building it now would pre-empt that decision. Evidence and the minimal reproduction are in audits/2026-11-10/50_new_gate_mining.md |
| 50-F5 | P3 | Test assertion depends on runner platform or pandas null representation | DEFERRED: a new gate, which waits on the owner's decision on the 50-series new-gate programme (50-F3, see its decision row); building it now would pre-empt that decision. Evidence and the minimal reproduction are in audits/2026-11-10/50_new_gate_mining.md |
| 50-F6 | P3 | Doctests and assertions that hold only on one pandas major | DEFERRED: a new gate, which waits on the owner's decision on the 50-series new-gate programme (50-F3, see its decision row); building it now would pre-empt that decision. Evidence and the minimal reproduction are in audits/2026-11-10/50_new_gate_mining.md |
| 51-F1 | P3 | sibling_floor_skew._github_version sends a token through urllib's default redirect handler | RESOLVED: a redirect to another host is refused before the token is sent (`_SameHostRedirects`); `test_a_redirect_to_another_host_is_refused_so_the_token_stays_home` pins it |
| 51-F2 | P2 | The release gates come from the tagged commit, so a v* tag from any branch reaches RELEASE_TOKEN (overlaps accepted 51-F8) | OPEN |
| 51-F3 | P3 | self-ci.yml runs an unpinned `uvx black`; the 51-F2 meta-test does not scan it | OPEN |
| 51-F4 | P3 | release.yml puts the base64 release token in git's argv in two places | RESOLVED: both pushes pass the header through `GIT_CONFIG_*` environment variables, as `_consumers` does; `test_the_release_push_passes_the_token_header_through_the_environment_not_argv` pins it |
| 51-F5 | P3 | Dev extras are floor-only and unconstrained on the release verify path and in self-ci | OPEN |
| 52-F11 | P2 | Public `main(argv)` entry points accept `argv` and never read it | RESOLVED: stray arguments refused with exit 2; tests in test_install_safe_hook and test_setup_env |
| 52-F12 | P3 | Unused-argument class is not enforced anywhere in the repo | OPEN |
| 52-F13 | P3 | Test-suite lint tolerance hides a mutable default in a test helper | OPEN |
| 52-F14 | P3 | Three src modules sit at or near the 1000-LOC budget | OPEN |

| 48-F12 | P2 | `baseline_trend.history` spawns one `git show` per commit that touched the file. | OPEN |
| 48-F13 | P2 | `hook_attestation._hash_at` spawns one `git show <sha>:<CONFIG>` per commit of the range. | OPEN |
| 48-F14 | P2 | `test_python_floor_compatibility.py` re-reads and re-parses every module in separate parametrized tests. | OPEN |
| 48-F15 | P2 | the git-backed test files build a fresh repository for every test. | OPEN |
| 48-F16 | P3 | `closed_audit_rounds._declared_edit` runs a full `git log` for every changed closed-round file. | OPEN |
| 48-F17 | P3 | `tests/conftest.py` samples the CPU for 0.5 s at the start of every xdist session. | OPEN |

## Round 2026-09-04 — the mutation harness, five dimensions

69 findings. Every one dispositioned. The round was run knowing what the first implementation round
had already found, and the agents were given that list so they would not re-report it.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 20-F1 | P0 | Cache key ignores `lines`/`limit`/`fallback_test_paths` | RESOLVED |
| 20-F2 | P0 | A directory in `test_paths` digests as one constant | RESOLVED |
| 20-F3 | P0 | A timed-out mutant is dropped from the denominator | RESOLVED |
| 20-F4 | P1 | A survivor whose re-check times out is counted as killed | RESOLVED |
| 20-F5 | P1 | `truncated` stays False while sampling drops candidates | RESOLVED |
| 20-F6 | P1 | `-z` does not unquote patch-mode paths | RESOLVED |
| 20-F7 | P1 | An added line beginning `++ ` is parsed as a file header | RESOLVED |
| 20-F8 | P1 | A truncated or sampled run prints nothing on the green path | RESOLVED |
| 20-F9 | P1 | A refresh regenerates from a truncated run | RESOLVED |
| 20-F10 | P2 | Cached results drop `killed_by_crash` and `coverage_gaps` | RESOLVED |
| 20-F11 | P2 | Annotations excluded wholesale, hiding enforced bounds | RESOLVED |
| 20-F12 | P2 | `lines_for` matches on a path suffix | RESOLVED |
| 20-F13 | P2 | The closure walk swallows `SyntaxError`/`OSError` | RESOLVED |
| 20-F14 | P2 | The warm worker's `run()` has no timeout | RESOLVED |
| 20-F15 | P3 | `Mutant.path` is a `str`, so `.key` raises | RESOLVED |
| 20-F16 | P3 | Dropped candidates counted but never reconciled | RESOLVED |
| 20-F17 | P3 | An unreadable untracked file is skipped silently | RESOLVED |
| 21-F1 | P0 | The mutant set is absent from the cache key | RESOLVED |
| 21-F2 | P0 | The closure misses every `from .sibling import name` | RESOLVED |
| 21-F3 | P1 | A `src/` layout fingerprints only the target file | RESOLVED |
| 21-F4 | P1 | A directory test path contributes nothing | RESOLVED |
| 21-F5 | P1 | The key is taken before the run and written after | RESOLVED |
| 21-F6 | P1 | `HARNESS_VERSION` is a human promise | RESOLVED |
| 21-F7 | P2 | A cache hit drops the caveats | RESOLVED |
| 21-F8 | P2 | Interpreter, dependencies and plugins outside the digest | RESOLVED |
| 21-F9 | P2 | The cache file is rewritten non-atomically | RESOLVED |
| 21-F10 | P2 | The cache-hit message asserts more than it knows | RESOLVED |
| 21-F11 | P3 | A cached clean result is invisible on the success path | RESOLVED |
| 21-F12 | P3 | Dynamic imports are outside the walk | WON'T FIX |
| 22-F1 | P0 | The sandbox is import-shadowed, so mutations never run | REJECTED IN PART / RESOLVED IN PART |
| 22-F2 | P0 | No warm baseline: the judging process is never validated | RESOLVED |
| 22-F3 | P1 | `killed_by_crash` is structurally always 0 | RESOLVED |
| 22-F4 | P1 | A stale `.pyc` reverts a same-length mutation | NOT REPRODUCED, MITIGATED |
| 22-F5 | P1 | Kills are believed unconditionally | RESOLVED |
| 22-F6 | P1 | An import-breaking mutant aborts the sweep, misdiagnosed | RESOLVED |
| 22-F7 | P2 | The crash list omits what a mutation actually raises | RESOLVED |
| 22-F8 | P2 | Process-global state a purge cannot reach | WON'T FIX |
| 22-F9 | P2 | Modules without `__file__` escape the purge | RESOLVED |
| 22-F10 | P2 | The warm path has no timeout | RESOLVED |
| 22-F11 | P3 | The cache round trip drops the caveats | RESOLVED |
| 22-F12 | P3 | Only `pytest-randomly` is neutralised | WON'T FIX |
| 22-F13 | P3 | `_pytest_env` sanitises one variable | RESOLVED IN PART |
| 23-P0-1 | P0 | Everything inside an f-string is unmutable | RESOLVED |
| 23-P1-2 | P1 | Regex internals cannot be perturbed | RESOLVED |
| 23-P1-3 | P1 | The sampler never fires on an annotated table | RESOLVED |
| 23-P1-4 | P1 | Argument ORDER is never transposed | RESOLVED |
| 23-P1-5 | P1 | `continue`/`break` are not mutable | RESOLVED |
| 23-P2-6 | P2 | Strings can only be emptied, never substituted | RESOLVED |
| 23-P2-7 | P2 | `+=`, `//`, `%`, `**` absent from the operator table | RESOLVED |
| 23-P2-8 | P2 | `max`/`min` cannot be swapped | RESOLVED |
| 23-P3-9 | P3 | Slice bounds unmutable when the bound is a name | RESOLVED |
| 23-P3-10 | P3 | The exception type and the swallow are unmutable | WON'T FIX |
| 23-P3-11 | P3 | A truthiness guard cannot be inverted | RESOLVED |
| 23-P3-12 | P3 | Waste: the "+1 on an opaque length" family | WON'T FIX |
| 24-1 | — | Purge scan is ~29% of a sweep | RESOLVED |
| 24-2 | — | Parallelism, claimed 3.5x on a loaded machine | IMPLEMENTED, SPEEDUP NOT CLAIMED |
| 24-3 | — | Test ordering, -22.6% of the pytest phase | RESOLVED |
| 24-4 | — | Survivor confirmations run serially | RESOLVED |
| 24-5 | — | `__pycache__` excluded from the copy costs ~12s once | WON'T FIX |
| 24-5b | — | A warm baseline would save 15s | REJECTED |
| 24-6 | — | Measured non-findings | ACKNOWLEDGED |
| 25-F1 | P0 | One baseline key silences every identical token | RESOLVED |
| 25-F2 | P0 | The prose note is policed by nothing | RESOLVED IN PART |
| 25-F3 | P0 | The refresh command launders survivors | RESOLVED |
| 25-F4 | P0 | A refresh after a narrow commit deletes valid entries | RESOLVED |
| 25-F5 | P1 | `coverage_gaps` never fails and never ratchets | RESOLVED |
| 25-F6 | P1 | Nothing ever runs the sweep | RESOLVED |
| 25-F7 | P1 | `enforce`'s output is swallowed by pytest's capture | RESOLVED |
| 25-F8 | P2 | "TRUNCATED" fires when nothing was truncated | RESOLVED |
| 25-F9 | P2 | A timed-out mutant vanishes from every number | RESOLVED |
| 25-F10 | P2 | A zero-mutant run reads as a clean run | RESOLVED |
| 25-F11 | P2 | Cached and measured results differ by one clause | RESOLVED |
| 25-F12 | P2 | The fallback net over- and under-matches | RESOLVED |
| 25-F13 | P3 | The suite checks printing, not key equality | RESOLVED |
| 25-F14 | P3 | The mutant's path is a bare basename | RESOLVED |
| 25-F15 | P3 | The cache file is not gitignored | RESOLVED |

**Counts, after the same session closed every deferral.** 62 RESOLVED (four of them in part),
5 WON'T FIX, 1 REJECTED, 1 NOT REPRODUCED, 1 ACKNOWLEDGED. Zero outstanding.

**What the deferrals were actually waiting on, and what happened.** Five were waiting on work:
an AST-span implementation careful enough for the byte-offset trap (23-P1-4, 23-P3-9), a RULE
for what to substitute a string or a pattern with rather than an arbitrary one (23-P1-2,
23-P2-6), and a module settled enough to gate its own version (21-F6). All five were done.

Two were waiting on a quiet machine (24-2, 24-4). The machine never got quiet, so the speedup
is still not claimed -- but the implementation shipped with the property that CAN be verified
under load and is the only one a harness may not trade for speed: `jobs=4` reaches the same
verdicts as `jobs=1`.

One (25-F6) turned out to be waiting on something nobody had noticed: this repository has no
git hooks installed at all, so its entire pre-commit configuration -- all 18 hooks -- is inert.
The sweep is now wired to `pre-push` and will fire after `pre-commit install --hook-type
pre-push`. Recorded as resolved IN PART rather than resolved, because arming a mechanism is not
the same as running it.

**The gate that caught its author.** Wiring 21-F6 immediately failed: this session had widened
the operator set four times over while `HARNESS_VERSION` still read 3. That is the entire
argument for the gate, demonstrated on the first run.

## Round 2026-09-24 -- whole-package audit, six dimensions

515 findings. Files: `30_scanners_a_to_l` (GA..GE, original G1..G5), `31_scanners_m_to_z` (MP, RS, TZ, MT, W, TS),
`32_architecture` (ARCH), `33_adoption` (ADOPT), `34_test_suite` (SUITE), `35_new_meta_tests` (NEW, INFRA).
Each file keeps the agent's original id on every finding.

| Status | Sev | Id | Finding |
|---|---|---|---|
| **RESOLVED** | High | `GA-1` | =1 is treated as covering the whole one category, but in ru/uk/pl one also contains 21,... |
| **RESOLVED** | Med | `GA-2` | The branch-head regex also scans branch bodies, so it finds fake branches |
| **RESOLVED** | Med | `GA-3` | ARB files are read as utf-8, so a BOM causes a JSONDecodeError crash |
| **RESOLVED** | Low | `GA-4` | The ICU # placeholder is not accepted (false positive) |
| **RESOLVED** | Low | `GA-5` | A missing template locale raises a bare KeyError |
| **RESOLVED** | Low | `GA-6` | The "plural" in value substring test skips the counted-phrase rule |
| **RESOLVED** | Med | `GA-7` | Only a positional str constant is detected; f-strings, concatenation, variables and sql... |
| **RESOLVED** | Med | `GA-8` | Unparseable or non-UTF8 migrations are skipped (fail-open) |
| **RESOLVED** | Low | `GA-9` | async with autocommit_block() is not a guard (false positive) |
| **RESOLVED** | Low | `GA-10` | The guard is lexical: a def nested in the with but called later passes; a bare-Name aut... |
| **RESOLVED** | Low | `GA-11` | Baseline key file:line breaks on any edit above the call |
| **RESOLVED** | Med | `GA-12` | The verdict regex misses common spellings; PARTIALLY RESOLVED parses as PARTIALLY |
| **RESOLVED** | Med | `GA-13` | Backslash paths are ignored; absolute or . |
| **RESOLVED** | Low | `GA-14` | A reversed migration range expands to nothing |
| **RESOLVED** | Low | `GA-15` | The :\d+ strip is dead code (the regex cannot capture :) |
| **RESOLVED** | Med | `GA-16` | The ratchet key splits on the first :; with root=None on Windows every key becomes C:<v... |
| **RESOLVED** | Med | `GA-17` | Round names in os.path.join/Path args, + concatenation and f-strings are missed |
| **RESOLVED** | Low | `GA-18` | Parse failures are skipped; a NUL byte raises ValueError (py<3.12) |
| **RESOLVED** | Med | `GA-19` | A row shorter than the status column is dropped, neither open nor closed |
| **RESOLVED** | Med | `GA-20` | The status mention check is case-sensitive |
| **RESOLVED** | Low | `GA-21` | finding_problems re-reads every sibling .md for each file: O(n^2) |
| **RESOLVED** | Low | `GA-22` | is_whole_file_read rejects any [ in the expression |
| **RESOLVED** | Low | `GA-23` | ~~~ fences are ignored; a BOM hides the header row |
| **RESOLVED** | Low | `GA-24` | min_trackers counts undated directories that the check then ignores |
| **RESOLVED** | Med | `GA-25` | A missing or empty tests_dir passes; patterns are case-sensitive |
| **RESOLVED** | Med | `GA-26` | A trailing --src-path raises IndexError, so exit 1 blocks the commit (the docstring say... |
| **RESOLVED** | Med | `GA-27` | --src-path is not backslash-normalised, so it silently scans nothing; a substring prefi... |
| **RESOLVED** | Low | `GA-28` | pip-audit runs (network) even when no .py is staged |
| **RESOLVED** | Med | `GA-29` | The {"entries":[...]} shape is read as the single key entries, so real entries are neve... |
| **RESOLVED** | Med | `GA-30` | The absolute-path regex misses /opt, /tmp, /root, /github/workspace, UNC paths and othe... |
| **RESOLVED** | Low | `GA-31` | Words are counted with [A-Za-z], so a non-Latin note fails |
| **RESOLVED** | Med | `GA-32` | No floor on found: a broken scanner returning {} exits 0 |
| **RESOLVED** | Low | `GA-33` | A raising scan aborts run_rules; scans without a rule are never enforced |
| **RESOLVED** | Med | `GA-34` | git log --follow crosses renames, but git show sha:<current path> fails before the rena... |
| **RESOLVED** | Low | `GA-35` | Bare {key:note} counts as None; "moved" compares only the endpoints; a git failure exits 0 |
| **RESOLVED** | Med | `GA-36` | Excluded dir names are matched against absolute ancestors, so a checkout under build/ s... |
| **RESOLVED** | Med | `GA-37` | File-wide """ parity counts quotes inside strings, so every later fix is rejected and -... |
| **RESOLVED** | Low | `GA-38` | No --stdin-filename (force-exclude and .pyi mode are ignored); a trailing --config rais... |
| **RESOLVED** | High | `GB-1` | The pytest\s+ regex matches pip install pytest pytest-cov as a pathless run, so every s... |
| **RESOLVED** | High | `GB-2` | YAML comments are scanned |
| **RESOLVED** | Med | `GB-3` | Substring match: tests/unit is found in tests/unit_slow |
| **RESOLVED** | Med | `GB-4` | The space form --ignore tests/gpu is not recognised and counts as an invocation |
| **RESOLVED** | Med | `GB-5` | An ignore in one job applies globally (false positive) |
| **RESOLVED** | Low | `GB-6` | \s+ crosses newlines, so the next line is read as pytest's args |
| **RESOLVED** | High | `GB-7` | A job without a name inherits the previous step's name, so allowlisted names mask other... |
| **RESOLVED** | Med | `GB-8` | with: name: (artifact input) is taken as the step name |
| **RESOLVED** | Med | `GB-9` | Misses - continue-on-error: true and quoted 'true' |
| **RESOLVED** | High | `GB-10` | Any indented uses: exempts the job as a reusable-workflow call, which covers most real... |
| **RESOLVED** | Med | `GB-11` | A step-level timeout satisfies the job check (enshrined by a test) |
| **RESOLVED** | Med | `GB-12` | jobs:  # comment finds no jobs, so the result is [] |
| **RESOLVED** | Low | `GB-13` | The last job's block runs to EOF |
| **RESOLVED** | Med | `GB-14` | permissions: read-all / {} are reported missing (false positive) |
| **RESOLVED** | Med | `GB-15` | working-directory is never reset between steps/jobs (false negative) |
| **RESOLVED** | Low | `GB-16` | A docker://…@sha256: digest is flagged as mutable |
| **RESOLVED** | Low | `GB-17` | The script regex is applied to name/description lines |
| **RESOLVED** | Low | `GB-18` | Dead repo_root recheck |
| **RESOLVED** | High | `GB-19` | BOM or SyntaxError files are silently skipped |
| **RESOLVED** | Med | `GB-20` | Keyword section=/key= calls are skipped |
| **RESOLVED** | Med | `GB-21` | Binding only via Assign; AnnAssign, walrus and with .. |
| **RESOLVED** | Low | `GB-22` | Bound names are file-wide, not scoped (false positive) |
| **RESOLVED** | Med | `GB-23` | _const_in_file takes the first assignment and ignores AnnAssign |
| **RESOLVED** | Med | `GB-24` | Relative imports are unresolved, so defaults are silently skipped |
| **RESOLVED** | Low | `GB-25` | import a.b binds b (wrong) |
| **RESOLVED** | Low | `GB-26` | 1 == True == 1.0 counts as agreement |
| **RESOLVED** | Low | `GB-27` | sorted() on mixed-type keys raises TypeError |
| **RESOLVED** | Low | `GB-28` | The min_checked guard is a bare assert (dropped under -O) |
| **RESOLVED** | Low | `GB-29` | relative_to raises outside root; every assert re-parses all files; resolve runs twice |
| **RESOLVED** | Med | `GB-30` | A missing baseline is seeded and the test skipped (green in CI; also writes into the tree) |
| **RESOLVED** | Low | `GB-31` | An empty snippet falls back to a line{n} key |
| **RESOLVED** | Med | `GB-32` | The hash concatenates contents without separators; an empty file list gives a constant... |
| **RESOLVED** | Med | `GB-33` | Refresh via sys.argv is ignored under xdist |
| **RESOLVED** | Med | `GB-34` | A missing baseline seeds and skips; any version change, including a revert, self-certif... |
| **RESOLVED** | Low | `GB-35` | A title containing * is not a bullet; matching is a case-sensitive substring |
| **RESOLVED** | Low | `GB-36` | A missing section or trigger produces a permanent skip |
| **RESOLVED** | Low | `GB-37` | copytree into an existing dir raises; the str-prefix check accepts copy2; the return co... |
| **RESOLVED** | Low | `GB-38` | Relative constants resolve against the CWD, not repo_root |
| **RESOLVED** | Low | `GB-39` | A field missing in one repo is not reported as drift; a list-valued field raises TypeEr... |
| **RESOLVED** | High | `GC-1` | A missing functions dir returns [] (a test enshrines it) |
| **RESOLVED** | High | `GC-2` | Misses the psycopg cursor pattern |
| **RESOLVED** | Med | `GC-3` | The key has no class, so same-named methods collide |
| **RESOLVED** | Med | `GC-4` | BOM, non-UTF8 and SyntaxError files are silently skipped; no min_files |
| **RESOLVED** | Med | `GC-5` | A BOM file is silently skipped |
| **RESOLVED** | Med | `GC-6` | async def is not collected |
| **RESOLVED** | Med | `GC-7` | The group key includes defaults, so a drifted default is never compared |
| **RESOLVED** | Low | `GC-8` | Only tree.body is scanned; no stale-allow check; no min_files |
| **RESOLVED** | Med | `GC-9` | A nested function's copy is reported under both outer and inner; allowing inner still f... |
| **RESOLVED** | Med | `GC-10` | AnnAssign and walrus copies are missed |
| **RESOLVED** | Low | `GC-11` | Passing the copy to a log call counts as an escape |
| **RESOLVED** | Low | `GC-12` | relative_to raises; allowed is keyed by bare name, so it applies repo-wide |
| **RESOLVED** | Med | `GC-13` | A missing baseline is rewritten and skipped; the write happens before min_lists (a test... |
| **RESOLVED** | Low | `GC-14` | Refresh via sys.argv is ignored under xdist |
| **RESOLVED** | Med | `GC-15` | Imports under module-level try/if are missed; relative level is ignored |
| **RESOLVED** | Low | `GC-16` | Attribute exceptions count as 0 |
| **RESOLVED** | Low | `GC-17` | A module function shadows a same-named method |
| **RESOLVED** | Low | `GC-18` | A BOM makes the gate crash with SyntaxError |
| **RESOLVED** | Med | `GC-19` | Aliased @dataclass is not recognised; no test file |
| **RESOLVED** | Med | `GC-20` | Empty inputs pass; same-named classes merge |
| **RESOLVED** | Med | `GC-21` | Rules are per file; a nested helper gets name lib, so the public array-cap rule is skipped |
| **RESOLVED** | Med | `GC-22` | The IP-log regex stops at the first ); plain args are missed |
| **RESOLVED** | Low | `GC-23` | Only new Response( is recognised |
| **RESOLVED** | Low | `GC-24` | An emptiness check is flagged as timing-unsafe |
| **RESOLVED** | Med | `GC-25` | uv run python tool.py --x marks the project's own flags foreign |
| **RESOLVED** | Low | `GC-26` | ls-files quotes non-ASCII paths, so they drop out; relative_to raises |
| **RESOLVED** | Med | `GC-27` | Any pkg[extra] is treated as a self-reference |
| **RESOLVED** | Med | `GC-28` | Tokens keep .py or a trailing period (false positive) |
| **RESOLVED** | Low | `GC-29` | every_declared is recomputed per group (O(n^2)); name(args) markers are misparsed; non-... |
| **RESOLVED** | Med | `GC-30` | Table-row dispositions, backslash paths and parametrised ids are missed |
| **RESOLVED** | Low | `GC-31` | .pyi is truncated to .py; ::Class::method is not class-scoped |
| **RESOLVED** | Med | `GC-32` | Comment stripping runs inside strings, so a URL eats the rest of the line and hides the... |
| **RESOLVED** | Med | `GC-33` | Keys are file#ordinal, so the ratchet cannot see a fixed finding being swapped for a ne... |
| **RESOLVED** | Low | `GC-34` | The "try" in window substring matches entry/retry; prefs reached through an attribute a... |
| **RESOLVED** | High | `GD-1` | A BOM makes ast.parse fail and the file is silently skipped |
| **RESOLVED** | High | `GD-2` | Pre-commit hook args: are never inspected |
| **RESOLVED** | High | `GD-3` | Decorator patches are credited to the LAST params, not the leading ones in bottom-up order |
| **RESOLVED** | High | `GD-4` | Relative imports are ignored, so modules drop out of the map (fail-open) |
| **RESOLVED** | High | `GD-5` | Misses pow, math.pow, /=, np.divide and 3+ term sums |
| **RESOLVED** | Med | `GD-6` | Zero files scanned still passes |
| **RESOLVED** | Med | `GD-7` | OR is a verb under IGNORECASE; nouns like set/see/use also match |
| **RESOLVED** | Med | `GD-8` | Aliased/imported fail and reason= are unaudited |
| **RESOLVED** | Low | `GD-9` | Parse failures are skipped; reports use path.name |
| **RESOLVED** | Med | `GD-10` | A BOM drops the first assignment |
| **RESOLVED** | Med | `GD-11` |  # inside quotes is truncated |
| **RESOLVED** | Low | `GD-12` | export X= and X = v are skipped |
| **RESOLVED** | Low | `GD-13` | AliasChoices/AliasPath are dropped |
| **RESOLVED** | Med | `GD-14` | Same qualname overwrites (property setter, overload) |
| **RESOLVED** | Low | `GD-15` | SyntaxError is skipped; relative_to raises |
| **RESOLVED** | Low | `GD-16` | The refresh advice and write_length_baseline disagree for n <= limit |
| **RESOLVED** | Med | `GD-17` | No boundary after the flag: --skip-without-db matches --skip= |
| **RESOLVED** | Med | `GD-18` | The comment promises short flags but none are listed |
| **RESOLVED** | Med | `GD-19` | Workflow keys lack job/step, so copies collapse |
| **RESOLVED** | Med | `GD-20` | Config narrowing keys are incomplete |
| **RESOLVED** | Med | `GD-21` | A missing pre-commit file or workflows dir gives {} and passes |
| **RESOLVED** | Med | `GD-22` | The completion check is a raw substring of entry |
| **RESOLVED** | Low | `GD-23` | The cov parity regex scans comments and skips templated values |
| **RESOLVED** | Low | `GD-24` | Trigger paths-ignore is reported as a narrowing |
| **RESOLVED** | Low | `GD-25` | Top-level pre-commit exclude:/files: are ignored |
| **RESOLVED** | Med | `GD-26` | The config flag is searched across the whole multi-line run |
| **RESOLVED** | Med | `GD-27` | scope is ignored for workflow steps; duplicate step names collide |
| **RESOLVED** | Low | `GD-28` | Always-zero idioms are missed |
| **RESOLVED** | Low | `GD-29` | Advisory words match as substrings |
| **RESOLVED** | Low | `GD-30` | Stale detection is string-based, so a label rename churns entries |
| **RESOLVED** | Med | `GD-31` | hasattr on a dotted attr or an extras suffix gives a false positive; an empty attr passes |
| **RESOLVED** | Low | `GD-32` | 0 specs passes |
| **RESOLVED** | Med | `GD-33` | Any from X import name is exempt as a local module; ast.Import aliases are ignored |
| **RESOLVED** | Med | `GD-34` | No stale check on accepted entries; an empty map passes |
| **RESOLVED** | Med | `GD-35` | Any sqlite3.connect in a test file credits all effects |
| **RESOLVED** | Low | `GD-36` | Any importing test without a driver patch excuses a module |
| **RESOLVED** | Low | `GD-37` | A multi-package src layout gives an empty map |
| **RESOLVED** | Low | `GD-38` | A global functools.cache goes stale on edits and grows without bound |
| **RESOLVED** | Med | `GD-39` | Misses keep.append((spec,0)), log.log(DEBUG,..), not isnan guards and module-level code |
| **RESOLVED** | Low | `GD-40` | Scope is the bare name, so A.check/B.check collide |
| **RESOLVED** | Low | `GD-41` | Parse errors are skipped; empty files passes; relative_to sits outside the try |
| **RESOLVED** | Med | `GD-42` | Top-level names only (misses try/if defs, flags imports); a str _CANARY is iterated per... |
| **RESOLVED** | Low | `GD-43` | initdb output goes to DEVNULL and the log is deleted; wrong arch wheel; fetch(dest) rmt... |
| **RESOLVED** | Low | `GD-44` | No test_fail_message_quality.py; format_warn skips silently on an over-long Windows com... |
| **RESOLVED** | Med | `GE-1` | covers() ignores schema/table |
| **RESOLVED** | Med | `GE-2` | The cast strip ::[\w ]+ eats IS NOT NULL |
| **RESOLVED** | Med | `GE-3` | UNIQUE is not tracked |
| **RESOLVED** | Low | `GE-4` | INCLUDE is ignored |
| **RESOLVED** | Low | `GE-5` | NULLS FIRST/LAST is dropped |
| **RESOLVED** | Low | `GE-6` | Statement split ignores quotes and block comments |
| **RESOLVED** | Med | `GE-7` | A sync anywhere in the region (even before the GPU work) suppresses it |
| **RESOLVED** | Med | `GE-8` | A call in the chain means the sync is not recognised (false positive) |
| **RESOLVED** | Med | `GE-9` | timeit.default_timer and aliased timers are missed |
| **RESOLVED** | Low | `GE-10` | Module-level regions are not scanned |
| **RESOLVED** | Low | `GE-11` | The sync regex matches _sync_to_disk |
| **RESOLVED** | Low | `GE-12` | _iter_stmt_blocks is dead |
| **RESOLVED** | Low | `GE-13` | BOM/SyntaxError files are skipped; no min_files |
| **RESOLVED** | Med | `GE-14` | BOM files are dropped from both collection and scan |
| **RESOLVED** | Low | `GE-15` | Tuple, if/try and class constants are missed |
| **RESOLVED** | Low | `GE-16` | Constant names are global across files (false positive) |
| **RESOLVED** | Med | `GE-17` | Name constructors, hashlib.new arg 2 and data= kwargs are missed |
| **RESOLVED** | Low | `GE-18` | tobytes(order='F') is flagged, but the suggested fix changes the digest |
| **RESOLVED** | Low | `GE-19` | BOM files are skipped; no min_files |
| **RESOLVED** | Med | `GE-20` | Nested functions are reported twice |
| **RESOLVED** | Low | `GE-21` | TryStar is not handled |
| **RESOLVED** | Low | `GE-22` | builtins.Exception is not treated as broad; BOM files are skipped |
| **RESOLVED** | Med | `GE-23` | Class bodies, decorators and defaults (all run at import) are skipped |
| **RESOLVED** | Low | `GE-24` | An aliased environ is missed |
| **RESOLVED** | Med | `GE-25` | Refresh via sys.argv is ignored under xdist |
| **RESOLVED** | Med | `GE-26` | With first_party, a violation is attributed to the innermost frame, so a first-party re... |
| **RESOLVED** | Low | `GE-27` | BOM/SyntaxError test files are skipped |
| **RESOLVED** | Med | `GE-28` | bound walks the whole module, so function locals count as module attributes |
| **RESOLVED** | Low | `GE-29` | A BOM module is dropped from the index |
| **RESOLVED** | Low | `GE-30` | setattr, AnnAssign and tuple targets are missed |
| **RESOLVED** | Low | `GE-31` | If recurses twice: O(2^depth) |
| **RESOLVED** | Low | `GE-32` | Duplicate helpers kept only for a parity test |
| **RESOLVED** | Med | `GE-33` | Depends on the user's diff prefix config |
| **RESOLVED** | Low | `GE-34` | Untracked-file line count off by one |
| **RESOLVED** | Low | `GE-35` | No --no-textconv |
| **RESOLVED** | Med | `GE-36` | Extras and uv/poetry source tables are not scanned |
| **RESOLVED** | Low | `GE-37` | Uppercase SHAs are reported as unpinned |
| **RESOLVED** | Low | `GE-38` | No left boundary on the package name |
| **RESOLVED** | Med | `GE-39` | _checkout_root climbs to any .git, including the consuming repo when a .venv is inside it |
| **RESOLVED** | Low | `GE-40` | Unreadable files are skipped; min_pins=0 with no pins raises StopIteration |
| **RESOLVED** | Med | `GE-41` | Counts are keyed by exact code, so a prefix ignore reads as 0 |
| **RESOLVED** | Low | `GE-42` | Syntax-error items (code None) are dropped |
| **RESOLVED** | Med | `GE-43` | Common staging-sweep forms are missed |
| **RESOLVED** | Low | `GE-44` | Block ends at the first fi; split("else") splits substrings |
| **RESOLVED** | Low | `GE-45` | lines.index picks the first duplicate; "SKIPPED" in a comment exempts the script |
| **RESOLVED** | Med | `GE-46` | Python imports never match, yet the file counts as examined |
| **RESOLVED** | Low | `GE-47` | Matches in comments; rglob walks .git, node_modules and .venv |
| **RESOLVED** | Med | `GE-48` | A missing scanned dir gives 0 files and passes |
| **RESOLVED** | Low | `GE-49` | BOM/non-UTF8 crashes; each file is parsed 3-4 times |
| **RESOLVED** | Low | `GE-50` | The default SDK set misses .responses.create, .messages.stream and .chat.completions.pa... |
| **RESOLVED** | Low | `GE-51` | A missing baseline re-seeds and skips (green) |
| **RESOLVED** | Low | `GE-52` | _loc returns 0 on OSError and raises on non-UTF8; files=[] passes |
| **RESOLVED** | Low | `GE-53` | git rev-parse --git-dir ignores core.hooksPath |
| **RESOLVED** | Low | `GE-54` | read_text/write_text use the locale encoding and rewrite line endings |
| **RESOLVED** | High | `MP-1` | BOM files are dropped: ast.parse rejects U+FEFF and the SyntaxError is swallowed |
| **RESOLVED** | High | `MP-2` | any SyntaxError/decode error is a silent skip; newer syntax than the interpreter passes |
| **RESOLVED** | Med | `MP-3` | only .utcnow() calls matched; default_factory=datetime.utcnow and utcfromtimestamp missed |
| **RESOLVED** | Med | `MP-4` | no file-count floor; missing root passes |
| **RESOLVED** | Med | `MP-5` | class-level markers, AnnAssign/class pytestmark, from pytest import mark missed |
| **RESOLVED** | High | `MP-6` | positional tests (no /, no .py) not seen as path → "reaches everything" |
| **RESOLVED** | Med | `MP-7` | ./tests not normalised; cd pkg && pytest tests/ never matches |
| **RESOLVED** | Med | `MP-8` | first -m used, docstring says last wins; -m=/-mexpr not parsed |
| **RESOLVED** | Med | `MP-9` | node-id runner reaches whole file; -k ignored |
| **RESOLVED** | Med | `MP-10` | ratchet key is the file: new tests in a known file excused; node-id known reported stale |
| **RESOLVED** | Low | `MP-11` | eval of marker expr; exception = "selects" (fail-open) |
| **RESOLVED** | Med | `MP-12` | aliased reload, importlib as il, sys as _sys missed; substring prefilter drops files |
| **RESOLVED** | High | `MP-13` | any sys.modules[...] = x counts as restore, including installing a fake |
| **RESOLVED** | Med | `MP-14` | innermost-scope only (FP); conftest/usefixtures fixtures unseen (FP); one autouse resto... |
| **RESOLVED** | Low | `MP-15` | any __dict__.update, addfinalizer, subprocess.run counts as restore |
| **RESOLVED** | Low | `MP-16` | allowlist keyed on (path, line): drift, no stale check, missing roots skipped |
| **RESOLVED** | Med | `MP-17` | success line + nonzero exit reported clean |
| **RESOLVED** | Low | `MP-18` | --min-files w/o value IndexError; --min-files=200 passed to mypy; locale decoding on Wi... |
| **RESOLVED** | Med | `MP-19` | _ENV_PROBE substring (os in loss); any enclosing Try exempts |
| **RESOLVED** | Low | `MP-20` | reversed ranges, AnnAssign/walrus/with, from pytest import skip missed |
| **RESOLVED** | High | `MP-21` | _unimportable discarded; walk_packages(onerror=lambda _: None) hides subpackages |
| **RESOLVED** | Low | `MP-22` | plain prefix: pkg.io skips pkg.iostats |
| **RESOLVED** | Med | `MP-23` | if not x, IfExp, while, assert, comprehension ifs missed |
| **RESOLVED** | Med | `MP-24` | key uses path.name; no stale check; line-number keys |
| **RESOLVED** | Low | `MP-25` | walk descends into nested defs; Annotated not unwrapped |
| **RESOLVED** | Med | `MP-26` | from pkg.a import _impl and relative imports missed |
| **RESOLVED** | Low | `MP-27` | relative_to ValueError when src outside root |
| **RESOLVED** | Low | `MP-28` | non-recursive glob; parse errors silent; import_module("pkg._x") missed |
| **RESOLVED** | Med | `MP-29` | # inside a string treated as comment (FP) |
| **RESOLVED** | Med | `MP-30` | docstring parity flips on SQL = """ literals |
| **RESOLVED** | Med | `MP-31` | declared head → Class.member never checked; a.b.c skipped |
| **RESOLVED** | Low | `MP-32` | test files matched by basename over rglob incl .venv |
| **RESOLVED** | Med | `MP-33` | also resolves vs repo root (FN); /x.md joined to drive root (FP) |
| **RESOLVED** | Low | `MP-34` | anchors, queries, <..>, reference links, other extensions skipped; fences scanned |
| **RESOLVED** | High | `MP-35` | required non-str fields get string sentinels → ValidationError read as "enforced"; stil... |
| **RESOLVED** | Med | `MP-36` | only first bound probed; conint/Interval skipped |
| **RESOLVED** | Low | `MP-37` | fractional bound on int field rejected by int parsing |
| **RESOLVED** | Med | `MP-38` | [tool.pytest], pytest.toml, root conftest, non-literal addinivalue_line missed → false... |
| **RESOLVED** | Low | `MP-39` | configparser error swallowed |
| **RESOLVED** | Low | `MP-40` | pin regex needs "ruff==x"; pre-commit regex order/quote sensitive |
| **RESOLVED** | Med | `MP-41` | $defs names reported as fields |
| **RESOLVED** | Med | `MP-42` | DDL types lack INT/BOOL/FLOAT/CHAR/quoted names |
| **RESOLVED** | Med | `MP-43` | unparsable prompt module contributes nothing (fail-open); unguarded non-UTF8 reads crash |
| **RESOLVED** | Low | `MP-44` | Sequence[, Mapping[, frozenset[, Optional[list] treated as scalar |
| **RESOLVED** | Low | `MP-45` | float(group(1)) crashes on groupless pattern, None group, 1.2k |
| **RESOLVED** | High | `RS-1` | BOM files dropped; sql_verifier_coverage.py:63 crashes |
| **RESOLVED** | High | `RS-2` | parse errors fail open; floors count inputs not parsed files |
| **RESOLVED** | Med | `RS-3` | from os import environ/getenv, os as _os, os.environ["X"], getenv(key=) missed |
| **RESOLVED** | Med | `RS-4` | missing baseline seeds and skips |
| **RESOLVED** | Low | `RS-5` | baseline never tightens (test pins it) |
| **RESOLVED** | Low | `RS-6` | refresh from sys.argv misses xdist/pytest.main |
| **RESOLVED** | Med | `RS-7` | .coverage substring flags .coveragerc; /build/ flags packages named build |
| **RESOLVED** | Med | `RS-8` | ls-files without -z: non-ASCII paths quoted, rules miss them |
| **RESOLVED** | Med | `RS-9` | ${COV:-} accepted as guard |
| **RESOLVED** | Low | `RS-10` | test -n "$COV" not a guard (FP) |
| **RESOLVED** | Low | `RS-11` | per-line simple $X compares only |
| **RESOLVED** | Med | `RS-12` | only arg-less .dispose() |
| **RESOLVED** | Low | `RS-13` | substring protection; redispose() exempts module |
| **RESOLVED** | Low | `RS-14` | constructor alias missed |
| **RESOLVED** | Med | `RS-15` | decorator-factory inner deco flagged (FP) |
| **RESOLVED** | High | `RS-16` | any name called at module scope (incl |
| **RESOLVED** | Med | `RS-17` | collections.defaultdict/OrderedDict not recognised |
| **RESOLVED** | Low | `RS-18` | registries under try/if; mod._REGISTRY[k]=; aliases missed |
| **RESOLVED** | Low | `RS-19` | relative_to crash outside root |
| **RESOLVED** | Med | `RS-20` | patch applied although target check found missing attrs |
| **RESOLVED** | Low | `RS-21` | failed restore leaves concurrent edits only in patch file |
| **RESOLVED** | Med | `RS-22` | rf"/F", .extend, +=, dynamic f-strings missed; comments matched |
| **RESOLVED** | Med | `RS-23` | missing root → {} passes |
| **RESOLVED** | Low | `RS-24` | strict utf-8 read crashes scan |
| **RESOLVED** | Low | `RS-25` | re-run after moving clone keeps stale export; no tests |
| **RESOLVED** | Low | `RS-26` | value not XML-escaped / shell-quoted; decode error uncaught |
| **RESOLVED** | High | `RS-27` | SECURITY DEFINER after $$ body never seen |
| **RESOLVED** | High | `RS-28` | quoted identifiers (pg_dump) / multi-line headers invisible |
| **RESOLVED** | High | `RS-29` | commented-out REVOKE counts |
| **RESOLVED** | High | `RS-30` | schema ignored; DROP+CREATE ordering ignored |
| **RESOLVED** | Med | `RS-31` | search_path checked on every definition, not last (FP); O(n·m) re-reads |
| **RESOLVED** | Low | `RS-32` | REVOKE w/o parens, ON ALL FUNCTIONS IN SCHEMA, default privileges missed (FP) |
| **RESOLVED** | Med | `RS-33` | quote-led lines skipped incl |
| **RESOLVED** | Med | `RS-34` | non-Python literal on line exempts; getsource as gs, open(__file__).read() missed |
| **RESOLVED** | High | `RS-35` | fixtures, getsource as gs, dis.get_instructions, closures missed |
| **RESOLVED** | Med | `RS-36` | key rel::func::kind: more claims of same kind never new |
| **RESOLVED** | Med | `RS-37` | m = AsyncMock(); f(m), tuple unpack, patch() as s, spec=None missed |
| **RESOLVED** | Med | `RS-38` | startswith w/o boundary (prose FPs); comment-led SQL missed; chained targets |
| **RESOLVED** | Low | `RS-39` | pkg.__init__.X key; no dedicated test file |
| **RESOLVED** | High | `RS-40` | check() commits every statement: writes persist |
| **RESOLVED** | Med | `RS-41` | fetchall() on no-result statement → FAIL |
| **RESOLVED** | Med | `RS-42` | psycopg2 with connect() does not close; no connect_timeout |
| **RESOLVED** | Med | `RS-43` | every connect error → SKIPPED, exit 0 with --skip-without-db; +driver DSN not normalised |
| **RESOLVED** | Low | `RS-44` | psycopg2 imported before DSN check |
| **RESOLVED** | Low | `RS-45` | params={} always passed; % literal behaviour undetermined |
| **RESOLVED** | Low | `RS-46` | Python slices flagged; quoted casts missed; comments/docstrings scanned |
| **RESOLVED** | High | `RS-47` | blame failure → {} → all skipped; shallow clone makes gate a no-op |
| **RESOLVED** | High | `RS-48` | trailing # TODO never checked |
| **RESOLVED** | High | `RS-49` | issue-ref regex on whole line exempts foo(x), UTF-8 |
| **RESOLVED** | Med | `RS-50` | commented-out code needs ;/,: Python dead calls missed |
| **RESOLVED** | Low | `RS-51` | SHA-256 repos (64 hex) rejected |
| **RESOLVED** | Low | `RS-52` | one -L per candidate (cmdline limit); locale decoding |
| **RESOLVED** | High | `RS-53` | mock.patch and aliases not in _PATCH_NAMES |
| **RESOLVED** | High | `RS-54` | routing exemption starts at def line, so stacked @patch flagged |
| **RESOLVED** | Med | `RS-55` | new_callable and autospec=False count as autospecced |
| **RESOLVED** | Low | `RS-56` | last-name match flags HTTP mocks; module pytestmark ignored |
| **RESOLVED** | Low | `RS-57` | no floor; relative_to crash |
| **RESOLVED** | Low | `RS-58` | no tests for BOM, aliases, decorator factories, post-body DEFINER, quoted ids, trailing... |
| **RESOLVED** | High | `TZ-1` | cat-file -e = object exists, not reachable: staged-only work counts as committed → REMO... |
| **RESOLVED** | High | `TZ-2` | ignored files (.env, data) never unsaved |
| **RESOLVED** | High | `TZ-3` | failed git cherry → every branch "spare" |
| **RESOLVED** | High | `TZ-4` | parse/decode failures dropped; BOM; uncalled_functions loses call sites → FPs |
| **RESOLVED** | Med | `TZ-5` | __getattr__ substring anywhere marks module dynamic |
| **RESOLVED** | Med | `TZ-6` | pkg prefix matches pkg_other |
| **RESOLVED** | Med | `TZ-7` | type X=, with/for/walrus/match/global bindings missed (FP); walk over-binds nested loca... |
| **RESOLVED** | Low | `TZ-8` | only missing[0] reported |
| **RESOLVED** | Low | `TZ-9` | suppress(ImportError), tuple raises, BaseException guards unseen (FP) |
| **RESOLVED** | Med | `TZ-10` | floor satisfied by assert in another floorless loop with same var |
| **RESOLVED** | Med | `TZ-11` | nested function loops reported twice; duplicate keys |
| **RESOLVED** | Med | `TZ-12` | continue/pass/raise/pytest.fail/with subtests bodies not assert-only |
| **RESOLVED** | Low | `TZ-13` | [*m] treated non-empty |
| **RESOLVED** | Low | `TZ-14` | cwd-relative keys; crash outside root |
| **RESOLVED** | Med | `TZ-15` | identical asserts collapse into one key; 90-char truncation collisions |
| **RESOLVED** | Low | `TZ-16` | bare Name/Attribute/Subscript always narrowing |
| **RESOLVED** | Med | `TZ-17` | missing baseline seeds and skips |
| **RESOLVED** | Med | `TZ-18` | self-recursion / same-name locals count as calls |
| **RESOLVED** | Low | `TZ-19` | defs under module if/try not judged; no file floor |
| **RESOLVED** | Med | `TZ-20` | self.x: T = p and chained assigns treated as used |
| **RESOLVED** | Low | `TZ-21` | any string constant counts as read (__slots__) |
| **RESOLVED** | Low | `TZ-22` | relative_to crash |
| **RESOLVED** | Med | `TZ-23` | test.describe.skip, test.fixme, xit, xdescribe missed |
| **RESOLVED** | Med | `TZ-24` | --project="Mobile Chrome" → "Mobile" |
| **RESOLVED** | Med | `TZ-25` | any playwright test line without --project disables check |
| **RESOLVED** | Med | `TZ-26` | absolute parts: any ancestor tests skips all |
| **RESOLVED** | Med | `TZ-27` | missing runner/tag/config/spec paths → clean |
| **RESOLVED** | Low | `TZ-28` | quoted/short tag flags, 4-space tags unparsed (fail-open) |
| **RESOLVED** | Low | `TZ-29` | substring script match |
| **RESOLVED** | Low | `TZ-30` | empty/stale allowlist reasons accepted |
| **RESOLVED** | Med | `TZ-31` | guard counts headers in fences; rejects **Findings** |
| **RESOLVED** | Low | `TZ-32` | non-backticked rows skipped; empty cells crash |
| **RESOLVED** | Low | `TZ-33` | status column assumed first; other headers counted as findings |
| **RESOLVED** | Med | `TZ-34` | ./scripts vs scripts string compare; ruff.toml not read (fail-open) |
| **RESOLVED** | Low | `TZ-35` | only first component vs test dirs |
| **RESOLVED** | Low | `TZ-36` | locale decoding; syntax-error files silently skipped (hypothesis) |
| **RESOLVED** | Low | `TZ-37` | missing/unmatched source dropped silently |
| **RESOLVED** | Med | `TZ-38` | wasted subprocess; any nonzero = "not ancestor"; missing git crash |
| **RESOLVED** | Med | `TZ-39` | unanchored DOTALL regex: core: inside app_core:, path deps steal refs, quoted refs skipped |
| **RESOLVED** | Low | `TZ-40` | first version = anywhere; suffixes truncated |
| **RESOLVED** | Med | `TZ-41` | trailing --src-path IndexError blocks commit; = form unparsed |
| **RESOLVED** | Med | `TZ-42` | env path \ not normalised; substring match |
| **RESOLVED** | Low | `TZ-43` | any nonzero = findings (missing vulture) |
| **RESOLVED** | Low | `TZ-44` | ~4 processes per file |
| **RESOLVED** | Low | `TZ-45` | rename's old path token cut by [3:] |
| **RESOLVED** | Low | `TZ-46` | no tests for vulture_warn/tool_versions; no BOM/non-UTF8/missing-baseline cases |
| **RESOLVED** | High | `MT-1` | NEEDS-JUSTIFICATION: never rejected on the next run |
| **RESOLVED** | High | `MT-2` | zero mutants (unparsable file, empty scope) passes |
| **RESOLVED** | High | `MT-3` | BOM: AST operators return [] |
| **RESOLVED** | Med | `MT-4` | generator lines consumed by fingerprint; whole file swept, cached under narrow key |
| **RESOLVED** | Med | `MT-5` | lines=[] = whole file, same cache key as full run |
| **RESOLVED** | Med | `MT-6` | twin verdicts not fanned out; accepted twins read stale |
| **RESOLVED** | High | `MT-7` | worker reply desync after stray fd-1 output |
| **RESOLVED** | High | `MT-8` | sweep_files(jobs>1) extra sandboxes not removed → FileExistsError on 2nd file |
| **RESOLVED** | Low | `MT-9` | extra sandboxes copied from live repo, no cold baseline |
| **RESOLVED** | Med | `MT-10` | warm-path crash kills undercounted |
| **RESOLVED** | Low | `MT-11` | dead AssertionError entry |
| **RESOLVED** | Med | `MT-12` | worker not restarted after timeout; timeout paid twice |
| **RESOLVED** | Med | `MT-13` | wider-net timeout recorded as survivor |
| **RESOLVED** | Med | `MT-14` | cache drops wider_net_note |
| **RESOLVED** | Med | `MT-15` | unlocked RMW cache with fixed .tmp name |
| **RESOLVED** | Med | `MT-16` | node-id test paths hashed as placeholder → stale replay |
| **RESOLVED** | Low | `MT-17` | only test_*.py in fingerprint |
| **RESOLVED** | Low | `MT-18` | string >= instead of ancestry |
| **RESOLVED** | Med | `MT-19` | "dropped a not" eats a char: unparsable mutant counted as kill |
| **RESOLVED** | Med | `MT-20` | emptying string: bytes type change / no-op on empty literals |
| **RESOLVED** | Low | `MT-21` | min description says "max becomes min" (re-keys baseline) |
| **RESOLVED** | Low | `MT-22` | sampled_containers wrong counts; AST operators skip sampling |
| **RESOLVED** | Low | `MT-23` | non-UTF8 → UnicodeDecodeError not MutationHarnessError |
| **RESOLVED** | Low | `MT-24` | unclosed open() handles (ResourceWarning; Windows locks) |
| **RESOLVED** | Med | `MT-25` | no check the mutated module is imported from sandbox (editable install → all survive) |
| **RESOLVED** | Med | `MT-26` | timeout kills only direct child; Windows grandchildren leak; rmtree errors ignored |
| **RESOLVED** | Low | `MT-27` | last_timings/last_failed not reset on early return |
| **RESOLVED** | Low | `MT-28` | 6-7 ast.parse per target; O(tokens×ranges) |
| **RESOLVED** | Low | `MT-29` | non-range lines entries dropped from key but crash later |
| **RESOLVED** | Low | `MT-30` | parent __init__.py not in import closure |
| **RESOLVED** | Med | `MT-31` | refresh writes baseline before inconclusive/gap/truncation checks |
| **RESOLVED** | Low | `MT-32` | truncated run passes with warning (documented) |
| **RESOLVED** | Med | `W-1` | cwd/env/sys.path/argv not restored between runs → false kills |
| **RESOLVED** | Low | `W-2` | namespace paths re-resolved per mutant |
| **RESOLVED** | High | `TS-1` | pytest exit code/stderr ignored: usage/conftest errors → every case "NO TEETH" |
| **RESOLVED** | Med | `TS-2` | timeout leaves xdist workers alive on Windows |
| **RESOLVED** | Low | `TS-3` | one bad case aborts whole sweep |
| **RESOLVED** | Low | `TS-4` | read-back check vacuous for empty/duplicated repl |
| **RESOLVED** | Low | `TS-5` | CRLF old becomes \r\r\n |
| **RESOLVED** | High | `ARCH-1` | Package version is 0.1.0 while releases are tagged up to v1.16.1 |
| **RESOLVED** | High | `ARCH-2` | Consumer pinning is inconsistent: README policy is the moving @v1 tag; pyutilz uses @v1... |
| **RESOLVED** | High | `ARCH-3` | v1 -> 71cf6d0, v1.16.1 -> 797f045; 32 commits on master since v1.16.1 (last tag 2026-09... |
| **RESOLVED** | High | `ARCH-4` | Reusable workflows git clone --depth 1 py-ci-shared default-branch tip to get configs/r... |
| **RESOLVED** | Med | `ARCH-5` | 6 of 7 reusable workflows declare no permissions:; they inherit the caller's token scop... |
| **RESOLVED** | Med | `ARCH-6` | pip-audit, import-linter, pydoclint, semgrep installed unpinned (uvx pip-audit, uv pip... |
| **RESOLVED** | Med | `ARCH-7` | pyutilz-ref defaults to empty = unpinned pyutilz tip; the description itself documents... |
| **RESOLVED** | Med | `ARCH-8` | requires-python >=3.9 but CI runs only 3.11 (matrix is OS only) and mypy checks as 3.10 |
| **RESOLVED** | Med | `ARCH-9` | Manifest exposes one hook (mypy-full-manual, which is just python -m mypy), while 13 mo... |
| **RESOLVED** | Med | `ARCH-10` | Only 3 console scripts; the other 10 CLIs are python -m only |
| **RESOLVED** | Med | `ARCH-11` | 67 of 112 modules are not mentioned in README (e.g |
| **RESOLVED** | Low | `ARCH-12` | Docstring describes the package as four scripts (black_filtered_apply, format_warn, ban... |
| **RESOLVED** | Low | `ARCH-13` | Stale build/lib (22 modules, vs 112 in src) and build/bdist.win-amd64 exist on disk |
| **RESOLVED** | High | `ARCH-14` | Corpus enumeration is inconsistent |
| **RESOLVED** | High | `ARCH-15` | Seven divergent skip-dir sets |
| **RESOLVED** | Med | `ARCH-16` | Every gate re-reads and re-parses the same files; a consumer running 40 gates in one py... |
| **RESOLVED** | Med | `ARCH-17` | Unparsable files are silently dropped (continue/return []/return set()), so a file with... |
| **RESOLVED** | Low | `ARCH-18` | read_text() without encoding=: locale-dependent on Windows (cp1251 here). |
| **RESOLVED** | Med | `ARCH-19` | CLI conventions differ: argparse in 4 (baseline_trend, embedded_postgres, pinned_tool_v... |
| **WON'T FIX** | Med | `ARCH-20` | No common signature |
| **RESOLVED** | Med | `ARCH-21` | Refresh mechanism duplicated six times, with 10 different flags; detection via REFRESH_... |
| **RESOLVED** | Med | `ARCH-22` | Baseline formats and ratchet semantics differ: json vs orjson (orjson a hard dependency... |
| **RESOLVED** | Med | `ARCH-23` | No per-repo configuration surface ([tool.py_ci_shared] appears nowhere) |
| **RESOLVED** | Low | `ARCH-24` | Message quality varies: some gates print file:line plus fix (pinned_tool_versions, per... |
| **RESOLVED** | Med | `ARCH-25` | Dogfooding is partial: self-ci runs pytest, ruff, mypy and the reusable workflows, but... |
| **WON'T FIX** | Low | `ARCH-26` | T201 ignores are listed per file (16 entries) and must be maintained by hand as CLIs ar... |
| **RESOLVED** | Low | `ARCH-27` | orjson is a hard runtime dependency used only for baseline I/O in 5 modules, where stdl... |
| **RESOLVED** | Low | `ARCH-28` | ruff-base.toml/ruff-tests.toml are not package data (pyproject comment), so every consu... |
| **RESOLVED** | Info | `ARCH-29` | Convention document, not an API reference; claims are measurement-based (realtime_appli... |
| **RESOLVED** | Info | `ARCH-30` | Only module over the 1k LOC limit used elsewhere in these repos. |
| **RESOLVED** | High | `ADOPT-1` | CI installs .[test,llm] only; py-ci-shared is in [dev], so it is never installed |
| **RESOLVED** | High | `ADOPT-2` | [dev] lists bare "py-ci-shared" with no direct URL |
| **RESOLVED** | High | `ADOPT-3` | py-ci-shared is not a dependency, yet tests/test_meta/test_no_top_level_side_effects.py... |
| **RESOLVED** | High | `ADOPT-4` | On ImportError these gates print "SKIPPED" and sys.exit(0): a missing install reads as... |
| **RESOLVED** | High | `ADOPT-5` | _SHARED_AVAILABLE=False on ImportError silently drops 7 Dart scans from SCANS; the ratc... |
| **RESOLVED** | Med | `ADOPT-6` | collect_ignore of the meta tests whenever import py_ci_shared fails |
| **RESOLVED** | Med | `ADOPT-7` | pytest.importorskip("py_ci_shared..."): whole gate files turn SKIPPED when the package... |
| **RESOLVED** | Med | `ADOPT-8` | Black-pin cross-check reads ../py-ci-shared/.github/workflows/black-filtered.yml from a... |
| **RESOLVED** | Med | `ADOPT-9` | mypy_gate --min-files 150 dashboard \/\/ true: result and population floor both discard... |
| **RESOLVED** | Med | `ADOPT-10` | mypy_gate --min-files floors hand-set per repo with no recorded measurement; mlframe an... |
| **RESOLVED** | Med | `ADOPT-11` | One job installs the package at a49421c (152 behind) but clones unpinned master for con... |
| **RESOLVED** | Med | `ADOPT-12` | CI installs @v1 (moving tag) while declared deps pin f103a36: local and CI run differen... |
| **RESOLVED** | Med | `ADOPT-13` | Five different py-ci-shared SHAs in one repo (41cbadc, 64e2b6b, 915217a, 7195776, f5028... |
| **RESOLVED** | Med | `ADOPT-14` | Two versions of the same upload-codecov action in one repo. |
| **RESOLVED** | Med | `ADOPT-15` | Gate package floats on master while workflows are ~165-173 commits old; tool_versions.R... |
| **RESOLVED** | Med | `ADOPT-16` | Pinned to v1.2.0 (~173 behind); no meta-test gates at all, only the three lint workflows. |
| **RESOLVED** | Med | `ADOPT-17` | Unpinned git installs of master: any py-ci-shared push can break or silently change the... |
| **RESOLVED** | Low | `ADOPT-18` | The moving tag v1 is 20 commits behind master, so @v1 repos run older workflows than SH... |
| **RESOLVED** | Low | `ADOPT-19` | Local clone 4 commits behind origin/master; hooks in repos that use an editable sibling... |
| **RESOLVED** | Low | `ADOPT-20` | extend = "../py-ci-shared/configs/ruff-base.toml" sibling path; ruff errors on any mach... |
| **RESOLVED** | Low | `ADOPT-21` | Hook id pinned-tool-versions (python -m py_ci_shared.pinned_tool_versions) defined twic... |
| **RESOLVED** | Med | `ADOPT-22` | Local copy of py_ci_shared.index_coverage (same class and 9 functions, cosmetic diffs o... |
| **RESOLVED** | Med | `ADOPT-23` | Hand-copied scanner module, already drifted: flutter_app_core's _IMPORT regex (~line 29... |
| **RESOLVED** | Med | `ADOPT-24` | Same bug classes implemented twice with no cross-reference: additive_epsilon_denominato... |
| **NOT A DEFECT** | Med | `ADOPT-25` | Modules adopted by no consumer: baseline_trend, checkpoint_isolation, config_drift_chec... |
| **RESOLVED** | Med | `ADOPT-26` | 28 modules with exactly one consumer; generic ones worth rolling out: fail_open_handler... |
| **RESOLVED** | Med | `ADOPT-27` | audit_round_format (required in every Python project) missing in pyutilz, llm_bench, da... |
| **RESOLVED** | Low | `ADOPT-28` | autopsia calls lint-advisory but not lint-blocking; llm_bench and social call neither. |
| **RESOLVED** | Low | `ADOPT-29` | Stale second clone of autopsia (2026-09-08, 15 days behind Redline/autopsia); greps and... |
| **RESOLVED** | Info | `ADOPT-30` | Jobs are not starting: "recent account payments have failed or your spending limit need... |
| **RESOLVED** | High | `SUITE-1` | CI tests only Python 3.11 while requires-python = ">=3.9" |
| **RESOLVED** | High | `SUITE-2` | The floor-guard test itself does import tomllib at module level, so it fails at collect... |
| **RESOLVED** | Med | `SUITE-3` | The tomllib ban matches only the exact line import tomllib; import tomllib as t and fro... |
| **RESOLVED** | High | `SUITE-4` | There is no coverage measurement, no fail_under and no pytest config (no testpaths, -ra... |
| **RESOLVED** | High | `SUITE-5` | uv pip install -e ".[dev]" \/\/ uv pip install -e  |
| **RESOLVED** | Med | `SUITE-6` | pytest.importorskip("pre_commit"), but pre-commit is not in the dev extra, so the whole... |
| **RESOLVED** | High | `SUITE-7` | setup_env is a [project.scripts] entry point (py-ci-setup-env) with 143 LOC and zero te... |
| **RESOLVED** | Med | `SUITE-8` | No test references either module. |
| **RESOLVED** | Med | `SUITE-9` | Covered only nominally (referenced, or 1 to 3 unrelated tests); their own branches (7,... |
| **RESOLVED** | Med | `SUITE-10` | 9 gates share 3 grouped files at about 5 to 8 tests each |
| **RESOLVED** | Med | `SUITE-11` | Thin tests relative to branches: runtime_registry_mutation 6/38, unread_init_params 6/3... |
| **RESOLVED** | Med | `SUITE-12` | No meta-test asserts that every module has a test file, every gate has a README entry,... |
| **RESOLVED** | Med | `SUITE-13` | Many of the repo's own gates are not run on itself (full list in section 2): ci_workflo... |
| **RESOLVED** | Low | `SUITE-14` | The dogfood test pytest.skips if lint-advisory.yml is missing (it always exists in this... |
| **RESOLVED** | Med | `SUITE-15` | Mutant P7 survives: removing the ast.Import branch (so import pkg.sub._priv is never re... |
| **RESOLVED** | Med | `SUITE-16` | Mutant F2 survives: async functions dropped from measurement |
| **RESOLVED** | Med | `SUITE-17` | Survivors: S3 (an indented # :a::text comment line is not tested), S5 (__pycache__ skip... |
| **RESOLVED** | Low | `SUITE-18` | Survivor I4: a "%s" % x constant is not tested as stringish. |
| **RESOLVED** | Low | `SUITE-19` | Survivor N3: the default skip-dir list (build) is not pinned; only user-given skip dirs... |
| **RESOLVED** | Med | `SUITE-20` | assert_no_naive_utcnow has no empty-scan guard (no min_files), unlike every sibling gate |
| **RESOLVED** | Low | `SUITE-21` | getattr(node, chr(108)+chr(105)+...) spells "lineno" through chr() concatenation, appar... |
| **RESOLVED** | Low | `SUITE-22` | Real git commit in tmp repos inherits the developer's global git config (commit.gpgsign... |
| **RESOLVED** | Low | `SUITE-23` | Only 10 calls pass timeout=; git and python subprocesses have no deadline, and there is... |
| **RESOLVED** | Low | `SUITE-24` | The fake reader sleeps 30 s in a background thread that is left running after the test... |
| **RESOLVED** | Low | `SUITE-25` | The real-server tests skip unless Postgres binaries are on PATH or PG_BIN; the GitHub r... |
| **RESOLVED** | Low | `SUITE-26` | Every file does sys.path.insert(0, <repo>/src) |
| **RESOLVED** | Low | `SUITE-27` | No shared fixtures (git repo factory, write helper), so each file re-implements _write/... |
| **NOT A DEFECT** | Info | `SUITE-28` | Portability is otherwise good: all tests use tmp_path (one tempfile.TemporaryDirectory... |
| **RESOLVED** | High | `NEW-1` | gate `import_cycles` |
| **RESOLVED** | High | `NEW-2` | gate `local_copy_report` (migrate locals onto existing central gates) |
| **RESOLVED** | High | `NEW-3` | gate `swallowed_exceptions` |
| **RESOLVED** | High | `NEW-4` | gate `pickle_state_completeness` (runtime + static) |
| **RESOLVED** | High | `NEW-5` | gate `test_resource_leaks` (pytest plugin) |
| **RESOLVED** | High | `NEW-6` | gate `atomic_write_staging` |
| **RESOLVED** | High | `NEW-7` | gate `hash_key_determinism` |
| **RESOLVED** | High | `NEW-8` | gate `sentinel_or_fallback` |
| **RESOLVED** | High | `NEW-9` | gate `no_xfail_to_defer` |
| **RESOLVED** | High | `NEW-10` | gate `timing_assertions` |
| **RESOLVED** | Med | `NEW-11` | gate `stale_source_citations` |
| **RESOLVED** | Med | `NEW-12` | gate `console_encoding_safety` |
| **RESOLVED** | Med | `NEW-13` | gate `lf_file_writes` |
| **RESOLVED** | Med | `NEW-14` | gate `module_cache_thread_safety` |
| **RESOLVED** | Med | `NEW-15` | gate `machine_specific_paths` |
| **RESOLVED** | Med | `NEW-16` | gate `hardcoded_token_ceilings` |
| **RESOLVED** | Med | `NEW-17` | gate `coverage_config_parity` |
| **RESOLVED** | Med | `NEW-18` | gate `pytest_addopts_path_runs` |
| **RESOLVED** | Low | `NEW-19` | gate `rollback_then_continue` |
| **RESOLVED** | Low | `NEW-20` | gate `reiterated_iterable_params` |
| **RESOLVED** | Low | `NEW-21` | gate `numba_seed_range` |
| **RESOLVED** | Low | `NEW-22` | gate `stdlib_json_ban` (opt-in) |
| **RESOLVED** | Low | `NEW-23` | gate `polars_null_equality` |
| **RESOLVED** | Low | `NEW-24` | gate `plotly_annotation_loop` |
| **RESOLVED** | High | `INFRA-1` | pytest11 plugin py_ci_shared.pytest_plugin |
| **RESOLVED** | High | `INFRA-2` | Gate-teeth self-check |
| **RESOLVED** | High | `INFRA-3` | Canary corpus |
| **RESOLVED** | High | `INFRA-4` | Per-gate runtime budget |
| **RESOLVED** | Med | `INFRA-5` | Registry + README parity |
| **RESOLVED** | Med | `INFRA-6` | Consumer adoption matrix |
| **RESOLVED** | Med | `CANARY-1` | env_flag_parsing skips a BOM file |
| **RESOLVED** | Med | `CANARY-2` | env_flag_parsing passes an unparsable file |
| **RESOLVED** | Med | `CANARY-3` | git_dependency_pins passes an unparsable pyproject.toml |
| **RESOLVED** | Med | `CANARY-4` | git_dependency_pins misses a single-line dependency array |
| **RESOLVED** | Med | `CANARY-5` | module_reload_safety.assert_no_reloads_in_code passes an empty corpus |
| **RESOLVED** | Med | `CANARY-6` | vacuous_loop_assertions.assert_no_new_floorless_loop passes an empty corpus |
| **RESOLVED** | Med | `CANARY-7` | config_call_site_parity.assert_no_divergent_cfg_get_call_site_defaults passes an empty corpus |
| **RESOLVED** | Med | `CANARY-8` | unresolved_imports.assert_all_from_imports_resolve passes an empty corpus |
| **RESOLVED** | Med | `CANARY-9` | phantom_markdown_links.assert_no_phantom_markdown_links passes an empty corpus |
| **RESOLVED** | Med | `CANARY-10` | pytest_markers.assert_markers_registered passes an empty corpus |
| **RESOLVED** | Low | `CANARY-11` | 22 fail_open_handlers baseline entries carry NEEDS-JUSTIFICATION placeholders |
| **RESOLVED** | Med | `CANARY-12` | _core parse cache serves a stale tree after a same-size rewrite within one mtime tick |
| **RESOLVED** | Low | `CANARY-13` | test_docs_inventory_parity imports tomllib unguarded |
| **RESOLVED** | Low | `CANARY-14` | phantom_markdown_links and tracker_summary_parity import private helpers of sibling modules |
| **RESOLVED** | Low | `CANARY-15` | resource_release_paths narrows with assert isinstance, which python -O strips |
| **RESOLVED** | Med | `CANARY-16` | printed_advice skips BOM and unparsable files and has no floor |
| **RESOLVED** | Low | `CANARY-17` | test_setup_env's shell round trip ran through the stubbed subprocess.run |
| **RESOLVED** | Med | `CANARY-18` | embedded_postgres cannot start as an unprivileged user on Linux |
| **RESOLVED** | Low | `CANARY-19` | the leak-guard plugin test counted a message pytest prints twice under -rA |
| **RESOLVED** | Low | `CANARY-20` | printed_advice fix landed without black and with unescaped match= patterns |
| **RESOLVED** | Med | `CANARY-21` | kwarg_forwarding skipped unreadable and unparsable files and was unregistered |
| **RESOLVED** | Med | `CANARY-22` | order_losing_filters skipped unreadable and unparsable files, had no assert entry and was unregistered |
| **RESOLVED** | Low | `CANARY-23` | randomly_seed_guard unregistered |
| **RESOLVED** | Med | `CANARY-24` | phantom_code_references flagged real dotted paths of installed dependencies |
| **RESOLVED** | Med | `CANARY-25` | test_partition_reachability read a subshell's closing paren as part of a project name |
| **RESOLVED** | Med | `NEW-25` | upstream Dart scanner file-size |
| **RESOLVED** | Med | `NEW-26` | upstream Dart scanner empty-catch |
| **RESOLVED** | Med | `NEW-27` | upstream Dart scanner source-text-assertions |
| **RESOLVED** | Med | `NEW-28` | upstream Dart scanner import-cycles |
| **RESOLVED** | Med | `NEW-29` | upstream Dart scanner tests-without-assertions |
| **RESOLVED** | Med | `NEW-30` | upstream Dart scanner timed-dismissal |
| **RESOLVED** | Med | `NEW-31` | upstream Dart scanner double-error-reports |
| **RESOLVED** | Med | `NEW-32` | upstream Dart scanner unused-test-seams |
| **RESOLVED** | Med | `CANARY-26` | gates handed a list of directories treated each directory as a file |
| **RESOLVED** | Med | `CANARY-27` | effect_assertion_parity reported SELECT-only reads as unchecked database effects |
| **RESOLVED** | Med | `CANARY-28` | git_dependency_pins could no longer read a requirements file |
| **RESOLVED** | Med | `CANARY-29` | audit_round_format read status words inside other words as status mentions |
| **RESOLVED** | Med | `CANARY-30` | identity_comparisons reported `is None` and enum-member identity as string identity |
| **RESOLVED** | Med | `CANARY-31` | gpu_timing_sync reported a region that synchronizes before the timer stops |
| **RESOLVED** | Med | `CANARY-32` | marker_runner_coverage read shell variables, Actions expressions and a Python argument list as runner paths |
| **RESOLVED** | Med | `CANARY-33` | vacuous_loop_assertions ignored guard asserts and flagged constant iterables |
| **RESOLVED** | Low | `CANARY-34` | stale_comment_age read prose with parentheses as commented-out code |
| **RESOLVED** | Med | `CANARY-35` | readme_env_var_parity counted environment writes as reads |
| **RESOLVED** | Low | `CANARY-36` | optional_truthiness flagged guards whose zero case a sibling comparison spells out |
| **RESOLVED** | Med | `CANARY-37` | a one-or-many path parameter configured with a directory string became a one-file list |
| **RESOLVED** | High | `CANARY-38` | a consumer's refresh-option registration stopped pytest once the plugin was installed |
| **RESOLVED** | Med | `CANARY-39` | import_cycles reported `from . import sibling` as a cycle through the parent package |
| **RESOLVED** | Med | `CANARY-40` | gate_commands ignored a workflow-level working directory |
| **RESOLVED** | Med | `CANARY-41` | private_imports treated a module's private helper as foreign to its sibling modules |
| **RESOLVED** | Med | `CANARY-42` | coverage_config_parity missed a --cov-config passed through a shell variable or array |
| **RESOLVED** | Low | `CANARY-43` | the pinned black version was not importable |
| **RESOLVED** | Low | `CANARY-44` | the package shipped no py.typed |
| **RESOLVED** | Med | `CANARY-45` | source_text_claims read a deserialised report keyed by .py paths as source text |
| **RESOLVED** | Low | `CANARY-46` | config default parity compared a default before the call's own numeric cast |
| **RESOLVED** | Med | `CANARY-47` | lint-blocking.yml could not lint a monorepo subproject |
| **RESOLVED** | Med | `CANARY-48` | machine_specific_paths baseline keys carried the absolute path they flag |
| **RESOLVED** | Med | `CANARY-49` | self-CI could not install the dev extra once sqlalchemy 2.1.0 was released |
| **RESOLVED** | Low | `CANARY-50` | Python 3.9: refresh option, stdlib head detection and a 3.10-only test API |
| **RESOLVED** | Low | `CANARY-51` | effect_assertion_parity counted an f-string SELECT as a database write |
| **RESOLVED** | Med | `NEW-36` | scheduled consumer-pin check |
| **RESOLVED** | Med | `NEW-37` | gate scaffolder and CLAUDE.md |
| **RESOLVED** | Med | `NEW-38` | nightly real-corpus drift job |
| **RESOLVED** | Med | `NEW-33` | C901 complexity ratchet |
| **RESOLVED** | High | `NEW-34` | Shrink-only baseline refresh |
| **RESOLVED** | Med | `NEW-35` | source_text_claims: arbitrary-file reads |
| **RESOLVED** | Low | `CANARY-52` | two C901 ratchets: function_complexity (ruff subprocess) and complexity_ratchet |
| **RESOLVED** | Med | `CANARY-53` | release.yml could not move v1: GITHUB_TOKEN may not update a ref to a commit that changes workflows |
| **RESOLVED** | High | `CANARY-54` | randomly_seed_guard crashed pytest on Python 3.9: entry_points(group=...) exists only from 3.10 |
| **RESOLVED** | Med | `CANARY-55` | the shrink-only refresh left 27 tests red: gate tests seeded baselines without the growth opt-in |
| **RESOLVED** | Low | `CANARY-56` | self-CI mypy failed after the AST node index landed: nodes_of returns list[AST] |
| **RESOLVED** | Low | `CANARY-57` | nodes_of returned list[AST], so every gate moved onto the shared node index failed mypy |
| **NOT A DEFECT** | Med | `NEW-39` | epsilon_padded_denominators: shapes its pyutilz counterpart catches |
| **NOT A DEFECT** | Med | `NEW-40` | swallowed_exceptions: shapes its pyutilz counterpart catches |
| **NOT A DEFECT** | Med | `NEW-41` | sentinel_or_fallback: shapes its pyutilz counterpart catches |
| **RESOLVED** | Med | `NEW-42` | phantom_code_references: shapes its pyutilz counterpart catches |
| **RESOLVED** | Med | `NEW-43` | config_getattr_default_parity: shapes its pyutilz counterpart catches |
| **NOT A DEFECT** | Med | `NEW-44` | pickle_state_completeness: shapes its pyutilz counterpart catches |
| **RESOLVED** | Med | `NEW-45` | stale_source_citations: shapes its pyutilz counterpart catches |
| **NOT A DEFECT** | Med | `NEW-46` | clock_day_boundary: shapes its pyutilz counterpart catches |
| **WON'T FIX** | Med | `NEW-47` | vacuous_loop_assertions: shapes its pyutilz counterpart catches |
| **RESOLVED** | Med | `NEW-48` | nondiscriminating_shapes: shapes its pyutilz counterpart catches |
| **WON'T FIX** | Med | `NEW-49` | inert_patch_targets: shapes its pyutilz counterpart catches |
| **RESOLVED** | Low | `CANARY-58` | complexity_ratchet caught a new over-limit function in config_getattr_default_parity |

## Round 2026-10-03 -- full audit: new code, core, workflows/docs/tests, gap analysis

69 findings. Files: `10_new_code` (N), `20_core_infra` (K), `30_workflows_docs_tests` (WF; W belongs to 2026-09-24), `40_gaps` (G, proposals). Dispositions are under each finding and in `dispositions/`.

| Status | Sev | Id | Finding |
|---|---|---|---|
| **RESOLVED** | High | `N-1` | worktree_hygiene: an untracked nested git repository is silently counted as saved |
| **RESOLVED** | High | `N-2` | function_complexity: unparsable files are dropped silently (fail-open), unlike complexity_ratchet |
| **RESOLVED** | High | `N-3` | unresolved_imports: ModuleIndex mutates the shared tree_memo set, so later indexes see stale names |
| **RESOLVED** | Med | `N-4` | ci_install_covers_conftest: `dynamic = ["dependencies"]` is read as "no dependencies" (false ci-install-missing) |
| **RESOLVED** | Med | `N-5` | ci_install_covers_conftest: the finding's rule (and baseline key) depends on the interpreter running the gate |
| **RESOLVED** | Med | `N-6` | ci_health: a billing-blocked newest run hides a code failure that has been red for weeks |
| **RESOLVED** | Med | `N-7` | ci_health `--only` with a name that matches no consumer exits 0 with an empty report |
| **RESOLVED** | Med | `N-8` | adoption_matrix: a pin to a tag or SHA that does not exist passes with `--resolve-in` |
| **RESOLVED** | Med | `N-9` | corpus_drift.compare: a repo missing from tonight's snapshot, and a finder that errors from its first night, never fail |
| **RESOLVED** | Med | `N-10` | standard_stream_restore: any unrelated `is` comparison exempts the function; aliases and setattr are invisible |
| **RESOLVED** | Med | `N-11` | standard_stream_restore: false positives on real consumers (process-lifetime wrappers, guard via a loop variable) |
| **RESOLVED** | Med | `N-12` | nondiscriminating_shapes: `"exists"`/`"supported"` env-probe words exempt data-decided late skips; any other assert disarms median-roundtrip |
| **RESOLVED** | Med | `N-13` | Version and release state drift: HEAD declares 1.19.0 (already tagged) while CHANGELOG and registry say 1.20.0; the guard test passes |
| **RESOLVED** | Med | `N-14` | release.yml: no check that the pushed tag is the newest release before moving `v1` |
| **RESOLVED** | Low | `N-15` | _core.node_index keeps every tree it ever indexed alive (unbounded growth) |
| **RESOLVED** | Low | `N-16` | randomly_seed_guard: a reseeder that already carries `__wrapped__` is never bounded |
| **RESOLVED** | Low | `N-17` | nondiscriminating_shapes: an unknown `extra_shapes` slug is silently ignored; `nonempty-only-assert` misses `!= 0` and bare `len()` |
| **RESOLVED** | Low | `N-18` | config_getattr_default_parity: a declared pydantic field is reported as "undeclared" when dataclass_classes is given; a non-dataclass is silently ignored |
| **RESOLVED** | Low | `N-19` | Baseline refresh on a corrupt baseline says "does not exist" |
| **RESOLVED** | Low | `N-20` | ci_install_covers_conftest: `pytest_plugins` and `-p` plugins are not required; non-recognised runners pass silently |
| **RESOLVED** | Low | `N-21` | stale_comment_age early warning fails the test under `-W error` |
| **RESOLVED** | Low | `N-22` | Docs drift: new options not in README |
| **RESOLVED** | Med | `K-1` | `py-ci-shared run` crashes on a cp1251 console and loses the finding text |
| **RESOLVED** | Med | `K-2` | The plugin reads `[tool.py_ci_shared]` from pytest's rootdir, the CLI from the nearest pyproject; gates vanish silently |
| **RESOLVED** | Med | `K-3` | Glob kwargs return nothing when the repo path contains `[`, `]`, `*` or `?` |
| **RESOLVED** | Med | `K-4` | `iter_files` silently changes corpus definition when `git ls-files` fails or times out |
| **RESOLVED** | Med | `K-5` | `resource_leak_guard` misses env leaks: the first test of every session without psutil, and on 3.9 any test that imports a stdlib module |
| **RESOLVED** | Low | `K-6` | `version_tag_currency` accepts tag `10.2.3` as the tag for declared version `0.2.3` |
| **RESOLVED** | Low | `K-7` | `[tool.py_ci_shared]` shapes are under-validated: a string `enable`, duplicate names and a non-numeric `budget_s` |
| **RESOLVED** | Low | `K-8` | `scan_python` scans one file twice when it is reached through a relative and an absolute spelling |
| **RESOLVED** | Low | `K-9` | `node_index` pins every superseded tree; the "bounded by corpus size" promise does not hold |
| **RESOLVED** | Low | `K-10` | A malformed baseline count raises `ValueError` instead of `BaselineError`, and negative counts are accepted |
| **RESOLVED** | Low | `K-11` | Configuration ERROR results also print a spurious "over its 0s budget" line |
| **RESOLVED** | Low | `K-12` | `run`/`refresh` reject dashed gate names that `tool` and the plugin accept |
| **RESOLVED** | Low | `K-13` | `subprocess.run(text=True)` without `encoding` loses or garbles git output on a non-UTF-8 Windows locale |
| **RESOLVED** | Low | `K-14` | Eight private git runners with divergent policies belong in `_core` |
| **RESOLVED** | Low | `K-15` | `allow_unparsed` and floor naming are inconsistent across gates, against the repo's own rule |
| **RESOLVED** | Low | `K-16` | `find_stale_pin` counts prereleases and unrelated tags as releases behind |
| **RESOLVED** | Low | `K-17` | No written compatibility promise for the moving `@v1` tag, although v1.x has changed behaviour |
| **RESOLVED** | High | `WF-1` | lint-advisory's pip-audit audits pip-audit's own venv, never the consumer project |
| **RESOLVED** | Med | `WF-2` | release.yml's publish job is not re-runnable; a failed v1 move leaves "Latest" published and v1 stale |
| **RESOLVED** | Med | `WF-3` | Version not bumped after v1.19.0 shipped; registry `since` and CHANGELOG say 1.20.0, and no test catches it |
| **RESOLVED** | Med | `WF-4` | Reusable workflows silently fall back to master when the pinned ref cannot be fetched |
| **RESOLVED** | Med | `WF-5` | self-ci never exercises the consumer fetch path of the reusable workflows |
| **RESOLVED** | Med | `WF-6` | release.yml does not require the tagged commit's CI to be green, and runs 3 test files, not "the test suite" |
| **RESOLVED** | Med | `WF-7` | No documented rollback for a bad release on the moving v1 tag |
| **RESOLVED** | Med | `WF-8` | README states the wrong config-fetch mechanism (`github.job_workflow_sha`) |
| **RESOLVED** | Low | `WF-9` | RELEASE_TOKEN is not behind a protected environment |
| **RESOLVED** | Low | `WF-10` | README stale or wrong facts |
| **RESOLVED** | Low | `WF-11` | EXEMPT entries whose subject can be seeded as files |
| **RESOLVED** | Med | `WF-12` | Timeout-bound warm-worker tests fail and crash xdist workers under load |
| **WON'T FIX** | Low | `WF-13` | Slow tests: top offenders |
| **RESOLVED** | Low | `WF-14` | Workflow hygiene leftovers |
| **RESOLVED** | Low | `WF-15` | Own-suite tests whose assertion loops can run zero times |
| **NOT A DEFECT** | Low | `WF-16` | The consumer doctest symptom is not a stdout leak; nothing in py-ci-shared swaps sys.stdout |
| **RESOLVED** | High | `G-1` | Commits that never went through the hooks: a CI check over the pushed range |
| **RESOLVED** | High | `G-2` | A sibling dependency floor in pyproject vs the sibling revision CI actually installs |
| **RESOLVED** | High | `G-3` | optional_truthiness misses optionals carried on `self` and forwarded to helpers |
| **RESOLVED** | Med | `G-4` | Stdlib API newer than `requires-python` (guard-aware vermin) |
| **RESOLVED** | Med | `G-5` | Line endings of the COMMITTED blobs: bare CR, mixed, CRLF in an LF repo |
| **RESOLVED** | Med | `G-6` | Library repos: every name a consumer imports still resolves |
| **RESOLVED** | Med | `G-7` | resource_leak_guard: check the standard streams, cwd, sys.path and warning filters; and get it adopted |
| **RESOLVED** | Med | `G-8` | Imports of another package's vendored internals |
| **RESOLVED** | Med | `G-9` | External-fact tables carry a source and a dated check, and go stale visibly |
| **RESOLVED** | Low | `G-10` | Required status-check contexts that no workflow produces |
| **RESOLVED** | Low | `G-11` | Consumer workflows: moving runner labels and no update channel for actions |
| **RESOLVED** | Med | `G-12` | stale_comment_age: make the early warning the default |
| **RESOLVED** | Low | `G-13` | Commit metadata policy: forbidden trailers and BOM-prefixed subjects |
| **RESOLVED** | Low | `G-14` | Closed audit rounds are append-only |

## Round 2026-10-19 -- test-speed work and the self code audit

Details and measurements: `audits/2026-10-19/followups.md`.

| Disposition | Sev | Finding | Note |
|---|---|---|---|
| **RESOLVED** | Med | `S-1` | Postgres servers left by a killed process are reaped at the next start |
| **RESOLVED** | Med | `S-2` | pytest-randomly reseeders loaded in sessions without randomly (about 7 s per start) |
| **RESOLVED** | Low | `S-3` | `warn_days=0` rewritten to the default 7 by an `or` |
| **RESOLVED** | Med | `S-4` | Mutation sweep verdict split out of a file over the 1000-line budget (harness version 19) |
| **RESOLVED** | P1 | `S-5` | bare_except: the detector honours `# audit-exempt: bare_except -- <reason>`; the one intended handler in tests/test_mutation_teeth_regressions.py is marked (pyutilz 5372fee, pin in pyproject) |
| **RESOLVED** | P1 | `S-6` | busy_retry_loop: the detector honours the marker; the deadline-bounded read in _mutation_runner._reply_to is marked (pyutilz 5372fee) |
| **RESOLVED** | P1 | `S-7` | sentinel_guard_mismatch: the detector honours the marker; the guard on _git_show_version (None on every failure) is marked in sibling_floor_skew (pyutilz 5372fee) |
| **RESOLVED** | P2 | `S-8` | drifted duplicates: 13 groups down to 1. Merged since the last note: the two `_dotted` helpers of discarded_model_copy and statement_compilation (dotted_chain), and the two root-based `_workflow_files` (now the shared workflow_files, strict on a lowercase .yml/.yaml suffix; the Windows glob had listed .YML). Left on purpose: `register_refresh_option` in six modules is the public API that each consumer's conftest calls, with its own help text; `_dotted` variants that return other types (persisted_negative_probe keeps call receivers, config_call_site_parity returns lists) |
| **RESOLVED** | P2 | `S-9` | `default_via_or`: all 182 hits classified by reading each caller (audits/2026-10-21/). Three real traps fixed with regression tests: sql_verifier_coverage (empty exclusion reason), stale_comment_age (explicit empty scan_dirs), _mutation_fingerprint (explicit empty python_files, which pytest collects as nothing; harness 24). The rest are false positives or documented contracts |
| **RESOLVED** | Low | `S-10` | C901 over 10: all 62 functions refactored (bdf3aaa); `complexity_ratchet.json` is empty and enforced by the self-gate test |
| **RESOLVED** | Med | `S-11` | Corpus gates run on the package itself: 18 more enabled in af7e788, each a self-gate test in tests/test_self_gates.py |
| **WON'T FIX** | Low | `S-12` | Mutation sweep cost: measured, the cost is cold child pytest starts (about 7 s each). The one fix, PYTEST_DISABLE_PLUGIN_AUTOLOAD for nested runs, changes which plugins a consumer's tests load, so a mutant can look killed for the wrong reason; kept, cost accepted (audits/2026-10-21/mutation_profile.md) |

## Round 2026-10-20 — the attack-surface audit (secrets and CI, deserialization, subprocess and git, hot paths)

39 findings from four read-only agents. Every one dispositioned. Reports: `audits/2026-10-20/audit_*.md`.

| Disposition | Sev | Finding | Note |
|---|---|---|---|
| **RESOLVED** | P2 | `SEC-1` | release.yml publish checkout keeps no credential (persist-credentials: false); RELEASE_TOKEN reaches only the push step as a one-shot extraheader |
| **RESOLVED** | P2 | `SEC-2` | the rollback job runs only from refs/heads/master, with persist-credentials: false and the same one-shot header |
| **RESOLVED** | P2 | `SEC-3` | corpus_drift records the exception type name only; tests/test_corpus_drift.py::test_snapshot_errors_never_carry_the_exception_text |
| **RESOLVED** | P2 | `SEC-4` | env_example_round_trip reports the field name and exception type, never the value; the test asserts the value is absent |
| **RESOLVED** | P2 | `SEC-5` | ci_health and release_guard use a same-host redirect handler, so Authorization never leaves api.github.com; two tests |
| **RESOLVED** | Low | `SEC-6` | the eight lint tools default to exact pins (tool_versions.py: actionlint-py 1.7.12.25, zizmor 1.30.1, codespell 2.4.3, yamllint 1.38.0, bandit 1.9.4, vulture 2.16, interrogate 1.7.0, deptry 0.25.1; each at least two weeks old); a test holds the workflow defaults to them; CHANGELOG entry marks the behaviour change |
| **RESOLVED** | Low | `SEC-7` | self-ci writes ruff --show-settings to a file and prints its head, so pipefail cannot fail the step |
| **RESOLVED** | Low | `SEC-8` | ruff-blocking, black-filtered and lint-advisory reject a py-ci-shared-ref that is not letters, digits and ._/- or that starts with -, and fetch with - |
| **RESOLVED** | Low | `SEC-9` | load_consumers validates repo and name (same fix as DS-2); tests/test_consumers_validation.py |
| **RESOLVED** | Low | `SEC-10` | lint-advisory splits its pip-audit inputs with read -ra, so a pattern is not expanded against the workspace |
| **RESOLVED** | P2 | `DS-1` | the Dart empty-catch body now has one parse per position (disjoint branches, a comment runs to the end of its line); tests/test_regex_backtracking_reg |
| **RESOLVED** | P2 | `DS-2` | load_consumers rejects names and repos with separators, dot components, ? or #; checkout refuses a target outside dest |
| **RESOLVED** | P2 | `DS-3` | iter_files skips symlinks; tests/test_core_corpus_symlinks.py |
| **RESOLVED** | P2 | `DS-4` | the resolution probe copies with symlinks=True; tests/test_checkout_resolution_symlinks.py |
| **RESOLVED** | Low | `DS-5` | a config corpus entry that resolves outside the repository root raises ConfigError; tests/test_core_config_containment.py |
| **RESOLVED** | Low | `DS-6` | black receives the file names after --; tests/test_audit_20261020_hardening.py |
| **RESOLVED** | Low | `DS-7` | the PEP 508 rest group is taken greedily and the specifier pattern has one split per position; measured quadratic at 40000 spaces before, milliseconds |
| **RESOLVED** | Low | `DS-8` | the bare-domain URL pattern starts only at a token boundary, so it is linear |
| **RESOLVED** | Low | `DS-9` | one whitespace quantifier per position around the closing parentheses |
| **RESOLVED** | Low | `DS-10` | is_whole_file_read falls back to the text test on RecursionError, MemoryError and ValueError |
| **RESOLVED** | Low | `DS-11` | install_safe_hook refuses to patch a symlinked hook |
| **RESOLVED** | Low | `DS-12` | consumer_import_census rejects members that climb out of the destination and uses filter='data' on Python 3.12 and later |
| **RESOLVED** | P1 | `SUB-1` | _core/git.py sets NoDefaultCurrentDirectoryInExePath at import, which Windows reads at call time; the test asserts the variable, the Windows lookup it |
| **RESOLVED** | P2 | `SUB-2` | the nested pytest environment and the teeth sweep scrub GIT_DIR, GIT_INDEX_FILE and GIT_WORK_TREE through git_env |
| **RESOLVED** | P2 | `SUB-3` | embedded_postgres: tzdata pinned to 2026.5 and the pgserver wheel per platform to 0.1.4 (cp312) are checked against pinned SHA-256 before anything is unpacked; a platform without a verified digest refuses with the PG_BIN hint; both downloads have timeouts |
| **RESOLVED** | P2 | `SUB-4` | BLACK_CMD is split with posix=False on Windows; tests/test_audit_20261020_subprocess.py |
| **RESOLVED** | P2 | `SUB-5` | every listed subprocess call carries a timeout; warn-only runners report skipped on expiry, mypy_gate fails, black raises, the embedded server and dow |
| **RESOLVED** | Low | `SUB-6` | same fix as DS-2 |
| **RESOLVED** | Low | `SUB-7` | PY_CI_SHARED_GIT_TIMEOUT_S must be finite and positive, otherwise GitError |
| **RESOLVED** | Low | `SUB-8` | _reap_orphans skips directories owned by another uid |
| **RESOLVED** | Low | `SUB-9` | the README states that guard_population runs repository shell on whatever code it is given, so it must not run on untrusted code with secrets |
| **RESOLVED** | Low | `SUB-10` | ruff, bandit, vulture, pip-audit and black run with python -I; mypy is left out because a repository-local mypy plugin must be importable from the wor |
| **RESOLVED** | Low | `SUB-11` | setup_env refuses values with a line break or, for setx, 1024 characters or more |
| **RESOLVED** | Low | `SUB-12` | install_safe_hook writes the patched hook beside the original and replaces it |
| **WON'T FIX** | Low | `SUB-13` | the clone token already travels in GIT_CONFIG_* and never in argv; hiding it from the child environment needs a credential helper file with its own li |
| **RESOLVED** | Low | `SUB-14` | captured child output is bounded everywhere: run_with_deadline keeps the last 256 KiB and classifies while it streams (harness 25); the warm worker's reply reader reads in 1 MiB pieces and keeps at most 8 MiB of any line (harness 26). Peak memory on a 50 MiB child output: 150 MiB to 0.6 MiB; verdicts unchanged for every line within the caps |
| **RESOLVED** | P2 | `HOT-1` | git_listing remembers a positive work-tree probe per root, which removes two of the three git processes per listing (about 90 ms of about 260 ms measu |
| **WON'T FIX** | Low | `HOT-2` | measured: child_nodes costs about 0.65 microseconds a node, about 4 s over 6 million calls unprofiled; the profiled 35 s is mostly cProfile overhead p |
| **RESOLVED** | P2 | `HOT-3` | api_floor keeps vermin's output per file digest, floor and vermin version; a warm run on this repository takes 0.8 s instead of 17.2 s |

## Round 2026-10-21 -- meta-test profile (cProfile of the self-gate tests)

Details and measurements: `audits/2026-10-21/meta_test_profile.md`.

| Disposition | Sev | Finding | Note |
|---|---|---|---|
| **RESOLVED** | P1 | `PROF-1` | gate_cache stat walk: os.scandir instead of os.walk and stat; the self-gate run 1365 s to 595 s under cProfile (2a7b663) |
| **RESOLVED** | Low | `PROF-2` | config._inside_root: the repository root is resolved once per corpus entry instead of once per path; the symlink containment check is unchanged |
| **RESOLVED** | P2 | `PROF-3` | every gate walked the tree again: the tree digest is computed once per process and root (gate_cache.tree_digest, forget_tree_digests); a conftest fixture forgets it around every test except the self-gate module (16befa3) |

### Round 2026-10-21 -- all round files, dispositions

Every finding in the round has a disposition in its file; the table is the round's status by file.

| Disposition | Sev | Finding | Note |
|---|---|---|---|
| **RESOLVED** | P2 | `DVO-52`..`DVO-54` | empty `python_files` replaced by the default: pytest collects no files for an empty value (settled by running it), fixed in `_mutation_fingerprint.py`, pinned by `tests/test_mutation_fingerprint_python_files.py` |
| **RESOLVED** | Low | `DVO-87`, `DVO-88` | `stale_comment_age` widened an explicit empty `scan_dirs`; fixed at `stale_comment_age.py` (`scan_dirs is not None`) |
| **RESOLVED** | P2 | `DVO-134` | an empty exclusion reason was replaced by its unparsed AST; fixed in `sql_verifier_coverage.py` |
| **RESOLVED** | P2 | `allow_unparsed` / floor for `stale_comment_age` and `test_partition_reachability` | the contract's two baseline entries; additive switches with defaults that keep behaviour (CHANGELOG, Unreleased) |
| **RESOLVED** | P2 | `gate_entry_contract` baseline | 7 entries to 0: 5 never-parse entries in `NEVER_PARSES`, 2 handed-in entries in `NO_CORPUS`, the rest fixed in code |
| **RESOLVED** | P2 | `fail_open_handlers` baseline | the best-effort marker covers the silent handlers; the one kept-element handler stays a baseline entry with its reason, by design (an entry that is rejected would vanish from the scan) |
| **WON'T FIX** | P2 | `DVO-*` FALSE-POSITIVE (163) and INTENTIONAL (17) dispositions | not defects; each has its reason in `audits/2026-10-21/dvo_group_*.md` |
| **WON'T FIX** | Low | `retrofit` NOT RETROFITTED entries | file names, bytes or runtime checks with no parse step, so `allow_unparsed` has no meaning there (see `audits/2026-10-21/retrofit_group_*.md`) |
| **RESOLVED** | P1 | `MUT-1` (`sub14_bounded_output.md`) | bounded worker output; fixed, verdicts unchanged |
| **RESOLVED** | P2 | `WALL-*`, 1 of 19 (`wallclock_and_failopen.md`) | the one FIXED finding; the other 18 are KEPT with reasons (WON'T FIX) |
| **RESOLVED** | P2 | `wallclock_test_waits` baseline | 17 accepted entries, all fixture or script text inside string literals; the gate gained `python_mode="ast"` (default `"text"`, so `@v1` consumers are unchanged), py-ci-shared runs in that mode and the baseline is empty; the one real sleep in a moved test became an `os.utime` |
| **RESOLVED** | P2 | `pins` CI job | cause established (the newest release was v1.22.13 and consumers were pinned more than two releases behind; successive pushes cancelled the main CI run, so no tagged commit got a green one). Closed 2026-10-23: v1.23.3 tagged on cf55a29, self-ci green on that commit (18/18), Release run green, consumers moved to the tag
| **RESOLVED** | P1 | option B, stage 1 | `count_user_deferred_entries` and its walk moved to `py_ci_shared.deferred_entries`; py-ci-shared no longer imports it from pyutilz |
| **RESOLVED** | P1 | option B, stages 2 and 3 | the detector base, 99 detectors, the registry and the scan cache moved to `py_ci_shared.code_audit`; the `code_audit_meta` gate runs them from there, so py-ci-shared imports nothing from pyutilz; pyutilz keeps aliases at the old paths |
| **RESOLVED** | P1 | option B, stage 3b | 907 detector tests moved to `tests/code_audit`; the tests of pyutilz-only code (the CLI, `field_text_agreement`, the pyutilz facade) stayed |
| **OPEN** | P1 | option B, stage 6 | consumers on the py-ci-shared pin: claude-usage-notifier, social, flutter_uptime_monitor, dash_app_core, llm_bench, glossum_backend_scripts, algopacksimple, pyutilz, autopsia. Pins moved to the release commit cf55a29 (v1.23.3) in this round; closes when each consumer's pin push is in. mlframe is out of this round by the owner's instruction (its own dependency work). Findings that the stronger scanners surfaced were fixed in code, not baselined
| **RESOLVED** | P2 | option B, stage 4 | one `Finding` type: decided as option 2 (the moved CLI and field/text checker live in py_ci_shared.code_audit; pyutilz's alias shims removed, consumers import py_ci_shared directly). Behaviour change recorded in CHANGELOG (CLI JSON output bytes); no compatibility layer by the owner's instruction
| **RESOLVED** | P2 | `PROF-4` slow tests in the self-gate suite | scan-cache, positive-case, and release-CLI tests were 20-40 s each. Merged to master (speed commits in 431dc10 and the mutation-strengthened corrupt-entry test in 8258892). 38 s to 0.35 s, 40 s to 1 s, 21 s to 4.4 s, 32 s to 6.2 s. Remaining: release-CLI tests about 10 s each (interpreter start-up per call), accepted
| **OPEN** | Low | `_todo_markers` copy | `py_ci_shared.code_audit._todo_markers` duplicates `pyutilz.dev.meta_test_utils.scan_todo_markers`; replace pyutilz's copy by an import once the facade exports it |

## Round 2026-10-24 -- threat-model audit of py-ci-shared (seven directions)

Read-only pass plus fixes. Report: `audits/2026-10-24/threat_model.md`. Every finding dispositioned; the four FIX items are committed locally (`1c11b5e`, `0d3ad5d`, `ad03182`, `f9efd7c`) with a test that fails before each fix.

| Disposition | Sev | Finding | Note |
|---|---|---|---|
| **RESOLVED** | Med | `T-12` | an unparseable deferred-entry file was counted as zero and the drift gate passed; it is now reported as unscanned (`1c11b5e`) |
| **RESOLVED** | Low | `T-11` | redundant_test_fit carried a dead py3.8 fallback and a reason that contradicted requires-python >=3.9; now direct `ast.unparse` (`0d3ad5d`) |
| **RESOLVED** | Low | `T-13` | an unparseable string annotation was logged at debug; now warning (`ad03182`) |
| **RESOLVED** | Low | `T-01` | an audit-exempt reason shorter than 20 characters is refused; the 15 real markers in the tree all pass (`f9efd7c`) |
| **ACCEPTED** | Low | `T-02` | `${{ inputs.* }}` in run steps takes values from the calling repo's own pins; not an untrusted input |
| **ACCEPTED** | Low | `T-03` | write permissions are scoped per job and needed for the release and push steps |
| **ACCEPTED** | Low | `T-04` | baseline growth via environment is allowed in CI only; a local run cannot grow the committed baseline |
| **ACCEPTED** | Low | `T-08` | the command for guard_population comes from `tool/check*.sh` inside this repo and runs in the same CI; no new trust boundary |
| **ACCEPTED** | Info | `T-05` | every action is pinned by SHA |
| **ACCEPTED** | Info | `T-06` | the version gate and the rollback path are consistent |
| **ACCEPTED** | Info | `T-07` | `strict=False` is not called anywhere |
| **ACCEPTED** | Info | `T-09` | the hook installer writes only inside `.git/hooks` |
| **ACCEPTED** | Info | `T-10` | no `shell=True` anywhere |

## Round 2026-11-04 -- gate expansion, mutation speed, gate quality (six dimensions)

Reports: `audits/2026-11-04/30..35_*.md`; plan: `PLAN.md`.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 30-F1 | P1 | The unmutated suite is run twice per sweep (cold baseline, then warm baseline) and a third time per extra jobs partition; 31% of a small sweep | RESOLVED (commit: see git log, 'Cut the fixed cost of a mutation sweep'): `find_surviving_mutants(single_baseline=True)` is the default. The warm worker's first run (check_origin) IS the baseline of the primary set; the cold ru... |
| 30-F2 | P1 | Every surviving mutant is re-run COLD on the full test_paths (survivor re-check): 5x the warm cost, 28% of a small sweep | RESOLVED as opt-in: `confirm_in_worker=True` re-checks warm survivors in a second long-lived worker (`_mutation_sweep._confirm_survivor`); every `cold_confirm_every`-th survivor (default 10, the first included) stays a cold pro... |
| 30-F3 | P1 | No per-line coverage map: mutants on lines no test executes run the whole suite (and a cold confirm and the wider net) before being declared survivors | RESOLVED as opt-in: `coverage_prune=True` (needs `coverage`). One pytest run under `coverage run` with `dynamic_context=test_function` is the unmutated baseline and yields a statement-first-line -> test files map (`_mutation_sp... |
| 30-F4 | P2 | Killer file per line is not persisted; ordering state is per partition and per run | RESOLVED: the checkpoint entry now carries `killer` (first failing file, warm or cold, also from the confirm) and `line`. On start the order is seeded by killer frequency and before each mutant the killer of the nearest recorde... |
| 30-F5 | P2 | Per-mutant timeout is a constant (300s default), not derived from the measured baseline | RESOLVED: `timeout_factor` (default None = unchanged 300s constant): per-mutant timeout = min(timeout, max(30, factor * measured baseline seconds)) (`effective_timeout`); `timeout` stays the cap and a timeout stays INCONCLUSIVE... |
| 30-F6 | P2 | Wider-net baseline and each wider-net re-check multiply fixed cost for files with fallback_test_paths | RESOLVED (partly through F1/F3): the wider baseline is lazy (see F1), so a sweep with no survivor never runs it, and it is run once, not per partition. NOT done: skipping the wider net for lines no primary test covers, because ... |
| 30-F7 | P2 | Sandbox copy: shutil.copytree of the whole repo for every sweep and every extra jobs partition; ignore list is name-only | RESOLVED in part: `copy_ignore_patterns` (also through `sweep_files`) adds caller glob names to the sandbox and partition copies (`_mutation_model.copy_ignore`). WON'T FIX: hardlinks/junctions/reflinks for extra partitions; tes... |
| 30-F8 | P3 | Already implemented, no action: -x, -p no:cacheprovider, twin dedup, lead_with ordering, checkpoint/resume | RESOLVED (nothing to do for -x, cacheprovider, twin dedup, ordering, checkpoint: already implemented). The `addopts` part is done as opt-in `ignore_addopts=True`, which adds `-o addopts=` to every nested run and warm request an... |
| 30-F9 | P3 | Warm worker is 23% slower on autopsia but default use_warm_worker=True; no auto-fallback | RESOLVED as advice, auto-switch WON'T FIX: with F1 there is no longer a cold baseline wall time to compare with. Instead the sweep times its cold survivor re-checks and, after at least 3 warm kills, logs a WARNING when a cold w... |
| 30-F10 | P3 | Not recommended: in-process importlib.reload mutation | WON'T FIX: agreed with the finding. In-process importlib.reload gives stale references and half-reloaded classes (false survivors and false kills); the worker's purge is the safe in-process variant and its measured residual (3.... |
| 31-F1 | P2 | duplicate_test_body_same_file: two test functions with identical bodies, args and decorators (copy-pasted twin that asserts nothing new) | RESOLVED: gate duplicate_test_body. Hits (use_git=False): pyutilz 2 (tests/test_general_audit_20260926.py:381, tests/test_pythonlib_extra.py:88), social 6 (dashboard/tests/test_close_stays_closed.py:74, test_region_gate_is_name... |
| 31-F2 | P2 | definition_after_entry_guard: a top-level def/class that follows the script's if __name__ == '__main__' entry guard | RESOLVED: gate definition_after_entry_guard (new module rather than a script_entry_points rule, to stay additive and opt-in). Hits: pyutilz 0, social 1 TRUE (realtime_applications/show_top_jobs.py:342 get_dsn below the guard at... |
| 31-F3 | P2 | verify_then_reopen: a function verifies a file (sidecar/hash) by path, then re-opens the same path to load it (TOCTOU) | RESOLVED: gate verified_then_reopened. Hits: pyutilz 2 TRUE (src/pyutilz/text/tokenizers.py:380 load_tokens; src/pyutilz/core/serialization.py:181 _resolve_serialized_source), social 0. |
| 31-F4 | P2 | state_advanced_before_fallible_work: a schedule deadline/last-run stamp or a progress counter is written before the work it gates | RESOLVED: gate state_before_work (stamp + counter forms, configurable regexes, restored-in-except/finally and stamp-first-ok exemptions). Hits: pyutilz 0, social 0 (regression guard, matches the report); no true hits to fix. |
| 31-F5 | P2 | systemexit_in_library_function: sys.exit()/raise SystemExit inside a non-entry function that background threads and library callers reach | RESOLVED: gate systemexit_in_library. Hits (tests skipped): pyutilz 0, social 10: upwork_shared/_db.py:652 db_connection, _orchestration.py:329 _verify_proxy, _singleton_lock.py:59 acquire_singleton_lock, realtime_applications/... |
| 31-F6 | P2 | statement_privileges_granted: the (table, privilege) pairs each service's SQL needs are checked against its role's GRANTs | DEFERRED: needs each consumer grant-file format plus sqlglot lineage; the reference implementation is social test_scraper_sql_needs_are_granted.py and was not prototyped on a second repo, so a shared contract would be guessed. ... |
| 31-F7 | P2 | exception_text_bound_to_db_column_unredacted: str(exc) written into an INSERT/UPDATE parameter without the redaction helper | DEFERRED: FP risk high and the 0-hit prototype proves nothing without data flow; the right home is a configured taint rule on code_audit/provenance_flow.py (source str/repr of caught exception, sink execute-family parameter, sa... |
| 31-F8 | P3 | process_lifetime_collection_unbounded: module-level or self dict/list/set that grows inside functions and is never evicted or capped | WON'T FIX (generic form) / DEFERRED (narrow throttle-key form): the generic form had about 1 true hit in 16 and the 4 social hits read were all false positives; the narrow throttle-key variant found 5 hits in social, all bounde... |
| 31-F9 | P3 | dead_optional_collaborator: a *_memo/*_cache/*_fn parameter with a None default that no production call site ever passes | RESOLVED: gate optional_collaborator_wired with positional+keyword binding. Hits: pyutilz 0, social 0. The two unverified candidates (_build_client_context client_cache, _require_scan_mode fetch_fn) pass a variable positionally... |
| 31-F10 | P3 | monotonic_clock_zero_sentinel: an attribute initialised to 0/0.0 and later compared as time.monotonic() - attr with no unset guard | WON'T FIX: the report measured 0 true of 3 (all guarded by a disabled flag set together with the timestamp) and a precise gate needs flag-aware guard detection; the incident class is already covered by a None-sentinel conventio... |
| 31-F11 | P3 | per_call_figure_overwritten_not_summed: a per-call usage/cost attribute assigned by each POST/attempt instead of accumulated | WON'T FIX as an AST gate: not AST-checkable (the original defects lived in a helper called once per POST, so a loop rule would not have caught them) and the report itself recommends against it. The recommended alternative, a py... |
| 31-F12 | P3 | cli_flag_spelling_split: the same flag stem registered both as --a-b and --a_b across sibling scripts | RESOLVED: gate cli_flag_spelling (low-FP, built as proposed; reports the minority spelling sites). Hits: pyutilz 0, social 8 TRUE (stem dry_run: --dry-run x8 vs --dry_run x10): marketplace_crawler.py:927, scripts/dispose_termin... |
| 31-F13 | P3 | nullable_key_negative_control: the engine test of a statement that joins or NOT-EXISTS-es on a nullable column must seed a NULL row | DEFERRED: the guard half needs sqlglot lineage plus DDL nullability and the engine-test half needs a statement-to-test mapping that social itself has deferred; neither was prototyped, so a shared gate would be unvalidated. Revi... |
| 32-F1 | P1 | Gate njit_fastmath_nnan: @njit(fastmath=True) (nnan/ninf) on a kernel whose body produces or tests NaN/inf | RESOLVED: new opt-in gate `njit_fastmath_nan` (commit fb834f8). Hits over read-only trees: mlframe/src 13 (10 production: hurst.py _hurst_rs_single, dfa_alpha, higuchi_fd, dfa_alpha2_quadratic, _mfdfa_hurst_slope, multifractal_... |
| 32-F2 | P1 | Runtime harness numeric_edge_matrix: adversarial-input matrix (empty, single-class, all-NaN, ties, +/-inf, constant, float32-overflow, 1 row) driven over every registered metric/kernel, plus CPU-vs-GPU backend parity | RESOLVED as a library: `numeric_edge_matrix` (assert_edge_contract with expect table and twin comparison, assert_population_covered, edge_cases; commit f9f795b). Own tests pin a toy ZeroDivision-on-empty metric, a Brier-style 0... |
| 32-F3 | P1 | Runtime target-leak canary: transformers/encoders that emit train-row features must be out-of-fold (fit_transform on a pure-noise target must show ~0 train correlation) | RESOLVED as a library: `target_leak_canary` (assert_no_train_target_leak with average-rank Spearman, assert_oof_scores_at_chance for stackers; commit f9f795b). Tests: an in-sample per-bin target-mean encoder fails (+0.18 on noi... |
| 32-F4 | P2 | Gate ordinal_rank_scatter: ranks built as ranks[argsort(x)] = arange(...) (ties split arbitrarily, NaN ranked highest) instead of average ranks | RESOLVED: new opt-in gate `ordinal_rank_scatter` (commit 8295d56). Hits: mlframe/src 8 (5 production: _ranks_shared.py:15 ordinal_ranks_float (name says ordinal on purpose: mark `# ordinal-rank-ok`), evaluation/blend_source_sel... |
| 32-F5 | P2 | Gate group_blind_split: function that receives groups/group ids but builds KFold/StratifiedKFold/ShuffleSplit/train_test_split without them | RESOLVED: new opt-in gate `group_blind_split` (commit c6dc34c) with group_params/splitters/allowed config and `# group-blind-ok`. Hits: mlframe/src 7, pyutilz 0 (the `if groups is None` fallback exemption removed the prototype'... |
| 32-F6 | P2 | Gate caller_frame_mutation: public function mutates a DataFrame parameter in place (X[col]=..., X.columns=..., inplace=True) without a copy | RESOLVED: new ADVISORY gate `caller_frame_mutation` (sha e18a338): frame-named parameter never rebound and mutated by item assignment, `.columns`/`.index` assignment, `del X[c]` or `inplace=True`; warns with `CallerFrameMutatio... |
| 32-F7 | P2 | Gate oof_buffer_zero_init: OOF/holdout buffer initialised with zeros/empty and filled per fold from a caller-supplied splitter (rows no fold validates keep 0) | RESOLVED with the refined rule: new opt-in gate `oof_buffer_zero_init` (commit bfffa78): oof/holdout-named np.zeros/empty/ones buffer filled in a loop over `<x>.split(...)` or a splitter-like parameter, skipping locally constru... |
| 32-F8 | P2 | Gate unseeded_rng_fallback: library function turns rng/random_state=None into an OS-entropy generator (np.random.default_rng() / RandomState() / random.Random()) on a selection or fit path | RESOLVED: new opt-in gate `unseeded_rng_fallback` (sha 51c2300): zero-argument or `None`-seeded `default_rng`/`RandomState`/`random.Random`/`np.random.seed`/`random.seed` inside a function; marker `# rng-entropy-ok`, config `al... |
| 32-F9 | P2 | Runtime harness cache_key_sensitivity: every input that changes the memoised result must change the cache key | RESOLVED as a library: `cache_key_sensitivity` (assert_key_sensitive with sensitive/insensitive tables, determinism check and a signature population check; commit f9f795b). Tests: key ignoring y fails, key including verbose fai... |
| 32-F10 | P2 | Runtime harness estimator_input_invariants: column-order, n_jobs/worker-count and frame-type invariance of fit/predict results | RESOLVED as a library: `estimator_invariants` (assert_column_order_invariant, assert_n_jobs_invariant, assert_frame_type_parity; commit f9f795b). Tests: position-indexed predictor fails, by-name passes, a clear refusal is accep... |
| 32-F11 | P3 | Ratchet explicit_ddof: np.std/np.var/np.nanstd/.std() without ddof in statistics code (numpy ddof=0 vs pandas/polars ddof=1) | WON'T FIX: 277 numpy-form sites and 256 method-form sites, FP high by the audit's own measure, and 'ddof must be explicit' is a code-style convention per repo, which py-ci-shared's baseline_ratchet already supports for any cons... |
| 32-F12 | P3 | Promote mlframe-local ML meta-tests to py-ci-shared so pyutilz/social/glossum get them: njit_unsupported_calls, truncating_target_discretisation, perm_loop_invariants, frame_copy_in_target_loops, no_sklearn_metrics_in_production | RESOLVED for the two AST-checkable members: `njit_unsupported_calls` (typing.cast, np.median, np.nanmedian, warnings.warn, logger calls inside njit/jit/vectorize/guvectorize/cfunc/torch.jit.script; table replaceable and extensi... |
| 32-F13 | P3 | Gate loop_invariant_heavy_call: np.sort/argsort/unique/quantile/median/bincount/searchsorted on a loop-invariant array inside a Python loop | RESOLVED: second opt-in rule set in `loop_invariant_cast` (`heavy=True`, rule `loop-invariant-heavy-call`, sha 9e0379d) with the proposed HEAVY table, all operands loop-invariant (positional AND keyword, which removed 9 `np.bin... |
| 32-F14 | P3 | Regression guard gates for already-fixed shapes: boosting best_iteration used as a tree count without +1, raw-label compare against argmax of a proba matrix, estimator __init__ rewriting its own parameter | RESOLVED: new opt-in gate `ml_api_contracts` with three rules, best-iteration-count, argmax-vs-raw-label, init-rewrites-param (commit 908e757). Hits: mlframe/src 2, pyutilz 0, both true init-rewrites-param: feature_engineering/... |
| 32-F15 | P3 | Honest negative results (no new gate proposed): fit-before-split leakage, list(set()) column order, tuple/dict key omission by static scan | RESOLVED (note, no gate): recorded so later rounds do not re-propose these. Fit-before-split 0 hits, list(set()) 1 display-only hit and static cache-key omission (215/331) are closed as negative results; the cache-key concern i... |
| 33-F1 | P1 | ImportAliases is one flat per-module map, last import wins and unimported names resolve to themselves: a stray same-named import anywhere hides every use; a parameter/local named like a library is judged as that library | RESOLVED. `ImportAliases.from_tree(tree, scoped=True)` is a new opt-in scope-aware mode (imports bind their own scope, parameters and non-import rebinds shadow and resolve to None, `p = pickle` / `L = pickle.loads` resolve one ... |
| 33-F2 | P2 | Text-ratchet gates silently skip any file that is not valid UTF-8 (skip_as_pass, wallclock_test_waits, guard_without_fixture, git_child_env, dup_baseline_percent_key) | RESOLVED. `_text_ratchet.text_scan()` returns the unreadable files as `<rel>: unreadable` findings; skip_as_pass, wallclock_test_waits (test files only), git_child_env (test files only) and guard_without_fixture use it. dup_bas... |
| 33-F3 | P2 | Text-ratchet findings are keyed by 'path: normalized line', so N identical lines in one file collapse to one key and a baseline entry for one hides every later copy | RESOLVED. `_text_ratchet.record_finding()` numbers repeated keys (`key`, `key #2`, ...) in skip_as_pass, wallclock_test_waits and git_child_env; a baseline accepting the first copy no longer hides pasted ones (test: a second bl... |
| 33-F4 | P2 | Case-mismatched paths pass on Windows (case-insensitive FS) in every exists()-based reference gate; fail on Linux CI | RESOLVED for the reference gates that test file existence: `_core.exists_exact_case(path, root)` (new `_core/paths.py`) compares every component below the root against the directory listing; used by phantom_markdown_links, comm... |
| 33-F5 | P2 | swallowed_exceptions does not see contextlib.suppress(Exception/OSError), the exact semantic twin of 'except: pass' | RESOLVED. swallowed_exceptions visits `with`/`async with`: `contextlib.suppress(Exception/BaseException/OSError/IOError/PermissionError/shutil.Error)` (any alias) is judged like the equivalent silent handler with the same benig... |
| 33-F6 | P2 | unsafe_deserialization: any earlier sha256/hmac/verify call in the function counts as verification of an unrelated payload | RESOLVED. unsafe_deserialization: an earlier hash/verify/producer call now counts only when it touches the loaded value (a name connected to the load argument by assignment, e.g. `data = fh.read()`); `hashlib.sha256(b'unrelated... |
| 33-F7 | P2 | unsafe_deserialization coverage holes: pandas.read_pickle, pickle.Unpickler(fh).load() used directly, class-body and lambda call sites, marshal/shelve/jsonpickle/keras load_model, ruamel typ='unsafe' | RESOLVED except one item. Added pandas.read_pickle, pickle.Unpickler(fh).load(), marshal.load(s), shelve.open, jsonpickle.decode, ruamel YAML(typ='unsafe'), keras load_model(safe_mode=False), non-literal torch.load weights_only... |
| 33-F8 | P2 | rollback_then_continue treats ANY assignment in the handler as an accumulator reset, so 'msg = str(e); session.rollback()' and 'errors = errors + 1' exempt the shipped bug shape | RESOLVED. A handler assignment is a reset only for a constant, an empty literal or a no-argument constructor; `msg = str(e)` and `errors = errors + 1` no longer exempt. Rollback helpers named *rollback* are seen. NOT done: `rb ... |
| 33-F9 | P2 | fail_open_handlers admit_on_error only recognises list.append of the loop element | RESOLVED except two items. admit_on_error now sees append/add/extend/insert/appendleft, `out += [x]`, `out[x] = ...` and yield; gate_returns_true also sees `ok = True` in the handler with `return ok`. NOT done: finite_guarded_r... |
| 33-F10 | P2 | hardcoded_seed_in_library misses a module constant seed, expression seeds, seeds in dict literals, and only recognises params literally named random_state/seed/rng; flags the 'only when caller gave none' else-branch | RESOLVED. Module constants bound once to an int, constant-folded `+ - * // %`, `**{'random_state': 7}`, parameter names matching `(^/_)(seed/random_state/rng/rs)s?($/_)`, lambdas, and the fall-through after `if <seed> is not No... |
| 33-F11 | P2 | ci_workflow_timeout_gate accepts any value of timeout-minutes: 0, 360 (the default), 99999, negative, non-numeric | RESOLVED. JobTimeout.timeout_problem(): 0, negative, non-integer, >= 360 and > max_minutes (default 120, configurable on find_jobs_missing_timeout and assert_all_jobs_have_timeout) count as missing; `${{ }}` expressions pass. C... |
| 33-F12 | P2 | wallclock_test_waits detects only the literal 'time.sleep(' and non-const Dart delays are missed, in BOTH text and ast mode | RESOLVED except one item. ast mode resolves through ImportAliases (renamed imports, `from time import sleep`) and sees asyncio/trio/anyio sleeps (`sleep(0)` is a yield and is not a wait); Dart `Future.delayed(Duration(...))` wi... |
| 33-F13 | P2 | machine_specific_paths db-url rule only knows postgres/mongodb schemes; mysql/redis/amqp credentials, string concatenation and non-default config files are not scanned | RESOLVED except UNC paths. db-url now matches any scheme with a non-placeholder password (the original already covered postgres/mysql/redis/mssql; amqp and https did not), string concatenation of constants and bytes constants a... |
| 33-F14 | P2 | latched_availability_flags decides 'availability flag' by 7 upper-case substrings; _HAVE_GPU, _CUDA_OK, _USE_NUMBA, _HAS_CUPY, _ENABLED, _OK, _GPU are not flagged, nor is a class-attribute or lru_cache latch | RESOLVED except the class-attribute latch. Flag names are matched by whole underscore words as well (HAVE, HAS, USE, OK, ENABLED, DISABLED, GPU, CUDA, NUMBA, READY, NO), and a functools.cache/lru_cache probe whose broad except ... |
| 33-F15 | P2 | optional/ratcheted-config gates that detect the setting by name only: sentinel_or_fallback and hardcoded_token_ceilings miss indirection and assignment forms | RESOLVED except two items. sentinel_or_fallback: names match case-insensitively, camelCase and without a unit suffix, walrus operands, `x.seed if x.seed else 5`, the marker anywhere in the expression's lines, root-level test mo... |
| 33-F16 | P2 | discarded_model_copy ignores the bare statement 'cfg.model_copy(update=...)' and tuple targets | RESOLVED. A bare `cfg.model_copy(update=...)` statement, tuple targets, `dataclasses.replace(...)` and module scope are covered. |
| 33-F17 | P2 | env_flag_parsing misses the dominant two-step idiom and aliasing: v = os.environ.get('X'); if v == '1' | RESOLVED. Two-step reads (`v = os.environ.get(..); v == '1'`, single assignment per file), walrus, `bool(read)`, `env = os.environ` aliases, constant names, case-insensitive prefix; membership only for boolean-looking literals ... |
| 33-F18 | P2 | predictable_tmp_path: gettempdir stored in a variable, concatenated, formatted or read as tempfile.tempdir / '/tmp/...' is not reported; any getlogin anywhere in the module suppresses; duplicate finding per site | RESOLVED. One finding per outermost expression, gettempdir stored in a once-assigned variable, f-strings and `+`, `tempfile.tempdir`, `/tmp` literals, and the per-user marker is scoped to the enclosing function (module-level co... |
| 33-F19 | P2 | ddl_lock_safety rule 2 covers only CHECK/FOREIGN KEY; ADD PRIMARY KEY/UNIQUE and SET NOT NULL (full scans/index builds under ACCESS EXCLUSIVE) pass; SET LOCAL lock_timeout before a COMMIT still counts | RESOLVED. ADD PRIMARY KEY/UNIQUE (without USING INDEX) and ALTER COLUMN SET NOT NULL are reported under the constraint rule (tiny_tables apply); SET LOCAL lock_timeout stops counting at COMMIT/END/ROLLBACK while a session-level... |
| 33-F20 | P3 | skip_as_pass: bare 'exit', lower-case 'skipped', 'sys.exit()' and 'raise SystemExit(0)' are not recognised; tools/ and .github/scripts are outside SKIP_DIRS | RESOLVED. Case-insensitive skipped, bare `exit`, `sys.exit()`/`exit()`/`quit()`/`SystemExit(0/None)`, and tools/ and .github/ added to SKIP_DIRS. logging.warning('SKIPPED') is not covered (a logging call is not a print and the ... |
| 33-F21 | P3 | swallowed_exceptions blind spots: handler nested in a finally is exempt regardless of its call, 'close'/'emit'/'__exit__' scopes are exempt even when they do real work, class bodies work but tests/scripts dirs are excluded by default | RESOLVED except the exclusion list. A handler in a `finally`, `close`, `aclose`, `__exit__` or `__aexit__` is judged by the release whitelist like any other (only `__del__` and `emit` stay exempt). WON'T FIX: normalising the de... |
| 33-F22 | P3 | identity_comparisons flags a local that shadows the module-level string, a name later rebound to a non-string, and conditionally bound names; misses str()/join/dedent-built constants | RESOLVED partly. A module name also assigned a non-string in the same scope is no longer a string constant; `str(...)`, `' '.join(...)`, `.format`, `textwrap.dedent(...)` built constants count. NOT done: a function-local rebind... |
| 33-F23 | P3 | lock_held_across_io: cursor.execute / Path.write_text under a lock, asyncio.sleep under async with lock, lock.acquire()/release() pairs and self._mutex are not judged | RESOLVED except lock.acquire()/release() pairs. cursor/conn/session execute/commit/fetch* round trips, write_text/write_bytes, asyncio.sleep under `async with`, and mutex/semaphore/camelCase lock names (a name like `clock` or `... |
| 33-F24 | P3 | vacuous_loop_assertions: unittest self.assert* in an if inside a loop over an empty iterable is not recognised; while loops not judged | RESOLVED partly. unittest `self.assert*`/`self.fail` are checks, and count as floors. REJECTED: while loops. The reported shape is a loop whose body is only checks; a `while` body that pops/assigns is not that shape and a while... |
| 33-F25 | P3 | workflow_runner_labels (regex over text) flags the label inside run: strings and name: fields and misses upper-case spellings | RESOLVED. A runner label inside a `run:`/`name:`/`if:`/`shell:`/`id:`/`description:` value or a `/`/`>` block under one is text and no longer reported; comparison is case-insensitive (`Ubuntu-Latest`). The gate stays line-based... |
| 33-F26 | P3 | phantom_markdown_links ignores HTML <img src>/<a href>, multi-line link text and anchors; comment_repo_paths ignores trailing comments after shell code and Makefile/ps1/go/sql | RESOLVED partly. phantom_markdown_links reads HTML `src`/`href` attributes (img, a, source, video, audio, link, script, iframe) and link text wrapped over lines. NOT done: heading-anchor existence (`real.md#nope`; needs the ren... |
| 33-F27 | P3 | epsilon_padded_denominators misses a named EPS constant, np.finfo(...).eps, a power stored in a variable first, floor division and x ** -1 | RESOLVED partly. Module constants bound once to a small number, `np.finfo(..).eps` / `.epsilon`, `//` and `(x + eps) ** -1`. NOT done: a power stored in a local first (`p = r**d; k / (p + eps)`) and `np.exp(-x) + eps`, which ne... |
| 33-F28 | P3 | no_xfail_to_defer reports each decorator-xfail twice; reason checks do not apply to skip with a reason; unread_function_params flags interface-required overrides | RESOLVED. REJECTED: the 'twice' report; `xfail-not-strict` and `no-reason` on one line are two different defects with their own baseline keys, by design. RESOLVED: an unconditional `pytest.mark.skip` / `pytest.skip` reason must... |
| 33-F29 | P3 | Smaller detector gaps: single_shot_timing_assertion (datetime.now diffs, benchmark-fixture .elapsed), hash_fed_by_array_copy (xxhash, crc32, variable-then-hash, memoryview), gpu_timing_sync (jax without block_until_ready; .cpu() flagged), order_losing_filters (polars is_in with .take form, helper-split mask), polars_null_equality (df['a'] == df['b'], lit(None), is_in([None])) | RESOLVED partly. hash_fed_by_array_copy: crc32/adler32/xxhash sinks and `b = a.tobytes(); sha256(b)`; gpu_timing_sync: `.cpu()/.item()/.numpy()/.tolist()` end a timed region. polars_null_equality (advisory): `col != pl.lit(None... |
| 33-F30 | P3 | Teeth/canary corpus: 117 of 146 canaries have a single violation file with one seeded shape, and the teeth check only requires 'raises and names the seed token' | RESOLVED partly. `Canary.tokens` makes the teeth check require EVERY seeded shape to be named by the failure (violation and BOM variants). Converted to multi-shape: swallowed_exceptions and unsafe_deserialization (earlier) and ... |
| 33-F31 | P3 | Several gate unit tests assert only 'some finding contains substring' (any(... in v for v in problems.values())) so extra/duplicate findings and wrong counts pass | RESOLVED partly. tests/test_dart_scanners.py: all 27 `assert any(token in v ...)` became exact counts; in this pass eleven more files lost their `assert any(substring)` assertions (`assert sum(1 for x in findings if token in x)... |
| 33-F32 | P3 | line_has_marker accepts the opt-out marker anywhere after the first '#' on the line, including inside a string literal | RESOLVED. `line_has_marker` finds the marker only in the real trailing comment (a `#` inside a quoted string is skipped; an unterminated quote falls back to the first `#`, as before). Tests: tests/test_gate_report_marker.py. CH... |
| 34-F1 | P1 | Ranked adoption matrix: gates adopted by 4-7 of 8 Python consumers but missing in specific repos | RESOLVED. adoption_matrix counts non-text evidence now: [tool.py_ci_shared] enable lists and gate tables (already), plus pre-commit hook ids of the py-ci-shared repo block (id: repo-hygiene) and gate names listed as strings in ... |
| 34-F2 | P1 | 29 registered modules are referenced by no consumer (enabled nowhere) | RESOLVED for this repo, rest goes to step 8. The report now has an Enabled nowhere list; the committed matrix classifies the 27 unreferenced modules as self jobs, user-level hooks, plumbing or real gates with no adopter (the la... |
| 34-F3 | P2 | config_drift_check hard-codes two consumers instead of reading configs/consumers.toml | RESOLVED. config_drift_check.consumer_urls reads configs/consumers.toml (every consumer, each on its own branch; private repos through the contents API with CONSUMER_READ_TOKEN, passed to the step in config-drift-check.yml; ski... |
| 34-F4 | P2 | consumer-pins daily job silently degrades to public repos when CONSUMER_READ_TOKEN is absent | RESOLVED. _consumers checkout takes --fail-on-skipped-private (exit 1, names the skipped repos); consumer-pins.yml passes it unless a manual run sets the new allow-skipped-private input. Default CLI behaviour is unchanged (corp... |
| 34-F5 | P2 | Pin skew across consumers: six different py-ci-shared SHAs, flutter_app_core at v1.22.8 | DEFERRED. The summary already shows releases behind per repo whatever the tolerance (adoption_matrix Summary column; the consumer-pins job writes it to the job summary). Auto-PRs bumping pins on release need a token with write ... |
| 34-F6 | P2 | Reusable lint workflows adopted unevenly; dash_app_core uses none | RESOLVED. The report lists, per Python consumer, the reusable workflows it does not call (Reusable workflows a Python consumer does not call), and the committed matrix records the current holes (dash_app_core none; social lacks... |
| 34-F7 | P2 | No reusable workflow runs the gate suite; gate execution depends on each consumer's own tests | RESOLVED as an additive, default-off workflow. adoption_matrix flags Python consumers without a [tool.py_ci_shared] enable list. `.github/workflows/gates.yml` is the reusable runner: it installs py-ci-shared at `py-ci-shared-re... |
| 34-F8 | P3 | cli tool subcommand coerces non-int returns to 0; main() maps any KeyError to exit 2 | RESOLVED. cli tool: None or int is the exit code, anything else (a findings list, a str) is a ConfigError with exit 2 and a message saying so; the KeyError catch in main() is gone and only the two lookups that mean 'unknown nam... |
| 34-F9 | P3 | Reusable workflows define no concurrency group | DEFERRED, by scope rather than risk. A `concurrency` group belongs to the CALLER workflow (a reusable workflow's own block does not apply to the caller's runs), so the only lever is a check on callers. An opt-in check could not... |
| 34-F10 | P3 | lint-blocking skips interrogate, deptry and zizmor silently when inputs are empty/false | RESOLVED. lint-blocking.yml has a first step 'Report tools skipped by their inputs': a ::notice:: and a job-summary line for each of interrogate, deptry and zizmor that the caller's inputs skip; never fails. Reporting each call... |
| 34-F11 | P3 | OS/Python matrix hole: Windows and macOS legs run 3.11 only; all other workflows 3.11 only | RESOLVED (non-blocking first, as proposed). self-ci.yml gains windows-latest legs for Python 3.9 and 3.13 with preview: true (continue-on-error). They become blocking after a week of green runs; that decision needs CI history t... |
| 34-F12 | P3 | adoption measurement may under-count: autopsia shows 32 gates with no [tool.py_ci_shared] table despite 16k files | RESOLVED. The two readers named in the finding exist now (listed gate names in .py files, pre-commit hook ids); py-ci-shared run <name> CLI calls were already matched. Re-run against the real clones: counts moved mlframe 125 to... |
| 35-F1 | P1 | ~32% of a code_audit scan of pyutilz is raw ast.walk: 183 call sites in 65 scanners bypass the memoised walk, and subtree walks re-walk the corpus ~5x | RESOLVED. Every ast.walk (169 real calls in 57 scanners) and ast.iter_child_nodes (20 calls) in code_audit now goes through walk_nodes/child_nodes from _base, i.e. _core.node_index. node_index.walk memoises the descendants of A... |
| 35-F2 | P2 | code_audit._base._safe_parse and _core.source.parse_source are two separate parse caches: every file is parsed (and walked) twice in one run_all | RESOLVED. code_audit._base._safe_parse delegates to _core.source.parse_file; SourceReadError/SourceParseError (and RecursionError/MemoryError, kept from the old handler) give None. _PARSE_CACHE and its LRU are gone; _base.clear... |
| 35-F3 | P2 | Pre-push self gate is I/O-bound, not parse-bound: _io.open is 340s of 606s profiled (23,576 opens, ~14 reads per file); parse only 6s | RESOLVED. `_core.source.read_bytes_cached` is the one process-wide bytes cache (stat signature plus the racy-timestamp rule of the parse cache; files over 4 MB never kept; table dropped past 400 MB) used by read_source, parse_s... |
| 35-F4 | P2 | budget="warn" lets repo_hygiene (363s vs 30s) and standard_stream_restore (39.8s vs 30s) pass the self gate with exit 0 | RESOLVED (additive). cli._report keeps the exit code but now also prints a BUDGET summary line after the totals, a GitHub ::warning annotation per over-budget gate (when GITHUB_ACTIONS is set) and writes the list to GITHUB_STEP... |
| 35-F5 | P3 | near_duplicate_function_body._candidate_pairs is O(n^2) over functions | RESOLVED. `_candidate_pairs` was already an inverted shingle index; what stayed super-linear was counting EVERY posting of every shingle of every entry. It now visits each pair once from its smaller side and follows only a pref... |
| 35-F6 | P3 | No self meta-test for public API quality: 20 of 1018 public module-level functions not fully annotated, 68 lack docstrings, 194 public modules lack __all__ | RESOLVED. tests/test_self_api_hygiene.py with shrink-only baselines in tests/baselines/self_api_hygiene.json: unannotated public functions 20, public functions without docstring 61, public modules without __all__ 184 (the audit... |
| 35-F7 | P3 | print() in non-CLI library modules: 14 calls in arb_checks, baseline_ratchet, import_layering, sql_verify (not cli=true in registry) | RESOLVED. Same file, rule library_print: print() outside registry cli=true modules, outside main/_main/_cmd_*, without file= or a T201 noqa. Baseline 14 call sites in 6 functions (arb_checks.assert_arb_catalogues_are_sound 1, b... |
| 35-F8 | P3 | No self meta-test on module-level call statements: 113 top-level Expr(Call) statements in library modules | RESOLVED. Same file, rule import_time_calls: module-body Expr(Call) other than append/add/update/setdefault/register/extend. The audit's 113 counted a different set than this definition (module-body statements, the only ones that run a... |
| 35-F9 | P3 | Canary exemptions: 35 of 179 gates have no tests/canary/<name>/ directory; staleness of EXEMPT entries is not asserted | RESOLVED. tests/test_gate_teeth.py::test_canary_exemptions_are_valid_and_still_needed: every EXEMPT key names a module, and an EXEMPT gate whose source now calls a corpus function (_core.gate_contract.scans_a_corpus) must carry... |
| 35-F10 | P3 | Verified already satisfied (no action): bare except 0, registry sorted/unique, registry vs README 0 absent, registry vs tests 0 missing, cli=true without main 0, registry entries naming absent functions 0 | WON'T FIX (nothing to fix). Verified already satisfied; the optional E722 pin is redundant: ruff's default select already contains E722 (E4, E7, E9, F), and a bare except would fail the blocking ruff hook. |

## Round 2026-11-07 — full agent audit of py_ci_shared (dimensions 40-52)

132 findings. Reports in audits/2026-11-07/. Each disposition is the text written into the finding; OPEN means not yet worked.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 40-F1 | P1 | systemexit_in_library: entry_names is searched as a substring, so any function name containing main, cli, entry exempts itself (domain, client, remaining, decline) | RESOLVED 0c707a1: default entry_names anchored. |
| 40-F2 | P1 | swallowed_exceptions: ConnectionError/TimeoutError (OSError subclasses), named exception-tuple constants and suppress(*errs) are out of scope | RESOLVED 0c707a1: OSError subclasses, socket/os/select aliases, module tuple constants and suppress(*errs) (unresolved starred = broad). WON'T FIX part: sqlite3.Error, requests.RequestException, CalledProcessError are... |
| 40-F3 | P1 | hardcoded_seed_in_library: only the keywords random_state/seed are seen; random_seed (CatBoost), rng=, locals, SeedSequence/PCG64/Generator.manual_seed, tf/keras seeds pass | RESOLVED 0c707a1: widened keywords, seeders, .manual_seed, local int constants. |
| 40-F4 | P2 | swallowed_exceptions: any `return` in the try body exempts the whole handler, including a write that follows a conditional return | RESOLVED 3852401: fallback exemption now requires that no call follows the first return (the proposed 'last statement' rule flagged a real psutil fallback in this repo). |
| 40-F5 | P2 | systemexit_in_library: `import sys as _sys` and cross-module use through `import mod; mod.fn()` are not seen | RESOLVED 3852401: ImportAliases for exits; `import mod; mod.fn()` counted as shared use. |
| 40-F6 | P2 | rollback_then_continue: write detection keys on a receiver-name regex and literal SQL, so s.add(), helper writes and variable SQL pass | RESOLVED 3852401 (partial): writes found through the rolled-back object and execute(var_sql, params). WON'T FIX: bare execute(var_sql) stays a probe (existing negative control); `rb = s.rollback` aliasing not followed. |
| 40-F7 | P2 | check_then_act_outside_lock: class-level dicts, aliases, non-subscript acts (del, pop, update, setdefault, add, append), try/except KeyError and lazy singletons are out of scope | RESOLVED 3852401 (partial) + follow-up (see git log, 'Stop check_then_act_outside_lock pairing'): del, pop/popitem/update and cls._x acts. Follow-up removed false pairings found on pyutilz (8 src findings, all false: ... |
| 40-F8 | P2 | unsafe_deserialization: Unpickler subclass without find_class, a verify call inside a conditional branch, np.load(allow_pickle=var), keras/tf load_model | RESOLVED 3852401 (partial): dominating verify, Unpickler subclass without find_class, np.load(allow_pickle=var). WON'T FIX keras/tf load_model (safe_mode defaults True) and the sklearn.externals alias. |
| 40-F9 | P2 | fail_open_handlers: gate-named functions that swallow with pass/continue/suppress, sys.exit(0), pytest.skip, ignore a returncode or return clean on empty input are not reported | RESOLVED 0cb41d8 (partial): implemented as rule gate_swallows_error in fail_open_handlers: in a gate-named function an except for Exception/OSError/SyntaxError/ValueError/bare whose whole body is pass, continue, sys.exit(0), pytest.skip(...) or print(...) is reported; WARNING+ log, raise or '# best-effort:' honours it. Consumer-tree check (read-only): 0 findings on this repo src and tests, pyutilz (868 files) and llm_bench (132 files). WON'T FIX: unread subprocess returncode and 'return clean on empty input' (return 0 on empty is legitimate for most gates; an unread returncode needs dataflow and gives high false-positive risk); `except ...: return []` and `return 0` handlers are already silent_default_return/gate_returns_true. CHANGELOG Behaviour change entry. |
| 40-F10 | P3 | env_flag_parsing: bool(int(os.environ.get())), `'X' in os.environ`, f-string keys, default-argument reads are not boolean contexts | RESOLVED 3852401 (partial): bool(int(env)), `'X' in os.environ`, f-string keys. WON'T FIX default-argument reads (not a boolean context) and dict(os.environ). |
| 40-F11 | P3 | module_reload_safety.find_reloads_in_code: sys.modules.clear(), sys.modules[k]=None, exec_module of a spec, getattr(importlib,'reload') | RESOLVED 3852401 (partial): sys.modules.clear(), sys.modules[k]=None, getattr(importlib,'reload'). WON'T FIX exec_module (legitimate in importers; cannot tell re-execution from first load). |
| 40-F12 | P3 | predictable_tmp_path: exemption is by mere mention of getuid/USERNAME, and tempfile.mktemp and .format() are not seen | RESOLVED 3852401: per-user value must flow into the path; .format() paths and mktemp reported. |
| 40-F13 | P3 | naive_utcnow: getattr(datetime.datetime, 'utcnow') with a literal name is not seen | RESOLVED 3852401: getattr(x, 'utcnow'/'utcfromtimestamp') with a literal name. |
| 41-F1 | P1 | shared_mutable_placeholder flags every module-level singleton accessor (`_CLIENT = Client()`, a ThreadPoolExecutor, an Engine, a cache dict) and has no marker, allow list or baseline | RESOLVED 3ba0809: marker '# shared-mutable-placeholder-ok: <reason>' (assignment or return line, or a comment line above) and classes ending Executor/Pool/Client/Session/Engine/Queue/Logger/ContextVar/Local/Manager are not ... (full text in the finding) |
| 41-F2 | P2 | fail_open_handlers `silent_default_return` flags ordinary control-flow exceptions (StopIteration, KeyError, queue.Empty, ProcessLookupError, ZeroDivisionError, AttributeError, TimeoutError, CancelledError, UnicodeDecodeError, JSONDecodeError, CalledProcessError) in any function | RESOLVED 3ba0809: KeyError/IndexError/StopIteration/StopAsyncIteration/Empty/ProcessLookupError/ZeroDivisionError/CancelledError around a one-statement try are not a silent default; OSError, Exception, TimeoutError and ... (full text in the finding) |
| 41-F3 | P2 | CHANGELOG 'Gate false positives 2026-11-06' says a single-value parse guard (`int(x)` / `except ValueError: return default`) is no longer flagged; the code and its tests still flag it | RESOLVED 3ba0809: CHANGELOG corrected, code unchanged. Evidence for the code side: commit 70f2152 re-flagged value-parse fallbacks on purpose, tests/test_fail_open_handlers.py pins it, and the 2026-11-04 per-key decision says ... (full text in the finding) |
| 41-F4 | P2 | `# literal-set-ok:` exempts only the `literal-set` rule; the `threshold` and `named-constant` rules of drifted_duplicate_literals have no marker, so numeric-zero floors and per-kernel constants can only be baselined | RESOLVED 3ba0809: '# literal-set-ok:' now exempts named-constant and threshold sites too. The epsilon-floor default ignore for the threshold rule is DEFERRED (needs the operand-name heuristic); the marker is the per-site exit. |
| 41-F5 | P2 | no_source_text_tests reports any upper-case SQL keyword in a membership assert whatever the right operand is (HTTP Allow header, event list, dict keys, set of operations) | RESOLVED 3ba0809: a collection right operand (display, comprehension, .keys(), name ending in s, .headers) is not a probe; sql/stmt/query names are still flagged. |
| 41-F6 | P2 | env_flag_parsing reports numeric and path-like variables read by truthiness or compared with '0'/'1' whenever the variable name is not on its short word list | RESOLVED 3ba0809 + 050fa0d: a variable the same file also feeds to int/float/Path/os.path/.split/arithmetic/ordering is not a switch; bool(int(read)) stays a switch. pyutilz 1 -> 0, pcs 4 -> 3. |
| 41-F7 | P2 | fail_open_handlers still flags fail-closed verification and deny helpers (`return False` in `is_allowed`, `check_ready`, MAC and signature checks); only names containing 'signature' or 'token' escape by accident | RESOLVED by the commit "Stale baseline entries and surplus counts fail; include_stubs opt-in for runtime-hazard gates; fail-closed predicates exempt from silent_default_return; stale_baseline_sweep (audits 42-F4, 42-F5, 42-F9, 41-F7)": silent_default_return exempts a falsy-literal handler (False/None/0/'' or bare return) in a predicate-named function, or any handler marked '# fail-closed: <reason>'; token/digest stay in the cache-miss regex. Negative controls: return True in is_allowed/check_ready/cuda_ok/verify_mac/_is_trusted and a non-predicate returning False are still flagged. Measured on autopsia_wt37 485f08b61 (read-only): clears _is_trusted, load_valid, load_verified, is_minted_case_id, cupy_device_usable; _secret, load_verified_bytes, trailer_mac, signed_pickle, load_record stay flagged (names are not predicates) and need the marker in autopsia; no new findings. |
| 41-F8 | P2 | pickle_state_completeness `unpicklable-without-getstate` flags every class that assigns a Thread, lock or connection outside __init__, including classes that are never pickled; no marker | RESOLVED 3ba0809: '# pickle-ok: <reason>' on the assignment line or the comment line above. The 'only classes plausibly pickled' narrowing is DEFERRED (needs cross-file pickle-site resolution). |
| 41-F9 | P3 | drifted_duplicate_literals `literal-set` groups dtype bounds, significance levels and port pairs because they are not in DEFAULT_IGNORE | RESOLVED 3ba0809: integer type bounds (127, 32767, 65535, 2147483647) and significance levels (0.01, 0.025, 0.05, 0.1, 0.9, 0.975) added to the ignored numbers; ports not added (no registry). |
| 41-F10 | P3 | hash_key_determinism flags `json.dumps` of a dict literal with a fixed key order and of `dataclasses.asdict(obj)` that is hashed without sort_keys | RESOLVED 3ba0809: dict display with string-constant keys (nested displays likewise) and dataclasses.asdict are fixed-order; comprehension, ** unpack and parameter payloads stay flagged. vars(self)/_asdict not covered (DEFERRED). |
| 41-F11 | P3 | secret_assertion_operands: 37-F22 closed the literal-name form only; `key = 'OMP_NUM_THREADS'; assert os.environ.get(key) == '1'` is still flagged | RESOLVED 3ba0809: a name bound once to a str constant, or looped over str constants, is judged by those names; parametrize-supplied names stay flagged. |
| 41-F12 | P3 | skip_as_pass treats `echo "SKIPPED <optional step>"` followed by `exit 0` after the real checks ran as a skipped guard | RESOLVED: skip_as_pass: read shell with a block parser and each workflow run: step as its own script (audit 41-F12) |
| 41-F13 | P3 | Tracker rows 37-F35 and 37-F40 carry stale or contradictory text, and 37-F19 stays 'NOT REPRODUCED' without a closing action | RESOLVED: tracker_summary_parity: one finding, one state across tracker row, table row and Disposition line; fix the rows it found (audit 41-F13) |
| 41-F14 | P3 | Glossum's gate_false_positives GF-2 ('gate may have lost detection of ADD UNIQUE / SET NOT NULL') is not reproduced: the current ddl_lock_safety still reports both migrations, so the baseline entries glossum removed are live findings | RESOLVED 3ba0809 (pin only): test_ddl_lock_safety gains the ADD UNIQUE (no CONSTRAINT) pin; SET NOT NULL and ADD CONSTRAINT UNIQUE were already pinned. The glossum disposition note is the parent's: GF-1 closed by ... (full text in the finding) |
| 42-F1 | P1 | DEFAULT_EXCLUDE drops any directory named build/dist/venv/site-packages anywhere in the tree, so a real package directory such as src/pkg/build is outside every gate's corpus in git mode | RESOLVED 3ba0809: iter_files keeps TRACKED files under build/dist/venv/node_modules/site-packages in git mode (one extra git ls-files --cached only when such a path is listed); dot-dirs and __pycache__ stay excluded; test ... (full text in the finding) |
| 42-F2 | P1 | scan_roots_excluding matches the exclude fragments against the ABSOLUTE posix path (the ARCH-14 defect again): a checkout under a directory containing a fragment parses zero files | RESOLVED 3ba0809: scan_roots_excluding matches root-relative paths; test_scan_roots_excluding_matches_fragments_against_the_root_relative_path failed before. The helper-level assert_ok was NOT added: the assert_* entry points ... (full text in the finding) |
| 42-F3 | P2 | line_has_marker treats text inside a multi-line string literal as a trailing comment, so a '# <gate>-ok: reason' line in a docstring or triple-quoted string suppresses a finding | RESOLVED 3ba0809: line_has_marker ignores lines inside a multi-line string (tokenize, only when the text parses as Python). The per-gate regex markers (atomic_write_staging etc.) run on raw lines and are NOT changed: DEFERRED, ... (full text in the finding) |
| 42-F4 | P2 | baseline_ratchet.Baseline.enforce exits 0 on stale entries and is key-only: a fixed violation leaves a slot that a later regression of the same key takes for free | RESOLVED by the commit "Stale baseline entries and surplus counts fail; include_stubs opt-in for runtime-hazard gates; fail-closed predicates exempt from silent_default_return; stale_baseline_sweep (audits 42-F4, 42-F5, 42-F9, 41-F7)": baseline_ratchet.Baseline.enforce returns 1 on a stale entry (prune message names the refresh command), so the slot a fixed violation leaves cannot be taken back by a regression; tests test_a_fixed_violation_cannot_be_taken_back_for_free and test_paid_debt_fails_until_the_baseline_is_refreshed failed before. CHANGELOG Behaviour change entry; stale_baseline_sweep measured 0 stale entries in the six consumers it could run (pyutilz, llm_bench, algopacksimple, dash_app_core, flutter_uptime_monitor, claude-usage-notifier); mlframe, glossum_backend_scripts, autopsia (timeout at 1500 s) and social (collection errors) not measured. |
| 42-F5 | P2 | _core Baseline.enforce BaselineOutcome.ok ignores stale entries and surplus counts; callers that test only .ok accept up to the old count | RESOLVED by the commit "Stale baseline entries and surplus counts fail; include_stubs opt-in for runtime-hazard gates; fail-closed predicates exempt from silent_default_return; stale_baseline_sweep (audits 42-F4, 42-F5, 42-F9, 41-F7)": BaselineOutcome.ok includes stale entries and surplus counts, new BaselineOutcome.blocking keeps the old meaning; mutation_teeth is strict through the property with no edit, unexecuted_function_bodies uses blocking to avoid a duplicate message; tests in tests/test_baseline_stale_strict.py and TestMultiset::test_a_surplus_count_cannot_be_refilled_for_free failed before. Same sweep result as 42-F4. |
| 42-F6 | P3 | parse and bytes caches trust a stat signature (mtime_ns, size, ino) for files older than 3s: a same-size rewrite with a preserved mtime serves the old tree | RESOLVED 3ba0809 on POSIX: stat signature gains st_ctime_ns (test skipped on Windows). On Windows ctime is the creation time, so the gap remains there; documented in the code and CHANGELOG. |
| 42-F7 | P3 | List-form baseline entries are joined with '/', colliding with a plain string key containing '/' | RESOLVED 3ba0809: a list baseline holding a pair and the string it joins to raises BaselineError; test_a_pair_and_a_string_that_join_alike_are_refused. |
| 42-F8 | P3 | Duplicate JSON keys in a baseline resolve last-wins silently, so a bad merge can raise a count unnoticed | RESOLVED 3ba0809: duplicate JSON keys raise BaselineError (Behaviour change entry); test_a_duplicate_json_key_is_refused. |
| 42-F9 | P3 | iter_files default pattern '*.py' is case-sensitive and ignores .pyi/.pyw although source.py decodes them as Python | RESOLVED by the commit "Stale baseline entries and surplus counts fail; include_stubs opt-in for runtime-hazard gates; fail-closed predicates exempt from silent_default_return; stale_baseline_sweep (audits 42-F4, 42-F5, 42-F9, 41-F7)": iter_files/scan_python take include_stubs=False (default unchanged; True matches .py/.pyi/.pyw case-insensitively). Opted in: unsafe_deserialization, predictable_tmp_path, systemexit_in_library, swallowed_exceptions, naive_utcnow (runtime-hazard gates). Deliberately NOT opted in: module-identity and graph gates (import_cycles, unresolved_imports, private_imports: x.py and x.pyi are one module), api_floor and signature/definition gates (a stub is not runtime code). No consumer checkout holds a .pyi/.pyw/.PY file, so the before/after delta on pcs and pyutilz and the other consumers is zero; tests/test_corpus_include_stubs.py shows each opted-in gate finding a violation in U.PY and w.pyw. |
| 42-F10 | P3 | Finding.key omits the line, so replacing one violation by another with an identical message in the same file passes the count check | WON'T FIX: measured: 42 of 114 consumer baseline keys have a count of 2 or more, but a swap leaves the violation total unchanged and a line or symbol in the key would re-key every consumer baseline; reopen a gate individually when a swap hides a different severity. |
| 42-F11 | P3 | git_listing returns None (silent walk fallback) for dubious ownership, and the walk and git modes disagree on nested repositories | RESOLVED: corpus: raise on dubious ownership, prune nested .git directories in the walk, slice relative paths (audits 42-F11, 48-F3) |
| 43-F1 | P1 | CHANGELOG.md has no heading for any of the six releases v1.23.3 to v1.23.8; their entries sit under 'Unreleased (next: 1.23.3)' and a second bare 'Unreleased' | RESOLVED 9e32bc2: CHANGELOG split by git blame and the first containing tag into 1.23.3, 1.23.4-1.23.7, 1.23.8, 1.23.9 and one Unreleased (next: 1.23.10); the bare ... |
| 43-F2 | P2 | Behaviour change entries that name no action for the consumer (CLAUDE.md requires 'with what to do') | RESOLVED 9e32bc2: all Behaviour change entries from 1.23.0 backfilled with What to do; tests/test_changelog_actions.py enforces it from 1.23.0. DEFERRED: the 11 older ... |
| 43-F3 | P2 | Reusable workflow default refs disagree: black-filtered, lint-advisory and ruff-blocking default to the unpublished tag v1.23.9, gates.yml to v1.23.8; CLAUDE.md says 'the three reusable workflows' | RESOLVED 9e32bc2: defaults of all four workflows back at the last published tag v1.23.9; the test holds the four equal and equal to the newest tag or v<version>; ... |
| 43-F4 | P2 | registry.toml `since` disagrees with the first tag that contains the module for 24 entries | RESOLVED 9e32bc2: eleven since values corrected (README catalogue too), 1.22.3 documented as the first live release in registry.py, tests/test_registry_since.py ... |
| 43-F5 | P2 | CLAUDE.md tells contributors to lint with `ruff@0.16.1` but the pin is 0.16.10 | RESOLVED 9e32bc2: CLAUDE.md points at RUFF_VERSION; tests/test_tool_pin_docs.py compares ruff@X and black==X in CLAUDE.md and the mypy pin in self-ci.yml with ... |
| 43-F6 | P2 | Consumers import underscore names from py_ci_shared, contradicting 'internal, may change in any release' | RESOLVED: Code commit: "Strict private consumers, public names for consumed internals, registry summary test". public code_audit.is_test_file / is_locals_or_globals_call, code_audit.registry.physical_cpu_count / MIN_SCANNERS_PER_WORKER, sql_verify.LITERAL_PERCENT and the module toml_compat, each old underscore name kept as an alias (registry.toml, INTERNAL_MODULES, tests/test_promoted_public_names.py). New consumer_import_census --private-imports report/fail (find_consumer_private_imports, rule consumer-private-import) reports an underscore module or name of py_ci_shared imported by a consumer; consumer-pins.yml runs it over configs/consumers.toml in report mode. Consumer repos were not edited; the CHANGELOG entry lists what each would change. |
| 43-F7 | P3 | Canary directories: 16 gates have no unparsable/ directory, and CLAUDE.md states four are required without naming the exception | RESOLVED 9e32bc2: CLAUDE.md states the parses=False exception and the undecodable-bytes variant; tests/test_canary_unparsable_dirs.py asserts a gate lacking ... |
| 43-F8 | P3 | Registry `summary` is not held to the module docstring: 166 of 242 differ, one is cut off mid code span | RESOLVED: Code commit: "Strict private consumers, public names for consumed internals, registry summary test". tests/test_registry_summaries.py holds every summary to balanced code spans, a capital first letter, no trailing period or dangling word, no entry-function prefix, and at least half of the stems of the docstring's first paragraph. Of the 166 that differed, 21 were real defects or too far from the docstring (ci_workflow_gate cut inside a code span, setup_env cut mid-quote, three starting with an entry-function name, five lowercase starts, paraphrases that shared too few words) and were rewritten from their docstrings by hand; the rest are legitimate shortenings or reorderings and pass the test. README catalogue regenerated from the registry. |
| 44-F1 | P2 | 137 of 164 canaries pin only the default token 'seed': a gate that loses a whole detection branch still passes tests/test_gate_teeth.py | PARTIAL: second pass, commit "Gate teeth: per-rule canary tokens for 14 gates, both ratchet ceilings lowered": 6 thin canaries converted plus 8 custom ones given per-rule tokens, missing violation shapes added to 6 corpora, ceiling 77 -> 71, teeth proven on a scratch src/ copy (fail_open_handlers, validation_errors_echo_input, destructive_tests_throwaway_only, machine_specific_paths). Third pass, commit "Gate teeth: per-branch canary tokens for 16 more gates, default-token ceiling 71 -> 55": 16 more canaries converted (one seeded shape and token per branch), ceiling 71 -> 55, teeth proven on a scratch copy with 7 branch mutations. DEFERRED: the remaining 55 canaries... |
| 44-F2 | P2 | test_a_pattern_that_would_not_compile_is_not_emitted is vacuous: it loops over mutants and asserts nothing when no 'regex widened' mutant exists | RESOLVED 648caff (+d9ae01e): both loops now collect the mutants and assert non-empty before checking; proven: renaming the label to 'regex WIDE' fails the would_not_compile test. The star-signature test exposed that d... |
| 44-F3 | P2 | wallclock_test_waits: the async-sleep regex (text mode) has no test; changing its zero-argument exemption survives | RESOLVED 648caff (+d9ae01e): text-mode parametrized cases for asyncio/trio/anyio sleeps and the zero/no-arg exemptions in an unparsable file; proven: the (?!\s*1?\s*\)) mutant fails 3 tests. |
| 44-F4 | P2 | skip_as_pass: the 'same-indent return/raise ends the block' rule is untested; flipping == to != survives | RESOLVED 648caff (+d9ae01e): same-indent return and deeper-return cases; proven: == to != fails 2 tests. |
| 44-F5 | P2 | advisory_warn: the 'findings are WARNINGS ONLY' banner and the warned flag are untested; inverting the ruff return-code test survives | RESOLVED 648caff (+d9ae01e): exits fixture with per-tool return codes, banner present when ruff or pip-audit exits non-zero, absent otherwise; proven: returncode == 0 mutant fails 2 tests. Line 61 (pip-audit) is cover... |
| 44-F6 | P3 | Four tests assert only 'does not raise' for a registration function: test_double_registration_is_a_noop* never check the option is registered once | RESOLVED 648caff for the four double-registration tests (flag parses True and appears exactly once in the parser). The other flagged tests (test_audit_20261020_subprocess.py:65 and the two mutation_teeth ones via 44-F... |
| 44-F7 | P3 | Several canaries use a weak token that appears in any message about the file or the gate: 'main', 'seed', 'OSError', 'alice', 'limit', 'WHERE', '221' | RESOLVED: commit "Gate teeth: per-rule canary tokens for 14 gates, both ratchet ceilings lowered" replaced all six weak tokens with rule ids or per-shape message fragments; _WEAK_TOKENS_FROZEN is now empty. |
| 44-F8 | P3 | cli_flag_spelling: the long-option length boundary is unpinned | RESOLVED 648caff (+d9ae01e): the audit's example does not work (the gate splits only on - versus _, never on case, so --n/--N never reports). The boundary is pinned with --_ x2 against --- x1, the shortest splittable ... |
| 44-F9 | P3 | tool_versions: a bump of a pinned constant is invisible to its own test file, and the two-week-age rule in its comment has no test (equivalent mutation, noted for scope) | WON'T FIX: the mutations are equivalent (a consistent bump is legitimate and is checked against the workflows by test_pinned_tool_versions.py). The two-week age rule is review-only; enforcing it needs a release-date s... |
| 45-F1 | P0 | Finding messages built from ast.unparse are baseline keys, and ast.unparse output differs between Python 3.9 and 3.11+ (lambda with no args, parenthesised for-targets, star-subscripts) | RESOLVED in 10a487c |
| 45-F2 | P0 | resource_leak_checks._imported_library uses sys.stdlib_module_names with an empty fallback, so on 3.9 every stdlib first-import counts as 'a library was imported' and suppresses real leak findings | RESOLVED in 10a487c |
| 45-F3 | P1 | near_duplicate_function_body tokenises ast.dump output, so the SequenceMatcher ratio against fixed 0.99/0.98 thresholds depends on the interpreter | RESOLVED in 10a487c |
| 45-F4 | P1 | self-ci matrix has no Python 3.14 leg anywhere and the Windows 3.9 / 3.13 legs are non-blocking; macOS and Windows run 3.11 only | RESOLVED in 10a487c |
| 45-F5 | P2 | No cross-version equivalence test: nothing runs the same gate fixtures on two interpreters and compares keys, so every unparse/dump/stdlib-names divergence is found by accident | RESOLVED in aec2d6a |
| 45-F6 | P3 | atomic_write_text and install_safe_hook do os.replace with no retry on Windows PermissionError, while _mutation_runner.rmtree has a 3-attempt retry | RESOLVED in aec2d6a |
| 45-F7 | P3 | Stale 'supports 3.8' comments and dead ast.unparse/hasattr fallbacks contradict requires-python >= 3.9 | RESOLVED in aec2d6a |
| 45-F8 | P3 | Checked and found robust (recorded to prevent re-audit): CRLF text gates, bash on Windows, tomllib fallback, subprocess executable names, committed line endings | WON'T FIX (no defect, recorded as robust) |
| 46-F1 | P1 | release_guard ci-green with an abbreviated SHA reports "self-ci never ran" for a commit whose run succeeded or is queued | RESOLVED 9e32bc2: --sha must be 40 lowercase hex (exit 2 with the reason); the test with a 7-character SHA failed before the fix and passes after. |
| 46-F2 | P1 | A newer push cancels the queued self-ci of the previous commit; a release commit can be left with only a cancelled run, and ci-green then fails with no recovery short of a new commit | RESOLVED 9e32bc2: self-ci push runs grouped per commit (github.sha), workflow_dispatch added, ci-green names the recovery command when only cancelled runs exist; ... |
| 46-F3 | P1 | A failing preview leg (continue-on-error job) leaves the run green: Windows 3.9 was red for days and every reader of the signal (ci-green, ci-health, run list) saw success | RESOLVED 9e32bc2: preview-report job (needs test, if always) lists failed legs in the step summary and as warnings; ci-green prints failed jobs of a green run; a test ... |
| 46-F4 | P1 | py-ci-shared's own scheduled workflows are watched by nothing: corpus-drift succeeded 2 times in its last 51 runs, and ci-health ignores cancelled runs and this repo | RESOLVED 9e32bc2: ci_health treats 3 consecutive cancelled scheduled runs as red; configs/self_watch.toml plus a self-watch step in ci-health.yml watches this repo; ... |
| 46-F5 | P2 | release.yml waits at most 1800 s for self-ci, but a self-ci run can take 2 hours of queue plus run, so a release fails 'still running' | RESOLVED 9e32bc2: release verify waits 6000 s with a 120-minute job timeout (tested). DEFERRED: reducing the 11 integration job fan-out and a distinct pending exit code. |
| 46-F6 | P2 | Between the 'Bump the next release' commit and the tag, master's reusable workflows default py-ci-shared-ref to a tag that does not exist; a consumer pinned to such a SHA fails at install, by design without fallback | RESOLVED 9e32bc2: same fix as 43-F3 (defaults stay at the last published tag until prepare; only the prepared commit names the unpublished tag, a window CLAUDE.md ... |
| 46-F7 | P2 | gates.yml's py-ci-shared-ref default is tied to the version by no test, and `prepare --check` is run by no CI job: HEAD has three workflows at 1.23.9 and gates.yml at 1.23.8 | RESOLVED 9e32bc2: test parametrised over all four workflows, prepare --check added to the release.yml verify job, three fixed to four in CLAUDE.md and the guard ... |
| 46-F8 | P2 | CONSUMER_READ_TOKEN is not set: consumer-pins has been red every day checking nothing, ci-health skips private repos with a warning, corpus-drift has no strict flag | RESOLVED in code, owner action pending: Code commit: "Strict private consumers, public names for consumed internals, registry summary test". ci_health --fail-on-skipped-private writes a RED row (NOT CHECKED), an ::error:: annotation and exit 1 for a private repo skipped for want of CONSUMER_READ_TOKEN; ci-health.yml and corpus-drift.yml pass it like consumer-pins.yml, and a manual run can set allow-skipped-private. Public consumers are still checked out and scanned without the token. The owner created the fine-grained secret CONSUMER_READ_TOKEN on 2026-11-07 at 12:22 UTC (it did not exist while this was written), and consumer-pins run 42372584759 then cloned all 13 consumers with none skipped, so the strict behaviour is live; the secret missing or expired turns the three scheduled jobs red. |
| 46-F9 | P2 | RELEASE_TOKEN is a repository secret, not a `release` environment secret, so the environment's branch/tag policy does not protect it | PARTLY RESOLVED 9e32bc2: test that release.yml is the only workflow naming secrets.RELEASE_TOKEN. DEFERRED: moving the secret into the release environment is an owner ... |
| 46-F10 | P2 | A SHA pin with a `# vX.Y.Z` comment passes ci_pin_version_skew even when the SHA is not that release's commit; three different SHAs claim v1.23.8 | RESOLVED in the commit "adoption_matrix: report a pin whose # vX.Y.Z comment is not that release's commit (ci-pin-comment-not-release)": a Pin carries its trailing comment and adoption_matrix with --resolve-in reports ci-pin-comment-not-release when the tag named in a `# vX.Y.Z` comment is not the pinned commit (or does not exist); without a checkout the comments are not checked and a note says so. Report-only unless --strict-pin-comments, so consumer-pins stays green; tests/test_adoption_pin_comment.py (temp git repos: wrong SHA flagged, tagged SHA and abbreviated tagged SHA clean, comment without a version clean, missing tag flagged). The "master <sha7>" form is not checked. DEFERRED in the consumer repos: mlframe, pyutilz, social, dash_app_core, llm_bench, algopacksimple carry the wrong comments (listed in the CHANGELOG entry). |
| 46-F11 | P3 | ci_verdict accepts any success run for the SHA of any event and branch, including a pull_request run that tested the merge ref | RESOLVED 9e32bc2: ci-green queries branch=master (the event filter is left open so a workflow_dispatch run on master counts). |
| 46-F12 | P3 | rollback accepts any existing vX.Y.Z tag, including one whose Release run failed and was never published | RESOLVED 9e32bc2: rollback --repo requires a GitHub release for the target and lists the newest published ones (release.yml passes the repo). The age warning was not ... |
| 46-F13 | P3 | install-command inputs are expanded into `run:` with ${{ }} in docs.yml, mypy-full.yml and lint-blocking.yml (zizmor template-injection, suppressed) | RESOLVED 9e32bc2: docs.yml, mypy-full.yml and lint-blocking.yml pass the commands through env and run bash -eo pipefail -c; suppressions removed; ... |
| 46-F14 | P3 | No ruleset or branch protection on master and no SHA-pin enforcement: red pushes reach master and any action version can be introduced | WON'T FIX in code: repository settings (ruleset, SHA pinning enforcement) are an owner action; waits on the owner. |
| 47-F1 | P1 | Three fixed-pin consumers are 9-12 releases behind and two more 3, against a tolerance of 2 | PARTLY RESOLVED 9e32bc2: adoption_matrix --waivers with configs/pin_waivers.toml (dated, per consumer, oldest tag allowed; an expired waiver fails again), wired into ... |
| 47-F2 | P2 | Pin comments say `# v1.23.8` on SHAs that are not the v1.23.8 commit; four different SHAs carry that label | RESOLVED in the commit "adoption_matrix: report a pin whose # vX.Y.Z comment is not that release's commit (ci-pin-comment-not-release)": same rule as 46-F10, named ci-pin-comment-not-release (not pin-comment-mismatch). Correcting the wrong comments is work in the consumer repos (mlframe, pyutilz, social, dash_app_core, llm_bench, algopacksimple); glossum_backend_scripts pins a commit-subject comment and is not flagged. |
| 47-F3 | P2 | 43 of 67 'Behaviour change' mentions carry no instruction to the consumer, and no test enforces the rule | RESOLVED 9e32bc2: same fix as 43-F2 (test plus backfill); the clause is spelled What to do:. |
| 47-F4 | P2 | `more findings` behaviour changes ship in the patch/minor line the @v1 promise limits to additive and stricter-defect changes, with no consumer impact count | WON'T FIX: requiring per-consumer hit counts from corpus-drift in every more-findings entry cannot be checked offline, and corpus-drift has been dead (46-F4); revisit ... |
| 47-F5 | P3 | consumers.toml marks only 3 of 13 consumers as corpus; adoption of the newest gates is unmeasured for the rest | RESOLVED: Code commit: "Strict private consumers, public names for consumed internals, registry summary test". glossum_backend_scripts and algopacksimple are corpus = true (the owner decided the nightly cost is worth it); each of the eight other consumers has a one-line reason in configs/consumers.toml (measured size and shape: autopsia about 2400 python files, social about 2200 files / 490k lines, dash_app_core, flutter_*, polyvocab_app, noema_app and claude-usage-notifier too small or mostly Dart/TypeScript). corpus-drift with no token still scans the public repos; with the strict flag it fails loudly for the private ones. |
| 48-F1 | P1 | gate_cache.tree_digest hashes the whole repo root including .claude/worktrees; a cache lookup costs 25.7 s to 65 s, 30 to 80 times a gate's own run | OPEN |
| 48-F2 | P2 | tree_digest reads every file's bytes into the process-wide bytes cache: 199.6 MB resident for a 31k-file root, evicting nothing but doubling gate RSS | WON'T FIX |
| 48-F3 | P2 | Large consumer corpora: 34x RSS amplification and 10 to 13 s per warm scan_python call (mlframe, 8,767 files) | RESOLVED: path part of the warm scan -35 to -41 percent CPU on mlframe (corpus: raise on dubious ownership, prune nested .git directories in the walk, slice relative paths (audits 42-F11, 48-F3)); tree/index memory and listing memoisation WON'T FIX with measurements (AST is 88 percent of memory, index 18 percent) |
| 48-F4 | P2 | node_index.child_nodes is not memoised: 10.4 M calls and 126.9 M isinstance calls are 19 percent of a run-all profile | WON'T FIX |
| 48-F5 | P2 | corpus.iter_files resolves every path twice per scan: 62 percent of a warm scan_python is path arithmetic, 24k GetFinalPathNameByHandle calls per run-all | RESOLVED |
| 48-F6 | P3 | Repeated parsing is mostly solved (86 percent hit rate) but 412 extra ast.parse calls remain from short-text parsing and non-cached entry points | WON'T FIX |
| 48-F7 | P3 | git ls-files is spawned 41 times (plus 7 other git calls) per run-all with the listing deliberately uncached | WON'T FIX |
| 48-F8 | P3 | Pre-push hook: stale cost comment, self-gate duplication, and a half-of-hook cost concentrated in test_self_gates | OPEN |
| 48-F9 | P3 | Gate budgets are wall-clock on shared machines and produce BUDGET warnings that measure load, not the gate | OPEN |
| 48-F10 | P3 | Mutation harness: per-mutant cost is dominated by the serial pytest run; candidate generation is superlinear in file size | OPEN |
| 48-F11 | P3 | Per-repo gate_cache store files are never pruned and the 500-key cap holds only about nine tree versions | WON'T FIX |
| 49-F1 | P1 | import_cycles run as a script prints findings and exits 0; a missing root is a bare traceback | RESOLVED 5ce4d25: import_cycles.main returns 0/1/2, repo-relative paths, tests/test_diagnostics_exit_codes.py (failed before the fix). |
| 49-F2 | P1 | Missing tool is reported as 'findings above are WARNINGS' by bandit_warn/advisory_warn/format_warn | RESOLVED 5ce4d25: _tool_installed (find_spec) probe in bandit_warn, advisory_warn, format_warn prints 'skipped: <tool> is not installed'; exit stays 0; real findings keep the WARNINGS ONLY banner. |
| 49-F3 | P2 | Gate crash (ERROR) shows `Type: message` only: no traceback, no file:line, no fix hint | RESOLVED 0cb41d8: run_gate checks required parameters first and raises a ConfigError naming [tool.py_ci_shared.gates.X] and the missing arguments (ERROR, exit 2 as before); other crashes append '(at file:line; PY_CI_SHARED_TRACEBACK=1 prints the traceback)' and the env switch prints the full traceback. tests/test_diagnostics_runner_outcomes.py::TestF3CrashDiagnostics. |
| 49-F4 | P2 | Wrong fix hint under the CLI: missing/stale baseline messages tell the user to pass a pytest flag | RESOLVED 0cb41d8: run-all/run/refresh set PY_CI_SHARED_CLI=1; Baseline.refresh_text and _core.outcome.refresh_hint print `py-ci-shared refresh <gate>` (and the PY_CI_SHARED_REFRESH form) under the CLI and the pytest flag under pytest; applied in _core Baseline (covers every gate that passes a pytest-form refresh_command) and in function_length, function_complexity, complexity_ratchet, fail_open_handlers, import_side_effects, including the stale-entry messages. A custom refresh_command (baseline_ratchet) is left as given. Messages of gates with their own wording elsewhere (code_audit_meta, db_transaction prose) not individually changed. |
| 49-F5 | P2 | `refresh` reports a successful baseline rewrite as `SKIP` and says nothing about what changed | RESOLVED 0cb41d8: new REFRESHED status (_core/outcome.refreshed_skip, still a pytest skip under pytest) in 15 gate/_core sites; the CLI prints `REFRESH <gate>: +N -M entries (file)` from tracked atomic_write_text writes and a `, N refreshed` summary count; exit code unchanged. A baseline written without atomic_write_text prints the gate's own message without counts. |
| 49-F6 | P2 | Floor/min-count failures name the symptom but not the config key that fixes it | RESOLVED 5ce4d25: function_length floor message names min_functions in its gate table; EmptyScanError names root=/files=/min_files=. Other floor messages not individually changed. |
| 49-F7 | P2 | Gates that cannot evaluate their input return SKIP (exit 0) with a one-line reason | RESOLVED 0cb41d8: owner decision applied: opt-in `skip = "fail"` in [tool.py_ci_shared] (default "ok", green consumers unchanged) turns a skip into FAILED (exit 1); every skip prints `skipped: <reason>` and emits a ::warning:: under GITHUB_ACTIONS; the 'cannot judge its input' skips (changelog_promise_parity x2, import_side_effects x2, sibling_floor_skew, tracked_secret_shapes) use unevaluated_skip. require_section default not changed. |
| 49-F8 | P3 | Config error printed twice, with a 'substituted a fallback' log whose line number is stale in 10 of 13 sites | RESOLVED 0cb41d8: the doubled cli.py logger.warning is now logger.debug (the handler prints its own message); the stale 'x.py line N' prefix is removed from all 13 sites; tests/test_diagnostics_runner_outcomes.py::TestF8StaleLineNumbers scans src for a literal 'x.py line N:'. |
| 49-F9 | P3 | `run-all` writes BUDGET to stderr and results to stdout; a cached PASS is counted as 'passed' with no cached tally | RESOLVED 0cb41d8: BUDGET row printed to stdout under its gate; summary line appends ' (N cached)' (and ', N refreshed') only when non-zero so the greppable head is unchanged. |
| 49-F10 | P3 | `workflow_runner_labels` main says nothing on success or when zero workflow files were scanned | RESOLVED 5ce4d25: summary line printed, exit 2 on zero workflow files unless --allow-empty (CHANGELOG Behaviour change). |
| 49-F11 | P3 | mutation_teeth truncated/cached runs pass with a warnings.warn only | RESOLVED 0cb41d8: PASS carries a note (_core.outcome.add_note, GateResult.note, printed as `note:` and as ::warning:: under Actions); mutation_teeth adds it for truncated/cached runs. WON'T FIX: defaulting fail_on_truncation for CLI-enabled runs (a deliberate limit is legitimate; the note makes it visible). Verified against a stubbed run, not the real mutation harness. |
| 49-F12 | P3 | teeth_sweep: unreadable --cases file is a traceback with exit 1, the 'toothless' exit code | RESOLVED 5ce4d25: unreadable/malformed --cases and empty list return 2 before the baseline suite; unit tests with the suite stubbed. |
| 50-F1 | P1 | possibly_unbound_local: a local bound only inside one branch of an if/try and read after it (UnboundLocalError on the cached or second call) | RESOLVED b352327: new gate possibly_unbound_local (AST definite-assignment pass, rules possibly-unbound-after-if/-try/-match; the mlframe 169f17000 Muon dispatcher shape is the failing-first test). Measured: pyutilz/src 22, glossum_backend_scripts 44, algopacksimple 25, mlframe/src 281, llm_bench/src 0; 0 on this repo after one marker. Not delegated to mypy possibly-undefined: no mypy run, no third-party stubs, baseline-able. |
| 50-F2 | P2 | nested_numba_parallel_guard: promote mlframe's test_no_unguarded_nested_parallel (a parallel=True/prange kernel reachable from a thread pool must pass one process-wide lock) | RESOLVED b352327: new gate nested_numba_parallel_guard (name-based call graph, 6 hops, dispatch tables, rules nested-parallel-via-executor/-thread/-joblib, configurable guard_name, marker # parallel-guarded:). Measured: mlframe/src 8, pyutilz/src 0, glossum_backend_scripts 0, algopacksimple 0, llm_bench/src 0. |
| 50-F3 | P2 | uv_lock_parity: uv.lock must agree with pyproject.toml (requires-dist names and specifiers) without network | RESOLVED b352327: new gate uv_lock_parity (offline TOML comparison of the root package's requires-dist/requires-dev and requires-python; 5 rules). Measured: mlframe 0, glossum_backend_scripts 0; pyutilz, llm_bench, algopacksimple have no uv.lock. The optional `uv lock --check --offline` subprocess was not added: it needs uv on PATH and the network-free mode is not reliable across uv versions; CI keeps running it. |
| 50-F4 | P3 | nan_admitted_by_reject_guard: `if loss >= worst: continue` used to keep a top-N admits NaN, because NaN fails the comparison | RESOLVED b352327: new gate nan_admitted_by_reject_guard (rules nan-admitted-by-loop-reject/-return-reject; float evidence in the function and an earlier isnan/isfinite on the name keep it quiet). Measured: mlframe/src 1 (of the 4 regex hits only one is in a function with float evidence), pyutilz/src 0, glossum_backend_scripts 0, algopacksimple 0, llm_bench/src 0. |
| 50-F5 | P3 | instance_clear_leaves_module_cache: a clear/reset/release method touches only self state while sibling methods use a module-level cache container | RESOLVED b352327: new gate instance_clear_leaves_module_cache (rules instance-clear-/instance-release-leaves-module-cache; only cache-named module containers count, so the pyutilz warn-once set is not matched). Measured: 0 in pyutilz, mlframe, glossum_backend_scripts, algopacksimple, llm_bench. |
| 50-F6 | P3 | tool_config_env_vars_set_in_every_tool_job: a variable a tool config reads ($VAR in [tool.ruff] extend) must be defined in every CI job that runs the tool | RESOLVED b352327: new gate tool_config_env_vars_set_in_every_tool_job (direct form tool-env-var-unset-in-job and the indirect pytest-over-a-shelling-out-test form tool-env-var-unset-in-test-job, with the pytest path arguments narrowing the tree). Measured: glossum_backend_scripts 2 (ci.yml test, nightly-thorough.yml property-tests), pyutilz 2, algopacksimple 0, llm_bench 0; mlframe was not measured to completion (its tests tree is very large; the run was still going when this was written). |
| 50-F7 | P3 | Runtime harness: launch-time kernel argument dtype audit promoted from mlframe, for templated kernels the static cuda_kernel_pointer_types gate cannot see | RESOLVED in the commit 'Add njit_twin_untested, njit_twin_parity and launch_dtype_audit (audit 50-F7, 50-F8)': new library module launch_dtype_audit (kernel_parameters with {PLACEHOLDER} substitution, check_launch_args, assert_launch_dtypes, audited_kernel, audit_cupy, python -m entry) promoted from mlframe kernel_arg_dtype_audit. Duck-typed arguments and a lazy cupy import make it testable on a CPU-only runner with fake kernels and arrays (tests/test_launch_dtype_audit.py, plus a numpy-gated real-array case), so the earlier premise that it only skips without a GPU leg was wrong for the helper itself. No GPU CI leg is added: every verdict is plain Python over signature text and dtype names; a consumer with a GPU job wraps its real RawKernel with audited_kernel there. Hit count: RawKernel/RawModule/ElementwiseKernel users mlframe 105 files, pyutilz 2, glossum 0 are the population the helper serves; it is runtime, so it has no finding count. |
| 50-F8 | P3 | Runtime harness: pure-Python fallback vs njit twin parity (same arithmetic order, bit-identical on the same input) | RESOLVED in the commit 'Add njit_twin_untested, njit_twin_parity and launch_dtype_audit (audit 50-F7, 50-F8)': (a) njit_twin_parity.assert_njit_twin_parity (py_func or twin= on seeded inputs, bitwise by default, NaN-aware, nested results and mutated output buffers, clear failure text, clean skip without numba; numba added to the dev extra so the real-dispatcher tests run in CI) and (b) static gate njit_twin_untested limited to code that shows a twin: njit-flag-fallback-untested, njit-twin-name-untested, njit-redefined-twin-untested, njit-parity-marker-untested; evidence is py_func/helper use, flag toggling by name, or a test naming both twins. Measured obligations / unmet: mlframe 82 / 55 (43 flag, 4 name, 8 redefined; 27 flag dispatchers already tested), pyutilz 1 / 0, algopacksimple 0 / 0, llm_bench 0 / 0 (no njit); mlframe has 1065 kernel names, so the 294-file inventory was the wrong scope and 55 is a baselinable number. 0 on this repo. The earlier 'ten-line assert' premise missed output buffers, NaN, nested results and the flag-dispatcher fallback shape (mlframe 1b56cfb90), which is not a py_func at all. |
| 51-F1 | P2 | config_drift_check._fetch_pyproject sends a Bearer token with urllib's default redirect handler | RESOLVED |
| 51-F2 | P2 | lint-blocking tool pins fall back to unpinned uvx/pip when the PIN input is empty | RESOLVED |
| 51-F3 | P3 | install-pyutilz passes the ref to git fetch/checkout without the shape guard the other workflows use | RESOLVED |
| 51-F4 | P3 | pyutilz cloned from the default branch and not verified against a full SHA | RESOLVED |
| 51-F5 | P3 | Direct ${{ inputs.* }} substitution into run: in docs.yml, mypy-full.yml, lint-blocking.yml | RESOLVED |
| 51-F6 | P3 | github.action_path interpolated inside a run: script | RESOLVED |
| 51-F7 | P3 | tarfile.extractall without a filter on Python < 3.12 | RESOLVED |
| 51-F8 | P3 | release trust depends on repository settings the code cannot verify | WON'T FIX (owner decision 2026-11-07): verified with the API that RELEASE_TOKEN is an environment secret with a master/v* deployment policy; only a required reviewer is not configured, accepted and documented in CLAUDE.md |
| 51-F9 | P3 | Verified clean record: no pull_request_target, actions SHA-pinned, least-privilege permissions, safe deserialization | WON'T FIX: verified-clean record, no defect |
| 52-F1 | P2 | 22 private functions in src are defined and never called or referenced | RESOLVED |
| 52-F2 | P2 | An identical helper is copied 10 times and 7 more duplicate groups drift; the package's own drift gate is not enabled on itself | RESOLVED |
| 52-F3 | P2 | 158 of 201 gates are not enabled on the package itself, with a recorded reason for only one | RESOLVED (the cheap gates; the rest DEFERRED in the finding) |
| 52-F4 | P3 | uncalled_functions is disabled for the whole tree although a private-only scope has no public-API objection | RESOLVED |
| 52-F5 | P3 | loc_budget covers src only; one test module exceeds the 1000-LOC budget with no baseline entry and another is at 953 | RESOLVED |
| 52-F6 | P3 | self_api_hygiene baseline carries 265 grandfathered offenders | RESOLVED |
| 52-F7 | P3 | Dead parameters: one keyword always None and one never read | RESOLVED |
| 52-F8 | P3 | Stray tracked file f.txt and an untracked out.txt in the repo root | RESOLVED |
| 52-F9 | P3 | unread_function_params is enabled on self with root=tests, takes 72.9s and trips its own 30s budget | RESOLVED |
| 52-F10 | P3 | Two suppression markers stacked on one line | WON'T FIX |
