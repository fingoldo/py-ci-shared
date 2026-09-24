"""A metric scored only on the rows where the prediction happened to be finite.

``rmse(y[finite], pred[finite])`` answers a question nobody asked: how good the model is where it produced a number at
all. The rows it dropped are the ones it failed on, and the deployed predictor has no such option - it fills them, or it
falls back. A gate written this way under-rejects exactly the collapse it exists to catch: one shipped instance scored a
spec on the 60% of its holdout it could invert and let it through, while the same spec carried the fill error on the rest.

The check flags a metric call whose arguments are both indexed by a mask derived from ``isfinite(<prediction>)``, unless
the enclosing function does something about the dropped rows: fills them before scoring (a name containing ``fill``),
or reports the dropped fraction as part of its verdict (a name containing ``dropped_frac``, ``finite_frac`` or
``coverage_frac``). Counting the finite rows to reject below a floor is not a remedy: the survivors are still scored alone.

Usage from a repository's meta tests::

    from py_ci_shared.survivorship_scoring import assert_no_survivorship_scoring

    def test_metrics_are_not_scored_on_survivors_only():
        assert_no_survivorship_scoring(files=SRC_FILES, repo_root=REPO_ROOT, allowed={})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from ._core import ParsedFile, ScanResult, scan_python

__all__ = ["SurvivorshipScore", "DEFAULT_METRIC_NAMES", "find_survivorship_scoring", "assert_no_survivorship_scoring"]

DEFAULT_METRIC_NAMES: frozenset[str] = frozenset(
    {
        "rmse",
        "mae",
        "mse",
        "r2",
        "r2_score",
        "mape",
        "smape",
        "logloss",
        "brier",
        "mean_squared_error",
        "mean_absolute_error",
        "root_mean_squared_error",
        "mean_absolute_percentage_error",
    }
)
# Filling the dropped rows before scoring is a remedy; so is reporting the fraction that was dropped as part of the
# verdict. Merely counting the finite rows to reject a spec below a floor is not: the survivors are still scored alone.
_REMEDY_MARKERS: tuple[str, ...] = ("fill", "dropped_frac", "dropped_fraction", "finite_frac", "coverage_frac")


class SurvivorshipScore:
    """One metric call scored on survivors: where it is, which metric, and the mask it was indexed by."""

    __slots__ = ("function", "lineno", "mask", "metric", "path")

    def __init__(self, path: str, function: str, lineno: int, metric: str, mask: str) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.metric = metric
        self.mask = mask

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.metric}(... [{self.mask}]) in {self.function}()"


def _call_name(node: ast.Call) -> str | None:
    """The called name, attribute access included (``metrics.rmse`` reads as ``rmse``)."""
    func = node.func
    return getattr(func, "attr", getattr(func, "id", None))


def _finite_masks(func: ast.AST) -> set[str]:
    """Names bound to a mask derived from ``isfinite(...)`` inside ``func``, following ``&`` and ``~``."""
    masks: set[str] = set()
    for _ in range(3):
        before = len(masks)
        for node in ast.walk(func):
            if not (isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None):
                continue
            uses_finite = any(isinstance(n, ast.Call) and _call_name(n) == "isfinite" for n in ast.walk(node.value)) or any(
                isinstance(n, ast.Name) and n.id in masks for n in ast.walk(node.value)
            )
            if not uses_finite:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            masks.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
        if len(masks) == before:
            break
    return masks


def _masked_arg(node: ast.expr, masks: set[str]) -> str | None:
    """The mask name when ``node`` is ``<something>[<mask>]``, else None."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Name) and node.slice.id in masks:
        return node.slice.id
    return None


def _has_remedy(func: ast.AST) -> bool:
    """True when the function fills the dropped rows or reports how many there were."""
    for node in ast.walk(func):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = node.value
        if name and any(marker in name.lower() for marker in _REMEDY_MARKERS):
            return True
    return False


def _scan(files: Iterable[Path], repo_root: Path) -> ScanResult:
    return scan_python([Path(p) for p in files], root=repo_root)


def _find(parsed: Iterable[ParsedFile], metric_names: Sequence[str] | frozenset[str]) -> list[SurvivorshipScore]:
    metrics = frozenset(metric_names)
    out: list[SurvivorshipScore] = []
    for f in parsed:
        tree, rel = f.tree, f.rel
        for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            masks = _finite_masks(func)
            if not masks or _has_remedy(func):
                continue
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call) and _call_name(node) in metrics and len(node.args) >= 2):
                    continue
                first, second = _masked_arg(node.args[0], masks), _masked_arg(node.args[1], masks)
                if first is not None and second is not None:
                    out.append(SurvivorshipScore(rel, func.name, node.lineno, _call_name(node) or "?", first))
    return sorted(out, key=lambda s: (s.path, s.lineno))


def find_survivorship_scoring(
    files: Iterable[Path], repo_root: Path, metric_names: Sequence[str] | frozenset[str] = DEFAULT_METRIC_NAMES, *, allow_unparsed: bool = False
) -> list[SurvivorshipScore]:
    """Every metric call whose arguments are both indexed by a finite-mask, in a function that neither fills nor reports.

    A file outside *repo_root* is keyed by its absolute path. A file that cannot be read or parsed raises
    ``UnparsedFilesError`` (an ``AssertionError``) unless *allow_unparsed*.
    """
    scan = _scan(files, repo_root)
    if not allow_unparsed:
        scan.check_unparsed()
    return _find(scan.files, metric_names)


def assert_no_survivorship_scoring(
    files: Iterable[Path],
    repo_root: Path,
    allowed: Mapping[str, str] | None = None,
    min_files: int = 1,
    metric_names: Sequence[str] | frozenset[str] = DEFAULT_METRIC_NAMES,
) -> None:
    """Fail on a metric scored only where the prediction was finite.

    ``allowed`` maps ``path::function`` to the reason that site scores survivors on purpose; an empty reason is rejected,
    and an entry with nothing left to excuse must be removed.
    """
    scan = _scan(files, repo_root)
    scan.min_files = min_files
    try:
        scan.check_floor()
    except AssertionError as exc:
        raise AssertionError(f"the scan lost its subject: {exc}") from exc
    scan.check_unparsed()
    allowed = dict(allowed or {})
    empty = sorted(k for k, v in allowed.items() if not str(v).strip())
    if empty:
        raise AssertionError(f"allowed sites need a reason: {empty}")
    found = _find(scan.files, metric_names)
    keys = {f"{s.path}::{s.function}" for s in found}
    bad = [s for s in found if f"{s.path}::{s.function}" not in allowed]
    stale = sorted(set(allowed) - keys)
    msgs = []
    if bad:
        msgs.append(
            "metrics scored only on the rows where the prediction was finite (the dropped rows are the failures, "
            "and the deployed predictor fills them instead): " + "; ".join(map(repr, bad))
        )
    if stale:
        msgs.append(f"allowed sites that no longer score survivors: {stale}")
    if msgs:
        raise AssertionError("\n".join(msgs))
