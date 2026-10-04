"""A test must not assert on a wall-clock ratio or ceiling derived from one measurement per side.

``t0 = perf_counter(); work(); t = perf_counter() - t0`` is one sample of a noisy quantity. On a shared CI runner under
``pytest-xdist`` a single sample is routinely perturbed 2x-3x by neighbours that have nothing to do with the code, so a
test asserting ``t_cpu / t_gpu >= 3`` or ``elapsed < 5.0`` from one run per side fails on contention, and the usual
repair (raising the constant) is how a real regression gets absorbed. In mlframe the same flake was repaired four times
(a bootstrap-AUC speedup floor, a polars window benchmark, a diagnostics baseline, a quantile-clip identity test), each by
the same change: best-of-N per side before comparing.

What is reported, per test function (a function whose name starts with ``test``, methods included):

* ``single-shot-timing-assertion``: an ``assert`` (or ``assertLess``/``assertGreater`` family call) that compares a
  duration the test measured ONCE (a timer subtraction, a call to a helper that times one run, ``timeit.timeit(number=1)``)
  against another measured duration or a ratio of them (``t_cpu / t_gpu >= 3``, ``t_njit <= t_numpy * 1.5``), or a single
  measured duration against an UPPER bound (``assert elapsed < 5.0``; a lower bound such as ``elapsed >= delay`` cannot fail
  from contention, which only lengthens a run, and is not reported);
* ``tight-timing-race``: a race between two measured durations with less than 25% slack (``t_a <= t_b * 1.05``,
  ``t_a >= t_b * 0.95``), even when each side is best-of-N: more samples shrink the noise of each side, not the slack that
  contention eats; durations read off a result object by a name ending ``_seconds``/``_elapsed``/``_duration_s`` counts
  here when two of them are compared;
* ``timing-skip-after-measure``: a test that computes a timing and only then calls ``pytest.skip(...)`` because of xdist,
  contention or an unreliable clock (``if os.environ.get("PYTEST_XDIST_WORKER"): pytest.skip("timing unreliable under
  -n")``). It never asserts where CI runs it, so the timing check is dead there; skip BEFORE measuring, or assert on
  work counts instead.

Accepted: a duration produced inside a loop or comprehension, one timed across a loop (``t0 = ...; for _ in range(20): work();
mean = (now() - t0) / 20``), or through ``min``/``sorted``/``median``/``mean`` and ``best_of``/``repeat`` style helpers
(best-of-N); ``timeit.repeat``; a bound the host scales (``assert speedup >= perf_speedup_floor(3.0)``, ``wall <
perf_time_budget(30)``, or a name assigned from such a call; names matching ``perf_*``, ``*budget*``, ``*speedup_floor``,
``*_floor``, ``*_ceiling``); assertions on work counts; tests marked ``hang_guard`` or ``perf``
(``exempt_markers``), whose ceiling is deliberately loose or which run outside the default job.

The analysis is per function and by source order: a name is a measured duration when it was assigned from a timer
subtraction (``time.perf_counter() - t0``, any alias of ``time``/``timeit`` resolved through the module's imports) or from a
call to a function of the same module that returns a single timed run, and a value derived from such names stays one. It
is a heuristic, so a corpus with a backlog should pass ``baseline_path``; a call to a helper defined in another module is
not recognised unless its name is in ``single_shot_helpers``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = [
    "RULE",
    "RULE_SKIP",
    "RULE_TIGHT",
    "assert_single_shot_timing_assertion",
    "find_single_shot_timing_assertion",
]

RULE = "single-shot-timing-assertion"
RULE_TIGHT = "tight-timing-race"
RULE_SKIP = "timing-skip-after-measure"

_TIMERS = frozenset(
    {
        "time.time",
        "time.time_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
        "time.monotonic",
        "time.monotonic_ns",
        "time.process_time",
        "time.process_time_ns",
        "timeit.default_timer",
    }
)
_AGGREGATOR = re.compile(r"^(min|sorted|mean|fmean|amin|median\w*)$|best_?of|min_?of|repeat|percentile|quantile", re.IGNORECASE)
_MEASURING_NAME = re.compile(r"best_?of|min_?of|repeat|bench|timeit", re.IGNORECASE)
_CALIBRATED = re.compile(r"^perf_|budget|speedup_floor|_floor$|_ceiling$", re.IGNORECASE)
_PASS_THROUGH = frozenset({"float", "int", "abs", "round"})
_REPORTED = ("_seconds", "_elapsed", "_duration_s")
_REPORTED_ATOM = "rep:"
_ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_UNITTEST_COMPARES = {"assertLess": ast.Lt, "assertLessEqual": ast.LtE, "assertGreater": ast.Gt, "assertGreaterEqual": ast.GtE}
_MIN_SLACK = 1.25
_SKIP_CAUSE = re.compile(
    r"xdist|numprocesses|\b(under|with)\s+-n\b|-n\s*\d|contention|contended|unreliable|noisy|flak|too\s+(slow|loaded|busy)|loaded|loadavg|shared\s+(runner|ci)",
    re.IGNORECASE,
)
_Helper = tuple[str, Optional[frozenset[int]]]
_FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)
_Scoped = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


class _Val:
    """What an expression holds: the measured durations it derives from (atom id -> measured once?), and whether it is a raw timer stamp."""

    __slots__ = ("atoms", "line", "ratio", "stamp")

    def __init__(self, atoms: Optional[dict[str, bool]] = None, stamp: bool = False, line: int = 0, ratio: bool = False) -> None:
        self.atoms: dict[str, bool] = atoms or {}
        self.stamp = stamp
        self.line = line  # a stamp: the line it was taken on
        self.ratio = ratio  # derived through a division: a required ratio, not a race


def _merge(vals: Iterable[_Val]) -> _Val:
    atoms: dict[str, bool] = {}
    ratio = False
    for val in vals:
        ratio = ratio or val.ratio
        for key, single in val.atoms.items():
            atoms[key] = atoms.get(key, False) or single
    return _Val(atoms, ratio=ratio)


def _last_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Nodes of *scope* outside any nested function, lambda or class."""
    out: list[ast.AST] = []
    stack = [c for c in ast.iter_child_nodes(scope) if not isinstance(c, _Scoped)]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _Scoped))
    return out


class _Analysis:
    """The measurements and comparisons of one test function."""

    def __init__(self, aliases: ImportAliases, helpers: dict[str, _Helper], timers: frozenset[str], loops: list[tuple[int, int]]) -> None:
        self.aliases = aliases
        self.helpers = helpers
        self.timers = timers
        self.loops = loops
        self.calibrated: set[str] = set()
        self.env: dict[str, _Val] = {}
        self.first_measure = 0
        self.problems: list[tuple[int, str, str]] = []

    def _atom(self, node: ast.AST, single: bool) -> _Val:
        line = getattr(node, "lineno", 0)
        if line and (not self.first_measure or line < self.first_measure):
            self.first_measure = line
        return _Val({f"dur@{line}:{getattr(node, 'col_offset', 0)}": single})

    def eval(self, node: ast.AST, in_loop: bool) -> _Val:
        """Evaluate one expression to the durations it derives from."""
        if isinstance(node, ast.Call):
            return self._call(node, in_loop)
        if isinstance(node, ast.Name):
            if node.id in self.env:
                return self.env[node.id]
            return _Val({f"{_REPORTED_ATOM}{node.id}": True}) if node.id.endswith(_REPORTED) else _Val()
        if isinstance(node, ast.Attribute):
            return _Val({f"{_REPORTED_ATOM}{node.attr}": True}) if node.attr.endswith(_REPORTED) else _Val()
        if isinstance(node, ast.BinOp):
            left, right = self.eval(node.left, in_loop), self.eval(node.right, in_loop)
            if isinstance(node.op, ast.Sub) and left.stamp and right.stamp:
                # a loop between the two stamps makes it a mean over iterations, not one sample
                averaged = any(right.line < start and end <= node.lineno for start, end in self.loops)
                return self._atom(node, not in_loop and not averaged)
            merged = _merge([left, right])
            if isinstance(node.op, ast.Div) and merged.atoms:
                merged.ratio = True
            return merged
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            inner = self.eval(node.elt, True)
            return _Val({k: False for k in inner.atoms})
        if isinstance(node, ast.DictComp):
            inner = self.eval(node.value, True)
            return _Val({k: False for k in inner.atoms})
        if isinstance(node, (ast.UnaryOp, ast.Subscript, ast.Starred)):
            return self.eval(node.operand if isinstance(node, ast.UnaryOp) else node.value, in_loop)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return _merge(self.eval(e, in_loop) for e in node.elts)
        if isinstance(node, ast.IfExp):
            return _merge([self.eval(node.body, in_loop), self.eval(node.orelse, in_loop)])
        return _Val()

    def _call(self, node: ast.Call, in_loop: bool) -> _Val:
        qualified = self.aliases.qualified_name(node.func) or ""
        last = _last_name(node.func)
        if qualified in self.timers:
            return _Val(stamp=True, line=node.lineno)
        if qualified == "timeit.timeit":
            once = any(kw.arg == "number" and isinstance(kw.value, ast.Constant) and kw.value.value == 1 for kw in node.keywords)
            return self._atom(node, once and not in_loop)
        if qualified == "timeit.repeat":
            return self._atom(node, False)
        if last in self.helpers:
            return self._atom(node, self.helpers[last][0] == "single" and not in_loop)
        if _AGGREGATOR.search(last):
            inner = _merge(self.eval(a, True) for a in [*node.args, *(k.value for k in node.keywords)])
            if inner.atoms:
                return _Val({k: False for k in inner.atoms})
            return self._atom(node, False) if _MEASURING_NAME.search(last) else _Val()
        if last in _PASS_THROUGH and node.args:
            return self.eval(node.args[0], in_loop)
        return _Val()

    def bind(self, target: ast.AST, value: ast.AST, val: _Val) -> None:
        """Record *val* for the names *target* binds; element-wise for ``a, b = x, y``."""
        if isinstance(target, ast.Name):
            self.env[target.id] = val
        elif isinstance(target, (ast.Tuple, ast.List)):
            positions = self.helpers[_last_name(value.func)][1] if isinstance(value, ast.Call) and _last_name(value.func) in self.helpers else None
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts):
                for t, v in zip(target.elts, value.elts):
                    self.bind(t, v, self.eval(v, False))
            elif positions is not None:  # ``rec, secs = fit()``: only the elements the helper times carry a duration
                for i, t in enumerate(target.elts):
                    self.bind(t, value, val if i in positions else _Val())
            else:
                for t in target.elts:
                    self.bind(t, value, val)

    def _statement(self, stmt: ast.stmt, in_loop: bool) -> None:
        """Track what one simple statement binds, or judge it when it is an assertion."""
        if isinstance(stmt, ast.Assign):
            val = self.eval(stmt.value, in_loop)
            for target in stmt.targets:
                self.bind(target, stmt.value, val)
                if isinstance(target, ast.Name) and self._calibrated(stmt.value):
                    self.calibrated.add(target.id)
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            self.bind(stmt.target, stmt.value, self.eval(stmt.value, in_loop))
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            self.env[stmt.target.id] = _merge([self.env.get(stmt.target.id, _Val()), self.eval(stmt.value, in_loop)])
        elif isinstance(stmt, ast.Assert):
            self.judge(stmt.test)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            self.judge_unittest(stmt.value)

    def run(self, stmts: list[ast.stmt], in_loop: bool) -> None:
        """Walk *stmts* in source order, tracking assignments and judging assertions."""
        for stmt in stmts:
            if isinstance(stmt, _Scoped):
                continue
            self._statement(stmt, in_loop)
            for field in ("body", "orelse", "finalbody"):
                block = getattr(stmt, field, None)
                if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                    self.run(block, in_loop or isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)))
            for handler in getattr(stmt, "handlers", []):
                self.run(handler.body, in_loop)

    def judge_unittest(self, call: ast.Call) -> None:
        """``self.assertLess(elapsed, 5)`` reads as ``elapsed < 5``."""
        op = _UNITTEST_COMPARES.get(_last_name(call.func))
        if op is not None and len(call.args) >= 2:
            self.compare(call.args[0], op(), call.args[1], call.lineno)

    def judge(self, test: ast.expr) -> None:
        """Judge every ordering comparison inside an ``assert`` condition."""
        for node in ast.walk(test):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                for i, op in enumerate(node.ops):
                    self.compare(operands[i], op, operands[i + 1], node.lineno)

    def _calibrated(self, node: ast.AST) -> bool:
        """Does the expression call a helper that scales its bound to the host (``perf_speedup_floor(3.0)``, ``perf_time_budget(30)``)?"""
        return any(
            (isinstance(n, ast.Call) and self.aliases.qualified_name(n.func) not in self.timers and _CALIBRATED.search(_last_name(n.func)))
            or (isinstance(n, ast.Name) and n.id in self.calibrated)
            for n in ast.walk(node)
        )

    def compare(self, left: ast.expr, op: ast.cmpop, right: ast.expr, line: int) -> None:
        if not isinstance(op, _ORDERING) or self._calibrated(left) or self._calibrated(right):
            return
        vl, vr = self.eval(left, False), self.eval(right, False)
        atoms = _merge([vl, vr]).atoms
        measured = {k: v for k, v in atoms.items() if not k.startswith(_REPORTED_ATOM)}
        reported = [k for k in atoms if k.startswith(_REPORTED_ATOM)]
        scaled = any(isinstance(s, ast.BinOp) and isinstance(s.op, ast.Mult) for s in (left, right))
        counted = len(measured) + (len(reported) if len(reported) >= 2 and scaled else 0)
        if counted >= 2:
            if any(measured.values()) or not measured:
                self.problems.append(
                    (line, RULE, "compares wall-clock durations measured once per side; one sample moves 2x-3x on a shared runner, use best-of-N")
                )
            elif _slack(left, right, vl.ratio or vr.ratio) < _MIN_SLACK:
                self.problems.append((line, RULE_TIGHT, "races two wall-clock measurements with under 25% slack; contention outweighs the margin"))
            return
        if len(measured) == 1 and next(iter(measured.values())):
            small = left if isinstance(op, (ast.Lt, ast.LtE)) else right
            if self.eval(small, False).atoms:
                self.problems.append((line, RULE, "asserts a wall-clock ceiling on one measured run; one sample moves 2x-3x on a shared runner, use best-of-N"))


def _slack(left: ast.expr, right: ast.expr, ratio: bool) -> float:
    """Distance from parity, as a factor >= 1: ``1.05`` for ``a <= b * 1.05`` (and for ``a >= b * 0.95``); infinite when there is no constant to read."""

    def factor(node: ast.expr) -> Optional[float]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            for const in (node.left, node.right):
                if isinstance(const, ast.Constant) and isinstance(const.value, (int, float)) and not isinstance(const.value, bool) and const.value > 0:
                    return float(const.value)
        return None

    def plain(node: ast.expr) -> bool:
        """A bare operand, optionally scaled by one constant: anything with an offset or a second term is not a race the slack can be read off."""
        if isinstance(node, ast.BinOp):
            return isinstance(node.op, ast.Mult) and (
                (plain(node.left) and isinstance(node.right, ast.Constant)) or (plain(node.right) and isinstance(node.left, ast.Constant))
            )
        return not isinstance(node, (ast.Compare, ast.BoolOp, ast.IfExp))

    if not (plain(left) and plain(right)):
        return float("inf")
    k = factor(left) or factor(right)
    if k is None or ratio:
        # a speedup floor (``t_a / t_b >= 1.15``, ``speedup >= floor``) states a required ratio, and a bare ``best_a < best_b`` states no margin to read
        return float("inf")
    return max(k, 1.0 / k)


def _timed_positions(own: list[ast.AST], aliases: ImportAliases, timers: frozenset[str]) -> Optional[frozenset[int]]:
    """The tuple positions of a helper's ``return`` that carry a timing, or None when it returns the timing itself (or something unreadable)."""

    def mentions(node: ast.AST, names: set[str]) -> bool:
        return any(
            (isinstance(n, ast.Call) and aliases.qualified_name(n.func) in timers) or (isinstance(n, ast.Name) and n.id in names) for n in ast.walk(node)
        )

    names: set[str] = set()
    for _ in range(3):
        for n in own:
            if isinstance(n, ast.Assign) and mentions(n.value, names):
                names.update(t.id for t in n.targets if isinstance(t, ast.Name))
    positions: set[int] = set()
    for n in own:
        if isinstance(n, ast.Return) and n.value is not None:
            if not isinstance(n.value, ast.Tuple):
                return None
            positions.update(i for i, element in enumerate(n.value.elts) if mentions(element, names))
    return frozenset(positions) or None


def _timing_helpers(tree: ast.Module, aliases: ImportAliases, timers: frozenset[str], extra: Iterable[str]) -> dict[str, _Helper]:
    """``name -> ("single" | "repeated", timed tuple positions)`` for each non-test function of the module that times its body and returns the measurement."""
    out: dict[str, _Helper] = {name: ("single", None) for name in extra}
    for node in ast.walk(tree):
        if not isinstance(node, _FuncDef) or node.name.startswith("test"):
            continue
        own = _own_nodes(node)
        if not any(isinstance(n, ast.Call) and aliases.qualified_name(n.func) in timers for n in own):
            continue
        if not any(isinstance(n, ast.Return) and n.value is not None for n in own):
            continue
        looped = any(isinstance(n, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.GeneratorExp)) for n in own) or any(
            isinstance(n, ast.Call) and _AGGREGATOR.search(_last_name(n.func)) for n in own
        )
        out.setdefault(node.name, ("repeated" if looped else "single", _timed_positions(own, aliases, timers)))
    return out


def _marked(node: ast.AST, markers: frozenset[str]) -> bool:
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        if _last_name(target) in markers:
            return True
    return False


def _module_marked(tree: ast.Module, markers: frozenset[str]) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            if any(isinstance(n, ast.Attribute) and n.attr in markers for n in ast.walk(node.value)):
                return True
    return False


def _test_functions(tree: ast.Module, markers: frozenset[str]) -> list[tuple[str, ast.AST]]:
    """``(qualified name, function)`` for each test function not exempted by a marker on itself or its class."""
    out: list[tuple[str, ast.AST]] = []

    def visit(node: ast.AST, prefix: str, exempt: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", exempt or _marked(child, markers))
            elif isinstance(child, _FuncDef):
                if child.name.startswith("test") and not exempt and not _marked(child, markers):
                    out.append((f"{prefix}{child.name}", child))
                visit(child, f"{prefix}{child.name}.", exempt)
            else:
                visit(child, prefix, exempt)

    visit(tree, "", False)
    return out


def _skip_after_measure(func: ast.AST, analysis: _Analysis) -> None:
    """Record each ``pytest.skip`` placed after the first measurement whose condition or reason blames xdist or contention."""
    if not analysis.first_measure:
        return
    parents = {id(c): n for n in [func, *_own_nodes(func)] for c in ast.iter_child_nodes(n)}
    for node in _own_nodes(func):
        if not isinstance(node, ast.Call) or node.lineno <= analysis.first_measure:
            continue
        if analysis.aliases.qualified_name(node.func) != "pytest.skip" and _last_name(node.func) != "skipTest":
            continue
        words = [str(n.value) for a in [*node.args, *(k.value for k in node.keywords)] for n in ast.walk(a) if isinstance(n, ast.Constant)]
        cur = parents.get(id(node))
        while cur is not None and cur is not func:
            if isinstance(cur, (ast.If, ast.IfExp, ast.While)):
                words.append(ast.unparse(cur.test))
            cur = parents.get(id(cur))
        if _SKIP_CAUSE.search(" ".join(words)):
            analysis.problems.append(
                (node.lineno, RULE_SKIP, "skips for xdist or contention after the timing was computed, so the timing check never runs where CI runs it")
            )


def _findings_in(
    tree: ast.Module,
    rel: str,
    aliases: ImportAliases,
    markers: frozenset[str],
    timers: frozenset[str],
    extra_helpers: Iterable[str],
) -> list[Finding]:
    """The findings in one parsed file."""
    if _module_marked(tree, markers):
        return []
    helpers = _timing_helpers(tree, aliases, timers, extra_helpers)
    out: list[Finding] = []
    for qualname, func in _test_functions(tree, markers):
        loops = [(n.lineno, n.end_lineno or n.lineno) for n in _own_nodes(func) if isinstance(n, (ast.For, ast.AsyncFor, ast.While))]
        analysis = _Analysis(aliases, helpers, timers, loops)
        analysis.run(func.body, False)  # type: ignore[attr-defined]
        _skip_after_measure(func, analysis)
        out.extend(Finding(rel, line, rule, f"{qualname}: {what}") for line, rule, what in analysis.problems)
    return out


def find_single_shot_timing_assertion(
    root: Union[str, Path],
    *,
    exempt_markers: Iterable[str] = ("hang_guard", "perf"),
    timer_calls: Iterable[str] = (),
    single_shot_helpers: Iterable[str] = (),
    patterns: Iterable[str] = ("test_*.py", "*_test.py"),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    *exempt_markers* are pytest marker names (``@pytest.mark.perf``, a class decorator or a module ``pytestmark`` too) whose
    tests are not read. *timer_calls* adds qualified names (``mypkg.clock.now``) to the recognised timers and
    *single_shot_helpers* names helpers defined outside the scanned file that time one run. Raises ``EmptyScanError`` when
    fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless
    *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, patterns=tuple(patterns), use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    markers = frozenset(exempt_markers)
    timers = _TIMERS | frozenset(timer_calls)
    extra = tuple(single_shot_helpers)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), markers, timers, extra))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_single_shot_timing_assertion(
    root: Union[str, Path],
    *,
    exempt_markers: Iterable[str] = ("hang_guard", "perf"),
    timer_calls: Iterable[str] = (),
    single_shot_helpers: Iterable[str] = (),
    patterns: Iterable[str] = ("test_*.py", "*_test.py"),
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_single_shot_timing_assertion(
        root,
        exempt_markers=exempt_markers,
        timer_calls=timer_calls,
        single_shot_helpers=single_shot_helpers,
        patterns=patterns,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "measure each side best-of-N (min over a loop or comprehension) before comparing, or assert on work counts, or skip before measuring"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="single_shot_timing_assertion", refresh_command="PY_CI_SHARED_REFRESH=single_shot_timing_assertion")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} single-shot timing assertion finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
