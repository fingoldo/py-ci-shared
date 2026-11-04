"""Ranks built by scattering ``arange`` through an argsort: ``ranks[order] = np.arange(n)``.

The idiom gives tied values DIFFERENT ranks (decided by input order or sort stability) and ranks NaN as the
largest values. Rank statistics built on it (Spearman, copula/MI transforms, gains and lift tables, bootstrap
resamplers) become row-order dependent on tie-heavy columns, and NaN rows leak into the top ranks. The array passes
every unit test with continuous random data. mlframe hand-fixed the family in 8325a3937, 345f7f9bb, 9edd1a913,
68154252f, acac56157, bc50f7383 and 1de3baf29.

What is reported: an assignment ``<rank-like>[<order>] = <expression containing arange/range>`` where the target
name contains ``rank`` (or is ``r``/``rk``), and ``<order>`` is a name assigned from ``argsort``/``lexsort``/
``argpartition`` in the same function or a direct call of one of them. A function that also uses tie vocabulary
(``rankdata``, ``searchsorted``, ``bincount``, ``unique``, ``cumsum``, ``average``, ``tie``) is taken to handle
ties already and is not reported. An ordinal tiebreak chosen on purpose is marked
``# ordinal-rank-ok: <reason>`` on the assignment line.

Usage in a consumer's meta test::

    from py_ci_shared.ordinal_rank_scatter import assert_ordinal_rank_scatter

    def test_ordinal_rank_scatter():
        assert_ordinal_rank_scatter("src", baseline_path="tests/baselines/ordinal_rank_scatter.json")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "SUPPRESSION", "assert_ordinal_rank_scatter", "find_ordinal_rank_scatter"]

RULE = "ordinal-rank-scatter"
SUPPRESSION = "# ordinal-rank-ok:"

_ORDER_CALLS = frozenset({"argsort", "lexsort", "argpartition"})
_RANGE_CALLS = frozenset({"arange", "range"})
_TIE_WORDS = re.compile(r"rankdata|searchsorted|bincount|unique|cumsum|average|\btie|ties\b|tied", re.IGNORECASE)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _rank_like(name: str) -> bool:
    return "rank" in name.lower() or name in {"r", "rk"}


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        if isinstance(node.func, ast.Name):
            return node.func.id
    return None


def _is_order_call(node: ast.AST) -> bool:
    return _call_name(node) in _ORDER_CALLS


def _has_range(node: ast.AST) -> bool:
    return any(_call_name(n) in _RANGE_CALLS for n in shared_walk(node))


def _order_names(func: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in shared_walk(func):
        if isinstance(node, ast.Assign) and _is_order_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _findings_in(tree: ast.Module, rel: str, source: str, lines: list[str]) -> list[Finding]:
    out: list[Finding] = []
    seen: set[int] = set()
    for func in (n for n in shared_walk(tree) if isinstance(n, _FUNCTIONS)):
        segment = ast.get_source_segment(source, func) or ""
        if _TIE_WORDS.search(segment):
            continue
        orders = _order_names(func)
        for node in shared_walk(func):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Subscript):
                continue
            target = node.targets[0]
            if not (isinstance(target.value, ast.Name) and _rank_like(target.value.id)):
                continue
            index = target.slice
            via_order = (isinstance(index, ast.Name) and index.id in orders) or _is_order_call(index)
            if not via_order or not _has_range(node.value):
                continue
            if node.lineno in seen:
                continue
            seen.add(node.lineno)
            if 1 <= node.lineno <= len(lines) and SUPPRESSION in lines[node.lineno - 1]:
                continue
            out.append(
                Finding(
                    rel,
                    node.lineno,
                    RULE,
                    f"'{target.value.id}' is filled by scattering arange through an argsort in '{func.name}': tied values get different ranks "
                    "and NaN ranks highest; use scipy.stats.rankdata (average) or mark `# ordinal-rank-ok: <reason>`",
                )
            )
    return out


def find_ordinal_rank_scatter(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_ordinal_rank_scatter(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_ordinal_rank_scatter(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "use scipy.stats.rankdata(x, method='average') (and mask NaN first), or `# ordinal-rank-ok: <reason>` for a deliberate ordinal tiebreak"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="ordinal_rank_scatter", refresh_command="PY_CI_SHARED_REFRESH=ordinal_rank_scatter")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} ordinal-rank-scatter finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
