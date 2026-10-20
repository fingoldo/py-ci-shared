# Follow-ups from the meta-suite speed work and the self code audit (2026-10-19)

Source of the items: the py-ci-shared self code audit (`pyutilz.dev.code_audit` over `src/py_ci_shared`), and the test-speed
work on this package. Each item below carries its disposition; the rows in `audits/TRACKER.md` point here.

## Test speed (measured)

- Full suite, serial, before the work: 24 min 35 s (8035 tests).
- Full suite on one xdist worker after it (the memory cap on the measuring machine picks one): 8 min 32 s (8060 tests).
- The biggest single fixes: pytest-randomly reseeders were loaded in sessions that do not use pytest-randomly (about 7 s per
  start: thinc, torch, cupy); one Postgres server per session instead of one per module; the parse cache's stat fast path.
- Not yet done: the mutation sweep's own cost (`test_four_workers_reach_the_same_verdict_as_one` about 33 to 77 s, depending on
  load; `test_survivors_come_back_in_source_order` about 17 to 31 s). Each is a real sweep in a sandbox, so the cost is the
  sweep, not the harness startup.

## Self code audit (P1 and P2)

- P1 `bare_except` x3 (`_core/runner.py`, `_mutation_worker.py`, `mutation_teeth.py`): each re-raises `KeyboardInterrupt` /
  `SystemExit` above the broad clause, or collects the exception for the caller to re-raise. The detector reads no
  `audit-exempt` marker for this check, so the finding stays open until the detector takes one or the clause is narrowed.
- P1 `busy_retry_loop` (`_mutation_runner._reply_to`): the loop waits on a blocking queue read with the deadline; it does not
  spin. The detector looks for a sleep in the handler and reads no exemption for this check.
- P1 `sentinel_guard_mismatch` (`sibling_floor_skew`): a false match between two functions. `version_at` does not call
  `find_sibling_floor_skew`. No exemption for this check either.
- P2 `duplicate_function_body` (56 groups): extract shared helpers, one pass per group.
- P2/Low `default_via_or` (about 180): most are `or 1` on a line number (done for `exc.line`, 28 sites), the rest are
  defaults on values that may legitimately be falsy; each needs a look at the caller.
- Advisory C901 (62 functions over 10): in CI this is `lint-advisory`, not blocking. Two of them (`_sweep_mutants`,
  `find_surviving_mutants`) were split in 1.23.0.

## Gates on themselves

- Every corpus-taking `assert_*` gate of the package run on `src/py_ci_shared` and its tests, classified: most take `files`,
  `root` or `repo_root`. The applicable ones should be enabled in `[tool.py_ci_shared]` so `tests/test_self_gates.py` covers
  them. Not started.
