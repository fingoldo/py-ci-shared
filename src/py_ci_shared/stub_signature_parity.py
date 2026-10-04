"""A stub that replaces a callable in a test must accept every parameter of the callable it replaces.

``monkeypatch.setattr(data, "submit_approved", _submit)`` keeps passing after the real ``submit_approved`` gains a
keyword: the stub is never called with it until the code under test forwards it, and then the call raises a
``TypeError`` that production code catches and renders as "Unexpected error". The test is green and proves nothing
about the path it was written for. Dashboard cases this was built on (all fixed): six
``async def _submit(conn, submission_id, request, provider=None)`` stubs of ``data.submit_approved`` after it gained
``expected_dry_run``; ``lambda *_a: ["t1"]`` for ``store.missing_detail_transaction_ids``, whose caller passes
``limit=`` by keyword; ``lambda job_uid, fl_cid: "questions"`` for ``_confirm_dialog._questions_line`` after it gained
``saved_answers``.

The rule (:func:`missing_parameters`), per parameter of the REAL callable:

* positional-only: the stub has a positional slot at that index, or ``*args``;
* positional-or-keyword WITHOUT a default: a positional slot at that index (any name: callers pass required
  arguments positionally), ``*args``, or a parameter of the same name;
* positional-or-keyword WITH a default, and keyword-only: the same name, or ``**kwargs`` (callers pass these by
  keyword, which is exactly what ``*args`` cannot take);
* the real ``*args`` / ``**kwargs`` are not required of the stub: they name no parameter, and a real
  ``ensure_configured(**connect_kwargs)`` called bare everywhere would make every ``lambda: None`` a finding.

Two halves share that rule:

* :mod:`py_ci_shared.stub_signature_guard`, an opt-in pytest plugin (``stub_signature_guard = true`` in
  ``[tool.py_ci_shared]``, or ``-p py_ci_shared.stub_signature_guard``): it checks every ``monkeypatch.setattr`` at
  run time against the live object, so it sees through re-exports, decorators and instances;
* this module's static scan (:func:`find_stub_signature_parity`): ``<monkeypatch>.setattr(module_or_class, "name",
  stub)``, ``<monkeypatch>.setattr("pkg.mod.name", stub)``, ``patch("pkg.mod.name", stub_or_new=...)`` and
  ``patch.object(module_or_class, "name", ...)`` (also ``side_effect=``) where the stub is a lambda or a ``def``
  in the same file and the target resolves to a ``def`` in the scanned tree (following ``from x import name``
  re-exports). Anything it cannot resolve (an instance, a builtin, a third-party module) is skipped; the plugin
  covers those.

Relation to :mod:`py_ci_shared.spec_bound_doubles`: that gate makes a ``Mock`` reaching duck-typed code carry a
spec, so it cannot invent ATTRIBUTES the real object lacks. This one is the other direction for hand-written
FUNCTION doubles: the stub must not refuse ARGUMENTS the real callable takes. A ``create_autospec`` mock satisfies
both; a ``Mock`` without a spec accepts any arguments and is not this gate's subject.

Usage in a consumer's meta test::

    from py_ci_shared.stub_signature_parity import assert_stub_signature_parity

    def test_stubs_accept_what_the_real_callables_take():
        assert_stub_signature_parity(".", baseline_path="tests/baselines/stub_signature_parity.json")
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = [
    "DEFAULT_PATCHER_NAMES",
    "RULE",
    "Param",
    "assert_stub_signature_parity",
    "describe",
    "find_stub_signature_parity",
    "missing_parameters",
    "params_of_callable",
    "params_of_node",
]

RULE = "stub-signature-parity"
#: Receiver names whose ``.setattr(...)`` is a monkeypatch (``with pytest.MonkeyPatch.context() as mp``).
DEFAULT_PATCHER_NAMES = frozenset({"monkeypatch", "mp", "m"})
POSONLY, POK, VARARG, KWONLY, VARKW = "posonly", "pok", "vararg", "kwonly", "varkw"
_SKIPPED_DECORATORS = frozenset({"staticmethod", "classmethod", "property", "cached_property", "overload"})


@dataclass(frozen=True)
class Param:
    """One parameter: its name, kind (``posonly``/``pok``/``vararg``/``kwonly``/``varkw``) and whether it has a default."""

    name: str
    kind: str
    has_default: bool = False


_INSPECT_KINDS = {
    inspect.Parameter.POSITIONAL_ONLY: POSONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD: POK,
    inspect.Parameter.VAR_POSITIONAL: VARARG,
    inspect.Parameter.KEYWORD_ONLY: KWONLY,
    inspect.Parameter.VAR_KEYWORD: VARKW,
}


def params_of_callable(fn: Any) -> Optional[tuple[Param, ...]]:
    """The parameters of a live callable, or None when it has no inspectable signature (some builtins)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    return tuple(Param(p.name, _INSPECT_KINDS[p.kind], p.default is not p.empty) for p in sig.parameters.values())


def params_of_node(node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda]) -> tuple[Param, ...]:
    """The parameters of a ``def`` or ``lambda`` as written."""
    a = node.args
    positional = list(a.posonlyargs) + list(a.args)
    first_default = len(positional) - len(a.defaults)
    out = [Param(arg.arg, POSONLY if i < len(a.posonlyargs) else POK, i >= first_default) for i, arg in enumerate(positional)]
    if a.vararg is not None:
        out.append(Param(a.vararg.arg, VARARG))
    out += [Param(arg.arg, KWONLY, default is not None) for arg, default in zip(a.kwonlyargs, a.kw_defaults)]
    if a.kwarg is not None:
        out.append(Param(a.kwarg.arg, VARKW))
    return tuple(out)


def missing_parameters(real: Sequence[Param], stub: Sequence[Param]) -> list[str]:
    """Names of the real callable's parameters the stub cannot accept (the module docstring has the rule)."""
    stub_kinds = {p.kind for p in stub}
    stub_positional = [p for p in stub if p.kind in (POSONLY, POK)]
    stub_names = {p.name for p in stub if p.kind in (POK, KWONLY)}
    missing: list[str] = []
    index = 0
    for p in real:
        if p.kind in (POSONLY, POK):
            by_position = VARARG in stub_kinds or index < len(stub_positional)
            by_name = VARKW in stub_kinds or p.name in stub_names
            index += 1
            ok = by_position if p.kind == POSONLY else (by_name if p.has_default else by_position or p.name in stub_names)
        elif p.kind == KWONLY:
            ok = VARKW in stub_kinds or p.name in stub_names
        else:
            continue  # a variadic of the real one names no parameter (module docstring)
        if not ok:
            missing.append(p.name)
    return missing


def describe(params: Sequence[Param]) -> str:
    """``(a, b=..., *, c)`` for a message."""
    parts: list[str] = []
    star_done = False
    for i, p in enumerate(params):
        if p.kind == KWONLY and not star_done:
            parts.append("*")
        if p.kind in (VARARG, KWONLY, VARKW):
            star_done = True
        prefix = {VARARG: "*", VARKW: "**"}.get(p.kind, "")
        parts.append(prefix + p.name + ("=..." if p.has_default else ""))
        if p.kind == POSONLY and (i + 1 == len(params) or params[i + 1].kind != POSONLY):
            parts.append("/")
    return "(" + ", ".join(parts) + ")"


# ---- static scan ---------------------------------------------------------------------------------------------------


@dataclass
class _Module:
    rel: str
    dotted: str
    tree: ast.Module
    is_package: bool


def _dotted(rel: str) -> tuple[str, bool]:
    parts = rel[:-3].split("/") if rel.endswith(".py") else rel.split("/")
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


class _Index:
    """Module dotted name -> parsed file. A dotted name of two or more parts also matches a unique file whose dotted path
    ends with it (``src/pkg/m.py`` is ``pkg.m``); one part (``time``) only matches exactly, so a local ``utils/time.py``
    is never taken for the standard library. Scanned from inside a package, ``<root>.db`` is the root's ``db.py``."""

    def __init__(self, modules: Iterable[_Module], root_name: str = "") -> None:
        self.root_name = root_name
        self.by_suffix: dict[str, list[_Module]] = {}
        for m in modules:
            parts = m.dotted.split(".")
            for i in range(len(parts)):
                self.by_suffix.setdefault(".".join(parts[i:]), []).append(m)

    def _lookup(self, dotted: str) -> Optional[_Module]:
        hits = self.by_suffix.get(dotted, [])
        exact = [m for m in hits if m.dotted == dotted]
        if len(exact) == 1:
            return exact[0]
        return hits[0] if len(hits) == 1 and not exact and "." in dotted else None

    def module(self, dotted: str) -> Optional[_Module]:
        found = self._lookup(dotted)
        head, _, rest = dotted.partition(".")
        if found is None and rest and head == self.root_name:
            found = self._lookup(rest)
        return found

    def resolve(self, dotted: str, depth: int = 0) -> Optional[tuple[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]]]:
        """The ``def`` that *dotted* (``pkg.mod.fn`` or ``pkg.mod.Cls.meth``) names, with its own dotted name."""
        if depth > 4 or "." not in dotted:
            return None
        head, name = dotted.rsplit(".", 1)
        mod = self.module(head)
        if mod is not None:
            return self._in_module(mod, name, depth)
        if "." in head:
            mod_name, cls_name = head.rsplit(".", 1)
            mod = self.module(mod_name)
            if mod is not None:
                for node in mod.tree.body:
                    if isinstance(node, ast.ClassDef) and node.name == cls_name:
                        fn = _def_in(node.body, name)
                        return (f"{mod.dotted}.{cls_name}.{name}", fn) if fn is not None and not _skipped(fn) else None
        return None

    def _in_module(self, mod: _Module, name: str, depth: int) -> Optional[tuple[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]]]:
        fn = _def_in(mod.tree.body, name)
        if fn is not None:
            return (f"{mod.dotted}.{name}", fn) if not _skipped(fn) else None
        package = mod.dotted if mod.is_package else mod.dotted.rpartition(".")[0]
        target = ImportAliases.from_tree(mod.tree, package=package or None).mapping.get(name)
        return self.resolve(target, depth + 1) if target and not target.startswith(".") else None


def _def_in(body: Iterable[ast.stmt], name: str) -> Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]]:
    found = None
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            found = node  # the last definition wins, as at import
    return found


def _skipped(fn: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        label = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if label in _SKIPPED_DECORATORS or label in ("setter", "getter", "deleter"):
            return True
    return False


def _target_dotted(node: ast.expr, aliases: ImportAliases) -> Optional[str]:
    """The dotted name of an imported module or class used as a patch target; None for a local object."""
    root = node
    while isinstance(root, ast.Attribute):
        root = root.value
    if not isinstance(root, ast.Name) or root.id not in aliases.mapping:
        return None
    return aliases.qualified_name(node)


def _str(node: Optional[ast.expr]) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _kw(call: ast.Call, name: str) -> Optional[ast.expr]:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _patch_sites(call: ast.Call, aliases: ImportAliases, patchers: frozenset[str]) -> list[tuple[str, ast.expr]]:
    """``(dotted target, stub expression)`` for a monkeypatch/``mock.patch`` call that installs a callable."""
    func = call.func
    out: list[tuple[str, ast.expr]] = []
    if isinstance(func, ast.Attribute) and func.attr == "setattr" and isinstance(func.value, ast.Name) and func.value.id in patchers:
        args = call.args
        if len(args) >= 3 and _str(args[1]) is not None:
            base = _target_dotted(args[0], aliases)
            if base is not None:
                out.append((f"{base}.{_str(args[1])}", args[2]))
        elif len(args) == 2 and _str(args[0]) is not None and "." in str(_str(args[0])):
            out.append((str(_str(args[0])), args[1]))
        return out
    qualified = aliases.qualified_name(call) or ""
    if qualified.endswith("patch.object") or qualified == "patch.object":
        if len(call.args) >= 2 and _str(call.args[1]) is not None:
            base = _target_dotted(call.args[0], aliases)
            if base is not None:
                dotted = f"{base}.{_str(call.args[1])}"
                stub = call.args[2] if len(call.args) >= 3 else _kw(call, "new")
                out += [(dotted, s) for s in (stub, _kw(call, "side_effect")) if s is not None]
    elif qualified.rsplit(".", 1)[-1] == "patch" and (qualified == "patch" or "mock" in qualified):
        dotted_s = _str(call.args[0]) if call.args else _str(_kw(call, "target"))
        if dotted_s and "." in dotted_s:
            stub = call.args[1] if len(call.args) >= 2 else _kw(call, "new")
            out += [(dotted_s, s) for s in (stub, _kw(call, "side_effect")) if s is not None]
    return out


_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _stub_node(expr: ast.expr, chain: list[ast.AST], tree: ast.Module) -> Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda]]:
    """The lambda, or the ``def`` a name refers to in the nearest enclosing scope (then the module)."""
    if isinstance(expr, ast.Lambda):
        return expr
    if not isinstance(expr, ast.Name):
        return None
    for scope in [*reversed([n for n in chain if isinstance(n, _SCOPES)]), tree]:
        body = getattr(scope, "body", [])
        fn = _def_in(_flatten(body), expr.id)
        if fn is not None:
            return fn
        lam = _lambda_bound(_flatten(body), expr.id)
        if lam is not None:
            return lam
    return None


def _flatten(body: Iterable[ast.stmt]) -> list[ast.stmt]:
    """Statements of a scope including those nested in if/with/try/for blocks (not nested scopes)."""
    out: list[ast.stmt] = []
    stack = list(body)
    while stack:
        node = stack.pop(0)
        out.append(node)
        if not isinstance(node, _SCOPES):
            for field in ("body", "orelse", "finalbody", "handlers"):
                stack.extend(getattr(node, field, []) or [])
    return out


def _lambda_bound(body: Iterable[ast.stmt], name: str) -> Optional[ast.Lambda]:
    found = None
    for node in body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            found = node.value
    return found


def _calls_with_chain(tree: ast.Module) -> Iterable[tuple[ast.Call, list[ast.AST]]]:
    stack: list[tuple[ast.AST, list[ast.AST]]] = [(tree, [])]
    while stack:
        node, chain = stack.pop()
        if isinstance(node, ast.Call):
            yield node, chain
        inner = [*chain, node] if isinstance(node, _SCOPES) else chain
        stack.extend((child, inner) for child in ast.iter_child_nodes(node))


def _findings_in(tree: ast.Module, rel: str, index: _Index, patchers: frozenset[str]) -> list[Finding]:
    aliases = ImportAliases.from_tree(tree)
    out: list[Finding] = []
    for call, chain in _calls_with_chain(tree):
        for dotted, stub_expr in _patch_sites(call, aliases, patchers):
            stub = _stub_node(stub_expr, chain, tree)
            if stub is None:
                continue
            resolved = index.resolve(dotted)
            if resolved is None:
                continue
            real_name, real = resolved
            missing = missing_parameters(params_of_node(real), params_of_node(stub))
            if not missing:
                continue
            where = next((n.name for n in reversed(chain) if isinstance(n, _FUNCS)), "<module>")
            label = "lambda" if isinstance(stub, ast.Lambda) else stub.name
            out.append(
                Finding(
                    rel,
                    call.lineno,
                    RULE,
                    f"{where}: stub {label}{describe(params_of_node(stub))} replaces {real_name}{describe(params_of_node(real))} "
                    f"but cannot accept {', '.join(missing)}",
                )
            )
    return out


def find_stub_signature_parity(
    root: Union[str, Path],
    *,
    patcher_names: Iterable[str] = DEFAULT_PATCHER_NAMES,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every stub under *root* that cannot accept a parameter of the ``def`` it replaces, sorted by path and line.

    The whole tree is scanned so a test's target resolves to its ``def``. Raises ``EmptyScanError`` when fewer than
    *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless
    *allow_unparsed*): a gate that cannot read the code must not pass it."""
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    parsed = list(scan)
    modules = []
    for p in parsed:
        dotted, is_package = _dotted(p.rel)
        modules.append(_Module(p.rel, dotted, p.tree, is_package))
    index = _Index(modules, Path(root).resolve().name)
    patchers = frozenset(patcher_names)
    out: list[Finding] = []
    for p in parsed:
        out.extend(_findings_in(p.tree, p.rel, index, patchers))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_stub_signature_parity(
    root: Union[str, Path],
    *,
    patcher_names: Iterable[str] = DEFAULT_PATCHER_NAMES,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any stub that cannot accept a parameter of what it replaces, or with *baseline_path* on any finding the
    baseline does not accept."""
    found = find_stub_signature_parity(root, patcher_names=patcher_names, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "give the stub the missing parameters (or **kwargs), so a new argument of the real callable reaches the test as a failure"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="stub_signature_parity", refresh_command="PY_CI_SHARED_REFRESH=stub_signature_parity")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} stub-signature-parity finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
