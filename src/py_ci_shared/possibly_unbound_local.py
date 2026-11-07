"""A local bound on only some paths (one ``if``/``try``/``match`` arm) and read after the construct raises ``UnboundLocalError`` on the other path.

mlframe 169f17000: ``device_name`` and ``speedup`` were bound only in the ``verdict is None`` branch of the Muon dispatcher and read by a log line after it. Every call after the first
(the calibrated, cached path) crashed training with ``UnboundLocalError``; the single-call gate tests only took the first path and passed. ruff has no rule for it and plain mypy does not
report it (only ``--enable-error-code possibly-undefined`` does); this gate is the definite-assignment pass, with no mypy run and no third-party import.

Each function (and the module body) is walked in statement order, tracking the names bound on every path so far ("definite") and on at least one path ("maybe"). A read of a name that is
maybe-bound but not definitely bound is reported under the rule of the construct that made it conditional:

* ``possibly-unbound-after-if``: bound in one arm of an ``if``/``elif`` chain (or in the body of an ``if`` with no ``else``).
* ``possibly-unbound-after-try``: bound in a ``try`` body (or an ``else``) that a handler can skip, or only in some handlers.
* ``possibly-unbound-after-match``: bound in some ``case`` arms of a ``match`` with no irrefutable ``case``.

Not reported: a path that ends in ``raise``/``return``/``continue``/``break``/``sys.exit`` does not contribute (``if x: y = 1 else: raise`` binds ``y``); a name bound in a ``for``/``while`` body or
by a ``with`` is treated as bound afterwards (the loop is assumed to run; reporting every loop variable read after its loop is noise); a ``global``/``nonlocal`` name; a name read before any
binding at all (a loop-carried value); a nested ``def``/``lambda``/``class`` body (it runs later).

Known limit: two conditions that always agree (``if flag: a = 1`` ... ``if flag: use(a)``) are reported, because the pass does not correlate tests. Suppress with
``# unbound-ok: <reason>`` on the read line, or put the binding before the branch (``a = None``).

Usage in a consumer's meta test::

    from py_ci_shared.possibly_unbound_local import assert_possibly_unbound_local

    def test_no_possibly_unbound_local():
        assert_possibly_unbound_local("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

from ._core import Baseline, Finding, child_nodes, scan_python, walk

__all__ = [
    "MARKER",
    "RULE_IF",
    "RULE_MATCH",
    "RULE_TRY",
    "RULES",
    "assert_possibly_unbound_local",
    "find_possibly_unbound_local",
]

RULE_IF = "possibly-unbound-after-if"
RULE_TRY = "possibly-unbound-after-try"
RULE_MATCH = "possibly-unbound-after-match"
RULES = (RULE_IF, RULE_TRY, RULE_MATCH)
MARKER = "unbound-ok:"

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_EXIT_CALLS = {"exit", "_exit", "abort", "quit", "fail", "skip", "xfail"}  # sys.exit, os._exit, pytest.fail/skip/xfail/exit: none returns
_MATCH = getattr(ast, "Match", None)
_BRANCHING = tuple(t for t in (ast.If, ast.Try, getattr(ast, "TryStar", None), _MATCH) if t is not None)


@dataclass
class _State:
    """Names bound on every path (``definite``; ``None`` when the point is unreachable) and on some path (``maybe``), plus which construct made a name conditional."""

    definite: Optional[set]
    maybe: set = field(default_factory=set)
    why: dict = field(default_factory=dict)

    def fork(self) -> "_State":
        return _State(None if self.definite is None else set(self.definite), set(self.maybe), dict(self.why))

    def bind(self, names: "set[str]") -> None:
        if self.definite is not None:
            self.definite |= names
        self.maybe |= names
        for name in names:
            self.why.pop(name, None)

    def unbind(self, names: "set[str]") -> None:
        if self.definite is not None:
            self.definite -= names
        self.maybe -= names


def _merge(arms: "list[_State]", kind: str) -> _State:
    """The state after control joins from *arms*; a name that is not bound on every live arm is conditional because of *kind*."""
    live = [a for a in arms if a.definite is not None]
    if not live:
        return _State(None, set().union(*(a.maybe for a in arms)) if arms else set())
    definite = set.intersection(*(a.definite for a in live))  # type: ignore[arg-type]
    maybe = set().union(*(a.maybe for a in live))
    why: dict = {}
    for a in live:
        why.update(a.why)
    for name in maybe - definite:
        why.setdefault(name, kind)
    return _State(definite, maybe, why)


def _stored_in_comprehensions(node: ast.AST) -> "set[str]":
    return {n.id for gen in getattr(node, "generators", []) for n in walk(gen.target) if isinstance(n, ast.Name)}


def _names(node: ast.AST) -> "Iterator[tuple[str, ast.Name]]":
    """Every ``Name`` of *node* outside nested function/class scopes; comprehension variables are scoped to their comprehension."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Name):
            yield current.id, current
        for child in child_nodes(current):
            if isinstance(child, _SCOPES):
                continue
            if isinstance(child, _COMPREHENSIONS):
                hidden = _stored_in_comprehensions(child)
                stack.extend(n for n in walk(child) if isinstance(n, ast.Name) and n.id not in hidden and not isinstance(n.ctx, ast.Store))
                continue
            stack.append(child)


def _reads(node: ast.AST) -> "list[ast.Name]":
    return sorted((n for _, n in _names(node) if isinstance(n.ctx, ast.Load)), key=lambda n: (n.lineno, n.col_offset))


def _stores(node: ast.AST) -> "set[str]":
    return {name for name, n in _names(node) if isinstance(n.ctx, ast.Store)}


def _deletes(node: ast.AST) -> "set[str]":
    return {name for name, n in _names(node) if isinstance(n.ctx, ast.Del)}


def _aliases_bound(stmt: ast.stmt) -> "set[str]":
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return {(a.asname or a.name).split(".")[0] for a in stmt.names if a.name != "*"}
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    return set()


def _pattern_names(pattern: ast.AST) -> "set[str]":
    out: "set[str]" = set()
    for node in walk(pattern):
        for attr in ("name", "rest"):
            value = getattr(node, attr, None)
            if isinstance(value, str) and type(node).__name__.startswith("Match"):
                out.add(value)
    return out


def _is_exit_call(stmt: ast.stmt) -> bool:
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    func = stmt.value.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in _EXIT_CALLS


def _always_true(test: ast.expr) -> bool:
    return isinstance(test, ast.Constant) and bool(test.value)


class _Walker:
    """Definite-assignment pass over one scope's statements; ``locals`` is every name the scope binds (minus global/nonlocal)."""

    def __init__(self, rel: str, lines: "list[str]", local_names: "set[str]") -> None:
        self.rel = rel
        self.lines = lines
        self.locals = local_names
        self.findings: "list[Finding]" = []
        self._seen: "set[tuple[int, str]]" = set()

    def check(self, node: ast.AST, state: _State) -> None:
        """Report each read of a name that is bound on some path only."""
        self._check_names(_reads(node), state)

    def _check_names(self, name_nodes: "list[ast.Name]", state: _State) -> None:
        if state.definite is None:
            return
        for name_node in sorted(name_nodes, key=lambda n: (n.lineno, n.col_offset)):
            name = name_node.id
            if name in self.locals and name in state.maybe and name not in state.definite:
                self._report(name_node, state.why.get(name, RULE_IF))

    def _report(self, node: ast.Name, rule: str) -> None:
        key = (node.lineno, node.id)
        if key in self._seen or self._marked(node.lineno):
            return
        self._seen.add(key)
        self.findings.append(
            Finding(
                self.rel,
                node.lineno,
                rule,
                f"`{node.id}` is bound on only some paths to this read and raises UnboundLocalError on the others: bind it before the branch, or mark `# unbound-ok: <reason>`",
            )
        )

    def _marked(self, number: int) -> bool:
        if not 0 < number <= len(self.lines):
            return False
        line = self.lines[number - 1]
        return "#" in line and MARKER in line[line.index("#") :]

    def block(self, stmts: "list[ast.stmt]", state: _State) -> _State:
        for stmt in stmts:
            if state.definite is None:
                break
            state = self.stmt(stmt, state)
        return state

    def stmt(self, stmt: ast.stmt, state: _State) -> _State:
        handler = _DISPATCH.get(type(stmt), _Walker._simple)
        return handler(self, stmt, state)

    def _simple(self, stmt: ast.stmt, state: _State) -> _State:
        if isinstance(stmt, _SCOPES):
            state.bind(_aliases_bound(stmt))
            return state
        found = list(_names(stmt))
        self._check_names([n for _, n in found if isinstance(n.ctx, ast.Load)], state)
        state.bind({name for name, n in found if isinstance(n.ctx, ast.Store)} | _aliases_bound(stmt))
        state.unbind({name for name, n in found if isinstance(n.ctx, ast.Del)})
        if _is_exit_call(stmt):
            state.definite = None
        return state

    def _augassign(self, stmt: ast.stmt, state: _State) -> _State:
        self.check(stmt, state)
        target = stmt.target  # type: ignore[attr-defined]
        if isinstance(target, ast.Name) and target.id in self.locals and target.id in state.maybe and target.id not in state.definite:  # type: ignore[operator]
            self._report(target, state.why.get(target.id, RULE_IF))
        state.bind(_stores(stmt))
        return state

    def _dead(self, stmt: ast.stmt, state: _State) -> _State:
        self.check(stmt, state)
        state.definite = None
        return state

    def _if(self, stmt: ast.If, state: _State) -> _State:
        self.check(stmt.test, state)
        state.bind(_stores(stmt.test))
        body = self.block(stmt.body, state.fork())
        orelse = self.block(stmt.orelse, state.fork())
        return _merge([body, orelse], RULE_IF)

    def _loop(self, stmt: ast.stmt, state: _State) -> _State:
        head = stmt.iter if isinstance(stmt, (ast.For, ast.AsyncFor)) else stmt.test  # type: ignore[attr-defined]
        self.check(head, state)
        inner = state.fork()
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            inner.bind(_stores(stmt.target))
        else:
            inner.bind(_stores(head))
        self.block(stmt.body, inner)  # type: ignore[attr-defined]
        after = state.fork()
        every = _all_stores(stmt.body)  # type: ignore[attr-defined]
        after.bind(every | (_stores(stmt.target) if isinstance(stmt, (ast.For, ast.AsyncFor)) else set()))
        after = self.block(stmt.orelse, after)  # type: ignore[attr-defined]
        if isinstance(stmt, ast.While) and _always_true(stmt.test) and not _has_break(stmt):
            after.definite = None
        return after

    def _with(self, stmt: ast.stmt, state: _State) -> _State:
        for item in stmt.items:  # type: ignore[attr-defined]
            self.check(item.context_expr, state)
            if item.optional_vars is not None:
                state.bind(_stores(item.optional_vars))
        return self.block(stmt.body, state)  # type: ignore[attr-defined]

    def _try(self, stmt: ast.Try, state: _State) -> _State:
        every = _all_stores(stmt.body)
        body = self.block(stmt.body, state.fork())
        arms = [self.block(stmt.orelse, body.fork())]
        for handler in stmt.handlers:
            entry = state.fork()
            entry.maybe |= every
            if handler.type is not None:
                self.check(handler.type, entry)
            if handler.name:
                entry.bind({handler.name})
            end = self.block(handler.body, entry)
            if handler.name:
                end.unbind({handler.name})
            arms.append(end)
        merged = _merge(arms, RULE_TRY)
        if stmt.finalbody:
            entry = state.fork()
            entry.maybe |= every | merged.maybe
            self.block(stmt.finalbody, entry)
            if merged.definite is not None:
                merged = self.block(stmt.finalbody, merged)
        return merged

    def _match(self, stmt: ast.stmt, state: _State) -> _State:
        self.check(stmt.subject, state)  # type: ignore[attr-defined]
        arms = []
        irrefutable = False
        for case in stmt.cases:  # type: ignore[attr-defined]
            entry = state.fork()
            entry.bind(_pattern_names(case.pattern))
            if case.guard is not None:
                self.check(case.guard, entry)
            arms.append(self.block(case.body, entry))
            irrefutable = irrefutable or (case.guard is None and isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None)
        if not irrefutable:
            arms.append(state.fork())
        return _merge(arms, RULE_MATCH)


_DISPATCH: "dict[type, Callable[..., _State]]" = {
    ast.If: _Walker._if,
    ast.For: _Walker._loop,
    ast.AsyncFor: _Walker._loop,
    ast.While: _Walker._loop,
    ast.Try: _Walker._try,
    ast.With: _Walker._with,
    ast.AsyncWith: _Walker._with,
    ast.Return: _Walker._dead,
    ast.Raise: _Walker._dead,
    ast.Break: _Walker._dead,
    ast.Continue: _Walker._dead,
    ast.AugAssign: _Walker._augassign,
}
for _extra, _method in ((_MATCH, _Walker._match), (getattr(ast, "TryStar", None), _Walker._try)):
    if _extra is not None:
        _DISPATCH[_extra] = _method


def _nested_statements(stmts: "list[ast.stmt]") -> "Iterator[ast.stmt]":
    """Every statement of *stmts*, nested ones included, without entering nested function/class bodies."""
    stack = list(stmts)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(c for c in child_nodes(node) if isinstance(c, ast.stmt))
            stack.extend(h for h in getattr(node, "handlers", []))
            stack.extend(s for case in getattr(node, "cases", []) for s in case.body)


def _all_stores(stmts: "list[ast.stmt]") -> "set[str]":
    """Every name bound anywhere in *stmts* (nested blocks included, nested scopes excluded): assignments, loop and ``with`` targets, imports, definitions, match captures."""
    out: "set[str]" = set()
    for s in _nested_statements(stmts):
        if isinstance(s, ast.ExceptHandler):
            continue
        out |= _aliases_bound(s)
        for child in child_nodes(s):
            if not isinstance(child, (ast.stmt, ast.ExceptHandler)) and not isinstance(child, _SCOPES):
                out |= _stores(child)
        out |= _pattern_names_of(s)
    return out


def _pattern_names_of(stmt: ast.stmt) -> "set[str]":
    return {n for case in getattr(stmt, "cases", []) for n in _pattern_names(case.pattern)}


def _has_break(loop: ast.stmt) -> bool:
    stack = list(loop.body)  # type: ignore[attr-defined]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Break):
            return True
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            stack.extend(node.orelse)
            continue
        stack.extend(c for c in child_nodes(node) if isinstance(c, ast.stmt))
    return False


def _declared_nonlocal(body: "list[ast.stmt]") -> "set[str]":
    out: "set[str]" = set()
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            out.update(node.names)
        stack.extend(c for c in child_nodes(node) if isinstance(c, ast.stmt))
    return out


def _scope_locals(body: "list[ast.stmt]", params: "set[str]") -> "set[str]":
    names = set(params) | _all_stores(body)
    for stmt in _nested_statements(body):
        if isinstance(stmt, ast.ExceptHandler) and stmt.name:
            names.add(stmt.name)
    return names - _declared_nonlocal(body)


def _params(fn: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> "set[str]":
    args = fn.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs, *([args.vararg] if args.vararg else []), *([args.kwarg] if args.kwarg else [])]
    return {a.arg for a in every}


def _scope_findings(rel: str, lines: "list[str]", body: "list[ast.stmt]", params: "set[str]") -> "list[Finding]":
    if not any(isinstance(s, _BRANCHING) for s in _nested_statements(body)):
        return []  # no conditional binding without a branch construct
    walker = _Walker(rel, lines, _scope_locals(body, params))
    walker.block(body, _State(set(params), set(params)))
    return walker.findings


def _findings_in(tree: ast.Module, rel: str, lines: "list[str]") -> "list[Finding]":
    out = _scope_findings(rel, lines, tree.body, set())
    for node in walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.extend(_scope_findings(rel, lines, node.body, _params(node)))
    return out


def find_possibly_unbound_local(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every read of a name bound on only some paths under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read
    the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_possibly_unbound_local(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any possibly-unbound read, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_possibly_unbound_local(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "bind the name before the branch (`name = None`) or mark the read `# unbound-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="possibly_unbound_local", refresh_command="PY_CI_SHARED_REFRESH=possibly_unbound_local")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} possibly-unbound local read(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
