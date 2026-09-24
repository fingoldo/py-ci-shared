"""``value or fallback`` on a setting whose falsy values are meaningful: ``max_tokens=0``, ``timeout=0``, ``seed=0``.

``or`` treats every falsy value as "not given". For most names that is fine; for a declared list of settings it is not:
``0`` is a real seed, ``0`` or ``-1`` a real ``n_jobs``, and for ``max_tokens`` the value ``0`` is the sentinel meaning
"the model's full ceiling". pyutilz's derived request timeout read the size as ``body.get("max_tokens") or
body.get("max_completion_tokens") or 0``, so the recommended way to ask for the full budget also switched the
size-derived timeout off, and a long generation died at the 240 s name default while the model was still working.

The rule is scoped by NAME, not by shape (``optional_truthiness`` covers annotated parameters; pyutilz's
``default_via_or`` covers the general shape with heuristics): an operand of ``or`` that reads a declared setting --
``x.max_tokens``, ``cfg["seed"]``, ``d.get("timeout")``, ``getattr(o, "limit", ...)``, or a bare name -- is reported
unless every operand after it is a falsy constant (``x.seed or 0`` maps 0 to 0, so it changes nothing). A line
carrying ``# falsy-ok: <reason>`` is exempt. Fix with ``x if x is not None else fallback``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ScanResult, scan_python
from ._gate_run import enforce_findings

__all__ = ["DEFAULT_SENTINEL_NAMES", "REFRESH_FLAG", "assert_no_sentinel_or_fallback", "find_sentinel_or_fallback", "sentinel_read"]

REFRESH_FLAG = "--refresh-sentinel-or-fallback-baseline"
GATE = "sentinel-or-fallback"
RULE = "sentinel-or-fallback"
#: Settings whose falsy values are values: token budgets, timeouts, seeds, worker counts, limits, sampling knobs.
DEFAULT_SENTINEL_NAMES = frozenset(
    {"max_tokens", "max_completion_tokens", "max_output_tokens", "timeout", "seed", "random_state", "n_jobs", "limit"}
    | {"temperature", "top_k", "top_p", "max_retries", "retries", "max_rows", "n_samples", "offset"}
)
_DEFAULT_EXCLUDE = frozenset({"tests", "test", "testing"})
_MARKER = re.compile(r"#\s*falsy-ok:\s*\S")


def sentinel_read(node: ast.expr, names: frozenset[str]) -> Optional[str]:
    """The declared setting *node* reads (``x.seed``, ``d["seed"]``, ``d.get("seed")``, ``getattr(x, "seed")``, ``seed``)."""
    if isinstance(node, ast.Name):
        return node.id if node.id in names else None
    if isinstance(node, ast.Attribute):
        return node.attr if node.attr in names else None
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in names:
        return str(node.slice.value)
    if isinstance(node, ast.Call) and node.args:
        key = None
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            key = node.args[0]
        elif isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            key = node.args[1]
        if isinstance(key, ast.Constant) and key.value in names:
            return str(key.value)
    return None


def _falsy_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and not node.value


def find_sentinel_or_fallback(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    names: Iterable[str] = DEFAULT_SENTINEL_NAMES,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)``: one finding per ``or`` operand that reads a declared setting and can be replaced."""
    wanted = frozenset(names)
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    findings: list[Finding] = []
    for f in scan:
        lines = f.source.splitlines()
        for node in ast.walk(f.tree):
            if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
                continue
            if 1 <= node.lineno <= len(lines) and _MARKER.search(lines[node.lineno - 1]):
                continue
            for i, operand in enumerate(node.values[:-1]):
                name = sentinel_read(operand, wanted)
                rest = node.values[i + 1 :]
                if name is None or all(_falsy_constant(r) for r in rest):
                    continue
                message = f"`{ast.unparse(operand)} or {ast.unparse(rest[0])}` replaces a falsy {name} (0 is a value for it)"
                findings.append(Finding(f.rel, node.lineno, RULE, message))
    findings.sort(key=lambda x: (x.path, x.line))
    return findings, scan


def assert_no_sentinel_or_fallback(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    names: Iterable[str] = DEFAULT_SENTINEL_NAMES,
    baseline_path: Optional[Union[str, Path]] = None,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a finding not accepted by *baseline_path*. Missing baseline fails; refresh with ``REFRESH_FLAG`` or
    ``PY_CI_SHARED_REFRESH=sentinel-or-fallback``."""
    findings, scan = find_sentinel_or_fallback(root, names=names, exclude_parts=exclude_parts, use_git=use_git)
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="test for absence (`x if x is not None else fallback`); mark a deliberate one `# falsy-ok: <reason>`",
    )
