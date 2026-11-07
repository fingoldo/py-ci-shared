"""Boolean environment flags read by hand, each with its own idea of what "on" means.

``not os.environ.get(NAME)`` treats any non-empty value as on, so setting the flag to ``0`` turns it on. ``lower() in
{"1", "true", "yes"}`` silently ignores ``on``. ``== "1"`` ignores everything else. The shipped instance had all three in
one package: an operator who wrote ``FLAG=0`` got the opposite of what they asked for from one switch and the right
answer from the next.

The check flags every ``os.environ.get("<PREFIX>...")`` / ``os.environ["<PREFIX>..."]`` / ``os.getenv(...)`` used in a
boolean context - a truth test, ``not``, a comparison against a string literal, or membership in a literal collection -
and asks for one shared parser instead. Non-boolean reads (paths, numbers, backend names) never appear in those contexts
and are not reported.

Usage from a repository's meta tests::

    from py_ci_shared.env_flag_parsing import assert_env_flags_use_one_parser

    def test_env_flags_go_through_env_flag():
        assert_env_flags_use_one_parser(files=SRC_FILES, repo_root=REPO_ROOT, prefixes=("MYPROJ_",), allowed={})
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from ._core import ScanResult, scan_python
from ._core.node_index import walk as _fast_walk

__all__ = ["EnvFlagRead", "find_hand_parsed_env_flags", "assert_env_flags_use_one_parser"]

_GETTERS = frozenset({"getenv", "environ"})


class EnvFlagRead:
    """One hand-parsed boolean env read: where it is, which variable, and what shape the parse took."""

    __slots__ = ("function", "lineno", "path", "shape", "var")

    def __init__(self, path: str, function: str, lineno: int, var: str, shape: str) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.var = var
        self.shape = shape

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.var} ({self.shape})"


_BOOL_WORDS = frozenset({"0", "1", "true", "false", "yes", "no", "on", "off", "y", "n", "t", "f", ""})
#: A variable whose name says it holds something other than a switch: a path, a number, a URL or host, a credential or a secret.
#: Such a read is a presence or value check, so it is not judged (37-F8, 37-F32).
_NOT_A_FLAG = re.compile(
    r"(?:^|_)(PATHS?|DIRS?|FILES?|ROOT|URLS?|URIS?|DSN|HOSTS?|PORT|USERS?|USERNAME|PASS|PASSWORD|PWD|TOKEN|SECRET|KEY|CREDENTIALS?|AUTH"
    r"|COUNT|NUM|ROWS|LIMIT|SIZE|TIMEOUT|SECONDS|THREADS|WORKERS)$",
    re.IGNORECASE,
)


class _Ctx:
    """What a file binds that makes an env read recognisable behind a name: ``env = os.environ``, ``N = "MY_F"``, ``v = os.environ.get(N)``."""

    def __init__(self, tree: ast.Module, prefixes: Sequence[str]) -> None:
        self.prefixes = tuple(p.lower() for p in prefixes)
        self.strings: dict[str, str] = {}
        self.environ_names: set[str] = {"environ"}
        self.bound: dict[str, str] = {}
        counts: dict[str, int] = {}
        pending: list[tuple[str, ast.AST]] = []
        for node in _fast_walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                counts[name] = counts.get(name, 0) + 1
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    self.strings[name] = node.value.value
                elif _is_environ(node.value, self.environ_names):
                    self.environ_names.add(name)
                else:
                    pending.append((name, node.value))
            elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                counts[node.target.id] = counts.get(node.target.id, 0) + 1
                pending.append((node.target.id, node.value))
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For)) and isinstance(node.target, ast.Name):
                name = node.target.id
                counts[name] = counts.get(name, 0) + 2  # rebound by something else: not a single assignment
        for name, value in pending:
            var = self.read(value)
            if var is not None and counts.get(name) == 1:
                self.bound[name] = var

    def read(self, node: ast.AST) -> str | None:
        """The variable name when ``node`` reads the environment for one of the prefixes (directly, through a walrus, or through a once-assigned local)."""
        target = node
        while isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute) and target.func.attr in {"strip", "lower", "upper", "casefold"}:
            target = target.func.value
        if isinstance(target, ast.NamedExpr):
            return self.read(target.value)
        if isinstance(target, ast.Name):
            return self.bound.get(target.id)
        name = None
        if isinstance(target, ast.Call):
            func = target.func
            attr = getattr(func, "attr", getattr(func, "id", None))
            if attr in {"getenv", "get"} and target.args:
                ok = attr == "getenv" or _is_environ(getattr(func, "value", None), self.environ_names)
                name = self._key(target.args[0]) if ok else None
        elif isinstance(target, ast.Subscript) and _is_environ(target.value, self.environ_names):
            name = self._key(target.slice)
        if name is None or not any(name.lower().startswith(p) for p in self.prefixes):
            return None
        return name

    def _key(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.strings.get(node.id)
        if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant) and isinstance(node.values[0].value, str):
            return node.values[0].value + "*"  # f"MY_{name}": the literal prefix decides whether it is ours
        return None


def _is_environ(node: ast.AST | None, names: set[str]) -> bool:
    return getattr(node, "attr", getattr(node, "id", None)) in names


def _boolean_literals(node: ast.AST) -> bool:
    """A literal collection or string whose members all look like boolean spellings (``("1", "true")``), not a backend selector."""
    items = node.elts if isinstance(node, (ast.Set, ast.List, ast.Tuple)) else [node]
    return bool(items) and all(isinstance(i, ast.Constant) and isinstance(i.value, str) and i.value.lower() in _BOOL_WORDS for i in items)


def _compare_shape(node: ast.Compare, ctx: _Ctx) -> list[tuple[str, str]]:
    if isinstance(node.ops[0], (ast.In, ast.NotIn)) and _is_environ(node.comparators[0], ctx.environ_names):
        key = ctx._key(node.left)
        if key is not None and any(key.lower().startswith(p) for p in ctx.prefixes):
            return [(key, "presence test (`X in os.environ`: FLAG=0 turns it on)")]
        return []
    var = ctx.read(node.left)
    if var is None:
        return []
    op = node.ops[0]
    right = node.comparators[0]
    if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Constant) and isinstance(right.value, str) and right.value.lower() in _BOOL_WORDS:
        return [(var, f"compared with {right.value!r}")]
    if isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, (ast.Set, ast.List, ast.Tuple)) and _boolean_literals(right):
        return [(var, "membership in a literal collection")]
    return []


def _boolean_shapes(node: ast.AST, ctx: _Ctx) -> list[tuple[str, str]]:
    """``(var, shape)`` for each env read *node* uses as a boolean, in source order."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        var = ctx.read(node.operand)
        return [(var, "not <read>")] if var else []
    if isinstance(node, ast.BoolOp):
        if any(isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.lower() not in _BOOL_WORDS for v in node.values):
            return []  # `read or "auto"` supplies a default string: a selector, not a flag
        return [(var, "truthiness in and/or") for var in (ctx.read(value) for value in node.values) if var]
    if isinstance(node, (ast.If, ast.While, ast.IfExp)):
        var = ctx.read(node.test)
        return [(var, "truthiness in a condition")] if var else []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "bool" and len(node.args) == 1:
        inner = node.args[0]
        while isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "int" and len(inner.args) == 1:
            inner = inner.args[0]  # bool(int(read)): the ad-hoc "0/1" parse
        var = ctx.read(inner)
        return [(var, "bool(<read>)")] if var else []
    if isinstance(node, ast.Compare) and node.comparators:
        return _compare_shape(node, ctx)
    return []


_NUMERIC_CONVERTERS = frozenset({"int", "float", "Path", "PurePath", "Decimal"})
_VALUE_METHODS = frozenset({"split", "rsplit", "isdigit", "isnumeric", "isdecimal", "partition", "rpartition"})
_ARITHMETIC = (ast.Mult, ast.Sub, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _consumed_values(node: ast.AST) -> list[ast.AST]:
    """The expressions *node* consumes as a number, a path or a list (``int(x)``, ``Path(x)``, ``os.path.join(x)``, ``x.split()``, ``x * 2``, ``x > 3``)."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _NUMERIC_CONVERTERS:
            return list(node.args[:1])
        if isinstance(func, ast.Attribute) and func.attr in _VALUE_METHODS:
            return [func.value]
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute) and func.value.attr == "path":
            return list(node.args)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, _ARITHMETIC):
        return [node.left, node.right]
    elif isinstance(node, ast.Compare) and any(isinstance(o, _ORDERING) for o in node.ops):
        return [node.left, *node.comparators]
    return []


def _numeric_vars(tree: ast.Module, ctx: _Ctx) -> frozenset[str]:
    """Variables whose value this file also uses as a number, a path or a list: not a boolean switch, whatever its name."""
    out: set[str] = set()
    nodes = list(_fast_walk(tree))
    # ``bool(int(read))`` is the hand-parsed switch itself, not a numeric use
    switch_parses = {id(n.args[0]) for n in nodes if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "bool" and len(n.args) == 1}
    for node in nodes:
        if id(node) in switch_parses:
            continue
        for value in _consumed_values(node):
            var = ctx.read(value)
            if var is not None:
                out.add(var)
    return frozenset(out)


def _boolean_contexts(tree: ast.Module, prefixes: Sequence[str]):
    """Yield ``(node, var, shape)`` for each env read that is used as a boolean."""
    ctx = _Ctx(tree, prefixes)
    numeric = _numeric_vars(tree, ctx)
    for node in _fast_walk(tree):
        for var, shape in _boolean_shapes(node, ctx):
            if var not in numeric and not _NOT_A_FLAG.search(var):
                yield node, var, shape


def _reads_in(tree: ast.Module, rel: str, prefixes: Sequence[str]) -> list[EnvFlagRead]:
    owner: dict[int, str] = {}
    for func in (n for n in _fast_walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for child in _fast_walk(func):
            owner.setdefault(id(child), func.name)
    return [EnvFlagRead(rel, owner.get(id(node), "<module>"), node.lineno, var, shape) for node, var, shape in _boolean_contexts(tree, prefixes)]


def _dedup(out: list[EnvFlagRead]) -> list[EnvFlagRead]:
    seen: dict[tuple, EnvFlagRead] = {}
    for r in out:
        seen.setdefault((r.path, r.lineno, r.var, r.shape), r)
    return [seen[k] for k in sorted(seen)]


def _scan(files: Iterable[Path], repo_root: Path) -> ScanResult:
    return scan_python([Path(f) for f in files], root=Path(repo_root).resolve(), min_files=0)


def find_hand_parsed_env_flags(files: Iterable[Path], repo_root: Path, prefixes: Sequence[str]) -> list[EnvFlagRead]:
    """Every boolean-context read of a ``prefixes`` environment variable that does not go through a shared parser.

    Files that cannot be read or parsed yield nothing here; :func:`assert_env_flags_use_one_parser` reports them.
    """
    scan = _scan(files, repo_root)
    return _dedup([r for f in scan for r in _reads_in(f.tree, f.rel, prefixes)])


def assert_env_flags_use_one_parser(
    files: Iterable[Path],
    repo_root: Path,
    prefixes: Sequence[str],
    allowed: Mapping[str, str] | None = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Fail on a boolean env flag parsed by hand, and on any file that could not be read or parsed.

    ``allowed`` maps a variable name to the reason it is read directly (a three-state switch, a value forwarded verbatim
    to another tool); an empty reason is rejected, and an entry with nothing left to excuse must be removed.
    ``min_files`` is a floor on the files that PARSED.
    """
    allowed = dict(allowed or {})
    empty = sorted(k for k, v in allowed.items() if not str(v).strip())
    if empty:
        raise AssertionError(f"allowed env flags need a reason: {empty}")
    scan = _scan(files, repo_root)
    if scan.parsed_count < min_files:
        raise AssertionError(f"parsed only {scan.parsed_count} files (< {min_files}); the scan lost its subject")
    reads = _dedup([r for f in scan for r in _reads_in(f.tree, f.rel, prefixes)])
    bad = [r for r in reads if r.var not in allowed]
    stale = sorted(set(allowed) - {r.var for r in reads})
    msgs = []
    if scan.unparsed and not allow_unparsed:
        msgs.append("files that could not be read or parsed, so their env reads were not checked: " + "; ".join(u.render() for u in scan.unparsed))
    if bad:
        msgs.append(
            "boolean env flags parsed by hand (each spelling of 'on' differs; route them through one shared "
            "env_flag(name, default)): " + "; ".join(map(repr, bad))
        )
    if stale and not scan.unparsed:
        msgs.append(f"allowed env flags that are no longer read by hand: {stale}")
    if msgs:
        raise AssertionError("\n".join(msgs))
