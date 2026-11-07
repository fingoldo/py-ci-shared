# 48 Performance (round 2026-11-07)

Read-only. Machine: 22 logical CPUs, LoadPercentage 100 on every sample, 51 to 71 python processes from other sessions. Timings were
repeated where cheap; loaded and quiet numbers are both given and labelled. Python 3.14.3, Windows 11. Findings are in
`48_performance.json` (same ids); this file holds the evidence narrative. All dispositions OPEN.

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
- 48-F2 P2: the digest reads every file into the bytes cache (199.6 MB).
- 48-F3 P2: mlframe scale, 34x RSS per source byte, 10 to 13 s per warm scan, node index +0.56 GB.
- 48-F4 P2: `child_nodes` unmemoised, 10.4 M calls, 80 s cumulative of 419 s in the profile; `isinstance` 126.9 M calls.
- 48-F5 P2: `iter_files` re-resolves paths, 62 percent of a warm scan, 24,424 `_getfinalpathname` calls.
- 48-F6 P3: parse cache works (12,752 requests, 1,727 real `ast.parse`, 5.9 s); 412 repeat parses remain, mostly text under 1000 chars.
- 48-F7 P3: 41 `git ls-files` spawns per run-all.
- 48-F8 P3: stale hook comment; self gates run twice in CI (pytest step and `run-all (self)` step).
- 48-F9 P3: wall-clock budgets measure load (repo_hygiene 6.4 s then 50.3 s).
- 48-F10 P3: mutation harness; generation 1.6 to 26.7 ms per mutant, serial test runs dominate (not run, out of scope).
- 48-F11 P3: 151 gate-cache files never pruned; 500-key cap holds about nine tree versions.

## Caveats

mlframe numbers are a single loaded measurement (about 10 minutes per pass), not repeated. The cProfile run inflates absolute times
roughly 2 to 4x; its call counts are exact. Nothing in the package or any consumer was edited; the only writes are this file, its
JSON, and scratch files in the session scratchpad (plus the tool's own gate-cache entries under `~/.cache/py_ci_shared`).
