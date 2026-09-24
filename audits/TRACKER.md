# Findings tracker

One row per finding, all rounds. The disposition and its reasoning live in the finding itself; this
table exists so the state of a round is readable without opening five files.

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
| **DEFERRED** | High | `ARCH-2` | Consumer pinning is inconsistent: README policy is the moving @v1 tag; pyutilz uses @v1... |
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
| **OPEN** | High | `ADOPT-1` | CI installs .[test,llm] only; py-ci-shared is in [dev], so it is never installed |
| **OPEN** | High | `ADOPT-2` | [dev] lists bare "py-ci-shared" with no direct URL |
| **OPEN** | High | `ADOPT-3` | py-ci-shared is not a dependency, yet tests/test_meta/test_no_top_level_side_effects.py... |
| **OPEN** | High | `ADOPT-4` | On ImportError these gates print "SKIPPED" and sys.exit(0): a missing install reads as... |
| **OPEN** | High | `ADOPT-5` | _SHARED_AVAILABLE=False on ImportError silently drops 7 Dart scans from SCANS; the ratc... |
| **OPEN** | Med | `ADOPT-6` | collect_ignore of the meta tests whenever import py_ci_shared fails |
| **OPEN** | Med | `ADOPT-7` | pytest.importorskip("py_ci_shared..."): whole gate files turn SKIPPED when the package... |
| **OPEN** | Med | `ADOPT-8` | Black-pin cross-check reads ../py-ci-shared/.github/workflows/black-filtered.yml from a... |
| **OPEN** | Med | `ADOPT-9` | mypy_gate --min-files 150 dashboard \/\/ true: result and population floor both discard... |
| **OPEN** | Med | `ADOPT-10` | mypy_gate --min-files floors hand-set per repo with no recorded measurement; mlframe an... |
| **OPEN** | Med | `ADOPT-11` | One job installs the package at a49421c (152 behind) but clones unpinned master for con... |
| **OPEN** | Med | `ADOPT-12` | CI installs @v1 (moving tag) while declared deps pin f103a36: local and CI run differen... |
| **OPEN** | Med | `ADOPT-13` | Five different py-ci-shared SHAs in one repo (41cbadc, 64e2b6b, 915217a, 7195776, f5028... |
| **OPEN** | Med | `ADOPT-14` | Two versions of the same upload-codecov action in one repo. |
| **OPEN** | Med | `ADOPT-15` | Gate package floats on master while workflows are ~165-173 commits old; tool_versions.R... |
| **OPEN** | Med | `ADOPT-16` | Pinned to v1.2.0 (~173 behind); no meta-test gates at all, only the three lint workflows. |
| **OPEN** | Med | `ADOPT-17` | Unpinned git installs of master: any py-ci-shared push can break or silently change the... |
| **OPEN** | Low | `ADOPT-18` | The moving tag v1 is 20 commits behind master, so @v1 repos run older workflows than SH... |
| **OPEN** | Low | `ADOPT-19` | Local clone 4 commits behind origin/master; hooks in repos that use an editable sibling... |
| **OPEN** | Low | `ADOPT-20` | extend = "../py-ci-shared/configs/ruff-base.toml" sibling path; ruff errors on any mach... |
| **OPEN** | Low | `ADOPT-21` | Hook id pinned-tool-versions (python -m py_ci_shared.pinned_tool_versions) defined twic... |
| **OPEN** | Med | `ADOPT-22` | Local copy of py_ci_shared.index_coverage (same class and 9 functions, cosmetic diffs o... |
| **OPEN** | Med | `ADOPT-23` | Hand-copied scanner module, already drifted: flutter_app_core's _IMPORT regex (~line 29... |
| **OPEN** | Med | `ADOPT-24` | Same bug classes implemented twice with no cross-reference: additive_epsilon_denominato... |
| **OPEN** | Med | `ADOPT-25` | Modules adopted by no consumer: baseline_trend, checkpoint_isolation, config_drift_chec... |
| **OPEN** | Med | `ADOPT-26` | 28 modules with exactly one consumer; generic ones worth rolling out: fail_open_handler... |
| **OPEN** | Med | `ADOPT-27` | audit_round_format (required in every Python project) missing in pyutilz, llm_bench, da... |
| **OPEN** | Low | `ADOPT-28` | autopsia calls lint-advisory but not lint-blocking; llm_bench and social call neither. |
| **OPEN** | Low | `ADOPT-29` | Stale second clone of autopsia (2026-09-08, 15 days behind Redline/autopsia); greps and... |
| **OPEN** | Info | `ADOPT-30` | Jobs are not starting: "recent account payments have failed or your spending limit need... |
| **RESOLVED** | High | `SUITE-1` | CI tests only Python 3.11 while requires-python = ">=3.9" |
| **RESOLVED** | High | `SUITE-2` | The floor-guard test itself does import tomllib at module level, so it fails at collect... |
| **RESOLVED** | Med | `SUITE-3` | The tomllib ban matches only the exact line import tomllib; import tomllib as t and fro... |
| **DEFERRED** | High | `SUITE-4` | There is no coverage measurement, no fail_under and no pytest config (no testpaths, -ra... |
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
| **DEFERRED** | High | `NEW-2` | gate `local_copy_report` (migrate locals onto existing central gates) |
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
| **OPEN** | Med | `INFRA-6` | Consumer adoption matrix |
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
