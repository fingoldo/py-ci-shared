"""Variance, skewness and kurtosis built from raw power sums cancel catastrophically on large-offset data.

The one-pass textbook formula ``var = sum(x*x)/n - (sum(x)/n)**2`` subtracts two numbers that agree in almost every
digit whenever the data sit far from zero relative to their spread. A float64 carries about 16 digits, so a
mean of 1.7e9 (an epoch-seconds timestamp) with unit noise leaves ``E[x^2] ~ 2.9e18`` and a true variance of 1: the
difference is smaller than one ulp of the operands, and the result is rounding noise, negative or wildly too large.
In a feature pipeline the shipped instance reported a standard deviation of 51 against a true 0.98 on 1.7e9 plus unit
noise: no exception, a plausible-looking positive number, a feature silently carrying the wrong scale. The same
cancellation, worse, hits the third and fourth moments (``s3 - 3*m*s2 + 2*n*m**3``) that skewness and kurtosis expand
into, and the textbook fix is the same everywhere: centre first (two passes) or update with Welford's recurrence.

What is reported, per function (and module level), is a subtraction chain that combines
a raw power sum with a squared or cubed mean:

* the power sum is an accumulator fed by ``x*x``, ``x**2``, ``x**3`` or ``x**4`` (``s2 += x[i] * x[i]``), or a direct
  ``np.sum(x**2)`` / ``np.mean(x**2)`` / ``(x*x).sum()`` / ``np.dot(x, x)``;
* the subtracted term holds a mean (a ``sum``/``mean``/``average`` call, a first-power accumulator, or a name derived
  from either) squared or cubed: ``(s1/n)**2``, ``mean*mean``, ``n*mean*mean``, ``s1*s1/n``, ``np.mean(x)**2``.

So ``s2/n - (s1/n)**2``, ``s2 - n*mean*mean``, ``(s2 - s1*s1/n)/(n-1)``, ``np.mean(x**2) - np.mean(x)**2`` and the
binomial expansion of ``sum(x**3)`` are reported. Not reported: the centred two-pass form
(``np.sum((x - mean)**2)``, whose power base is itself a difference), Welford updates (no power sum), and
``np.var``/``np.std``.

Known false-positive shape: data that is provably small-offset (a z-scored column, a probability) computed with the
raw formula is reported too, since the parse cannot see the scale; suppress it on the statement with
``# moment-ok: <reason>``.

Usage in a consumer's meta test::

    from py_ci_shared.cancellation_prone_moments import assert_cancellation_prone_moments

    def test_no_cancellation_prone_moments():
        assert_cancellation_prone_moments("src")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = ["RULE", "assert_cancellation_prone_moments", "find_cancellation_prone_moments"]

RULE = "cancellation-prone-moments"
SUPPRESSION = "# moment-ok:"

_FIRST_SUM_CALLS = frozenset({"sum", "mean", "average", "nansum", "nanmean", "fsum"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes of *scope* outside nested functions and lambdas, so each statement is judged by its own function."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (*_FUNCTIONS, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _same(a: ast.AST, b: ast.AST) -> bool:
    return ast.dump(a) == ast.dump(b)


def _is_power(node: ast.AST) -> bool:
    """``x*x``, ``x**2``/``**3``/``**4`` (also ``x*x*x``) where the base is not a difference, i.e. not centred."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        exponent = node.right
        return isinstance(exponent, ast.Constant) and exponent.value in (2, 3, 4) and not _centred(node.left) and not isinstance(node.left, ast.Constant)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        factors = _factors(node)
        return (
            len(factors) >= 2
            and not any(isinstance(f, ast.Constant) or _centred(f) for f in factors)
            and any(_same(f, g) for f in factors for g in factors if f is not g)
        )
    return False


def _centred(node: ast.AST) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Sub, ast.Add))


def _factors(node: ast.AST) -> list[ast.AST]:
    """Operands of a ``*``/``/`` chain, flattened: ``n * m * m / k`` -> ``[n, m, m, k]``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
        return _factors(node.left) + _factors(node.right)
    return [node]


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _is_power_sum_call(node: ast.AST) -> bool:
    """``np.sum(x**2)``, ``np.mean(x*x)``, ``(x**2).sum()``, ``np.dot(x, x)``."""
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node)
    if name in ("dot", "vdot", "inner") and len(node.args) == 2:
        return _same(node.args[0], node.args[1]) and not _centred(node.args[0])
    if name not in _FIRST_SUM_CALLS:
        return False
    if isinstance(node.func, ast.Attribute) and _is_power(node.func.value):
        return True
    if not node.args:
        return False
    arg = node.args[0]
    return _is_power(arg.elt if isinstance(arg, ast.GeneratorExp) else arg)


def _is_first_sum_call(node: ast.AST) -> bool:
    """A ``sum``/``mean``/``average`` call that is not a power sum and not over a centred argument."""
    if not isinstance(node, ast.Call) or _call_name(node) not in _FIRST_SUM_CALLS or _is_power_sum_call(node):
        return False
    arg = node.args[0] if node.args else (node.func.value if isinstance(node.func, ast.Attribute) else None)
    if arg is None:
        return False
    if isinstance(arg, ast.GeneratorExp):
        return not _is_power(arg.elt) and not _centred(arg.elt)
    return not _centred(arg)


def _contains(node: ast.AST, pred) -> bool:
    return any(pred(n) for n in ast.walk(node))


def _key(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _key(node.value)
        return f"{base}.{node.attr}" if base else ""
    if isinstance(node, ast.Subscript):
        return _key(node.value)
    return ""


def _mentions(node: ast.AST, names: set[str]) -> bool:
    return bool(names) and _contains(node, lambda n: isinstance(n, (ast.Name, ast.Attribute)) and _key(n) in names)


def _record_accumulator(node: ast.AugAssign, power: set[str], first: set[str]) -> None:
    """File the target of ``acc += value`` as a power-sum or a first-power accumulator."""
    if _is_power(node.value) or _is_power_sum_call(node.value):
        power.add(_key(node.target))
    elif isinstance(node.value, (ast.Name, ast.Subscript, ast.Attribute)):
        first.add(_key(node.target))


def _named_assignments(node: ast.AST) -> list[tuple[str, ast.AST]]:
    """``(name, value)`` pairs of a plain or annotated assignment, skipping subscript targets and unnamed ones."""
    if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
        return []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [(_key(t), node.value) for t in targets if _key(t) and not isinstance(t, ast.Subscript)]


def _propagate_names(assigns: list[tuple[str, ast.AST]], power: set[str], first: set[str]) -> None:
    """Three rounds of: a name assigned from a power sum (or power-sum name) is one too, likewise for mean-like names."""
    for _ in range(3):
        for name, value in assigns:
            if name in power or name in first:
                continue
            if _contains(value, _is_power_sum_call) or _mentions(value, power):
                power.add(name)
            elif _contains(value, _is_first_sum_call) or _mentions(value, first):
                first.add(name)


def _classify(own: list[ast.AST]) -> tuple[set[str], set[str]]:
    """``(power-sum names, mean-like names)`` of one scope: accumulators and derived names."""
    power: set[str] = set()
    first: set[str] = set()
    assigns: list[tuple[str, ast.AST]] = []
    for node in own:
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) and _key(node.target):
            _record_accumulator(node, power, first)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            assigns.extend(_named_assignments(node))
    _propagate_names(assigns, power, first)
    return power, first


def _terms(node: ast.AST, sign: int = 1) -> list[tuple[ast.AST, int]]:
    """A ``+``/``-`` chain flattened to signed terms."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _terms(node.left, sign) + _terms(node.right, sign)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
        return _terms(node.left, sign) + _terms(node.right, -sign)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _terms(node.operand, -sign)
    return [(node, sign)]


def _squared_mean(term: ast.AST, first: set[str]) -> bool:
    """Does the term hold a mean-like quantity to the second or third power (``m**2``, ``m*m``, ``n*m*m``, ``s*s/n``)?"""

    def meanlike(node: ast.AST) -> bool:
        return not _centred(node) and (_mentions(node, first) or _contains(node, _is_first_sum_call))

    for node in ast.walk(term):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and node.right.value in (2, 3) and meanlike(node.left):
                return True
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
            factors = [f for f in _factors(node) if not isinstance(f, ast.Constant)]
            for i, f in enumerate(factors):
                if meanlike(f) and any(_same(f, g) for g in factors[i + 1 :]):
                    return True
    return False


def _power_term(term: ast.AST, power: set[str]) -> bool:
    """A term built on ONE power sum; a product of two distinct ones (``M00*M11 - M01*M01``) is a 2x2 determinant."""
    names = {_key(n) for n in ast.walk(term) if isinstance(n, (ast.Name, ast.Attribute)) and _key(n) in power}
    if len(names) > 1:
        return False
    return bool(names) or _contains(term, _is_power_sum_call)


def _suppressed(lines: list[str], lo: int, hi: int) -> bool:
    return any(SUPPRESSION in lines[i] for i in range(max(lo - 1, 0), min(hi, len(lines))))


def _is_additive(node: ast.AST) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))


def _scope_may_cancel(own: list[ast.AST]) -> bool:
    """Does the scope hold both a ``+``/``-`` and a power or power-sum call, the two ingredients of a cancelling moment?"""
    if not any(_is_additive(n) for n in own):
        return False
    return any(_is_power(n) or _is_power_sum_call(n) for n in own if isinstance(n, (ast.BinOp, ast.Call)))


def _statement_anchor(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST:
    """The enclosing statement of *node* (or the topmost parent reachable)."""
    anchor = node
    while id(anchor) in parents and not isinstance(anchor, ast.stmt):
        anchor = parents[id(anchor)]
    return anchor


def _cancelling_term(signed: list[tuple[ast.AST, int]], power: set[str], first: set[str]) -> bool:
    """Is some term a power sum while a term of the opposite sign holds a squared or cubed mean?"""
    return any(_power_term(term, power) and any(s != sign and _squared_mean(t, first) for t, s in signed) for term, sign in signed)


def _moment_finding(node: ast.BinOp, anchor: ast.AST, rel: str, lines: list[str]) -> Optional[Finding]:
    """The finding for a cancelling chain, or None when its statement carries the suppression comment."""
    lo, hi = getattr(anchor, "lineno", node.lineno), getattr(anchor, "end_lineno", None) or node.lineno
    if _suppressed(lines, lo, hi):
        return None
    shown = ast.unparse(node)
    shown = shown if len(shown) <= 100 else shown[:97] + "..."
    return Finding(rel, node.lineno, RULE, f"moment from raw power sums cancels catastrophically on large-offset data: {shown}")


def _scope_findings(scope: ast.AST, own: list[ast.AST], rel: str, lines: list[str]) -> list[Finding]:
    """The findings of one scope whose own nodes are *own*."""
    out: list[Finding] = []
    power, first = _classify(own)
    parents = {id(c): p for p in [scope, *own] for c in ast.iter_child_nodes(p)}
    seen: set[int] = set()
    for node in own:
        if not isinstance(node, ast.BinOp) or not _is_additive(node):
            continue
        signed = _terms(node)
        if len(signed) < 2 or not _cancelling_term(signed, power, first):
            continue
        anchor = _statement_anchor(node, parents)
        if id(anchor) in seen:
            continue
        seen.add(id(anchor))
        finding = _moment_finding(node, anchor, rel, lines)
        if finding is not None:
            out.append(finding)
    return out


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: Optional[list[str]] = None) -> list[Finding]:
    """The findings in one parsed file. ``lines`` are its source lines, for the suppression comment."""
    lines = lines or []
    out: list[Finding] = []
    scopes: list[ast.AST] = [tree, *(n for n in ast.walk(tree) if isinstance(n, _FUNCTIONS))]
    for scope in scopes:
        own = list(_own_nodes(scope))
        if _scope_may_cancel(own):
            out.extend(_scope_findings(scope, own, rel, lines))
    return out


def find_cancellation_prone_moments(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = (),
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    *exclude* holds path fragments (matched against the POSIX relative path) of files to skip. Raises ``EmptyScanError``
    when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless
    *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    skipped = tuple(exclude)
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        if any(fragment in parsed.rel for fragment in skipped):
            continue
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cancellation_prone_moments(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = (),
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cancellation_prone_moments(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, exclude=exclude)
    guidance = "centre first (np.sum((x - x.mean())**2)) or use Welford's update; or `# moment-ok: <reason>` when the data is provably small-offset"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cancellation_prone_moments", refresh_command="PY_CI_SHARED_REFRESH=cancellation_prone_moments")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} cancellation-prone-moments finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
