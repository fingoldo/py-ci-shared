"""A failed hardware probe must not persist a negative verdict that outlives the process.

Advisory gate. A hardware fingerprint was persisted to disk for 7 days, and the probe behind it was::

    try:
        n = cupy.cuda.runtime.getDeviceCount()
        gpu = {"name": "cuda", "devices": n}
    except Exception:
        gpu = {"name": "no-gpu"}
    CACHE_FILE.write_text(json.dumps({"gpu": gpu}))

A transient failure (the device busy in another process, a driver hiccup, ``CUDA_VISIBLE_DEVICES`` set to opt a worker
out) wrote ``no-gpu`` and every later run for a week believed it, tuned for the CPU, and never touched the working GPU.
:mod:`py_ci_shared.latched_availability_flags` covers the in-process flag; this covers the same verdict once it leaves the
process.

Reported: an ``except`` handler that catches broadly (everything but a bare ``ImportError``/``ModuleNotFoundError``,
which is a genuine and permanent absence) around a ``try`` body that probes hardware (a call or import whose dotted name
holds ``cupy``, ``cuda``, ``nvml``, ``pynvml``, ``device_count``, ``mem_info``, ``get_device``, or whose function name
holds ``gpu``) and whose handler writes, in one of these shapes, a payload carrying a negative marker:

* ``json.dump``/``pickle.dump``/``yaml.dump``/``yaml.safe_dump``;
* ``<path>.write_text``/``write_bytes``;
* ``.write(...)`` in a handler that also calls ``open(..., "w"/"a"/"x")``;
* ``.set``/``.put``/``.save`` on an object whose name says cache, store or persist.

A negative marker is a string holding ``no-gpu``/``no_gpu``/``nogpu``/``no gpu``/``cpu``/``unavailable``/``none``/``0 devices``, or
the constant ``False``/``None``, found anywhere in the written expression, or in a local the handler bound to one. The
same defect one step removed is reported too: the handler binds such a verdict to a name that a persisting call AFTER the
``try`` in the same function writes.

Not reported: a handler that only logs or re-raises, a handler that persists nothing, a negative verdict kept in a local
that is never written, and a bare ``ImportError`` handler. Not followed: a verdict returned to a caller that persists it
(``return "no-gpu"``) and environment-variable assignments. Known false positives: a handler that writes a CPU
fallback deliberately as a short-lived marker with its own expiry, and a negative that is a legitimate steady state (a
config file recording that the user disabled the GPU). Suppress with ``# probe-ok: <reason>`` on the ``except`` line or
on the persisting call. The fix is to persist only a positive probe, or to store the failure with a short expiry and no
verdict.

Usage in a consumer's meta test::

    from py_ci_shared.persisted_negative_probe import assert_persisted_negative_probe

    def test_failed_probes_do_not_persist_negative_verdicts():
        assert_persisted_negative_probe("src", min_files=50)
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from ._core.node_index import walk as _fast_walk
from ._gate_report import line_has_marker

__all__ = ["RULE", "MARKER", "assert_persisted_negative_probe", "find_persisted_negative_probe"]

RULE = "persisted-negative-probe"
MARKER = "probe-ok"
_PROBE_TOKENS = ("cupy", "cuda", "nvml", "nvidia", "device_count", "mem_info", "get_device", "getdevice")
_NEGATIVE_TEXT = ("no-gpu", "no_gpu", "nogpu", "no gpu", "cpu", "unavailable", "none", "0 devices")
_PERMANENT_ABSENCE = frozenset({"ImportError", "ModuleNotFoundError"})
_DUMPERS = frozenset({"json.dump", "pickle.dump", "yaml.dump", "yaml.safe_dump", "cPickle.dump", "dill.dump", "orjson.dump"})
_FILE_WRITERS = frozenset({"write_text", "write_bytes"})
_CACHE_WRITERS = frozenset({"set", "put", "save"})
_CACHE_RECEIVER = ("cache", "store", "persist")
_TRY_TYPES: tuple[type, ...] = (ast.Try,) + ((getattr(ast, "TryStar"),) if hasattr(ast, "TryStar") else ())


def _own_nodes(nodes: list[ast.stmt]) -> Iterator[ast.AST]:
    """Nodes under *nodes* without descending into nested functions, classes or lambdas."""
    stack: list[ast.AST] = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for a Name/Attribute chain, with a call receiver kept (``f().g`` -> ``f.g``); else ``""``."""
    parts: list[str] = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Name):
            parts.append(node.id)
            break
        else:
            break
    return ".".join(reversed(parts))


def _probes_hardware(body: list[ast.stmt], aliases: ImportAliases) -> bool:
    for node in _own_nodes(body):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or "", *(f"{node.module}.{a.name}" for a in node.names)]
        elif isinstance(node, ast.Attribute):
            names = [_dotted(node)]
        elif isinstance(node, ast.Call):
            names = [_dotted(node.func), aliases.qualified_name(node) or ""]
            if "gpu" in _dotted(node.func).rsplit(".", 1)[-1].lower():
                return True
        for name in names:
            lowered = name.lower()
            if any(token in lowered for token in _PROBE_TOKENS):
                return True
    return False


def _broad(handler: ast.ExceptHandler, aliases: ImportAliases) -> bool:
    """Everything but a handler naming only ``ImportError``/``ModuleNotFoundError``."""
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return not all((aliases.qualified_name(t) or _dotted(t)).rsplit(".", 1)[-1] in _PERMANENT_ABSENCE for t in types)


def _negative_in(expr: ast.AST, bound: dict[str, ast.AST], depth: int = 0) -> str:
    """The first negative marker in *expr* (looking through locals the handler bound, one level deep), else ``""``."""
    for node in _fast_walk(expr):
        if isinstance(node, ast.Constant):
            if node.value is False or node.value is None:
                return repr(node.value)
            if isinstance(node.value, (str, bytes)):
                lowered = (node.value if isinstance(node.value, str) else node.value.decode("utf-8", "replace")).lower()
                hit = next((m for m in _NEGATIVE_TEXT if m in lowered), "")
                if hit:
                    return repr(node.value)[:40]
        elif isinstance(node, ast.Name) and depth == 0 and node.id in bound:
            found = _negative_in(bound[node.id], bound, 1)
            if found:
                return found
    return ""


def _is_write_mode(call: ast.Call) -> bool:
    mode: Optional[ast.AST] = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(c in mode.value for c in "wax")


def _persist_calls(nodes: list[ast.stmt], aliases: ImportAliases) -> list[tuple[ast.Call, str, list[ast.expr]]]:
    """``(call, shape, written expressions)`` for each persisting call among *nodes*."""
    calls = [n for n in _own_nodes(nodes) if isinstance(n, ast.Call)]
    opens_for_write = any(isinstance(c.func, ast.Name) and c.func.id == "open" and _is_write_mode(c) for c in calls)
    out: list[tuple[ast.Call, str, list[ast.expr]]] = []
    for call in calls:
        qualified = aliases.qualified_name(call) or _dotted(call.func)
        attr = call.func.attr if isinstance(call.func, ast.Attribute) else ""
        args = [*call.args, *(k.value for k in call.keywords if k.arg not in ("mode", "encoding"))]
        if qualified in _DUMPERS and call.args:
            out.append((call, qualified, [call.args[0]]))
        elif attr in _FILE_WRITERS:
            out.append((call, f".{attr}", args))
        elif attr == "write" and opens_for_write:
            out.append((call, "open(..., 'w').write", args))
        elif attr in _CACHE_WRITERS and isinstance(call.func, ast.Attribute) and any(r in _dotted(call.func.value).lower() for r in _CACHE_RECEIVER):
            out.append((call, f".{attr}", args))
    return out


def _handler_bindings(handler: ast.ExceptHandler) -> dict[str, ast.AST]:
    bound: dict[str, ast.AST] = {}
    for node in _own_nodes(handler.body):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            bound[node.target.id] = node.value
    return bound


def _names_in(exprs: list[ast.expr]) -> set[str]:
    return {n.id for e in exprs for n in _fast_walk(e) if isinstance(n, ast.Name)}


def _after(statements: list[ast.stmt], line: int) -> list[ast.stmt]:
    return [s for s in statements if s.lineno > line]


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, source: str) -> list[Finding]:
    lines = source.splitlines()
    out: list[Finding] = []
    for scope in [tree, *(n for n in _fast_walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]:
        trailing = list(_own_nodes(scope.body))
        for node in trailing:
            if not isinstance(node, _TRY_TYPES):
                continue
            body: list[ast.stmt] = getattr(node, "body", [])
            if not _probes_hardware(body, aliases):
                continue
            end = int(getattr(node, "end_lineno", 0) or getattr(node, "lineno", 0))
            later = [n for n in trailing if isinstance(n, ast.stmt) and n.lineno > end]
            for handler in getattr(node, "handlers", []):
                if not _broad(handler, aliases):
                    continue
                hit = _verdict(handler, aliases, later)
                if hit is None:
                    continue
                call, shape, marker = hit
                if line_has_marker(lines, handler.lineno, MARKER) or line_has_marker(lines, call.lineno, MARKER):
                    continue
                out.append(
                    Finding(
                        rel,
                        handler.lineno,
                        RULE,
                        f"except handler around a hardware probe persists a negative verdict ({marker}) via {shape} at line {call.lineno}; "
                        "a transient probe failure then pins it for the lifetime of the cache",
                    )
                )
    return out


def _verdict(handler: ast.ExceptHandler, aliases: ImportAliases, later: list[ast.stmt]) -> Optional[tuple[ast.Call, str, str]]:
    """The first persisting call that writes a negative verdict for this handler, or ``None``."""
    bound = _handler_bindings(handler)
    for call, shape, written in _persist_calls(handler.body, aliases):
        for expr in written:
            marker = _negative_in(expr, bound)
            if marker:
                return call, shape, marker
    negative_names = {name for name, value in bound.items() if _negative_in(value, {})}
    if negative_names:
        for call, shape, written in _persist_calls(later, aliases):
            shared = _names_in(written) & negative_names
            if shared:
                return call, shape, f"{sorted(shared)[0]} = {_negative_in(bound[sorted(shared)[0]], {})}"
    return None


def find_persisted_negative_probe(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every broad ``except`` around a hardware probe that persists a negative verdict, under *root*, by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_persisted_negative_probe(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_persisted_negative_probe(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "persist only a positive probe result (or a failure with a short expiry and no verdict); `# probe-ok: <reason>` for a deliberate negative"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="persisted_negative_probe", refresh_command="PY_CI_SHARED_REFRESH=persisted_negative_probe")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} persisted-negative-probe finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
