"""Validator errors and RequestValidationError handlers that echo the rejected input back into the 422 detail.

A pydantic validator's error message becomes the ``msg`` of a FastAPI 422 response, and FastAPI's default handler also
returns each error's ``input``: the rejected value itself. Clients log 422 bodies, so a validator that quotes what it
refused puts that text (clinician notes, a patient's age, a token) into the client's logs. Found in the autopsia
backend (2026-10-15): ``bounded_entries`` raised ``ValueError(f"entry too long (...): {too_long[:40]!r}...")``, the
context check raised ``f"context[{key!r}] must be in [0, 130], got {val!r}"``, and the RequestValidationError handler
dropped ``input`` only for ``*_too_long`` errors, so an over-long entry refused by a ``value_error`` came back verbatim.

Two shapes are reported (the finding key is ``path::function::shape``, line-free, so a baseline survives edits):

``raise-echoes-value``
    In a *validator* (a function decorated with pydantic's ``field_validator``, ``validator``, ``model_validator`` or
    ``root_validator``, resolved through imports), the validated value is its first positional parameter that is not
    ``self``/``cls``; a ``model_validator(mode="after")`` has none and is out of scope (its ``self.field`` messages
    are mostly the model's own configuration, not request text). In a *helper* (a module-level function called by
    bare name, ``bounded_entries(value)``, or a method called as ``self.x(value)``/``cls.x(value)``, by a validator or
    another helper, with the value as an argument; matched by name across the scanned corpus), the parameter at that
    position (or keyword) is the value. ``obj.split(value)``/``re.match(p, value)`` are not helpers. A name assigned from an expression that reaches the
    value is the value too: a slice, a subscript, ``float(v)``, ``next(x for x in v ...)``, a ``for`` target over it.
    ``for k, val in v.items()`` and ``for i, item in enumerate(v)`` bind only ``val``/``item``: a key or an index
    says where, not what. Reported: ``raise ValueError/TypeError/AssertionError/PydanticCustomError(...)`` or an
    ``assert ..., msg`` whose arguments reach the value outside ``len``/``type``/``isinstance``/``bool``/``id``.
    Names are not judged: a validator that matches by name only (``validate_x``) without a pydantic decorator is out of
    scope, because "check_*" helpers in CLIs legitimately name the path they could not open.

``handler-echoes-input``
    A function registered for ``RequestValidationError`` (``@app.exception_handler(RequestValidationError)`` or
    ``app.add_exception_handler(RequestValidationError, fn)`` in the same file) that calls ``.errors()`` without
    removing ``input`` unconditionally. Removal is a dict comprehension filtering ``k != "input"`` /
    ``k not in (..., "input")``, ``e.pop("input", ...)``, ``del e["input"]``, an error rebuilt as a dict literal of
    string keys with ``loc`` or ``msg`` and without ``"input"``, or ``.errors(include_input=False)``. A removal that sits
    in a branch of an ``if`` or a conditional expression is removal for SOME errors only, which is the finding (autopsia's ``*_too_long`` case).

Not reported: ``len(v)`` and ``type(v).__name__`` in a message, a message naming the field (``info.field_name``), a
handler that drops ``input`` for every error, and a handler that never calls ``.errors()``.

Usage in a consumer's meta test::

    from py_ci_shared.validation_errors_echo_input import assert_validation_errors_echo_input

    def test_validation_errors_echo_input():
        assert_validation_errors_echo_input("src", baseline_path="baselines/validation_errors_echo_input.json")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, ParsedFile, child_nodes, qualified_functions, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "SHAPE_HANDLER", "SHAPE_RAISE", "assert_validation_errors_echo_input", "find_validation_errors_echo_input"]

RULE = "validation-errors-echo-input"
SHAPE_RAISE = "raise-echoes-value"
SHAPE_HANDLER = "handler-echoes-input"
_VALIDATOR_DECORATORS = frozenset({"field_validator", "validator", "model_validator", "root_validator"})
_ERRORS = frozenset({"ValueError", "TypeError", "AssertionError", "PydanticCustomError"})
#: Calls whose result says nothing about the content of their argument.
_SAFE_CALLS = frozenset({"len", "type", "isinstance", "issubclass", "bool", "id", "callable", "hasattr"})
_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]
#: helper name -> the positional indices and keyword names that carry the value.
_HelperArgs = dict[str, tuple[set[int], set[str]]]


def _final_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _walk_local(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """Every node under *nodes*, not descending into nested function, class or lambda bodies."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in child_nodes(node) if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)))


def _positional(fn: _FuncDef) -> list[str]:
    return [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]


def _validator_value(fn: _FuncDef, aliases: ImportAliases) -> Optional[str]:
    """The validated value's parameter name when *fn* is a pydantic validator, else None."""
    for deco in fn.decorator_list:
        qualified = aliases.qualified_name(deco)
        if qualified is None or not qualified.startswith("pydantic.") or qualified.rsplit(".", 1)[-1] not in _VALIDATOR_DECORATORS:
            continue
        params = _positional(fn)
        rest = [p for p in params[:1] if p not in ("self", "cls")] + params[1:]
        return rest[0] if rest else None
    return None


def _reaches(node: ast.AST, tainted: set[str]) -> bool:
    """The value of *node* depends on a tainted name's content (outside ``len``/``type``/... calls)."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Call) and _final_name(current.func) in _SAFE_CALLS and isinstance(current.func, ast.Name):
            continue
        if isinstance(current, ast.Name) and current.id in tainted:
            return True
        stack.extend(child_nodes(current))
    return False


def _names(target: ast.AST) -> list[str]:
    return [n.id for n in shared_walk(target) if isinstance(n, ast.Name)]


def _loop_bound(target: ast.AST, source: ast.AST, tainted: set[str]) -> list[str]:
    """Names a ``for``/comprehension target over *source* binds from the value. ``k, v in x.items()`` and
    ``i, item in enumerate(x)`` bind only the second element."""
    if not _reaches(source, tainted):
        return []
    if isinstance(target, ast.Tuple) and len(target.elts) == 2 and isinstance(source, ast.Call):
        if _final_name(source.func) in ("items", "enumerate"):
            return _names(target.elts[1])
    return _names(target)


def _propagate(fn: _FuncDef, seeds: set[str]) -> set[str]:
    """Every local name bound, directly or through other names, from the seeds' content."""
    tainted = set(seeds)
    nodes = list(_walk_local(fn.body))
    changed = True
    while changed:
        changed = False
        for node in nodes:
            new: list[str] = []
            if isinstance(node, ast.Assign) and _reaches(node.value, tainted):
                new = [n for t in node.targets for n in _names(t)]
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None and _reaches(node.value, tainted):
                new = _names(node.target)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                new = _loop_bound(node.target, node.iter, tainted)
            elif isinstance(node, ast.comprehension):
                new = _loop_bound(node.target, node.iter, tainted)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                new = [n for item in node.items if item.optional_vars is not None and _reaches(item.context_expr, tainted) for n in _names(item.optional_vars)]
            fresh = set(new) - tainted
            if fresh:
                tainted |= fresh
                changed = True
    return tainted


def _echoing_raises(fn: _FuncDef, tainted: set[str]) -> Iterator[tuple[int, str]]:
    for node in _walk_local(fn.body):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and _final_name(node.exc.func) in _ERRORS:
            if any(_reaches(a, tainted) for a in [*node.exc.args, *(kw.value for kw in node.exc.keywords)]):
                yield node.lineno, f"raise {_final_name(node.exc.func)}(...)"
        elif isinstance(node, ast.Assert) and node.msg is not None and _reaches(node.msg, tainted):
            yield node.lineno, "assert ..., <message>"


def _calls_with_value(fn: _FuncDef, tainted: set[str]) -> _HelperArgs:
    """Callees given the value: ``helper(value)`` -> ``{"helper": ({0}, set())}``. A method on the value itself
    (``value.strip()``) is not a helper."""
    out: _HelperArgs = {}
    for node in _walk_local(fn.body):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id not in _SAFE_CALLS:
            name = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in ("self", "cls"):
            name = f".{node.func.attr}"
        else:
            continue
        positions = {i for i, a in enumerate(node.args) if not isinstance(a, ast.Starred) and _reaches(a, tainted)}
        keywords = {kw.arg for kw in node.keywords if kw.arg is not None and _reaches(kw.value, tainted)}
        if positions or keywords:
            pos, kws = out.setdefault(name, (set(), set()))
            pos |= positions
            kws |= keywords
    return out


def _helper_seeds(fn: _FuncDef, args: tuple[set[int], set[str]]) -> set[str]:
    params = _positional(fn)
    if params and params[0] in ("self", "cls"):
        params = params[1:]
    positions, keywords = args
    every = {a.arg for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]}
    return {params[i] for i in positions if i < len(params)} | (keywords & every)


# --- shape 2: the RequestValidationError handler ---------------------------------------------------------------------


def _is_rve(node: ast.AST) -> bool:
    return _final_name(node) == "RequestValidationError"


def _handlers(tree: ast.Module) -> set[str]:
    """Names of functions registered for RequestValidationError in this file."""
    out: set[str] = set()
    for node in shared_walk(tree):
        if isinstance(node, ast.Call) and _final_name(node.func) == "add_exception_handler" and len(node.args) >= 2 and _is_rve(node.args[0]):
            name = _final_name(node.args[1])
            if name:
                out.add(name)
    return out


def _registered(fn: _FuncDef, added: set[str]) -> bool:
    if fn.name in added:
        return True
    return any(isinstance(d, ast.Call) and _final_name(d.func) == "exception_handler" and d.args and _is_rve(d.args[0]) for d in fn.decorator_list)


def _is_input(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "input"


def _excludes_input(test: ast.AST) -> bool:
    """``k != "input"`` or ``k not in (..., "input", ...)``."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    op, right = test.ops[0], test.comparators[0]
    if isinstance(op, ast.NotEq):
        return _is_input(right) or _is_input(test.left)
    if isinstance(op, ast.NotIn) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
        return any(_is_input(e) for e in right.elts)
    return False


def _removes_input(node: ast.AST) -> bool:
    if isinstance(node, ast.DictComp):
        return any(_excludes_input(cond) for gen in node.generators for cond in gen.ifs)
    if isinstance(node, ast.Call):
        if _final_name(node.func) == "pop" and node.args and _is_input(node.args[0]):
            return True
        if _final_name(node.func) == "errors":
            return any(kw.arg == "include_input" and isinstance(kw.value, ast.Constant) and kw.value.value is False for kw in node.keywords)
    if isinstance(node, ast.Delete):
        return any(isinstance(t, ast.Subscript) and _is_input(t.slice) for t in node.targets)
    if isinstance(node, ast.Dict) and node.keys:
        # An error rebuilt from an allow-list ({"loc": e["loc"], "msg": e["msg"], "type": e["type"]}).
        names = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        return len(names) == len(node.keys) and "input" not in names and bool(names & {"loc", "msg"})
    return False


def _conditional(node: ast.AST, parents: dict[int, ast.AST], stop: ast.AST) -> bool:
    """*node* sits in a branch (not the test) of an ``if`` or a conditional expression below *stop*."""
    child, parent = node, parents.get(id(node))
    while parent is not None and parent is not stop:
        if isinstance(parent, ast.IfExp) and child is not parent.test:
            return True
        if isinstance(parent, ast.If) and child is not parent.test:
            return True
        child, parent = parent, parents.get(id(parent))
    return False


def _handler_problem(fn: _FuncDef) -> Optional[str]:
    nodes = list(_walk_local(fn.body))
    if not any(isinstance(n, ast.Call) and _final_name(n.func) == "errors" and isinstance(n.func, ast.Attribute) for n in nodes):
        return None
    parents: dict[int, ast.AST] = {}
    for n in shared_walk(fn):
        for c in child_nodes(n):
            parents[id(c)] = n
    removals = [n for n in nodes if _removes_input(n)]
    if any(not _conditional(n, parents, fn) for n in removals):
        return None
    if removals:
        return "drops `input` only for some errors (inside a condition); drop it for every error"
    return "returns exc.errors() with each error's `input` (the rejected value); drop `input` for every error"


# --- the scan ----------------------------------------------------------------------------------------------------------


def _finding(rel: str, line: int, where: str, shape: str, message: str) -> Finding:
    return Finding(rel, line, RULE, f"{where}: {message}", key=f"{rel}::{where}::{shape}")


_PerFile = list[tuple[ParsedFile, ImportAliases, list[tuple[str, _FuncDef]]]]
_Seeded = dict[tuple[str, int], tuple[ParsedFile, str, _FuncDef, set[str]]]


def _merge(helpers: _HelperArgs, calls: _HelperArgs) -> bool:
    """Add *calls* to *helpers*; True when that added anything."""
    grew = False
    for name, (positions, keywords) in calls.items():
        pos, kws = helpers.setdefault(name, (set(), set()))
        if not (positions <= pos and keywords <= kws):
            pos |= positions
            kws |= keywords
            grew = True
    return grew


def _seed_helpers(per_file: _PerFile, helpers: _HelperArgs) -> _Seeded:
    """Every non-validator function the value is passed to, with its value parameters, followed to a fixed point (a
    helper's own helpers)."""
    seeded: _Seeded = {}
    changed = True
    while changed:
        changed = False
        for parsed, aliases, functions in per_file:
            for where, fn in functions:
                key = f".{fn.name}" if "." in where else fn.name  # a method answers self.x(...), a function x(...)
                if key not in helpers or _validator_value(fn, aliases) is not None:
                    continue
                seeds = _helper_seeds(fn, helpers[key])
                slot = (parsed.rel, fn.lineno)
                if not seeds or (slot in seeded and seeded[slot][3] == seeds):
                    continue
                seeded[slot] = (parsed, where, fn, seeds)
                changed = _merge(helpers, _calls_with_value(fn, _propagate(fn, seeds))) or changed
    return seeded


def _collect(files: list[ParsedFile]) -> list[Finding]:
    out: list[Finding] = []
    per_file: _PerFile = [(p, ImportAliases.from_tree(p.tree), list(qualified_functions(p.tree, locals_marker=False))) for p in files]
    helpers: _HelperArgs = {}
    for parsed, aliases, functions in per_file:
        added = _handlers(parsed.tree)
        for where, fn in functions:
            problem = _handler_problem(fn) if _registered(fn, added) else None
            if problem is not None:
                out.append(_finding(parsed.rel, fn.lineno, where, SHAPE_HANDLER, f"RequestValidationError handler {problem}"))
            value = _validator_value(fn, aliases)
            if value is None:
                continue
            tainted = _propagate(fn, {value})
            for line, what in _echoing_raises(fn, tainted):
                out.append(
                    _finding(
                        parsed.rel,
                        line,
                        where,
                        SHAPE_RAISE,
                        f"{what} in a validator puts the validated value `{value}` into the validation error (FastAPI's 422 detail)",
                    )
                )
            _merge(helpers, _calls_with_value(fn, tainted))
    for parsed, where, fn, seeds in _seed_helpers(per_file, helpers).values():
        param = ", ".join(sorted(seeds))
        for line, what in _echoing_raises(fn, _propagate(fn, seeds)):
            out.append(
                _finding(
                    parsed.rel,
                    line,
                    where,
                    SHAPE_RAISE,
                    f"{what} in a validator helper puts the validated value `{param}` into the validation error (FastAPI's 422 detail)",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def find_validation_errors_echo_input(
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
    return _collect(list(scan))


def assert_validation_errors_echo_input(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the shrink-only baseline does not accept."""
    found = find_validation_errors_echo_input(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "say what was wrong and where (the field, the bound, len(value)), never the value; drop `input` from every 422 error"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="validation_errors_echo_input", refresh_command="PY_CI_SHARED_REFRESH=validation_errors_echo_input")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} validation-errors-echo-input finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
