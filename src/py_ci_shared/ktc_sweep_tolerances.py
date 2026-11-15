"""A kernel-tuning equivalence sweep must not carry a tolerance far looser than its own claim.

A kernel-tuning-cache sweep (``sweep_backend_grid(..., equiv_rtol=, equiv_atol=)`` in pyutilz) decides, per host, whether a candidate backend is "equivalent enough" to be crowned the fastest. When the variants
are the same arithmetic differing only in floating-point reduction order, their real divergence is a few ULP times the cell count; a literal tolerance orders of magnitude above that can crown a genuinely
divergent kernel (an absolute 5e-2 can exceed the very value compared). The converse failure is recorded in mlframe ``CUDA.md`` section 7: a fixed 1e-9 dropped the GPU variant of the pair-residual kernel as
divergent (its float-term difference is about 1e-8) and the sweep chose the CPU in every cell, so the tolerance has to follow the crit dtype (``max(1e-9, 8 * eps(REAL))``). That second half is not decidable
statically; this gate pins the first (an upper bound on a LITERAL tolerance) and a computed tolerance is never reported.

* ``ktc-loose-sweep-tolerance`` (hard): a call to one of *call_names* whose keyword in *kwarg_names* is a numeric literal above *max_value* (default 1e-6, generous for an FP-reorder twin).

Not reported: a tolerance that is not a literal (a name, a call such as ``max(1e-9, 8 * eps)``, ``float("inf")``), and a sweep comparing genuinely different algorithms, which legitimately needs a loose
tolerance: allow it by ``# ktc-ok: <reason>`` on the first or last line of the call, or by ``allow={"relpath::function": reason}`` (an empty reason allows nothing). Project values (the sweep function
name, the keyword names, the cap, the exempt path parts) are parameters; the defaults are the pyutilz API.

Measured on mlframe (2026-11-16): the same verdict as its ``test_ktc_sweep_tolerances.py``.

Usage in a consumer's meta test::

    from py_ci_shared.ktc_sweep_tolerances import assert_ktc_sweep_tolerances

    def test_no_ktc_sweep_uses_a_loose_equivalence_tolerance():
        assert_ktc_sweep_tolerances("src/mlframe", exclude=("tests", "legacy"), allow={"pkg/mod.py::fn": "rank binner vs percentile binner"})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import DEFAULT_EXCLUDE, Baseline, Finding, child_nodes, nodes_of, scan_python

__all__ = ["MARKER", "RULE", "assert_ktc_sweep_tolerances", "find_ktc_sweep_tolerances"]

RULE = "ktc-loose-sweep-tolerance"
MARKER = "ktc-ok:"


def _called_name(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _literal_tolerances(call: ast.Call, kwarg_names: "frozenset[str]") -> "dict[str, float]":
    out: "dict[str, float]" = {}
    for kw in call.keywords:
        if kw.arg in kwarg_names and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[kw.arg] = float(value)
    return out


def _enclosing(tree: ast.Module) -> "dict[int, str]":
    """``{id(call): name of the innermost function containing it}``; calls outside any function are absent (``<module>``)."""
    owner: "dict[int, str]" = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                owner[id(child)] = name
            visit(child, name)

    visit(tree, "<module>")
    return owner


def _marked(call: ast.Call, lines: "list[str]") -> bool:
    for number in {call.lineno, getattr(call, "end_lineno", None) or call.lineno}:
        if 0 < number <= len(lines):
            line = lines[number - 1]
            if "#" in line:
                comment = line[line.index("#") :]
                if MARKER in comment and comment.split(MARKER, 1)[1].strip():
                    return True
    return False


def find_ktc_sweep_tolerances(
    root: Union[str, Path],
    *,
    call_names: "Iterable[str]" = ("sweep_backend_grid",),
    kwarg_names: "Iterable[str]" = ("equiv_rtol", "equiv_atol"),
    max_value: float = 1e-6,
    exclude: "Iterable[str]" = (),
    allow: "Optional[Mapping[str, str]]" = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every sweep call under *root* with a literal tolerance above *max_value*, sorted by path and line.

    *exclude* names path parts out of scope (added to the shared junk list). *allow* maps ``"relpath::function"`` to a non-empty reason. Raises ``EmptyScanError`` below *min_files* parsed files and
    ``UnparsedFilesError`` for a file that cannot be parsed (unless *allow_unparsed*).
    """
    names, kwargs = frozenset(call_names), frozenset(kwarg_names)
    accepted = {k for k, reason in (allow or {}).items() if str(reason).strip()}
    scan = scan_python(root, min_files=min_files, exclude=set(DEFAULT_EXCLUDE) | set(exclude), use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        calls = [c for c in nodes_of(parsed.tree, ast.Call) if _called_name(c) in names]
        if not calls:
            continue
        owner = _enclosing(parsed.tree)
        lines = parsed.source.splitlines()
        for call in calls:
            loose = {k: v for k, v in _literal_tolerances(call, kwargs).items() if v > max_value}
            if not loose or _marked(call, lines):
                continue
            function = owner.get(id(call), "<module>")
            if f"{parsed.rel}::{function}" in accepted:
                continue
            shown = ", ".join(f"{k}={v:g}" for k, v in sorted(loose.items()))
            out.append(
                Finding(
                    parsed.rel,
                    call.lineno,
                    RULE,
                    f"{_called_name(call)} in {function} uses {shown}, above the cap {max_value:g}: tighten it to the real FP-reorder divergence "
                    f"(follow the crit dtype, e.g. max(1e-9, 8 * eps)) and re-tune, or mark a genuinely different-algorithm comparison `# ktc-ok: <reason>`",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_ktc_sweep_tolerances(
    root: Union[str, Path],
    *,
    call_names: "Iterable[str]" = ("sweep_backend_grid",),
    kwarg_names: "Iterable[str]" = ("equiv_rtol", "equiv_atol"),
    max_value: float = 1e-6,
    exclude: "Iterable[str]" = (),
    allow: "Optional[Mapping[str, str]]" = None,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any sweep with a literal tolerance above *max_value*, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_ktc_sweep_tolerances(
        root, call_names=call_names, kwarg_names=kwarg_names, max_value=max_value, exclude=exclude, allow=allow, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git
    )
    guidance = "a sweep decides which backend a host runs, so a slack tolerance can crown a divergent kernel: tighten it, or allow a different-algorithm comparison with a reason"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="ktc_sweep_tolerances", refresh_command="PY_CI_SHARED_REFRESH=ktc_sweep_tolerances")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} kernel-tuning sweep(s) with a loose literal tolerance; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
