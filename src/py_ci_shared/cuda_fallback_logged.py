"""A handler that catches a device failure and silently finishes on the CPU hides a slowdown: say so (log with numbers, ``log_throttle``, a warning) or re-raise.

mlframe's rule "say so when a gate makes a run slower" and CUDA.md (the OOM or driver fault finishes on the CPU kernel and logs a warning) exist because a silent CPU fallback is how a 50x
regression survived for weeks. The rule is a measured one (audit 2026-11-16, findings 54-T8 / G10): 23 handlers in 20 files of mlframe, 2 of 5 judged real; the false positives were all
"logged in an outer handler or earlier in the function", which the narrowing below and the marker cover. The thresholds are the audit author's measurement, not a third-party claim.

* ``cuda-fallback-not-logged`` (ADVISORY, a ratchet): an ``except`` handler whose ``try`` body is GPU work (it mentions ``cupy`` / ``numba.cuda`` / a configurable alias such as ``cp``, or calls a
  ``*_gpu`` / ``*_cuda`` callable) catches a device error type, a broad ``Exception`` / bare ``except``, or a type the gate cannot name (``except tuple(errs):``), and neither logs nor re-raises.
  Logging is a call on ``logger`` / ``log`` / ``logging`` / ``warnings`` or a callable whose name contains ``log`` or ``warn`` (``log_throttle``, ``_log_fallback``); ``raise`` anywhere in the handler counts.
  Handlers that catch only ``ImportError`` / ``ModuleNotFoundError`` / ``AttributeError`` / ``KeyError`` / ``ValueError`` / ``TypeError`` / ``OSError`` / ``LinAlgError`` (a numerical error, not a device fault) are not reported.

Narrowing (the audit's): a handler whose body is only ``pass`` / ``continue`` / a constant is not reported when (a) a sibling handler of the same ``try`` logs, (b) an enclosing ``try`` has a handler
that logs (the inner handler is best-effort inside an outer one that already said so), or (c) the enclosing function logs before the ``try`` ("over budget: already logged above").

Suppression: ``# fallback-logged-ok: <reason>`` on the ``except`` line or inside the handler; the reason (non-empty) says where the fallback is logged. An empty reason does not suppress.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_fallback_logged import assert_cuda_fallback_logged

    def test_cuda_fallbacks_say_so():
        assert_cuda_fallback_logged("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
from ._core.ast_try import TRY_NODES
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, child_nodes, nodes_of, scan_python, walk

__all__ = ["MARKER", "RULE", "assert_cuda_fallback_logged", "find_cuda_fallback_logged"]

RULE = "cuda-fallback-not-logged"
MARKER = "fallback-logged-ok:"

DEFAULT_GPU_MODULES = ("cupy", "numba.cuda", "cp", "cupyx")
DEFAULT_EXCLUDED_ERRORS = ("ImportError", "ModuleNotFoundError", "AttributeError", "KeyError", "ValueError", "TypeError", "OSError", "LinAlgError")
DEFAULT_BROAD_ERRORS = ("Exception", "BaseException")
DEFAULT_LOG_ROOTS = ("logger", "log", "logging", "warnings", "LOGGER", "LOG", "_logger", "_log")
_GPU_CALLEE = re.compile(r"(^|_)(gpu|cuda)(_|$)", re.IGNORECASE)
_LOG_CALLEE = re.compile(r"log|warn", re.IGNORECASE)
_TRIVIAL = (ast.Pass, ast.Continue, ast.Break)
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name_of(node.value) + "." + node.attr
    return ""


def _caught(handler: ast.ExceptHandler) -> "Optional[list[str]]":
    """The dotted names of the caught types (``""`` for a type the gate cannot name); ``None`` for a bare ``except``."""
    if handler.type is None:
        return None
    nodes = list(handler.type.elts) if isinstance(handler.type, ast.Tuple) else [handler.type]
    return [_name_of(n) for n in nodes]


def _is_gpu_work(body: "list[ast.stmt]", aliases: ImportAliases, gpu_modules: "tuple[str, ...]") -> bool:
    for stmt in body:
        for node in walk(stmt):
            if isinstance(node, ast.Name):
                target = aliases.mapping.get(node.id, node.id)
                if any(target == m or target.startswith(m + ".") for m in gpu_modules):
                    return True
            if isinstance(node, ast.Call):
                callee = _name_of(node.func).rsplit(".", 1)[-1]
                if callee and _GPU_CALLEE.search(callee):
                    return True
    return False


def _is_log_call(node: ast.AST, log_roots: "tuple[str, ...]") -> bool:
    if not isinstance(node, ast.Call):
        return False
    dotted = _name_of(node.func)
    return dotted.split(".", 1)[0] in log_roots or bool(_LOG_CALLEE.search(dotted.rsplit(".", 1)[-1]))


def _logs_or_raises(stmts: "Iterable[ast.AST]", log_roots: "tuple[str, ...]") -> bool:
    return any(isinstance(n, ast.Raise) or _is_log_call(n, log_roots) for stmt in stmts for n in walk(stmt))


def _marked(handler: ast.ExceptHandler, py_lines: "list[str]") -> bool:
    last = getattr(handler, "end_lineno", None) or handler.lineno
    for number in range(handler.lineno, last + 1):
        if 0 < number <= len(py_lines):
            line = py_lines[number - 1]
            if "#" in line:
                comment = line[line.index("#") :]
                at = comment.find(MARKER)
                if at >= 0 and comment[at + len(MARKER) :].strip():
                    return True
    return False


def _parent_map(tree: ast.AST) -> "dict[int, ast.AST]":
    return {id(child): node for node in walk(tree) for child in child_nodes(node)}


def _outer_logs(try_node: ast.Try, parents: "dict[int, ast.AST]", log_roots: "tuple[str, ...]") -> bool:
    child: ast.AST = try_node
    node: Optional[ast.AST] = parents.get(id(try_node))
    while node is not None and not isinstance(node, _FUNCS):
        if isinstance(node, TRY_NODES) and child in node.body and any(_logs_or_raises(h.body, log_roots) for h in node.handlers):
            return True
        child, node = node, parents.get(id(node))
    return False


def _function_logs_before(try_node: ast.Try, parents: "dict[int, ast.AST]", log_roots: "tuple[str, ...]") -> bool:
    node: Optional[ast.AST] = parents.get(id(try_node))
    while node is not None and not isinstance(node, _FUNCS):
        node = parents.get(id(node))
    if node is None:
        return False
    return any(_is_log_call(sub, log_roots) and sub.lineno < try_node.lineno for sub in walk(node))  # type: ignore[attr-defined]


def _findings_in(
    tree: ast.Module,
    rel: str,
    py_lines: "list[str]",
    *,
    gpu_modules: "tuple[str, ...]",
    excluded: "tuple[str, ...]",
    broad: "tuple[str, ...]",
    log_roots: "tuple[str, ...]",
) -> "list[Finding]":
    aliases = ImportAliases.from_tree(tree)
    parents = _parent_map(tree)
    out: "list[Finding]" = []
    for try_node in nodes_of(tree, ast.Try):
        if not _is_gpu_work(try_node.body, aliases, gpu_modules):
            continue
        for handler in try_node.handlers:
            names = _caught(handler)
            if names and all(n.rsplit(".", 1)[-1] in excluded for n in names):
                continue
            if _logs_or_raises(handler.body, log_roots) or _marked(handler, py_lines):
                continue
            trivial = all(isinstance(s, _TRIVIAL) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)) for s in handler.body)
            if trivial and (
                any(h is not handler and _logs_or_raises(h.body, log_roots) for h in try_node.handlers)
                or _outer_logs(try_node, parents, log_roots)
                or _function_logs_before(try_node, parents, log_roots)
            ):
                continue
            caught = "bare except" if names is None else "/".join(n or "<computed type>" for n in names)
            kind = "broad" if names is None or any(n in broad for n in names) else "device"
            out.append(
                Finding(
                    rel,
                    handler.lineno,
                    RULE,
                    f"the {kind} handler `{caught}` around GPU work neither logs nor re-raises: log the fallback with numbers (log_throttle / logger.warning) or mark `# fallback-logged-ok: <where it is logged>`",
                )
            )
    return out


def find_cuda_fallback_logged(
    root: Union[str, Path],
    *,
    gpu_modules: "tuple[str, ...]" = DEFAULT_GPU_MODULES,
    excluded_errors: "tuple[str, ...]" = DEFAULT_EXCLUDED_ERRORS,
    broad_errors: "tuple[str, ...]" = DEFAULT_BROAD_ERRORS,
    log_roots: "tuple[str, ...]" = DEFAULT_LOG_ROOTS,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every silent device-failure handler under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for an unreadable file (unless *allow_unparsed*)."""
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        out.extend(
            _findings_in(
                parsed.tree,
                parsed.rel,
                parsed.source.splitlines(),
                gpu_modules=gpu_modules,
                excluded=excluded_errors,
                broad=broad_errors,
                log_roots=log_roots,
            )
        )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_fallback_logged(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    gpu_modules: "tuple[str, ...]" = DEFAULT_GPU_MODULES,
    excluded_errors: "tuple[str, ...]" = DEFAULT_EXCLUDED_ERRORS,
    broad_errors: "tuple[str, ...]" = DEFAULT_BROAD_ERRORS,
    log_roots: "tuple[str, ...]" = DEFAULT_LOG_ROOTS,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any silent device-failure handler, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_fallback_logged(
        root,
        gpu_modules=gpu_modules,
        excluded_errors=excluded_errors,
        broad_errors=broad_errors,
        log_roots=log_roots,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "log the fallback with numbers (log_throttle / logger.warning) or re-raise, or mark the handler `# fallback-logged-ok: <where it is logged>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_fallback_logged", refresh_command="PY_CI_SHARED_REFRESH=cuda_fallback_logged")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA fallback handler(s) that do not log; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
