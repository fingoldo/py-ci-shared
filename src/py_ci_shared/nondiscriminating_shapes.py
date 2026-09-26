"""Assertion shapes that pass whether or not the property under test holds.

Each shape below passed a test through a real defect:

* ``wide-literal-range``: ``assert 0 < rmse < 100`` with literal bounds that span a factor of 20 or more, or run from
  zero or below to 10 or more. An error ten times too large sits comfortably inside. ``0 <= p <= 1`` is exempt: a
  probability bound is the whole contract there.
* ``envelope-assert``: ``assert pred.min() > 0.5 * y.min()`` / ``pred.max() < 1.5 * y.max()``: an envelope a constant
  prediction satisfies.
* ``median-roundtrip``: ``np.median(np.abs(a - b)) < tol`` in a round-trip / inverse test, which passes while up to half
  the rows are wrong: the shape of a tail- or level-only defect.
* ``late-skip``: ``pytest.skip(...)`` after the test has computed something, outside an environment probe: the data decided
  to skip, so the regression that changes the data also turns the test off.

``shape_reasons(func)`` returns the slugs one test function exhibits; a repository's own meta test decides scope and
baseline (see mlframe's ``test_no_nondiscriminating_assert.py``).
"""

from __future__ import annotations

import ast
import re
from typing import Optional

from ._core import ImportAliases
from ._core.node_index import walk as _fast_walk

__all__ = ["shape_reasons", "SHAPE_HELP"]

SHAPE_HELP = {
    "wide-literal-range": "literal bounds spanning >= 20x (or <= 0 to >= 10): a badly wrong value still passes; assert against a measured reference",
    "envelope-assert": "a min/max envelope a constant prediction satisfies; assert an error metric instead",
    "median-roundtrip": "median error in a round-trip test passes while half the rows are wrong; assert the max error",
    "late-skip": "pytest.skip after computing: the data decides to skip, so the regression that changes it also disables the test",
}

_ROUNDTRIP_NAME = re.compile(r"round_?trip|inverse", re.IGNORECASE)
#: Identifier parts (split on ``_`` and case) that mark an ``if`` as probing the environment rather than the data.
_ENV_PARTS = frozenset(
    {
        "sys",
        "platform",
        "os",
        "environ",
        "importlib",
        "spec",
        "shutil",
        "which",
        "cuda",
        "gpu",
        "gpus",
        "mps",
        "available",
        "installed",
        "version",
        "win32",
        "linux",
        "darwin",
        "ci",
    }
)
_IDENT_PART = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _num(node: ast.AST) -> float | None:
    """A numeric literal's value (``-3`` included), else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _num(node.operand)
        return -v if v is not None else None
    return None


def _wide_literal_range(test: ast.AST) -> bool:
    """``lo < x < hi`` (or ``<=``) with literal ``lo``/``hi`` spanning a factor of 20, or from <= 0 to >= 10."""
    for node in _fast_walk(test):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 2):
            continue
        if all(isinstance(o, (ast.Lt, ast.LtE)) for o in node.ops):
            lo, hi = _num(node.left), _num(node.comparators[1])
        elif all(isinstance(o, (ast.Gt, ast.GtE)) for o in node.ops):
            lo, hi = _num(node.comparators[1]), _num(node.left)  # ``100 > x > 0`` is the same range written backwards
        else:
            continue
        if lo is None or hi is None or hi <= 1.0:
            continue
        if (lo > 0 and hi / lo >= 20) or (lo <= 0 and hi >= 10):
            return True
    return False


def _is_min_max_call(node: ast.AST) -> bool:
    """``x.min()`` / ``x.max()`` / ``np.min(x)`` / ``np.max(x)`` (and the nan- variants)."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
        and getattr(node.func, "attr", getattr(node.func, "id", "")) in {"min", "max", "nanmin", "nanmax", "amin", "amax"}
    )


def _envelope_assert(test: ast.AST) -> bool:
    """``a.min() > k * b.min()`` / ``a.max() < k * b.max()``: both sides a min/max, one scaled by a literal."""
    for node in _fast_walk(test):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Gt, ast.GtE, ast.Lt, ast.LtE))):
            continue
        left, right = node.left, node.comparators[0]
        for a, b in ((left, right), (right, left)):
            if _is_min_max_call(a) and isinstance(b, ast.BinOp) and isinstance(b.op, ast.Mult):
                if (_num(b.left) is not None and _is_min_max_call(b.right)) or (_num(b.right) is not None and _is_min_max_call(b.left)):
                    return True
    return False


def _median_error(test: ast.AST) -> bool:
    """A median over an absolute difference: ``np.median(np.abs(a - b))``."""
    for node in _fast_walk(test):
        name = getattr(getattr(node, "func", None), "attr", getattr(getattr(node, "func", None), "id", ""))
        if isinstance(node, ast.Call) and name in {"median", "nanmedian"}:
            if any(isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) in {"abs", "absolute", "fabs"} for n in _fast_walk(node)):
                return True
    return False


def _is_skip_call(node: ast.AST, aliases: Optional[ImportAliases] = None) -> bool:
    """``pytest.skip(...)``, however ``pytest`` or ``skip`` was imported when *aliases* is given."""
    if not isinstance(node, ast.Call):
        return False
    if aliases is not None:
        return aliases.qualified_name(node) == "pytest.skip"
    return isinstance(node.func, ast.Attribute) and node.func.attr == "skip" and isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest"


def _computes(node: ast.AST) -> bool:
    """A statement or expression that binds a name from a call: ``x = f()``, ``x: T = f()``, ``(x := f())``, ``with f() as x``."""
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
        return isinstance(node.value, ast.Call)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return any(item.optional_vars is not None and isinstance(item.context_expr, ast.Call) for item in node.items)
    return False


def _late_skip(func: ast.AST, aliases: Optional[ImportAliases] = None) -> bool:
    """A ``pytest.skip`` after the function assigned from a call, outside an ``if`` that probes the environment."""
    body = getattr(func, "body", [])
    first_compute = min((getattr(n, "lineno", 0) for n in _fast_walk(func) if _computes(n)), default=None)
    if first_compute is None:
        return False
    parents: dict[int, ast.AST] = {}
    for p in _fast_walk(func):
        for c in ast.iter_child_nodes(p):
            parents[id(c)] = p
    for node in _fast_walk(func):
        line = getattr(node, "lineno", 0)
        if not _is_skip_call(node, aliases) or line <= first_compute:
            continue
        if body and getattr(body[0], "lineno", None) == line:
            continue
        if not _under_environment_probe(node, parents):
            return True
    return False


def _identifier_parts(test: ast.AST) -> set[str]:
    parts: set[str] = set()
    for sub in _fast_walk(test):
        name = sub.id if isinstance(sub, ast.Name) else sub.attr if isinstance(sub, ast.Attribute) else None
        if name is None:
            continue
        if name.startswith("HAS_"):
            parts.add("available")  # the HAS_TORCH / HAS_CUDA constant convention
        for piece in name.split("_"):
            parts.update(m.lower() for m in _IDENT_PART.findall(piece))
    return parts


def _probes_environment(test: ast.AST) -> bool:
    """Does an ``if`` test read the environment? Matched on whole identifier parts: ``loss`` is not ``os``."""
    return bool(_identifier_parts(test) & _ENV_PARTS)


def _under_environment_probe(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """True when an enclosing ``if`` probes the environment, or the skip sits in an ``except`` handler (a missing
    dependency). A ``try`` BODY is not exempt: a skip there is still decided by the data."""
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, ast.If) and _probes_environment(cur.test):
            return True
        if isinstance(cur, ast.ExceptHandler):
            return True
    return False


def shape_reasons(func: ast.FunctionDef | ast.AsyncFunctionDef, *, aliases: Optional[ImportAliases] = None) -> list[str]:
    """The nondiscriminating shapes one test function exhibits, as slugs (see ``SHAPE_HELP``).

    Pass *aliases* (``ImportAliases.from_tree(module)``) so ``from pytest import skip`` / ``import pytest as pt``
    skips are recognised; without it only the literal ``pytest.skip`` is.
    """
    asserts = [n.test for n in _fast_walk(func) if isinstance(n, ast.Assert)]
    out: list[str] = []
    if any(_wide_literal_range(t) for t in asserts):
        out.append("wide-literal-range")
    if any(_envelope_assert(t) for t in asserts):
        out.append("envelope-assert")
    if _ROUNDTRIP_NAME.search(func.name) and any(_median_error(t) for t in asserts):
        out.append("median-roundtrip")
    if _late_skip(func, aliases):
        out.append("late-skip")
    return out
