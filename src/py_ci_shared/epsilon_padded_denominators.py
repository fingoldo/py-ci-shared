"""An additive epsilon must not guard a denominator whose magnitude falls off geometrically.

``a / (b + 1e-12)`` is the usual reflex for "don't divide by zero". It is safe only when ``b``'s scale is
bounded near 1 -- a relative-error denominator like ``abs(a) + 1e-12`` is fine, because the pad is
negligible exactly when it is not needed. It is unsafe the moment ``b`` is a POWER: ``b = d**k`` falls off
geometrically in ``k``, so an absolute pad stops being negligible at ordinary values of ``d`` and starts
deciding the result.

Two production instances, both found by this check's own rule and both silent:

* ``k / (r**d + 1e-12)`` as a kNN local-density estimator. At ``d=8, r=0.01`` the true 1e17 came back as
  9.999e12 -- a 99.99% error, every dense row saturating onto the same value, the feature losing all
  variation exactly where it carries the most signal.
* ``1.0 / (dist**power + 1e-12)`` as inverse-distance weights. The pad dominates UNEVENLY across a row, so
  near neighbours collapse onto one weight while farther ones keep theirs. On distances (1, 2, 5, 9) * 1e-4
  at ``power=4`` the weights came out [0.282, 0.282, 0.266, 0.170] -- an almost unweighted average --
  against the true [0.940, 0.059, 0.0015, 0.0001]. Inverse-distance weighting had stopped weighting.

Neither raised, neither logged, and both passed their own test suites: the fixtures used coordinates of
order 1, where the pad genuinely is negligible.

The safe forms are a CLAMP (``np.maximum(denominator, tiny)``), which engages only on a real underflow and
leaves every representable value untouched, or restructuring so no epsilon is needed at all. Both are what
the two sites above were rewritten to.

Known blind spot: a denominator that is quadratically small for a REASON the parse cannot see is not
reported. `expected = r @ c` in a correspondence analysis is a product of two marginal probabilities, so
two categories each at 1e-6 of the rows give 1e-12 -- the same order as a `+ 1e-12` pad, which shrank the
standardised residual by 29.3% there and by 90.0% one decade further down. Syntactically it is a name
divided into another name, indistinguishable from any safe denominator, so catching it would need to know
what the quantity means.

Scope is deliberately narrow. A pad on a non-power denominator is not flagged: on a repository of ~3500
modules the general form matched 109 sites, nearly all of them legitimate relative-error denominators,
while the power-denominator rule matched exactly 2 -- both real bugs. A check that has to be triaged is a
check that gets baselined.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = [
    "Finding",
    "assert_no_epsilon_padded_power_denominators",
    "find_epsilon_padded_power_denominators",
]

# Below this, a literal added to a denominator is a divide-by-zero guard rather than a modelling term.
_EPSILON_CEILING = 1e-6

# Calls that produce a geometrically-shrinking quantity just as ``**`` does.
_POWER_CALLS = frozenset({"power", "square", "float_power"})


class Finding:
    """One padded power denominator: where it is, and what it reads."""

    def __init__(self, path: Path, lineno: int, source: str) -> None:
        """Record the site."""
        self.path = path
        self.lineno = lineno
        self.source = source

    def __str__(self) -> str:
        """Render as ``path:line  expression``, the form an editor can jump to."""
        return f"{self.path.as_posix()}:{self.lineno}  {self.source}"


def _is_small_positive_literal(node: ast.AST) -> bool:
    """A numeric constant small enough to be a guard rather than a term."""
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool) and 0 < abs(node.value) <= _EPSILON_CEILING


def _falls_off_geometrically(node: ast.AST) -> bool:
    """Whether this denominator's magnitude shrinks as a power, so no absolute pad can stay negligible."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        # ``x * x`` is ``x ** 2`` written out; comparing the rendered operands catches it without
        # resolving names, which a parse-only check cannot do anyway.
        try:
            return ast.unparse(node.left) == ast.unparse(node.right)
        except Exception:
            return False
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _POWER_CALLS:
        return True
    return False


class _Visitor(ast.NodeVisitor):
    """Collects every ``<anything> / (<power> + <epsilon>)`` in one module."""

    def __init__(self, path: Path) -> None:
        """Start with no findings for ``path``."""
        self.path = path
        self.findings: list[Finding] = []

    def visit_BinOp(self, node: ast.BinOp) -> None:
        """Flag a division whose denominator is a power with a small literal added to it."""
        if isinstance(node.op, ast.Div) and isinstance(node.right, ast.BinOp) and isinstance(node.right.op, ast.Add):
            left, right = node.right.left, node.right.right
            for eps, other in ((left, right), (right, left)):
                if _is_small_positive_literal(eps) and _falls_off_geometrically(other):
                    try:
                        rendered = ast.unparse(node)
                    except Exception:
                        rendered = "<unrenderable>"
                    self.findings.append(Finding(self.path, node.lineno, rendered[:160]))
                    break
        self.generic_visit(node)


def find_epsilon_padded_power_denominators(
    roots: Sequence[Path],
    exclude: Iterable[str] = (),
) -> list[Finding]:
    """Scan every ``*.py`` under ``roots`` and return each padded power denominator found.

    ``exclude`` holds path fragments to skip, matched against the POSIX form of each file's path --
    benchmark and vendored-baseline trees are the usual entries, since a frozen copy is meant to keep the
    shape it was frozen with.
    """
    excluded = tuple(exclude)
    findings: list[Finding] = []
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            posix = path.as_posix()
            if any(fragment in posix for fragment in excluded):
                continue
            try:
                tree = ast.parse(path.read_bytes().decode("utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            visitor = _Visitor(path)
            visitor.visit(tree)
            findings.extend(visitor.findings)
    return findings


def assert_no_epsilon_padded_power_denominators(
    roots: Sequence[Path],
    exclude: Iterable[str] = (),
    allow: Iterable[str] = (),
) -> None:
    """Raise ``AssertionError`` listing every padded power denominator that is not explicitly allowed.

    ``allow`` holds ``path:line`` strings for sites a reader has judged safe. Prefer fixing the site: the
    safe rewrite -- clamping with ``np.maximum(denominator, tiny)`` -- is usually one line and removes the
    need for a judgement call entirely.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    findings = [f for f in find_epsilon_padded_power_denominators(roots, exclude) if f"{f.path.as_posix()}:{f.lineno}" not in allowed]
    if not findings:
        return
    newline = chr(10)
    listing = (newline + '  ').join(str(f) for f in findings)
    raise AssertionError(
        newline.join(
            [
                f"{len(findings)} denominator(s) guarded by an additive epsilon under a power.",
                "  A power falls off geometrically, so a fixed pad stops being negligible at ordinary inputs and",
                "  starts deciding the result -- silently, since nothing raises. Clamp instead:",
                "      np.maximum(denominator, np.finfo(np.float64).tiny)",
                f"  {listing}",
            ]
        )
    )
