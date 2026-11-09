# 48 Performance (round 2026-11-10)

Read-only. Source and tests were not edited. Profiling ran on a scratch copy of `src/`, `tests/`, `pyproject.toml` and
`configs/` under the session scratchpad. Machine: Windows 11, Python 3.14, loaded (other sessions running), so every
wall-clock number below is a loaded measurement unless labelled otherwise. Ids 48-F1 to 48-F11 belong to the 2026-11-07
round and are not re-reported; this round adds 48-F12 onward. The full local suite (about 11,500 tests) was not run; the
work was sampled by collection counts, per-file timings and call-level profiles.

## Scope and method

- Collection: 11,494 tests collected in the scratch copy (3 collection errors there, all from files that need `.github/`
  which the copy does not carry; not a finding).
- Largest files by collected tests: `test_python_floor_compatibility.py` 1,193; `test_gate_teeth.py` 1,003 (already measured
  at 64 s in 2026-11-07); `test_package_inventory.py` 546; `test_registry_summaries.py` 261.
- Per-file timings: `test_stale_comment_age.py` 70 tests in 94 s; `test_python_floor_compatibility.py` 1,193 tests in 21.8 s.
- Spawn and loop counting: `subprocess.Popen` and `run_git` calls counted with monkeypatches in a driver script.

## Measurements

| What | Measured | Note |
|---|---|---|
| `git rev-parse` spawn, 20 runs | 86 ms each | loaded |
| `git show <sha>:<path>` spawn, 20 runs | 98 ms each | loaded |
| `baseline_trend.history` on `audits/TRACKER.md` (74 commits) | 75 `run_git` calls, 22.4 s | loaded |
| `hook_attestation.find_hook_attestation` on `HEAD~60..HEAD` | 61 spawns, 8.7 s | loaded |
| 60 x `git show sha:pyproject.toml` vs one `git cat-file --batch` | 4.44 s vs 0.115 s | quiet-ish, same run |
| One `find_comments_going_stale` call (stale-comment gate) | 5 spawns, 0.59 s | loaded |
| Repo fixture `_repo_at_t0` (init, config x2, add, commit) | median 0.60 s, min 0.52 s over 10 | loaded |
| `test_stale_comment_age.py` alone | 70 passed in 94.0 s | loaded |
| `test_python_floor_compatibility.py` alone | 1,193 passed in 21.8 s | loaded |
| Bare `ast.parse` over all 405 `src/` modules (read + parse) | 2.24 s | loaded |
| `worker_budget.available_workers` sampling inside one pytest session | 0.505 s (`time.sleep` in `psutil.cpu_percent`) | profile of one session |

## Findings

- 48-F12 P2: `baseline_trend.history` spawns one `git show` per commit that touched the file.
  Measurement: 75 `run_git` calls and 22.4 s on a 74-commit file (loaded). Each commit costs one spawn of about 0.1 to 0.3 s.
  Cause: `history()` in `src/py_ci_shared/baseline_trend.py` loops over `_commits_with_paths()` and calls `_git(repo, "show", f"{sha}:{at_path}")`
  per entry; the one `git log --follow --name-status` that finds the commits is already a single spawn, so only the blob reads repeat.
  Proposed disposition: read all blobs with one `git cat-file --batch` spawn fed `sha:path` lines on stdin, then count per
  blob. Expected saving: about 74 spawns, roughly 20 s on this file loaded (about 4 s quiet, from the 60-commit ratio above);
  the report output is unchanged.

- 48-F13 P2: `hook_attestation._hash_at` spawns one `git show <sha>:<CONFIG>` per commit of the range.
  Measurement: 61 spawns and 8.7 s for a 60-commit range (loaded); by the same ratio a full-history range (752 commits, this
  repository) would spawn about 750 times, roughly 100 s loaded. Cause: `find_hook_attestation` calls `expected(sha)` for every
  commit that `commits_in()` returns, and `expected` runs `run_git(repo, "show", ...)` each time. The default range
  (`HEAD^!` or `@{upstream}..HEAD`, from `commit_metadata._default_range`) is small, so the cost is paid only with an explicit wide `--range`
  or a long-lived unpushed branch.
  Proposed disposition: collect the distinct `sha:CONFIG` specs for the range and resolve them with one `git cat-file --batch`
  call, keyed by sha (the `expected` callable keeps its signature). Expected saving: 60 spawns become 1 (8.7 s to about 0.2 s
  loaded for 60 commits); a 752-commit range from about 100 s to about 1 s.

- 48-F14 P2: `test_python_floor_compatibility.py` re-reads and re-parses every module in separate parametrized tests.
  Measurement: 1,193 tests in 21.8 s. A single `ast.parse` pass over the 405 `src/` modules takes 2.24 s, and the file runs that parse
  in two parametrized families (`test_no_module_uses_a_runtime_union_between_classes` over 405 modules and
  `test_no_file_imports_tomllib_without_a_version_guard` over `MODULES + TEST_FILES`, about 900 modules), so roughly 1,300 file
  reads and parses per run. Cause: each test calls `path.read_text(...)` and `ast.parse` on its own, with no shared cache, and every
  module costs one pytest item. The other part of the 21.8 s is per-item pytest overhead for 1,193 items.
  Proposed disposition: parse each module once through a module-level cache (the same parse cache the gates use, see 48-F6) and keep one
  parametrized item per file for the assertion, or fold the two checks into a single per-file test that reads once. Expected saving:
  the parse share, about 6 to 9 s of the 21.8 s (estimated from the 2.24 s parse pass times the roughly 1,300 parses, not measured
  directly after the change); the item count drops from 1,193 to about 800 if the families merge.

- 48-F15 P2: the git-backed test files build a fresh repository for every test.
  Measurement: `test_stale_comment_age.py` (70 tests, 94 s loaded). The repository fixture alone costs median 0.60 s
  (`git init`, two `git config`, `git add`, `git commit`), so about 40 s of the 94 s is fixture setup, and the gate call itself is
  0.59 s (5 spawns). 31 test files call `git init` directly and 85 call sites use `git_env()`; the same per-test pattern appears in
  `test_repo_hygiene.py` (32 tests), `test_core_corpus.py` (31), `test_version_tag_currency.py` (26), `test_worktree_hygiene.py` (23) and
  `test_adoption_matrix.py` (20). Those files were not timed; the 0.6 s figure applies to each of their per-test setups.
  Cause: the setup is repeated inside each test instead of built once per module.
  Proposed disposition: a module-scoped fixture that builds one template repository (init, config, one commit) and gives each
  test a `shutil.copytree` copy, or a shared helper in `tests/conftest.py` that does so. Expected saving: about 0.5 s per test
  that builds a repository, so about 35 s of this file (loaded) and an estimated 30 to 60 s over the 31 files; the copy costs
  tens of milliseconds. Tests that need a different history (dates, shallow clone, sha256 init args) keep their own setup.

- 48-F16 P3: `closed_audit_rounds._declared_edit` runs a full `git log` for every changed closed-round file.
  Measurement: not measured (needs a branch that edits closed rounds). Cause: `find_closed_round_edits` calls `_declared_edit`
  per changed file, and each call runs `read_commits(repo, rev_range, paths=...)`, one spawn per file in the diff under
  `closed_dir`. A branch that touches N closed rounds spawns N logs over the same range.
  Proposed disposition: one `read_commits` over the range with no path filter, then filter the trailer and paths in Python (or
  memoise per range). Expected saving: N-1 spawns; negligible for the usual one or two closed rounds, so this is a P3 cleanup.

- 48-F17 P3: `tests/conftest.py` samples the CPU for 0.5 s at the start of every xdist session.
  Measurement: `worker_budget.available_workers` took 0.505 s in the profile of one pytest session (`time.sleep` inside
  `psutil.cpu_percent`). Cause: `available_workers()` blocks for `sample_seconds` by design; the conftest hook calls it once per
  session, and the pre-push hook runs one session.
  Proposed disposition: accept. The saving is 0.5 s per session and the sample is what makes the count honest; no change.
  Recorded so the number is not rediscovered.

## Non-findings checked

- Import time of `py_ci_shared` and of the pytest plugin: under 0.1 s for the plugin module itself (`pytest_plugin.py` 0.106 s cumulative
  in the profile, dominated by stdlib imports). Not a finding.
- Gate-level git spawns per call: `find_comments_going_stale` makes 5 spawns (0.59 s). Not a finding.
- `autouse` fixtures in `tests/conftest.py`: `_fresh_tree_digests` and `_no_inherited_coverage_env` do trivial work per test;
  `_isolated_git_config` is session-scoped. Not the cause of the per-item overhead.

## Summary

| Id | Sev | Area | Measured saving | Status |
|---|---|---|---|---|
| 48-F12 | P2 | `baseline_trend.history`: one `git show` per commit | 75 spawns, 22.4 s on a 74-commit file (loaded) | proposed |
| 48-F13 | P2 | `hook_attestation`: one `git show` per commit in the range | 61 spawns, 8.7 s for 60 commits; about 100 s for full history | proposed |
| 48-F14 | P2 | floor-compatibility test re-parses each module in each family | 21.8 s file; parse share estimated 6 to 9 s | proposed |
| 48-F15 | P2 | per-test git repository setup across 31 test files | 0.6 s per test; 40 s of a 94 s file (loaded) | proposed |
| 48-F16 | P3 | closed-round check spawns one log per changed file | not measured; N-1 spawns | proposed |
| 48-F17 | P3 | xdist session CPU sample | 0.5 s per session | accepted, no change |

Counts: 6 findings (4 P2, 2 P3). Four are proposed for change (48-F12, 48-F13, 48-F14, 48-F15, with 48-F16 a proposed cleanup
of five), one accepted (48-F17). Expected combined saving on the affected paths: about 20 s on report and history commands,
about 9 s of the floor-compatibility file, about 40 s of the stale-comment file and an estimated 30 to 60 s across the
31 git-backed test files, all loaded-machine figures. The gate path (`run-all`) is not affected by 48-F12 to 48-F16 except
through 48-F13 when a wide range is passed.

## Caveats

- The full local suite was not timed; per-file and per-test numbers come from the scratch copy on a loaded machine, and the
  stale-comment and floor-compatibility files were each run once after a warm-up pass.
- 48-F15 extrapolates to the other 30 files from one measured file; their per-test setup was not timed.
- 48-F14 and 48-F12 savings are estimates from measured parts (parse pass time, spawn cost), not a before/after run.
