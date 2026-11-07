# 48 Performance (round 2026-11-07)

Read-only. Machine: 22 logical CPUs, LoadPercentage 100 on every sample, 51 to 71 python processes from other sessions. Timings were
repeated where cheap; loaded and quiet numbers are both given and labelled. Python 3.14.3, Windows 11. Findings are in
`48_performance.json` (same ids); this file holds the evidence narrative. Dispositions are recorded per finding below.

## Measurements

| What | Loaded | Quieter |
|---|---|---|
| `run-all` on py-ci-shared, cache off, 57 gates | 176 s, 253 s, 419 s (cProfile) | 108.9 s (gate sum 101.5 s) |
| Median gate / gates over 1 s | | 0.79 s / 27 of 57 |
| Slowest gates (quiet) | | predictable_tmp_path 12.05, standard_stream_restore 9.26, wallclock_test_waits 7.80, unresolved_imports 6.12 |
| `tree_digest` of repo root (cache key input) | 62.3 s, 65.6 s, 29.8 s | 25.7 s |
| Same digest with `.claude` skipped | | 0.79 s |
| `import py_ci_shared.cli` | 0.75 to 1.03 s (bare python 0.34 to 0.73) | |
| scan_python pyutilz (616 files, 6.4 MB): cold / warm | 11.57 s / 0.70 s | 3.41 s / 0.16 to 0.22 s |
| scan_python py-ci-shared (835 files): cold / warm | 18.04 s / 1.57 to 2.35 s | |
| scan_python mlframe (8,767 files, 74 MB): cold / warm | 173.5 s / 9.95 to 13.26 s, RSS +2.5 GB | |

Import time is not a finding: the CLI adds about 0.4 s over bare Python, mostly stdlib (logging, traceback, typing, inspect).

## Pre-push hook, one file at a time (`tool pytest_run`, `--durations`)

| File | Tests | Time |
|---|---|---|
| test_package_inventory | 519 | 7.51 s |
| test_release_version | 39 | 9.21 s (two CLI tests 3.26 s + 2.13 s) |
| test_pytest_markers | 15 | 6.29 s |
| test_complexity_ratchet | 22 | 13.27 s (`test_parity_with_ruff_on_this_package` 9.47 s) |
| test_gate_teeth | 959 | 64.12 s, flat (largest test 1.05 s) |
| test_self_gates | 58 (57 passed) | 102.08 s (first gate test pays the tree digest: repo_hygiene 21.53 s vs 6.4 s in run-all) |

Serial sum 202 s, 1,612 tests; the hook comment says about 4 minutes and 1,075 tests. The single failure in test_self_gates
(`audit_rounds_format`) was another session's half-written `50_new_gate_mining.md`, not a defect of this round.

## Findings

- 48-F1 P1: the gate cache digest hashes `.claude/worktrees` (28,948 of 31,137 entries, 183 MB) although `corpus.DEFAULT_EXCLUDE`
  and the corpus docstring already say those are full repo copies. A cache lookup costs 25.7 to 65 s; excluding `.claude` costs 0.79 s.
  Three back-to-back single-gate runs took 78.4, 63.8, 64.1 s for a gate that takes 3.6 s.
  Disposition: RESOLVED in the commit carrying this change (SHA in the hand-off report; a commit cannot name itself): _SKIP_DIRS gains '.claude'. Measured on the main checkout (28,948 of 31,155 stat entries under .claude): tree_digest 44.97 s before, 2.53 s after (2,207 entries, 0 under .claude), loaded machine, same session. Test test_the_tree_digest_ignores_agent_worktrees_under_dot_claude failed first (the fake .claude/worktrees tree was listed), passes now. Gate results unchanged by construction: the digest only forms the cache key and no gate reads it. The proposal to derive the file set from corpus.git_listing / DEFAULT_EXCLUDE was not taken because build/ and .benchmarks might hold files a gate reads; only .claude is skipped.
- 48-F2 P2: the digest reads every file into the bytes cache (199.6 MB).
  Disposition: WON'T FIX: Resolved by 48-F1: tree_digest of this repo now leaves 11.8 MB in the bytes cache (was 199.6 MB), 0.8 s warm over 3 runs. The racy-window stat signature would trade cache-key correctness for a saving that no longer exists.
- 48-F3 P2: mlframe scale, 34x RSS per source byte, 10 to 13 s per warm scan, node index +0.56 GB.
  Disposition: RESOLVED: Partly implemented, rest WON'T FIX with measurements. Code commit: "corpus: raise on dubious ownership, prune nested .git directories in the walk, slice relative paths (audits 42-F11, 48-F3)" (relative_posix slices strings instead of Path.relative_to per scanned file). Profiled first: pyutilz (618 files) cold 8 s, RSS +224 MB, warm 0.4 to 0.6 s; tracemalloc: AST nodes 186 of 212 MiB (88 percent), node index plus descendant lists 39 MiB (18 percent), source strings 8 MiB (4 percent). mlframe (8,790 files): cold 109 s warm-disk-cache, RSS 2,533 MB, warm scan cProfile 6.4 s of which Path.relative_to 2.83 s (44 percent). Implemented, because it clears the 25 percent bar: relative_posix fast path, warm scan_python CPU on mlframe (process_time, 15 scans, ran alone) two before/after pairs, minimum 1.438 -> 0.938 s (-35 percent) and 1.281 -> 0.750 s (-41 percent), median 1.688 -> 1.062 s (-37 percent) and 1.547 -> 1.109 s (-28 percent); wall-clock minimum under load 2.03/2.33 s -> 1.70/1.79 s (-16 to -23 percent, loaded machine); rel lists identical (sha256 of all 8,790 relative paths equal before and after), a 300000-case fuzz of the shortcut against Path.relative_to found no difference, skip_as_pass findings on the consumers identical. WON'T FIX (a) listing memoisation: warm listing is not the cost any more (git listing 0.1 to 0.5 s) and there is still no safe invalidation key, see 48-F7. WON'T FIX (b) dropping trees / tree LRU / __slots__ / lazy parent maps: 88 percent of memory is the AST itself, which CPython owns (no __slots__ for ast nodes); the whole node index plus descendant lists is 18 percent, so removing all of it saves less than the 25 percent bar and costs the warm time it caches; dropping trees forces the 109 to 173 s re-parse. Bytes cache and source strings are 4 percent. WON'T FIX (c) RSS in GateResult: a reporting feature, not a perf fix (unchanged from the earlier disposition).
- 48-F4 P2: `child_nodes` unmemoised, 10.4 M calls, 80 s cumulative of 419 s in the profile; `isinstance` 126.9 M calls.
  Disposition: WON'T FIX: Measured upper bound: a per-node children cache makes a full recursion over this repo's 396 trees (680,554 nodes) 0.75 s -> 0.30 s warm but 0.97 s cold (+30 percent first pass). 10.4 M profiled calls are about 15 passes, so the saving is about 6.8 s of 72 to 83 s (8 to 9 percent) for +42 MB (about 5 bytes of RSS per source byte, about +375 MB on mlframe) that no existing bound covers, and a cached list goes stale if a gate mutates a tree. Below the 10 percent bar and against 48-F3.
- 48-F5 P2: `iter_files` re-resolves paths, 62 percent of a warm scan, 24,424 `_getfinalpathname` calls.
  Disposition: RESOLVED: Code commit: "corpus.iter_files: one lstat per listed file and sort on the POSIX spelling instead of Path.relative_to (audit 48-F5)". On Python 3.14 the profile showed isfile+islink (two stats per file) and Path.relative_to, not resolve(); both removed. iter_files median 0.154 s -> 0.133 s (-14 percent, 25 runs x3, pyutilz 618 files), identical listing. Gate output of all 59 gates byte-identical before/after on pyutilz, noema_app, polyvocab_app, flutter_app_core and this repo. The whole-run effect is about 1 to 2 percent (git ls-files dominates).
- 48-F6 P3: parse cache works (12,752 requests, 1,727 real `ast.parse`, 5.9 s); 412 repeat parses remain, mostly text under 1000 chars.
  Disposition: WON'T FIX: 412 repeat parses cost about 1.4 s of 72 to 109 s (1.3 percent), under the 10 percent bar.
- 48-F7 P3: 41 `git ls-files` spawns per run-all.
  Disposition: WON'T FIX: Measured git ls-files 0.07 s quiet, 0.26 s loaded (x41 = 3 to 11 s, 4 to 14 percent loaded). The untracked part (--others) has no cheap validity key: .git/index mtime does not change when a gate writes a file, and a directory-mtime scan costs about what the listing does. Caching it risks the stale listing the corpus comment warns about; changing no gate result outweighs a loaded-only saving.
- 48-F8 P3: stale hook comment; self gates run twice in CI (pytest step and `run-all (self)` step).
  Disposition: RESOLVED in the commit carrying this change (SHA in the hand-off report) for the stale hook comment: .pre-commit-config.yaml now quotes the 2026-11-07 measurement (1,612 tests, 202 s serial); the first-test digest cost is removed by F1. DEFERRED for the self gates running twice in CI (pytest step and run-all (self) step): that is the CI workflow, owned by the audit 43/46/47 agent.
- 48-F9 P3: wall-clock budgets measure load (repo_hygiene 6.4 s then 50.3 s).
  Disposition: RESOLVED in the commit carrying this change (SHA in the hand-off report) for the standing BUDGET line: unread_function_params and complexity_ratchet get budget_s = 120 in pyproject with the measured 73 s and 50 s. DEFERRED for reporting CPU time next to wall time (a runner change; wall-clock budgets still measure machine load).
- 48-F10 P3: mutation harness; generation 1.6 to 26.7 ms per mutant, serial test runs dominate (not run, out of scope).
  Disposition: WON'T FIX: the audit itself finds generation is 1 to 2 percent of a mutation run and recommends no change; coverage-based test selection is a separate feature.
- 48-F11 P3: 151 gate-cache files never pruned; 500-key cap holds about nine tree versions.
  Disposition: WON'T FIX: Measured: reading the largest store file (34,080 bytes) takes 0.3 ms, 57 reads = 17 ms per run-all; disk use is 428 KB. After 48-F1 the tree digest changes far less often, so 500 keys hold many more versions. A store-format change for no measurable gain.

## Caveats

mlframe numbers are a single loaded measurement (about 10 minutes per pass), not repeated. The cProfile run inflates absolute times
roughly 2 to 4x; its call counts are exact. Nothing in the package or any consumer was edited; the only writes are this file, its
JSON, and scratch files in the session scratchpad (plus the tool's own gate-cache entries under `~/.cache/py_ci_shared`).
