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
| 25-F6 | P1 | Nothing ever runs the sweep | RESOLVED IN PART |
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
