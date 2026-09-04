# 24 — cost: where the 8.5 s per mutant goes

Dimension: **cost**. Read-only audit; nothing implemented, nothing under `pcs-audit/src` or the
consumer changed. All probes ran from a private scratch directory against a throwaway sandbox copy
of the consumer.

Consumer: `C:\Users\Admin\Machine learning\social\upwork\new_scraper\realtime_applications`,
target `stage2_prompt_builder/user_prompt.py`, the four test files from `recheck_userprompt.py`
(67 collected tests, not 29 — the reported "2 test files / ~29 tests" baseline is the narrower
selection; every number below is on the 4-file selection the real sweep used).

## Measurement conditions — read this before trusting a single number

The machine was **not quiet**. Roughly 40-50 `python.exe` processes belonging to another session
were resident throughout, and two of my own probes overlapped early on. The effect is large and
visible: the identical warm run measured 2.05 s and 23.67 s in different probes. Every
load-bearing number below is therefore either

* a **paired A/B**, alternating the two variants back-to-back inside one process so both see the
  same contention (probe9, probe6), or
* a **median over repeats**, with the raw values printed so you can see the outliers.

Single-shot wall clocks (probe1, probe8) are reported only as context and are explicitly marked as
contaminated. I did not use them to size any proposal.

## The budget, reconciled

Per mutant, warm path, 4 test files. Quiet-window values.

| phase | measured | share of 8.5 s |
|---|---|---|
| `_purge_local_modules` scan | 2.3–2.9 s (median ~2.5) | ~29% |
| `pytest.main` — collection + re-import of the 84 purged modules | ~1.0 s | ~12% |
| `pytest.main` — actual test execution under `-x` | ~1.5 s | ~18% |
| survivor cold re-verification, amortised over the sweep | ~2.4 s | ~28% |
| IPC, two file writes, misc | ~1.1 s | ~13% |

One-time, per sweep: `copytree` 0.87–1.17 s; cold baseline run 15–27 s; `generate_mutants` 0.19 s.

Reconciliation check: probe6 measured the warm marginal at **5.52–5.64 s/mutant** with one worker
(purge 2.5 + pytest 2.5 + IPC). Add ~2.4 s/mutant of amortised survivor re-verification and you get
~8.0 s against the reported 8.5 s. The budget closes; there is no large unexplained residue.

A useful anchor for the whole report: `python -c "import pytest"` in a fresh subprocess is **0.55 s**
(probe10). A cold pytest run of these tests is **15.0 s**. So the 14.5 s difference is importing the
*consumer's* dependency stack — `sys.modules` reaches **3897 entries** after one run. That number is
the villain in findings 1 and 2 alike.

---

# Finding 1 — the purge scan calls `Path.resolve()` on all 3897 modules, every mutant

**Measured win: 2.3–6.3 s → 0.005–0.008 s per mutant. ~29% of the sweep. Zero correctness change,
proved by set equality.**

`src/py_ci_shared/_mutation_worker.py:43-62`, specifically line 56:

```python
if Path(file).resolve().is_relative_to(root):
```

`sys.modules` holds 3897 entries after one run of these tests; **84** of them are repo-local. So the
scan constructs 3895 `Path` objects and issues 3895 `resolve()` calls — each a real filesystem
round-trip on Windows — to find 84, and it does it again for every single mutant. The `__file__`
strings are identical every time.

## Measurement

`probe5.py`: warm the process with one full `pytest.main`, then alternate the shipped scan against a
memoised scan that keeps `Path(f).resolve().is_relative_to(root)` verbatim and caches the answer on
the `__file__` string.

```
mods 3897
slow=2.948 cached=1.9307 equal=True n=86     <- first pass, cache cold
slow=2.343 cached=0.0053 equal=True n=86
slow=6.336 cached=0.0080 equal=True n=86     <- contention spike
slow=5.756 cached=0.0060 equal=True n=86
```

Cross-checked in `probe4.py` under an alternating fast/slow schedule, which also confirms the
result set is byte-identical:

```
iter1 slow scan=1.747 ... n=86
iter3 slow scan=2.951 ... n=84
iter5 slow scan=2.087 ... n=84
iter2 fast scan=0.118 ... n=84
iter4 fast scan=0.118 ... n=84
EQUIV True 84 []          <- symmetric difference empty
```

Corroborated independently at small scale in `probe2.py`: **285** modules already cost 194–280 ms.

## Proposed change

In `_purge_local_modules`, keep a module-level `dict[str, bool]` keyed on the raw `__file__` string
and consult it before calling `resolve()`:

```python
_RESOLVED: dict[str, bool] = {}
...
hit = _RESOLVED.get(file)
if hit is None:
    try:
        hit = Path(file).resolve().is_relative_to(root)
    except (OSError, ValueError):
        hit = False
    _RESOLVED[file] = hit
if hit:
    doomed.append(name)
```

## Correctness risk: none that I can construct

The predicate is unchanged — same `resolve()`, same `is_relative_to`, same `root`. The only way the
cache can be wrong is if the *filesystem meaning* of a `__file__` string changes mid-sweep, i.e.
someone retargets a symlink or junction under the sandbox or under `site-packages` while the sweep
runs. The sandbox is a private `mkdtemp` nobody else writes, and `root` is resolved once at
`_mutation_worker.py:66`. Note the worker is one process per sweep, so the cache dies with it.

**Do not** replace `resolve()` with `os.path.abspath` + prefix match to get the same speed. I
measured that variant too (`probe2.py` `scan_fast` = 1.7–2.5 ms, `probe4.py` = 0.118 s) and it gave
an identical set here — but it does not follow symlinks, so it would silently under-purge a repo
reached through a junction, and under-purging is exactly the failure mode documented at
`_mutation_worker.py:12-15` that produces a **false SURVIVOR**. The memoised form gets the same
speed with none of that exposure. Take the cache, not the prefix match.

---

# Finding 2 — one warm worker, when four cost 2.9 s of extra copying

**Measured win: 5.52 s → 1.59 s per warm run with 4 workers in the quiet pair (3.5x); end-to-end
throughput 4.7x. Cost: 2.9 s of extra `copytree`, 2.7 GB of extra RAM.**

`src/py_ci_shared/mutation_teeth.py:776-782` argues against parallelism:

> A single worker is used rather than four. Four cold starts measured 39.3s against 19.2s for one
> ... four workers would each need their own tree copy because they mutate the same file. The
> measured single-worker gain is already 6.9x; the parallel variant's extra 1.2-1.6x is not worth a
> per-worker copy and a crash-recovery protocol.

Two of the three premises no longer hold, and I can show it:

* "**four cold starts**" is the wrong comparison. The question is four *warm* workers, where the
  interpreter and the 3897-module stack are paid once per worker for the whole sweep, not per
  mutant. A 38-mutant sweep amortises four cold starts over 38 runs.
* "**a per-worker tree copy**" is priced as if it were expensive. It is **0.87–1.17 s** for 394
  files / 5.0 MB. Three extra copies is 2.9 s against a sweep of 323–361 s.

## Measurement

`probe6.py`: N warm workers, each on its own `copytree` sandbox, each doing 5 identical runs of the
4-file selection. Two full A/B pairs, alternating N=1 and N=4 so both see the same background load.
`per_warm_run` excludes each worker's first (cold-import) run.

```
NW=1 copies=0.94s wall=55.3s runs=5  per_run=11.06s  per_warm_run=5.64s  rss=[914]MB
NW=4 copies=3.15s wall=89.9s runs=20 per_run=4.49s   per_warm_run=4.43s  rss=[913,912,914,914]MB
NW=1 copies=1.17s wall=50.5s runs=5  per_run=10.11s  per_warm_run=5.52s  rss=[913]MB
NW=4 copies=2.91s wall=43.2s runs=20 per_run=2.16s   per_warm_run=1.59s  rss=[912,913,913,912]MB
cores phys 16 mem GB 136.9 avail GB 78.8
```

The first pair (1.27x) was taken during heavy external load; the second (3.47x per warm run,
4.7x on end-to-end throughput — 20 runs in 43.2 s versus 5 runs in 50.5 s) is the quiet one. **I am
citing 3.5x, the quiet pair, and flagging that the noisy pair says 1.27x.** Repeat this on an idle
machine before committing to a number.

Copy sizing (`probe1.py` / independent walk): 394 files, 5,008,041 bytes, `copytree` 0.75–1.17 s.
`__pycache__`, `.git`, `.venv` are all excluded by `_COPY_IGNORE` at `mutation_teeth.py:120-123`.

## Constraint check — I measured each candidate blocker rather than assuming

* **Sandbox copy**: 0.87 s each. Not a constraint.
* **Memory**: 913 MB RSS per warm worker, stable across runs (no leak). 4 workers = 3.7 GB against
  78.8 GB available. Not a constraint.
* **DB / network fixtures**: `realtime_applications/tests/conftest.py:96` sets
  `UPWORK_DB_DSN` to a placeholder `postgresql://test:test@localhost:5432/test` and
  `conftest.py:168-190` resets a mocked pool (`db.pool._reset_pool`, `MagicMock` cursors). No live
  server is touched. Not a constraint *for this consumer*.
* **Cores**: 16 physical. The project convention of a quarter gives 4, which is what I measured.

## Proposed change

A `jobs: int = 1` parameter on `find_surviving_mutants` (`mutation_teeth.py:886`), defaulting to 1
so no existing caller changes behaviour, and set to 4 by the consumer's own call site. Implementation
shape: N `mkdtemp` sandboxes, N `_WarmRunner`s, mutants dealt round-robin, results joined and
re-sorted into source order before building `MutationRun`.

## Correctness risk: real, and the reason this must be opt-in

* **Shared external resources.** Each worker gets its own sandbox, so the mutated file is never
  shared — that hazard is gone. What is not gone is anything the consumer's tests reach *outside*
  the tree: a fixed TCP port, a real database, a fixed path under `%TEMP%`, an API. I verified this
  consumer is clean (above). I cannot verify it for the next consumer, and a test suite that
  collides with itself under concurrency produces **false KILLs** — the direction that hides a real
  defect, per `_mutation_worker.py:16-18`. Hence `jobs=1` default and a docstring that says
  precisely this.
* **Crash recovery.** The existing single-worker fallback (`mutation_teeth.py:979-986`) already
  degrades a dead worker to a cold subprocess. That logic generalises per worker; it does not need
  a new protocol, contrary to the docstring's claim.
* **Result ordering.** Survivors must be re-sorted into source order before reporting, or the
  survivor list — and therefore the cached baseline keyed off it — becomes nondeterministic. This is
  a correctness bug waiting to happen in the implementation, not in the idea.

## Also fix the docstring

Independently of whether parallelism ships, `mutation_teeth.py:776-782` states a conclusion that my
measurements contradict, and it is the kind of confident, numbered comment that stops the next
reader from re-measuring. It should at minimum record that the 39.3s/19.2s figure was cold-start
and that the per-worker copy is under a second.

---

# Finding 3 — the test files run in the caller's order; putting the likely killer first saves 22.6%

**Measured win: 2.529 s → 1.957 s median on the pytest phase, −0.572 s per mutant (−22.6% of the
phase, ~6.7% of the 8.5 s sweep). No correctness cost at all.**

`mutation_teeth.py:809` builds the worker's argv as `test_paths` verbatim, and `-x` is appended.
pytest executes files in argv order, so the caller's list order decides how many tests run before
the kill. Nothing in the harness reorders or learns.

## Measurement — how late is the killing test?

`probe7.py` runs the real `generate_mutants` output (38 mutants from 40 candidates on the changed
lines) and records, per mutant, the position of the first failing test among the 67 collected:

```
m0  ran=2/67   m1  SURVIVED 67/67   m2  ran=1/67   m3  ran=66/67
m4  ran=42/67  m5  ran=42/67  m6  ran=50/67  m7  ran=44/67  m8  ran=44/67
m9  ran=53/67  m10 ran=51/67  m11 ran=42/67  m12 ran=42/67  m13 ran=52/67
killed 13 of 14
mean tests executed before kill 40.85
```

**`-x` is already working** — 40.85 of 67 is 61%, not 100%. But the distribution is strongly
clustered: 10 of the 13 kills came from `test_attachment_budgets_are_bounded.py`, which the caller
listed **third**. `probe8.py`, identical but with that file listed first:

```
mean tests executed before kill 11.62
```

40.85 → 11.62, a 3.5x reduction in tests executed. (probe8's wall clocks are contaminated — it ran
during the external load spike, m3 took 23.67 s — so I did not use them.)

## Measurement — what that is worth in seconds

`probe9.py`, paired: for each of 10 mutants, run ordering A (as shipped) and ordering B (budgets
first) back-to-back in the same warm process, twice over.

```
A median 2.529  mean 2.623  n=20
B median 1.957  mean 1.968  n=20
```

B beat A on **20 of 20** pairs. Median delta −0.572 s.

## The variant that looked better and measured worse

I also measured `ONLY` — run just the single most-likely file, and fall back to the full set only if
it does not kill:

```
ONLY median 1.563  mean 1.660  n=20
```

Faster per attempt, but from probe7 the likely file fails to kill for 4 of 14 mutants (3 killed
elsewhere, 1 survivor), so expected cost is `1.563 + (4/14) x 2.529 = 2.29 s` — **worse than plain
reordering at 1.96 s**, and it adds a second collection. Rejected on measurement, not on taste.

The reason neither variant does better is that collection is charged in full regardless: `--co` over
all four files is 0.73 s of collection inside a 2.97 s call. Only ~1.5 s of the phase is executable
test time, so ~1.5 s is the entire ceiling for any ordering trick.

## Proposed change

Sort `test_paths` before use so the file that killed the previous mutant goes first — a single
`str` remembered across the loop at `mutation_teeth.py:976-1027`, applied at line 809. Locality is
high (mutants are generated in source order, and neighbouring lines are killed by the same file), so
a one-entry memory captures most of the win.

Getting the killer's identity requires the worker to report the failing nodeid's file, which today
it discards (`_mutation_worker.py:90-93` returns only `rc`). Cheapest sound version: have the worker
attach a tiny `pytest_runtest_logreport` plugin recording the first failure's filename and return it
alongside `rc`. That is what `probe7.py` does; it costs nothing measurable.

## Correctness risk: none

The same set of tests is selected, the same `-x` semantics apply, and pass/fail of the *set* is
order-independent unless the consumer's tests have order dependencies — which `-p no:randomly`
(`mutation_teeth.py:736`) already exists to hold fixed, and which would equally break the shipped
order. The heuristic only changes *which* test reports the kill first, never whether a kill happens.
If the reordering makes an unmutated baseline fail, that is a real order-dependency bug in the
consumer that the harness should surface, not hide.

---

# Finding 4 — every survivor costs a full 15 s cold process, serially

**Measured: 15.0 s per cold re-verification. At ~6 survivors on this file that is ~90 s, ~28% of the
sweep, and it is ~2.4 s/mutant of the reported 8.5 s.**

`mutation_teeth.py:996-1010`: a warm survivor is re-verified in a cold subprocess, and if
`fallback_test_paths` is set, a *second* cold subprocess runs the wider set.

I am **not** proposing to remove or weaken this. The reasoning at `_mutation_worker.py:25-29` is
sound, and a false survivor is the outcome that wastes a human's afternoon. The cost is the problem,
not the check.

## Measurement

`probe10.py`, sandbox with warm `__pycache__`:

```
COLD warm-pycache 14.98  67 passed in 1.52s
COLD warm-pycache 15.3   67 passed in 1.89s
COLD warm-pycache 36.3   67 passed in 2.13s     <- contention spike, discarded
interp only      0.23
interp+pytest    0.55
```

15.0 s of which pytest reports 1.5–1.9 s as tests, and only 0.55 s is interpreter + pytest import.
The remaining ~13 s is importing the consumer's 3897-module stack — unavoidable in a cold process,
which is the entire point of the check.

## Proposed change

Two options, neither weakening the check:

1. **Free, if Finding 2 ships.** Defer confirmations: collect survivor candidates during the sweep,
   restore the file, and run all confirmations at the end across the N sandboxes concurrently. 6
   survivors x 15 s serial = 90 s becomes ~30 s at jobs=4. Requires re-applying each candidate's
   `mutated_file_text` in its assigned sandbox — the text is already retained on every `Mutant`.
2. **Standalone.** Start one dedicated cold-verify subprocess in the background at the moment a
   survivor is found and keep the warm loop moving, joining before the result is assembled. This
   overlaps ~15 s of the tail with ~3 mutants of forward progress.

## Correctness risk: option 1 is where the risk lives

Deferring means each confirmation re-applies a mutation to a sandbox that other mutants have since
touched. The current loop restores `original` after every mutant (`mutation_teeth.py:1027`), so the
tree is clean — but a deferred confirmation that ran in a sandbox where a *different* mutant was
still applied would be silently, catastrophically wrong in both directions. If this is implemented,
the confirmation pass must write `original` first and assert the file matches the intended mutant
text before running. Option 2 has the same hazard in sharper form (the warm loop is mutating the
same file concurrently) and therefore needs its own sandbox for the verifier — at which point it is
just option 1 with extra steps. **Prefer option 1, gated behind the same `jobs` flag.**

---

# Finding 5 — `__pycache__` is excluded from the copy, costing ~12 s once per sweep

**Measured: 27.1 s for the first pytest run in a fresh sandbox versus 15.0 s once bytecode exists.
~12 s, one-time, ~3.5% of a 340 s sweep. I recommend accepting most of this rather than fixing it.**

`_COPY_IGNORE` at `mutation_teeth.py:120-123` excludes `__pycache__` and `*.pyc`, so the first run in
the sandbox — the unmutated baseline at `mutation_teeth.py:954` — recompiles every module the tests
import.

## Measurement

`probe10.py`:

```
COLD cold-pycache (1st in fresh sandbox)  27.14   67 passed in 9.06s
COLD 2nd in same fresh sandbox            31.7                        <- contention, discarded
pycache dirs created 8
```

Note the reported *test* time is 9.06 s on the first run against 1.5 s on a warm-cache run: the
compile cost lands inside collection and import. The second-run figure is contaminated and I am not
citing it; the honest claim is "first run in a fresh sandbox cost 27.1 s where a bytecode-warm run
costs 15.0 s", i.e. ~12 s.

## Proposed change, and why I am lukewarm

Copying `__pycache__` would remove it, and Python's mtime+size invalidation would in principle keep
it honest. I am **not** recommending that. Stale-`.pyc` bugs in this environment are documented and
they fail silently — the harness would run the *pre-mutation* bytecode and report a false SURVIVOR,
which is precisely the class of bug `_mutation_worker.py:12-15` was written to eliminate. Trading a
3.5% one-time win for exposure to that class is the wrong side of the ledger.

The safe version: after `copytree`, run `compileall` over the sandbox with `workers=0` (all cores).
That is a parallel compile of 394 files instead of a serial one inside the first pytest run. I did
not measure it, so **this finding ships with a number for the problem and no number for the fix** —
it needs a measurement before anyone acts on it. Flagging it rather than dropping it.

---

# Finding 6 — the baseline run could use the worker (~15 s once), but should not

`mutation_teeth.py:954` runs the unmutated baseline through `_run_pytest`, a cold subprocess, before
the worker starts at line 976. Starting the worker first and running the baseline through it would
save one cold start, measured at 15.0 s, ~4.4% of the sweep.

**I recommend against it.** The baseline's job is to establish that the tests pass in a clean
process before any mutant result is believed (`mutation_teeth.py:955-964`). Running it inside the
warm worker means the very first thing the worker's module purge has to be trusted for is the
measurement that validates everything else — and if the purge is subtly wrong, the baseline is the
one run that would have caught it. Recorded as a real 4.4% that I am declining on correctness
grounds, not overlooking.

---

# Measured non-findings — things that looked expensive and are not

Recording these so nobody re-measures them.

* **`_pytest_flags()` re-runs `importlib.util.find_spec` twice per mutant** (`mutation_teeth.py:737-740`,
  called from `:809` and `:751`). Looks like per-mutant filesystem work. Measured (`probe5.py`):
  **0.01–0.02 ms** for both calls — `find_spec` hits the already-populated `sys.path_importer_cache`.
  0.0002% of a mutant. Not worth a line of code.
* **`generate_mutants` re-parsing the source per candidate.** It calls `ast.parse(mutated)` on the
  full file for every candidate (`mutation_teeth.py:530`). Measured (`probe7.py`): **0.191, 0.210,
  0.168, 0.184 s** total for all 38 mutants from 40 candidates — and it runs **once per sweep**, not
  per mutant. 0.06% of the sweep. Leave it.
* **`copytree` per sweep**: 0.75–1.17 s (three repeats), 394 files / 5.0 MB. Already once-per-sweep,
  not per-mutant. 0.3%.
* **Two full-file writes per mutant** (mutate at `:978`, restore at `:1027`). ~15 KB each; under a
  millisecond. The restore immediately before the next mutant's write is strictly redundant, but the
  saving is unmeasurable and the redundancy is what makes the loop safe to interrupt. Leave it.
* **`sys.modules.pop` of the 84 doomed modules**: effectively free. My first instrumentation
  (`probe3.py`) appeared to show 1.6–2.8 s here, but that interval accidentally enclosed a second
  scan; corrected in `probe4.py`, the pop itself is ~0. Recording the error so the 1.6–2.8 s figure
  in `probe3.log` is not cited by someone reading the raw logs.
* **`-p no:cacheprovider` and `--no-cov` are already passed** (`mutation_teeth.py:736-741`). There is
  no win left there. `--co` caching is not applicable: the harness needs execution, and collection is
  0.73 s of a 2.97 s call — it would be re-validated against the mutated file anyway.

# Combined effect

Findings 1, 3 and 2 are independent and compose. Per-mutant, from the measured 8.5 s:

* Finding 1 (purge memoisation): −2.5 s → **6.0 s**
* Finding 3 (killer-first ordering): −0.57 s → **5.4 s**
* Finding 4 (parallel confirmations, needs Finding 2): −~1.8 s amortised → **3.6 s**
* Finding 2 (jobs=4): the remaining per-mutant work divides ~3.5x → **~1.0–1.6 s effective**

38 mutants: **323–361 s → roughly 60–90 s**, plus the ~27 s cold baseline that none of these touch.
Findings 1 and 3 alone, with no concurrency and no new failure modes, take it to ~210 s.

Caveat repeated: the machine was loaded throughout. Findings 1 and 3 rest on paired or
many-repeat measurements and I consider their sizes solid. Finding 2's 3.5x rests on one quiet A/B
pair whose noisy twin said 1.27x — re-run `probe6.py` on an idle machine before quoting it.

# Probe scripts

All under `<scratchpad>/work/`, none inside the audited tree or the consumer:
`probe1.py` (cold vs warm wall clock), `probe2.py` (purge scan at 285 modules), `probe3.py`
(first, flawed phase split), `probe4.py` (corrected phase split + scan equivalence),
`probe5.py` (memoised scan A/B, find_spec), `probe6.py` (N-worker parallel A/B, RSS),
`probe7.py` (kill position, shipped order), `probe8.py` (kill position, reordered),
`probe9.py` (paired ordering A/B/ONLY), `probe10.py` (cold subprocess, bytecode penalty).


**DISPOSITION -- PARTIALLY RESOLVED.** Findings 1 and 3 are implemented, both measured and both without a correctness cost.

**Finding 1 (purge scan, ~29% of a sweep) -- RESOLVED.** The per-`__file__` verdict is memoised, so `Path.resolve()` runs once per module rather than once per module per mutant.

**Finding 3 (test ordering, -22.6% of the pytest phase) -- RESOLVED.** The worker now reports which FILE failed first, through a small pytest plugin rather than by parsing output -- re-enabling the report to scrape a filename would put pytest's text back on the protocol channel that already broke this worker once. The caller leads with the previous killer. It cannot change a verdict: a kill is a kill whichever file produces it, and a survivor still requires every listed test to pass.

**Finding 2 (parallelism, claimed 3.5x) -- DEFERRED, and not for timidity.** The report's own measurement conditions say the machine was not idle (40-50 foreign python processes; the same run measured 2.05s and 23.67s), and it says the quiet A/B pair gave 3.5x while its noisy twin gave 1.27x. Benchmarking on a loaded machine measures the load. The finding also carries a real correctness risk it names itself: a consumer whose tests share a port or a database gets FALSE KILLS, which is the direction that hides defects. Waiting on: a re-run of `probe6.py` on a quiet machine, and a default of `jobs=1`.

**Finding 4 (parallelise survivor confirmations) -- DEFERRED**, dependent on finding 2.

**Finding 5 (`__pycache__` in the copy) -- WON'T FIX.** Copying `.pyc` files to save ~12s once risks a stale bytecode entry silently reverting a mutation, which is a false SURVIVOR -- twelve seconds against the harness's whole purpose. The report reaches the same conclusion.

**Finding 5's second half (warm baseline saves 15s) -- REJECTED.** It argues that running the baseline warm destroys the cold check that validates the purge. The argument is right, and this round did the opposite of what it warns against: a warm baseline was ADDED alongside the cold one (22-F2), not substituted for it. The cost is one extra run per sweep, against a class of failure -- 'all killed, no survivors' with a green cold baseline -- that nobody investigates.

**Finding 6 (measured non-findings) -- ACKNOWLEDGED.** Recorded here because a measured non-finding is worth as much as a finding: it stops the next round proposing the same thing. Of particular note, the agent corrected its own instrumentation error between probes 3 and 4 rather than reporting the first number.
