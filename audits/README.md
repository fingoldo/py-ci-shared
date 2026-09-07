# Audits of `py_ci_shared`

Read-only agents, one per dimension, run in parallel. The rules below are not optional and each of
them was earned by something going wrong.

## Layout

```
audits/
  README.md                  this file, spans all rounds
  TRACKER.md                 one row per finding, spans all rounds
  <yyyy-mm-dd>/              ONE FOLDER PER ROUND, named for the day it ran
    20_silent_under_reporting.md
    21_cache_soundness.md
    ...
  implemented/
    <yyyy-mm-dd>/            a fully-dispositioned round keeps its date when it moves
```

The DATE identifies the round, so the NUMBER stays tied to the dimension: `21` is cache soundness
in every round. That is what stops the `_round2` suffix treadmill and stops two parallel agents
claiming the same unused number.

## How a finding is handled

- **Every finding gets worked, including P3-Low.** "Low" is a severity, not a permission to skip.
  A finding is closed by a disposition and never by being ignored.
- **The disposition is written into the finding itself** -- `RESOLVED` with what was done,
  `WON'T FIX` with the reason, `DEFERRED` with what it is waiting on. The file has to be readable
  on its own, months later, by someone who was not here.
- **Findings are never deleted.** The record of what was considered and rejected is the point, and
  it is what stops the next round re-proposing it.
- **A file whose findings are all dispositioned moves to `implemented/<date>/`.** Its tracker rows
  stay.

## Discipline this subject has specifically taught

- **Verify a P0 yourself before acting on it.** Round 2026-09-04 opened with a P0 stating that the
  sandbox is import-shadowed so mutations never execute. The refutation was already in hand: the
  same day's sweep had killed 37 of 38 mutants, which is impossible if no mutation is applied. The
  finding still had a true residue -- a `src/` layout really is shadowed -- and that residue was
  worth fixing. Both halves matter: the claim was wrong, and dismissing the whole finding would
  have been wrong too.
- **A demonstration beats a reading, and a demonstration can still be wrong.** One agent
  demonstrated a stale-`.pyc` revert that two attempts here could not reproduce, including with
  size and mtime forced identical. It is recorded as NOT REPRODUCED and mitigated anyway, because
  the mitigation is free. Recording it as fixed would have been a lie about the state of the code.
- **The harness's own output is a finding surface.** Two of this round's dimensions found nothing
  wrong with what the harness MEASURES and a great deal wrong with what it SAYS -- a truncation
  banner that fires when nothing was truncated, a zero-mutant run that prints the same sentence as
  a clean one, a cache replay that reads better than the run it replays. A measurement nobody can
  act on correctly is not a measurement.
- **Fixing a check can break the check.** The `truncated` flag was widened in this round and
  immediately began to over-report, because the candidate counter incremented before invalid
  candidates were discarded. Found by testing the fix on real files rather than on the example that
  motivated it.
- **Escapes through a shell heredoc arrive mangled.** Three times in one round: a `\x00` that
  became a real NUL byte and made a source file binary, and a `\b` that became a backspace and made
  a regex match nothing. Both were caught by checking the RESULT, not by any test. Patch scripts go
  in files.
