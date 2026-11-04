"""Three small regression guards for ML API contract slips mlframe already fixed by hand.

Zero (or two) hits on mlframe today, so these are preventive; opt in per repo.

1. ``best-iteration-count``: an XGBoost/CatBoost ``best_iteration`` is a 0-based INDEX of the best round. Used as a
   tree count (``n_estimators = model.best_iteration``, ``iterations=m.get_best_iteration()``) the refit has one tree
   too few (mlframe a8c1982bc, 695b44fba). LightGBM's ``best_iteration_`` is already a count and is not matched.
   A ``+ 1`` (any arithmetic) makes the value a different expression and is accepted.
2. ``argmax-vs-raw-label``: ``proba.argmax(axis=1) == y_true`` compares a COLUMN position with a raw label; it is
   only right when labels are ``0..K-1`` in sorted order (mlframe 29f4c905a). Reported when the function never
   mentions ``classes``, ``searchsorted``, ``unique`` or ``label`` (mapping vocabulary).
3. ``init-rewrites-param``: in an ``__init__`` of a class deriving ``BaseEstimator`` (or an sklearn mixin),
   ``self.p = <anything but the bare parameter p>`` breaks the ``get_params``/``clone`` round trip.

Suppress any rule with ``# ml-api-ok: <reason>`` on the reported line.

Usage in a consumer's meta test::

    from py_ci_shared.ml_api_contracts import assert_ml_api_contracts

    def test_ml_api_contracts():
        assert_ml_api_contracts("src")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python
from ._core import walk as shared_walk

__all__ = ["RULES", "SUPPRESSION", "assert_ml_api_contracts", "find_ml_api_contracts"]

RULES = ("best-iteration-count", "argmax-vs-raw-label", "init-rewrites-param")
SUPPRESSION = "# ml-api-ok:"

_COUNT_NAME = re.compile(r"estimators|iterations|rounds|trees|n_iter", re.IGNORECASE)
_LABEL_NAME = re.compile(r"^(y|y_true|y_test|y_val|y_valid|target|targets|labels?|true_labels?|y_actual)$")
_MAPPING_WORDS = re.compile(r"classes|searchsorted|unique|label", re.IGNORECASE)
_ESTIMATOR_BASES = frozenset({"BaseEstimator", "TransformerMixin", "ClassifierMixin", "RegressorMixin", "ClusterMixin"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_best_iteration(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "best_iteration"
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get_best_iteration"


def _is_argmax_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "argmax") or (isinstance(func, ast.Name) and func.id == "argmax")


def _best_iteration_findings(tree: ast.Module, rel: str) -> list[Finding]:
    out: list[Finding] = []
    for node in shared_walk(tree):
        if not isinstance(node, (ast.Assign, ast.Call)):
            continue
        pairs: list[tuple[str, ast.AST]] = []
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            pairs.append((node.targets[0].id, node.value))
        if isinstance(node, ast.Call):
            pairs.extend((kw.arg, kw.value) for kw in node.keywords if kw.arg)
        for name, value in pairs:
            if _COUNT_NAME.search(name) and _is_best_iteration(value):
                out.append(Finding(rel, node.lineno, RULES[0], f"'{name}' takes best_iteration, a 0-based index, as a count: use best_iteration + 1"))
    return out


def _argmax_findings(tree: ast.Module, rel: str, source: str) -> list[Finding]:
    out: list[Finding] = []
    for func in (n for n in shared_walk(tree) if isinstance(n, _FUNCTIONS)):
        if _MAPPING_WORDS.search(ast.get_source_segment(source, func) or ""):
            continue
        for cmp_ in (n for n in shared_walk(func) if isinstance(n, ast.Compare)):
            if len(cmp_.ops) != 1 or not isinstance(cmp_.ops[0], (ast.Eq, ast.NotEq)):
                continue
            sides = (cmp_.left, cmp_.comparators[0])
            for a, b in (sides, sides[::-1]):
                if _is_argmax_call(a) and isinstance(b, ast.Name) and _LABEL_NAME.match(b.id):
                    out.append(
                        Finding(
                            rel,
                            cmp_.lineno,
                            RULES[1],
                            f"argmax column position compared with raw labels '{b.id}' in '{func.name}' with no class mapping: map through classes_ first",
                        )
                    )
    return out


def _class_bases(cls: ast.ClassDef) -> set[str]:
    return {b.id if isinstance(b, ast.Name) else b.attr for b in cls.bases if isinstance(b, (ast.Name, ast.Attribute))}


def _rewrites(init: ast.FunctionDef) -> Iterator[tuple[ast.Assign, str]]:
    params = {a.arg for a in (*init.args.args, *init.args.kwonlyargs)} - {"self"}
    for node in shared_walk(init):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            is_self_param = isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self" and t.attr in params
            if is_self_param and not (isinstance(node.value, ast.Name) and node.value.id == t.attr):  # type: ignore[attr-defined]
                yield node, t.attr  # type: ignore[attr-defined]


def _init_findings(tree: ast.Module, rel: str) -> list[Finding]:
    out: list[Finding] = []
    for cls in (n for n in shared_walk(tree) if isinstance(n, ast.ClassDef) and _class_bases(n) & _ESTIMATOR_BASES):
        for init in (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"):
            out.extend(
                Finding(rel, node.lineno, RULES[2], f"{cls.name}.__init__ rewrites its parameter '{attr}': get_params/clone no longer round-trip")
                for node, attr in _rewrites(init)
            )
    return out


def _findings_in(tree: ast.Module, rel: str, source: str, lines: list[str]) -> list[Finding]:
    found = _best_iteration_findings(tree, rel) + _argmax_findings(tree, rel, source) + _init_findings(tree, rel)
    return [f for f in found if not (1 <= f.line <= len(lines) and SUPPRESSION in lines[f.line - 1])]


def find_ml_api_contracts(
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
    return sorted(set(out), key=lambda f: (f.path, f.line, f.rule))


def assert_ml_api_contracts(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_ml_api_contracts(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "use best_iteration + 1 as a count; map argmax through classes_; keep __init__ parameters as given; or `# ml-api-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="ml_api_contracts", refresh_command="PY_CI_SHARED_REFRESH=ml_api_contracts")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} ml-api-contracts finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
