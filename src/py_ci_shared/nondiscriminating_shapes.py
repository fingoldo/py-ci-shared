"""Assertion shapes that pass whether or not the property under test holds.

Each shape below passed a test through a real defect:

* ``wide-literal-range``: ``assert 0 < rmse < 100`` with literal bounds that span a factor of 20 or more, or run from
  zero or below to 10 or more. An error ten times too large sits comfortably inside. ``0 <= p <= 1`` is exempt: a
  probability bound is the whole contract there.
* ``envelope-assert``: ``assert pred.min() > 0.5 * y.min()`` / ``pred.max() < 1.5 * y.max()``: an envelope a constant
  prediction satisfies.
* ``median-roundtrip``: ``np.median(np.abs(a - b)) < tol`` as the SOLE verdict of a round-trip / inverse test, which
  passes while up to half the rows are wrong: the shape of a tail- or level-only defect. A median next to a real
  PER-ELEMENT check (an ``assert_*`` call such as ``assert_allclose``, or an ``assert`` over ``np.all``/``max``/
  ``allclose``/``array_equal`` or an ``==`` between values) is a canary and is not flagged; a trivial companion
  (``is not None``, ``len(x) > 0``, ``a.shape == b.shape``) checks no element and does not disarm it.
* ``late-skip``: ``pytest.skip(...)`` after the test has computed something, outside an environment probe: the data decided
  to skip, so the regression that changes the data also turns the test off. ``exists``/``supported`` mark a probe only
  when no name in the ``if`` was assigned from a call in the test: ``out = run(); if not out.exists(): skip`` is the
  code under test deciding.
* ``nonempty-only-assert`` (opt-in via ``extra_shapes=["nonempty-only-assert"]``, so existing baselines do not
  change): the function's ONLY assertion is ``len(x) > 0`` / ``len(x) >= 1`` / ``len(x) != 0`` / a bare ``len(x)``
  (or a comparison written the other way round): true for one bad element exactly as it is for a whole correct collection, so it
  cannot fail on a wrong-but-nonempty result. Scoped to a SOLE assertion, not any non-emptiness check: a test that also
  asserts a value already has a real floor, and reporting it too would make every ``assert x; assert len(x) > 0``
  combination noise.

``shape_reasons(func)`` returns the slugs one test function exhibits; a repository's own meta test decides scope and
baseline (see mlframe's ``test_no_nondiscriminating_assert.py``).

Counterpart: ``pyutilz.dev.code_audit.nondiscriminating_test`` also flags an assertion whose both sides come from the
identical call expression (always true by construction); this module does not attempt that shape, since resolving
"the identical call" without false positives on two calls that happen to render the same needs the wider scanner's
heuristics.
"""

from __future__ import annotations

from collections.abc import Iterable

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
    "nonempty-only-assert": "the only assertion checks non-emptiness; true for a wrong result exactly as for a right one, assert a value too",
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
        "supported",  # ``not backend_supported()`` / ``callbacks_supported``: a feature probe (see _PROBE_UNLESS_COMPUTED)
        "vram",  # ``if vram_total < 4 GiB``: a device-capacity probe
        "major",  # ``(major, minor) < (7, 0)``: a compute-capability / version tuple
        "minor",
        "version",
        "win32",
        "linux",
        "darwin",
        "ci",
        "exists",  # Path.exists() / os.path.exists(): a baseline-file-not-written-yet probe (see _PROBE_UNLESS_COMPUTED)
    }
)
#: Parts that probe the environment only when the ``if`` reads no name the test computed: ``BASELINE.exists()`` is a
#: probe, ``out = run(); out.exists()`` / ``result = fit(); result.supported`` is the code under test deciding.
_PROBE_UNLESS_COMPUTED = frozenset({"exists", "supported"})
#: Calls whose result judges every element: a companion assert using one is a real per-element check.
_PER_ELEMENT_CALLS = frozenset({"all", "max", "amax", "nanmax", "allclose", "array_equal", "array_equiv", "isclose", "equals"})
#: Attributes describing a container, not its elements: ``a.shape == b.shape`` checks no value.
_META_ATTRS = frozenset({"shape", "dtype", "dtypes", "ndim", "size", "columns", "index", "name"})
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


def _median_is_the_only_check(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A median-of-absolute-differences assertion that nothing else backs up.

    A median check next to a real per-element check (``np.testing.assert_allclose``, ``assert_array_equal``, another
    ``assert`` that is not itself a median) is a canary or precondition -- "this fixture does amplify" -- not the
    round-trip's pass/fail criterion; only a median that is the SOLE verdict passes while half the rows are wrong.
    """
    median_asserts = [n for n in _fast_walk(func) if isinstance(n, ast.Assert) and _median_error(n.test)]
    if not median_asserts:
        return False
    other_asserts = [n for n in _fast_walk(func) if isinstance(n, ast.Assert) and not _median_error(n.test) and _checks_elements(n.test)]
    assert_calls = [n for n in _fast_walk(func) if isinstance(n, ast.Call) and _call_name(n).startswith("assert_")]
    return not other_asserts and not assert_calls


def _describes_container(node: ast.AST) -> bool:
    """``len(x)``, ``x.shape`` and the like, ``None`` or a literal: a side that holds no element value."""
    if isinstance(node, ast.Call) and _call_name(node) == "len":
        return True
    if isinstance(node, ast.Attribute) and node.attr in _META_ATTRS:
        return True
    return isinstance(node, ast.Constant)


def _checks_elements(test: ast.AST) -> bool:
    """Does an assert test judge element values (``np.all(...)``, ``max(...)``, ``out == expected``), rather than only
    presence or shape (``x is not None``, ``len(x) > 0``, ``a.shape == b.shape``)?"""
    for node in _fast_walk(test):
        if isinstance(node, ast.Call) and _call_name(node) in _PER_ELEMENT_CALLS:
            return True
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            if not any(_describes_container(side) for side in [node.left, *node.comparators]):
                return True
    return False


def _call_name(node: ast.AST) -> str:
    func = getattr(node, "func", None)
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _len_nonempty_target(test: ast.AST) -> Optional[ast.expr]:
    """The collection a ``len(x) > 0`` / ``len(x) >= 1`` / ``len(x) != 0`` (either side) or bare ``len(x)`` assertion
    checks, or None."""
    if isinstance(test, ast.Call) and _call_name(test) == "len" and test.args:
        return test.args[0]
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return None
    left, op, right = test.left, test.ops[0], test.comparators[0]
    if isinstance(left, ast.Call) and _call_name(left) == "len" and left.args:
        if (isinstance(op, (ast.Gt, ast.NotEq)) and _num(right) == 0) or (isinstance(op, ast.GtE) and _num(right) == 1):
            return left.args[0]
    if isinstance(right, ast.Call) and _call_name(right) == "len" and right.args:
        if (isinstance(op, (ast.Lt, ast.NotEq)) and _num(left) == 0) or (isinstance(op, ast.LtE) and _num(left) == 1):
            return right.args[0]
    return None


def _nonempty_only_assert(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """The function's SOLE assertion is a non-emptiness check: true for any bad-but-present result too."""
    asserts = [n for n in _fast_walk(func) if isinstance(n, ast.Assert)]
    return len(asserts) == 1 and _len_nonempty_target(asserts[0].test) is not None


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
    computing = [n for n in _fast_walk(func) if _computes(n)]
    first_compute = min((getattr(n, "lineno", 0) for n in computing), default=None)
    if first_compute is None:
        return False
    computed = _computed_names(computing)
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
        if not _under_environment_probe(node, parents, computed):
            return True
    return False


def _computed_names(computing: Iterable[ast.AST]) -> set[str]:
    """Names bound by the computing statements (see :func:`_computes`)."""
    targets: list[ast.AST] = []
    for node in computing:
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            targets.append(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets.extend(item.optional_vars for item in node.items if item.optional_vars is not None)
    return {n.id for t in targets for n in _fast_walk(t) if isinstance(n, ast.Name)}


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


def _probes_environment(test: ast.AST, computed: "set[str] | frozenset[str]" = frozenset()) -> bool:
    """Does an ``if`` test read the environment? Matched on whole identifier parts: ``loss`` is not ``os``. The parts in
    :data:`_PROBE_UNLESS_COMPUTED` do not count when the test reads a name in *computed*."""
    parts = _identifier_parts(test) & _ENV_PARTS
    if parts & _PROBE_UNLESS_COMPUTED and any(isinstance(n, ast.Name) and n.id in computed for n in _fast_walk(test)):
        parts -= _PROBE_UNLESS_COMPUTED
    return bool(parts)


def _under_environment_probe(node: ast.AST, parents: dict[int, ast.AST], computed: "set[str] | frozenset[str]" = frozenset()) -> bool:
    """True when an enclosing ``if`` probes the environment, or the skip sits in an ``except`` handler (a missing
    dependency). A ``try`` BODY is not exempt: a skip there is still decided by the data."""
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, ast.If) and _probes_environment(cur.test, computed):
            return True
        if isinstance(cur, ast.ExceptHandler):
            return True
    return False


OPT_IN_SHAPES = frozenset({"nonempty-only-assert"})


def shape_reasons(func: ast.FunctionDef | ast.AsyncFunctionDef, *, aliases: Optional[ImportAliases] = None, extra_shapes: Iterable[str] = ()) -> list[str]:
    """The nondiscriminating shapes one test function exhibits, as slugs (see ``SHAPE_HELP``).

    Pass *aliases* (``ImportAliases.from_tree(module)``) so ``from pytest import skip`` / ``import pytest as pt``
    skips are recognised; without it only the literal ``pytest.skip`` is. An *extra_shapes* slug that is not in
    ``OPT_IN_SHAPES`` raises ``ValueError``: a typo must not switch the shape off silently.
    """
    extra = [extra_shapes] if isinstance(extra_shapes, str) else list(extra_shapes)
    unknown = sorted(set(extra) - OPT_IN_SHAPES)
    if unknown:
        raise ValueError(f"unknown extra_shapes {unknown}; the opt-in shapes are {sorted(OPT_IN_SHAPES)}")
    asserts = [n.test for n in _fast_walk(func) if isinstance(n, ast.Assert)]
    out: list[str] = []
    if any(_wide_literal_range(t) for t in asserts):
        out.append("wide-literal-range")
    if any(_envelope_assert(t) for t in asserts):
        out.append("envelope-assert")
    if _ROUNDTRIP_NAME.search(func.name) and _median_is_the_only_check(func):
        out.append("median-roundtrip")
    if _late_skip(func, aliases):
        out.append("late-skip")
    if "nonempty-only-assert" in extra and _nonempty_only_assert(func):
        out.append("nonempty-only-assert")
    return out
