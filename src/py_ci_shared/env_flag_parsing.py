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


def _env_var_name(node: ast.AST, prefixes: Sequence[str]) -> str | None:
    """The variable name when ``node`` reads ``os.environ`` / ``os.getenv`` for one of ``prefixes``, else None."""
    target = node
    # Unwrap the string grooming a hand parse applies: .strip().lower() and friends.
    while isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute) and target.func.attr in {"strip", "lower", "upper", "casefold"}:
        target = target.func.value
    name = None
    if isinstance(target, ast.Call):
        func = target.func
        attr = getattr(func, "attr", getattr(func, "id", None))
        if attr in {"getenv", "get"} and target.args:
            receiver = getattr(func, "value", None)
            ok = attr == "getenv" or getattr(receiver, "attr", getattr(receiver, "id", None)) == "environ"
            first = target.args[0]
            if ok and isinstance(first, ast.Constant) and isinstance(first.value, str):
                name = first.value
    elif isinstance(target, ast.Subscript):
        receiver = target.value
        if getattr(receiver, "attr", getattr(receiver, "id", None)) == "environ" and isinstance(target.slice, ast.Constant):
            if isinstance(target.slice.value, str):
                name = target.slice.value
    if name is None or not any(name.startswith(p) for p in prefixes):
        return None
    return name


def _boolean_contexts(tree: ast.Module, prefixes: Sequence[str]):
    """Yield ``(node, var, shape)`` for each env read that is used as a boolean."""
    for node in _fast_walk(tree):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            var = _env_var_name(node.operand, prefixes)
            if var:
                yield node, var, "not <read>"
        elif isinstance(node, ast.BoolOp):
            for value in node.values:
                var = _env_var_name(value, prefixes)
                if var:
                    yield node, var, "truthiness in and/or"
        elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
            var = _env_var_name(node.test, prefixes)
            if var:
                yield node, var, "truthiness in a condition"
        elif isinstance(node, ast.Compare) and node.comparators:
            var = _env_var_name(node.left, prefixes)
            if var is None:
                continue
            op = node.ops[0]
            right = node.comparators[0]
            if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Constant) and isinstance(right.value, str):
                yield node, var, f"compared with {right.value!r}"
            elif isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, (ast.Set, ast.List, ast.Tuple)):
                yield node, var, "membership in a literal collection"


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
    files: Iterable[Path], repo_root: Path, prefixes: Sequence[str], allowed: Mapping[str, str] | None = None, min_files: int = 1
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
    if scan.unparsed:
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
