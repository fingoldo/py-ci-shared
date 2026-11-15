# 48 performance of the gates: round 2026-11-16 (T)

Read-only audit of commit `48e49b1` (py_ci_shared master) plus the uncommitted tree of other sessions at the time. Consumers were measured as
snapshots: pyutilz `src` read in place (188 files, 2,494 KB), mlframe `src` copied at commit `d2ef3afd6` (2,992 files, 32,634 KB; copy in the scratch
directory, so its moving tree did not matter). No repository file was edited; every fix below was prototyped in the scratch directory by
monkeypatching the function under test, and every prototype was checked for IDENTICAL findings against the unpatched code.

Scratch directory: `C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\audit_T\48\`.

## Method (what I ran)

- `prof_ca.py`: pre-warms the shared parse cache (`_core.source.parse_file`) for the whole corpus, then times every one of the 105 registered
  `code_audit` scanners singly (`wall`, and ms per KB of source). pyutilz: `pyutilz_ca.txt/json`; mlframe: `mlframe_ca.txt/json`.
- `run_top.py` + `enumerate_gates.py`: the 105 top-level gates whose only required argument is a root (`find_*`, `scan_*`, `collect_*`), timed over
  pyutilz `src` (or the pyutilz repo root for `repo_root` gates): `pyutilz_top.txt`. The same over mlframe: `mlframe_top.txt` (partial, see "Not covered").
- `cProfile` (`prof_one.py`, `prof_ca1.py`, `syscalls.py`) on the slowest gates; `syscalls.py` counts `_getfinalpathname`, `open`, `stat`, `compile`
  calls per scanner per file.
- Scale tests: `scale.py` (one real 30 KB file, `pandaslib/dtypes.py`, concatenated 1x, 10x, 50x into ONE module; identical names, so name-keyed gates
  are over-stressed) and `scale_distinct.py` (the same file with every module-level name suffixed per copy, 10x and 40x; distinct names). `iso.py`
  re-measures suspects best-of-3 at 10, 20, 40, 80 copies. `synth_bench.py`: a module of N distinct functions (64 KB at N=300, 1 MB at N=4800).
  `top_scale.py`: the same 10x/40x test over the top-level gates.
- Static hunts: `nested.py` (an `ast.walk` loop containing another walk or a heavy call), `gitloop.py` (a git/subprocess call inside a loop),
  `redos_run.py` (every module-level compiled regex, 606 patterns in 412 modules, against 21 adversarial 3000-character inputs, one child
  process per module with a 60 s limit).

The machine was heavily loaded by my own parallel jobs for most of the run (up to four Python processes), so absolute seconds are inflated 2x to
4x; ratios between sizes and before/after pairs measured back to back are the figures to trust. Every before/after below was taken in the same
process, back to back.

## Findings

## 48-T1 (P2) Five gates call `ast.get_source_segment` once per function: quadratic in the functions of a file (the `git_child_env` class, not fixed here)
- **Evidence:** `ast.get_source_segment(source, node)` re-splits the WHOLE source on every call (that was `git_child_env`'s defect, fixed in c2abbc6 with
  `git_child_env._split_source` / `_segment`). Seven other call sites remain (`grep -rn get_source_segment src`):
  - `src/py_ci_shared/code_audit/partial_fix.py:80` (once per member of every sibling-function family of >= 3) and `:131` (once per function in
    `scan_inconsistent_filter`)
  - `src/py_ci_shared/ordinal_rank_scatter.py:74` (once per function, before any candidate test)
  - `src/py_ci_shared/ml_api_contracts.py:80` (once per function, in `_argmax_findings`)
  - `src/py_ci_shared/oof_buffer_zero_init.py:117` (once per function that has a buffer; lazy)
  - `src/py_ci_shared/predictable_tmp_path.py:130,132` (once per scope that a candidate asks for; lazy) and
    `src/py_ci_shared/module_reload_safety.py:395` (once per reload site; few)
  The first four are unconditional, so they are the hot ones.
  Demonstration 1, `synth_bench.py`, one module of N distinct 9-line functions (unpatched, loaded machine):

  | N functions | file | partial_guard | inconsistent_filter | ordinal_rank_scatter | ml_api_contracts |
  |---|---|---|---|---|---|
  | 300 | 64 KB | 1.1 s | 1.0 s | 2.6 s | 1.8 s |
  | 1,200 | 258 KB | 13.3 s | 13.4 s | 14.1 s | 13.7 s |
  | 4,800 | 1,038 KB | 242 s | 260 s | 242 s | 239 s |

  4x the size costs 12x then 18x the time. With a one-line patch of the segment function (`patched_bench.py`, regex-split once per source) the same
  runs take 0.02 / 0.06 / 0.9 / 0.7 s at N=300, 0.3 / 0.3 / 0.9 / 0.9 s at N=1,200 and 0.2 / 0.5 / 1.6 / 1.6 s at N=4,800.
  Demonstration 2, `scale_distinct.py` (a real file replicated with distinct names, 10 vs 40 copies, 4x the size, linear would be 4.0): at 10 copies
  partial_guard 3.0 s, ml_api_contracts 3.2 s, ordinal_rank_scatter 3.8 s; at 40 copies 53.0 s, 43.1 s, 45.4 s (ratios 17.9x, 13.3x, 11.9x). The
  same test, copies concatenated with identical names (`scale.py`): partial_guard 0.023 s at 1x, 3.1 s at 10x, 85 s at 50x (3,700x for 50x the size).
  Demonstration 3, real code (`eqfind.py`, same findings before and after, `identical findings: True`): mlframe `feature_selection` subtree,
  `scan_partial_guard_across_siblings` 40.0 s -> 7.9 s, the three whole-gate runs together 32.1 s -> 18.1 s; pyutilz `src`, partial_guard plus
  inconsistent_filter 2.75 s -> 0.43 s, the three gates 8.3 s -> 2.9 s.
  Equivalence of the replacement: `equiv.py` compares the original and the pre-split slice on 5,276 real function and assignment nodes of pyutilz:
  0 mismatches.
  Why it matters beyond one file: a file with F functions costs F x (file size), so one 250 KB module with 1,200 functions is 13 s per gate; the
  mlframe `_mrmr_class.py` (273 KB, the biggest file in the snapshot) is in that range, and a generated or vendored module would stall a pre-push
  hook with no output.
- **Impact:** wall time of a pre-push `code_audit` run and of the three top-level gates on large files; no wrong verdicts. The same defect is what
  made `git_child_env` a 15-minute gate, so it will recur on the next copy-pasted `get_source_segment` unless a rule forbids it.
- **Proposed fix:** move `git_child_env._split_source` and `_segment` (already correct for CRLF, lone CR and multi-byte characters, and used by a
  gate that ships) into `src/py_ci_shared/_core/source.py` as `source_segment(source_or_lines, node)`, with the split memoised on the source string
  like `comment_tokens` (`lru_cache(maxsize=8)` or a one-entry identity memo; the five sites call it with the same `source`). Replace the seven call
  sites. Add one self-gate test: no module under `src/py_ci_shared` other than `_core/source.py` calls `ast.get_source_segment` (AST scan, a
  pinned count of 0), so the class cannot return. ADDITIVE (identical findings, shown above).
- **Disposition:** OPEN

## 48-T2 (P2) `console_unicode` re-resolves and re-reads the `__init__.py` chain for every file: 14x faster with a per-directory memo
- **Evidence:** `src/py_ci_shared/code_audit/console_unicode.py:78-102`, `_ancestor_package_has_stdio_utf8_reconfigure(py, root)`, called once per
  file (line 127). It climbs from the file's directory to the root and, at every level, runs `is_file()`, reads and decodes that level's
  `__init__.py` (`_read_init_source`; the "once per path" guard in its docstring covers only the warning, not the read) and calls
  `current.resolve()` (a `GetFinalPathNameByHandle`). So a file at depth D costs about D reads and D resolves, and a package with 100 modules reads
  its `__init__.py` 100 times. `syscalls.py` over pyutilz `src` (188 files): `console_unicode` makes 1,696 `_getfinalpathname` calls (9.0 per file; the next
  scanner makes 396), 472 `open` calls (2.5 per file), 660 `is_file` probes. `prof_ca1.py`: 0.95 s of the 2.75 s are `_getfinalpathname`.
  Measured (`fix_console.py`, best of 3, same findings before and after, `same findings True`): pyutilz `src` 2.63 s -> 0.20 s; mlframe snapshot
  (2,992 files, deeper trees) 68.3 s -> 4.7 s. In the mlframe ranking it is the slowest scanner of the 105 (95.8 s in `mlframe_ca.txt`, 2.9 ms per KB
  against a median of about 0.5), and it sits at the top of the pyutilz ranking by ms per KB among the simple scanners too (1.2).
  The prototype keeps a dict `directory -> bool` for the duration of one `scan_console_unicode` call: a chain is resolved upward once, and every
  directory on the chain gets the answer.
- **Impact:** about a minute of CPU per consumer run on a large tree (mlframe) for one trivial check.
- **Proposed fix:** in `scan_console_unicode`, create `memo: dict[Path, bool] = {}` and pass it to `_ancestor_package_has_stdio_utf8_reconfigure(py, root, memo)`
  (optional third parameter, default None, so the helper's signature stays compatible); resolve `root` once per scan. ADDITIVE.
- **Disposition:** OPEN

## 48-T3 (P3) `comment_cites_absolute_line` tokenizes every file; its sibling skips files that cannot match
- **Evidence:** `src/py_ci_shared/code_audit/comment_names_missing_symbol.py:225-228`: `scan_comment_cites_absolute_line` calls `_comment_texts(py)`
  (a `tokenize` pass plus an `ast.get_docstring` walk, which the sibling's own comment at lines 163-168 calls "by far the most expensive phase") for
  every file. The sibling `scan_comment_names_missing_symbol` avoids it with a cheap pre-filter on the source lines; this one has none. `_comment_texts`
  is cached per process (`_COMMENT_TEXTS_CACHE`), so in a sequential run one of the two scanners pays; under `run_all(parallel=True)` each worker
  process has its own cache and both can pay. Measured (`fix_cites.py`, cache cleared before each run, `same True`): pyutilz 3.58 s -> 1.21 s; mlframe
  74.95 s -> 20.66 s (60 findings both ways). The prototype returns `[]` from `_comment_texts` for a file whose text has no `_LINE_CITATION` match
  (`\b(?:at |on )?line[s]? (\d+)\b`, case-insensitive), the same "a comment is a substring of the source" argument the sibling already relies on.
- **Impact:** CPU time only (about 54 s of CPU per mlframe run, spread over workers).
- **Proposed fix:** pre-filter by `_LINE_CITATION.search(text)` over the file text (read once through the shared bytes cache) before `_comment_texts`.
  ADDITIVE. One edge: a docstring that builds "line" and the number by implicit string concatenation would stop matching; the sibling accepted the
  same edge.
- **Disposition:** OPEN

## 48-T4 (P3) `doc_identifier_parity._absent` searches a 7 MB corpus string once per distinct token (1.9x available without changing the answer)
- **Evidence:** `src/py_ci_shared/doc_identifier_parity.py:152-157,159-195`. `_in_corpus` memoises per token, but each distinct token not yet seen does
  `token in corpus` over the whole joined corpus. Profile over the pyutilz repo root (`prof_one.py`): `find_absent_doc_identifiers` 11.9 s, of which
  `_in_corpus` 3.97 s (2,355 calls) and the git listing 3.1 s. `doc_fix.py`: corpus 7.0 MB in 682 files, 843 distinct tokens, 138 of them absent
  (absent tokens scan the full corpus); the loop takes 2.78 s. A pre-check against the set of `[\w-]+` words of the corpus (`token in words` is
  O(1); only tokens that are not a whole word fall back to the substring search, which keeps the "substring anywhere" meaning) takes 1.48 s including
  building the set, `same: True` on all 843 tokens. In the unloaded pyutilz run this gate was 51 s on the first measurement (cold parse, loaded
  machine) and is the slowest `repo_root`-style gate in `pyutilz_top.txt`.
- **Impact:** seconds per documentation gate run, growing with corpus size x distinct tokens (a documentation-heavy repo with a large corpus is the
  worst case).
- **Proposed fix:** build `words = set(re.findall(r"[\w-]+", corpus))` once in `_absent` and test `token in words or token in corpus` in `_in_corpus`
  (pass `words` through the memo argument). ADDITIVE; verdicts identical by construction (a whole word is a substring).
- **Disposition:** OPEN

## 48-T5 (P3) `source.comment_tokens` is an `lru_cache(maxsize=512)`: a corpus larger than 512 files gets no reuse between gates
- **Evidence:** `src/py_ci_shared/_core/source.py` (`comment_tokens`, `@functools.lru_cache(maxsize=512)`). `tokc.py` over 1,500 mlframe files, two passes in one process: pass 1 5.2 s (hits=6, misses=1494), pass 2 4.0 s (hits=12, misses=2988), cache full at 512: a sequential scan of more files than the cache holds evicts every entry before it is read again (LRU thrash). mlframe has 2,992 files, so every gate that tokenizes pays again. Profile of `comment_repo_paths` over the pyutilz repo (635 files, above the limit): 13.5 s of 63 s in the C tokenizer and `_make`; on the mlframe snapshot the gate takes 169 s (pyutilz 5.1 s, 33x for 13x the source; `mlframe_top.txt`). Same snapshot: `doc_identifier_parity` 204 s (T4 corpus search plus listing).
- **Impact:** CPU time on corpora above 512 files; no wrong verdicts.
- **Proposed fix:** key a bounded cache by the file (path + digest) instead of the text with a size near the corpus, or size the LRU from the corpus (e.g. 8192 entries; tokens of one file are small). The saving was not prototyped beyond the thrash demonstration. ADDITIVE.
- **Disposition:** OPEN

## Rankings (task: "rank gates by wall time and by time per kilobyte, look at the top ten")

All 105 scanners see the same corpus, so ms per KB ranks like wall time within one corpus. Warm parse cache; the FIRST scanner of a process also pays
the shared node index (`node_index._index`) and the cold-parse remainder, so first-in-order scanners look worse than they are (mlframe `bare_except`
15 s for a ten-line check, pyutilz 0.11 s; the gap is the index build, not the scanner; see "Considered").

pyutilz `src` (188 files, 2,494 KB; parse of the corpus 3.8 s; sum of all 105 scanners 62.8 s; median scanner 0.42 s):

| rank | scanner | wall s | ms per KB | note |
|---|---|---|---|---|
| 1 | near_duplicate_function_body | 4.73 | 1.90 | inverted shingle index already; 3.6 s is `_candidate_pairs`, 3.9 s `difflib.find_longest_match` over candidates (profile, warm); linear 1x -> 10x -> 40x (1.7 -> 1.9 s), not a finding |
| 2 | console_unicode | 3.00 | 1.20 | **48-T2** |
| 3 | comment_cites_absolute_line | 2.47 | 0.99 | **48-T3** (cold tokenize) |
| 4 | readonly_to_numpy_mutation | 2.06 | 0.83 | linear in the scale tests (15.8x at 10x, 0.9 s at 50x; small base, noise) |
| 5 | default_via_or | 1.95 | 0.78 | linear: 0.19 / 0.40 / 0.74 / 1.60 s at 10 / 20 / 40 / 80 copies |
| 6 | getattr_unknown_attribute | 1.90 | 0.76 | 4 stats per file (752 `stat` calls), linear in scale |
| 7 | accumulator_helper_bypassed | 1.77 | 0.71 | linear (0.106 s at 10x, 0.44 s at 40x) |
| 8 | unthrottled_hot_loop_log | 1.72 | 0.69 | linear |
| 9 | table_header_row_drift | 1.48 | 0.59 | linear |
| 10 | parameter_aliasing_mutation | 1.37 | 0.55 | linear |

mlframe snapshot (2,992 files, 32,634 KB; parse 62 s cold; sum of all 99 completed scanners 1,451 s, sequential, loaded machine): console_unicode 95.8 s,
comment_cites_absolute_line 77.0 s, near_duplicate_function_body 75.8 s, default_via_or 51.9 s, getattr_unknown_attribute 51.1 s,
duplicate_function_body 44.8 s, record_field_flow 40.1 s, late_binding_closure 29.9 s, parameter_aliasing_mutation 27.5 s, readonly_to_numpy_mutation
27.1 s. Ratio of mlframe ms/KB to pyutilz ms/KB, where a linear gate gives about 1 and load noise about 2x: partial_guard_across_siblings 4.7 (T1),
sibling_guard_missing 8.8, sql_aggregate_before_cast 7.2, write_counted_from_input 6.2; the last three were re-measured in isolation (`iso.py`, best of 3, 10 to
80 copies) and are linear (sql_aggregate_before_cast 0.023 / 0.049 / 0.132 / 0.309 s; write_counted_from_input 0.066 / 0.074 / 0.202 / 0.303 s), so the
mlframe ratio there is load and memory-pressure noise.

Top-level gates (`pyutilz_top.txt`, pyutilz repo root or `src`, 105 gates, first-in-order cold): doc_identifier_parity 51.4 s (T4; 11.9 s in the
profile), adoption_matrix.scan_repo 20.6 s (a composite: `_local_copies` 17.4 s, `find_silent_skips` 9.7 s, `find_module_usage` 4.6 s; profiles show
cold parse, git listing and `os.lstat`, linear), atomic_write_staging 6.7 s (cold parse 7.1 s of its 18 s under the profiler, the rest node-index
walks; linear in `d10 -> d40`: 0.87 -> 2.15 s), comment_repo_path_problems 5.1 s, guard_without_fixture 4.3 s, ml_api_contracts 3.5 s and
ordinal_rank_scatter 3.1 s (both T1: 3.2 -> 43.1 s and 3.8 -> 45.4 s from 10 to 40 distinct copies), committed_line_endings 3.3 s. On mlframe
(partial): scan_repo 152 s, atomic_write_staging 78 s, find_silent_skips 41 s, caller_frame_mutation 32 s, find_module_usage 29 s,
cancellation_prone_moments 18 s; the profile of `caller_frame_mutation` on the 1,488-file mlframe `feature_selection` subtree is 17.6 s in
`node_index._walk` and 8.8 s in `compile` (cold parse), which is the already-dispositioned cost of 48-F4 (node-index not memoised) and 48-F6, not new.

## Scale test results (task: "replicate a real file 10x and 50x and see which gates grow worse than linearly")

Identical-name concatenation (`scale.py`, 1x / 10x / 50x of a 30 KB file, 105 scanners): only `partial_guard_across_siblings` is catastrophic
(0.023 / 3.1 / 85 s). Everything else with a base above 10 ms stays within about 2x of linear at 50x after removing noise (default_via_or 1.2 s,
unthrottled_hot_loop_log 1.4 s, near_duplicate_function_body 1.5 s at 50x). Distinct-name replication (`scale_distinct.py`, 10x vs 40x; linear is 4.0):
partial_guard 17.9x, the other seven apparent outliers (sql_aggregate_before_cast 43.8x, unraised_exception_class 27.4x, sql_limit_without_order_by
12.9x, write_counted_from_input 11.0x, guard_decidable_from_constants 8.3x, comment_cites_absolute_line 8.1x, default_via_or 3.5x) were bases of 18 to 60 ms
on a busy machine; `iso.py` (best of 3, 4 sizes) shows all of them linear. Top-level gates (`top_scale.py`, 10x vs 40x): only ml_api_contracts (13.3x)
and ordinal_rank_scatter (11.9x) exceed the linear 4x; both are T1. oof_buffer_zero_init, atomic_write_staging, stdlib_json_ban, duplicate_test_body,
possibly_unbound_local all 2.5x to 4x.

## Considered and not raised

- **Nested `ast.walk` loops.** `nested.py` over every module of `src/py_ci_shared` finds exactly two: `nested_numba_parallel_guard._guarded_calls` (a walk
  of each `with <guard>()` block inside a walk of the function, linear in with-nesting depth) and `state_before_work._restored` (a walk of each
  `try` handler inside a walk of the function). Both are bounded by nesting depth, not by file size; no measurable cost in the scale runs. Not raised.
- **Repeated parsing of the same file.** `syscalls.py` over pyutilz after one pre-warm: `builtins.compile` is called 0 times by 104 of the 105 scanners
  (2 by `stale_source_citation`, which parses source text it extracts); the shared parse cache (`_core.source`, byte-digest keyed with a stat
  fast path) is used by every `code_audit` scanner (`_safe_parse` -> `parse_file`) and by every `scan_python` gate. Not raised; the cold-parse cost
  per process is 48-F6 (WON'T FIX, 2026-11-07).
- **Git subprocesses inside loops.** `gitloop.py` lists every call to a git or subprocess helper nested in a loop or comprehension: all are bounded by
  entities, not by files or lines: `stale_comment_age._blame_times` (one `git blame` per file with a stale comment, batched `-L` ranges),
  `version_tag_currency` (one `tag --list`, one `merge-base` per run), `git_dependency_pins._checkout_root` (one `ls-files` per checkout),
  `guard_population` (one bash per guard script), `ci_install_covers_entry_imports` (one interpreter per entry point), `_consumers.checkout_consumers`
  (one clone per consumer), `baseline_trend.history` and `committed_line_endings` (one `cat-file --batch` each, per 48-R12). Not raised.
  `worktree_hygiene` (48-S18) is resolved per the tracker.
- **Catastrophic backtracking.** `redos_run.py` ran all 606 module-level patterns against 21 adversarial 3,000-character inputs (long runs of space,
  tab, dash, dot, quotes, parentheses, backslashes, `a.a.`, `a, a`, digits, 80-character lines...). No exponential pattern: the slowest 65 are
  QUADRATIC in a single unbroken run of one character (for example `ci_default_branch_never_cancelled._TOKEN` 1.1 / 3.7 / 15.8 s at 3,000 / 6,000 / 12,000
  spaces, `_ci_install_parts._MARKER_TOKEN` 0.9 / 3.3 / 14.4 s, `arb_checks._SELECTOR_RE` 0.5 / 1.8 / 6.9 s, `workflow_runner_labels._TEXT_KEYS` 0.6 / 2.6 / 11.9 s),
  and every one I checked is used through `.match(text, pos)`, `.fullmatch` or on a bounded snippet, never `.search` over a long line. One pattern hangs a
  naive probe: `edge_function_hygiene._EMPTY_RIGHT_RE = \s*\(*\s*(?:...)` is CUBIC under `.search` on a whitespace run (two adjacent `\s*` split by a
  zero-width `\(*`; my probe timed out at 60 s on 3,000 spaces). Its only call, `edge_function_hygiene.py:173`, is `_EMPTY_RIGHT_RE.match(right)` where `right`
  is at most 120 characters (`_COMPARISON_RE`, line 58, caps both sides at 120), so the worst case is 120^2 = 14,400 steps. Two patterns could not be probed
  (`coverage_config_parity` and `git_changed_lines` hold bytes patterns). A line of thousands of spaces in a consumer file is not a realistic input. Not raised.
- **Reading whole files into memory for huge corpora.** `_core.source._BYTES` is capped (4 MB per file, 400 MB in total, dropped past the cap) and the
  tree digest skipped `.claude` since 48-F1; the AST-per-file cache is 88 percent of RSS and is 48-F3 (WON'T FIX with measurements). The mlframe snapshot
  holds 6.5 million GC objects after parsing 32.6 MB of source. `gc.freeze()` after the pre-parse changed the following scanners by 5 to 35 percent in an
  experiment (`gcexp.py`) that was confounded by a concurrent job (`gc-off` was not faster than `gc-frozen` on three of four scanners), so no claim is made.
- **`scanner order effect`.** `bare_except` costs 15 s on mlframe only because it was the first scanner to touch the trees and built the node index
  (22.3 s first, 5.1 s after `gc.freeze()` and 3.9 s with GC off in the same process: the second and third runs reuse the index). Known cost, 48-F4.
- **adoption_matrix / atomic_write_staging / caller_frame_mutation heavy numbers.** Cold-parse, git listing and node-index cost, linear in the
  scale tests (`atomic_write_staging` 0.87 s -> 2.15 s, 10 -> 40 copies); nothing quadratic found. Not raised.
- **`tracker_summary_parity`, `closed_audit_rounds`, `floor`-compatibility tests.** Not re-measured; see 48-F12 to 48-F17, 48-S18, 48-R12 to 48-R17 in the tracker.
- **Per-test git repositories (48-R15).** Already measured by the previous round (35 ms saving per repo, not the estimated 0.5 s); not revisited.

## Not covered

- mlframe top-level gate ranking finished afterwards (105 gates, loaded machine): doc_identifier_parity 204.5 s, comment_repo_paths 168.8 s, adoption_matrix.scan_repo 152.4 s, atomic_write_staging 77.9 s, find_silent_skips 40.5 s, tracked_secret_shapes 33.8 s, caller_frame_mutation 31.7 s, find_module_usage 29.1 s, find_crlf_writes 28.7 s, possibly_unbound_local 28.5 s (cold-parse and tokenize dominated except the two named findings).
- Parallel `run_all(parallel=True)` wall time was not measured end to end (the on-disk scan cache would have been written outside the repository; I
  measured each scanner singly instead).
- Mutation-harness and pytest-plugin performance (`_mutation_*`) were excluded by the dimension's own earlier rounds (48-F10).

## Summary

| id | sev | title |
|---|---|---|
| 48-T1 | P2 | five gates call `ast.get_source_segment` per function: quadratic per file (partial_guard, inconsistent_filter, ordinal_rank_scatter, ml_api_contracts; lazy: oof_buffer_zero_init, predictable_tmp_path, module_reload_safety); 240 s -> 1.6 s at 1 MB |
| 48-T2 | P2 | `console_unicode` re-reads and re-resolves the `__init__.py` chain for every file; mlframe 68 s -> 4.7 s with a per-directory memo |
| 48-T3 | P3 | `comment_cites_absolute_line` tokenizes every file, its sibling pre-filters; mlframe cold 75 s -> 21 s |
| 48-T5 | P3 | `comment_tokens` LRU of 512 thrashes on corpora above 512 files |
| 48-T4 | P3 | `doc_identifier_parity` substring-searches a 7 MB corpus per distinct token; 2.8 s -> 1.5 s with a word-set shortcut |

Counts: 5 findings (0 P0, 0 P1, 2 P2, 3 P3).
