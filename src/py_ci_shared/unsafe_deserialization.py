"""Deserialization that can execute attacker-chosen code, or that trusts bytes nothing verified.

``pickle`` runs arbitrary callables named in the stream; every hardening is a statement about WHICH callables may be
named, and each is easy to get subtly wrong. Five shapes are reported:

* a restricted ``Unpickler`` (``pickle``/``dill``/``cloudpickle``) whose ``find_class`` allows whole MODULES. The
  usual hardening is ``if module.startswith("sklearn."): return getattr(importlib.import_module(module), name)``, and
  that admits every callable the module exposes or imports: ``sklearn.externals`` re-exports, ``numpy.testing``
  helpers that call ``eval``, ``joblib.parallel`` entry points that spawn processes. A per-name allowlist
  (``(module, name) in ALLOWED``) is the safe form. The same check reports a ``find_class`` that admits a gadget module
  (``builtins``, ``types``, ``functools``, ``os``, ``subprocess``, ``importlib``, ``sys``, ``operator``, ...): with
  ``builtins.eval`` or ``functools.partial`` reachable, the pickle is arbitrary code execution however long the rest of
  the allowlist is.
* ``torch.load(...)`` without ``weights_only=True``: it unpickles, so a downloaded checkpoint is code.
* ``np.load(..., allow_pickle=True)``: the ``.npy`` object-array path is a pickle.
* ``pickle.load``/``loads``, ``dill``, ``cloudpickle`` and ``joblib.load`` of a path or bytes that no hash or signature
  check precedes in the same function (an earlier call whose name contains ``sha256``, ``hmac`` or ``verify``). This
  one is advisory-grade by construction: a hash computed elsewhere, or a trusted local cache, is invisible to the
  parse, so the finding is a prompt to look, and a verified-by-construction site is marked
  ``# deserialize-ok: <reason>``. Methods of a restricted-``Unpickler`` subclass are exempt (they ARE the safe loader),
  and so is a load preceded in the same function by a ``dump``/``dumps``/``save`` call: the bytes were produced here
  (a round-trip test, a cache written and read back), not received.
* ``yaml.load`` without a safe Loader (``SafeLoader``/``CSafeLoader``/``BaseLoader``), and ``yaml.unsafe_load``: a
  ``!!python/object/apply:os.system`` document runs a shell command.

``# deserialize-ok: <reason>`` on the statement (or the ``class``/``def find_class`` line) suppresses a finding.

Usage in a consumer's meta test::

    from py_ci_shared.unsafe_deserialization import assert_unsafe_deserialization

    def test_no_unsafe_deserialization():
        assert_unsafe_deserialization("src")
"""

from __future__ import annotations

import ast
from ._core.unparse import unparse_stable
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, child_nodes, qualified_functions, scan_python
from ._core import walk as shared_walk
from ._core.node_index import tree_memo
from ._core.scopes import iter_scopes

logger = logging.getLogger(__name__)

__all__ = ["RULE", "assert_unsafe_deserialization", "find_unsafe_deserialization"]

RULE = "unsafe-deserialization"
SUPPRESSION = "# deserialize-ok:"

_UNPICKLERS = frozenset(
    {
        "pickle.Unpickler",
        "_pickle.Unpickler",
        "dill.Unpickler",
        "dill._dill.Unpickler",
        "cloudpickle.Unpickler",
        "cloudpickle.cloudpickle.Unpickler",
        "pickle._Unpickler",
        "cPickle.Unpickler",
    }
)
_PICKLE_LOADS = frozenset(
    {
        "pickle.load",
        "pickle.loads",
        "_pickle.load",
        "_pickle.loads",
        "cPickle.load",
        "cPickle.loads",
        "dill.load",
        "dill.loads",
        "cloudpickle.load",
        "cloudpickle.loads",
        "joblib.load",
        "pandas.read_pickle",
        "pandas.io.pickle.read_pickle",
        "marshal.load",
        "marshal.loads",
        "shelve.open",
        "jsonpickle.decode",
        "jsonpickle.unpickler.decode",
    }
)
#: ``pickle.Unpickler(fh).load()``: the load is a method of an Unpickler instance built in the same expression.
_UNPICKLER_CTORS = frozenset({"pickle.Unpickler", "pickle._Unpickler", "_pickle.Unpickler", "cPickle.Unpickler", "dill.Unpickler", "cloudpickle.Unpickler"})
_KERAS_LOADERS = frozenset({"keras.models.load_model", "keras.saving.load_model", "tensorflow.keras.models.load_model", "tf.keras.models.load_model"})
_GADGET_MODULES = frozenset(
    {"builtins", "__builtin__", "types", "functools", "os", "posix", "nt", "subprocess", "importlib", "sys", "operator", "runpy", "shutil", "pty", "socket"}
)
_DANGEROUS_NAMES = frozenset(
    {
        "eval", "exec", "compile", "open", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars", "input", "breakpoint",
        "system", "popen", "run", "call", "check_call", "check_output", "Popen", "spawn", "execv", "execl", "fork", "remove", "rmtree", "walk",
        "partial", "partialmethod", "reduce", "import_module", "reload", "modules", "exit", "attrgetter", "methodcaller", "call_method",
    }
)  # fmt: skip
_SAFE_YAML = frozenset({"SafeLoader", "CSafeLoader", "BaseLoader", "CBaseLoader"})
# A dump/dumps/save earlier in the same function means the bytes were produced here (a round-trip), not received.
_PRODUCERS = frozenset({"dump", "dumps", "save", "to_pickle"})
_VERIFY = re.compile(r"sha256|hmac|verif|sidecar", re.IGNORECASE)  # verify, verified, verification; a sidecar-checked read
_DENY_NAME = re.compile(r"deny|block|forbid|unsafe|banned|reject|disallow", re.IGNORECASE)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _suppressed(lines: list[str], node: ast.AST) -> bool:
    lo = getattr(node, "lineno", 0)
    hi = getattr(node, "end_lineno", None) or lo
    return any(SUPPRESSION in lines[i] for i in range(max(lo - 1, 0), min(hi, len(lines))))


def _kw(call: ast.Call, name: str) -> Optional[ast.expr]:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in shared_walk(node))


def _module_constants(tree: Union[ast.Module, ast.ClassDef]) -> dict[str, ast.AST]:
    """Names bound by a plain assignment in the body of a module or class."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            out[node.target.id] = node.value
    return out


def _raises_in_body(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """Is *node* inside the test of an ``if`` whose body raises (a deny-list, not an allow-list)?"""
    cur: Optional[ast.AST] = node
    while cur is not None:
        parent = parents.get(id(cur))
        if isinstance(parent, ast.If) and cur is parent.test:
            return any(isinstance(s, ast.Raise) for s in parent.body)
        cur = parent
    return False


def _is_resolver_call(node: ast.AST) -> bool:
    """A ``getattr`` / ``__import__`` / ``import_module`` / ``find_class`` call, which resolves a name rather than testing it."""
    return isinstance(node, ast.Call) and _call_name(node) in ("getattr", "__import__", "import_module", "find_class")


def _module_test_kind(node: ast.AST, name: str) -> str:
    """How one comparison or call that mentions the module parameter constrains it: ``"per_name"``, ``"module_only"`` or ``""``."""
    if _mentions(node, name):
        return "per_name"
    if isinstance(node, ast.Compare) or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("startswith", "endswith")):
        return "module_only"
    return ""


def _resolves_module(node: ast.AST, module: str) -> bool:
    """Does the call resolve the module parameter (``getattr(module, ...)`` or ``super().find_class(module, ...)``)?"""
    if not isinstance(node, ast.Call):
        return False
    callee = node.func
    if isinstance(callee, ast.Name) and callee.id == "getattr" and node.args and _mentions(node.args[0], module):
        return True
    return isinstance(callee, ast.Attribute) and callee.attr == "find_class" and any(_mentions(a, module) for a in node.args)


def _admits_whole_modules(func: ast.FunctionDef, module: str, name: str) -> bool:
    """True when ``find_class`` tests or resolves the module without any per-name allowlist."""
    module_only = False
    per_name = False
    resolves = False
    for node in shared_walk(func):
        if isinstance(node, (ast.Compare, ast.Call)) and _mentions(node, module):
            if _is_resolver_call(node):
                continue  # a resolver call that mentions the module is neither a test nor counted as resolving
            kind = _module_test_kind(node, name)
            per_name = per_name or kind == "per_name"
            module_only = module_only or kind == "module_only"
        elif isinstance(node, ast.Compare) and _mentions(node, name):
            per_name = True
        resolves = resolves or _resolves_module(node, module)
    return (module_only or resolves) and not per_name


def _gadget_pool(node: ast.AST, consts: dict[str, ast.AST], parents: dict[int, ast.AST], paired: set[int]) -> list[ast.AST]:
    """The container to search for gadget strings at *node*: the literal itself, or the module constant it names."""
    if isinstance(node, (ast.Constant, ast.Tuple)):
        return [] if _raises_in_body(node, parents) or id(node) in paired else [node]
    ref = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
    if ref in consts and not _DENY_NAME.search(ref) and not _raises_in_body(node, parents):
        return [consts[ref]]
    return []


def _find_class_problems(func: ast.FunctionDef, consts: dict[str, ast.AST]) -> list[str]:
    """What is wrong with one ``find_class`` body, as short messages."""
    params = [a.arg for a in func.args.args]
    if len(params) < 3:
        return []
    module, name = params[1], params[2]
    parents = {id(c): p for p in shared_walk(func) for c in child_nodes(p)}
    problems: list[str] = []
    if _admits_whole_modules(func, module, name):
        problems.append("find_class admits whole modules (a test on the module alone, no per-name allowlist)")
    seen_gadget: set[str] = set()
    paired = _paired_ids(func)
    for node in shared_walk(func):
        for container in _gadget_pool(node, consts, parents, paired):
            for text in _gadget_entries(container):
                if text not in seen_gadget:
                    seen_gadget.add(text)
                    problems.append(f"find_class admits gadget module {text.split('.', 1)[0]!r} ({text!r})")
    return problems


def _is_pair(node: ast.AST) -> bool:
    return isinstance(node, ast.Tuple) and len(node.elts) == 2 and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts)


def _paired_ids(root: ast.AST) -> set[int]:
    """Ids of the string constants that sit in a ``("module", "name")`` pair, judged as a pair rather than alone."""
    return {id(e) for n in shared_walk(root) if _is_pair(n) for e in n.elts}  # type: ignore[attr-defined]


def _gadget_entries(container: ast.AST) -> list[str]:
    """Strings under *container* that admit a gadget module: the module alone (``"builtins"``), or ``("os", "system")`` /
    ``"builtins.eval"`` naming a dangerous callable. A harmless pair such as ``("builtins", "set")`` is the safe pattern."""
    found: list[str] = []
    paired = _paired_ids(container)
    for node in shared_walk(container):
        if _is_pair(node):
            module, name = (e.value for e in node.elts)  # type: ignore[attr-defined]
            if module in _GADGET_MODULES and name in _DANGEROUS_NAMES:
                found.append(f"{module}.{name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in paired:
            head, _, tail = node.value.partition(".")
            if head in _GADGET_MODULES and (not tail or tail in _DANGEROUS_NAMES):
                found.append(node.value)
    return found


def _is_unpickler_class(node: ast.ClassDef, aliases: ImportAliases) -> bool:
    for base in node.bases:
        qualified = aliases.qualified_name(base)
        if qualified in _UNPICKLERS or (
            qualified is not None
            and qualified.rsplit(".", 1)[-1] == "Unpickler"
            and qualified.split(".", 1)[0] in ("pickle", "dill", "cloudpickle", "_pickle", "cPickle")
        ):
            return True
    return False


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _call_label(node: ast.Call) -> str:
    try:
        return unparse_stable(node.func)
    except (
        Exception
    ) as exc:  # pragma: no cover - ast.unparse handles every parsed node  # best-effort: the rendering is a label for the message; the call is judged on its own nodes
        logger.warning("substituted a fallback for %s: %s", type(exc).__name__, exc)
        logger.debug("could not unparse call target: %s", type(exc).__name__)
        return "<call>"


class _Sink:
    """Collects the findings of one file, dropping those whose node (or anchor) carries the suppression comment."""

    def __init__(self, rel: str, lines: list[str]) -> None:
        self.rel = rel
        self.lines = lines
        self.out: list[Finding] = []

    def report(self, node: ast.AST, message: str, anchors: Iterable[ast.AST] = ()) -> None:
        if _suppressed(self.lines, node) or any(_suppressed(self.lines, a) for a in anchors):
            return
        self.out.append(Finding(self.rel, getattr(node, "lineno", 0), RULE, message))


def _check_unpicklers(tree: ast.Module, aliases: ImportAliases, consts: dict[str, ast.AST], sink: _Sink) -> set[int]:
    """(a) Restricted unpicklers: report their ``find_class`` problems; return the ids of the functions that make up a safe loader."""
    safe_loader_functions: set[int] = set()
    for node in shared_walk(tree):
        if not (isinstance(node, ast.ClassDef) and _is_unpickler_class(node, aliases)):
            continue
        safe_loader_functions.update(id(member) for member in shared_walk(node) if isinstance(member, _FUNCTIONS))
        if not any(isinstance(item, ast.FunctionDef) and item.name == "find_class" for item in node.body):
            sink.report(node, f"{node.name}: an Unpickler subclass without find_class keeps the default, unrestricted class lookup (full gadget surface)")
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "find_class":
                for problem in _find_class_problems(item, {**consts, **_module_constants(node)}):
                    sink.report(item, f"{node.name}: {problem}", anchors=(node,))
    return safe_loader_functions


def _yaml_message(node: ast.Call, qualified: str, aliases: ImportAliases) -> Optional[str]:
    """The message for a ``yaml.load``-family call without a safe Loader, else None."""
    loader = _kw(node, "Loader") or (node.args[1] if len(node.args) > 1 else None)
    loader_name = (aliases.qualified_name(loader) or "").rsplit(".", 1)[-1] if loader is not None else ""
    if qualified.endswith(("unsafe_load", "unsafe_load_all", "full_load", "full_load_all")) or loader_name not in _SAFE_YAML:
        return f"{qualified} without a safe Loader can construct arbitrary Python objects; use yaml.safe_load"
    return None


def _flag_is(node: ast.Call, name: str, value: object) -> bool:
    flag = _kw(node, name)
    return isinstance(flag, ast.Constant) and flag.value is value


def _torch_message(node: ast.Call) -> Optional[str]:
    if not _flag_is(node, "weights_only", True):
        return "torch.load without weights_only=True unpickles the checkpoint, which executes code"
    return None


def _numpy_message(node: ast.Call) -> Optional[str]:
    flag = _kw(node, "allow_pickle")
    if isinstance(flag, ast.Constant) and flag.value is True:
        return "np.load(allow_pickle=True) unpickles object arrays, which executes code"
    if flag is not None and not isinstance(flag, ast.Constant):
        return "np.load(allow_pickle=<variable>) may unpickle object arrays, which executes code; pass the literal False"
    return None


def _keyword_loader_message(node: ast.Call, aliases: ImportAliases) -> Optional[str]:
    """(b)(c)(e) The message for a ``torch.load`` / ``np.load`` / ``yaml.load`` call that unpickles or constructs arbitrary objects, else None."""
    qualified = aliases.qualified_name(node) or ""
    if qualified == "torch.load":
        return _torch_message(node)
    if qualified in ("numpy.load", "np.load"):
        return _numpy_message(node)
    if qualified in _KERAS_LOADERS:
        return "load_model(safe_mode=False) deserialises Lambda layers and custom code, which executes it" if _flag_is(node, "safe_mode", False) else None
    if qualified in ("ruamel.yaml.YAML", "ruamel.yaml.main.YAML"):
        typ = _kw(node, "typ")
        if isinstance(typ, ast.Constant) and typ.value == "unsafe":
            return "ruamel YAML(typ='unsafe') can construct arbitrary Python objects; use typ='safe'"
        return None
    if qualified in ("yaml.load", "yaml.load_all", "yaml.unsafe_load", "yaml.unsafe_load_all", "yaml.full_load", "yaml.full_load_all"):
        return _yaml_message(node, qualified, aliases)
    return None


def _check_keyword_loaders(tree: ast.Module, aliases: ImportAliases, sink: _Sink) -> None:
    """(b)(c)(e) Keyword-driven loaders anywhere in the file."""
    for node in shared_walk(tree):
        if isinstance(node, ast.Call):
            message = _keyword_loader_message(node, aliases)
            if message is not None:
                sink.report(node, message)


def _scope_calls(scope: ast.AST) -> list[ast.Call]:
    """Calls of *scope* outside nested functions, lambdas and classes."""
    calls: list[ast.Call] = []
    stack = list(child_nodes(scope))
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Call):
            calls.append(current)
        if not isinstance(current, (*_FUNCTIONS, ast.Lambda, ast.ClassDef)):
            stack.extend(child_nodes(current))
    return calls


def _calls_by_scope(tree: ast.Module) -> dict[int, list[ast.Call]]:
    """``id(scope) -> its own calls`` for the module, every function and every class, from the shared scope index."""
    out: dict[int, list[ast.Call]] = {}
    for scope in iter_scopes(tree):
        key = id(tree) if scope.node is None else id(scope.node)
        out[key] = [node for node in scope.nodes if isinstance(node, ast.Call)]
    return out


def _scope_calls_of(tree: ast.Module, scope: ast.AST) -> list[ast.Call]:
    """:func:`_scope_calls`, read from the per-tree index; a lambda (not an index scope) is walked as before."""
    hit = tree_memo(tree, "unsafe_deserialization.calls_by_scope", lambda: _calls_by_scope(tree)).get(id(scope))
    return hit if hit is not None else _scope_calls(scope)


def _load_target(call: ast.Call, aliases: ImportAliases) -> Optional[ast.Call]:
    """The call whose first argument is the deserialised source: *call* itself for a ``pickle.loads``-family function, the
    ``Unpickler(fh)`` constructor for ``Unpickler(fh).load()``, else None."""
    if aliases.qualified_name(call) in _PICKLE_LOADS:
        return call
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "load" and isinstance(func.value, ast.Call) and aliases.qualified_name(func.value) in _UNPICKLER_CTORS:
        return func.value
    return None


def _names_of(node: ast.AST, aliases: ImportAliases) -> set[str]:
    """Local names read in *node*; imported modules and functions name no data."""
    return {n.id for n in shared_walk(node) if isinstance(n, ast.Name) and not aliases.is_imported(n.id)}


def _related_names(scope: ast.AST, seeds: set[str], aliases: ImportAliases) -> set[str]:
    """*seeds* plus every name connected to them by an assignment in *scope* (``data = fh.read()`` ties ``data`` to ``fh``)."""
    pairs: list[set[str]] = []
    for node in shared_walk(scope):
        if isinstance(node, ast.Assign):
            pairs.append(_names_of(node, aliases))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            pairs.append(_names_of(node, aliases))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            pairs.append(_names_of(node.target, aliases) | _names_of(node.iter, aliases))
    related = set(seeds)
    changed = True
    while changed:
        changed = False
        for group in pairs:
            if group & related and not group <= related:
                related |= group
                changed = True
    return related


def _touches(candidate: ast.Call, related: set[str], aliases: ImportAliases) -> bool:
    """Does the verification or producer *candidate* deal with the loaded value? A call with no data at all
    (``self._check()``) cannot be judged and counts; ``hashlib.sha256(b"unrelated")`` has data and it is not the payload."""
    names = _names_of(candidate, aliases)
    if names & related:
        return True
    return not names and not any(isinstance(n, ast.Call) and (n.args or n.keywords) for n in shared_walk(candidate))


def _is_verifying_call(candidate: ast.Call, call: ast.Call) -> bool:
    """Is *candidate* a hash/signature check or a producer (dump/save) placed before *call*?"""
    return (
        candidate is not call
        and (candidate.lineno, candidate.col_offset) < (call.lineno, call.col_offset)
        and bool(_VERIFY.search(_call_label(candidate)) or _call_name(candidate) in _PRODUCERS)
    )


_BRANCHY = (ast.Try, ast.For, ast.AsyncFor, ast.ClassDef, *_FUNCTIONS, *(getattr(ast, n) for n in ("TryStar", "Match") if hasattr(ast, n)))


def _dominating_call_ids(scope: ast.AST) -> set[int]:
    """Ids of the calls that run on EVERY path to the end of *scope*'s first statements: top-level statements, the test of a top-level
    ``if``/``while``, and the body of a top-level ``with``. A check inside a branch (``if strict: verify(b)``) does not guard a load after it."""
    out: set[int] = set()

    def add(node: ast.AST) -> None:
        out.update(id(n) for n in shared_walk(node) if isinstance(n, ast.Call))

    def block(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.If, ast.While)):
                add(stmt.test)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    add(item.context_expr)
                block(stmt.body)
            elif isinstance(stmt, _BRANCHY):
                if isinstance(stmt, (ast.For, ast.AsyncFor)):
                    add(stmt.iter)
            else:
                add(stmt)

    body = getattr(scope, "body", None)
    if isinstance(body, list):
        block(body)
    else:  # a lambda: its body is one expression
        add(scope)
    return out


def _unverified_load(call: ast.Call, calls: list[ast.Call], scope: ast.AST, tree: ast.Module, aliases: ImportAliases) -> bool:
    """Is *call* a pickle-family load of non-literal bytes with no earlier verification in its function or an enclosing one?"""
    target = _load_target(call, aliases)
    if target is None:
        return False
    first = target.args[0] if target.args else None
    if isinstance(first, ast.Constant) and not isinstance(first.value, (str, bytes)):
        return False
    dominating = _dominating_call_ids(scope)
    verifiers = [c for c in calls if id(c) in dominating and _is_verifying_call(c, call)]
    seeds = _names_of(first, aliases) if first is not None else set()
    if verifiers and seeds:
        related = _related_names(scope, seeds, aliases)
        verifiers = [c for c in verifiers if _touches(c, related, aliases)]
    if verifiers:
        return False
    return not (isinstance(scope, _FUNCTIONS) and _verified_by_enclosing(tree, scope, call))


def _check_pickle_loads(tree: ast.Module, aliases: ImportAliases, safe_loader_functions: set[int], sink: _Sink) -> None:
    """(d) Pickle-family loads with no preceding hash or signature check in the same function."""
    extra = [
        (n, n.name if isinstance(n, ast.ClassDef) else "<lambda>") for n in shared_walk(tree) if isinstance(n, (ast.ClassDef, ast.Lambda))
    ]  # a class body and a lambda run code of their own, with no function around them to hold a verification
    for scope, qualname in [(tree, "<module>"), *[(f, q) for q, f in qualified_functions(tree)], *extra]:
        if id(scope) in safe_loader_functions:
            continue
        calls = _scope_calls_of(tree, scope)
        for call in calls:
            if _unverified_load(call, calls, scope, tree, aliases):
                sink.report(call, f"{_call_label(call)} in {qualname} with no earlier sha256/hmac/verify call: unpickling unverified bytes executes code")


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: Optional[list[str]] = None) -> list[Finding]:
    """The findings in one parsed file. ``lines`` are its source lines, for the suppression comment."""
    sink = _Sink(rel, lines or [])
    safe_loader_functions = _check_unpicklers(tree, aliases, _module_constants(tree), sink)
    _check_keyword_loaders(tree, aliases, sink)
    _check_pickle_loads(tree, aliases, safe_loader_functions, sink)
    return sink.out


def _verified_by_enclosing(tree: ast.Module, scope: ast.AST, call: ast.Call) -> bool:
    """A nested function loads what its enclosing function verified earlier."""
    for node in shared_walk(tree):
        if isinstance(node, _FUNCTIONS) and node is not scope and any(n is scope for n in shared_walk(node)):
            for inner in shared_walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and (inner.lineno, inner.col_offset) < (call.lineno, call.col_offset)
                    and (_VERIFY.search(_call_label(inner)) or _call_name(inner) in _PRODUCERS)
                ):
                    return True
    return False


def find_unsafe_deserialization(
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
    scan = scan_python(root, min_files=min_files, use_git=use_git, include_stubs=True)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        if any(fragment in parsed.rel for fragment in skipped):
            continue
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree, scoped=True), parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_unsafe_deserialization(
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
    found = find_unsafe_deserialization(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, exclude=exclude)
    guidance = (
        "allowlist (module, name) pairs in find_class, pass weights_only=True / allow_pickle=False / yaml.safe_load, "
        "verify a sha256 or hmac before unpickling, or `# deserialize-ok: <reason>`"
    )
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="unsafe_deserialization", refresh_command="PY_CI_SHARED_REFRESH=unsafe_deserialization")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} unsafe-deserialization finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
