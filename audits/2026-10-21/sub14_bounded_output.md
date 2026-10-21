# SUB-14 bounded child output (2026-10-21)

Scope: the captured output of the mutation runner's cold children (`run_with_deadline`) and of the warm worker's reply pump. Tracker finding SUB-14 said the parent's memory grew with whatever a mutant printed, until the timeout.

### SUB-14-1 (P2) -- run_with_deadline and the warm reply pump keep the whole child output

**Disposition:** FIXED, verdicts unchanged. The fix is in the mutation harness content hash list, so the parent must refresh the baseline and bump `HARNESS_VERSION`; this change does not do either.

The defect: `run_with_deadline` used `communicate()`, which holds all of stdout and stderr in memory, and the warm worker's pump queued every line without a limit. A mutant that makes the suite print in a loop grew the parent until the timeout.

The fix has three parts.

1. `OutputScan` (in `_mutation_runner.py`) reads the classifiers' markers as the stream arrives. It records the first decisive `E ` line (crash or assertion), the target of the first FAILED/ERROR line, the last summary line, and every FAILED/ERROR name. The names are capped at 10,000 and the true count is kept in `failed_total`. Pieces that end mid-line are held until the line ends, up to 1 MiB per line. A final unterminated line is judged by `finish()`.
2. `run_with_deadline` keeps only the last 256 KiB of stdout and of stderr. The result is a `CapturedRun` whose `stdout_scan` holds the whole-stream scan. `_scan_of(run)` returns that scan, or builds one from the text for a plain `CompletedProcess` (used by test fakes).
3. The callers (`_mutation_sweep.py` and `teeth_sweep.py`) classify from the scan. `_killed_by_crash`, `_first_failing_file` and `read_pytest_outcome` keep their string signatures for the existing tests and now run on `OutputScan`. The `[-2000:]` diagnostic prints are unchanged, because the tail is an exact suffix of the stream. The warm reply pump's queue holds at most 64 lines and drops the oldest, so the newest reply always survives.

**Before / after (measured with the child printing 50 MiB, `sub14_measure_tmp.py`):**

| | captured stdout chars | tracemalloc peak | elapsed |
|---|---|---|---|
| before (HEAD) | 52,428,800 | 150.4 MiB | 1.4 s |
| after | 262,144 | 0.6 MiB | 2.5 s |

The elapsed time rose by about a second, from the per-line scan in Python. That is the cost of the bound.

**Verdict analysis.** Every classifier input is either the same as before or a tail whose value is the same.

- `_killed_by_crash`: the first decisive `E ` line is taken over the whole stream, not the tail. A crash marker early in a 50 MiB stream still wins.
- `_first_failing_file`: the first FAILED/ERROR line is taken over the whole stream.
- `read_pytest_outcome` and `outcome_of`: the summary is the last matching line over the whole stream. The names are the same list, capped only beyond 10,000. The verdict uses only whether that list is empty, and `failed[:3]` and `failed[:10]` are printed from its head, so the verdict is unaffected.
- Exit codes: unchanged. `_classify` still reads only `returncode`.
- Timeout: still returns `None` and kills the tree.

**Where a verdict could still differ, in theory.** Only in lines longer than 1 MiB. Such a line is judged by its first 1 MiB, so a marker past that point is not seen. The warm worker's pump still calls `readline()` with no size, so a single unterminated line of unbounded length still accumulates there. The parent's memory is bounded for every line under 1 MiB and for any number of lines. The worker's own in-process capture buffer (`_captured_output` in `_mutation_worker.py`) is not changed and is outside the parent.

**Tests.** `tests/test_mutation_output_bounded.py` (11 tests):

- A crash marker and a FAILED marker placed early, followed by more than the cap, classify the same as the unbounded reference functions (copied from HEAD into the test).
- A random-chunking property test checks that any split of the stream gives the same result as the whole text. It found a real bug during development: a marker split across two pipe reads was dropped. The scanner now holds a partial line.
- A child process printing 4 x the cap, with early crash and FAILED markers, returns a tail of at most one piece and classifies as the whole stream does.
- A long line with an early marker, a timeout returning `None`, and the warm queue keeping the newest line.

**Results.**

- `tests/test_mutation_output_bounded.py`: 11 passed.
- Existing files (`test_mutation_teeth.py`, `test_mutation_teeth_regressions.py`, `test_mutation_worker.py`, `test_mutation_fingerprint_python_files.py`, `test_mutation_harness_version_is_bumped.py`, `test_runtime_registry_mutation.py`, `test_teeth_sweep*.py`, `test_gate_teeth.py`, with `-n 4 --no-cov`): 1066 passed, 1 failed. The failure is `test_mutation_harness_version_is_bumped.py::test_the_harness_version_tracks_the_harness`, which reports "tracked source changed (content hash 5e3492aa2902bc16 -> 0ac38791bfa60b44) but '24' was NOT bumped". That is the expected gate for a change to `_mutation_runner.py`, which is in the hash list. It is not a verdict change. The parent needs to refresh the content-hash baseline and bump `HARNESS_VERSION`.
- The affected subset (new file, `test_mutation_worker.py`, `test_mutation_teeth_regressions.py`, `test_teeth_sweep*.py`) was rerun after the last refactor of `_put_latest` and the black pass: 120 passed.
- `python -m ruff check` on the four changed Python files: passed.
- `python -m mypy src/py_ci_shared`: no issues in 252 files.
- `black --check` on the changed files: passed after formatting.

**Files changed.**

- `src/py_ci_shared/_mutation_runner.py` (harness hash list)
- `src/py_ci_shared/_mutation_sweep.py` (harness hash list)

- `tests/test_mutation_output_bounded.py` (new)
- `audits/2026-10-21/sub14_bounded_output.md` (this report)

Line endings are LF in HEAD and in the changed files.
