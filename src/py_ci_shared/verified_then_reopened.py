"""A function verifies a file by path (sidecar, hash, checksum), then opens the same path again to load it: a time-of-check/time-of-use gap.

Another process can ``os.replace`` the payload between the check and the open, so the bytes that get unpickled or parsed are not the bytes that
were verified and the integrity check the code advertises protects nothing (pyutilz ``safe_load`` hashed one read and unpickled a second; fixed
by reading once, and the same shape survived in two other loaders). Read the bytes ONCE, verify those bytes, load from the same bytes
(``pickle.loads(payload)``).

Reported, per function: a call whose final name matches *verify_name_regex* (default ``verif|sidecar|checksum|check_hash``) with a path expression
(a name or dotted attribute chain) as its first argument, followed by a later call that opens that same expression: ``open``/``gzip.open``/
``np.load``/``pickle.load``-style callees named in *sink_names* given the path as first argument, or a method on the path itself
(``path.read_bytes()``, ``path.open()``). A re-binding of the path between the two calls ends the match.

A size check (a verify-style name containing ``size`` or ``length``) is not verification: it proves a length, not the bytes, so it does not
start a finding (a truncate that is only size-checked is not a TOCTOU on verified content).

Not reported: the sink receiving bytes from an earlier read (``pickle.loads(payload)``), a different path, a sink before the verify call, a verify
function that is itself the sink's only consumer, a size-only check, and a reopen whose line carries ``# reopen-ok: <reason>`` (a reason is required).

Usage::

    from py_ci_shared.verified_then_reopened import assert_verified_then_reopened

    def test_no_verified_then_reopened():
        assert_verified_then_reopened(REPO / "src")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, child_nodes, scan_python, walk

__all__ = ["DEFAULT_SINK_NAMES", "DEFAULT_VERIFY_REGEX", "RULE", "assert_verified_then_reopened", "find_reopened_after_verify"]

RULE = "verified-then-reopened"
DEFAULT_VERIFY_REGEX = r"verif|sidecar|checksum|check_hash"
DEFAULT_SINK_NAMES: frozenset = frozenset({"open", "load", "read_bytes", "read_text", "read_pickle", "fromfile", "memmap", "read_csv", "read_parquet"})
_METHOD_SINKS = frozenset({"read_bytes", "read_text", "open"})
_SIZE_CHECK = re.compile(r"size|length", re.IGNORECASE)
_REOPEN_OK = re.compile(r"#\s*reopen-ok:\s*\S")


def _final(func: ast.AST) -> Optional[str]:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _expr_key(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _expr_key(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _pos(node: ast.AST) -> tuple:
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _rebinds(fn: ast.AST, key: str, lo: tuple, hi: tuple) -> bool:
    for node in walk(fn):
        targets: list = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for t in targets:
            if _expr_key(t) == key and lo < _pos(node) < hi:
                return True
    return False


def _functions(tree: ast.Module) -> Iterable:
    return [n for n in walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _own_calls(fn: ast.AST) -> list:
    """Calls in *fn* excluding nested function bodies, in source order."""
    calls: list = []
    stack = list(child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call):
            calls.append(node)
        stack.extend(child_nodes(node))
    return sorted(calls, key=_pos)


def _sink_path(call: ast.Call, sinks: frozenset) -> Optional[str]:
    final = _final(call.func)
    if final in sinks and call.args:
        key = _expr_key(call.args[0])
        if key is not None:
            return key
    if final in _METHOD_SINKS and isinstance(call.func, ast.Attribute):
        return _expr_key(call.func.value)
    return None


def _findings_in(tree: ast.Module, rel: str, verify: "re.Pattern[str]", sinks: frozenset, lines: list) -> list:
    out: list = []
    for fn in _functions(tree):
        calls = _own_calls(fn)
        for i, vcall in enumerate(calls):
            name = _final(vcall.func)
            if name is None or not verify.search(name) or _SIZE_CHECK.search(name) or not vcall.args:
                continue
            key = _expr_key(vcall.args[0])
            if key is None:
                continue
            for later in calls[i + 1 :]:
                if later is vcall or _pos(later) <= _pos(vcall) or _sink_path(later, sinks) != key:
                    continue
                if _rebinds(fn, key, _pos(vcall), _pos(later)):
                    break
                if 0 < later.lineno <= len(lines) and _REOPEN_OK.search(lines[later.lineno - 1]):
                    break
                out.append(
                    Finding(
                        rel,
                        later.lineno,
                        RULE,
                        f"{fn.name}: {name}({key}) verifies the path at line {vcall.lineno}, then {_final(later.func)}({key}) opens it again; "
                        "read the bytes once, verify them, and load from those bytes",
                    )
                )
                break
    return out


def find_reopened_after_verify(
    root: Union[str, Path],
    *,
    verify_name_regex: str = DEFAULT_VERIFY_REGEX,
    sink_names: Iterable[str] = DEFAULT_SINK_NAMES,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every verify-then-reopen under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    pattern, sinks = re.compile(verify_name_regex, re.IGNORECASE), frozenset(sink_names)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, pattern, sinks, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_verified_then_reopened(
    root: Union[str, Path],
    *,
    verify_name_regex: str = DEFAULT_VERIFY_REGEX,
    sink_names: Iterable[str] = DEFAULT_SINK_NAMES,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any verify-then-reopen, or with *baseline_path* on any the baseline does not accept."""
    found = find_reopened_after_verify(
        root, verify_name_regex=verify_name_regex, sink_names=sink_names, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git
    )
    guidance = "read the bytes once, verify those bytes, and load from them"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="verified_then_reopened", refresh_command="PY_CI_SHARED_REFRESH=verified_then_reopened")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} verified-then-reopened finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
