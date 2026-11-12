# Mutation sweep test cost (tracker S-12)

Scope: `tests/test_mutation_teeth.py`, `tests/test_mutation_teeth_regressions.py`, `tests/test_mutation_worker.py`,
`tests/test_mutation_fingerprint_python_files.py`, `tests/test_mutation_harness_version_is_bumped.py`.
Baseline durations: `audits/2026-10-21/prof/before_durations.txt` (173 passed in 190.5 s, `-n 0 --no-cov`).
Profiles: the binary cProfile dump of the four_workers run was not kept (it was not committed; the figures below are from the run it recorded). Measurement script: `scripts/exp_startup.py`.

### S-12 (Low) -- Mutation sweep tests spend their time in cold pytest children, not in the harness

**Disposition:** Diagnosed; no test or source change made. The lever is the child-process startup in `_mutation_runner.py`
(`_pytest_env` / `_pytest_flags`), which this task may not edit, and no test-side change keeps every assertion and the
verdicts the tests prove. Proposed harness change below; parent to decide.

**Before (measured):**

| Test | Duration (suite, `-n 0`) |
| --- | --- |
| `TestConcurrencyChangesSpeedAndNothingElse::test_four_workers_reach_the_same_verdict_as_one` | 79.00 s (94.8 s under cProfile) |
| `TestConcurrencyChangesSpeedAndNothingElse::test_survivors_come_back_in_source_order` | 31.48 s |
| `TestAMutantThatStopsPytestStartingIsAKill::test_end_to_end_a_slots_mutation_is_killed_rather_than_refusing_the_file` | 13.22 s |
| `test_regressions::TestTheImportMustComeFromTheSandbox::test_end_to_end_a_shadowing_copy_is_detected` | 10.71 s |
| worker-protocol and process-state tests in `test_mutation_worker.py` | 6.8-8.5 s each |

**What the profile shows (four_workers, cProfile):**

- Time is wall-clock waiting on child processes, not Python CPU. `_run_pytest` accounts for 83.9 s cumulative over 15 calls
  (about 8 s each). Self time is almost all pipe reads (`TextIOWrapper.read/readline`, 62 s + 27 s), i.e. waiting on children.
- Breakdown of the serial sweep on the same fixture (`find_surviving_mutants`, `limit=8`): `pytest` 5.4 s for 6 mutants,
  `recheck` 41.7 s. Recheck is the cold confirmation run that `_primary_verdict` (`_mutation_sweep.py:102`) performs for every
  survivor. The fixture has 5 survivors out of 6 mutants, so 5 cold children run.
- Each cold child costs about 6.7-8.5 s on this machine, against 0.9 s with plugin autoload disabled
  (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, measured with a direct subprocess). Individual plugin timings are noisy on this
  loaded machine; `dash` alone was the largest single saving observed (about 3 s), so no single plugin is the whole cost.

**Why no test-side change was made:**

- Fewer mutants or fewer survivors would reduce how much the tests prove (the survivor-order test needs several survivors
  across lines). Those are assertion-level weakenings, which the task forbids.
- Setting `PYTEST_DISABLE_PLUGIN_AUTOLOAD` from the test environment does not work as is: `_pytest_flags()` still adds
  `-n 0` whenever `xdist` is importable, and with autoload off pytest exits 4 (unrecognised argument). Monkeypatching the
  flags inside the tests would stop them exercising the real isolation flags, which is what they are meant to prove.

**Measured effect of the proposed harness change (scratch experiment, not committed):** same fixture, child pytest run with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and flags `-q --no-header -p no:randomly -p no:cacheprovider` (no `-n 0`), applied by
monkeypatch in `scripts/exp_startup.py`:

| Sweep on the four_workers fixture | Before | Trimmed child startup | Verdicts |
| --- | --- | --- | --- |
| `jobs=1` | 64.4 s wall | 6.2 s | run 6, killed 1, survivors `[2,8,8,8,8]` both |
| `jobs=4` | 41.8 s wall | 4.2 s | run 6, killed 1, survivors `[2,8,8,8,8]` both |

**Proposed harness change (for the parent; `src/py_ci_shared/_mutation_runner.py`, which has the pending marker edit):**

1. In `_pytest_env()`, set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` for nested runs, and in `_pytest_flags()` add `-p xdist` / `-p pytest_cov`
   explicitly when they are installed, instead of relying on autoload. That keeps `-n 0` valid and makes the child set
   explicit rather than inherited. This must be checked against consumers whose conftests depend on an autoloaded plugin
   (a mutant run that loses a plugin would be refused with exit 4 or, worse, misread), so run the full mutation suite and a
   consumer sample before landing it.
2. Alternatively, keep autoload and profile which plugin dominates (`python -X importtime` per child), then block only that one.

Either change touches `_mutation_runner.py`, which is under the mutation-harness content hash, so it needs a harness baseline
refresh. `HARNESS_VERSION` was not changed.

**After:** no repository test or source change, so the suite durations are unchanged (four_workers 79.0 s, survivors-order 31.5 s).
The trimmed-startup numbers above are the expected effect of proposal 1, not a measured repo change.

## Files changed

- `audits/2026-10-21/mutation_profile.md` (this report)
- `audits/2026-10-21/prof/before_durations.txt`, `scripts/exp_startup.py` (measurement evidence; the four_workers `.prof` dump was lost and is not in git)

No file under `src/` or `tests/` was modified. No harness content-hash file was touched. Ruff, mypy and the test runs were
not needed for a changed-file set, since none exists.
