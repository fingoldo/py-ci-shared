# Audit 22 — false kills in `mutation_teeth`

Dimension: every way a mutant is recorded as KILLED by something other than a test legitimately
detecting the defect. Read-only audit; nothing was changed.

Files: `src/py_ci_shared/mutation_teeth.py` (1176 lines), `src/py_ci_shared/_mutation_worker.py`
(100 lines), `tests/test_mutation_teeth.py` (453 lines).

Excluded by instruction (already fixed): `ast.col_offset` byte offsets, exit codes 2/3/4/5 read as
killed, the worker's stdout doubling as the protocol channel, coverage-map gaps.

---

## F1 — P0 — The sandbox copy is import-shadowed by the editable install, so the purge is exactly the half-measure the worker's own docstring says produces false kills

**DISPOSITION -- REJECTED IN PART, RESOLVED IN PART.** The headline claim -- the sandbox is import-shadowed, so mutations never execute -- is false for a consumer, and the evidence was already in hand before I checked: the same day's sweep killed 37 of 38 mutants, which is impossible if no mutation is applied. Verified directly: consumer modules resolve from the working directory, so `cwd=sandbox` imports the sandbox copy. What IS true is the narrower case the report reached through: a `src/` layout installed editable resolves to the real tree, so the harness cannot mutate its own package. That is fixed under 21-F3, and its failure mode is loud (every mutant survives), not a false kill.

**file:line** — `mutation_teeth.py:945` (`shutil.copytree`), `mutation_teeth.py:961`
(`_run_pytest(test_paths, sandbox, ...)`), `_mutation_worker.py:67`
(`sys.path.insert(0, str(root))`), `_mutation_worker.py:43-62` (`_purge_local_modules`).

**Claim.** This project is a `src/` layout installed in editable mode, so a pytest run whose cwd is
the sandbox still imports `py_ci_shared` from the ORIGINAL working tree; the purge, keyed on the
sandbox root, therefore drops the test modules (which do live under the sandbox) while leaving every
production module imported — the precise "purging production modules but leaving test modules
imported" inversion that `_purge_local_modules`'s docstring names as the cause of a false kill.

**Scenario.** `pyproject.toml` declares `[tool.setuptools.packages.find] where = ["src"]`.
`_WarmRunner.start` inserts the sandbox ROOT on `sys.path`, not `sandbox/src`, so
`import py_ci_shared` misses the sandbox entirely and resolves through the installed distribution
to `C:\Users\Admin\Machine learning\py-ci-shared\src\py_ci_shared\`. Consequences, in order of
severity for this dimension:

1. Mutations written to `sandbox/src/py_ci_shared/<target>.py` are never executed. Every mutant is
   a false SURVIVOR (out of dimension, but it is the same root cause and it is the louder half).
2. `_purge_local_modules(sandbox_root)` matches only files under the sandbox. Test modules,
   collected from `sandbox/tests/`, ARE purged. `py_ci_shared.*` is NOT — its `__file__` is under
   the real tree. So the warm worker re-imports fresh test modules against production modules that
   have been resident since the first mutant, carrying whatever module-level state the first
   mutant's run left behind. That is a stale-state channel with no bound on it.
3. If the operator "fixes" (1) by adding `sandbox/src` to `sys.path` without also fixing the purge
   root, the asymmetry in (2) persists and becomes the dominant error mode: production modules
   pinned at mutant 1, test modules fresh every time.

Note also that the sandbox target is resolved as `sandbox / relative` and only checked for
existence (`mutation_teeth.py:947`). Nothing anywhere verifies that the file the tests actually
import is the file being mutated.

**How verified — DEMONSTRATED.** In a scratch directory outside the audited tree, a sandbox-shaped
copy with `tests/test_x.py` doing `import py_ci_shared.mutation_teeth as m` and printing
`m.__file__`, run with cwd = sandbox and the harness's own flags:

```
IMPORTED FROM: C:\Users\Admin\Machine learning\py-ci-shared\src\py_ci_shared\mutation_teeth.py
```

Also confirmed `importlib.util.find_spec("py_ci_shared").origin` points at the real tree and no
editable meta-path finder is installed (a plain `.pth`/`site-packages` path entry, which
`sys.path.insert(0, root)` cannot outrank for a `src/` layout because `root` does not contain the
package).

**Remedy.** Make the sandbox authoritative and prove it: insert the sandbox's package root (derive
it from the target path — walk up past `__init__.py` files, or read `tool.setuptools.packages.find`)
at `sys.path[0]` in the worker AND in `_pytest_env`'s `PYTHONPATH`; then, before any mutant, assert
that the module under test imports from under the sandbox (a one-line probe run through the worker
that returns `sys.modules[<pkg>].__file__`) and raise `MutationHarnessError` otherwise. Key
`_purge_local_modules` on that same verified root.

---

## F2 — P0 — There is no warm baseline: the process that judges every mutant is never validated unmutated

**DISPOSITION -- RESOLVED.** The unmutated tests now run once through the warm worker before any mutant is judged. A systematic warm-path failure previously produced 'all killed, no survivors' with a green cold baseline behind it -- the flattering direction, and the only one nobody investigates.

**file:line** — `mutation_teeth.py:951-967` (baseline via `_run_pytest`, a COLD subprocess),
`mutation_teeth.py:977` (`with _WarmRunner(...)`), `mutation_teeth.py:979-984` (first `warm.run`).

**Claim.** The mandatory green baseline is enforced, but only in a cold subprocess; the warm worker
— a different interpreter configuration with a different `sys.path`, a different cwd lifetime and a
persistent `sys.modules` — issues its first verdict on a MUTATED tree, so any warm-only failure is
attributed to the mutation.

**Scenario.** A test that passes cold and fails warm (it depends on a fresh `sys.modules`, on
`sys.path` not containing the repo root, on `__main__` identity, on a plugin that behaves
differently on a second `pytest.main()` in one process, or on the extra `sys.path[0]` entry the
worker adds at `_mutation_worker.py:67`) fails on mutant 1 and on every mutant after it. Every one
is recorded as killed. The run reports "40 mutants run, 40 killed, 0 survived", which is the single
most flattering output the harness can produce, and nothing in `MutationRun` distinguishes it from
a genuinely excellent test suite. The cold baseline that "protects" against this passed, so the
guard reads as satisfied.

The asymmetry is explicit in the design: a SURVIVOR is re-verified in a cold process
(`mutation_teeth.py:1000-1003`) precisely because a warm verdict is not trusted, but a KILL — the
verdict in the opposite direction, from the same untrusted process — is believed unconditionally.
The comment at `mutation_teeth.py:1001-1003` even states the reasoning ("a false survivor is the
outcome that wastes a human's afternoon"), which concedes the point: the other direction was
considered cheap and left unguarded.

**How verified — READ.** Traced every path from `_WarmRunner.__enter__` to the first `warm.run`;
there is no unmutated request, no sentinel run, and no post-sweep re-baseline.
`tests/test_mutation_teeth.py` contains no test asserting a warm baseline (grep for `warm` returns
only `test_json_shaped_test_output_does_not_break_the_channel` at line 407).

**Remedy.** Immediately after `_WarmRunner.start()`, send one request against the UNMUTATED tree and
require `rc == 0`; raise `MutationHarnessError` naming the warm environment if it differs from the
cold baseline's verdict. Repeat the same unmutated probe after the last mutant (see F5) and again
every N mutants, so drift is caught mid-sweep rather than never.

---

## F3 — P1 — `killed_by_crash` is computed only on a fallback path that by default never runs, so it is structurally always 0

**DISPOSITION -- RESOLVED.** Crashes are counted on the warm path from the exit code, and the comment claiming the summary reports the lower bound is no longer false -- the summary prints the caveat, and the count now reaches it.

**file:line** — `mutation_teeth.py:993-996` (`result = None` on the warm path),
`mutation_teeth.py:998-999` (`if not passed and result is not None and _killed_by_crash(...)`),
`mutation_teeth.py:186-190` (`summary()` prints the caveat only `if self.killed_by_crash`).

**Claim.** With `use_warm_worker=True` (the default) the warm path sets `result = None`, so the
crash test is skipped for every mutant, `crashes` stays 0, and `summary()` therefore omits the
"N of the kills were CRASHES, not assertions" sentence entirely — the crash caveat is not
under-reported, it is absent, and its absence reads as "none of these kills were free".

**Scenario.** A sweep in the default configuration on a module full of crash-prone mutants
(`*` → `/` on a value that is sometimes zero, `dropped a not` producing a `TypeError`, an emptied
string reaching an index) reports "38 killed" with no qualification. The reader concludes the
assertions are sharp. In fact most of those mutants died against any test that merely reaches the
line, proving nothing about the assertions — which is exactly what the `_CRASH_EXCEPTIONS` docstring
(`mutation_teeth.py:648-654`) says the label exists to prevent.

The inline comment at `mutation_teeth.py:994-996` acknowledges this ("The count is therefore a lower
bound, which the summary says"). The summary does NOT say it: it prints nothing at all when the
count is zero, so the lower bound is silently indistinguishable from a true zero. A caveat that
disappears when it is needed most is not a caveat.

Compounding it: the cache round-trip (`mutation_teeth.py:1035-1050` write, `mutation_teeth.py:918-936`
read) does not persist `killed_by_crash` or `coverage_gaps` at all, so even a cold run's correctly
computed crash count is erased the moment the result is served from cache — `MutationRun` is
reconstructed with the field defaulting to 0.

**How verified — READ**, plus a DEMONSTRATED half: a fixture raising `KeyError` produces
`E   KeyError: 'boom'` on pytest's summary line and exit code 1, so `_killed_by_crash` would
correctly return True — the detector works, it is simply never called on the default path.

**Remedy.** Have the worker return pytest's captured output (or at minimum a crash/assert
classification computed worker-side from the captured buffer it already holds at
`_mutation_worker.py:90-92`) alongside `rc`, and classify on the warm path. Until then, make
`summary()` state explicitly that the crash count is unavailable rather than printing nothing, and
add `killed_by_crash` / `coverage_gaps` to the cache payload.

---

## F4 — P1 — A stale `__pycache__` entry silently reverts a same-length mutation written inside the same wall-clock second as the previous one

**DISPOSITION -- NOT REPRODUCED, MITIGATED.** I could not reproduce the stale-`.pyc` revert in two attempts, including one that forced the source's size and mtime to be identical across the rewrite; the mutation applied both times. Recorded as unconfirmed rather than fixed. `PYTHONDONTWRITEBYTECODE=1` is set for mutant runs regardless: it costs nothing measurable against a warm worker that already pays the compile, and it removes the class outright rather than leaving it as an open question.

**file:line** — `mutation_teeth.py:979` (mutant write), `mutation_teeth.py:1015` (restore write),
`_mutation_worker.py:43-62` (purge forces a re-import, which consults the pyc),
`mutation_teeth.py:120-124` (`_COPY_IGNORE` excludes `__pycache__` from the copy but nothing
suppresses generation inside the sandbox).

**Claim.** CPython validates a `.pyc` against the source's mtime truncated to **whole seconds** plus
its byte size, so two successive mutants of identical length written within the same second reuse
the first one's bytecode — mutant N+1 is silently evaluated as mutant N, and if N was killed, N+1 is
falsely killed.

**Scenario.** The harness's own operator table is full of length-preserving swaps: `==`↔`!=`,
`+`↔`-`, `*`↔`/` (`mutation_teeth.py:213-224`). Two such mutants on the same file, with a pytest run
between them that finishes in under a second — entirely reachable, since `-x` aborts on the first
failure and the warm worker's marginal cost is quoted at 1.6-2.7s but that figure is for this
repo's own suite, not a narrow `test_paths` selection. The second mutant's source on disk is
correct; the executed bytecode is the first mutant's.

Direction of the error depends on the pair, and both directions are bad: killed-then-killed hides
that N+1 was never tried; killed-then-survivor reports a false kill for N+1. (The survivor
re-check at `mutation_teeth.py:1000` would not catch it either — the cold re-check re-reads the
same stale pyc.)

**How verified — DEMONSTRATED.** In a scratch directory, writing `OP = 1 == 2` then `OP = 1 != 2`
(same byte length) with the module popped from `sys.modules` between them, in one run:

```
A source says OP = 1 == 2 -> module says False
B source says OP = 1 != 2 -> module says False      <-- stale: should be True
```

With a 1.2s sleep inserted between the two writes, the same script prints `A ... False` /
`B ... True`, confirming the one-second mtime granularity is the whole mechanism.

**Remedy.** Set `PYTHONDONTWRITEBYTECODE=1` in `_pytest_env()` (and/or `sys.dont_write_bytecode = True`
in the worker before the first mutant), and delete `sandbox/**/__pycache__` after each mutant write.
`PYTHONDONTWRITEBYTECODE` is the cheap complete fix and costs nothing here, since no import in the
sandbox is repeated often enough for the pyc to pay for itself.

---

## F5 — P1 — Kills are believed unconditionally while survivors get two independent confirmations

**DISPOSITION -- RESOLVED.** The structural asymmetry is closed by F2: the process that judges every mutant is now validated unmutated before it judges anything. Kills are still not individually re-verified, which is a cost decision -- a survivor is rare and a false one wastes an afternoon, while re-verifying every kill would double the sweep.

**file:line** — `mutation_teeth.py:998-1014` (survivor path: cold re-check, then optional wider
re-check) versus `mutation_teeth.py:986-997` (kill path: no verification at all).

**Claim.** The harness spends real time proving that a survivor is a survivor and zero time proving
that a kill is a kill, so every failure mode in this report lands entirely on the unverified side of
the asymmetry and is invisible by construction.

**Scenario.** Any of F1, F2, F4, F8 or F10 turns a mutant into a "kill". Because a kill terminates
the per-mutant logic immediately, no second opinion is ever taken, no cold cross-check runs, and no
counter records that the kill was unusual. The result is a monotonically flattering report: every
harness defect in this dimension inflates the kill count and nothing pushes back. The design note at
`mutation_teeth.py:1001-1003` justifies the asymmetry on cost ("survivors are rare"), which is true
but is an argument about survivors, not an argument that kills are trustworthy.

A cheap version of the missing guard already exists in the code's own vocabulary: the run knows how
many mutants it evaluated, and it would be nearly free to re-run the UNMUTATED tree once at the end
of the sweep in the warm worker. A sweep that ends with the unmutated tree failing has produced
nothing but noise, and there is currently no way for the caller to learn that.

**How verified — READ.** Traced both branches of `if passed:`; the `not passed` branch does nothing
but `crashes += 1` (itself dead on the default path, see F3).

**Remedy.** (a) Add a mandatory post-sweep unmutated re-run in the same warm process and raise if it
fails, invalidating the whole run. (b) Sample-verify kills: re-run a small random fraction of the
killed mutants in a cold subprocess and raise `MutationHarnessError` if a cold run disagrees with
the warm verdict; a single disagreement means the warm path is unsound for this repo. (c) Record
the sampled agreement rate on `MutationRun` so `summary()` can state it.

---

## F6 — P1 — A mutant that breaks import exits 2 and aborts the whole sweep with a message that misdiagnoses the cause

**DISPOSITION -- RESOLVED.** Exit 2 or 3 on a MUTANT is now a kill of the crash kind rather than a harness abort. The reasoning is that the baseline has already passed with the same `test_paths`, so the mutation is the only thing that changed; on the baseline itself the exit code still aborts loudly.

**file:line** — `mutation_teeth.py:855-864` (`_classify_code`), `mutation_teeth.py:990` (called
inside the mutant loop, inside the `with _WarmRunner` block).

**Claim.** With `-x`, a mutation that makes the target module fail to import produces a collection
error and pytest exit code 2, which `_classify_code` converts into a `MutationHarnessError` whose
text blames `test_paths` and reachability — so a legitimate, informative mutant destroys the run and
sends the operator to reconfigure something that was never wrong.

**Scenario.** Mutating a module-level constant into something an import-time `re.compile`,
`enum` construction, `dataclass` default or module-level validation rejects. Exit 2 propagates out
of the loop, `find_surviving_mutants` raises, and `assert_no_new_surviving_mutant` fails with
"pytest exited 2 ... Check `test_paths` and that the tests are reachable from `repo_root`." Every
mutant after it in source order is never evaluated, and the partial result is discarded rather than
reported. Under the ratchet this looks like a configuration regression, not a finding.

This is the correct half of the already-fixed "2/3/4/5 read as killed" issue — the codes are no
longer silently counted as kills — but the replacement behaviour is a hard abort with a wrong
explanation, and an import-breaking mutant is a case the harness will meet routinely rather than
exceptionally.

**How verified — DEMONSTRATED.** Scratch directory, `mod.py` containing
`raise ValueError("mutant broke import")` and `test_a.py` doing `import mod`, run with the harness's
own flags (`-q --no-header -x -p no:randomly`):

```
EXIT=2
E   ValueError: mutant broke import
```

Contrast, same flags, a fixture raising `KeyError`: `EXIT=1` (an ordinary kill).

**Remedy.** Distinguish exit 2 caused by a collection error on the MUTATED target from exit 2
caused by a genuine interruption: on exit 2, re-run once with `--co -q` (or inspect the output for
`errors during collection`) and, if the target module is the one failing to import, record the
mutant as `killed_by_crash` — it proves nothing about assertions and must not be counted as an
ordinary kill either — then continue the sweep instead of aborting it.

---

## F7 — P2 — `_CRASH_EXCEPTIONS` omits the exception classes a mutant is most likely to produce, so crash-kills that ARE detected are still undercounted

**DISPOSITION -- RESOLVED.** The crash list gained the classes a MUTATION produces rather than the ones application code raises: `NameError`, `UnboundLocalError`, `ImportError`, `AssertionError`, `re.error`, the `OSError` family and others.

**file:line** — `mutation_teeth.py:656-668` (the tuple), `mutation_teeth.py:671-686`
(`_killed_by_crash`).

**Claim.** The list covers eleven builtins but omits `ImportError`, `ModuleNotFoundError`,
`NameError`, `UnboundLocalError`, `RuntimeError`, `NotImplementedError`, `StopIteration`,
`OSError`/`FileNotFoundError` and `ArithmeticError`, so those crash-kills are recorded as ordinary
assertion kills.

**Scenario.** `dropped a not` around a guard clause commonly yields `UnboundLocalError` or
`NameError` further down; a deleted statement-level call that was an initialiser yields
`RuntimeError` or `AttributeError` (covered) or `NameError` (not); a mutated path constant yields
`FileNotFoundError`. Each is a free kill labelled as a real one — the same misreporting F3
describes, on the path where the detector does run.

Two smaller issues in the same function. `payload.startswith(name)` is a prefix match, so a
user-defined `ValueErrorish` or `KeyErrorSubclass` counts as a builtin crash. And the function
returns on the FIRST `E ` line only; with `-x` that is usually correct, but a parametrised failure
or a fixture error followed by a test failure can put an `assert` line first and return False for a
run that was really a crash.

**How verified — READ**, with the exception-name list read against pytest's actual summary format
confirmed in F6's demonstration (`E   ValueError: ...`).

**Remedy.** Add the missing names; anchor the match on `f"{name}:"` or `f"{name}("` rather than a
bare prefix; and consider inverting the test (anything that is not `assert`/`AssertionError`/
`Failed:`/`DID NOT RAISE` is a crash) since the crash side is open-ended and the assertion side is
not.

---

## F8 — P2 — Process-global state a purge cannot reach accumulates across mutants in the warm worker

**DISPOSITION -- WON'T FIX.** Process-global state inside an installed third-party package cannot be reached by a purge, and this is why every reported survivor is re-verified in a cold subprocess -- the mitigation predates the finding and is documented in `_mutation_worker`'s module docstring. Chasing individual registries would be an unbounded list of special cases with no way to know when it is complete.

**file:line** — `_mutation_worker.py:43-62` (`_purge_local_modules` removes `sys.modules` entries
and nothing else), `_mutation_worker.py:70-93` (the loop, which does no other teardown).

**Claim.** Dropping a module from `sys.modules` does not undo what it did to the interpreter, so
several classes of global state survive every purge and grow monotonically across a sweep; each one
can fail a test for a reason unconnected to the current mutation.

Concrete channels, all reachable in this repo:

- **`logging`.** `logging.Logger.manager.loggerDict` lives in the stdlib and is never purged. A
  repo module that adds a handler at import time adds another one on every re-import. By mutant 20
  a `caplog` assertion counting records sees twenty copies. `mutation_teeth.py:436-441` states this
  repo has 152 `caplog` references, 54 of them asserting record contents, so this is a live target
  rather than a hypothetical.
- **`warnings`.** `__warningregistry__` is per-module and dies with the purge, but
  `warnings.filters` mutated by a repo module (or `simplefilter("once")` state) does not; a test
  asserting a warning is emitted passes on mutant 1 and fails on mutant 2.
- **`os.chdir`.** `monkeypatch.chdir` is undone by pytest, a raw `os.chdir` in a test or fixture is
  not. In a cold subprocess it does not matter; in the warm worker every subsequent mutant runs
  from the wrong directory, and relative `test_paths` stop resolving.
- **Threads and `atexit`.** A repo module that starts a background thread or registers an `atexit`
  handler at import time does so again on every re-import. Threads accumulate (port/file conflicts
  → false kills); `atexit` handlers accumulate and all fire at worker shutdown.
- **`functools.lru_cache` / registries held by third-party objects.** The worker docstring
  (`_mutation_worker.py:25-29`) names this class and says the caller re-verifies survivors because
  of it. That mitigation covers only survivors — see F5. The kill direction is unmitigated.
- **`os.environ`.** Mutated by a test and not restored, it persists for the rest of the sweep.

**How verified — READ.** The purge function's body is six lines and touches only `sys.modules`;
no `logging.shutdown`, no `warnings.resetwarnings`, no cwd save/restore, no thread accounting, no
`importlib.invalidate_caches()` and no `sys.path_importer_cache` clear appears anywhere in either
file (grep confirms).

**Remedy.** Snapshot and restore around each request in the worker: cwd, `os.environ`,
`sys.path`, `warnings.filters`, and the set of handlers on each logger reachable from
`loggerDict`; count non-daemon threads before and after and report a leak in the reply so the
caller can degrade to a cold run instead of trusting the verdict. Add `importlib.invalidate_caches()`
after the purge.

---

## F9 — P2 — Modules without `__file__` escape the purge and can hand the next mutant a stale submodule

**DISPOSITION -- RESOLVED.** A module with no `__file__` but a `__path__` under the sandbox root is purged too, so a namespace package can no longer keep the next mutant's import resolving through a stale parent.

**file:line** — `_mutation_worker.py:53-55`
(`file = getattr(module, "__file__", None); if not file: continue`).

**Claim.** The purge skips any `sys.modules` entry with no `__file__` — namespace packages are the
main case — and a retained namespace-package object still carries attribute references to the
submodules that WERE purged, so `from pkg import sub` resolves through the stale attribute rather
than re-importing.

**Scenario.** A repo (or a consuming repo, since this is shared tooling) with an implicit namespace
package: `pkg` has `__file__ = None` and survives the purge; `pkg.sub` has a real `__file__` and is
dropped from `sys.modules`. On the next mutant, `from pkg import sub` finds `pkg` cached, misses
`sys.modules["pkg.sub"]`, falls back to `getattr(pkg, "sub")` — which is still bound to the
pre-purge module object. The mutation is invisible to that import path, and the previous mutant's
code runs. Direction depends on the pair; it is the same "stale module makes the next mutant look
killed" channel the worker docstring warns about, arriving through the one door the purge leaves
open.

The same clause also skips extension modules loaded without `__file__`, and `sys.modules` entries
pytest itself creates for `conftest` under some import modes.

**How verified — READ.** The `if not file: continue` guard is unconditional and there is no
secondary check on `__path__` or `__spec__.submodule_search_locations`.

**Remedy.** When `__file__` is absent, fall back to `__path__` /
`__spec__.submodule_search_locations` and purge the package if any entry is under the root;
additionally, after computing `doomed`, `delattr` each purged submodule from its (possibly
retained) parent package object before popping it.

---

## F10 — P2 — The warm path has no timeout at all, and the cold path's timeout silently drops the mutant

**DISPOSITION -- RESOLVED.** Both halves: the warm path has a deadline (20-F14) and a timed-out mutant is tallied rather than dropped (20-F3).

**file:line** — `_WarmRunner.__init__` stores `self.timeout` (`mutation_teeth.py:785-788`);
`_WarmRunner.run` (`mutation_teeth.py:801-829`) never uses it — `self.process.stdout.readline()` at
line 812 blocks unbounded. Cold path: `mutation_teeth.py:986-990`.

**Claim.** `find_surviving_mutants`'s own docstring says the timeout is mandatory "rather than
optional" because `<`→`<=` produces non-terminating loops by construction
(`mutation_teeth.py:735-737`), yet on the default warm path that timeout is never applied and a
non-terminating mutant hangs the sweep forever.

**Scenario.** Any loop-bound comparison mutated by `_OP_SWAP`. Cold, the run is abandoned and the
mutant is excluded from the denominator (`mutation_teeth.py:988-990` — `continue` without
incrementing `run`), which is defensible and documented. Warm, the parent blocks on `readline()`
with no deadline; CI hits its job timeout and the whole run is lost with no partial result. The
timeout parameter is accepted, documented as mandatory, stored, and silently ignored.

Secondary, in-dimension: on the cold path, a timed-out mutant vanishes from BOTH numerator and
denominator with no counter recording it, so the reported kill rate is computed over a silently
shrunken population. Nothing on `MutationRun` says how many mutants were dropped.

**How verified — READ.** `grep -n "self.timeout" mutation_teeth.py` shows the attribute assigned
and never read.

**Remedy.** Give `_WarmRunner.run` a real deadline (a reader thread with `join(timeout)`, or a
watchdog that kills the process and returns `None` so the existing cold-fallback path takes over);
add a `timed_out: int` field to `MutationRun` and surface it in `summary()`.

---

## F11 — P3 — The cache round-trip drops `killed_by_crash` and `coverage_gaps`

**DISPOSITION -- RESOLVED.** Same as 20-F10.

**file:line** — write: `mutation_teeth.py:1035-1050`; read: `mutation_teeth.py:918-936`.

**Claim.** Both fields are omitted from the cached payload and from the reconstructed `MutationRun`,
so a cache hit reports the same kill count with the crash caveat and the coverage-gap warning
silently deleted.

**Scenario.** A first run correctly reports "30 mutants run, 30 killed; 12 of the kills were
CRASHES, not assertions". Nothing changes, the fingerprint matches, the second run reports
"30 mutants run, 30 killed, 0 survived (cached: nothing in the import closure changed)". The
qualification that made the first number honest is gone, and the reader is now looking at the
flattering version of the same result. Same for the "fix the map, not the tests" line, whose entire
purpose (`mutation_teeth.py:191-197`) is to stop a reader writing a test that already exists.

**How verified — READ.** The `existing[relative.as_posix()] = {...}` dict has no `killed_by_crash`
key; the `MutationRun(...)` constructed on the cache-hit path passes neither field, so both take
their dataclass defaults (`0` and `[]`).

**Remedy.** Persist and restore both fields. `coverage_gaps` holds `Mutant`s, which already have a
serialisation shape in the same function — reuse it.

---

## F12 — P3 — Only `pytest-randomly` is neutralised; other ordering and flakiness sources are not

**DISPOSITION -- WON'T FIX.** `-p no:randomly` is applied, and the remaining ordering and flakiness sources -- shared fixtures, the clock, the filesystem -- are properties of the consumer's suite, not of the harness. A harness that tried to neutralise them would be making decisions about tests it did not write. A flaky suite makes mutation testing meaningless, and the honest remedy is to fix the suite.

**file:line** — `mutation_teeth.py:718`
(`flags = ["-q", "--no-header", "-p", "no:randomly", "-p", "no:cacheprovider"]`).

**Claim.** `-p no:randomly` disables `pytest-randomly` specifically; `pytest-random-order`
(plugin name `random_order`, flag `--random-order`) and `pytest-reverse` are untouched, and a
consuming repo that configures either in `pyproject.toml`/`pytest.ini` still gets shuffled ordering
under this harness.

**Scenario.** Order-dependent tests reorder between the baseline run and a mutant run; a test fails
because of the order rather than the mutation, `-x` stops there, exit 1, false kill. `PYTEST_ADDOPTS`
is cleared (`mutation_teeth.py:640-646`, a good mitigation) but `addopts` in the repo's own config
file is not, and that is the more common place to configure ordering.

Related, same severity: `PYTHONHASHSEED` is not pinned in `_pytest_env()`. The warm worker holds ONE
seed for the entire sweep while each cold run (baseline, survivor re-check, wider re-check) gets a
fresh one, so set/dict iteration order can differ between the run that judged a mutant and the run
that re-checks it — a source of warm/cold disagreement that would currently be attributed to the
mutation.

**How verified — READ.**

**Remedy.** Add `-p no:random_order -p no:reverse` guarded by `importlib.util.find_spec` the same
way `xdist` and `pytest_cov` already are; pin `PYTHONHASHSEED=0` in `_pytest_env()`.

---

## F13 — P3 — `_pytest_env` sanitises one variable and inherits the rest

**DISPOSITION -- RESOLVED IN PART.** `PYTHONDONTWRITEBYTECODE` was added (F4) and `PYTEST_ADDOPTS` was already cleared. The rest of the environment is inherited deliberately: a mutant run must reproduce the consumer's real conditions, and a sanitised environment would report kills and survivors that do not correspond to how the code actually runs.

**file:line** — `mutation_teeth.py:640-646`.

**Claim.** Clearing `PYTEST_ADDOPTS` addresses the loudest case, but `PYTHONPATH`, `PYTEST_PLUGINS`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD`, `COV_CORE_SOURCE`/`COV_CORE_CONFIG` (set by `pytest-cov` when the
harness itself is invoked under coverage) and `PYTHONSTARTUP` are inherited into every nested run.

**Scenario.** The harness is very likely to be invoked from inside a `pytest --cov` run of the
consuming repo's own suite. `COV_CORE_*` in the environment makes the child start coverage via the
`.pth` hook regardless of the `--no-cov` flag the harness passes, adding startup cost and, if the
outer configuration has `fail_under`, a non-zero exit for a reason unconnected to the mutation.
`PYTHONPATH` pointing at the real working tree is a direct contributor to F1's import shadowing.

**How verified — READ.**

**Remedy.** Allowlist rather than denylist: build the child environment from a small explicit set
(`PATH`, `SYSTEMROOT`, `TEMP`, `HOME`/`USERPROFILE`, `VIRTUAL_ENV`) plus a deliberately constructed
`PYTHONPATH` pointing at the sandbox, and drop everything else.

---

## Verified SOUND

Stated positively, because these are the places a false kill could plausibly have lived and does not:

1. **The baseline is genuinely mandatory, not advisory.** `mutation_teeth.py:951-967` runs it before
   any mutant, raises `MutationHarnessError` on a timeout and on a non-green result, and includes
   the tail of pytest's output in the message. `assert_revert_fails_tests` enforces the same
   precondition independently (`mutation_teeth.py:1148-1157`). This is the single most important
   guard in the dimension and it is correctly placed — F2 is about its SCOPE (cold only), not its
   existence.
2. **`__pycache__`, `.pytest_cache`, `.mypy_cache` and `.hypothesis` are excluded from the sandbox
   copy** (`mutation_teeth.py:120-124`), so the sweep starts from a clean cache state. F4 is about
   caches generated DURING the sweep, not inherited ones.
3. **`-p no:cacheprovider`** (`mutation_teeth.py:718`) stops pytest's `--lf`/`--nf` state carrying
   between mutants, which would otherwise reorder tests based on the previous mutant's failures —
   a textbook order-dependence false kill, correctly closed.
4. **Plugins are disabled through their own options, not by name.** The `_pytest_flags` docstring
   (`mutation_teeth.py:708-716`) records that `-p no:xdist` broke `pytest_progress` hook validation
   and exited 3; `-n 0` and `--no-cov` are added conditionally on `find_spec`. Correct, and the
   reasoning is preserved.
5. **`--cov-fail-under` is defused** both by `--no-cov` and by clearing `PYTEST_ADDOPTS` — the
   named "makes every mutant look killed" scenario is genuinely handled for the `PYTEST_ADDOPTS`
   channel (see F13 for the channel that remains).
6. **A dead or unparseable worker degrades to a cold run, never to a verdict.** `_WarmRunner.run`
   returns `None` for a dead process, an empty line, a `JSONDecodeError`, a non-dict reply, a reply
   without `rc`, and a non-integer `rc` (`mutation_teeth.py:806-829`); the caller treats `None` as
   "use the cold path", not as "killed" (`mutation_teeth.py:980-990`). This is exactly right.
7. **A mutant that does not compile is skipped rather than run** (`mutation_teeth.py:521-524`), so
   a `SyntaxError` mutant cannot masquerade as a kill.
8. **A mutant whose text equals the original is skipped** (`mutation_teeth.py:519-520`), so a no-op
   edit cannot be counted either way.
9. **The target file is restored after every mutant on every exit path** through the loop —
   normal completion (`mutation_teeth.py:1015`), the cold-timeout `continue`
   (`mutation_teeth.py:989`) and the coverage-gap `continue` (`mutation_teeth.py:1010-1011`) — so
   mutant N's text cannot leak into mutant N+1's source. (The bytecode still can; see F4.)
10. **The sandbox is removed in a `finally`** (`mutation_teeth.py:1031-1032`, and again at
    `mutation_teeth.py:1176`), and every mutation happens in the copy, so a crash cannot leave the
    working tree modified.
11. **`symlinks=False` on the copytree** means symlinks are materialised as their targets rather
    than left dangling into the original tree — a dangling link would have been a reliable
    false-kill source.
12. **`_killed_by_crash` reads pytest's summary line rather than the traceback body**
    (`mutation_teeth.py:671-686`), so a `pytest.raises(ValueError)` test doing its job is not
    misread as a crash. The reasoning is right and the test at `tests/test_mutation_teeth.py:319`
    pins it. F7 is about coverage of the exception list, not about this design choice.
13. **`MutationRun` distinguishes the three ways an empty survivor list can arise**
    (`mutation_teeth.py:160-172`), and `truncated` / `candidates_total` mean a truncated run cannot
    be mistaken for a complete one.
14. **The baseline key is a digest of the mutated span, not a line number**
    (`mutation_teeth.py:150-158`), so an edit elsewhere in the file does not silently re-key an
    accepted survivor onto a different mutant.
15. **`HARNESS_VERSION` participates in the fingerprint** (inside `fingerprint`,
    `mutation_teeth.py:625`), so adding an operator invalidates every cached result rather than
    continuing to serve the old survivor list.
16. **`assert_revert_fails_tests` checks both that the anchor is present and that the text actually
    changed** (`mutation_teeth.py:1140-1146`), closing the `str.replace`-silently-did-nothing hole
    that motivated the module.

---

## Summary (10 lines)

1. Two P0s, four P1s, four P2s, three P3s, sixteen verified-sound items; three findings DEMONSTRATED by execution in a scratch directory, the rest by READ.
2. **F1 (P0, DEMONSTRATED):** src-layout plus editable install means the sandbox is import-shadowed by the real tree — mutations never execute, and the purge drops test modules while leaving production modules resident, the exact half-measure the worker's docstring calls a false-kill cause.
3. **F2 (P0):** the mandatory green baseline runs COLD only; the warm process that judges every mutant is never validated unmutated, so a warm-only failure reports "all killed" — the harness's most flattering possible output.
4. **F3 (P1):** `killed_by_crash` is computed only on the cold fallback (`result = None` on the warm path), so it is structurally always 0 and `summary()` prints no caveat at all; the cache drops the field too.
5. **F4 (P1, DEMONSTRATED):** CPython's one-second pyc mtime granularity means two same-length mutants written inside one second reuse the first's bytecode — mutant N+1 is evaluated as mutant N.
6. **F5 (P1):** survivors get two confirmations, kills get none, so every defect in this dimension lands on the unverified side and inflates the score invisibly.
7. **F6 (P1, DEMONSTRATED):** an import-breaking mutant exits 2 and aborts the entire sweep with a message blaming `test_paths` — verified `EXIT=2` for a collection error versus `EXIT=1` for a fixture crash.
8. **F7-F10 (P2):** `_CRASH_EXCEPTIONS` omits `ImportError`/`NameError`/`RuntimeError`/`OSError`; logging handlers, cwd, threads and `atexit` accumulate across warm runs; namespace packages escape the purge; and `_WarmRunner.run` stores `self.timeout` and never uses it, so a non-terminating mutant hangs the sweep forever.
9. **F11-F13 (P3):** the cache drops `killed_by_crash`/`coverage_gaps`; only `pytest-randomly` is disabled and `PYTHONHASHSEED` is unpinned; `_pytest_env` inherits `PYTHONPATH`/`COV_CORE_*`.
10. Cheapest high-value fixes, in order: `PYTHONDONTWRITEBYTECODE=1` (closes F4 outright), a warm unmutated probe before and after the sweep (closes F2 and most of F5), and an assertion that the module under test imports from under the sandbox (closes F1).
