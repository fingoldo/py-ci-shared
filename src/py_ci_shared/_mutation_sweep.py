"""The per-mutant verdict of the mutation sweep, and the wider-net and import-origin checks it relies on.

Split out of ``mutation_teeth`` to keep that module under its size budget. The sweep (``_sweep_mutants``) and the public entry points stay there;
this module decides one mutant at a time and keeps the state the sweep carries from one mutant to the next.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._mutation_model import Mutant, MutationHarnessError
from ._mutation_runner import (
    _MUTATION_BROKE_THE_RUN,
    _classify,
    _classify_code,
    _lead_with,
    _run_pytest,
    _scan_of,
    _WarmRunner,
)

PathArg = Union[Path, str]


@dataclass
class _SweepState:
    """What one sweep has decided so far, and the orderings that make the next mutant cheaper.

    ``ordered`` and ``ordered_wider`` put the test file that killed the last mutant first; ``judged_at`` is when the current mutant's verdict
    was reached, which splits the mutant's time between judging it and the cold re-checks after it.
    """

    ordered: "list[PathArg]"
    ordered_wider: "list[PathArg]"
    survivors: "list[Mutant]" = field(default_factory=list)
    coverage_gaps: "list[Mutant]" = field(default_factory=list)
    inconclusive: "list[Mutant]" = field(default_factory=list)
    judged: "list[Mutant]" = field(default_factory=list)
    crashed: "list[Mutant]" = field(default_factory=list)
    budget: "dict[str, float]" = field(default_factory=lambda: {"purge": 0.0, "pytest": 0.0, "overhead": 0.0, "recheck": 0.0, "mutants": 0.0})
    judged_at: float = 0.0


def _warm_timed_out(warm: "_WarmRunner") -> bool:
    """A warm run that overran is INCONCLUSIVE, and the worker is replaced for the next mutant.

    Re-running it cold would pay the same timeout a second time for the same answer.
    """
    if not warm.last_timed_out:
        return False
    warm.restart()
    return True


def _charge_warm_timings(warm: "_WarmRunner", state: _SweepState, started: float) -> None:
    """Add the warm worker's own timings to the budget; what the parent spent that the worker did not is overhead (the pipe, the JSON, and the two whole-file writes)."""
    if warm.last_timings:
        state.budget["purge"] += warm.last_timings.get("t_purge", 0.0)
        state.budget["pytest"] += warm.last_timings.get("t_pytest", 0.0)
        state.budget["overhead"] += max(0.0, (state.judged_at - started) - warm.last_timings.get("t_total", 0.0))


def _primary_verdict(
    mutant: "Mutant",
    warm: "_WarmRunner",
    use_warm_worker: bool,
    test_paths: "Sequence[PathArg]",
    sandbox: Path,
    timeout: float,
    state: _SweepState,
    started: float,
) -> "tuple[Optional[bool], bool]":
    """``(passed, crash)`` of one mutant on the primary tests: ``passed`` is True when it survives, False when killed, None when inconclusive.

    A warm verdict that says the mutant survived is confirmed cold, because state can leak between warm runs; the cold verdict is the one that holds.
    """
    code = warm.run(state.ordered) if use_warm_worker else None
    state.judged_at = time.perf_counter()
    _charge_warm_timings(warm, state, started)
    if warm.last_failed:
        state.ordered = _lead_with(state.ordered, warm.last_failed)
    if code is None and use_warm_worker and _warm_timed_out(warm):
        return None, False
    if code is None:
        if use_warm_worker and (warm.process is None or warm.process.poll() is not None):
            warm.restart()  # died rather than timed out: the next mutant gets a live worker again
        result = _run_pytest(test_paths, sandbox, timeout)
        if result is None:
            return None, False
        passed = _classify(result, f"mutant {mutant}", baseline_verified=True)
        crash = not passed and (result.returncode in _MUTATION_BROKE_THE_RUN or _scan_of(result).killed_by_crash)
    else:
        passed = _classify_code(code, f"mutant {mutant}", baseline_verified=True)
        crash = not passed and (code in _MUTATION_BROKE_THE_RUN or warm.last_crash)
    if not passed:
        return False, crash
    confirm = _run_pytest(test_paths, sandbox, timeout)
    if confirm is None:
        return None, False
    if not _classify(confirm, f"survivor re-check {mutant}", baseline_verified=True):
        return False, confirm.returncode in _MUTATION_BROKE_THE_RUN or _scan_of(confirm).killed_by_crash
    return True, False


def _judge(
    mutant: "Mutant",
    warm: "_WarmRunner",
    use_warm_worker: bool,
    test_paths: "Sequence[PathArg]",
    sandbox: Path,
    timeout: float,
    fallback_test_paths: "Sequence[PathArg]",
    relative: Path,
    state: _SweepState,
    started: float,
) -> None:
    """File one mutant under its verdict: killed, a coverage gap (killed only by the wider net), a survivor, crashed or inconclusive."""
    passed, crash = _primary_verdict(mutant, warm, use_warm_worker, test_paths, sandbox, timeout, state, started)
    if passed is None:
        state.inconclusive.append(mutant)
        return
    if not passed:
        state.judged.append(mutant)
        if crash:
            state.crashed.append(mutant)
        return
    if fallback_test_paths:
        wider_killed, wider_killer = _wider_net(warm, use_warm_worker, state.ordered_wider, sandbox, timeout, str(mutant))
        if wider_killed is None:
            state.inconclusive.append(mutant)
            return
        if wider_killed:
            # Record WHICH file killed it. Without this the report says "fix the
            # map" and leaves the operator to find the one test among dozens --
            # and adding them all is precisely what the map exists to avoid.
            if wider_killer:
                object.__setattr__(mutant, "category", f"killed-by:{wider_killer}")
                # Lead with it next time. Gaps cluster: the same unlisted test
                # usually kills many of them, and on the file that produced 65 gaps
                # every one named the same killer.
                state.ordered_wider = _lead_with(state.ordered_wider, wider_killer)
            state.judged.append(mutant)
            state.coverage_gaps.append(mutant)
            return
    state.judged.append(mutant)
    state.survivors.append(mutant)


def _check_unmutated(
    sandbox: Path,
    relative: Path,
    test_paths: "Sequence[PathArg]",
    fallback_test_paths: "Sequence[PathArg]",
    timeout: float,
    use_warm_worker: bool,
    have_mutants: bool,
    limit_name: None = None,
) -> "tuple[Sequence[PathArg], str]":
    """Refuse the sweep unless the unmutated tests pass; return the wider net to use and why it was dropped, if it was.

    The wider net decides verdicts too (a failure in it RECLASSIFIES a survivor as a coverage-map gap), so it gets the same unmutated check the
    primary set gets. A net that cannot run is not evidence, so it is dropped for this file and said out loud.
    """
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
    if have_mutants and not use_warm_worker:
        # The warm path checks this on its own baseline; a cold-only run needs one look of its own.
        _verify_import_origin(sandbox, relative, test_paths, timeout)
    wider_baseline_note = ""
    if fallback_test_paths:
        wider_baseline = _run_pytest(fallback_test_paths, sandbox, timeout)
        if wider_baseline is None or not _classify(wider_baseline, "the unmutated wider net"):
            broken = _scan_of(wider_baseline).first_failing_file if wider_baseline else None
            wider_baseline_note = (
                "the wider net does not pass unmutated"
                + (f" ({broken} fails in the sandbox)" if broken else "")
                + " -- it cannot tell a coverage-map gap from a survivor, so it was NOT used"
            )
            fallback_test_paths = ()
    return fallback_test_paths, wider_baseline_note


def _group_twins(mutants: "list[Mutant]") -> "dict[str, list[Mutant]]":
    """Mutants that produce a byte-identical file, grouped by that file.

    Two candidates can produce a byte-identical file -- an operator landing on a span a container sampler already covers, most often. Running the
    twin is pure cost, but its verdict must be FANNED to every colliding mutant rather than recorded once: each carries its own baseline key, and a
    silently unrun twin turns an accepted entry stale.
    """
    twins: dict[str, list[Mutant]] = {}
    for mutant in mutants:
        twins.setdefault(mutant.mutated_file_text, []).append(mutant)
    return twins


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
    return not _classify(wider, f"fallback re-check {what}", baseline_verified=True), _scan_of(wider).first_failing_file


def _verify_import_origin(sandbox: Path, relative: Path, test_paths: Sequence[PathArg], timeout: float) -> None:
    """The origin check for a run that does not use the warm worker: one warm session, used only to look."""
    with _WarmRunner(sandbox, timeout, target=sandbox / relative, module_names=_module_names(relative)) as probe:
        probe.run(test_paths, check_origin=True)
        _check_origin(probe.last_origin, relative)


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
