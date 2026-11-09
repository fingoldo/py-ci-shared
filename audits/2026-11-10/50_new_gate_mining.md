# 50 New gate mining (round 2026-11-10)

Read-only. Sources: TRACKER.md, audits/2026-11-07/50_new_gate_mining.md, configs/consumers.toml, the module list and README of src/py_ci_shared (about 300 gate modules), and local consumer git logs since 2026-10-17 (mlframe, pyutilz, glossum_backend_scripts, llm_bench, algopacksimple, social, noema_app; autopsia and polyvocab_app were not scanned, no local checkout with fix history was read).

Scope limit: hit counts are NOT measured in this round. Each candidate cites the commit that shipped the defect and the grep that showed no matching gate. Where no gate was found by grep, that is recorded as "not found", not as proof of absence.

Classes already gated and therefore dropped: stdlib API floor (api_floor), check-then-act on shared state (check_then_act_outside_lock), zero-valued `or` fallbacks (default_via_or), bare double CUDA pointers (cuda_kernel_pointer_types), wall-clock sleeps (wallclock_test_waits), single-shot timing (single_shot_timing_assertion), NaN-admitting reject guards (nan_admitted_by_reject_guard), unbound locals (possibly_unbound_local), nested numba parallel (nested_numba_parallel_guard), latched availability flags (latched_availability_flags), definition after an entry guard (definition_after_entry_guard), mutable shared placeholders (shared_mutable_placeholder), plotly annotation loops (plotly_annotation_loop), ordinal rank scatter (ordinal_rank_scatter), machine-specific paths (machine_specific_paths).

| id | sev | title | disposition |
|---|---|---|---|
| 50-F1 | P2 | Loop whose step can be zero or None never advances | DEFERRED |
| 50-F2 | P2 | Counter or state advanced before the write that it counts | DEFERRED |
| 50-F3 | P1 | Raw UPDATE/DELETE executed without a row-count check | ADD as gate |
| 50-F4 | P3 | Per-item log line inside a network page loop with no throttle | DEFERRED |
| 50-F5 | P3 | Test assertion depends on runner platform or pandas null repr | DEFERRED |
| 50-F6 | P3 | Doctests and assertions that hold only on one pandas major | WON'T FIX |

Counts: 6 candidates. ADD as gate: 1. DEFERRED: 4. WON'T FIX: 1.

## 50-F1 [P2] Loop whose step can be zero or None never advances

- Defect class: `while cond: x = x - step` (or a cap loop that shrinks a value by a step) where `step` comes from a parameter or config that may be 0 or None. The loop body makes no progress and the call hangs.
- Evidence: algopacksimple 6b13..-era commit "Split compute_optimal_price into helpers; fix infinite loop when min step is 0/None" (6b81ce1 range, 2026-10 to 2026-11) and 41d72f7 "Fix compute_optimal_price cap loop stalling when min step is below rounding precision". Both fixed in consumer code; no py-ci-shared gate was checked for this shape.
- Minimal reproduction:
  ```python
  def cap(price, step):
      while price > 100:
          price -= step   # step=0 -> hangs forever
      return price
  ```
- Detector idea: AST pass over `while` loops whose test compares a name N and whose body reassigns N by `N -=`/`N +=`/`N = N op S`, where S is a non-literal name or attribute. Flag unless S is compared or guarded (`if not S`, `S > 0`, `max(S, eps)`) earlier in the same function, or the loop has an iteration counter break.
- False-positive risk: medium. Most such loops are over literal steps or are bounded by a `for` over a precomputed range; the guard-recognition heuristic must accept `assert`, `raise`, and early `return`.
- Proposed disposition: DEFERRED. Needs a measured hit count across mlframe and algopacksimple before the guard heuristic is chosen; the single known instance is already fixed.

## 50-F2 [P2] Counter or state advanced before the write that it counts

- Defect class: a function does `self.n += 1` (or `counts[k] += 1`, or appends to a "sent" list) and then performs the commit or external write inside a `try`. When the write raises and is caught, the counter reports a success that did not happen.
- Evidence: glossum_backend_scripts 413d99b3 "Fix what the 1.23.8 py-ci-shared gates found: counters advanced before their commit, process-latched unavailability, unread ctor params, echoed values". The gates that found it were the ones named in the message; the counter-before-commit shape itself is not a gate in src (grep for a counter increment adjacent to commit/execute in src found none).
- Minimal reproduction:
  ```python
  def flush(self, rows):
      try:
          self.written += len(rows)     # advanced first
          conn.execute(INSERT, rows)    # raises
          conn.commit()
      except Exception:
          log.warning("flush failed")
      # self.written now claims len(rows) rows
  ```
- Detector idea: per function, when a `+=` on an attribute or subscript precedes a call whose name matches `commit|execute|send|write|post|put` inside the same `try` body, with no `-=` in an except arm. Report with the statement lines.
- False-positive risk: medium to high. Counters of attempts (not successes) are legitimate; the detector cannot tell attempt counters from success counters without a name hint.
- Proposed disposition: DEFERRED. Adopt only with an allowlist naming the success counters, or as a warn-only gate first.

## 50-F3 [P1] Raw UPDATE/DELETE executed without a row-count check

- Defect class: a write statement runs through `execute` (or `cursor.execute`) and the result is not checked. A `WHERE` that matches zero rows, or a stale id, passes silently. Consumers then report success for a change that never happened.
- Evidence: glossum_backend_scripts 3a72e8f0 "Route the proposed-lemma, existing-synset and quality-flag writes through execute_expecting_rows", 0d9cd241 "Route the validation relation UPDATEs through execute_expecting_rows", 577c92f4 "Route the validation delete statements through execute_expecting_rows", 49803872 "Route the wordfam verdict writes through execute_expecting_rows". Four separate write families were found silently accepting zero rows and were moved to a helper that asserts the row count. Grep of src for `rowcount`, `expecting_rows`, `affected` found hits only in plotly_annotation_loop, safe_precommit and spec_bound_doubles (none is a SQL write check); no gate enforces the helper.
- Minimal reproduction:
  ```python
  def mark_done(cur, job_id):
      cur.execute("UPDATE jobs SET state = 'done' WHERE id = %s", (job_id,))
      # job_id missing: zero rows, no error, caller logs success
  ```
- Detector idea: AST pass over string literals (or SQL text constants) starting with UPDATE/DELETE/INSERT ... passed as the first argument to `.execute`/`.executemany`/`text(...)` in non-test source. Pass if the call is wrapped in the project's row-count helper (configurable name, default `execute_expecting_rows`), or if the enclosing function reads `rowcount` or `.rowcount` in the same statement block, or the statement has `RETURNING`. Shrink-only baseline.
- False-positive risk: medium. `INSERT ... ON CONFLICT DO NOTHING` and idempotent deletes legitimately affect zero rows; these need a marker (`# zero-rows-ok: <reason>`). Dynamic SQL built by concatenation is out of reach and is skipped, not flagged.
- Proposed disposition: ADD as gate (`sql_write_rowcount_checked`), with the marker form above and a shrink-only baseline. Measure first on glossum_backend_scripts, where the helper already exists, so the gate's baseline shows the remaining unwrapped writes.

## 50-F4 [P3] Per-item log line inside a network page loop with no throttle

- Defect class: a warning or info log inside a per-page or per-item loop over a remote source. On a partially failing source the log volume grows with the page count and drowns the run.
- Evidence: social 82817970 "wayback: per-page failure logs throttled in the two hot loops; two baseline entries leave". Only the social repo shipped the fix; no gate in src covers log calls in loops (grep for `logging` in src gave gate modules that import logging, none that flags loop-body logs).
- Minimal reproduction:
  ```python
  for page in fetch_pages(url):
      try:
          parse(page)
      except ValueError as e:
          log.warning("page %s failed: %s", page.id, e)   # one line per page
  ```
- Detector idea: AST pass for `log.warning|error|info` calls inside a `for` or `while` body whose iterable is a call to a name matching `fetch|iter_|pages|scan|walk`, with no counter or throttle name (`throttle`, `_once`, `rate`) in the enclosing function.
- False-positive risk: high. Many loops legitimately log per item at low volume; the iterable-name heuristic is weak.
- Proposed disposition: DEFERRED. Better handled by a code-review check than a gate until a measured count exists.

## 50-F5 [P3] Test assertion depends on runner platform or pandas null representation

- Defect class: a test compares `str(x)` or a pandas frame's repr to a literal that differs between OS (path separators, line endings, `nan` versus `<NA>`, `None` versus `NaN` in object columns), so it passes on the author's machine and fails on the CI matrix.
- Evidence: pyutilz d246599 "Make three test groups independent of runner platform and pandas null representation"; pyutilz 0901b3a "Make doctests and the normality precondition pins hold on pandas 3 and Python 3.8". Existing machine_specific_paths covers absolute paths, not repr of nulls or path separators in assertion literals.
- Minimal reproduction:
  ```python
  def test_repr():
      assert str(pd.Series([None, 1.0])[0]) == "None"   # pandas 3 gives "nan" or "<NA>" depending on dtype
  ```
- Detector idea: in test files, flag `assert` comparisons where one side is `str(...)`, `repr(...)` or `.to_string()` of a value built from pandas/polars null constructors, or a string literal containing `\\` or a drive letter used with os.path comparisons.
- False-positive risk: medium. Literal repr checks are sometimes the point of the test; a marker should allow them.
- Proposed disposition: DEFERRED. The class is real but the detector needs the same allowlist design as the existing machine_specific_paths gate; evaluate that extension before a new module.

## 50-F6 [P3] Doctests and assertions that hold only on one pandas major

- Defect class: a doctest or pin written against one pandas major (object vs string dtype, copy-on-write semantics, changed default dtypes) fails when the matrix moves major version.
- Evidence: pyutilz 0901b3a (see 50-F5). The failing dimension is a dependency version matrix, not source text.
- Detector idea: none that is static. The only sound check is a CI matrix leg over pandas majors, which is a consumer workflow decision, not a py-ci-shared static gate.
- False-positive risk: not applicable.
- Proposed disposition: WON'T FIX as a static gate. Reason: the defect is observable only by running the test suite under two pandas majors; the correct home is the consumer's CI matrix, and pinned_tool_versions and api_floor already cover the version pins that py-ci-shared controls.

## Summary

- Candidates: 6.
- ADD as gate: 1 (50-F3, sql_write_rowcount_checked, P1).
- DEFERRED: 4 (50-F1, 50-F2, 50-F4, 50-F5), each needs a measured hit count or an allowlist design first.
- WON'T FIX: 1 (50-F6, needs a runtime matrix, not a static gate).
- Measured hit counts: none in this round. The 50-F3 gate should be measured on glossum_backend_scripts before the baseline is set.
- Caveats: autopsia and polyvocab_app were not scanned for fix commits; the grep checks for "already gated" were done on module names and README text, not on every gate's AST logic.
