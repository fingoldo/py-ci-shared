# 30_mutation_speed

Round 2026-11-04. Every finding is OPEN until its Disposition line says otherwise.

## 30-F1 (P1) The unmutated suite is run twice per sweep (cold baseline, then warm baseline) and a third time per extra jobs partition; 31% of a small sweep

- Evidence: mutation_teeth.py:_sweep_in_sandbox -> _mutation_sweep._check_unmutated (_run_pytest(test_paths), _mutation_sweep.py:168) and then _sweep_mutants (mutation_teeth.py ~line 245) warm.run(test_paths, check_origin=True) as a SECOND full baseline; _sweep_partition verify_baseline=slot>0 adds a cold one per extra jobs partition (mutation_teeth.py:~215). MEASURED on a copy of _mutation_checkpoint.py (53 mutants, 2 test files, scratchpad/run1.py): total 283s; cold baseline 38.4s + warm baseline 50.8s = 89s = 31% of wall time before the first mutant; warm per-mutant kill averages 2.2s.
- Failure scenario: On autopsia (baseline ~290s) the harness pays ~290s cold + ~290s+ warm + 200s origin check (the 'fixed 9 min') on every file, and again for each jobs partition.
- Proposed fix: Run ONE baseline: in the warm path drop the cold _run_pytest baseline and judge the warm baseline (it already reports rc and origin; keep cold only as fallback when the warm baseline fails, which the code already handles). Run the cold-vs-warm consistency check only when the warm baseline fails. For jobs>1 start all partitions' warm workers in parallel and let each worker's own first run be its verify_baseline instead of an extra cold run. Expected saving 30-50% of fixed startup; preserves the 'baseline passes' guarantee.
- AST-checkable: False; FP risk: low
- Disposition: OPEN

## 30-F2 (P1) Every surviving mutant is re-run COLD on the full test_paths (survivor re-check): 5x the warm cost, 28% of a small sweep

- Evidence: _mutation_sweep.py:_primary_verdict lines ~99-107: `confirm = _run_pytest(test_paths, sandbox, timeout)` after any warm pass. MEASURED: 10 survivors, cold confirm 10.8s each vs 2.2s warm = ~80s of 283s ('recheck' budget 80.25s). Survivors cannot short-circuit with -x so they are also the longest warm runs.
- Failure scenario: On autopsia a survivor costs ~2 min warm-equivalent session plus a cold confirm of the whole suite plus the wider net; with 20% survivors, survivors dominate wall time.
- Proposed fix: Confirm in a SECOND long-lived warm worker (fresh process, same sandbox) instead of a cold subprocess; a fresh process is what the confirm is for (leaked state), and it pays collection+fixture load once, not per survivor. Or confirm with the files that ran only (survivor means all passed, so no -x help) in a pool of 2 persistent confirm workers. Keep one cold confirm per N survivors as spot check. Saving est. 60-75% of the recheck budget (80s -> ~20-30s here).
- AST-checkable: False; FP risk: med
- Disposition: OPEN

## 30-F3 (P1) No per-line coverage map: mutants on lines no test executes run the whole suite (and a cold confirm and the wider net) before being declared survivors

- Evidence: grep of _mutation_sweep.py/mutation_teeth.py: the only coverage notion is 'coverage_gaps' (wider net), nothing computes executed lines. _sweep_mutants iterates every mutant through _judge unconditionally. In the measured run 10 of 53 mutants survived (e.g. ck.py:38 continue->break, 52:44 True->False); each cost warm+cold confirm ~13s vs 2s for a typical kill.
- Failure scenario: Dead lines are the most expensive mutants, 6x a kill, and on autopsia each is ~4 min plus wider net.
- Proposed fix: During the (single, see F1) baseline run pytest under coverage (coverage run -m pytest or sys.settrace-less sys.monitoring) with dynamic_context=test_function and write {line: [test files]} for the target once, cached in the checkpoint keyed by fingerprint. Then (a) mutants whose line set is not executed by any test are survivors by construction (zero pytest runs; still verify with ONE batched run only if the user asks), (b) other mutants run only the test files that cover the line, ordered by hit count. Correctness: a mutant on an unexecuted line cannot be killed by a test except via import-time or filesystem/subprocess side effects not seen by coverage; keep multi-line statements (map by statement span, not by first line) and treat module-level lines and lines executed in subprocesses as covered by all files. Estimated saving: 15-25% of mutants skipped entirely and 50-90% fewer test files per remaining mutant on large suites; the baseline under coverage costs 1.3-2x once. Known false-survivor risk is real: a coverage-misses-subprocess case would label a killable mutant survivor, so gate (a) behind an opt-in `coverage_prune=True` until measured on autopsia.
- AST-checkable: False; FP risk: high
- Disposition: OPEN

## 30-F4 (P2) Killer file per line is not persisted; ordering state is per partition and per run

- Evidence: _mutation_checkpoint.py:record writes only {key, mutant, filed, budget}. state.ordered (_mutation_sweep.py:85 _lead_with(warm.last_failed)) lives in _SweepState, created per partition in _sweep_mutants, so jobs=3 learns three times and a resume starts from the configured order again. _lead_with moves only ONE file to the front (mutation_teeth/_mutation_runner.py:687) and the front file is the LAST killer, not the killer of the neighbouring line.
- Failure scenario: After resume, or in each of 3 partitions, the cheap fast-failing file is rediscovered only after one slow kill (~2-4 min on autopsia).
- Proposed fix: Add 'killer' to the checkpoint entry (first failing file already in warm.last_failed / _scan_of(...).first_failing_file); on load, seed state.ordered by killer frequency (Counter), and in the loop lead with the killer of the nearest previous mutant on the same/neighbouring line first, then global frequency. Pure ordering: it changes cost only, not verdicts, because every file still runs for survivors. Saving: removes ~1 slow kill per partition per resume; with F3 it becomes exact.
- AST-checkable: False; FP risk: low
- Disposition: OPEN

## 30-F5 (P2) Per-mutant timeout is a constant (300s default), not derived from the measured baseline

- Evidence: mutation_teeth.py:find_surviving_mutants signature timeout: float = 300.0; used for every mutant in _run_pytest and WarmRunner._reply_to (_mutation_runner.py:564 deadline=time.monotonic()+self.timeout). Baseline duration is measured but never fed back.
- Failure scenario: A mutant that loops forever (< -> <= loops, documented in _run_pytest) burns 300s then is INCONCLUSIVE; on a suite whose baseline is 20s that is 15x; on autopsia (baseline ~120s warm) 300s is too tight for survivors on a loaded host so it risks inconclusive.
- Proposed fix: Time the warm baseline (t_total from worker reply) and set timeout = clamp(max(30, 3*baseline_pytest), floor, user_cap) when timeout is None / 'auto'. Killed-by-timeout stays INCONCLUSIVE (unchanged semantics), so no false kill. Saving only on hang mutants (typically 1-3% of mutants x up to 300s each).
- AST-checkable: False; FP risk: low
- Disposition: OPEN

## 30-F6 (P2) Wider-net baseline and each wider-net re-check multiply fixed cost for files with fallback_test_paths

- Evidence: _mutation_sweep._check_unmutated:184 runs fallback_test_paths cold once more as baseline; _judge calls _wider_net for every primary survivor (docstring at _mutation_sweep.py:209-220 states it was already 88% of wall clock on the worst file).
- Failure scenario: Dead lines (F3) pay primary warm + cold confirm + wider net sequence: three whole-suite passes per survivor.
- Proposed fix: With the F3 line map, the wider net is only asked about lines that some test in the wider set covers; mutants no file covers skip it. Run the wider baseline only lazily on first survivor that needs it (many sweeps have zero survivors). Saving: wider baseline fully when no survivor; est. 30-60% of wider-net runs.
- AST-checkable: False; FP risk: med
- Disposition: OPEN

## 30-F7 (P2) Sandbox copy: shutil.copytree of the whole repo for every sweep and every extra jobs partition; ignore list is name-only

- Evidence: mutation_teeth.py:_sweep_in_sandbox copytree(repo_root, sandbox, ignore=_COPY_IGNORE, symlinks=False); _run_partitions copytree(sandbox, extra,...) per extra partition. _COPY_IGNORE (_mutation_model.py:41) skips only caches/.git/venv/node_modules. Known 44s on autopsia (not re-measured here; the scratch repo is too small).
- Failure scenario: Data directories, build outputs, .coverage files, parquet/model files not needed by the selected tests are copied each file.
- Proposed fix: (1) Extend the ignore list with dist/build/*.egg-info/.coverage*/htmlcov/*.parquet?no (tests may read data), so make the extra ignores a parameter `copy_ignore`. (2) For extra partitions copy only once and use directory junctions/hardlinks for read-only trees EXCEPT the target file's directory and any dir the tests write to; hardlinks are unsafe where tests write in place (would corrupt the verified sandbox), and write_target must unlink-before-write, so keep this opt-in. (3) Copy the sandbox once, then make partitions with `git worktree`-like cp --reflink where the FS supports it (not NTFS). Saving: at most the 44s once per file; only worth it for jobs>1 (N-1 copies).
- AST-checkable: False; FP risk: med
- Disposition: OPEN

## 30-F8 (P3) Already implemented, no action: -x, -p no:cacheprovider, twin dedup, lead_with ordering, checkpoint/resume

- Evidence: _mutation_runner.py:180 flags ('-q','--no-header','-p no:randomly','-p no:cacheprovider'); both _run_pytest (line 458) and warm run (line 553) append '-x'; _mutation_sweep.py:196 _group_twins dedups byte-identical mutants and _merge_verdicts fans verdicts out; _lead_with at _mutation_runner.py:687; SweepCheckpoint. Findings (c) -x, (g), (i) cacheprovider are therefore closed.
- Failure scenario: n/a
- Proposed fix: Not needed. Remaining of (i): addopts override. _pytest_flags does not pass `-o addopts=`; a consumer's addopts (e.g. --durations=200, -p plugins, timeout, --strict) still apply to every mutant run (memory: addopts apply to every run). Adding `-o addopts=` could cut per-run output/collection cost but changes which plugins/markers apply (cf. S-12 WON'T FIX on plugin autoload), so treat as per-consumer opt-in.
- AST-checkable: False; FP risk: low
- Disposition: OPEN

## 30-F9 (P3) Warm worker is 23% slower on autopsia but default use_warm_worker=True; no auto-fallback

- Evidence: find_surviving_mutants default use_warm_worker=True. On the small module it was clearly better: warm kill 2.2s vs cold survivor 10.8s (measured). Owner measured warm 23% slower on autopsia (app fixture load leaks/refits per run).
- Failure scenario: The default is wrong for fixture-heavy repos and the setting must be discovered by hand.
- Proposed fix: Compare warm baseline wall time with cold baseline wall time (F1 already measures both today: 50.8s warm vs 38.4s cold here, so warm was SLOWER at baseline and faster per mutant) and pick per-mutant mode from the first 3 warm kills vs the cold baseline; log the choice. Cheap, verdict-neutral.
- AST-checkable: False; FP risk: low
- Disposition: OPEN

## 30-F10 (P3) Not recommended: in-process importlib.reload mutation

- Evidence: _mutation_worker.py header (lines 12-27) documents that module state survives purge across runs and the harness re-verifies via a cold confirm; reload keeps references in other modules and breaks isinstance/class identity.
- Failure scenario: False survivors (stale refs to the original function) and false kills (half-reloaded classes).
- Proposed fix: Rejected for correctness; the worker's purge-local-modules approach is the safe in-process variant. Residual saving would be small (purge measured 3.7s total over 51 mutants).
- AST-checkable: False; FP risk: high
- Disposition: OPEN
