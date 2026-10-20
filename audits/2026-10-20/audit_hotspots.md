# py-ci-shared hot-path audit (2026-10-20)

Read-only. No source or test files were edited. Throwaway scripts and profiles are in
`C:\Users\Admin\AppData\Local\Temp\claude\C--Users-Admin\9a02d9bd-67d1-4dcb-a0d2-36903f8921dc\scratchpad\`
(`drive.py` runs every gate entry that takes a root argument over this repo; `gates.prof` is the cProfile dump;
`pstats_report.txt` and `callers.txt` are the stats; `timing_plain.txt` is the unprofiled per-gate timing).

## Method

- Corpus: this repo as the consumer root (`src/` plus `tests/`, about 252 Python files, about 67k lines in `src/py_ci_shared`).
- Driver: every registered `kind = "gate"` module whose default entry takes `root` as its first argument was called once, in one process.
  92 of 178 gates were skipped by the driver for having no root parameter, so the numbers cover the root-taking gates only.
  Gate failures on this repo (expected, it is not a consumer) do not change the timing much.
- Unprofiled wall time: **111.7 s** for the run. Profiled wall time: **199.6 s** (cProfile overhead about 1.8x). Percentages below are of the profiled total.
- Tools: `cProfile` for the whole run, `pstats` for cumulative, self, and caller breakdowns.

## Per-gate wall time (unprofiled, top 6)

| gate | seconds | note |
|---|---|---|
| api_floor | 19.3 | 21.9 s of it is one `vermin` subprocess run |
| predictable_tmp_path | 6.6 | |
| ci_install_covers_entry_imports | 4.9 | |
| unsafe_deserialization | 4.7 | |
| git_child_env | 4.5 | |
| loop_invariant_cast | 3.7 | |

## Top 10 functions by cumulative time (profiled)

| # | function | ncalls | cumtime s | tottime s | note |
|---|---|---|---|---|---|
| 1 | `api_floor.assert_api_floor` | 1 | 27.7 | 0.00 | 21.9 s in `_run_vermin` (subprocess) |
| 2 | `_core/node_index.child_nodes` | 6,021,252 | 35.6 | 21.3 | recursive visitors across gates |
| 3 | `_core/corpus.iter_files` | 61 | 31.0 | 0.16 | 24.9 s in `git_listing` |
| 4 | `_core/scan.scan_python` | 44 | 30.0 | 0.09 | calls `iter_files` 43 times |
| 5 | `_core/node_index.tree_memo` | 10,485 | 29.9 | 0.09 | |
| 6 | `_core/git.run_git` | 191 | 26.5 | 0.01 | git subprocess; 183 are `ls-files`/`rev-parse`/`check-ignore` |
| 7 | `_core/corpus._git` | 183 | 24.8 | 0.00 | wrapper of 6 |
| 8 | `atomic_write_staging.find_atomic_write_staging` | 1 | 14.3 | 0.05 | 386k `child_nodes` calls |
| 9 | `cancellation_prone_moments.find_cancellation_prone_moments` | 1 | 14.2 | 0.02 | 779k `child_nodes` calls; `_own_nodes` 7.8 s |
| 10 | `lf_file_writes._collect` / `_file_findings` | 1 / 252 | 12.8 / 12.2 | 0.26 | two full recursive `visit` passes per file |

## Top 10 functions by self time (profiled)

| # | function | ncalls | tottime s | note |
|---|---|---|---|---|
| 1 | `TextIOWrapper.read` | 566 | 22.5 | mostly `pathlib.read_text`; not attributed further (see gaps) |
| 2 | `BufferedReader.read` | 914 | 22.4 | subprocess pipe readers: this is wait time, not CPU |
| 3 | `node_index.child_nodes` | 6,021,252 | 21.3 | |
| 4 | `builtins.isinstance` | 61,883,859 | 19.8 | driven by the per-node checks in (3) |
| 5 | `builtins.getattr` | 12,973,952 | 5.7 | driven by (3) |
| 6 | `node_index._walk` | 1,400,002 | 5.6 | one-time per tree (memoised by `_descendants`), so cheap in practice |
| 7 | `dict.get` | 8,663,701 | 3.6 | driven by `_FIELDS.get` and the visitors |
| 8 | `builtins.compile` (inside `ast.parse`) | 1,245 | 3.5 | 544 cache-miss parses of `_parse_text`, 267 small-text parses, 326 `_is_one_shot` |
| 9 | `_winapi.CreateProcess` | 194 | 2.7 | git and vermin spawns |
| 10 | `tokenize._generate_tokens_from_c_tokenizer` | 1,187,372 | 2.3 | `comment_repo_paths._python_comments` and similar token scans |

## Checked and ruled out

- **Repeated `ast.parse` of the same file across gates:** not a problem. `_core/source.parse_source` serves
  the cache, so only 544 real parses happened for about 252 files. The 267 `parse_text` calls are small strings below the cache threshold.
- **Regex compilation in loops:** not in the profile's top entries. `re.compile` never showed in the cumulative top 45.
- **Repeated `os.stat`/`resolve`:** `realpath` costs 1.6 s over 8,172 calls, minor.
- **`ast.walk` on the same tree:** `node_index.walk` already memoises the node list per tree; the one-time build is 9.2 s cumulative in `_walk`, and it is paid once per tree.

## Top 3 hotspots and proposed fixes

### HOT-1 (P2) -- Per-gate git subprocesses for file listings (about 25 s profiled, about 15-20 s wall)

**Disposition:** RESOLVED -- git_listing remembers a positive work-tree probe per root, which removes two of the three git processes per listing (about 90 ms of about 260 ms measured); ls-files is never cached because a gate may write a file and list again.

`corpus.git_listing` runs `rev-parse`, `check-ignore`, and `ls-files` on every `iter_files` call.
`iter_files` ran 61 times and `scan_python` 44 times in one process, so `run_git` ran 191 times. Many of these listings
are identical (same root, same flags).

Proposal: memoise `git_listing` per `(resolved root, include_untracked)` for the life of the process, keyed also by the
stat of `.git/index` so a file added or removed by a gate invalidates it. `clear_parse_cache` and `refresh` clear it.
Expected: about 183 git calls down to about 4 to 6 (one per distinct root and flag pair). Estimated saving 15-20 s wall
on this run, and more on a consumer that runs many gates per job. The risk is a stale listing if a gate writes files
and then lists again. The `.git/index` stat key covers that.

### HOT-2 (Low) -- Recursive visitors that re-walk every tree with `child_nodes` (about 35 s cumulative, about 20 s self, profiled)

**Disposition:** WON'T FIX -- measured: child_nodes costs about 0.65 microseconds a node, about 4 s over 6 million calls unprofiled; the profiled 35 s is mostly cProfile overhead per call, and the scope-carrying visitors cannot be replaced by a flat walk.

Six distinct full-tree recursions each call `child_nodes` once per node: `lf_file_writes` (two `visit` methods, 485k calls each),
`numba_seed_range` (two, 485k each), `_gate_report.visit` (488k), `cancellation_prone_moments._own_nodes` (779k),
`constant_fallback_cache_key._visit_functions` (785k), `atomic_write_staging` (387k), and `hash_key_determinism` (818k).
Each of them pays a Python-level call and an `isinstance` loop per node, about 62M `isinstance` calls in total.

Proposal: have these callers iterate `node_index.walk(tree)` (memoised, already in the code) or `nodes_of(tree, T)`
for the node types they need, and keep only the context-dependent recursion (parent or scope tracking) as a visitor.
`lf_file_writes` and `numba_seed_range` each do two passes that could share one list. Expected: `child_nodes` calls down by about
60 to 70 percent, saving about 20 s profiled and roughly 10-15 s wall. The change is mechanical per gate, and each gate's
existing tests cover the output.

### HOT-3 (P2) -- `vermin` subprocess in `api_floor` (21.9 s, the single largest gate)

**Disposition:** RESOLVED -- api_floor keeps vermin's output per file digest, floor and vermin version; a warm run on this repository takes 0.8 s instead of 17.2 s.

`api_floor._run_vermin` launches `vermin` over every file in batches on every run, with no result cache.
Proposal: cache the vermin hits per file, keyed by the file's blake2b digest (the `_core/gate_cache` store already holds
digest-keyed results for other gates), and run `vermin` only on changed files. Expected: warm runs drop from 21.9 s to near zero;
a cold run is unchanged. If the cache is not wanted, a single `vermin` run over all paths (instead of batches) would
remove the per-batch interpreter start-up, but that gain is unmeasured.

## Gaps and caveats

- `TextIOWrapper.read` (22.5 s self, 560 calls) is mostly from `pathlib.read_text`, but I did not attribute the calling gates.
  It could be re-reads of the same source file across gates, which would be another instance of fix 2 or of the parse cache not covering that path.
  Worth a follow-up with a `pstats` caller drill-down on that exact line.
- The profile includes 92 gates skipped by the driver. Gates whose failure short-circuits their work were also timed on failure paths.
- Profiled numbers are inflated by about 1.8x. The relative ranking is reliable. The absolute wall-time gains are estimates, not measured after the fix.
- `BufferedReader.read` (22.4 s) is subprocess pipe waiting. It is not CPU and is not a target.
