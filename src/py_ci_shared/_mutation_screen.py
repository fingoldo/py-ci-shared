"""A two-phase mutation sweep: screen every mutant on a cheap test set and environment, then re-judge the survivors on the full set under the real one.

The screen is for speed. A mutant it kills is killed for good: the unmutated code passed under the screen environment (the baseline check), and the
failing test is a real assertion. A mutant it lets through is only a candidate, because the screen can miss a kill in two ways: its test set omits the
test that kills it, or its environment changes what the code does (``NUMBA_DISABLE_JIT=1`` runs compiled kernels as plain Python, where integer
overflow and float corner cases behave differently). Each candidate is therefore re-run on the full set under the caller's own environment, and it
is reported as a survivor only when it survives there too.

What the screen cannot settle: a mutant that fails only under the screen environment and passes under the real one is a false kill, and the baseline
check does not catch it, because the baseline passes in both. Keep the screen environment to settings that do not change the semantics of the code
under test, and re-judge a sample of the screen's kills under the real environment when a change to the screen is made.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Union

from ._mutation_model import MutationHarnessError, MutationRun
from .mutation_teeth import find_surviving_mutants

logger = logging.getLogger(__name__)

PathArg = Union[Path, str]


@contextmanager
def _environment(overrides: Mapping[str, str]) -> Iterator[None]:
    """Set *overrides* in ``os.environ`` for the block and restore the previous values afterwards.

    Nested pytest runs and the warm worker copy ``os.environ`` when they start, so the screen environment reaches them without any change to how
    they are launched. Numba reads ``NUMBA_DISABLE_JIT`` when it is imported, so the screen runs in processes that start inside this block.
    """
    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def _with_tag(checkpoint: Any, tag: str) -> Any:
    """The checkpoint path with *tag* before its suffix, so the two phases never share one file (their verdicts are not interchangeable)."""
    if checkpoint is None:
        return None
    path = Path(checkpoint)
    return str(path.with_name(f"{path.stem}.{tag}{path.suffix}"))


def find_surviving_mutants_screened(
    path: PathArg,
    test_paths: Sequence[PathArg],
    repo_root: PathArg,
    *,
    screen_suites: Sequence[PathArg],
    screen_env: Mapping[str, str],
    **kwargs: Any,
) -> MutationRun:
    """:func:`find_surviving_mutants`, with the screen described in this module's docstring.

    *screen_suites* are the test files of the screen; *test_paths* the full set the confirmation uses. *screen_env* is set in the environment for the
    screen only. Every other argument is passed to both phases unchanged, except *limit* (applied to the screen alone) and *checkpoint_path* (split
    into two files).

    The result describes the full set's verdicts: ``survivors`` and ``inconclusive`` come from the confirmation, the kills of the screen stand, and
    ``mutants_run`` counts the screen's mutants. ``timings`` gains ``confirm_seconds``.

    Raises :class:`MutationHarnessError` when the confirmation regenerates fewer mutants than the screen's survivors, because a candidate that was not
    re-run cannot be reported as either killed or surviving.
    """
    if kwargs.get("use_cache"):
        raise ValueError("use_cache is not supported with a screen: a cache entry does not record the environment that produced it")
    checkpoint = kwargs.pop("checkpoint_path", None)
    limit = kwargs.pop("limit", None)
    shared = {**kwargs, "use_cache": False}

    with _environment(screen_env):
        screened = find_surviving_mutants(path, screen_suites, repo_root, limit=limit, checkpoint_path=_with_tag(checkpoint, "screen"), **shared)
    if not screened.survivors:
        return screened

    candidates = {mutant.key for mutant in screened.survivors}
    lines = sorted({mutant.line for mutant in screened.survivors})
    logger.warning(
        "mutation screen: %d survivors on %d screen files; re-judging them on the full set under the real environment", len(candidates), len(screen_suites)
    )
    started = time.perf_counter()
    confirmed = find_surviving_mutants(
        path,
        test_paths,
        repo_root,
        **{**shared, "lines": [range(line, line + 1) for line in lines]},
        checkpoint_path=_with_tag(checkpoint, "confirm"),
    )
    confirm_seconds = time.perf_counter() - started
    if confirmed.mutants_run < len(candidates) and not confirmed.from_cache:
        raise MutationHarnessError(
            f"the confirmation ran {confirmed.mutants_run} mutants for {len(candidates)} screen survivors: the mutant set changed between the two phases, "
            "so some candidates were neither re-judged nor killed"
        )

    re_judged = [mutant for mutant in confirmed.survivors if mutant.key in candidates]
    still_inconclusive = [mutant for mutant in confirmed.inconclusive if mutant.key in candidates]
    killed_on_confirmation = len(candidates) - len(re_judged) - len(still_inconclusive)
    logger.warning(
        "mutation screen: %d of %d candidates killed on the full set, %d survive it, %d inconclusive",
        killed_on_confirmation,
        len(candidates),
        len(re_judged),
        len(still_inconclusive),
    )
    timings = {**screened.timings, **confirmed.timings, "confirm_seconds": round(confirm_seconds, 3)}
    return MutationRun(
        survivors=list(confirmed.survivors),
        mutants_run=screened.mutants_run,
        killed=screened.killed + killed_on_confirmation,
        truncated=screened.truncated or confirmed.truncated,
        candidates_total=screened.candidates_total,
        sampled_containers=screened.sampled_containers,
        killed_by_crash=screened.killed_by_crash + confirmed.killed_by_crash,
        coverage_gaps=screened.coverage_gaps + confirmed.coverage_gaps,
        inconclusive=screened.inconclusive + [m for m in confirmed.inconclusive if m not in screened.inconclusive],
        wider_net_note=confirmed.wider_net_note or screened.wider_net_note,
        timings=timings,
        from_cache=False,
    )
