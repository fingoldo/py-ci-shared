"""Shared check: a test that claims to pin a defect must be able to FAIL.

Sibling of :mod:`gate_integrity`. That module answers "a gate declared blocking must be able to
block"; this one answers "a test declared to pin a defect must be able to fail". Both exist because
the same thing keeps happening: a check that exists, is believed, and checks nothing.

WHERE THIS CAME FROM
--------------------
One audit round produced four green tests that were green against the very defect they named, each
read and believed by its author first:

1. The revert script used ``str.replace``, which silently does nothing when it does not match, so
   the defect was never reintroduced and the green run nearly passed for confirmation.
2. An example-extraction regex matched 14 quoted spans instead of 6, so the assertion was true
   regardless of the subject.
3. After that was tightened, the variety still came from a Spanish-language example the hello-word
   list did not recognise.
4. A word-count test chose the one input shape where the old and new thresholds AGREE.

Case 1 is a harness flaw, addressed by :func:`assert_revert_fails_tests`. Cases 2-4 are what
mutation testing finds, addressed by :func:`find_surviving_mutants`.

WHAT THIS CANNOT DO
-------------------
"Is this test meaningful?" is not decidable, and nothing here claims otherwise.

* **Mutation sensitivity is not correctness.** A test that PINS a defect is fully
  mutation-sensitive and completely wrong. The same round found eight such tests, all asserting
  that a missing verdict field should default to a refusal; mutation testing would have defended
  every one.
* **Equivalent mutants are unavoidable.** A change that cannot alter observable behaviour survives
  every test, correctly. Survivors are a reading list, not a verdict.
* **A mutation SCORE is not computed, deliberately.** Measured over four real modules: 79% of
  candidates came from two lookup tables, so the denominator tracks data size rather than logic;
  crash mutants inflate the numerator for free; and at least 20 of 293 were unkillable, putting the
  ceiling below 100% at an unknowable, module-dependent point. No threshold could be set and no
  trend would be comparable. The survivor list is the product; if a scalar is ever wanted, the
  defensible one is the survivor COUNT under a ratchet, not a ratio.
* **Code only.** A third of that round's fixes changed a PROMPT. There is no meaningful mutation of
  an English sentence, which is what :func:`assert_revert_fails_tests` is for.
* **The operator set is not exhaustive.** Comparisons, boolean operators, ``not``, arithmetic,
  numeric and string constants, and statement-level calls. Not: control-flow removal, argument
  reordering, exception-type swaps. "Every single-change mutation" would overstate it.

DESIGN NOTES, each earned
-------------------------
**Mutations are made on a COPY of the working tree, never in place.** An earlier version mutated the
live file and restored it in ``finally``. ``finally`` does not run when the process is killed, and
in one afternoon that left a production module mangled twice. Copying this project's largest
consumer measured 410 files / 5.0 MB / 1.74s, paid ONCE per run rather than per mutant. It is a copy
of the WORKING TREE, not ``git worktree add HEAD``, because the uncommitted change is usually the
fix under test. It also makes moot a whole class of hardening the in-place design needed: dirty-file
refusal, symlink refusal, atomic writes, backup files and a recovery entry point.

**Edits are made at TOKEN level, not by re-rendering an AST node.** Two earlier attempts failed
here. ``ast.unparse`` of a whole module discards every comment, so the "mutant" was a reformatted
skeleton and a survivor meant nothing. Splicing a node's source span using ``col_offset`` was worse:
``col_offset`` is a UTF-8 BYTE offset while the source is a ``str``, so one non-ASCII character
earlier on the line silently moved the edit -- on a verified input the whole statement became
``a >= b``, reported at a line that had not been touched. A false accusation, not noise. Token
positions are character-based and each edit is one token, so a mutant is the original file with one
operator changed and every comment intact.

**Verified sound and deliberately not defended against:** CRLF line endings and tab indentation
both round-trip correctly through the token splice -- checked by execution rather than assumed,
because a harness that mangles a Windows file would be worse than none in these repos. There is
no special handling for either, and none is needed.

**Exit codes are classified, not truth-tested.** ``returncode != 0`` used to mean "killed". pytest
exits 4 on a usage error and 5 when it collects nothing, so a typo in ``test_paths`` reported every
mutant killed and the run as a clean bill of health -- precisely what :mod:`gate_integrity` exists
to prevent. There is now a mandatory unmutated baseline run, and any exit code other than 0 or 1
aborts loudly.

**Results are cached on a fingerprint of everything that could change the answer.** Re-running a
mutation check that cannot have changed is the dominant cost in a hook. The fingerprint covers the
target module's TRANSITIVE first-party import closure -- not just the file itself -- because the
function under test can be unchanged while a function it calls is not. What it cannot see is stated
at :func:`fingerprint`; ``extra_fingerprint_paths`` exists for the part no static analysis reaches.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import shutil
import tempfile
import threading
import time
import warnings
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Baseline as CoreBaseline
from ._core import BaselineError, atomic_write_text, refresh_requested, register_refresh_options
from ._mutation_fingerprint import _first_party_imports as _first_party_imports
from ._mutation_fingerprint import _installed_pytest_plugins as _installed_pytest_plugins
from ._mutation_fingerprint import _python_files_patterns as _python_files_patterns
from ._mutation_fingerprint import fingerprint
from ._mutation_model import _CONTAINER_SAMPLE as _CONTAINER_SAMPLE
from ._mutation_model import _COPY_IGNORE as _COPY_IGNORE
from ._mutation_model import _UNJUSTIFIED as _UNJUSTIFIED
from ._mutation_model import HARNESS_VERSION, REFRESH_FLAG, Mutant, MutationHarnessError, MutationRun, SourceText
from ._mutation_model import _mutant_from_json as _mutant_from_json
from ._mutation_model import _mutant_json as _mutant_json
from ._mutation_model import read_target as read_target
from ._mutation_model import write_target as write_target
from ._mutation_operators import _NAME_SWAP as _NAME_SWAP
from ._mutation_operators import _OP_SWAP as _OP_SWAP
from ._mutation_operators import _SYNTAX_RISKY_PREFIXES as _SYNTAX_RISKY_PREFIXES
from ._mutation_operators import _argument_transpositions as _argument_transpositions
from ._mutation_operators import _container_members as _container_members
from ._mutation_operators import _container_rows as _container_rows
from ._mutation_operators import _excluded_ranges as _excluded_ranges
from ._mutation_operators import _normalise_lines, _scope_of, generate_mutants
from ._mutation_operators import _repr_coupled_lines as _repr_coupled_lines
from ._mutation_operators import _slice_bound_candidates as _slice_bound_candidates
from ._mutation_operators import _statement_call_candidates as _statement_call_candidates
from ._mutation_operators import _substitution_candidates as _substitution_candidates
from ._mutation_operators import _token_candidates as _token_candidates
from ._mutation_runner import _MUTATION_BROKE_THE_RUN as _MUTATION_BROKE_THE_RUN
from ._mutation_runner import _classify as _classify
from ._mutation_runner import _classify_code as _classify_code
from ._mutation_runner import _first_failing_file as _first_failing_file
from ._mutation_runner import _kill_tree as _kill_tree
from ._mutation_runner import _killed_by_crash as _killed_by_crash
from ._mutation_runner import _lead_with as _lead_with
from ._mutation_runner import _pytest_env as _pytest_env
from ._mutation_runner import _pytest_flags as _pytest_flags
from ._mutation_runner import _remove_tree as _remove_tree
from ._mutation_runner import _run_pytest as _run_pytest
from ._mutation_runner import _WarmRunner as _WarmRunner
from ._mutation_worker import _CRASH_EXCEPTIONS as _CRASH_EXCEPTIONS

__all__ = [
    "HARNESS_VERSION",
    "Mutant",
    "MutationHarnessError",
    "MutationRun",
    "REFRESH_FLAG",
    "assert_no_new_surviving_mutant",
    "assert_revert_fails_tests",
    "find_surviving_mutants",
    "fingerprint",
    "generate_mutants",
    "sweep_files",
    "register_refresh_option",
]

PathArg = Union[Path, str]

#: How long a cache writer waits for another process's write to finish before giving up on storing.
_CACHE_LOCK_TIMEOUT = 60.0


def _module_names(relative: Path) -> list[str]:
    """The dotted names *relative* can be imported as, most specific first, never a bare name inside a package.

    ``src/pkg/mod.py`` -> ``["src.pkg.mod", "pkg.mod"]``. A bare ``mod`` is left out for a file inside a
    package: an unrelated top-level module of that name would otherwise read as a shadowing copy.
    """
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return []
    shortest = min(2, len(parts))
    return [".".join(parts[i:]) for i in range(len(parts)) if len(parts) - i >= shortest]


def _check_origin(origin: Optional[dict[str, Any]], relative: Path) -> None:
    """Refuse a sweep whose tests import the target from somewhere other than the sandbox.

    An editable install, or a ``PYTHONPATH`` that points at the real checkout, makes the tests load
    the ORIGINAL module; every mutant then survives, and the survivor list accuses tests that are fine.
    """
    if not origin or origin.get("sandbox") or not origin.get("elsewhere"):
        return
    raise MutationHarnessError(
        f"the tests import {relative.as_posix()} from outside the mutation sandbox ({'; '.join(origin['elsewhere'][:3])}), "
        "so no mutant would ever be seen and every one would read as a survivor. Make the sandbox copy win the import: "
        "run from a layout where `repo_root` is on the path (pytest `pythonpath`/rootdir conftest), not an editable "
        "install or a PYTHONPATH pointing at the real checkout."
    )


def _verify_import_origin(sandbox: Path, relative: Path, test_paths: Sequence[PathArg], timeout: float) -> None:
    """The origin check for a run that does not use the warm worker: one warm session, used only to look."""
    with _WarmRunner(sandbox, timeout, target=sandbox / relative, module_names=_module_names(relative)) as probe:
        probe.run(test_paths, check_origin=True)
        _check_origin(probe.last_origin, relative)


def _sweep_partition(
    sandbox: Path,
    relative: Path,
    mutants: "list[Mutant]",
    test_paths: "Sequence[PathArg]",
    fallback_test_paths: "Sequence[PathArg]",
    timeout: float,
    use_warm_worker: bool,
    verify_baseline: bool = False,
) -> "tuple[list[Mutant], list[Mutant], list[Mutant], list[Mutant], list[Mutant], dict[str, float]]":
    """One sandbox's share of the mutants.

    Returns ``(survivors, coverage_gaps, inconclusive, run, crashed, budget)``, the middle lists being
    the mutants with that verdict. Every path in and out is a value: the caller merges, and nothing
    here touches state another partition can see. That is what makes running several of these at
    once safe on the harness's side -- whether it is safe on the CONSUMER's side is a property of its
    tests, which is why concurrency is opt-in.

    *verify_baseline* runs the unmutated tests cold in this sandbox first. An extra sandbox is a new
    directory, and a test that depends on its location must fail here, not on every mutant.
    """
    target = sandbox / relative
    original = read_target(target)
    if verify_baseline:
        baseline = _run_pytest(test_paths, sandbox, timeout)
        if baseline is None or not _classify(baseline, f"the unmutated baseline in {sandbox}"):
            raise MutationHarnessError(
                f"the unmutated tests do not pass in the extra sandbox {sandbox}, so its kills would mean nothing. "
                "Run with `jobs=1`, or make the tests independent of the directory they run in."
            )
    # The restore is in a `finally` because the sandbox may be SHARED across files: an exception
    # mid-sweep used to leave the last mutant on disk, which was harmless while every file got
    # its own throwaway copy and becomes contamination of the next file the moment one does not.
    try:
        return _sweep_mutants(sandbox, target, original, mutants, test_paths, fallback_test_paths, timeout, use_warm_worker, relative)
    finally:
        write_target(target, original, original.text)


def _wider_net(
    warm: Any, use_warm_worker: bool, ordered_wider: list[PathArg], sandbox: Path, timeout: float, what: str
) -> tuple[Optional[bool], Optional[str]]:
    """``(killed, killer file)`` from the wider net, or ``(None, None)`` when it could not answer in time.

    WARM, unlike the survivor confirmation. That one is cold on purpose -- it exists to escape state
    a purge cannot reach, which is why a warm survivor is a candidate rather than a finding. This one
    asks a different question: does a test OUTSIDE the map kill it? That needs a wider selection, not
    a fresh process. Measured, it was half of the dominant cost: on the weakly covered file the two
    cold runs per survivor were 43 of the 46 seconds a mutant took.

    A timeout is INCONCLUSIVE, never a survivor: a hang in the net says nothing about the map.
    """
    wider_code = warm.run(ordered_wider) if use_warm_worker else None
    if wider_code is not None:
        return not _classify_code(wider_code, f"fallback re-check {what}", baseline_verified=True), warm.last_failed
    if use_warm_worker and warm.last_timed_out:
        warm.restart()
        return None, None
    # The worker died or was never used: fall back to the cold path rather than lose the answer.
    wider = _run_pytest(ordered_wider, sandbox, timeout)
    if wider is None:
        return None, None
    return not _classify(wider, f"fallback re-check {what}", baseline_verified=True), _first_failing_file(wider.stdout)


def _sweep_mutants(
    sandbox: Path,
    target: Path,
    original: "SourceText | str",
    mutants: "list[Mutant]",
    test_paths: "Sequence[PathArg]",
    fallback_test_paths: "Sequence[PathArg]",
    timeout: float,
    use_warm_worker: bool,
    relative: Optional[Path] = None,
) -> "tuple[list[Mutant], list[Mutant], list[Mutant], list[Mutant], list[Mutant], dict[str, float]]":
    """The loop itself, split out so the restore above can wrap it in a `finally`."""
    source = original if isinstance(original, SourceText) else SourceText(original)
    survivors: list[Mutant] = []
    coverage_gaps: list[Mutant] = []
    inconclusive: list[Mutant] = []
    judged: list[Mutant] = []
    crashed: list[Mutant] = []
    relative = relative if relative is not None else Path(target.name)
    names = _module_names(relative)

    def restore() -> None:
        write_target(target, source, source.text)

    with _WarmRunner(sandbox, timeout, target=target, module_names=names) as warm:
        if use_warm_worker:
            warm_baseline = warm.run(test_paths, check_origin=True)
            if warm_baseline is None:
                use_warm_worker = False
            else:
                _check_origin(warm.last_origin, relative)
                if not _classify_code(warm_baseline, "the unmutated baseline in the warm worker"):
                    raise MutationHarnessError(
                        "the tests pass in a fresh process but fail inside the reused worker, so "
                        "every mutant would be recorded as killed for a reason unrelated to the "
                        "mutation. Re-run with `use_warm_worker=False` to get a usable result, and "
                        "treat the difference between the two processes as the bug."
                    )
        ordered = list(test_paths)
        # The wider net gets the same treatment as the primary set, and for a bigger prize: it is
        # dozens of files, `-x` stops at the first failure, and the measurement says these re-checks
        # are where a sweep's time goes -- 88% of the wall clock on the worst-covered file.
        ordered_wider = list(fallback_test_paths)
        budget = {"purge": 0.0, "pytest": 0.0, "overhead": 0.0, "recheck": 0.0, "mutants": 0.0}

        def warm_timed_out() -> bool:
            """A warm run that overran is INCONCLUSIVE, and the worker is replaced for the next mutant.

            Re-running it cold would pay the same timeout a second time for the same answer.
            """
            if not warm.last_timed_out:
                return False
            warm.restart()
            return True

        for mutant in mutants:
            _mutant_started = time.perf_counter()
            _judged_at = _mutant_started
            write_target(target, source, mutant.mutated_file_text)
            try:
                code = warm.run(ordered) if use_warm_worker else None
                _judged_at = time.perf_counter()
                if warm.last_timings:
                    budget["purge"] += warm.last_timings.get("t_purge", 0.0)
                    budget["pytest"] += warm.last_timings.get("t_pytest", 0.0)
                    # What the parent spent that the worker did not: the pipe, the JSON, and the two
                    # whole-file writes around every mutant.
                    budget["overhead"] += max(0.0, (_judged_at - _mutant_started) - warm.last_timings.get("t_total", 0.0))
                if warm.last_failed:
                    ordered = _lead_with(ordered, warm.last_failed)
                if code is None and use_warm_worker and warm_timed_out():
                    inconclusive.append(mutant)
                    continue
                if code is None:
                    if use_warm_worker and (warm.process is None or warm.process.poll() is not None):
                        warm.restart()  # died rather than timed out: the next mutant gets a live worker again
                    result = _run_pytest(test_paths, sandbox, timeout)
                    if result is None:
                        inconclusive.append(mutant)
                        continue
                    passed = _classify(result, f"mutant {mutant}", baseline_verified=True)
                    crash = not passed and (result.returncode in _MUTATION_BROKE_THE_RUN or _killed_by_crash(result.stdout))
                else:
                    passed = _classify_code(code, f"mutant {mutant}", baseline_verified=True)
                    crash = not passed and (code in _MUTATION_BROKE_THE_RUN or warm.last_crash)
                if not passed:
                    judged.append(mutant)
                    if crash:
                        crashed.append(mutant)
                    continue
                confirm = _run_pytest(test_paths, sandbox, timeout)
                if confirm is None:
                    inconclusive.append(mutant)
                    continue
                if not _classify(confirm, f"survivor re-check {mutant}", baseline_verified=True):
                    # Warm said it survived and a fresh process kills it: state leaked between warm
                    # runs. The cold verdict is the one that holds.
                    judged.append(mutant)
                    if confirm.returncode in _MUTATION_BROKE_THE_RUN or _killed_by_crash(confirm.stdout):
                        crashed.append(mutant)
                    continue
                if fallback_test_paths:
                    wider_killed, wider_killer = _wider_net(warm, use_warm_worker, ordered_wider, sandbox, timeout, str(mutant))
                    if wider_killed is None:
                        inconclusive.append(mutant)
                        continue
                    if wider_killed:
                        # Record WHICH file killed it. Without this the report says "fix the
                        # map" and leaves the operator to find the one test among dozens --
                        # and adding them all is precisely what the map exists to avoid.
                        if wider_killer:
                            object.__setattr__(mutant, "category", f"killed-by:{wider_killer}")
                            # Lead with it next time. Gaps cluster: the same unlisted test
                            # usually kills many of them, and on the file that produced 65 gaps
                            # every one named the same killer.
                            ordered_wider = _lead_with(ordered_wider, wider_killer)
                        judged.append(mutant)
                        coverage_gaps.append(mutant)
                        continue
                judged.append(mutant)
                survivors.append(mutant)
            finally:
                restore()
                budget["mutants"] += 1
                # Everything after the verdict is a cold re-check: the confirmation and the wider net.
                budget["recheck"] += max(0.0, time.perf_counter() - _judged_at)
    return survivors, coverage_gaps, inconclusive, judged, crashed, budget


# ── the cache ─────────────────────────────────────────────────────────────────────────────────────
def _scope_key(lines: Optional[list[range]], limit: Optional[int], fallback_test_paths: Sequence[PathArg], allow_empty: bool) -> object:
    """Everything that changes WHICH mutants run, for the fingerprint.

    ``lines=None`` (the whole file) and ``lines=[]`` (nothing) are different scopes and key differently.
    """
    return (_scope_of(lines), limit, [str(t) for t in (fallback_test_paths or ())], bool(allow_empty))


def _try_lock(fd: int) -> bool:
    """One non-blocking attempt at an exclusive lock on *fd*."""
    try:
        if os.name == "nt":
            msvcrt: Any = importlib.import_module("msvcrt")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl: Any = importlib.import_module("fcntl")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            msvcrt: Any = importlib.import_module("msvcrt")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl: Any = importlib.import_module("fcntl")
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _cache_lock(cache_file: Path, timeout: float = _CACHE_LOCK_TIMEOUT) -> Iterator[None]:
    """An exclusive lock on a sibling ``.lock`` file, held across the cache's read-modify-write.

    Two sweeps (pytest-xdist workers, two hooks) that stored at once each read the old file and
    the last writer silently discarded the other's entry; both also staged into one fixed ``.tmp``.
    """
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = cache_file.with_name(cache_file.name + ".lock")
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"could not lock {lock_path} within {timeout}s")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def _read_cache(cache_file: Path) -> dict[str, Any]:
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.is_file() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_cached_run(cache_file: Path, relative: Path, key: str) -> Optional[MutationRun]:
    entry = _read_cache(cache_file).get(relative.as_posix())
    if not isinstance(entry, dict) or entry.get("fingerprint") != key:
        return None
    try:
        return MutationRun(
            survivors=[_mutant_from_json(m) for m in entry["survivors"]],
            coverage_gaps=[_mutant_from_json(m) for m in entry.get("coverage_gaps", [])],
            inconclusive=[_mutant_from_json(m) for m in entry.get("inconclusive", [])],
            killed_by_crash=entry.get("killed_by_crash", 0),
            mutants_run=entry["mutants_run"],
            killed=entry["killed"],
            truncated=entry["truncated"],
            candidates_total=entry["candidates_total"],
            sampled_containers={k: (int(v[0]), int(v[1])) for k, v in entry.get("sampled_containers", {}).items()},
            wider_net_note=entry.get("wider_net_note", ""),
            from_cache=True,
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return None  # an entry written by something else is a miss, never a crash


def _store_cached_run(cache_file: Path, relative: Path, key: str, outcome: MutationRun) -> None:
    entry = {
        "fingerprint": key,
        "mutants_run": outcome.mutants_run,
        "killed": outcome.killed,
        "truncated": outcome.truncated,
        "candidates_total": outcome.candidates_total,
        "sampled_containers": {k: list(v) for k, v in outcome.sampled_containers.items()},
        # Every caveat travels with the result. Dropping these made a replay read BETTER than
        # the run it replayed, which is the one direction a cache must never fail in.
        "killed_by_crash": outcome.killed_by_crash,
        "coverage_gaps": [_mutant_json(m) for m in outcome.coverage_gaps],
        "inconclusive": [_mutant_json(m) for m in outcome.inconclusive],
        "wider_net_note": outcome.wider_net_note,
        "survivors": [_mutant_json(m) for m in outcome.survivors],
    }
    try:
        with _cache_lock(cache_file):
            existing = _read_cache(cache_file)
            existing[relative.as_posix()] = entry
            # Atomic, through a unique temp file: a process killed mid-write left a truncated
            # file, which the read path treats as an empty cache and so discards every other entry.
            atomic_write_text(cache_file, json.dumps(existing, indent=2, sort_keys=True) + "\n")
    except (OSError, TimeoutError) as exc:
        # Not storing costs one re-run; failing the sweep over it would discard a finished answer.
        warnings.warn(f"mutation teeth: result for {relative.as_posix()} not cached ({exc})", stacklevel=3)


def _run_partitions(
    sandbox: Path,
    relative: Path,
    representatives: list[Mutant],
    test_paths: Sequence[PathArg],
    fallback_test_paths: Sequence[PathArg],
    timeout: float,
    use_warm_worker: bool,
    jobs: int,
) -> list[tuple]:
    """Sweep *representatives* in up to *jobs* sandboxes at once; each result is one partition's verdict lists.

    `jobs=1` is the default and runs everything in the verified sandbox: a consumer whose tests
    share a port, a database or a fixed temp path gets FALSE KILLS from concurrency, and the
    harness cannot see that condition from outside. The speedup reported for this was measured on
    a loaded machine and is deliberately not claimed here.
    """
    partitions = [representatives[i::jobs] for i in range(jobs)] if jobs > 1 else [representatives]
    partitions = [p for p in partitions if p]
    if not partitions:
        return []
    sandboxes = [sandbox]
    extras_parent: Optional[Path] = None
    results: list[tuple] = [()] * len(partitions)
    errors: list[BaseException] = []

    def _one(slot: int) -> None:
        try:
            results[slot] = _sweep_partition(
                sandboxes[slot], relative, partitions[slot], test_paths, fallback_test_paths, timeout, use_warm_worker, verify_baseline=slot > 0
            )
        except BaseException as exc:
            errors.append(exc)

    try:
        if len(partitions) > 1:
            # Copied from the VERIFIED sandbox, not the live repo, into a directory this call owns
            # and removes: the repo may have changed since the baseline, and a fixed name next to a
            # shared sandbox already existed on the second file of a `sweep_files` run.
            extras_parent = Path(tempfile.mkdtemp(prefix="mutation_teeth_extra_"))
            for index in range(1, len(partitions)):
                extra = extras_parent / f"{index}" / sandbox.name
                shutil.copytree(sandbox, extra, ignore=_COPY_IGNORE, symlinks=False)
                sandboxes.append(extra)
        threads = [threading.Thread(target=_one, args=(i,)) for i in range(1, len(partitions))]
        for thread in threads:
            thread.start()
        _one(0)
        for thread in threads:
            thread.join()
    finally:
        if extras_parent is not None:
            _remove_tree(extras_parent)
    if errors:
        # A harness failure in any partition is a harness failure, full stop. Merging the
        # partitions that happened to finish would report a subset as if it were the whole.
        raise errors[0]
    return results


def _merge_verdicts(
    results: list[tuple], twins: dict[str, list[Mutant]], mutants: list[Mutant]
) -> tuple[list[Mutant], list[Mutant], list[Mutant], int, int, dict[str, float]]:
    """Every partition's verdicts, FANNED to each twin of the mutant that was run, in source order."""

    def fan_out(verdicts: Iterable[Mutant]) -> list[Mutant]:
        out: list[Mutant] = []
        for rep in verdicts:
            for twin in twins[rep.mutated_file_text]:
                if twin is not rep:
                    object.__setattr__(twin, "category", rep.category or twin.category)
                out.append(twin)
        return out

    survivors = fan_out(m for r in results for m in r[0])
    coverage_gaps = fan_out(m for r in results for m in r[1])
    inconclusive = fan_out(m for r in results for m in r[2])
    run = len(fan_out(m for r in results for m in r[3]))
    crashes = len(fan_out(m for r in results for m in r[4]))
    budget = {k: sum(r[5].get(k, 0.0) for r in results) for k in ("purge", "pytest", "overhead", "recheck", "mutants")}
    # Source order, not completion order: a survivor list that reorders itself between
    # runs is a diff nobody can read.
    order = {id(m): i for i, m in enumerate(mutants)}
    for verdicts in (survivors, coverage_gaps, inconclusive):
        verdicts.sort(key=lambda m: order[id(m)])
    return survivors, coverage_gaps, inconclusive, run, crashes, budget


def find_surviving_mutants(
    path: PathArg,
    test_paths: Sequence[PathArg],
    repo_root: PathArg,
    lines: "Iterable[range] | range | None" = None,
    limit: Optional[int] = None,
    timeout: float = 300.0,
    cache_path: Optional[PathArg] = None,
    use_cache: bool = True,
    use_warm_worker: bool = True,
    jobs: int = 1,
    _sandbox: Optional[Path] = None,
    extra_fingerprint_paths: Sequence[PathArg] = (),
    fallback_test_paths: Sequence[PathArg] = (),
    allow_empty: bool = False,
) -> MutationRun:
    """Mutants the tests did NOT catch.

    *path* and *test_paths* are relative to *repo_root*. The repository is COPIED to a temporary
    directory and every mutation happens there, so nothing this function does can damage the working
    tree -- including being killed outright, which is how the previous in-place design was found to
    be unsafe.

    *lines* narrows the sweep to line ranges (``None``: the whole file; an empty iterable: nothing).

    Raises :class:`MutationHarnessError` rather than returning a misleading empty result when the
    harness cannot do its job: an unreadable or unparsable target, an unmutated baseline that does not
    pass, tests that import the target from outside the sandbox, a pytest exit code that means
    something other than pass or fail, or -- unless *allow_empty* -- a scope that produced no mutant
    to run, because "nothing survived" and "nothing was checked" must not read the same.
    """
    repo_root = Path(repo_root).resolve()
    relative = Path(path)
    ranges = _normalise_lines(lines)
    if jobs < 1:
        raise ValueError(f"jobs must be at least 1, got {jobs}")

    cache_file = Path(cache_path) if cache_path else None
    # Anything that changes WHICH mutants run belongs in the key. Omitting these made raising
    # `limit` after a truncated sweep a silent no-op whenever a cache file was in play.
    scope = _scope_key(ranges, limit, fallback_test_paths, allow_empty)
    key = fingerprint(repo_root, relative, test_paths, extra_fingerprint_paths, scope=scope)
    if use_cache and cache_file and cache_file.is_file():
        cached = _load_cached_run(cache_file, relative, key)
        if cached is not None:
            return cached

    # A caller-owned sandbox (from `sweep_files`) is reused as-is: the copy, the cold
    # baseline and the worker start are the fixed costs a multi-file sweep should pay once.
    # `_sweep_partition` restores the target in a `finally`, so a shared tree is clean for
    # the next file even when a sweep raises.
    borrowed = _sandbox is not None
    sandbox_parent: Optional[Path] = None
    if _sandbox is not None:
        sandbox = _sandbox
    else:
        sandbox_parent = Path(tempfile.mkdtemp(prefix="mutation_teeth_"))
        sandbox = sandbox_parent / repo_root.name
    try:
        if not borrowed:
            shutil.copytree(repo_root, sandbox, ignore=_COPY_IGNORE, symlinks=False)
        target = sandbox / relative
        if not target.is_file():
            raise MutationHarnessError(
                f"{relative} does not exist under {repo_root}. " "`path` must be relative to `repo_root`, not absolute and not relative to the cwd."
            )

        mutants, candidates_total, sampled = generate_mutants(target, lines=ranges, limit=limit)
        if not mutants and not allow_empty:
            raise MutationHarnessError(
                f"no mutant of {relative.as_posix()} to run ({candidates_total} candidate(s) in scope, lines={_scope_of(ranges)}), "
                "so a pass would check nothing. Widen `lines`, or pass `allow_empty=True` where an empty scope is expected."
            )

        baseline = _run_pytest(test_paths, sandbox, timeout)
        if baseline is None:
            raise MutationHarnessError(
                f"the unmutated baseline did not finish within {timeout}s, so nothing can be concluded. " "Raise `timeout`, or narrow `test_paths`."
            )
        if not _classify(baseline, "the unmutated baseline"):
            raise MutationHarnessError(
                "the unmutated baseline does not pass, so no mutant result would mean anything.\n"
                "Fix the failing tests first, then re-run.\n"
                f"{baseline.stdout[-2000:]}"
            )
        if mutants and not use_warm_worker:
            # The warm path checks this on its own baseline; a cold-only run needs one look of its own.
            _verify_import_origin(sandbox, relative, test_paths, timeout)

        # The wider net decides verdicts too -- a failure in it RECLASSIFIES a survivor as a
        # coverage-map gap -- so it needs the same unmutated check the primary set gets. It was not
        # getting one, and the consequence was measured rather than imagined: a test that reads two
        # files from a SIBLING project raises FileNotFoundError inside the sandbox on every mutant,
        # pytest exits non-zero, and 65 real survivors in one module were reported as "killed by a
        # test the map does not list". That module then read as having no survivors at all, which is
        # the direction nobody investigates.
        #
        # A net that cannot run is not evidence, so it is dropped for this file and said out loud.
        wider_baseline_note = ""
        if fallback_test_paths:
            wider_baseline = _run_pytest(fallback_test_paths, sandbox, timeout)
            if wider_baseline is None or not _classify(wider_baseline, "the unmutated wider net"):
                broken = _first_failing_file(wider_baseline.stdout) if wider_baseline else None
                wider_baseline_note = (
                    "the wider net does not pass unmutated"
                    + (f" ({broken} fails in the sandbox)" if broken else "")
                    + " -- it cannot tell a coverage-map gap from a survivor, so it was NOT used"
                )
                fallback_test_paths = ()

        for mutant in mutants:
            object.__setattr__(mutant, "path", relative)
        # Two candidates can produce a byte-identical file -- an operator landing on a span a
        # container sampler already covers, most often. Running the twin is pure cost, but its
        # verdict must be FANNED to every colliding mutant rather than recorded once: each carries
        # its own baseline key, and a silently unrun twin turns an accepted entry stale.
        twins: dict[str, list[Mutant]] = {}
        for mutant in mutants:
            twins.setdefault(mutant.mutated_file_text, []).append(mutant)
        representatives = [group[0] for group in twins.values()]

        results = _run_partitions(sandbox, relative, representatives, test_paths, fallback_test_paths, timeout, use_warm_worker, jobs)
        survivors, coverage_gaps, inconclusive, run, crashes, budget = _merge_verdicts(results, twins, mutants)
        outcome = MutationRun(
            survivors=survivors,
            mutants_run=run,
            killed=run - len(survivors),
            # NOT gated on `limit`: container sampling drops candidates too, and gating on the
            # cap left the flag False while the report read as exhaustive. Twins are run, by
            # fan-out, so they are not omissions.
            truncated=candidates_total > len(mutants),
            candidates_total=candidates_total,
            sampled_containers=sampled,
            killed_by_crash=crashes,
            coverage_gaps=coverage_gaps,
            inconclusive=inconclusive,
            wider_net_note=wider_baseline_note,
            timings={k: round(v, 3) for k, v in budget.items()},
        )
    finally:
        if sandbox_parent is not None:
            _remove_tree(sandbox_parent)

    if cache_file:
        # Re-taken AFTER the run. The sweep takes minutes; anything edited during one would
        # otherwise be filed under its NEW fingerprint with a verdict measured on the OLD content,
        # and the next run would replay a clean result for code that was never swept. A changed
        # fingerprint means the result describes nothing that still exists, so it is discarded
        # rather than stored under either key.
        if fingerprint(repo_root, relative, test_paths, extra_fingerprint_paths, scope=scope) != key:
            return outcome
        _store_cached_run(cache_file, relative, key, outcome)
    return outcome


def register_refresh_option(parser) -> None:
    """Add ``--refresh-mutation-survivors-baseline`` (and the shared ``--py-ci-refresh``) to a repo's pytest options."""
    register_refresh_options(parser, flags=(REFRESH_FLAG,), help_suffix="accepted mutation survivors")


def _refresh_requested(request) -> bool:
    """xdist-safe: the parsed config first, then ``PY_CI_SHARED_REFRESH``, then ``sys.argv``."""
    return refresh_requested(REFRESH_FLAG, request)


def sweep_files(
    targets: "Sequence[tuple[PathArg, Sequence[PathArg]]]",
    repo_root: PathArg,
    *,
    lines_by_source: "dict[str, Iterable[range]] | None" = None,
    fallback_by_source: "dict[str, Sequence[PathArg]] | None" = None,
    **common,
) -> "dict[str, MutationRun]":
    """Sweep several files, paying the fixed per-sweep costs once.

    *targets* is ``[(source, test_paths), ...]``; *lines_by_source* and *fallback_by_source* carry
    the per-file arguments that differ. Everything in *common* is forwarded to
    :func:`find_surviving_mutants` unchanged, so this adds no behaviour of its own -- the results
    are the same objects, keyed by source, and each file keeps its own fingerprint and cache entry.

    The saving is the tree copy, the cold baseline and the worker start, which
    :func:`find_surviving_mutants` pays once per call and this pays once per sweep. It is a wrapper
    rather than a merge on purpose: merging the fingerprints would make one changed file invalidate
    every verdict, which is the opposite of what the cache exists for.
    """
    repo_root = Path(repo_root).resolve()
    lines_by_source = lines_by_source or {}
    fallback_by_source = fallback_by_source or {}
    out: dict[str, MutationRun] = {}
    shared: Optional[Path] = None
    parent: Optional[Path] = None
    try:
        for source, test_paths in targets:
            key = str(source)
            if shared is None:
                # Lazy: an all-cache-hit sweep copies nothing at all, which is the common case once
                # the cache is warm and the reason the copy is not hoisted out of the loop.
                parent = Path(tempfile.mkdtemp(prefix="mutation_teeth_"))
                shared = parent / repo_root.name
                shutil.copytree(repo_root, shared, ignore=_COPY_IGNORE, symlinks=False)
            out[key] = find_surviving_mutants(
                source,
                test_paths,
                repo_root,
                lines=lines_by_source.get(key),
                fallback_test_paths=fallback_by_source.get(key, ()),
                _sandbox=shared,
                **common,
            )
    finally:
        if parent is not None:
            _remove_tree(parent)
    return out


def _regenerate(baseline: Any, found: dict[str, str]) -> None:
    """Write the survivors' keys, each marked unjustified unless a human already wrote its note."""
    if isinstance(baseline, CoreBaseline):
        notes: dict[str, str] = {}
        if baseline.exists():
            try:
                notes = baseline.load()[1]
            except BaselineError:
                notes = {}
        baseline.save(Counter(found.keys()), {k: notes.get(k) or v for k, v in found.items()})
    else:
        baseline.regenerate(found)


def assert_no_new_surviving_mutant(
    path: PathArg,
    test_paths: Sequence[PathArg],
    repo_root: PathArg,
    baseline,
    request=None,
    *,
    fail_on_truncation: bool = False,
    **kwargs,
) -> None:
    """The ratchet half: fail on a survivor that is not already accepted.

    *baseline* is a :class:`py_ci_shared.baseline_ratchet.Baseline` or a :class:`py_ci_shared._core.Baseline`;
    its accepted entries carry a mandatory human note, which ``baseline_hygiene`` polices for free.
    Survivors are keyed on a digest of the mutated SITE rather than a line number, so an edit
    elsewhere in the file does not invalidate every accepted entry.

    A truncated run (``limit=``, or table sampling) passes with a warning unless *fail_on_truncation*.
    A refresh is refused for a run that is truncated, inconclusive or has coverage-map gaps: it would
    rewrite the baseline from a partial answer and drop entries for mutants that were never run.

    Mechanically-noisy survivors (a sampled data-table row, an unobservable constant) are reported
    grouped under their category rather than listed, and never silently dropped: a hidden
    suppression list is the pattern ``gate_integrity`` explicitly argues against.
    """
    outcome = find_surviving_mutants(path, test_paths, repo_root, **kwargs)
    found = {m.key: f"{_UNJUSTIFIED} {m}" for m in outcome.survivors}
    refresh = _refresh_requested(request)
    if outcome.inconclusive:
        raise AssertionError(
            f"{len(outcome.inconclusive)} mutant(s) could not be run to a verdict in " f"{path}, so this sweep does not support a pass.\n{outcome.summary()}"
        )
    if outcome.coverage_gaps:
        # NOT a survivor and NOT something to accept: a test that kills this already exists and is
        # missing from the caller's map. Reporting it without failing left the map wrong for as
        # long as anyone tolerated the line, and a cached replay then stopped mentioning it.
        listed = "\n".join(f"  {m}" for m in outcome.coverage_gaps)
        raise AssertionError(
            f"{len(outcome.coverage_gaps)} mutant(s) in {path} are killed by a test that the "
            f"coverage map does not list. Fix the MAP, not the tests:\n{listed}"
        )
    if outcome.truncated and (refresh or fail_on_truncation):
        why = "a refresh from a partial run would drop the accepted entries of every mutant it did not run" if refresh else "`fail_on_truncation` is set"
        raise AssertionError(f"mutation teeth for {path} is TRUNCATED and {why}.\n{outcome.summary()}")
    if refresh:
        # A refresh writes the KEYS, never the justifications. Regenerating with a plausible
        # placeholder turned "accept every survivor" into one command whose output was green --
        # which is the opposite of what a baseline is for. The marker below fails the very next
        # run until a human replaces it with a real reason.
        _regenerate(baseline, found)
        raise AssertionError(
            f"{len(found)} survivor(s) written to {baseline.path} WITHOUT justifications.\n"
            f"Each is marked {_UNJUSTIFIED!r} and will keep failing until you replace that marker "
            "with the reason the mutant cannot be observed. A refresh records what was found; it "
            "does not decide that the finding is acceptable."
        )
    if outcome.truncated or outcome.from_cache:
        # Not a failure -- a deliberate `limit` is a legitimate way to run this, and a cache hit is
        # the point of the cache. But on the green path NOTHING was printed, so an incomplete or
        # replayed result was indistinguishable from a complete measured one. A warning lands in
        # pytest's summary without stopping anyone.
        warnings.warn(f"mutation teeth for {path}: {outcome.summary()}", stacklevel=2)
    guidance = (
        f"{outcome.summary()}\n"
        "A survivor means the tests do not notice that change. Either add an assertion that "
        "does, or accept it in the baseline with a note saying why it cannot be observed."
    )
    if isinstance(baseline, CoreBaseline):
        result = baseline.enforce(list(found), describe=found, guidance=guidance)
        if not result.ok:
            raise AssertionError(f"mutation survivors not accounted for in {baseline.path}\n{result.message}\n{outcome.summary()}")
        return
    exit_code = baseline.enforce(found, label=f"mutation teeth: {path}", guidance=guidance)
    if exit_code:
        raise AssertionError(f"mutation survivors not accounted for in {baseline.path}\n{outcome.summary()}")


def assert_revert_fails_tests(
    path: PathArg,
    old: str,
    new: str,
    test_paths: Sequence[PathArg],
    repo_root: PathArg,
    timeout: float = 300.0,
) -> None:
    """Reintroduce a defect by hand and require the tests to notice.

    Named for what it does rather than as the ``assert_*`` sibling of :func:`find_surviving_mutants`
    -- that role belongs to :func:`assert_no_new_surviving_mutant`. This is for what mutation
    testing cannot reach: a changed prompt, a reordered call, anything where the defect is prose or
    structure rather than an operator.

    A helper rather than a snippet because the hand-written version used ``str.replace``, which
    silently does nothing when the anchor has drifted: the revert never happened, the tests passed,
    and the pass was read as proof of teeth. Both the presence of *old* and the fact that the text
    changed are checked, and the work happens in a copy so a failure cannot leave the tree modified.
    """
    repo_root = Path(repo_root).resolve()
    relative = Path(path)
    sandbox_parent = Path(tempfile.mkdtemp(prefix="mutation_teeth_"))
    sandbox = sandbox_parent / repo_root.name
    try:
        shutil.copytree(repo_root, sandbox, ignore=_COPY_IGNORE, symlinks=False)
        target = sandbox / relative
        if not target.is_file():
            raise MutationHarnessError(f"{relative} does not exist under {repo_root}")
        source = read_target(target)
        original = source.text
        if old not in original:
            raise MutationHarnessError(
                f"revert anchor not found in {relative}: {old[:80]!r}\n"
                "The defect was never reintroduced, so a green run would prove nothing. "
                "Re-copy the anchor from the current file."
            )
        mutated = original.replace(old, new, 1)
        if mutated == original:
            raise MutationHarnessError(f"the revert changed nothing in {relative} -- `old` and `new` are identical")

        baseline = _run_pytest(test_paths, sandbox, timeout)
        if baseline is None:
            raise MutationHarnessError(f"the unmutated baseline did not finish within {timeout}s")
        if not _classify(baseline, "the unmutated baseline"):
            raise MutationHarnessError(
                "the unmutated baseline does not pass, so the teeth check means nothing. " f"Fix the failing tests first.\n{baseline.stdout[-2000:]}"
            )

        write_target(target, source, mutated)
        result = _run_pytest(test_paths, sandbox, timeout)
        if result is None:
            raise MutationHarnessError(f"the mutated run did not finish within {timeout}s")
        if _classify(result, "the mutated run", baseline_verified=True):
            raise AssertionError(
                f"The tests PASSED with the defect reintroduced in {relative}.\n"
                f"Reverted: {old[:100]!r}\n"
                "Most likely they do not assert what they claim to; possibly the reverted line is "
                "not on any path they execute. Either way this check cannot vouch for them.\n\n"
                f"{result.stdout[-2000:]}"
            )
    finally:
        _remove_tree(sandbox_parent)
