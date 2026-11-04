# Plan, round 2026-11-04

1. Collect agent reports 30-35 (JSON in this folder), render each to .md, add rows to audits/TRACKER.md, every finding gets a disposition.
2. Fix defects in existing gates (dimension 33) and the CI/release surface (34), each with a test that fails before the fix.
3. Mutation harness speed (30): single warm baseline, warm re-check of survivors, killer persistence in the checkpoint, baseline-derived timeout, opt-in line coverage map. Measure on a small module.
4. Shared scan speed (35): parse cache in `_core`, missing self meta-tests.
5. New gates from 31 and 32 (registry, README row, canary, test, CHANGELOG), prototype hit counts first.
6. Generalise the fixed classes into shared meta-tests (owner rule 2026-10-12).
7. Local self-gates, then ONE push to master, wait for CI, no second push mid-run.
8. LAST: wire as many meta-tests as possible into EVERY Python repo active in the last six months (git log since 2026-05), and run each repo to green.
   Mutation tests (mutation_teeth) are excluded: the owner runs them by hand because they are too slow.
   Findings the new gates surface are fixed in code, not baselined.
