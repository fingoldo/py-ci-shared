"""No test assertion has an environment value or a resolved DSN as an operand, because pytest prints operands.

pytest's assertion rewrite explains a failing ``assert`` by printing the value of every name, call and comparison in
it, and it does so even when the assert carries its own message: ``assert dsn is None, "a DSN was resolved"`` still
prints the DSN under the message. So ``assert "K" not in os.environ`` prints ``repr(os.environ)``, every ``.env`` value
included, and ``assert dsn == FAKE`` prints the real DSN in exactly the failure it exists to catch. A
``self.assertEqual`` (any ``self.assert*``) and a mock's ``.assert_called_with(...)`` print their arguments too.

Real cases: the Upwork dashboard's ``test_an_unset_dsn_is_none`` printed the full production DSN when a ``.env`` was
present (audit 16b.2), and a py-ci-shared meta-test printed every ``.env`` value (fixed in ``import_side_effects`` by
8d47b8f). The safe shape folds the value to a bool BEFORE the assert: ``resolved = dsn is not None`` then
``assert not resolved, "a DSN was resolved"``.

A value is SECRET-SHAPED (tainted) when it is ``os.environ`` itself (or ``environ`` imported from ``os``), a subscript
or method call on it, ``os.getenv(...)``, a call to a resolver named in *resolvers* (default :data:`DEFAULT_RESOLVERS`:
``dsn_or_none``, ``get_dsn``, ``dotenv_values`` and kin) or named like one (:data:`RESOLVER_NAME`: ``_dsn()``), an f-string, ``or``/conditional expression, ``dict(...)``/
``str(...)``/... of a tainted value, or a name bound from one in the same function, an enclosing function or at module
scope. A comparison or a predicate method (``startswith``, ``isdigit``) is a bool, which is what makes the safe shape
safe. Not followed: a fixture that returns an environment value (the test only sees a parameter name).

Allowlist: a text file of ``relpath::qualname  # reason`` lines. An entry without a reason is a finding, and so is an
entry that no longer matches an offender (stale), so the list only shrinks.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional

from ._core import Finding, ScanResult, scan_python
from ._core.gate_contract import check_scan

__all__ = [
    "DEFAULT_RESOLVERS",
    "RESOLVER_NAME",
    "RULE",
    "assert_no_secret_assertion_operands",
    "find_secret_assertion_operands",
    "offenders_in_source",
    "load_allowlist",
]

RULE = "secret-assertion-operand"
#: Calls whose result is a DSN or a mapping of secrets. Consumers add their own with ``resolvers=``.
DEFAULT_RESOLVERS = frozenset(
    {
        "dsn_or_none",
        "get_dsn",
        "_get_dsn",
        "resolve_dsn",
        "_resolve_dsn",
        "get_database_url",
        "database_url",
        "dotenv_values",
        "database_jobstracker_url_from_dotenv",
    }
)
#: A call named like a DSN getter (``_dsn``, ``get_dsn``, ``resolve_dsn``, ``dsn_or_none``) is a resolver too; a
#: predicate such as ``dsn_is_configured`` does not match.
RESOLVER_NAME = re.compile(r"_?(get_|resolve_|_resolve_)?(dsn|database_url)(_or_none)?", re.IGNORECASE)
_ENV_GETTERS = frozenset({"getenv", "getenvb"})
#: An environment variable name whose value is a secret. Not ``PWD`` (the working directory on POSIX) nor ``SESSION``
#: (``SESSIONNAME=Console`` on Windows). A ``*_URL``/``*_URI`` counts here, since the gate cannot see the value.
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSW(OR)?D|DSN|CREDENTIAL|AUTH|COOKIE|PRIVATE)", re.IGNORECASE)
_URL_NAME = re.compile(r"_UR[LI]$|^DATABASE", re.IGNORECASE)
#: Methods of a str or mapping that return a bool or a count, never the value.
_PREDICATE_METHODS = frozenset({"startswith", "endswith", "__contains__", "count", "find", "rfind", "index", "__len__", "keys"})
#: Builtins that carry their argument's text into their result.
_CARRYING_BUILTINS = frozenset({"dict", "str", "repr", "list", "tuple", "sorted", "set", "frozenset", "format", "ascii", "vars"})


def _is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr in ("environ", "environb") and isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id in ("environ", "environb")


def _call_name(func: ast.AST) -> Optional[str]:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _secret_name(name: str) -> bool:
    return bool(SECRET_NAME.search(name) or _URL_NAME.search(name))


def _harmless_env_read(node: ast.AST) -> bool:
    """``os.environ["HOME"]`` or ``os.getenv("HOME")``: one variable, named by a literal that does not look secret.

    pytest prints only the subscript's or call's VALUE there. ``os.environ.get("HOME")`` is not harmless: pytest
    explains the bound method ``environ.get``, whose repr is the whole environment."""
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
        key: ast.AST = node.slice
    elif isinstance(node, ast.Call) and _call_name(node.func) in _ENV_GETTERS and node.args:
        key = node.args[0]
    else:
        return False
    return isinstance(key, ast.Constant) and isinstance(key.value, str) and not _secret_name(key.value)


class _Taint:
    def __init__(self, names: "set[str]", resolvers: "frozenset[str]") -> None:
        self.names = names
        self.resolvers = resolvers

    def is_tainted(self, node: ast.AST) -> bool:
        if _is_environ(node):
            return True
        if _harmless_env_read(node):
            return False
        if isinstance(node, ast.Name):
            return node.id in self.names
        if isinstance(node, ast.Subscript):
            return self.is_tainted(node.value)
        if isinstance(node, ast.Call):
            return self._call_tainted(node)
        return any(self.is_tainted(part) for part in self._carried_parts(node))

    def _carried_parts(self, node: ast.AST) -> list[ast.AST]:
        """The sub-expressions whose value an expression can carry through to its own value."""
        if isinstance(node, ast.JoinedStr):
            return [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
        if isinstance(node, (ast.FormattedValue, ast.NamedExpr, ast.Starred)):
            return [node.value]
        if isinstance(node, ast.IfExp):
            return [node.body, node.orelse]
        if isinstance(node, ast.BoolOp):  # `a or b` evaluates to one of its operands, not to a bool
            return list(node.values)
        if isinstance(node, ast.BinOp):  # "prefix" + dsn, "%s" % dsn
            return [node.left, node.right]
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return list(node.elts)
        return []

    def _call_tainted(self, node: ast.Call) -> bool:
        name = _call_name(node.func)
        if name in self.resolvers or name in _ENV_GETTERS or (name is not None and RESOLVER_NAME.fullmatch(name)):
            return True
        if isinstance(node.func, ast.Name) and name in _CARRYING_BUILTINS:
            return any(self.is_tainted(a) for a in node.args)
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _PREDICATE_METHODS or attr.startswith("is"):
                return False
            if attr == "format" and any(self.is_tainted(a) for a in [*node.args, *(k.value for k in node.keywords)]):
                return True
            return self.is_tainted(node.func.value)
        return False

    def first_tainted(self, node: ast.AST) -> Optional[ast.AST]:
        """The first secret-shaped sub-expression pytest would print, or None."""
        queue = [node]
        while queue:
            sub = queue.pop(0)
            if self.is_tainted(sub):
                return sub
            if not _harmless_env_read(sub):  # its ``os.environ`` operand is evaluated, never printed
                queue.extend(ast.iter_child_nodes(sub))
        return None


def _targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [n for elt in node.elts for n in _targets(elt)]
    if isinstance(node, ast.Starred):
        return _targets(node.value)
    return []


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes of *scope* without descending into nested functions or classes (they are their own scope)."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _bindings(node: ast.AST) -> "list[tuple[ast.AST, ast.AST]]":
    """``(target, value)`` pairs *node* binds: assignments, walrus, ``for``/comprehension targets, ``with ... as``."""
    if isinstance(node, ast.Assign):
        return [(target, node.value) for target in node.targets]
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
        return [(node.target, node.value)]
    if isinstance(node, ast.NamedExpr):
        return [(node.target, node.value)]
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        return [(node.target, node.iter)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [(item.optional_vars, item.context_expr) for item in node.items if item.optional_vars is not None]
    return []


def _scope_taint(scope: ast.AST, inherited: "set[str]", resolvers: "frozenset[str]") -> "set[str]":
    """Names bound from a tainted value in *scope*, to a fixed point (``b = a.strip()`` after ``a = getenv(..)``)."""
    names = set(inherited)
    while True:
        taint = _Taint(names, resolvers)
        grown = set(names)
        for node in _own_nodes(scope):
            for target, value in _bindings(node):
                if taint.is_tainted(value):
                    grown.update(_targets(target))
        if grown == names:
            return names
        names = grown


def _printed_operands(node: ast.AST) -> list[ast.expr]:
    """The expressions pytest (or unittest/mock) prints when *node* fails, or [] when *node* is not an assertion."""
    if isinstance(node, ast.Assert):
        return [node.test] + ([node.msg] if node.msg is not None else [])
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr.startswith("assert"):
        if node.func.attr in ("assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex", "assertLogs", "assertNoLogs"):
            return []
        return [*node.args, *(k.value for k in node.keywords)]
    return []


def offenders_in_source(source: str, path: str = "<string>", *, resolvers: Iterable[str] = DEFAULT_RESOLVERS) -> list[tuple[str, int, str]]:
    """``(qualname, line, offending expression)`` for each assertion in *source* that would print a secret-shaped value."""
    tree = ast.parse(source, filename=path)
    resolver_set = frozenset(resolvers)
    found: list[tuple[str, int, str]] = []

    def visit(scope: ast.AST, qual: str, inherited: "set[str]") -> None:
        names = _scope_taint(scope, inherited, resolver_set)
        taint = _Taint(names, resolver_set)
        for node in _own_nodes(scope):
            for expr in _printed_operands(node):
                hit = taint.first_tainted(expr)
                if hit is not None:
                    found.append((qual or "<module>", getattr(node, "lineno", 0), ast.unparse(hit)))
                    break
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(node, f"{qual}.{node.name}" if qual else node.name, names)
            elif isinstance(node, ast.ClassDef):
                # A class body is not an enclosing scope for its methods' names, but module names still reach them.
                visit(node, f"{qual}.{node.name}" if qual else node.name, inherited)

    visit(tree, "", set())
    return sorted(set(found), key=lambda f: (f[1], f[0]))


def load_allowlist(path: Path) -> "tuple[dict[str, str], list[str]]":
    """``({key: reason}, problems)`` from ``relpath::qualname  # reason`` lines; a missing file is an empty list."""
    entries: dict[str, str] = {}
    problems: list[str] = []
    if not path.exists():
        return entries, problems
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, reason = line.partition("#")
        if not sep or not reason.strip():
            problems.append(f"{path.name}:{number}: allowlist entry {key.strip()!r} has no reason (`relpath::qualname  # why`)")
            continue
        entries[key.strip()] = reason.strip()
    return entries, problems


def _scan(test_files: Iterable[Path], root: Optional[Path]) -> ScanResult:
    return scan_python([Path(p) for p in test_files], root=root)


def _findings(scan: ScanResult, resolvers: Iterable[str]) -> list[Finding]:
    out: list[Finding] = []
    for parsed in scan:
        for qual, line, expr in offenders_in_source(parsed.source, parsed.rel, resolvers=resolvers):
            out.append(Finding(parsed.rel, line, RULE, f"{qual}: `{expr}`", key=f"{parsed.rel}::{qual}"))
    return out


def find_secret_assertion_operands(
    test_files: Iterable[Path], *, root: Optional[Path] = None, resolvers: Iterable[str] = DEFAULT_RESOLVERS, allow_unparsed: bool = True
) -> list[Finding]:
    """Every assertion in *test_files* (files or directories) that would print an environment value or a DSN."""
    scan = _scan(test_files, root)
    found = _findings(scan, frozenset(resolvers) | DEFAULT_RESOLVERS)
    return found if allow_unparsed else [*scan.unparsed_findings(), *found]


def assert_no_secret_assertion_operands(
    test_files: Iterable[Path],
    *,
    root: Optional[Path] = None,
    resolvers: Iterable[str] = (),
    allowlist_path: Optional[Path] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Fail on an assertion whose printed operand is an environment value or a DSN.

    *test_files*: files or directories (test trees). *resolvers*: extra function names whose result is a secret, on top
    of :data:`DEFAULT_RESOLVERS`. *allowlist_path*: ``relpath::qualname  # reason`` entries (relpath relative to *root*);
    an entry without a reason or matching no offender fails too.
    """
    scan = _scan(test_files, root)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    found = _findings(scan, frozenset(resolvers) | DEFAULT_RESOLVERS)
    allowed, problems = load_allowlist(Path(allowlist_path)) if allowlist_path is not None else ({}, [])
    offenders = [f for f in found if f.key not in allowed]
    live = {f.key for f in found}
    stale = [f"allowlist entry {key!r} matches no offender any more; delete it" for key in sorted(allowed) if key not in live]
    lines = [f.render() for f in offenders] + problems + stale
    if lines:
        raise AssertionError(
            f"{len(offenders)} assertion(s) would print an environment value or a DSN when they fail (pytest prints every "
            "operand, even under a custom message). Fold the value to a bool first: `resolved = dsn is not None` then "
            "`assert not resolved, 'a DSN was resolved'`:\n  " + "\n  ".join(lines)
        )
