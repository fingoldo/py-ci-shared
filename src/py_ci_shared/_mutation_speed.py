"""The options of a mutation sweep that only change what a sweep COSTS: the baselines it runs, the timeout, the survivor re-check, the line map.

Split out of ``mutation_teeth`` and ``_mutation_sweep`` to keep them under their size budget. Nothing here decides a verdict by itself: every helper
either orders the test files, narrows them to files that execute the mutated line (opt-in), or avoids a run whose answer is already known.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import sys
import tempfile
import threading
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ._mutation_model import Mutant, MutationHarnessError
from ._mutation_runner import _classify, _plugin_isolation_flags, _pytest_env, _pytest_flags, _run_pytest, _scan_of, run_with_deadline

logger = logging.getLogger(__name__)

PathArg = Union[Path, str]

#: Never shorter than this: a per-mutant timeout derived from a fast baseline must still survive a loaded host.
AUTO_TIMEOUT_FLOOR = 30.0


@dataclass
class SweepOptions:
    """The cost options of one ``find_surviving_mutants`` call, shared by its partitions.

    Every default is the cheapest setting that leaves the verdicts as they were, except ``coverage_prune`` and ``confirm_in_worker``, which are opt-in
    because they can change a verdict when their assumption (coverage sees every execution; a second warm worker is as good as a fresh process) fails.
    """

    #: One baseline of the primary set (the warm worker's own first run), and the wider net's baseline only when a survivor needs it.
    single_baseline: bool = True
    #: Per-mutant timeout = clamp(max(30, factor * baseline seconds), cap); ``None`` keeps the constant ``timeout``.
    timeout_factor: Optional[float] = None
    #: Confirm a warm survivor in a second long-lived worker instead of a cold process; every ``cold_confirm_every``-th survivor is still cold.
    confirm_in_worker: bool = False
    cold_confirm_every: int = 10
    #: Build a line -> test files map from one coverage run; mutants on unexecuted lines skip the primary run, others run only covering files.
    coverage_prune: bool = False
    #: Extra pytest flags for every nested run (``-o addopts=`` when ``ignore_addopts``).
    extra_flags: "tuple[str, ...]" = ()
    #: Seconds the unmutated primary tests took, when a baseline measured it.
    baseline_seconds: Optional[float] = None
    coverage: "Optional[CoverageMap]" = None
    wider: "Optional[LazyWiderBaseline]" = None


def effective_timeout(cap: float, factor: Optional[float], baseline_seconds: Optional[float]) -> float:
    """The per-mutant timeout: *cap* unless *factor* and a measured baseline say a shorter one is safe.

    A timeout stays INCONCLUSIVE whatever its length, so a tighter value can cost a verdict but never invent one; the floor and the cap bound it.
    """
    if factor is None or baseline_seconds is None or baseline_seconds <= 0:
        return cap
    return min(cap, max(AUTO_TIMEOUT_FLOOR, factor * baseline_seconds))


class NearestKiller:
    """Which test file killed the mutants judged so far, by line: the next mutant leads with the killer of its nearest neighbour."""

    def __init__(self) -> None:
        self.by_line: dict[int, str] = {}
        self.counts: Counter = Counter()

    def add(self, line: int, killer: Optional[str]) -> None:
        if killer:
            self.by_line[line] = killer
            self.counts[killer] += 1

    def nearest(self, line: int) -> Optional[str]:
        if not self.by_line:
            return None
        return self.by_line[min(self.by_line, key=lambda known: (abs(known - line), known))]

    def order(self, paths: "list[PathArg]") -> "list[PathArg]":
        """*paths* with the most frequent killer first (the least frequent is led with first, so the most frequent ends in front)."""
        from ._mutation_runner import _lead_with

        out = list(paths)
        for killer, _ in reversed(self.counts.most_common()):
            out = _lead_with(out, killer)
        return out


class LazyWiderBaseline:
    """The wider net's unmutated check, run at most once and only when a survivor needs the net."""

    def __init__(self, paths: "Sequence[PathArg]") -> None:
        self.paths = list(paths)
        self.note = ""
        self._checked = False
        self._usable: "Sequence[PathArg]" = ()
        self._lock = threading.Lock()

    def resolve(self, sandbox: Path, timeout: float, extra_flags: Sequence[str] = ()) -> "Sequence[PathArg]":
        """The paths to use as the wider net: all of them when they pass unmutated, none (and a note) when they do not."""
        with self._lock:
            if not self._checked:
                result = _run_pytest(self.paths, sandbox, timeout, *extra_flags)
                if result is None or not _classify(result, "the unmutated wider net"):
                    broken = _scan_of(result).first_failing_file if result else None
                    self.note = (
                        "the wider net does not pass unmutated"
                        + (f" ({broken} fails in the sandbox)" if broken else "")
                        + " -- it cannot tell a coverage-map gap from a survivor, so it was NOT used"
                    )
                    self._usable = ()
                else:
                    self._usable = self.paths
                self._checked = True
            return self._usable


# ── the line map ────────────────────────────────────────────────────────────────────────────────


@dataclass
class CoverageMap:
    """``lines``: statement first-line -> the test files that execute it (``None``: every file, e.g. import time).

    ``starts`` maps every line of a statement (continuation lines included) to its first line, so a mutant on the second line of a call is judged by
    the statement it belongs to. A line that is part of no statement is unknown and treated as covered by everything.
    """

    lines: "dict[int, Optional[list[str]]]"
    starts: "dict[int, int]" = field(default_factory=dict)

    def files_for(self, mutant: Mutant) -> "Optional[list[str]]":
        """``None``: run everything. ``[]``: no test executes the mutated statement. Otherwise the covering files."""
        end = mutant.line + mutant.original_span.count("\n")
        found: "set[str]" = set()
        for line in range(mutant.line, end + 1):
            start = self.starts.get(line)
            if start is None:
                return None
            if start not in self.lines:
                continue
            files = self.lines[start]
            if files is None:
                return None
            found.update(files)
        return sorted(found)

    def to_json(self) -> "dict[str, Any]":
        return {"lines": {str(k): v for k, v in self.lines.items()}, "starts": {str(k): v for k, v in self.starts.items()}}

    @classmethod
    def from_json(cls, data: Any) -> "Optional[CoverageMap]":
        try:
            lines = {int(k): (None if v is None else [str(x) for x in v]) for k, v in data["lines"].items()}
            starts = {int(k): int(v) for k, v in data["starts"].items()}
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        return cls(lines, starts)


def statement_starts(source: str) -> "dict[int, int]":
    """Every line of every simple statement (and of a compound statement's header) -> the statement's first line."""
    tree = ast.parse(source)
    out: "dict[int, int]" = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            end = max(node.lineno, body[0].lineno - 1) if not isinstance(node, ast.Try) else node.lineno
        for line in range(node.lineno, end + 1):
            # The innermost (latest-starting) statement wins, so a nested statement is not claimed by its parent's span.
            if line not in out or out[line] <= node.lineno:
                out[line] = node.lineno
    return out


def _context_files(context: str, test_files: "Sequence[str]") -> "Optional[list[str]]":
    """The test files a coverage context (``pkg.test_mod.TestC.test_fn``) belongs to; ``None`` when it names none of them (so: every file)."""
    if not context:
        return None
    parts = set(context.split("."))
    matched = [f for f in test_files if Path(f).stem in parts]
    return matched or None


def expand_test_files(sandbox: Path, test_paths: "Sequence[PathArg]") -> "list[str]":
    """The ``.py`` test files *test_paths* name (directories walked), as posix paths relative to *sandbox*."""
    files: "list[str]" = []
    for entry in test_paths:
        base = sandbox / str(entry).split("::")[0]
        if base.is_dir():
            found = sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
        else:
            found = [base]
        files.extend(p.relative_to(sandbox).as_posix() for p in found if p.suffix == ".py")
    return list(dict.fromkeys(files))


def build_coverage_map(
    sandbox: Path, relative: Path, test_paths: "Sequence[PathArg]", timeout: float, extra_flags: Sequence[str] = ()
) -> "tuple[Any, Optional[CoverageMap]]":
    """Run the unmutated primary tests once under coverage; return ``(the pytest result, the map or None)``.

    The result is the baseline's, so the caller classifies it. ``None`` for the map says coverage was unavailable or unreadable: the sweep then runs
    exactly as it would without the option. Code run in a subprocess the tests start is not measured, which is why this is opt-in.
    """
    if importlib.util.find_spec("coverage") is None:
        logger.warning("mutation teeth: coverage_prune needs the `coverage` package; running without the line map")
        return _run_pytest(test_paths, sandbox, timeout, *extra_flags), None
    from coverage import CoverageData

    target = (sandbox / relative).resolve()
    with tempfile.TemporaryDirectory(prefix="mutation_teeth_cov_") as scratch:
        data_file = Path(scratch) / "cov.data"
        rc = Path(scratch) / "covrc"
        rc.write_text(
            f"[run]\ndata_file = {data_file.as_posix()}\ndynamic_context = test_function\ninclude = {target.as_posix()}\n",
            encoding="utf-8",
        )
        result = run_with_deadline(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--rcfile={rc}",
                "-m",
                "pytest",
                *[str(t) for t in test_paths],
                *_pytest_flags(),
                *extra_flags,
                *_plugin_isolation_flags(sandbox),
                "-x",
            ],
            cwd=sandbox,
            env=_pytest_env(),
            timeout=timeout,
        )
        if result is None or result.returncode != 0:
            return result, None
        try:
            data = CoverageData(basename=str(data_file))
            data.read()
            measured = next((f for f in data.measured_files() if Path(f).resolve() == target), None)
            if measured is None:
                return result, CoverageMap({}, statement_starts((sandbox / relative).read_text(encoding="utf-8-sig")))
            test_files = expand_test_files(sandbox, test_paths)
            lines: "dict[int, Optional[list[str]]]" = {}
            for line, contexts in data.contexts_by_lineno(measured).items():
                collected: "set[str]" = set()
                every = False
                for context in contexts:
                    matched = _context_files(context, test_files)
                    if matched is None:
                        every = True
                        break
                    collected.update(matched)
                lines[line] = None if every else sorted(collected)
            return result, CoverageMap(lines, statement_starts((sandbox / relative).read_text(encoding="utf-8-sig")))
        except Exception as exc:
            logger.warning("mutation teeth: coverage data unreadable (%s: %s); running without the line map", type(exc).__name__, exc)
            return result, None


def narrowed_paths(files: "Sequence[str]", ranked: "Sequence[PathArg]") -> "list[PathArg]":
    """*files* ordered like *ranked* (the files that killed before come first); files not in *ranked* follow in their own order."""
    rank = {Path(str(p)).as_posix(): i for i, p in enumerate(ranked)}
    return sorted(files, key=lambda f: (rank.get(Path(f).as_posix(), len(rank)), f))


def warm_advice(warm_seconds: "Sequence[float]", cold_seconds: "Sequence[float]") -> Optional[str]:
    """A sentence when a cold run of the whole suite was measurably no slower than the warm per-mutant run, else ``None`` (verdict-neutral advice)."""
    if len(warm_seconds) < 3 or not cold_seconds:
        return None
    warm_mean = sum(warm_seconds) / len(warm_seconds)
    cold_mean = sum(cold_seconds) / len(cold_seconds)
    if cold_mean <= warm_mean:
        return (
            f"mutation teeth: a cold run of the suite took {cold_mean:.1f}s on average against {warm_mean:.1f}s for a warm one; "
            "use_warm_worker=False would be at least as fast for this repo"
        )
    return None


def baseline_error(sandbox: Optional[Path], result: Any) -> MutationHarnessError:
    """The refusal for an unmutated run that does not pass; the wording is the one the cold-first baseline always used."""
    if sandbox is not None:
        return MutationHarnessError(
            f"the unmutated tests do not pass in the extra sandbox {sandbox}, so its kills would mean nothing. "
            "Run with `jobs=1`, or make the tests independent of the directory they run in."
        )
    tail = result.stdout[-2000:] if result is not None else ""
    return MutationHarnessError(
        f"the unmutated baseline does not pass, so no mutant result would mean anything.\nFix the failing tests first, then re-run.\n{tail}"
    )


Resolver = Callable[[], "Sequence[PathArg]"]
