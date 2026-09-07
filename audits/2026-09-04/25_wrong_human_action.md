# 25 - The wrong human action

Dimension: the harness measures correctly and the operator still does the wrong thing, because of
how the result is presented, recorded, or enforced. Everything below is downstream of the numbers.

Paths:
- LIB = `...\scratchpad\pcs-audit\src\py_ci_shared`
- CON = `C:\Users\Admin\Machine learning\social\upwork\new_scraper\realtime_applications\tests\test_meta`

---

## F1 (P0) - One baseline key silences every identical token in the file, so accepting one equivalent mutant pre-accepts real survivors

**DISPOSITION -- RESOLVED.** Demonstrated, and it struck this round's own work: the baseline entry I had written the previous day as 'one equivalent mutant' silenced four `token_hex(8)` call sites, three of which the tests kill. `Mutant.key` now digests the surrounding LINE as well as the span, which makes a key name a SITE rather than a shape while keeping the property the line NUMBER was rejected for -- it does not move when code above it shifts. Both baseline entries were recomputed and the nonce note narrowed to the one site that actually survives, with the correction recorded in the note rather than quietly rewritten.

**Claim.** `Mutant.key` digests only the mutated span TEXT plus the description, so all N occurrences
of the same token with the same operator in one file collapse to a single baseline key; the operator
writes a note about one site and silently accepts the other N-1, forever.

**Where.** `LIB\mutation_teeth.py:157-166` (`Mutant.key`), consumed at `CON\mutation-survivors.json:3`.

```python
digest = hashlib.sha256(self.original_span.encode("utf-8")).hexdigest()[:12]
return f"{self.path.as_posix()}::{digest}::{self.description}"
```

**DEMONSTRATED.** `sha256("8")[:12] == "2c624232cdd2"`, `sha256("16")[:12] == "b17ef6d19c7a"` - exactly
the two digests in the committed baseline. Running `generate_mutants` over the real modules:

```
stage2_prompt_builder/user_prompt.py  5 mutants share key ...::2c624232cdd2::constant: 8 becomes 9   (lines 116,117,118,119,188)
llm_cache.py                          3 mutants share key ...::b17ef6d19c7a::constant: 16 becomes 17 (lines 292,312,316)
```

Then an actual sweep over lines 116-119 with the two nonce test files:

```
SUMMARY: 4 mutants run, 3 killed, 1 survived
SURVIVOR line 119 stage2_prompt_builder/user_prompt.py::2c624232cdd2::constant: 8 becomes 9
```

Three of the four sites are KILLED by the existing tests. The one accepted key blesses all five,
including line 188's `ci_nonce`, which was never in scope of any sweep. If a future edit makes any of
lines 116-118 or 188 genuinely unpinned, the sweep reports **nothing**: the key is already accepted.

**Scenario.** The operator accepts `title_nonce` (line 119, genuinely unpinned) with a careful note.
Six months later a refactor drops the `[0-9a-f]{16}` regex from the attachment test. The desc/
attachment nonce length becomes unpinned - a real hole - and the harness stays green, because the
finding it would raise carries a key that was retired in this round.

**Remedy.** Put a stable non-line locator in the key: the enclosing qualified function/class name from
`ast`, plus an occurrence ordinal within it. Both survive edits elsewhere in the file (the property
`test_the_key_survives_an_edit_elsewhere_in_the_file` protects) while distinguishing sites.

---

## F2 (P0) - The prose note is policed by nothing: `baseline_hygiene` is never called on this baseline, and could not catch a false claim if it were

**DISPOSITION -- RESOLVED IN PART.** `assert_baseline_is_honest` is now called on this baseline by an unconditional meta test, so a note-less or stale entry fails without waiting for anyone to run the expensive sweep. The second half of the finding stands and is not fixable by a checker: hygiene cannot tell a true justification from a false one. This round produced a live example -- a note of mine asserted that a property was pinned by tests when one of the two named properties was pinned by nothing, caught by reading the test file rather than by any tool.

**Claim.** The consumer's own docstring promises `baseline_hygiene` checks the notes; no code
anywhere calls it, and the checks it would apply (word count, autogen-phrase regex, absolute path)
cannot distinguish a true justification from a fabricated one.

**Where.** `CON\test_mutation_teeth.py:37` -

> "accept it in the baseline with a note saying WHY it cannot be observed, and `baseline_hygiene`
> will check the note is prose rather than a shrug."

**DEMONSTRATED.** `grep -rn "baseline_hygiene"` across the whole consumer repo returns exactly one
hit: that comment. There is no `assert_baseline_is_honest` call, in `test_meta` or elsewhere.

**DEMONSTRATED.** Even when wired, hygiene is a length check. Fed a baseline containing

```
"models.py::deadbeef1234::logic: and becomes or":
   "This is completely false, the invariant is pinned by tests/test_nonexistent.py"
```

`find_baseline_problems` returned `[]`. `LIB\baseline_hygiene.py:102-103` is the whole test:
`words = len(re.findall(r"[A-Za-z]{2,}", note))` against `_MIN_NOTE_WORDS = 4`. Nothing reads the
note's referenced file, line, or test name. Incident #3 in the brief (a note naming a property that
nothing pinned) is not merely uncaught - it is uncatchable by design here.

**Scenario.** The exact 2am path: survivor is inconvenient, operator writes a confident paragraph
citing a test, the gate goes green, and the sentence is never read again by anything.

**Remedy.** (a) Wire `assert_baseline_is_honest` on `mutation-survivors.json` in a fast (non-opt-in)
meta test. (b) Require every note to contain at least one `path.py:NNN` citation and machine-verify
each citation exists (file present, line in range, and - cheaply - that the cited test file is one of
the `_COVERAGE`/fallback tests for that source). A note that cites nothing verifiable is a shrug with
more words.

**Sound, and worth recording:** the two notes actually in `mutation-survivors.json` are unusually
good - they name the property, name the test and line, and explicitly state which property is NOT
asserted. The failure here is that the mechanism does not require any of that; the quality is the
operator's, not the harness's.

---

## F3 (P0) - The advertised refresh command does nothing, and the library's version of it launders survivors

**DISPOSITION -- RESOLVED.** The refresh path now writes keys marked `NEEDS-JUSTIFICATION:` and then raises, so it records what was found and refuses to decide that the finding is acceptable. Accepting every survivor is no longer a one-command operation with green output.

**Claim.** Two failures at once. In the consumer, `--refresh-mutation-survivors-baseline` is
registered but never read, so running the documented refresh command passes and changes nothing. In
the library, the code path that WOULD honour it writes an auto-generated placeholder note that
hygiene accepts.

**Where.**
- `CON\conftest.py:35` `register_mutation_refresh_option(parser)` registers `--refresh-mutation-survivors-baseline`.
- `CON\test_mutation_teeth.py:100` advertises `refresh_command="pytest tests/test_meta/test_mutation_teeth.py --run-mutation-teeth --refresh-mutation-survivors-baseline"`.
- `CON\test_mutation_teeth.py:105-189` never calls `_refresh_requested`, `regenerate`, or `assert_no_new_surviving_mutant`. `grep -n "refresh\|regenerate"` in that file returns only line 100.
- `tests/test_meta/regen_baselines.py` has no `mutation` hit either.

**DEMONSTRATED** by grep, and by the flag being a plain `store_true` that no consumer code reads.

**Scenario A.** `Baseline.enforce` prints "run <refresh_command> to prune". The operator runs it. The
run is green. The stale entries are still there. Nothing says the flag was ignored - an unread
`store_true` is indistinguishable from a satisfied one.

**Scenario B (the laundering path, library side).** `LIB\mutation_teeth.py:1104-1110`:

```python
found = {m.key: f"{m} -- accepted because ..." for m in outcome.survivors}
if request is not None and _refresh_requested(request):
    baseline.regenerate(found)
    return
```

One command turns every current survivor into an accepted entry whose note is the literal string
`... -- accepted because ...`, and then returns silently - no print, no count, no list of what was
just accepted. **DEMONSTRATED**: `find_baseline_problems` on a baseline holding exactly that note
returned `[]`, because the mutant's own repr contributes far more than four alphabetic words. The one
guard `baseline_hygiene` was written to provide - "an entry whose note is the scanner's own
auto-generated sentence" (`LIB\baseline_hygiene.py:6-9`) - does not fire on the sentence this
library itself generates.

**Remedy.** Make `regenerate` in this path write a note the hygiene check REFUSES (e.g. the literal
`TODO`), so a refresh is green only after a human replaces every placeholder. Print the accepted keys
on refresh. In the consumer, either implement the refresh or delete the flag and the
`refresh_command` string; a flag that is accepted and ignored is worse than an unknown-argument error.

---

## F4 (P0) - A refresh (or a stale report) after a narrow commit deletes accepted entries for files the commit did not touch

**DISPOSITION -- RESOLVED.** Closed by F3: a refresh no longer produces a passing run, so a stale report cannot silently prune accepted entries for files the commit did not touch.

**Claim.** `found` is scoped to the files changed in this commit, but `stale`/`regenerate` are
computed against the WHOLE baseline, so entries for untouched files are reported as stale and would
be erased by a regeneration.

**Where.** `LIB\baseline_ratchet.py:106` `stale = [key for key in accepted if key not in found]` and
`:135` `self.save({key: previous.get(key, value) for key, value in found.items()})`; consumer
`CON\test_mutation_teeth.py:121-166` builds `all_survivors` only from `_changed_targets()`.

**READ.** A commit touching only `llm_cache.py` produces `found` with no `user_prompt.py` key, so
enforce prints "1 baseline entry(ies) no longer violate the rule - run ... to prune", naming the
`user_prompt.py::2c624232cdd2` entry. It is not stale; it was simply out of scope.

**Scenario.** The operator obeys the prune instruction (by hand, since F3 makes the command a no-op)
and deletes a valid, carefully justified acceptance. Next time that file is touched, the survivor
returns as a *new* finding and someone re-derives the same paragraph - or, worse, accepts it fast
because it "was accepted before".

**Remedy.** Pass the scope to `enforce`: only report an accepted key as stale when its file was
actually in this run's target set. `regenerate` must merge into, not replace, the entries outside
scope.

---

## F5 (P1) - `coverage_gaps` never fails, never ratchets, and disappears from cached runs; nothing pushes the operator to fix the map

**DISPOSITION -- RESOLVED.** `coverage_gaps` now raises with the offending mutants listed and the instruction to fix the MAP rather than the tests, and it is persisted through the cache so a replay does not stop mentioning it.

**Claim.** The fallback re-check (the mechanism that exists precisely because of incident #2) has no
teeth of any kind: a coverage-map gap is a print, forever, and it is not stored in the cache, so the
second run of the same state does not even print it.

**Where.** `LIB\mutation_teeth.py:990-999` appends to `coverage_gaps` and `continue`s; the field is
absent from the cache write at `:1029-1050` and from the cache read at `:914-936`; consumer prints it
at `CON\test_mutation_teeth.py:147-151` and never counts it toward `exit_code`.

**READ / DEMONSTRATED** (cache): the persisted entry dict has keys `fingerprint, mutants_run, killed,
truncated, candidates_total, sampled_containers, survivors` - no `coverage_gaps`, no
`killed_by_crash`. The `MutationRun` reconstructed from cache therefore has `coverage_gaps=[]` and
`killed_by_crash=0`, and `summary()` prints neither warning.

**Scenario.** Run 1 prints "COVERAGE MAP GAP: ... is killed by a test not listed". The operator is
mid-commit and does not fix the map. Run 2, same state, cache hit: the line is gone, the summary is
clean, and the map stays wrong permanently. The next time that source's real coverage regresses, the
fallback silently covers for it and the harness reports a green sweep against a map that names the
wrong tests.

**Remedy.** Persist `coverage_gaps` and `killed_by_crash` in the cache. Ratchet the gaps: keep a
`coverage-map-gaps` baseline so a NEW gap fails, exactly as a new survivor does. The map is
hand-maintained; a report with no failure mode is not maintenance pressure.

---

## F6 (P1) - Nothing ever runs the sweep: no CI job, no hook, no scheduled invocation

**DISPOSITION -- RESOLVED.** A `pre-push` hook runs the sweep on changed lines, and the hooks are installed: this repository had none at all -- `.git/hooks` held only samples and `core.hooksPath` was unset -- so its entire configuration was inert, which the finding could not have known and which made every other hook in the file inert too.

Installing it exposed a second defect, in this very wiring. The sweep asked `changed_lines` for the working tree against HEAD, which is the right question for a pre-commit hook and always answers "nothing" on pre-push, because everything being pushed is committed by then. The hook would have run on every push, printed Passed, and mutated not one line: a check that exists, is believed, and checks nothing, arriving inside the mechanism built to catch exactly that. It was caught by reading the skip REASON rather than the exit status, which is the only way it could have been caught.

The base is now chosen by the situation: HEAD when the tree is dirty, the upstream when it is clean, `MUTATION_TEETH_REV` when a caller knows better. Three tests pin the decision against a real temporary repository, and the skip message names the base it compared against, so a future vacuous pass says so out loud.

One limitation stands: `pre-commit` installs one config per repository, and this repository carries two -- `realtime_applications` and `production_scrapers`, both written with repo-root-relative paths. The former is installed; the latter's 16 hooks remain inert. That is a pre-existing condition this finding uncovered rather than caused, and it is recorded here rather than quietly left.

---

## F7 (P1) - Everything `Baseline.enforce` prints on a passing run is swallowed by pytest's capture

**DISPOSITION -- RESOLVED.** Everything the enforcement path needs to say is now said by raising -- inconclusive runs, coverage-map gaps and unjustified refreshes all fail -- and the informational cases (truncated, replayed) go through `warnings.warn`, which pytest surfaces in its own summary rather than into a captured buffer.

**Claim.** The stale report and the accepted count go to stdout inside a test that passes, so pytest
discards them; the operator sees nothing at all.

**Where.** `LIB\baseline_ratchet.py:121-129` prints the stale list and
`f"{label}: no new violations ({len(accepted)} accepted, baselined)"` to plain stdout. The consumer
wraps only its OWN prints in `with capsys.disabled()` (`CON\test_mutation_teeth.py:153-155`); the
`enforce` call at `:157` is outside that block.

**READ.** pytest shows captured stdout only for failing tests. On the good path - the common path -
the "N baseline entry(ies) no longer violate the rule" message, which is the ONLY stale-detection
output in the entire system, is written to a buffer and thrown away.

**Scenario.** F4's mis-scoped stale report and a genuinely stale entry are equally invisible.
Acceptance is, in practice, permanent: nothing the operator can see ever revisits an accepted entry.

**Remedy.** Have `enforce` return the stale list to the caller instead of only printing it, and print
it inside the consumer's `capsys.disabled()` block. Better: fail (or warn loudly) once a stale entry
has been stale for more than one run.

---

## F8 (P2) - "TRUNCATED" fires when nothing was truncated, training the operator to ignore the one banner that mattered

**DISPOSITION -- RESOLVED.** Same recount as 20-F5 and 20-F16. Verified on real files that the banner no longer fires when nothing was omitted.

**Claim.** `candidates_total` counts candidates the run discards for reasons unrelated to `limit`
(non-compiling mutants, no-op mutants, sampled container rows), so `truncated` is True whenever any of
those exist - even when the limit was never reached.

**Where.** `LIB\mutation_teeth.py:512-545` (`total += 1` precedes the sample-skip, the `mutated ==
source` skip and the `SyntaxError` skip; `limit` is checked last) and `:1005` `truncated = limit is
not None and candidates_total > len(mutants)`.

**DEMONSTRATED.** A three-line file, `limit=40`:

```
generated: 1 candidates_total: 2
SUMMARY: 1 mutants run, 1 killed, 0 survived; TRUNCATED: 2 candidates existed, 1 were run
```

The missing candidate is `for i not in xs`, which does not parse and was correctly dropped. Nothing
was truncated.

**Scenario.** This is incident #1 in the brief, inverted and explained: the operator has seen
"TRUNCATED: 40 candidates existed, 30 were run" on runs where the shortfall was harmless noise, so
the banner is trained into background. When it finally means a real limit cut, it is accepted as a
result.

**Remedy.** Count the three discard reasons separately and report them separately: `truncated` should
mean only "the limit stopped us", and the summary should say `N not run: 3 do not compile, 2 no-ops,
5 sampled rows` so the shortfall always reconciles to zero.

---

## F9 (P2) - A mutant that times out vanishes from every number in the report

**DISPOSITION -- RESOLVED.** Same as 20-F3.

**Claim.** When the cold fallback run also times out, the mutant is skipped without incrementing
`mutants_run`, without being a survivor, and without any counter or message anywhere.

**Where.** `LIB\mutation_teeth.py:975-979`:

```python
result = _run_pytest(test_paths, sandbox, timeout)
if result is None:
    io.open(target, "w", ...).write(original)
    continue  # a mutant that hung is not a survivor
```

**READ.** `run` is incremented on the line after, so the mutant leaves no trace. `MutationRun` has no
`timed_out` field, and `len(mutants)` (what was generated) is never compared with `mutants_run` (what
finished) in any output.

**Scenario.** `<` becomes `<=` produces a non-terminating loop - by the module's own account the
motivating case for the mandatory timeout. That mutant is exactly the interesting kind. It hangs, is
dropped, and the report reads "12 mutants run, 12 killed, 0 survived": a perfect score that omits the
one mutant the tests demonstrably could not adjudicate.

**Remedy.** Add `timed_out: list[Mutant]`; surface it in `summary()` before the kill count, and treat
it as neither killed nor survived but as *unresolved* - a category the operator must clear.

---

## F10 (P2) - A zero-mutant run is not distinguishable from a clean run in the printed summary

**DISPOSITION -- RESOLVED.** A run that generated no mutants now says so in those words instead of printing '0 mutants run, 0 killed, 0 survived', which is the sentence a clean run would also produce.

**Claim.** `MutationRun`'s docstring says the fields exist to tell those apart; `summary()`, which is
the only thing a human reads, does not use them.

**Where.** `LIB\mutation_teeth.py:203-210` (docstring: "an empty list meant ... All three read as
success. These fields distinguish them") vs `:212-236` (`summary()`).

**DEMONSTRATED.** `MutationRun([], 0, 0, False, 0).summary()` returns exactly:

```
0 mutants run, 0 killed, 0 survived
```

`candidates_total` is printed only under `truncated`. In the consumer this line renders as
`llm_cache.py: 0 mutants run, 0 killed, 0 survived` next to files with real numbers.

**Scenario.** A `lines_for` mismatch (a path prefix change under `realtime_applications/`, a rename)
makes the range selection empty for one file. Its line looks like a modest but clean result and the
run passes. The state "we checked this file and it is fine" and "we checked nothing" are the same
string.

**Remedy.** `summary()` should lead with an explicit `NO MUTANTS GENERATED (0 of 0 candidates in the
selected ranges)` when `mutants_run == 0`, and the consumer should fail rather than pass when a
target it selected produced zero mutants.

---

## F11 (P2) - A cached result and a measured result differ only by a clause at the end of a long line

**DISPOSITION -- RESOLVED.** The cached marker was rewritten to lead with `REPLAYED FROM CACHE, not measured` and the same fact is raised as a warning on the green path.

**Claim.** `(cached: nothing in the import closure changed)` is appended LAST, after up to five other
clauses, on a semicolon-joined line - the least-read position in the string.

**Where.** `LIB\mutation_teeth.py:234-235`. Also note the cache is keyed on a fingerprint that
explicitly cannot see data files unless named, dynamic imports, dependency versions or the
environment (`:865-877`), so a cache hit is the result most in need of a visible marker.

**READ.** A realistic line: `models.py: 18 mutants run, 18 killed, 0 survived; 6 of the kills were
CRASHES, not assertions; TRUNCATED: 31 candidates existed, 18 were run; (cached: nothing in the
import closure changed)`.

**Scenario.** The operator upgrades a dependency, re-runs, reads a clean line, and concludes the
tests still have teeth against the new version. Nothing was executed.

**Remedy.** Prefix, do not suffix: `CACHED | 18 mutants run, ...`. A provenance marker belongs where
the eye starts.

---

## F12 (P2) - The fallback wider net misses real killing tests and over-matches on a substring, so `coverage_gaps` is both under- and over-reported

**DISPOSITION -- RESOLVED.** The consumer's `_fallback_tests` matched on a bare substring, so it matched `import models_extra` when asked about `models` and missed a module imported through its package -- wrong in both directions, which made `coverage_gaps` wrong in both directions. It now uses word-boundaried patterns and covers the package-import form. Measured: `models.py` 61 test files, `user_prompt.py` 5, `letter_guards.py` 3. Worth recording that the first attempt at this fix shipped `\b` as a literal backspace byte through a shell heredoc and matched nothing at all -- caught by checking the count rather than by the tests, which had no reason to fail.

**Claim.** `_fallback_tests` matches import lines by three hand-rolled substrings; a test that imports
the symbol from the PACKAGE is missed, and a longer symbol name matches by accident.

**Where.** `CON\test_mutation_teeth.py:182-188`:

```python
if f"from {module} import" in text or f"import {module}" in text or f"from {dotted} import" in text:
```

**DEMONSTRATED (miss).** For `stage2_prompt_builder/user_prompt.py`, `module="user_prompt"`,
`dotted="stage2_prompt_builder.user_prompt"`. `tests/test_fewshot_prompt.py:11` reads
`from stage2_prompt_builder import _build_user_prompt, _format_past_wins` - the same function under
test, from the package - and matches none of the three patterns. That test is excluded from the wider
net.

**DEMONSTRATED (over-match).** For `pipeline/replay.py`, `module="replay"`, so `"import replay"` is a
substring of `tests/test_audit_pass3_rt4.py:135` `from pipeline import replay_pending_notifications`.
A file that does not import `pipeline.replay` at all joins the wider net.

**Scenario.** The mechanism built to prevent incident #2 reproduces incident #2: a survivor killed by
`test_fewshot_prompt.py` is not reclassified as a coverage gap, is reported as a survivor, and the
operator writes a duplicate test. Also note the wider net silently contributes nothing when the glob
finds nothing - `if fallback_test_paths:` at `LIB\mutation_teeth.py:993` skips the re-check entirely
with no message, so "the wider net found nothing" and "there was no wider net" are the same output.

**Remedy.** Resolve importers with `ast` over the test files (module-level and function-level
`Import`/`ImportFrom`, including package-level re-exports) instead of substring matching, and make an
EMPTY fallback set a loud warning - a survivor re-checked against nothing must say so.

---

## F13 (P3) - The library's own test suite checks that two colliding mutants PRINT differently, and never that their keys differ

**DISPOSITION -- RESOLVED.** The suite now asserts that two identical spans on different lines produce two KEYS, not merely that they print differently, and separately that a key survives a line shift. The first fails against the previous revision; the second passes against both, deliberately, because it guards the property the fix could have broken.

**Claim.** The collision in F1 was reasoned about at the display layer and not at the key layer, and
the test that would have caught it asserts the weaker property.

**Where.** `pcs-audit\tests\test_mutation_teeth.py:279-285`:

```python
left  = Mutant(Path("m.py"), 3, 11, "operator: < becomes <=", "<", "<=")
right = Mutant(Path("m.py"), 3, 17, "operator: < becomes <=", "<", "<=")
assert str(left) != str(right)
```

`left.key == right.key` holds for this exact pair and is not asserted either way.

**READ.** Also absent from the suite: any test of `regenerate`/the refresh path, of `coverage_gaps`,
or of cache round-tripping of the warning fields (`grep -n "refresh\|regenerate\|coverage_gap"` on
that file returns nothing). Every finding above sits in the untested region.

**Remedy.** Add `assert left.key != right.key` and tests for the refresh and cache round-trip.

---

## F14 (P3) - The generated mutant's path is a bare basename until a caller happens to overwrite it

**DISPOSITION -- RESOLVED.** Same as 20-F15.

**Claim.** `generate_mutants` sets `path=Path(path).name`, so any direct caller gets keys that
conflate same-named files in different packages; only `find_surviving_mutants` repairs it.

**Where.** `LIB\mutation_teeth.py:535` `path=Path(path).name if not Path(path).is_absolute() else Path(Path(path).name)`
(both branches produce the basename), repaired at `:958` `object.__setattr__(mutant, "path", relative)`.

**DEMONSTRATED.** The keys printed by my direct `generate_mutants` call read
`user_prompt.py::2c624232cdd2::...`, while the committed baseline holds
`stage2_prompt_builder/user_prompt.py::2c624232cdd2::...`.

**Scenario.** A second consumer builds a baseline from `generate_mutants` directly and every
`utils.py` in the repo shares a key namespace.

**Remedy.** Take `repo_root` in `generate_mutants` and key on the relative path there, or refuse to
build a key when the path is a bare name.

---

## F15 (P3) - The mutation cache file is not gitignored and not excluded from the hooks

**DISPOSITION -- RESOLVED.** `tests/test_meta/_mutation_cache.json` is gitignored. It is a local speed-up whose correctness rests on the fingerprint, not a shared artefact.

**Claim.** `tests/test_meta/_mutation_cache.json` will be produced in the working tree on the first
real run; `.gitignore` has no `mutation` entry, and the pre-commit exclude at
`.pre-commit-config.yaml:41` covers `tests/test_meta/_.*_baseline\.json` only.

**READ.** `grep -n "mutation" .gitignore` returns nothing.

**Scenario.** The cache lands in a commit. Fingerprints computed on one machine are then treated as
authoritative on another; a colleague's first sweep is a cache hit against a state they never
measured. Alternatively it becomes recurring commit noise and gets `git checkout`-ed away, resetting
the cache at random.

**Remedy.** Ignore it explicitly, or - better, given F6 - commit it deliberately as the "last actually
run" marker and give it a schema that records the timestamp and harness version.

---

## Also noted: what a description reword does to a key

`Mutant.key` embeds `self.description`, and every description is a literal in `_OP_SWAP` /
`_NAME_SWAP` / the constant and string branches (`LIB\mutation_teeth.py:227-244`, `:404-427`).
Rewording `"operator: > becomes >="` orphans every accepted entry carrying it. `HARNESS_VERSION`
(`:120-123`) invalidates the CACHE on such a change but has no effect on baseline keys, and nothing
warns that a bump and a reword have different blast radii. The orphaned entries then surface only
through the stale report, which F7 shows is invisible on a passing run. The re-raised survivors DO
fail loudly, so this direction is safe; the lingering dead entries are the silent half.

---

## What is sound

- **The span digest key is right about the thing it was designed for.** It survives edits elsewhere in
  the file, which was the documented failure of line-number keys. The defect is that it under-specifies,
  not that the idea is wrong.
- **`MutationHarnessError` is never confused with "no survivors".** The consumer converts it to
  `pytest.fail` (`CON\test_mutation_teeth.py:140-143`) rather than an empty result. The mandatory
  unmutated baseline run and the exit-code classifier mean a misconfigured `test_paths` cannot present
  as a clean bill of health.
- **The warm-worker survivor is re-verified cold before being believed** (`LIB\mutation_teeth.py:985-989`),
  so the expensive human error - chasing a false survivor - is guarded at the point it is created.
- **The two notes in `mutation-survivors.json` are genuinely good writing**, and the `8`-nonce note is
  scrupulous about naming a property it did NOT verify. Its claim that a per-section nonce is asserted
  checks out: `tests/test_attachment_interpolations_are_fenced.py:91` `assert text_nonce != desc_nonce`.
  The note is honest; the key it is attached to is not what the note describes.
- **`_COVERAGE` carries an inline explanation of the incident that produced its `models.py` third
  entry** (`CON\test_mutation_teeth.py:85-88`), which is exactly the record a later maintainer needs.
