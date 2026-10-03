"""Shared check: an optional parameter tested for truth rather than for absence.

A parameter annotated ``int | None`` (or ``float | None``, ``str | None``, ``Optional[int]``)
has THREE states its author cares about: absent, zero-ish, and set. Writing ``if limit:``
collapses the first two, and the collapse is silent -- the code reads as though it handles
the default, and it does, by treating a deliberate 0 as "not given".

Two real instances, both from glossum:

* ``mwe_importer.import_file(limit: int | None)`` guarded its read with ``if limit and
  lines_read >= limit``. ``--limit 0`` therefore skipped the guard and read a multi-GB dump
  to EOF -- the opposite of what the caller asked for (2026-09-05 wave, 01-F4).
* ``word_selector`` scored difficulty with ``if sense.frequency_rank:``, so rank 0 -- the
  single most frequent word in the corpus, a legitimate value -- was treated as "unknown"
  and given the neutral 0.5 instead of the near-zero it had earned (2026-08-02 wave).

The check is deliberately limited to NUMERIC and string optionals. A ``list | None`` or a
``dict | None`` tested for truth is usually intentional ("empty or missing, same thing"),
and reporting those would drown the signal.

Usage::

    from py_ci_shared.optional_truthiness import assert_optionals_test_for_none

    def test_no_optional_is_tested_for_truth():
        assert_optionals_test_for_none(files=sorted(PKG.rglob("*.py")), repo_root=REPO)

Optionals carried on ``self`` (``follow_attributes=True``, the default): a class whose ``__init__`` stores a parameter
annotated ``Optional[int|float|Decimal]`` as ``self.<attr>``, or declares a class-level field with that annotation
(dataclass, pydantic), makes ``self.<attr>`` an optional number in every method; a truth test of ``self.<attr>``, of
``getattr(self, "<attr>", ...)`` or of a local bound from either is reported, and so is one hop of forwarding: such a
value passed as ``f(..., name=value)`` (or in ``name``'s position) to a function defined once in the scanned files,
which tests ``name`` for truth. Found by mlframe 1256fff4d (``max_runtime_mins=0`` / ``max_refits=0`` ran RFECV
unbounded: the budget was read as ``max_runtime_mins = self.max_runtime_mins`` and tested ``if max_runtime_mins:``,
and ``max_refits`` was forwarded to helpers that tested it). Precision rules, measured on mlframe: only attribute
names that read as a budget of something consumed (:data:`BOUND_NAMES`); and a method of a class that defines the
attribute itself is judged on that class's annotation alone, while a mixin or a module-level ``def f(self, ...)``
asks the classes nearest its file and needs them all to agree. A validation that rejects 0 in ``__init__`` does not
excuse a site: ``set_params`` bypasses it (the reason 1256fff4d fixed ``max_refits`` although ``__init__`` checks it).
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

from ._core import SourceError, parse_file, parse_source, relative_posix, walk

# Types where 0 is a value a caller can legitimately mean, distinct from absence.
#
# Numbers only, and the members are matched EXACTLY. `str | None` was included at first and
# produced 159 findings on one repo, nearly all of them `if lang:` -- an empty language code
# is not a meaningful value, so collapsing it with absence is correct there. Substring
# matching also mis-read `dict[str, int] | None` as an optional int. Both were noise around
# a signal worth keeping sharp.
_MEANINGFUL_FALSY = frozenset({"int", "float", "Decimal"})

_FuncLike = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda]
_LINE = re.compile(r"^(?P<path>.*?):\d+: ")


def _union_members(annotation: ast.AST) -> list[str]:
    """The parts of ``A | B | None`` / ``Optional[A]`` / ``Union[A, None]``, as source text. ``Annotated[T, ...]`` is ``T``."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _union_members(annotation.left) + _union_members(annotation.right)
    if isinstance(annotation, ast.Subscript):
        base = ast.unparse(annotation.value).split(".")[-1]
        inner = annotation.slice
        if base == "Annotated":
            first = inner.elts[0] if isinstance(inner, ast.Tuple) and inner.elts else inner
            return _union_members(first)
        if base in ("Optional", "Union"):
            members = [m for e in inner.elts for m in _union_members(e)] if isinstance(inner, ast.Tuple) else _union_members(inner)
            # Optional[X] means X | None: the None is implicit and has to be added, or the
            # whole spelling reads as non-optional.
            return [*members, "None"] if base == "Optional" else members
        return [ast.unparse(annotation)]  # dict[str, int] stays one opaque member
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return ["None"]
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # A string annotation ("int | None"): parse it rather than matching substrings.
        try:
            return _union_members(ast.parse(annotation.value, mode="eval").body)
        except SyntaxError:
            return [annotation.value]
    return [ast.unparse(annotation)]


def _optional_of_meaningful_falsy(annotation: ast.AST | None) -> bool:
    """True for ``int | None``, ``Optional[float]``, ``Union[Decimal, None]``, ``Annotated[int | None, ...]``."""
    if annotation is None:
        return False
    members = [m.strip().split(".")[-1] for m in _union_members(annotation)]
    return "None" in members and any(m in _MEANINGFUL_FALSY for m in members)


def _all_params(fn: _FuncLike) -> "list[ast.arg]":
    args = fn.args
    extra = [a for a in (args.vararg, args.kwarg) if a is not None]
    return list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs) + extra


def _optional_params(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> set[str]:
    return {a.arg for a in _all_params(fn) if _optional_of_meaningful_falsy(a.annotation)}


def _own_nodes(fn: _FuncLike) -> Iterator[ast.AST]:
    """Nodes of *fn*'s body, not descending into nested functions, lambdas or classes."""
    scoped = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    stack: list[ast.AST] = [n for n in (fn.body if isinstance(fn.body, list) else [fn.body]) if not isinstance(n, scoped)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, scoped))


def _nested(fn: ast.AST) -> Iterator[_FuncLike]:
    """Functions and lambdas defined directly inside *fn* (through any non-function nesting, classes included)."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node
            continue
        stack.extend(ast.iter_child_nodes(node))


def _truth_tested(expr: ast.AST) -> Iterator[ast.AST]:
    """Operands *expr* reads for truth: itself, through ``not`` and ``and``/``or``."""
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        yield from _truth_tested(expr.operand)
    elif isinstance(expr, ast.BoolOp):
        for value in expr.values:
            yield from _truth_tested(value)
    else:
        yield expr


def _tests_in(node: ast.AST) -> "list[ast.AST]":
    if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
        return [node.test]
    if isinstance(node, ast.BoolOp):
        return list(node.values)
    if isinstance(node, ast.comprehension):
        return list(node.ifs)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return [node.operand]
    return []


_COMPARE_AT_ZERO = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


def _number(node: ast.AST) -> "float | None":
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _number(node.operand)
        return None if inner is None else (-inner if isinstance(node.op, ast.USub) else inner)
    return None


def _holds_at_zero(compare: ast.AST, name: str) -> "bool | None":
    """What ``name <op> constant`` (or ``constant <op> name``) evaluates to when *name* is 0; None for any other shape."""
    if not (isinstance(compare, ast.Compare) and len(compare.ops) == 1 and type(compare.ops[0]) in _COMPARE_AT_ZERO):
        return None
    left, right, op = compare.left, compare.comparators[0], _COMPARE_AT_ZERO[type(compare.ops[0])]
    if isinstance(left, ast.Name) and left.id == name and _number(right) is not None:
        return bool(op(0.0, _number(right)))
    if isinstance(right, ast.Name) and right.id == name and _number(left) is not None:
        return bool(op(_number(left), 0.0))
    return None


def _zero_handled(fn: _FuncLike) -> "set[int]":
    """Truth tests whose collapse of 0 with None is spelled out by a sibling comparison: in ``not x or x <= 0`` 0 takes
    the same branch the comparison sends it to, and so it does in ``x and x > 0``. Returns the ids of those ``x`` nodes."""
    out: set[int] = set()
    for node in _own_nodes(fn):
        if not isinstance(node, ast.BoolOp):
            continue
        is_or = isinstance(node.op, ast.Or)
        for value in node.values:
            negated = isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not)
            target = value.operand if isinstance(value, ast.UnaryOp) and negated else value
            if not isinstance(target, ast.Name) or negated != is_or:
                continue
            # `not x or ...` treats 0 as True; `x and ...` treats 0 as False. A sibling comparison that agrees at 0 says so.
            if any(_holds_at_zero(other, target.id) is is_or for other in node.values if other is not value):
                out.add(id(target))
    return out


def _message(rel: str, line: int, name: str) -> str:
    return (
        f"{rel}:{line}: `{name}` is an optional number tested for "
        f"TRUTH; 0 is a value a caller can mean, and this reads it as absent. Use "
        f"`{name} is not None`."
    )


def _scan_function(fn: _FuncLike, inherited: "frozenset[str]", rel: str, out: "list[str]") -> None:
    own = {a.arg for a in _all_params(fn)}
    optional = (set(inherited) - own) | (_optional_params(fn) if not isinstance(fn, ast.Lambda) else set())
    seen: set[int] = _zero_handled(fn) if optional else set()
    if optional:
        for node in _own_nodes(fn):
            for tested in _tests_in(node):
                for expr in _truth_tested(tested):
                    if isinstance(expr, ast.Name) and expr.id in optional and id(expr) not in seen:
                        seen.add(id(expr))
                        out.append(_message(rel, expr.lineno, expr.id))
    for inner in _nested(fn):
        _scan_function(inner, frozenset(optional), rel, out)


def find_truthiness_tests(path: Path, *, repo_root: Optional[Path] = None) -> list[str]:
    """Report ``if param:`` / ``not param`` / ``param and ...`` (in ``if``, ``while``, ``assert``, a conditional
    expression or a comprehension filter) where *param* is an optional number.

    Paths are relative to *repo_root* when given (the file name otherwise, as before). A file that cannot be read
    or parsed is reported as ``<path>:<line>: unparsable: ...`` rather than skipped. Identical findings are all kept.
    """
    rel = relative_posix(path, repo_root) if repo_root is not None else path.name
    try:
        tree = parse_file(path)
    except SourceError as exc:
        return [f"{rel}:{exc.line or 1}: {exc.kind}: {exc.message}"]
    out: list[str] = []
    for node in tree.body:
        for fn in [node] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else _nested(node):
            _scan_function(fn, frozenset(), rel, out)
    return sorted(out)


# --- optionals carried on ``self`` and forwarded one hop (``follow_attributes``) --------------------------------------

BOUND_NAMES = re.compile(
    r"(?:^|_)(?:budget|timeout|deadline)(?:_|$)"
    r"|(?:^|_)(?:max|min|limit)_(?:\w+_)?(?:runtime|time|mins|minutes|secs|seconds|hours|refits|iters|iterations|evals|trials"
    r"|rounds|epochs|steps|retries|attempts|calls|requests|tokens)(?:_|$)",
    re.IGNORECASE,
)
"""Attribute names that read as a budget of something consumed (time, iterations, calls, tokens), where 0 means "stop
now". A size cap (``max_nfeatures``, ``max_train_size``, ``max_categorical_cardinality``) is left out: there 0 asks for
an empty result, and the project often defines it as "disabled" (``x is not None and x != 0``). So is a known total
(``total_iterations``). Measured on mlframe master: with every ``Optional`` number on ``self`` the attribute pass
reported 9 sites, 3 of them real zero-budget bugs; with this pattern it reports exactly those 3."""


@dataclass
class _AttrModel:
    """What the corpus says about each ``self.<attr>``: per class and across classes."""

    typed: "dict[str, list[tuple[str, bool]]]" = field(default_factory=dict)  # attr -> (rel, verdict) per typed definition
    owned: "dict[int, set[str]]" = field(default_factory=dict)  # id(ClassDef) -> attrs the class assigns or declares
    verdict: "dict[int, dict[str, bool]]" = field(default_factory=dict)  # id(ClassDef) -> attr -> optional number?
    funcs: "dict[str, list[tuple[str, _FuncLike]]]" = field(default_factory=dict)  # def name -> (rel, def)

    def qualifies(self, attr: str, cls: "Optional[ast.ClassDef]", rel: str, bound: "re.Pattern[str]") -> bool:
        """A class that defines *attr* answers for its own methods. A mixin method or a module-level ``def f(self)``
        asks the classes nearest to its file (same directory, then each parent), and only when all of those agree."""
        if not bound.search(attr):
            return False
        if cls is not None and attr in self.owned.get(id(cls), set()):
            return self.verdict.get(id(cls), {}).get(attr, False)
        defs = self.typed.get(attr, [])
        parts = rel.split("/")[:-1]
        for depth in range(len(parts), -1, -1):
            prefix = "/".join(parts[:depth])
            near = [ok for where, ok in defs if not prefix or where.startswith(prefix + "/")]
            if near:
                return all(near)
        return False


def _self_name(fn: _FuncLike, in_class: bool) -> "Optional[str]":
    """The instance parameter: the first positional of a method, or of a module-level ``def f(self, ...)``."""
    if isinstance(fn, ast.Lambda):
        return None
    positional = list(fn.args.posonlyargs) + list(fn.args.args)
    if not positional:
        return None
    decorators = {ast.unparse(d).split(".")[-1] for d in fn.decorator_list}
    if in_class and not decorators & {"staticmethod", "classmethod"}:
        return positional[0].arg
    return "self" if positional[0].arg == "self" else None


def _self_assignments(fn: "ast.FunctionDef | ast.AsyncFunctionDef", me: "Optional[str]") -> "Iterator[tuple[str, ast.AST]]":
    """``(attr, assignment)`` for every ``<me>.attr = ...`` / ``<me>.attr: T = ...`` in *fn*."""
    for node in ast.walk(fn):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for target in targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == me:
                yield target.attr, node


def _init_verdict(assign: ast.AST, params: "dict[str, Optional[ast.expr]]") -> "Optional[bool]":
    """Whether an ``__init__`` assignment types the attribute as an optional number; None when it does not type it."""
    if isinstance(assign, ast.AnnAssign):
        return _optional_of_meaningful_falsy(assign.annotation)
    if isinstance(assign, ast.Assign) and isinstance(assign.value, ast.Name) and assign.value.id in params:
        return _optional_of_meaningful_falsy(params[assign.value.id])
    return None


def _model_class(cls: ast.ClassDef, rel: str, model: _AttrModel) -> None:
    owned = model.owned.setdefault(id(cls), set())
    verdict = model.verdict.setdefault(id(cls), {})

    def typed(attr: str, ok: bool) -> None:
        owned.add(attr)
        model.typed.setdefault(attr, []).append((rel, ok))
        verdict[attr] = verdict.get(attr, False) or ok

    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            typed(stmt.target.id, _optional_of_meaningful_falsy(stmt.annotation))
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg: a.annotation for a in _all_params(stmt)}
        for attr, assign in _self_assignments(stmt, _self_name(stmt, True)):
            owned.add(attr)
            ok = _init_verdict(assign, params) if stmt.name == "__init__" else None
            if ok is not None:
                typed(attr, ok)


def _build_model(trees: "list[tuple[str, ast.Module]]", relevant: "list[tuple[str, ast.Module]]") -> _AttrModel:
    """Attribute definitions from the *relevant* files (those naming a bound attribute at all); the function index, which
    forwarding resolves callees in, from every file's module-level functions and class methods."""
    model = _AttrModel()
    for rel, tree in relevant:
        for node in walk(tree):
            if isinstance(node, ast.ClassDef):
                _model_class(node, rel, model)
    for rel, tree in trees:
        for node in tree.body:
            members = node.body if isinstance(node, ast.ClassDef) else [node]
            for fn in members:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    model.funcs.setdefault(fn.name, []).append((rel, fn))
    return model


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


@dataclass
class _Scope:
    rel: str
    cls: "Optional[ast.ClassDef]"
    selves: "frozenset[str]"
    carried: "dict[str, str]"  # local name -> the attribute it was bound from


def _carried_attr(expr: ast.AST, scope: _Scope, model: _AttrModel, bound: "re.Pattern[str]") -> "Optional[str]":
    """The attribute *expr* reads when it is an optional number carried on self: ``self.x``, ``getattr(self, "x", ...)``
    or a local bound from either."""
    if isinstance(expr, ast.Name):
        return scope.carried.get(expr.id)
    attr: Optional[str] = None
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id in scope.selves:
        attr = expr.attr
    elif isinstance(expr, ast.Call) and _call_name(expr.func) == "getattr" and isinstance(expr.func, ast.Name) and len(expr.args) >= 2:
        owner, name = expr.args[0], expr.args[1]
        if isinstance(owner, ast.Name) and owner.id in scope.selves and isinstance(name, ast.Constant) and isinstance(name.value, str):
            attr = name.value
    return attr if attr is not None and model.qualifies(attr, scope.cls, scope.rel, bound) else None


def _truth_tests_of(fn: _FuncLike, match: "Callable[[ast.AST], Optional[str]]") -> "Iterator[tuple[ast.AST, str]]":
    skipped = _zero_handled(fn)
    for node in _own_nodes(fn):
        for tested in _tests_in(node):
            for expr in _truth_tested(tested):
                attr = match(expr)
                if attr is not None and id(expr) not in skipped:
                    skipped.add(id(expr))
                    yield expr, attr


def _forwarded(fn: _FuncLike, param: str, inherited: bool = False) -> "Iterator[ast.AST]":
    """Truth tests of the parameter *param* in *fn* and in the functions nested in it that do not rebind it."""
    declared = param in {a.arg for a in _all_params(fn)}
    if declared == inherited:  # a nested def rebinding it, or a callee without it
        return
    for expr, _ in _truth_tests_of(fn, lambda e: param if isinstance(e, ast.Name) and e.id == param else None):
        yield expr
    for inner in _nested(fn):
        yield from _forwarded(inner, param, True)


def _scan_attr_function(fn: _FuncLike, scope: _Scope, model: _AttrModel, bound: "re.Pattern[str]", out: "set[tuple[str, int, str, str]]") -> None:
    params = {a.arg for a in _all_params(fn)}
    selves = set(scope.selves) - params
    me = _self_name(fn, scope.cls is not None and any(fn is s for s in scope.cls.body))
    if me is not None:
        selves.add(me)
    scope = _Scope(scope.rel, scope.cls, frozenset(selves), {k: v for k, v in scope.carried.items() if k not in params})
    nodes = list(_own_nodes(fn))
    if scope.carried or any(_carried_attr(n, scope, model, bound) for n in nodes if isinstance(n, (ast.Attribute, ast.Call))):
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                attr = _carried_attr(node.value, scope, model, bound)
                if attr is not None and len(targets) == 1 and isinstance(targets[0], ast.Name):
                    scope.carried[targets[0].id] = attr
        for expr, attr in _truth_tests_of(fn, lambda e: _carried_attr(e, scope, model, bound)):
            why = f"(bound from `self.{attr}`)" if isinstance(expr, ast.Name) else ""
            out.add((scope.rel, getattr(expr, "lineno", 1), ast.unparse(expr), why))
        for node in nodes:
            if isinstance(node, ast.Call) and (node.keywords or node.args):
                _follow_call(node, scope, model, bound, out)
    for inner in _nested(fn):
        _scan_attr_function(inner, scope, model, bound, out)


def _passed(call: ast.Call, callee: _FuncLike) -> "Iterator[tuple[str, ast.expr]]":
    """``(parameter, argument)`` pairs of *call* into *callee*: keywords, and positionals matched by position (the
    instance parameter skipped when an attribute call reaches a method or a ``def f(self, ...)``)."""
    for kw in call.keywords:
        if kw.arg:
            yield kw.arg, kw.value
    positional = [a.arg for a in list(callee.args.posonlyargs) + list(callee.args.args)]
    if isinstance(call.func, ast.Attribute) and positional and positional[0] in ("self", "cls"):
        positional = positional[1:]
    for name, arg in zip(positional, call.args):
        if not isinstance(arg, ast.Starred):
            yield name, arg


def _follow_call(call: ast.Call, scope: _Scope, model: _AttrModel, bound: "re.Pattern[str]", out: "set[tuple[str, int, str, str]]") -> None:
    """One hop: ``f(..., name=<carried value>)`` (or the value in ``name``'s position) into the one function of that name,
    which tests ``name`` for truth."""
    targets = model.funcs.get(_call_name(call.func), [])
    if len(targets) != 1:
        return  # unknown or ambiguous callee: no guess
    rel, callee = targets[0]
    annotations = {a.arg: a.annotation for a in _all_params(callee)}
    for param, value in _passed(call, callee):
        attr = _carried_attr(value, scope, model, bound)
        if attr is None or param not in annotations or _optional_of_meaningful_falsy(annotations[param]):
            continue  # an annotated optional parameter is already the per-function check's finding
        for expr in _forwarded(callee, param):
            out.add((rel, getattr(expr, "lineno", 1), param, f"(passed `self.{attr}` from {scope.rel})"))


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _attribute_findings(files: "list[tuple[str, str, ast.Module]]", bound: "re.Pattern[str]") -> "list[tuple[str, int, str, str]]":
    """*files* are ``(rel, source, tree)``. Only a file whose text names a bound identifier can define or read one, so
    the others are never walked (on mlframe that is most of them, and the difference between seconds and minutes)."""
    relevant = [(rel, tree) for rel, source, tree in files if any(bound.search(w) for w in set(_IDENTIFIER.findall(source)))]
    model = _build_model([(rel, tree) for rel, _, tree in files], relevant)
    out: set[tuple[str, int, str, str]] = set()
    for rel, tree in relevant:
        stack: list[tuple[ast.AST, Optional[ast.ClassDef]]] = [(n, None) for n in tree.body]
        while stack:
            node, cls = stack.pop()
            if isinstance(node, ast.ClassDef):
                stack.extend((n, node) for n in node.body)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _scan_attr_function(node, _Scope(rel, cls, frozenset(), {}), model, bound, out)
    return sorted(out)


def find_attribute_truthiness_tests(files: Iterable[Path], *, repo_root: Path, bound_names: "Union[str, re.Pattern[str]]" = BOUND_NAMES) -> list[str]:
    """Truth tests of optional numbers carried on ``self`` and forwarded one hop (see the module doc), in the same
    ``path:line: message`` form as :func:`find_truthiness_tests`. Unparsable files are left to that function, which
    reports them; *bound_names* is the attribute-name pattern (default :data:`BOUND_NAMES`)."""
    parsed: list[tuple[str, str, ast.Module]] = []
    for path in files:
        try:
            source, tree = parse_source(path)
        except SourceError:
            continue
        parsed.append((relative_posix(path, repo_root), source, tree))
    pattern = re.compile(bound_names) if isinstance(bound_names, str) else bound_names
    return sorted(
        f"{rel}:{line}: `{expr}` {why}{' ' if why else ''}is an optional number tested for TRUTH; 0 is a value a caller can mean, and this "
        f"reads it as absent. Use `{expr} is not None`."
        for rel, line, expr, why in _attribute_findings(parsed, pattern)
    )


def finding_key(finding: str) -> str:
    """A finding without its line number, so an edit above it does not invalidate a baseline entry."""
    return _LINE.sub(lambda m: f"{m.group('path')}: ", finding, count=1)


def assert_optionals_test_for_none(
    *,
    files: Iterable[Path],
    repo_root: Path,
    baseline: Iterable[str] = (),
    min_subjects: int = 1,
    follow_attributes: bool = True,
    bound_names: "Union[str, re.Pattern[str]]" = BOUND_NAMES,
) -> None:
    """Fail on a new optional-number parameter tested for truth, on a baseline entry nothing matches, or on an
    unparsable file.

    ``baseline`` takes already-reviewed findings verbatim (with or without the line number: entries are compared
    without it, as a multiset, with paths relative to *repo_root*), so the check can be adopted on a tree that has
    some, without either failing every commit or hiding the backlog. ``follow_attributes`` adds the findings of
    :func:`find_attribute_truthiness_tests` (optionals carried on ``self`` and forwarded one hop).
    """
    import pytest

    paths = list(files)
    findings = [p for path in paths for p in find_truthiness_tests(path, repo_root=repo_root)]
    if follow_attributes:
        findings += find_attribute_truthiness_tests(paths, repo_root=repo_root, bound_names=bound_names)
    unparsed = [f for f in findings if ": unparsable: " in f or ": unreadable: " in f]
    parsed = len(paths) - len({f.split(":", 1)[0] for f in unparsed})
    if parsed < min_subjects:
        pytest.fail(f"only {parsed} file(s) scanned -- expected at least {min_subjects}; the scan lost its subject")

    budget = Counter(finding_key(b) for b in baseline)
    problems: list[str] = []
    new: list[str] = []
    for finding in findings:
        if finding in unparsed:
            continue
        key = finding_key(finding)
        if budget[key] > 0:
            budget[key] -= 1
        else:
            new.append(finding)
    stale = sorted(budget.elements())
    if unparsed:
        problems.append(f"{len(unparsed)} file(s) could not be parsed, so they were not checked:\n  " + "\n  ".join(unparsed))
    if new:
        problems.append(f"{len(new)} optional(s) tested for truth rather than for None:\n  " + "\n  ".join(new))
    if stale:
        problems.append(f"{len(stale)} baseline entr(ies) no longer found -- remove them:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))
