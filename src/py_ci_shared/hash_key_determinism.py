"""JSON serialised for a hash, a cache key or a dedup comparison without sorting its keys.

``json.dumps(d)`` follows the dict's insertion order, so two equal dicts built in a different order (a config merged in
another sequence, a payload rebuilt by another code path, another process) serialise differently and hash differently:
the cache silently misses, or a dedup keeps both copies. ``sort_keys=True`` (``orjson.OPT_SORT_KEYS``) makes the text a
function of the value.

A ``json.dumps``/``orjson.dumps``/``simplejson.dumps`` call without key sorting is reported when its result reaches a
hash in the same function:

* directly or through ``.encode()`` as an argument of a ``hashlib`` constructor, ``.update(...)``, ``hash(...)``,
  ``zlib.crc32``/``adler32``, ``xxhash``/``mmh3``/``blake3``, or any callee whose name says hash/digest/fingerprint/
  checksum; or
* through a local name that later reaches one of those; or
* as a subscript or ``.get``/``setdefault``/``pop``/``add`` key of a mapping or set, or assigned to a name ending in
  ``key``/``fingerprint``/``digest``/``hash``. (``x in container`` is not judged: ``json.dumps(s) in script`` is a
  substring test as often as a set lookup.)

Not reported: payloads whose order is the value (a list/tuple display, a comprehension, ``sorted(...)``, a string or
number), as long as no dict literal is inside; and a call whose line carries ``# unsorted-ok: <reason>``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ImportAliases, ParsedFile, ScanResult, scan_python
from ._core.node_index import walk as _fast_walk
from ._gate_run import enforce_findings

__all__ = ["REFRESH_FLAG", "assert_hash_keys_are_deterministic", "find_unsorted_hash_keys"]

REFRESH_FLAG = "--refresh-hash-key-determinism-baseline"
GATE = "hash-key-determinism"
RULE = "unsorted-json-key"
_DUMPS = frozenset({"json.dumps", "orjson.dumps", "simplejson.dumps", "ujson.dumps", "rapidjson.dumps"})
_HASH_PREFIXES = ("hashlib.", "xxhash.", "mmh3.", "blake3.", "zlib.crc32", "zlib.adler32", "binascii.crc32")
_HASHY_NAME = re.compile(r"hash|digest|fingerprint|checksum|crc32|sha\d|md5|blake", re.IGNORECASE)
_KEY_NAME = re.compile(r"(^|_)(key|cache_key|dedup_key|fingerprint|digest|hash)$", re.IGNORECASE)
_MAP_KEY_METHODS = frozenset({"get", "setdefault", "pop", "add", "__contains__"})
_MARKER = re.compile(r"#\s*unsorted-ok:\s*\S")
_DEFAULT_EXCLUDE = frozenset({"tests", "test", "testing"})
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.Module]


def _sorts_keys(call: ast.Call, name: str) -> bool:
    for kw in call.keywords:
        if kw.arg == "sort_keys":
            return not (isinstance(kw.value, ast.Constant) and not kw.value.value)
        if kw.arg == "option" and name.startswith("orjson"):
            return "OPT_SORT_KEYS" in ast.unparse(kw.value)
        if kw.arg is None:
            return True  # **kwargs may carry sort_keys: not provable either way, so not reported
    if name.startswith("orjson") and len(call.args) > 2:
        return "OPT_SORT_KEYS" in ast.unparse(call.args[2])
    return False


def _order_is_the_value(payload: ast.expr) -> bool:
    if any(isinstance(n, (ast.Dict, ast.DictComp)) for n in _fast_walk(payload)):
        return False
    if isinstance(payload, (ast.List, ast.Tuple, ast.ListComp, ast.GeneratorExp, ast.Constant, ast.JoinedStr)):
        return True
    return isinstance(payload, ast.Call) and isinstance(payload.func, ast.Name) and payload.func.id in ("sorted", "list", "tuple", "str", "repr")


def _is_hash_sink(call: ast.Call, aliases: ImportAliases) -> bool:
    name = aliases.qualified_name(call.func) or ""
    if name.startswith(_HASH_PREFIXES) or name == "hash":
        return True
    if isinstance(call.func, ast.Attribute) and call.func.attr == "update":
        return True
    leaf = name.rsplit(".", 1)[-1]
    return bool(_HASHY_NAME.search(leaf))


def _arg_nodes(call: ast.Call) -> Iterable[ast.AST]:
    for a in [*call.args, *(k.value for k in call.keywords)]:
        yield from _fast_walk(a)


class _Scope:
    """One function (or the module body): its own nodes, not nested functions."""

    def __init__(self, fn: _FunctionNode) -> None:
        self.nodes: list[ast.AST] = []
        stack: list[ast.AST] = [s for s in fn.body if not isinstance(s, _NESTED)]
        while stack:
            node = stack.pop()
            self.nodes.append(node)
            stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _NESTED))
        self.parents: dict[int, ast.AST] = {}
        for node in self.nodes:
            for child in ast.iter_child_nodes(node):
                self.parents[id(child)] = node


def _sink_reason(dumps: ast.Call, scope: _Scope, aliases: ImportAliases, tainted_names: dict[str, str]) -> Optional[str]:
    node: ast.AST = dumps
    while id(node) in scope.parents:
        parent = scope.parents[id(node)]
        if isinstance(parent, ast.Call) and node is not parent.func and _is_hash_sink(parent, aliases):
            return f"hashed by {ast.unparse(parent.func)}(...)"
        if isinstance(parent, ast.Subscript) and node is parent.slice:
            return f"used as the key of {ast.unparse(parent.value)}[...]"
        if (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Attribute)
            and parent.func.attr in _MAP_KEY_METHODS
            and parent.args
            and node is parent.args[0]
        ):
            return f"used as the key of {ast.unparse(parent.func)}(...)"
        if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
            target = parent.targets[0].id
            if _KEY_NAME.search(target):
                return f"assigned to key variable `{target}`"
            return tainted_names.get(target)
        if isinstance(parent, (ast.stmt,)):
            return None
        node = parent
    return None


def _tainted_names(scope: _Scope, aliases: ImportAliases) -> dict[str, str]:
    """Local names that reach a hash sink (``h.update(text.encode())``) -> why."""
    out: dict[str, str] = {}
    for n in scope.nodes:
        if isinstance(n, ast.Call) and _is_hash_sink(n, aliases):
            for sub in _arg_nodes(n):
                if isinstance(sub, ast.Name):
                    out.setdefault(sub.id, f"reaches {ast.unparse(n.func)}(...) through `{sub.id}`")
    return out


def _scope_findings(f: ParsedFile, fn: _FunctionNode, aliases: ImportAliases, lines: list[str]) -> list[Finding]:
    scope = _Scope(fn)
    tainted = _tainted_names(scope, aliases)
    where = getattr(fn, "name", "<module>")
    out: list[Finding] = []
    for n in scope.nodes:
        if not (isinstance(n, ast.Call) and n.args):
            continue
        name = aliases.qualified_name(n.func) or ""
        if name not in _DUMPS or _sorts_keys(n, name) or _order_is_the_value(n.args[0]):
            continue
        if 1 <= n.lineno <= len(lines) and _MARKER.search(lines[n.lineno - 1]):
            continue
        reason = _sink_reason(n, scope, aliases, tainted)
        if reason is not None:
            out.append(Finding(f.rel, n.lineno, RULE, f"{where}: `{name}({ast.unparse(n.args[0])[:60]})` without key sorting is {reason}"))
    return out


def find_unsorted_hash_keys(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)`` over the Python files under *root*; tests are excluded by default, since a test that
    builds an unsorted key usually does so to prove the sorted one differs."""
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    findings: list[Finding] = []
    for f in scan:
        aliases = ImportAliases.from_tree(f.tree)
        lines = f.source.splitlines()
        scopes: list[_FunctionNode] = [f.tree, *(n for n in _fast_walk(f.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
        for fn in scopes:
            findings.extend(_scope_findings(f, fn, aliases, lines))
    findings.sort(key=lambda x: (x.path, x.line))
    return findings, scan


def assert_hash_keys_are_deterministic(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on an unsorted JSON hash/cache key not accepted by *baseline_path*. Missing baseline fails; refresh with
    ``REFRESH_FLAG`` or ``PY_CI_SHARED_REFRESH=hash-key-determinism``."""
    findings, scan = find_unsorted_hash_keys(root, exclude_parts=exclude_parts, use_git=use_git)
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="pass sort_keys=True (orjson: option=orjson.OPT_SORT_KEYS) so equal values hash equally",
    )
