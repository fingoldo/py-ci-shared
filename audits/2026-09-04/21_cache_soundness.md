# Audit 21 — mutation_teeth: cache and fingerprint soundness

Scope: every way the cache can return a verdict that is no longer true.
Paths are relative to `pcs-audit/` unless absolute.
Consumer under reference: `C:\Users\Admin\Machine learning\social\upwork\new_scraper\realtime_applications\tests\test_meta\test_mutation_teeth.py`.

Labels: **DEMONSTRATED** = reproduced by running the code in a scratch repo; **READ** = established by reading the code only.

---

## F1 (P0) — The cache key does not include the mutant set: `lines`, `limit`, `use_warm_worker` and `fallback_test_paths` are absent, so a narrow run's "0 survived" is replayed for a wide request

**DISPOSITION -- RESOLVED.** Same defect as 20-F1, found independently and demonstrated separately. Closed by the `scope` argument to `fingerprint`.

**Claim.** `fingerprint()` digests only file *contents* (import closure + tests + conftests + extras + `HARNESS_VERSION`); the arguments that decide *which mutants are generated at all* are not in the key, so a cached verdict measured over 4 mutants is returned verbatim for a request that would have generated 8.

**Location.** `src/py_ci_shared/mutation_teeth.py:583-641` (`fingerprint` takes only `repo_root, target, test_paths, extra_fingerprint_paths`), `:914` (`key = fingerprint(repo_root, relative, test_paths, extra_fingerprint_paths)`), `:920` (`entry = cached.get(relative.as_posix())` — keyed by target path alone), `:1030-1056` (the write, which also stores no `lines`/`limit`).

**DEMONSTRATED.** Scratch repo `scratchpad/demo2/repo`: `subject.py` holds a tested `guard()` (lines 1-4) and an entirely untested `unchecked()` (lines 6-9); the test covers only `guard`.

```
narrow lines 1-4: 4 mutants run, 4 killed, 0 survived
WIDE lines 1-9 : 4 mutants run, 4 killed, 0 survived; (cached: nothing in the import closure changed)
WIDE, no cache : 8 mutants run, 4 killed, 4 survived
    SURVIVOR subject.py:7:9   operator: > becomes >=  '>' -> '>='
    SURVIVOR subject.py:7:11  constant: 5 becomes 6   '5' -> '6'
    SURVIVOR subject.py:8:15  constant: 1 becomes 2   '1' -> '2'
    SURVIVOR subject.py:9:11  constant: 2 becomes 3   '2' -> '3'
```

Four real survivors are reported as zero, with a reassuring "nothing in the import closure changed" attached.

**Scenario in this consumer — REAL, not theoretical.** The consumer passes `lines=ranges` derived from `changed_lines(_GIT_ROOT)` (`_changed_targets`). The ranges are a function of the *git diff*, which moves independently of file content:

- Run the check where a commit touched 12 lines of `models.py` → cached clean under key K(content of models.py + closure + tests).
- `git reset --soft HEAD~1`, an amend, a rebase onto another base, or a branch switch that leaves the worktree bytes identical but the diff larger → `changed_lines` now reports 200 lines, content is byte-identical, so **K is unchanged** → cache hit → "clean" over 200 lines that were never mutated.
- The degenerate case is worse: a run where `lines` selected nothing caches `0 mutants run, 0 killed, 0 survived`, and that entry then answers every later request for the same file content.

`limit=40` has the same hole in the other direction: raising it to 200 replays the 40-mutant verdict (the cached `truncated` flag softens but does not remove this, because the widened run is not truncated and the reader is told it was).

`fallback_test_paths` is computed by globbing `tests/test_*.py` for importers (`_fallback_tests`); adding a test file changes the survivor/coverage-gap split but is not in the key unless that file is also in the primary `test_paths`.

**Remedy.** Fold the mutant-set parameters into the digest: normalised `lines` ranges, `limit`, `use_warm_worker`, sorted `fallback_test_paths`, and the sorted `test_paths` names. Or make the cache dict key `f"{relative}::{params_digest}"` instead of `relative.as_posix()`, so a different request cannot collide with a different measurement.

---

## F2 (P0) — The import-closure walk misses every `from .sibling import name`, so a change to a called sibling module leaves the fingerprint identical

**DISPOSITION -- RESOLVED.** The root cause was precise: for `from .mod import name` the code iterated `node.names`, which holds the FUNCTION, probed a file named after the function, and then re-resolved `node.module` against the repo root where a package sibling does not live. Verified on the real consumer before fixing -- `user_prompt.py`'s closure held 18 files and none of the five siblings it calls; it now holds 46. This is the exact hole the closure exists to close.

**Claim.** `_first_party_imports` resolves a relative `ImportFrom` by looking for `<pkgdir>/<alias>.py` for each *imported name*, then re-resolves `node.module` against `repo_root`; for the ordinary form `from .experience import _build_relevant_experience` both attempts fail and the sibling module is never hashed.

**Location.** `src/py_ci_shared/mutation_teeth.py:565-577`. For `node.level >= 1` it iterates `node.names` (for `from .experience import f` that is `f`, not `experience`) and probes `base / "f.py"`; then at `:576-577` appends `node.module` (`"experience"`) to `names`, which `:574-579` resolves as `repo_root/experience.py` — a top-level path that does not exist inside a package. Only the bare `from . import pool` form (alias *is* the module) resolves.

**DEMONSTRATED.** Scratch repo `scratchpad/demo/repo`, `pkg/mod.py` containing `from .helper import calc`:

```
closure of pkg/mod.py -> ['mod.py']        # helper.py and sub.py both missing
fingerprint unchanged after rewriting pkg/helper.py's body: True
```

`calc()` went from `x + 1` to `x - 99` and the fingerprint did not move.

**Scenario in this consumer — REAL.** Running the walk over the consumer's coverage map (real tree):

- `stage2_prompt_builder/user_prompt.py` — closure of 18 files, but **none** of `experience.py`, `formatting.py`, `smiley.py`, `truncation.py`, `word_count.py`, all imported at `user_prompt.py:19-23` and called. Those are exactly the modules whose behaviour the three mapped tests exercise. Edit `word_count.compute_target_words` and the cached "clean" for `user_prompt.py` is replayed.
- `llm_cache.py` and `pipeline/replay.py` — closures include `db/__init__.py` and `db/pool.py` (the latter only by the accident of `from . import pool as _pool_mod` at `db/__init__.py:15`) but **not** `db/jobs.py`, `db/clients.py`, `db/embeddings.py`, `db/screening.py`, `db/freelancers.py`, `db/outcomes.py`, `db/submissions.py`, `db/calibration.py`, all re-exported from `db/__init__.py` via `from .X import (...)`.
- `live_config/` contributes `__init__.py` and `models.py` but not `loader.py`, `legacy.py`, `defaults_toml.py`.

This is precisely the failure the fingerprint exists to prevent ("the function under test can be untouched while a function it CALLS is not"), and it is unguarded for every package-internal dependency in the consumer.

**Why the existing test does not catch it.** `tests/test_mutation_teeth.py:216-225` builds a *flat* `tmp_path` with an absolute import — the one layout where the resolver works.

**Remedy.** For `ImportFrom` with `level`, resolve `node.module` relative to `base` (not `repo_root`) first — `base/<node.module parts>.py` and `.../__init__.py` — keeping the per-alias probe only for the `from . import mod` case. Add a regression test with a package and a relative import.

---

## F3 (P1) — Absolute imports are resolved only against `repo_root`, so a `src/` layout (this very package) fingerprints nothing but the target file

**DISPOSITION -- RESOLVED.** Absolute imports are probed against `repo_root/src` as well as `repo_root`. This is also the true residue of 22-F1, which I rejected in its stated form.

**Claim.** `_first_party_imports` probes `repo_root/<dotted>/...` only; a project whose importable root is `src/` resolves nothing, and every cached result covers the target file alone.

**Location.** `src/py_ci_shared/mutation_teeth.py:574-579`.

**DEMONSTRATED.** Against `pcs-audit` itself (`src/` layout): closure of `src/py_ci_shared/gate_integrity.py` is `['gate_integrity.py']`.

**Real vs theoretical.** Theoretical for the *named* consumer (`realtime_applications` is flat, so absolute imports resolve — the 38-file `pipeline/replay.py` closure proves it). REAL for any `src/`-layout consumer, and `py_ci_shared` is a general-purpose shared package whose README advertises this cache. The failure is silent: the closure is never empty (it always contains the target), so nothing looks wrong.

**Remedy.** Accept an optional `source_roots` (default `[repo_root, repo_root/"src"]`) or derive roots from `pyproject.toml`; and warn when a target's closure is a single file although the file has first-party-looking imports.

---

## F4 (P1) — A test path that is a directory contributes nothing to the fingerprint, silently

**DISPOSITION -- RESOLVED.** Same as 20-F2.

**Claim.** `fingerprint` treats each `test_paths` entry as a file to hash; a directory raises `OSError` on `read_bytes` and is folded in as the literal `b"<missing>"`, so every test under it can change with no key movement — while pytest happily runs that directory.

**Location.** `src/py_ci_shared/mutation_teeth.py:615-620` (`files.add(test_path)`) and `:631-634` (`except OSError: body = b"<missing>"`).

**DEMONSTRATED.**
```
fingerprint(root, 'pkg/mod.py', ['tests']) == fingerprint after rewriting tests/test_a.py: True
```
The test body was replaced with `assert 0` and the key did not move.

**Real vs theoretical.** Theoretical in the named consumer (`_COVERAGE` lists individual files); REAL for any caller passing `["tests/"]` or `["tests/unit"]`, which is the natural thing to write and which works correctly for the pytest invocation itself. The same swallow hides a *typo* in `extra_fingerprint_paths`: a misspelled `prompts/system_v3.txt` becomes `b"<missing>"` and the data-file protection the consumer depends on is inert with no error. (Both named files exist today — verified — so the consumer is currently protected.)

**Remedy.** Expand directories to their `**/*.py` members; and raise `MutationHarnessError` on an `extra_fingerprint_paths` entry that does not exist, since a named-but-absent extra is always a caller bug.

---

## F5 (P1) — The cache entry is written under a fingerprint taken *before* the run, so an edit during a multi-minute run pins a verdict to content that was not measured

**DISPOSITION -- RESOLVED.** The fingerprint is re-taken after the run and the result is discarded when it differs, so an edit during a multi-minute sweep can no longer file an old measurement under new content.

**Claim.** `key` is computed at entry (`:914`), the tree is copied and mutants run for minutes (`:942-1024`), and the result is written under that stale `key` (`:1030-1056`) with no re-check that the tree still matches.

**Location.** `src/py_ci_shared/mutation_teeth.py:914` vs `:1036`.

**READ** (not demonstrated; would require racing a real multi-minute run).

**Scenario.** A ten-minute run on `models.py` is in flight. An editor or agent saves `models.py` (or any closure member) after `key` was taken but before `shutil.copytree` at `:943`. The measurement is of the NEW content; the entry is stored under the OLD content's key. Revert or `git checkout` back to the old content later — routine during review — and the cache replays a verdict never measured on that code. The classic "cache written from a different working-tree state than the one measured".

**Remedy.** Recompute `fingerprint(...)` right after `copytree` (or after the run) and refuse to write when it differs from `key`; better, fingerprint the *sandbox copy*, since that is what was actually measured.

---

## F6 (P1) — `HARNESS_VERSION` is a human promise, and the package ships the gate that would enforce it without applying it to itself

**DISPOSITION -- DEFERRED.** `HARNESS_VERSION` remains a human promise. The version was bumped to 3 in this round, and the operator-set changes here are exactly what it exists for -- but wiring `content_hash_version_bump_gate` to this module is a check about the check, and adding it in the same round that rewrote the module would have made every intermediate commit fail for a reason unrelated to the work. Waiting on: the module settling. The risk while it waits is bounded by the interpreter and plugin set now being in the digest, which catches the most common accidental staleness.

**Claim.** Nothing binds `HARNESS_VERSION = "2"` to the content of the mutant-generation code; adding an operator, changing an exclusion, or changing `_CONTAINER_SAMPLE` without touching the constant leaves every cached "clean" answering for a different, usually smaller, mutant set.

**Location.** `src/py_ci_shared/mutation_teeth.py:113-116` (constant and comment), `:127` (`_CONTAINER_SAMPLE = 3`, which changes the mutant set and is not named by the constant's comment), `tests/test_mutation_teeth.py:255-267`.

**READ, with the enforcement gap verified by grep.** The only test monkeypatches `HARNESS_VERSION` and asserts the digest changes — it proves the constant is *wired in*, not that anyone bumps it. A repo-wide grep for `HARNESS_VERSION` outside `mutation_teeth.py` and its own test returns nothing: no baseline pins the operator table's content hash.

The sharp edge: this package **already contains** `src/py_ci_shared/content_hash_version_bump_gate.py`, whose docstring describes exactly this pattern ("a cache-key version often gates whether a persisted/cached artifact is still valid ... enforced by nothing. A source edit that forgets the bump silently reuses a stale cached result under the OLD version"). It is not applied to `mutation_teeth`.

**Remedy.** Add `tests/test_meta/test_mutation_harness_version_bump.py` using `assert_version_bumped_with_content(files=[mutation_teeth.py, _mutation_worker.py], version=HARNESS_VERSION, baseline_path=...)`. Also widen the constant's comment to name `_CONTAINER_SAMPLE` and the exclusion sets as bump triggers.

---

## F7 (P2) — A cache hit silently drops `coverage_gaps` and `killed_by_crash`, so the "fix the map, not the tests" warning and the "these kills were free" caveat vanish on replay

**DISPOSITION -- RESOLVED.** Same as 20-F10.

**Claim.** The persisted entry stores only `fingerprint, mutants_run, killed, truncated, candidates_total, sampled_containers, survivors`; the replayed `MutationRun` therefore always has `killed_by_crash=0` and `coverage_gaps=[]`, whatever the original run found.

**Location.** `src/py_ci_shared/mutation_teeth.py:1035-1054` (write omits both) and `:925-941` (read constructs `MutationRun` without them, taking the dataclass defaults at `:187-189`).

**DEMONSTRATED.** Scratch repo `scratchpad/demo3`: primary tests that only import the module, `fallback_test_paths` that actually kill every mutant.

```
fresh : 4 mutants run, 4 killed, 0 survived; 4 'survivors' were killed by a test the coverage
        map does not list -- fix the map, not the tests   gaps=4 crash=0
cached: 4 mutants run, 4 killed, 0 survived; (cached: nothing in the import closure changed)
        gaps=0 crash=0
```

**Why a reader is misled.** The consumer prints `COVERAGE MAP GAP:` lines from `outcome.coverage_gaps`. On the first run the reader is told the map is wrong; on every later run the same tree reports a clean map. A genuinely wrong `_COVERAGE` entry is announced once and then never again. `killed_by_crash` fails the other way: a cached summary omits "N of the kills were CRASHES, not assertions", so a kill count the module deliberately annotates as overstated is replayed unannotated.

**Remedy.** Persist both fields (coverage gaps as full mutant dicts, like survivors) and restore them on read.

---

## F8 (P2) — pytest ini configuration, the interpreter, the installed dependency set and the active plugin set are outside the fingerprint, and two of them change what the harness itself does

**DISPOSITION -- RESOLVED.** The interpreter version and the installed pytest plugin set (name and version) now enter the digest. `pytest.ini`/`pyproject` `addopts` are still outside it and stated as such: `PYTEST_ADDOPTS` is cleared for nested runs by design, and reading a consumer's ini would duplicate pytest's own resolution rules badly.

**Claim.** The digest covers first-party source only. Several inputs that demonstrably change pass/fail are excluded; `conftest.py` is covered but `pyproject.toml` / `pytest.ini` / `setup.cfg` / `tox.ini` are not, though they carry `addopts`, markers and collection rules.

**Location.** `src/py_ci_shared/mutation_teeth.py:613-627` (the file set), `:725-741` (`_pytest_flags` branches on `importlib.util.find_spec("xdist")` and `("pytest_cov")` at *run time*), `:704` (`_pytest_env` clears `PYTEST_ADDOPTS` but cannot touch ini `addopts`), `:943` (`_COPY_IGNORE` excludes `.venv`, so the sandbox always runs against the outer interpreter's installed packages).

**READ.**

- **Python version / interpreter — REAL.** `sys.executable` runs every mutant. Switching venv or upgrading the interpreter changes behaviour with zero local diff, and the cache replays. This machine has more than one Python in play.
- **Installed third-party versions — REAL.** Named in the `fingerprint` docstring as a known limit, but stated only there: `MutationRun.summary()` says "nothing in the import closure changed", which a reader reasonably hears as "nothing that matters changed".
- **pytest ini `addopts` / markers — REAL for this consumer.** Its suite is marker-driven (`@pytest.mark.slow`, `--run-mutation-teeth`); an `addopts` change in `pyproject.toml` alters what the nested run collects, and conftest coverage does not reach it.
- **Plugin set — REAL but self-limiting.** Installing `pytest-xdist` or `pytest-cov` changes the nested command line (`-n 0`, `--no-cov`); a cached result was measured under the other configuration.
- **Environment variables, clock, database — REAL for this consumer** (`db/pool.py` is in three of the six closures) and documented, but again only in a docstring.

**Remedy.** Add to the digest: `sys.version_info[:3]`, the resolved `sys.executable`, a hash of the ini file in force, and the sorted `(name, version)` of installed distributions (at minimum the plugins `_pytest_flags` branches on). For dimensions that genuinely cannot be captured (database, clock), say so in `summary()` on a cache hit rather than only in a docstring — see F10.

---

## F9 (P2) — The cache file is rewritten non-atomically under a read-modify-write, so an interrupt destroys every entry and concurrent runs lose each other's

**DISPOSITION -- RESOLVED.** The cache is written to a staging file and renamed.

**Claim.** `cache_file.write_text(...)` at `:1056` truncates in place; there is no temp-file-plus-rename and no lock, while the surrounding code reads the whole dict at `:1032` and writes the whole dict back.

**Location.** `src/py_ci_shared/mutation_teeth.py:1030-1056`; the read-side swallow at `:917-919`.

**READ.**

**Scenario.** A Ctrl-C — routine, since these runs take minutes and are opt-in — during the write leaves truncated JSON. The next run's `json.loads` raises, is caught at `:918`, and the cache silently becomes `{}`: every target's entry for the whole repo is gone, and the next write cements the loss. The direction is fail-safe (a re-run, not a wrong verdict), so the cost is time; but it is invisible, and the identical swallow at `:1032-1034` means the operator never learns the cache was discarded. Two concurrent processes each read-modify-write the full dict, so the slower one's entry silently wins and the other's minutes of work are dropped.

**Also here (P3):** the hit path at `:925-939` indexes `entry["survivors"]`, `entry["mutants_run"]`, `entry["killed"]`, `entry["truncated"]`, `entry["candidates_total"]` unguarded. A cache file from an older schema whose `fingerprint` happens to match raises `KeyError` — not a `MutationHarnessError`, so the consumer's `except MutationHarnessError` does not turn it into a readable failure.

**Remedy.** Write to `cache_file.with_suffix(".tmp")` then `os.replace`; wrap the hit-path construction in `try/except (KeyError, TypeError)` and treat a malformed entry as a miss; log a one-line notice when a cache file fails to parse instead of silently emptying it.

---

## F10 (P2) — The documented limits are docstring-only; the cache-hit message asserts more than the fingerprint knows

**DISPOSITION -- RESOLVED.** The cache-hit line now reads `REPLAYED FROM CACHE, not measured: no fingerprinted input changed`, which is what the fingerprint knows. It also now appears as a warning on the green path (20-F8), so the claim is visible where it was previously printed only into a captured buffer.

**Claim.** The `fingerprint` docstring names four categories it cannot see (data files, dynamic imports, third-party versions, the environment) and concludes "a cache hit means *nothing statically reachable changed*, not *the answer is certainly the same*" — but the string an operator actually reads says the opposite in reassuring terms.

**Location.** `src/py_ci_shared/mutation_teeth.py:598-611` (the honest docstring) vs `:210-211`:
```python
if self.from_cache:
    parts.append("(cached: nothing in the import closure changed)")
```

**READ.**

**Scenario.** The reader sees `models.py: 12 mutants run, 12 killed, 0 survived; (cached: nothing in the import closure changed)`. Given F2 the import closure genuinely *did* change (a sibling was edited) and the harness cannot know it; given F8 the interpreter may have changed too. The parenthetical converts an unknown into a stated fact.

**Remedy.** Reword to what is true, e.g. `(CACHED from a previous run; only first-party source, listed tests, conftests and extra_fingerprint_paths are checked — dependency versions, the interpreter and the environment are not)`. Nothing else in the module needs to change for this to be worth doing.

---

## F11 (P3) — A cached clean result is invisible in `assert_no_new_surviving_mutant`'s success path

**DISPOSITION -- RESOLVED.** Closed by the same warning as 20-F8.

**Claim.** `outcome.summary()` — the only place `from_cache` surfaces — is passed to `baseline.enforce` as *guidance*, i.e. shown when something fails. A run with no new survivors prints nothing, so a cached clean and a freshly measured clean are indistinguishable to a reader of the library's own assert helper.

**Location.** `src/py_ci_shared/mutation_teeth.py:1084-1099`.

**READ.**

**Real vs theoretical.** Theoretical in the *named* consumer, which does not use this helper: it calls `find_surviving_mutants` directly and prints `outcome.summary()` for every target under `capsys.disabled()`, so `(cached: ...)` is always visible there. REAL for any other consumer taking the shorter, advertised route through `assert_no_new_surviving_mutant`.

**Remedy.** Emit a one-line note (print or `warnings.warn`) on the success path when `outcome.from_cache` is true.

---

## F12 (P3) — Dynamic imports, module `__getattr__` and `sys.path` manipulation are outside the walk

**DISPOSITION -- WON'T FIX.** Dynamic imports, module `__getattr__` and `sys.path` manipulation are outside any static walk, and pretending otherwise would be worse than the documented gap. They are named in `fingerprint`'s docstring alongside data files, third-party versions and the environment, and `extra_fingerprint_paths` exists for the part a caller CAN name. The honest position is that a cache hit means 'nothing statically reachable changed', which the message now says in those words.

**Claim.** `_first_party_imports` is a static `ast.walk` over `Import`/`ImportFrom` only; `importlib.import_module(name)`, entry-point plugin registries, and modules reached through a module-level `__getattr__` never enter the closure.

**Location.** `src/py_ci_shared/mutation_teeth.py:558-570`.

**Real vs theoretical — mostly theoretical here, with one live instance.** Grepping the consumer's non-test tree for `importlib.import_module` / `entry_points` returns nothing, so the dynamic-import hole is not currently exercised. There *is* a module `__getattr__` at `realtime_applications/db/__init__.py:168` with a documented open-ended fallback to `db.pool`; `db/pool.py` is in the closure anyway (via `from . import pool as _pool_mod`), so it is covered by accident rather than design. Note that imports inside function bodies ARE caught, since `ast.walk` is unconditional — verified by reading, and it is the one sub-case of this family that is sound.

**Remedy.** None beyond documenting; `extra_fingerprint_paths` is the intended escape hatch and the consumer uses it correctly.

---

## What I checked and found SOUND

- **`HARNESS_VERSION` is genuinely mixed into the digest** — `digest = hashlib.sha256(HARNESS_VERSION.encode())` at `:628` seeds the hash, so bumping it does invalidate every entry. The gap is F6 (nothing forces the bump), not the wiring.
- **`extra_fingerprint_paths` works** — verified by running: naming `prompt.txt` makes an edit to it move the key, not naming it does not. The consumer passes `prompts/system_v3.txt` and `config.toml`, both present in the tree; the docstring's flagship data-file hole is genuinely closed for this consumer, including the `live_config` TOML read at call time.
- **conftest chaining is correct** — `:617-624` walks from each test file's directory up to `repo_root` inclusive, adding every `conftest.py`; the `parent == repo_root: break` guard terminates properly and the root conftest is included.
- **Both file *names* and *contents* are hashed** (`:635-637`), and `files` is a `set` iterated `sorted(...)`, so the digest is order-stable across runs — no spurious misses.
- **A deleted or renamed closure member does move the key**, because membership is discovered by `candidate.is_file()` and the file set itself differs.
- **Imports inside function bodies are captured** (unconditional `ast.walk`), contrary to what the "dynamic imports" caveat might suggest.
- **`use_cache=False` bypasses the read but still writes** (`:915` guards only the read) — correct, and it means the documented reset path (`use_cache=False`, or deleting the file) works.
- **A cache miss is fail-safe in every corruption case I could construct**: unparseable file, missing entry, mismatched fingerprint all fall through to a real run. No path returns a stale verdict *because* of corruption; the stale-verdict paths are F1, F2, F3, F4, F5, F8.
- **The baseline key is content-digested, not line-numbered** (`Mutant.key`, `:159-166`), so cache/baseline interaction does not suffer the line-shift false-all-clear that `code_audit_meta` documents.

---

## Summary (10 lines)

1. **F1 (P0, demonstrated):** `lines`/`limit`/`fallback_test_paths` are not in the cache key — a 4-mutant "0 survived" was replayed for a request whose true answer was 4 survivors; live here because `lines` comes from the git diff, which moves without file content.
2. **F2 (P0, demonstrated):** every `from .sibling import name` is dropped from the import closure; in this consumer that hides `stage2_prompt_builder/{experience,formatting,smiley,truncation,word_count}.py` from `user_prompt.py` and eight `db/*.py` modules from `llm_cache.py`.
3. **F3 (P1, demonstrated):** `src/`-layout repos fingerprint the target file and nothing else — including `py_ci_shared` itself.
4. **F4 (P1, demonstrated):** a directory in `test_paths` contributes `b"<missing>"`, so test edits are invisible; the same swallow makes a typo'd `extra_fingerprint_paths` entry silently inert.
5. **F5 (P1, read):** the key is taken before a multi-minute run and written after, so an edit mid-run pins a verdict to content that was never measured.
6. **F6 (P1, read):** `HARNESS_VERSION` is a human promise; the package ships `content_hash_version_bump_gate` — the exact enforcement for this — and does not apply it to itself.
7. **F7 (P2, demonstrated):** cached replays drop `coverage_gaps` and `killed_by_crash`, so "fix the map, not the tests" is announced once and never again.
8. **F8 (P2, read):** interpreter, installed versions, pytest ini `addopts` and plugin set are all outside the digest; the first three are real for this consumer.
9. **F9 (P2) / F10 (P2) / F11 (P3) / F12 (P3):** non-atomic cache write plus silent parse-failure reset; the cache-hit message claims more than the fingerprint knows; a cached clean is invisible in `assert_no_new_surviving_mutant`'s success path; dynamic-import/`__getattr__` edges are theoretical here.
10. **Sound:** `HARNESS_VERSION` wiring, `extra_fingerprint_paths` (correctly used here for `system_v3.txt` and `config.toml`), conftest chaining, digest order-stability, function-body imports, fail-safe behaviour on every corruption path, and the content-digested baseline key.
