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
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

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
    }
)
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
_VERIFY = re.compile(r"sha256|hmac|verify", re.IGNORECASE)
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
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


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


def _find_class_problems(func: ast.FunctionDef, consts: dict[str, ast.AST]) -> list[str]:
    """What is wrong with one ``find_class`` body, as short messages."""
    params = [a.arg for a in func.args.args]
    if len(params) < 3:
        return []
    module, name = params[1], params[2]
    parents = {id(c): p for p in ast.walk(func) for c in ast.iter_child_nodes(p)}
    problems: list[str] = []
    module_only = False
    per_name = False
    resolves = False
    for node in ast.walk(func):
        if isinstance(node, (ast.Compare, ast.Call)) and _mentions(node, module):
            if isinstance(node, ast.Call) and _call_name(node) in ("getattr", "__import__", "import_module", "find_class"):
                continue
            if _mentions(node, name):
                per_name = True
            elif isinstance(node, ast.Compare) or (isinstance(node.func, ast.Attribute) and node.func.attr in ("startswith", "endswith")):
                module_only = True
        elif isinstance(node, ast.Compare) and _mentions(node, name):
            per_name = True
        if isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Name) and callee.id == "getattr" and node.args and _mentions(node.args[0], module):
                resolves = True
            if isinstance(callee, ast.Attribute) and callee.attr == "find_class" and any(_mentions(a, module) for a in node.args):
                resolves = True
    if (module_only or resolves) and not per_name:
        problems.append("find_class admits whole modules (a test on the module alone, no per-name allowlist)")
    seen_gadget: set[str] = set()
    paired = _paired_ids(func)
    for node in ast.walk(func):
        pool: list[ast.AST] = []
        if isinstance(node, (ast.Constant, ast.Tuple)):
            if not _raises_in_body(node, parents) and id(node) not in paired:
                pool = [node]
        else:
            ref = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
            if ref in consts and not _DENY_NAME.search(ref) and not _raises_in_body(node, parents):
                pool = [consts[ref]]
        for container in pool:
            for text in _gadget_entries(container):
                if text not in seen_gadget:
                    seen_gadget.add(text)
                    problems.append(f"find_class admits gadget module {text.split('.', 1)[0]!r} ({text!r})")
    return problems


def _is_pair(node: ast.AST) -> bool:
    return isinstance(node, ast.Tuple) and len(node.elts) == 2 and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts)


def _paired_ids(root: ast.AST) -> set[int]:
    """Ids of the string constants that sit in a ``("module", "name")`` pair, judged as a pair rather than alone."""
    return {id(e) for n in ast.walk(root) if _is_pair(n) for e in n.elts}  # type: ignore[attr-defined]


def _gadget_entries(container: ast.AST) -> list[str]:
    """Strings under *container* that admit a gadget module: the module alone (``"builtins"``), or ``("os", "system")`` /
    ``"builtins.eval"`` naming a dangerous callable. A harmless pair such as ``("builtins", "set")`` is the safe pattern."""
    found: list[str] = []
    paired = _paired_ids(container)
    for node in ast.walk(container):
        if _is_pair(node):
            module, name = (e.value for e in node.elts)  # type: ignore[attr-defined]
            if module in _GADGET_MODULES and name in _DANGEROUS_NAMES:
                found.append(f"{module}.{name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in paired:
            head, _, tail = node.value.partition(".")
            if head in _GADGET_MODULES and (not tail or tail in _DANGEROUS_NAMES):
                found.append(node.value)
    return found


def _qualified_scopes(tree: ast.Module) -> list[tuple[ast.AST, str]]:
    out: list[tuple[ast.AST, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, _FUNCTIONS):
                out.append((child, f"{prefix}{child.name}"))
                visit(child, f"{prefix}{child.name}.<locals>.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


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
        return ast.unparse(node.func)
    except Exception:  # pragma: no cover - ast.unparse handles every parsed node
        return "<call>"


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: Optional[list[str]] = None) -> list[Finding]:
    """The findings in one parsed file. ``lines`` are its source lines, for the suppression comment."""
    lines = lines or []
    out: list[Finding] = []
    consts = _module_constants(tree)

    def report(node: ast.AST, message: str, anchors: Iterable[ast.AST] = ()) -> None:
        if _suppressed(lines, node) or any(_suppressed(lines, a) for a in anchors):
            return
        out.append(Finding(rel, getattr(node, "lineno", 0), RULE, message))

    # (a) restricted unpicklers and the methods that make up the safe loader
    safe_loader_functions: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_unpickler_class(node, aliases):
            for member in ast.walk(node):
                if isinstance(member, _FUNCTIONS):
                    safe_loader_functions.add(id(member))
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "find_class":
                    for problem in _find_class_problems(item, {**consts, **_module_constants(node)}):
                        report(item, f"{node.name}: {problem}", anchors=(node,))

    # (b)(c)(e) keyword-driven loaders
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qualified = aliases.qualified_name(node) or ""
        if qualified == "torch.load":
            flag = _kw(node, "weights_only")
            if flag is None or (isinstance(flag, ast.Constant) and flag.value is not True):
                report(node, "torch.load without weights_only=True unpickles the checkpoint, which executes code")
        elif qualified in ("numpy.load", "np.load"):
            flag = _kw(node, "allow_pickle")
            if isinstance(flag, ast.Constant) and flag.value is True:
                report(node, "np.load(allow_pickle=True) unpickles object arrays, which executes code")
        elif qualified in ("yaml.load", "yaml.load_all", "yaml.unsafe_load", "yaml.unsafe_load_all", "yaml.full_load", "yaml.full_load_all"):
            loader = _kw(node, "Loader") or (node.args[1] if len(node.args) > 1 else None)
            loader_name = (aliases.qualified_name(loader) or "").rsplit(".", 1)[-1] if loader is not None else ""
            if qualified.endswith(("unsafe_load", "unsafe_load_all", "full_load", "full_load_all")) or loader_name not in _SAFE_YAML:
                report(node, f"{qualified} without a safe Loader can construct arbitrary Python objects; use yaml.safe_load")

    # (d) pickle-family loads with no preceding hash or signature check in the same function
    for scope, qualname in [(tree, "<module>"), *_qualified_scopes(tree)]:
        if id(scope) in safe_loader_functions:
            continue
        calls: list[ast.Call] = []
        stack = list(ast.iter_child_nodes(scope))
        while stack:
            current = stack.pop()
            if isinstance(current, ast.Call):
                calls.append(current)
            if not isinstance(current, (*_FUNCTIONS, ast.Lambda, ast.ClassDef)):
                stack.extend(ast.iter_child_nodes(current))
        for call in calls:
            if aliases.qualified_name(call) not in _PICKLE_LOADS:
                continue
            first = call.args[0] if call.args else None
            if isinstance(first, ast.Constant) and not isinstance(first.value, (str, bytes)):
                continue
            verified = any(
                c is not call and (c.lineno, c.col_offset) < (call.lineno, call.col_offset) and (_VERIFY.search(_call_label(c)) or _call_name(c) in _PRODUCERS)
                for c in calls
            )
            if not verified and isinstance(scope, _FUNCTIONS):
                verified = _verified_by_enclosing(tree, scope, call)
            if not verified:
                report(call, f"{_call_label(call)} in {qualname} with no earlier sha256/hmac/verify call: unpickling unverified bytes executes code")
    return out


def _verified_by_enclosing(tree: ast.Module, scope: ast.AST, call: ast.Call) -> bool:
    """A nested function loads what its enclosing function verified earlier."""
    for node in ast.walk(tree):
        if isinstance(node, _FUNCTIONS) and node is not scope and any(n is scope for n in ast.walk(node)):
            for inner in ast.walk(node):
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
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        if any(fragment in parsed.rel for fragment in skipped):
            continue
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source.splitlines()))
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
