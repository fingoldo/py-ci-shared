"""estimator_mixin_order: a scikit-learn mixin listed after ``BaseEstimator`` in a class statement.

``BaseEstimator.__sklearn_tags__`` is terminal in the MRO, so a mixin placed after it is never reached. Under
scikit-learn >= 1.6 ``is_classifier``/``is_regressor`` then answer False with no error, and ``GridSearchCV`` and
``cross_val_score`` silently pick the wrong splitter and scorer.

Rule: in ``class C(...)``, every mixin named in ``MIXINS`` precedes ``BaseEstimator`` in the base list. Bases are
resolved through ``ImportAliases``, so ``from sklearn.base import X`` and ``import sklearn.base as sb`` both count.
v1 reads direct bases only. A waiver is ``# mixin-order-ok: <reason>`` on the ``class`` line.

Usage in a consumer's meta test::

    from py_ci_shared.estimator_mixin_order import assert_estimator_mixin_order

    def test_estimator_mixin_order():
        assert_estimator_mixin_order("src")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Union
from collections.abc import Iterable

from ._core.aliases import ImportAliases
from ._core.findings import Finding
from ._core.scan import scan_python

__all__ = ["BASE", "MIXINS", "find_estimator_mixin_order", "assert_estimator_mixin_order"]

RULE = "estimator_mixin_order"
BASE = "sklearn.base.BaseEstimator"
MIXINS = frozenset(
    "sklearn.base." + name
    for name in (
        "ClassifierMixin",
        "RegressorMixin",
        "TransformerMixin",
        "ClusterMixin",
        "OutlierMixin",
        "DensityMixin",
        "BiclusterMixin",
        "MultiOutputMixin",
        "MetaEstimatorMixin",
        "OneToOneFeatureMixin",
        "ClassNamePrefixFeaturesOutMixin",
        "SelectorMixin",
    )
)
_WAIVER = re.compile(r"#\s*mixin-order-ok:\s*\S")


def _find_in_tree(tree: ast.AST, source: str, rel: str) -> list[Finding]:
    aliases = ImportAliases.from_tree(tree)
    lines = source.splitlines()
    out: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names = [aliases.qualified_name(b) for b in node.bases]
        if BASE not in names:
            continue
        base_at = names.index(BASE)
        bad = [n for i, n in enumerate(names) if n in MIXINS and i > base_at]
        if not bad:
            continue
        line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
        if _WAIVER.search(line):
            continue
        out.append(
            Finding(
                rel,
                node.lineno,
                RULE,
                f"class {node.name} lists {bad[0].rsplit('.', 1)[-1]} after BaseEstimator; " "put every sklearn mixin before BaseEstimator",
            )
        )
    return out


def find_estimator_mixin_order(
    files_or_root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> list[Finding]:
    """Findings for every class whose sklearn mixin follows ``BaseEstimator``. Unparsed files are findings unless allowed."""
    scan = scan_python(files_or_root, min_files=min_files)
    scan.check_floor()
    findings: list[Finding] = []
    for parsed in scan:
        findings.extend(_find_in_tree(parsed.tree, parsed.source, parsed.rel))
    if not allow_unparsed:
        findings.extend(scan.unparsed_findings())
    return findings


def assert_estimator_mixin_order(
    files_or_root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Raise ``AssertionError`` naming the gate when any class has a mixin after ``BaseEstimator``."""
    findings = find_estimator_mixin_order(files_or_root, min_files=min_files, allow_unparsed=allow_unparsed)
    if findings:
        raise AssertionError(f"{RULE}: {len(findings)} finding(s):\n" + "\n".join(f.render() for f in findings))
