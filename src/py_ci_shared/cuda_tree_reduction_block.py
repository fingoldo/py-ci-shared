"""A block-size helper whose result feeds a CUDA tree-reduction kernel must return a power of two.

``pyutilz.performance.gpu_hw_launch.block_size_for`` defaults to ``pow2=False`` and returns widths such as 96, 224 or 384 on realistic register and shared-memory pairs. A kernel that
halves its block (``for (int s = nt >> 1; s > 0; s >>= 1) { if (tid < s) sh[tid] += sh[tid + s]; }``) silently drops terms at such a width: 224 ones sum to 128, 768 ones to 512 (measured, finding
55-T2; mlframe avoids it today through ``pow2=True``, so this is a regression guard, not a defect hunt).

* ``cuda-tree-block-not-pow2`` (hard): a call ``block_size_for(K, ...)`` without ``pow2=True`` whose first argument ``K`` is a name that THIS module binds, through an assignment (in the same function,
  or at module level) or through a module-level function it calls, to a kernel named by a string literal (``cp.RawKernel(SRC, "rowsum")``, ``mod.get_function("rowsum")``), where a string constant of this module
  defines ``__global__ void rowsum(`` with a halving loop (``s >>= 1`` / ``s /= 2`` as the loop step, started from a width that is not a literal: a warp-shuffle loop from 16 is not a block tree) and no width contract (an ``x & (x - 1)`` guard, ``__trap``, or ``pow2-ok:``).

The launch half of finding 55-T2 as the report specified it (match a launch to its kernel at file level) gave 9 of 9 false positives; this gate binds the CALL to the kernel by name inside one
module and stays silent on anything it cannot bind (a kernel passed as a parameter, taken from a dict, defined in another module).

Not reported: ``pow2=True`` or any non-literal ``pow2=`` argument, a result that is assigned more than once in its function (the caller adjusts it), a call wrapped in another call
(``min(cap, block_size_for(...))``), a name that cannot be bound, a kernel declaring its contract.

Suppression: ``# pow2-ok: <reason>`` on a line of the call (how the host guarantees a power of two); ``// pow2-ok: <reason>`` in the kernel text declares the contract there. An empty reason does not suppress.

Measured on the mlframe snapshot of 2026-11-16: 0 hits. This is a cheap regression guard (documented as such, canary proves it fires); the one candidate the first draft reported, a warp-shuffle
`for (off = 16; off > 0; off >>= 1)` in `_pairs_analytic_gpu.py`, is why a literal start is excluded.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_tree_reduction_block import assert_cuda_tree_reduction_block

    def test_tree_reduction_kernels_are_launched_with_pow2_blocks():
        assert_cuda_tree_reduction_block("src", min_files=50)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, nodes_of, scan_python, walk

__all__ = ["MARKER", "RULE", "assert_cuda_tree_reduction_block", "find_cuda_tree_reduction_block"]

RULE = "cuda-tree-block-not-pow2"
MARKER = "pow2-ok:"
_HELPER = "block_size_for"
_KERNEL = re.compile(r"__global__\s+(?:__launch_bounds__\s*\([^)]*\)\s*)?void\s+(\w+)\s*\(")
_HALVING = re.compile(r"for\s*\(\s*(?:unsigned\s+|int\s+|uint\w*\s+)?\w+\s*=\s*([^;{}]*);[^;{}]*;\s*\w+\s*(?:>>=\s*1|/=\s*2)\s*\)")
_CONTRACT = re.compile(r"&\s*\(\s*\w+(?:\.\w+)?\s*-\s*1\s*\)|__trap\s*\(|pow2-ok:\s*\S")


def _kernel_bodies(text: str) -> "dict[str, str]":
    out: "dict[str, str]" = {}
    for match in _KERNEL.finditer(text):
        start = text.find("{", match.end())
        if start < 0:
            continue
        depth, i = 0, start
        while i < len(text):
            depth += (text[i] == "{") - (text[i] == "}")
            if depth == 0:
                break
            i += 1
        out[match.group(1)] = text[start : i + 1]
    return out


def _tree_kernels(tree: ast.Module) -> "set[str]":
    names: "set[str]" = set()
    for node in nodes_of(tree, ast.Constant):
        if isinstance(node.value, str) and "__global__" in node.value:
            for name, body in _kernel_bodies(node.value).items():
                if any(not m.group(1).strip().isdigit() for m in _HALVING.finditer(body)) and not _CONTRACT.search(body):
                    names.add(name)
    return names


def _strings(node: ast.AST) -> "set[str]":
    return {n.value for n in walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _marked(lines: "list[str]", node: Any) -> bool:
    first, last = node.lineno, getattr(node, "end_lineno", None) or node.lineno
    for number in range(first, min(last, len(lines)) + 1):
        tail = lines[number - 1]
        if "#" in tail:
            tail = tail[tail.index("#") :]
            position = tail.find(MARKER)
            if position >= 0 and tail[position + len(MARKER) :].strip():
                return True
    return False


def _pow2_arg(call: ast.Call) -> bool:
    """True when the call passes ``pow2=`` as anything but a literal ``False``: ``True`` is the fix, a variable is unknown and not judged."""
    for kw in call.keywords:
        if kw.arg == "pow2":
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
        if kw.arg is None:
            return True  # **kwargs: unknown
    return False


def _helper_call(node: ast.AST) -> "Optional[ast.Call]":
    if isinstance(node, ast.Call):
        func = node.func
        if (isinstance(func, ast.Name) and func.id == _HELPER) or (isinstance(func, ast.Attribute) and func.attr == _HELPER):
            return node
    return None


def _assigns(body: ast.AST) -> "dict[str, list[ast.AST]]":
    out: "dict[str, list[ast.AST]]" = {}
    for stmt in nodes_of(body, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                out.setdefault(target.id, []).append(stmt.value)
    return out


def _module_assigns(tree: ast.Module) -> "dict[str, list[ast.AST]]":
    out: "dict[str, list[ast.AST]]" = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, []).append(stmt.value)
    return out


def _bound_kernels(values: "list[ast.AST]", functions: "dict[str, ast.AST]", trees: "set[str]") -> "set[str]":
    """Tree-kernel names among the string literals of *values* and of the module functions they call."""
    names: "set[str]" = set()
    for value in values:
        names |= _strings(value)
        for node in walk(value):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
                names |= _strings(functions[node.func.id])
    return names & trees


def _unchecked_call(stmt: ast.Assign, local: "dict[str, list[ast.AST]]", lines: "list[str]") -> "Optional[ast.Call]":
    """The ``block_size_for`` call a plain assignment stores unchanged and unmarked, or ``None``."""
    call = _helper_call(stmt.value)  # the result is used as is; a wrapper (bit rounding, a min with a cap) is the caller's own adjustment
    if call is None or not call.args or _pow2_arg(call) or _marked(lines, call):
        return None
    targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
    if not targets or any(len(local.get(t, [])) != 1 for t in targets):
        return None
    return call


def _findings_in(tree: ast.Module, rel: str, lines: "list[str]") -> "list[Finding]":
    trees = _tree_kernels(tree)
    if not trees:
        return []
    functions: "dict[str, ast.AST]" = {fn.name: fn for fn in tree.body if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
    module_assigns = _module_assigns(tree)
    out: "list[Finding]" = []
    for fn in nodes_of(tree, ast.FunctionDef, ast.AsyncFunctionDef):
        local = _assigns(fn)
        for stmt in nodes_of(fn, ast.Assign):
            call = _unchecked_call(stmt, local, lines)
            if call is None or not isinstance(call.args[0], ast.Name):
                continue
            first = call.args[0].id
            bound = _bound_kernels(local.get(first) or module_assigns.get(first) or [], functions, trees)
            if bound:
                message = f"block_size_for({first}, ...) without pow2=True feeds the tree-reduction kernel {sorted(bound)[0]} (a halving loop that drops terms at a width such as 224): pass pow2=True, or declare the width contract and mark `# pow2-ok: <reason>`"
                out.append(Finding(rel, call.lineno, RULE, message))
    return out


def find_cuda_tree_reduction_block(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every ``block_size_for`` call without ``pow2=True`` bound to a tree-reduction kernel of its own module, sorted by path and line."""
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_tree_reduction_block(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any ``block_size_for`` call without ``pow2=True`` whose kernel is a tree reduction of the same module."""
    found = find_cuda_tree_reduction_block(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(
            f"{len(found)} block-size call(s) can return a non-power-of-two width for a tree-reduction kernel; pass pow2=True (or declare the contract in the kernel and mark `# pow2-ok: <reason>`):\n  "
            + "\n  ".join(f.render() for f in found)
        )
