# Audit: silent under-reporting in the mutation harness

Dimension: every path by which the harness examines FEWER mutants than the operator believes, or
reports a clean/near-clean result that is not earned.

Findings are labelled **DEMONSTRATED** (a reproduction was run in a scratch tree outside the audited
repo) or **READ** (established by reading the code only). Nothing was fixed.

Repro trees used: `scratchpad/gcl`, `gcl2`, `mt1`..`mt6`.

---

## F1 (P0) The result cache is keyed on a fingerprint that ignores `limit`, `lines` and `fallback_test_paths`, so a deliberately narrowed run poisons every later wider run

**DISPOSITION -- RESOLVED.** `lines`, `limit` and `fallback_test_paths` now enter the key through a `scope` argument to `fingerprint`. Found independently by 21-F1 and demonstrated by both agents. Regression test: `test_the_scope_of_a_run_is_part_of_its_key`.

**Claim.** `fingerprint()` digests only the import closure, the test files and `HARNESS_VERSION`, so a
cached run made with `limit=1` or `lines=range(1,3)` is served verbatim to a later run asking for the
whole file, and the operator sees a clean summary for work that was never done.

**Where.** `src/py_ci_shared/mutation_teeth.py:582` (`fingerprint` signature; no scope arguments),
consumed at `:912-940` (the cache lookup happens *before* `generate_mutants`, so scope is never
compared).

**Why it matters.** This is exactly the sweep that motivated the audit. The operator's remedy for a
truncated run is "re-run with the cap raised". With a `cache_path` in play that remedy is a no-op: the
second run returns the first run's numbers and the extra survivors are never generated. The `lines=`
variant is worse, because a changed-lines run carries no TRUNCATED notice at all.

**Verified — DEMONSTRATED** (`scratchpad/mt1`, `mt2`):

    RUN1 limit=1   : 1 mutants run, 1 killed, 0 survived; TRUNCATED: 8 candidates existed, 1 were run
    RUN2 limit=None: 1 mutants run, 1 killed, 0 survived; TRUNCATED: 8 candidates existed, 1 were run; (cached: ...)
                     from_cache=True

    RUN1 lines=1-2 : 2 mutants run, 2 killed, 0 survived
    RUN2 whole file: 2 mutants run, 2 killed, 0 survived; (cached: nothing in the import closure changed)
    RUN3 truth     : 8 mutants run, 4 killed, 4 survived
                     t.py:7:9  '<' -> '<='
                     t.py:7:11 '5' -> '6'
                     t.py:8:15 '"small"' -> '""'
                     t.py:9:11 '"big"' -> '""'

A whole-file run reported **0 survivors** where the truth is **4**, with no truncation marker of any
kind.

**Remedy.** Fold scope into the cache key: hash `limit`, the normalised `lines` ranges,
`fallback_test_paths` and `use_warm_worker` into `fingerprint` (or store them in the cache entry and
compare on read). Additionally refuse a cache hit whose recorded scope is *narrower* than the one
requested, rather than only comparing equality.

---

## F2 (P0) `test_paths` naming a directory makes the fingerprint blind to every test in it

**DISPOSITION -- RESOLVED.** A directory in `test_paths` is expanded to the `test_*.py` and `conftest.py` files under it, and an unreadable path now digests distinctly instead of collapsing to one constant. Regression test: `test_a_directory_of_tests_is_expanded_not_swallowed`.

**Claim.** `fingerprint` adds each entry of `test_paths` as a file and hashes `read_bytes()`; for a
directory that raises `OSError`, which is swallowed into the literal `b"<missing>"`, so the entire
test suite contributes a constant to the digest.

**Where.** `mutation_teeth.py:628-636` (`files.add(test_path)` for any entry) and `:643-647`
(`except OSError: body = b"<missing>"`). The conftest walk at `:632-641` starts from
`test_path.parent`, which for `tests` is the repo root, so `tests/conftest.py` is missed too.

**Why it matters.** `test_paths=["tests"]` is the natural call. Under it, editing, deleting or
emptying any test never invalidates the cache: the harness keeps replaying a stale "0 survivors" after
the tests that killed those mutants have been weakened or removed. That is the purest form of an
unearned clean result.

**Verified — DEMONSTRATED** (`scratchpad/mt5`):

    dir-as-test-path fingerprint changed after editing a test? False
    file-as-test-path changed?                                True

**Remedy.** Expand directories in `fingerprint` (sorted `rglob("*.py")`), and turn the `OSError`
fallback into a hard error instead of a magic constant, so an unreadable input can never quietly
become a stable hash.

---

## F3 (P0) A mutant whose test run times out is dropped from the run entirely, with no counter and no notice

**DISPOSITION -- RESOLVED.** A timed-out mutant is recorded in `MutationRun.inconclusive`, the summary says the sweep is incomplete, and `assert_no_new_surviving_mutant` refuses to pass. Neither killed nor survived is now a state the report can express.

**Claim.** On the cold path, `if result is None: ... continue` restores the file and moves on without
incrementing `run`, without a timed-out tally, and without any field the summary could print, so the
mutant ceases to have existed.

**Where.** `mutation_teeth.py:983-986`:

    result = _run_pytest(test_paths, sandbox, timeout)
    if result is None:
        io.open(target, "w", ...).write(original)
        continue  # a mutant that hung is not a survivor

The comment is right that it is not a survivor. It is also not a kill, and nothing says so.

**Why it matters.** The docstring of `_run_pytest` (`:730`) says "swapping `<` for `<=` produces
non-terminating loops **by construction**" — the mutants most likely to time out are precisely the
boundary mutants the harness exists to find. Each one silently reduces the denominator. `truncated`
stays False (it is gated on `limit`), and `mutants_run < len(mutants)` is never surfaced because
`len(mutants)` is not a field of `MutationRun`.

**Verified — DEMONSTRATED** (`scratchpad/mt6`; `DELAY = 0` mutated to `1` makes `time.sleep(DELAY*90)`
exceed a 30s timeout):

    candidates generated: 6 total 6
    SUMMARY: 5 mutants run, 2 killed, 3 survived
    mutants_run 5  vs generated 6  truncated False  candidates_total 6

Six mutants existed, five were evaluated, and the summary is silent about the sixth.

**Remedy.** Add `timed_out: list[Mutant]` (at minimum a count) to `MutationRun`, print it in
`summary()` unconditionally, treat it as a first-class "undecided" outcome, and persist it in the
cache entry.

---

## F4 (P1) A survivor whose cold re-check times out is silently converted into a kill

**DISPOSITION -- RESOLVED.** A survivor whose confirmation times out is recorded as inconclusive rather than falling through into the kill count. This was the only re-check on the path that failed unsafe.

**Claim.** After the warm worker reports a pass, a timeout in the confirmation run makes `confirm`
None, the `and` short-circuits, and the mutant is neither appended to `survivors` nor recorded as
undecided — but `run` was already incremented, so `killed = run - len(survivors)` counts it as killed.

**Where.** `mutation_teeth.py:997-1000`:

    confirm = _run_pytest(test_paths, sandbox, timeout)
    if confirm is not None and _classify(confirm, ...):
        ...append to survivors...

and `killed=run - len(survivors)` at `:1022`.

**Why it matters.** This is the worse direction of the two: a candidate survivor — the harness's only
product — is turned into evidence that the tests are fine. A mutant that survives warm and then hangs
cold is not exotic: a cold start is slower, and a survivor by definition runs the tests to completion
rather than short-circuiting on the first failed assertion.

Note the asymmetry: the `fallback_test_paths` re-check immediately below (`:1004-1008`) fails *safe* —
a timed-out wider run leaves the mutant in `survivors`. Only the confirmation run fails unsafe.

**Verified — READ.** Constructing a mutant that passes warm and hangs cold deterministically was not
attempted; the control flow is unambiguous on inspection and mirrors the demonstrated F3.

**Remedy.** Make the branch explicit: `if confirm is None: undecided.append(mutant); continue`, and
report `undecided` in `summary()`.

---

## F5 (P1) Container sampling drops candidates while `truncated` stays False, and the line printed instead is numerically wrong in both fields

**DISPOSITION -- RESOLVED.** `truncated` no longer depends on `limit`. The counting was rebuilt so `candidates_total` includes only candidates that became mutants or were dropped by the cap or by sampling -- a no-op or non-compiling candidate is not an omission. Verified on real files: `letter_guards.py` and `user_prompt.py` now report complete, `llm_cache.py` reports truncated because one candidate really was sampled out.

**Claim.** `truncated = limit is not None and candidates_total > len(mutants)` (`:1024`), so with
`limit=None` a run that dropped candidates through container sampling reports `truncated=False`; the
compensating "sampled N of M entries" line hard-codes `N = _CONTAINER_SAMPLE` and computes `M` in a
different unit (candidate tokens, not container elements).

**Where.** `mutation_teeth.py:544` (`{n: (_CONTAINER_SAMPLE, seen + _CONTAINER_SAMPLE)}`), `:517-522`
(`seen` counts suppressed *candidates*), `:317-336` (`_container_members` counts *elements*), `:1024`
(the `truncated` formula), `:203-207` (`summary()`).

**Why it matters.** Three under-reports stack:

1. `truncated` is False for a run that examined 13 of 18 candidates (below). The question this audit
   poses — can `truncated` be False while candidates were dropped — is answered **yes**.
2. The `3` in "sampled 3 of 8 entries" is a constant, not a measurement. Under `lines=` scoping the
   kept elements can fall outside the range and contribute zero mutants, and the line still claims 3.
3. `_container_members` keys on each element's *start* index, so an element that does not begin with a
   mutable token (a tuple value `("x1", "y1")`, whose start is `(`) is never suppressed at all while
   its interior strings are mutated normally. The sampling is both leakier and less honestly reported
   than the docstring's "18% of lines produced 64% of mutants" rationale implies.

**Verified — DEMONSTRATED** (`scratchpad/mt3`, a 6-row dict of 2-tuples, i.e. 12 elements):

    container members dropped: 9 element start indices
    FULL   mutants 13  total 18  sampled {'TABLE': (3, 8)}
    summary: 13 mutants run, 13 killed, 0 survived; sampled 3 of 8 entries in TABLE   <- no TRUNCATED line
    SCOPED lines 2-3: mutants 4  total 6  sampled {'TABLE': (3, 5)}                   <- "3 shown" in a 2-row window

Five candidates were dropped and the word TRUNCATED never appears. The unit tests do not cover this:
`tests/test_mutation_teeth.py:187` builds `MutationRun(truncated=total > len(mutants), ...)` by hand,
asserting a *different* formula than production uses.

**Remedy.** Compute `truncated` as `candidates_total > mutants_run` regardless of `limit` (naming the
cause), and have `generate_mutants` return true per-container `(kept, total)` element counts instead
of reconstructing them from `_CONTAINER_SAMPLE`.

---

## F6 (P1) `git_changed_lines` mis-parses paths with spaces or non-ASCII: `-z` does not unquote patch-mode paths

**DISPOSITION -- RESOLVED.** `-z` dropped (git ignores it in patch mode, and the docstring claiming otherwise is corrected), and git's C-quoting is decoded on bytes. Regression test with a space and a non-ASCII character in the path; it fails against the previous revision.

**Claim.** The module docstring asserts "`-z` also removes the ambiguity"; git honours `-z` only for
`--raw/--numstat/--name-only/--name-status`. In patch mode the `+++` path stays C-quoted, so the
result dict is keyed on a quoted, backslash-escaped, tab-suffixed string and `lines_for` finds nothing.

**Where.** `git_changed_lines.py:55` (the `-z` flag), `:70-78` (the `+++` handling and `text[2:]`
strip); the incorrect claim is at `:19-20`.

**Why it matters.** The caller asks for the changed lines of `pkg/mód ule.py`, gets `[]`, and either
passes `lines=[]` to `generate_mutants` (which reads an empty range list as "no scoping") or skips the
file. Either way the operator believes the file was covered by the sweep.

**Verified — DEMONSTRATED** (`scratchpad/gcl`):

    git output:  +++ "b/sub dir/m\303\263d ule.py"<TAB>
    parsed key:  '"b\\sub dir\\m\\303\\263d ule.py"\t'
    lines_for('sub dir/mód ule.py') -> []

**Remedy.** Drop `-z` in patch mode and decode git's C-quoting explicitly (surrounding quotes, `\ooo`
octal bytes, `\t`/`\n`/`\\`/`\"`), then decode the bytes as UTF-8. Add a regression test with a space
and a non-ASCII character in the path.

---

## F7 (P1) A diff content line beginning `++ ` is parsed as a file header, and the hunks after it are attributed to a file that does not exist

**DISPOSITION -- RESOLVED.** A three-state machine: `+++ ` is a header only after `--- `, which is only a header after `diff --git`. Regression test with an added markdown line beginning `++ `; it fails against the previous revision. My first attempt at that test passed against the OLD code because the line I inserted did not actually begin with `++ ` -- corrected before it was believed.

**Claim.** `raw.startswith(b"+++ ")` cannot distinguish the `+++ b/path` header from an *added source
line* whose text begins `++ `, which the diff renders as `+++ ...`. Everything after it is filed under
a phantom path until the next real header.

**Where.** `git_changed_lines.py:70`.

**Why it matters.** The real file's later changed lines are lost, so the sweep is scoped to a strict
subset of what the commit touched and reports clean on the rest. `++ ` is not contrived: a markdown
list marker, a C `++` comment, a diff or patch fixture inside a test, any doc line starting with `+`.

**Verified — DEMONSTRATED** (`scratchpad/gcl2`; `m.py` gained `++ note` at line 2 and changed line 7):

    {WindowsPath('m.py'): [range(2, 3)], WindowsPath('note'): [range(7, 8)]}

The genuine edit at line 7 of `m.py` is attributed to a nonexistent file `note`, so a sweep scoped by
this result never mutates line 7.

**Remedy.** Track state: accept `+++ ` only when the previous line was a `--- ` line, which is itself
accepted only after a `diff --git` line. A three-line state machine removes the ambiguity.

---

## F8 (P1) A truncated, sampled or timed-out run prints nothing at all when every survivor is already accepted

**DISPOSITION -- RESOLVED.** A truncated, sampled or replayed run now emits a `warnings.warn` on the green path. A warning rather than a failure: a deliberate `limit` is a legitimate way to run this, and the defect was silence, not the truncation.

**Claim.** `assert_no_new_surviving_mutant` routes `outcome.summary()` only into `baseline.enforce`'s
`guidance` and into the `AssertionError` message; when `enforce` returns 0 the function returns
silently, so the TRUNCATED / sampled / crash-count text is never emitted on the green path.

**Where.** `mutation_teeth.py:1077-1098`.

**Why it matters.** The green path is the one nobody reads twice. A file whose survivors are all
baselined, run with a `limit` covering a fifth of its candidates, produces a passing test and zero
output; the operator's belief "this file is ratcheted" then rests on a fifth of the file. The
framing "the truncation notice existed and was still too quiet" is at its most extreme here, where it
is not printed at all.

**Verified — READ** (control flow; `enforce`'s return value gates the only two emission sites).

**Remedy.** Emit the truncation/sampling/timeout facts unconditionally, independently of whether any
survivor is new; consider making an unacknowledged truncated run a hard failure with an explicit
`allow_truncation=True` opt-in.

---

## F9 (P1) `--refresh-mutation-survivors-baseline` regenerates the baseline from a truncated or line-scoped run, deleting accepted entries that were never examined

**DISPOSITION -- RESOLVED.** A refresh writes keys with a `NEEDS-JUSTIFICATION:` marker and then raises, so it records what was found without deciding that the finding is acceptable. See 25-F3, which is the same defect from the operator's side.

**Claim.** `if request is not None and _refresh_requested(request): baseline.regenerate(found); return`
runs before any truncation check, and `found` holds only the survivors of this (possibly heavily
narrowed) run.

**Where.** `mutation_teeth.py:1078-1081`.

**Why it matters.** Refreshing after a `lines=`-scoped or `limit=`-capped run rewrites the accepted
list down to that scope's survivors, discarding previously accepted entries together with their
mandatory human notes. A later full run re-raises them as *new* (the loud direction), but the notes —
the only record of why each was accepted — are gone; and if the next run is also scoped, the loss is
never noticed.

**Verified — READ.** `regenerate(found)` replaces rather than merges, and the call is unconditional on
scope.

**Remedy.** Refuse to regenerate when `outcome.truncated`, when `sampled_containers` is non-empty, or
when `lines` was passed; require an explicit override and carry existing notes forward.

---

## F10 (P2) Cached results drop `killed_by_crash` and `coverage_gaps`, so a replayed summary is quieter than the original

**DISPOSITION -- RESOLVED.** `killed_by_crash`, `coverage_gaps` and `inconclusive` are serialised and rehydrated, so a replay carries every caveat of the run it replays. `context` is serialised too, without which the new site-based baseline key would not survive a cache round trip.

**Claim.** The cache entry written at `:1035-1057` stores neither field, and the reconstruction at
`:920-940` leaves both at their defaults.

**Where.** `mutation_teeth.py:920-940` (read) and `:1035-1057` (write).

**Why it matters.** Both fields exist precisely to stop a high kill count being read as good tests
("Not a footnote", `:186`) and to stop a coverage-map gap being read as a test gap. On the second run
the summary loses both caveats and reads strictly better than the first for the same underlying
result, while `mutants_run`/`killed` are replayed unchanged so the improvement is invisible.

**Verified — READ** (field-by-field comparison of the two dictionaries; F1's demo shows a cached
summary being reconstructed from the stored subset).

**Remedy.** Persist and restore both fields; add a test asserting the cached summary equals the fresh
one modulo the `(cached: ...)` suffix.

---

## F11 (P2) Every annotation is excluded, which hides runtime-enforced constraints that tests can and do observe

**DISPOSITION -- RESOLVED.** Narrowed rather than removed. An annotation is excluded only when it carries no literal inside a subscript: a bare forward reference is never evaluated under `from __future__ import annotations` and is provably unkillable, while `Literal[...]` and `Annotated[..., Field(ge=1)]` are read when the model is built and enforced on every instance. Both directions are pinned by tests, including the counterweight that a forward reference stays excluded.

**Claim.** `_excluded_ranges` excludes the whole annotation subtree of every `AnnAssign`, argument and
return, justified by "with `from __future__ import annotations` they are never evaluated" — but that
import is never checked for, and the frameworks that matter evaluate annotations regardless.

**Where.** `mutation_teeth.py:288-297`; the justification is at `:266-269`.

**Why it matters.** A concrete, plausible, fully testable defect the exclusion hides:
`retries: Annotated[int, Field(ge=1, le=5)] = 2` — the `1` and the `5` are the validation bounds, and a
test asserting that `Cfg(retries=6)` raises is exactly the kind of test this harness is meant to
police. Neither bound is ever mutated. The same holds for every `Literal[...]` member, a
runtime-checked value set in pydantic, typer, and any `get_type_hints`-based dispatch.

**Verified — DEMONSTRATED** (`scratchpad/mt4`):

    total candidates 6
     line 5 constant: 2 becomes 3        <- the default, mutated
     line 6 constant: emptied a string   <- the default "strict", mutated
     line 9 ... (function body)

Line 5's `Field(ge=1, le=5)` and lines 6 and 8's `Literal["strict", "loose"]` produce **zero**
candidates, while the defaults beside them are mutated, so the file looks covered.

**Remedy.** Exclude an annotation only when the module actually carries `from __future__ import
annotations` and nothing suggests runtime evaluation; or, more simply, keep excluding bare
name/subscript annotations but stop excluding any annotation containing a `Call` or a string/int
constant (`Annotated[...]`, `Literal[...]`, `Field(...)`).

---

## F12 (P2) `lines_for` matches on a path *suffix*, so it can return another file's ranges

**DISPOSITION -- RESOLVED.** `lines_for` matches on whole path segments instead of a raw suffix, so `m.py` no longer picks up `sub/m.py`'s ranges and `helpers.py` no longer matches `test_helpers.py`.

**Claim.** `key.as_posix().endswith(wanted.as_posix())` matches `pkg/my_utils.py` for a request for
`utils.py`.

**Where.** `git_changed_lines.py:113`.

**Why it matters.** Under-reporting in its sneakiest form: the caller receives a non-empty, plausible
range list belonging to a *different* file, scopes the sweep to it, and mutates lines the target never
changed while the lines it did change go unmutated and the run reports clean. Same shape as the
recorded lesson about matching table rows on a shared prefix.

**Verified — READ** (a one-line string operation; no repro built).

**Remedy.** Match on path components (`key.parts[-len(wanted.parts):] == wanted.parts`), not on a
character suffix.

---

## F13 (P2) `_first_party_imports` swallows `SyntaxError`/`OSError`, truncating the fingerprint's import closure

**DISPOSITION -- RESOLVED.** An unparseable or unreadable file is added to the fingerprint set before the walk gives up on it, so the edit that fixes a syntax error invalidates the verdict measured without it.

**Claim.** `except (SyntaxError, OSError): return seen` abandons the rest of the closure below that
module without recording that it did.

**Where.** `mutation_teeth.py:552-555`.

**Why it matters.** Combined with the cache (F1, F2), a module the walker cannot parse — newer syntax
than the running interpreter, a temporarily broken file, an encoding failure — silently shrinks the
set of files whose changes invalidate the cache. The stale-clean-result failure mode is F2's, just
harder to see.

**Verified — READ.**

**Remedy.** Record the unparsable paths and either hash their bytes anyway (so an edit still
invalidates) or refuse to use the cache for that target.

---

## F14 (P2) The warm worker's `run()` has no timeout, so the "mandatory" timeout does not apply on the default path

**DISPOSITION -- RESOLVED.** `_WarmRunner.run` reads the reply on a helper thread with the stored timeout, and kills the worker when it overruns so the caller's cold fallback starts clean.

**Claim.** `_WarmRunner.__init__` stores `self.timeout` and `run()` never uses it;
`self.process.stdout.readline()` blocks indefinitely.

**Where.** `mutation_teeth.py:822-825` (`self.timeout = timeout`), `:838-847` (`run`, the blocking
`readline`). Contrast `_run_pytest`'s docstring at `:730`: "The timeout is mandatory rather than
optional... so an unbounded run hangs forever."

**Why it matters.** `use_warm_worker=True` is the default, so the mutant class the docstring names as
the *reason* a timeout exists (`<` to `<=` non-terminating loops) hangs the whole sweep with no bound.
The under-reporting consequence is indirect but real: the operator interrupts a stuck sweep, or CI
kills the job, and in neither case is there a summary saying how many mutants were never reached.

**Verified — READ** (`self.timeout` has no other reference in the class).

**Remedy.** Enforce the timeout on the warm path (a reader thread with `join(timeout)`, or push the
timeout into the worker), and on expiry kill the worker and record the mutant as timed out (F3's new
field) rather than falling through to a cold run that will hang for the same reason.

---

## F15 (P3) `generate_mutants` sets `Mutant.path` to a `str`, so `Mutant.key` and `__str__` raise for any direct caller

**DISPOSITION -- RESOLVED.** Both branches now produce a `Path`. Found again independently as 25-F14.

**Claim.** `path=Path(path).name if not Path(path).is_absolute() else Path(Path(path).name)` — the
first branch yields `str`, not `Path`.

**Where.** `mutation_teeth.py:535`.

**Why it matters.** `find_surviving_mutants` overwrites the field via `object.__setattr__` at `:966`,
so the harness path is unaffected; but any caller using `generate_mutants` directly (the unit tests,
any ad-hoc triage script) gets `AttributeError` from `key` or `str()`. A script with a broad `except`
around per-mutant reporting would drop mutants from its output for this reason alone.

**Verified — DEMONSTRATED** (`scratchpad/mt3`, printing mutants from `generate_mutants`):
`AttributeError: 'str' object has no attribute 'as_posix'` raised at `mutation_teeth.py:170`.

**Remedy.** `path=Path(Path(path).name)` in both branches.

---

## F16 (P3) Candidates dropped as "does not compile" or "no-op edit" are counted in `candidates_total` but never reconciled

**DISPOSITION -- RESOLVED.** Closed by the same recount as F5: candidates dropped as invalid are no longer counted, so the numbers reconcile by construction.

**Claim.** `if mutated == source: continue` and `except SyntaxError: continue` both occur after
`total += 1`, so `candidates_total` includes candidates that were never going to become mutants.

**Where.** `mutation_teeth.py:524-531`.

**Why it matters.** Two opposite small distortions. With `limit` set they inflate
`candidates_total > len(mutants)` and can report TRUNCATED for a run that was not truncated — crying
wolf on the one notice that must stay credible. With `limit` unset they widen the unexplained gap
between `candidates_total` and `mutants_run` that F3 and F5 also feed, making a future
"reconcile the numbers" assertion harder to write.

**Verified — READ.**

**Remedy.** Count them into a separate `not_viable` tally and subtract before computing `truncated`;
expose the tally so `candidates_total = mutants_run + truncated_off + sampled_off + timed_out +
not_viable` becomes an identity the summary can assert.

---

## F17 (P3) `changed_lines` silently skips an untracked file it cannot read

**DISPOSITION -- RESOLVED.** An unreadable untracked file raises a warning instead of vanishing. Not fatal, because a transient lock should not abort an unrelated run -- but never silent, because a skipped new file is exactly the code most likely to be untested.

**Claim.** `except OSError: continue` in the untracked-files loop drops the file from the result with
no signal.

**Where.** `git_changed_lines.py:96-99`.

**Why it matters.** Untracked files are, by the module's own docstring, "exactly the code most likely
to be under-tested"; a permission error or a transient Windows lock removes a whole new file from the
sweep and the caller sees a shorter dict rather than an error.

**Verified — READ.**

**Remedy.** Collect unreadable paths and surface them, by raising or by returning them separately.

---

## Verified as SOUND (checked, no finding)

* **Source ordering and the `limit` interaction.** `candidates.sort(key=(abs_start, description))`
  before truncation (`:490`), so a capped run covers the start of the file rather than an arbitrary
  AST-walk slice. The `limit` branch uses `continue`, not `break`, so `candidates_total` keeps counting
  past the cap: the truncation figure is correct when it does appear.
* **Multi-range `lines=` overlap.** `line <= r.stop - 1 and end_line >= r.start` is a genuine interval
  overlap on both ends, so an expression starting above a changed hunk and extending into it is in
  scope; empty ranges behave correctly. Confirmed against the `mt2` run.
* **`--unified=0` hunk-header parsing.** `_HUNK_RE` makes `,d` optional (single-line hunks parse), and
  `if count:` correctly yields no range for `d == 0` pure deletions. Verified against real git output
  in `scratchpad/gcl`/`gcl2`: `@@ -1,0 +2 @@` and `@@ -6 +7 @@` both parsed correctly.
* **Renames.** The range is taken from the `+++ b/new` side, so it lands on the new path (subject to
  F6/F7 for the path text itself).
* **`/dev/null` deletions** set `current = None`, so a deleted file contributes no ranges.
* **Baseline-not-green and missing-target** both raise `MutationHarnessError` instead of returning an
  empty `MutationRun` (`:952-965`), so two of the three "an empty list means three things" cases the
  `MutationRun` docstring describes are genuinely closed.
* **Warm-worker degradation.** `run()` returning `None` for a dead worker, an unparsable reply, or a
  reply without `rc` all fall back to a cold subprocess rather than being recorded as a result; the
  mutant is not dropped on any of those paths (only on the timeout path, F3/F4).
* **`fallback_test_paths` re-check** fails safe: a timed-out wider run leaves the mutant in
  `survivors`.
* **`_pytest_env` / `_pytest_flags`** clear `PYTEST_ADDOPTS` and switch off coverage and xdist through
  their own options, so a consumer's `--cov-fail-under` cannot make every mutant look killed.
* **Baseline keying** is a digest of the original span, not a line number, so an edit elsewhere in the
  file does not invalidate accepted entries; `assert_no_new_surviving_mutant` uses the corrected
  repo-relative path rather than F15's `str`.
* **`_line_starts`** splits on `"\n"` only, matching the tokenizer, so a form feed does not
  desynchronise line numbers into a zero-candidate result.
