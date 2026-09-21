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

__all__ = ["shape_reasons", "SHAPE_HELP"]

SHAPE_HELP = {
    "wide-literal-range": "literal bounds spanning >= 20x (or <= 0 to >= 10): a badly wrong value still passes; assert against a measured reference",
    "envelope-assert": "a min/max envelope a constant prediction satisfies; assert an error metric instead",
    "median-roundtrip": "median error in a round-trip test passes while half the rows are wrong; assert the max error",
    "late-skip": "pytest.skip after computing: the data decides to skip, so the regression that changes it also disables the test",
}

_ROUNDTRIP_NAME = re.compile(r"round_?trip|inverse", re.IGNORECASE)
_ENV_PROBE = re.compile(r"sys|platform|os|environ|importlib|find_spec|shutil|which|cuda|gpu|GPU|HAS_|_AVAILABLE|available|installed|version", re.IGNORECASE)


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
    for node in ast.walk(test):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 2 and all(isinstance(o, (ast.Lt, ast.LtE)) for o in node.ops)):
            continue
        lo, hi = _num(node.left), _num(node.comparators[1])
        if lo is None or hi is None or hi <= 1.0:
            continue
        if (lo > 0 and hi / lo >= 20) or (lo <= 0 and hi >= 10):
            return True
    return False


def _is_min_max_call(node: ast.AST) -> bool:
    """``x.min()`` / ``x.max()`` / ``np.min(x)`` / ``np.max(x)`` (and the nan- variants)."""
    return isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name)) and getattr(node.func, "attr", getattr(node.func, "id", "")) in {
        "min", "max", "nanmin", "nanmax", "amin", "amax"}


def _envelope_assert(test: ast.AST) -> bool:
    """``a.min() > k * b.min()`` / ``a.max() < k * b.max()``: both sides a min/max, one scaled by a literal."""
    for node in ast.walk(test):
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
    for node in ast.walk(test):
        name = getattr(getattr(node, "func", None), "attr", getattr(getattr(node, "func", None), "id", ""))
        if isinstance(node, ast.Call) and name in {"median", "nanmedian"}:
            if any(isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) in {"abs", "absolute", "fabs"} for n in ast.walk(node)):
                return True
    return False


def _is_skip_call(node: ast.AST) -> bool:
    """``pytest.skip(...)``."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "skip" and isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest"


def _late_skip(func: ast.AST) -> bool:
    """A ``pytest.skip`` after the function assigned from a call, outside an ``if`` that probes the environment."""
    body = getattr(func, "body", [])
    first_compute = min((n.lineno for n in ast.walk(func) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)), default=None)
    if first_compute is None:
        return False
    parents: dict[int, ast.AST] = {}
    for p in ast.walk(func):
        for c in ast.iter_child_nodes(p):
            parents[id(c)] = p
    for node in ast.walk(func):
        if not _is_skip_call(node) or node.lineno <= first_compute:
            continue
        if body and getattr(body[0], "lineno", None) == node.lineno:
            continue
        if not _under_environment_probe(node, parents):
            return True
    return False


def _under_environment_probe(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """True when an enclosing ``if`` probes the environment, or an enclosing ``try`` handles a missing dependency."""
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, ast.If) and _ENV_PROBE.search(ast.unparse(cur.test)):
            return True
        if isinstance(cur, (ast.ExceptHandler, ast.Try)):  # a skip on ImportError / a missing optional dependency
            return True
    return False


def shape_reasons(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """The nondiscriminating shapes one test function exhibits, as slugs (see ``SHAPE_HELP``)."""
    asserts = [n.test for n in ast.walk(func) if isinstance(n, ast.Assert)]
    out: list[str] = []
    if any(_wide_literal_range(t) for t in asserts):
        out.append("wide-literal-range")
    if any(_envelope_assert(t) for t in asserts):
        out.append("envelope-assert")
    if _ROUNDTRIP_NAME.search(func.name) and any(_median_error(t) for t in asserts):
        out.append("median-roundtrip")
    if _late_skip(func):
        out.append("late-skip")
    return out
