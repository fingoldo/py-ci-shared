"""A CUDA kernel launch takes its block size from the device (occupancy, registers, shared memory), not from a literal tuned on one card.

``kernel((grid,), (256,), (args...))`` fixes the threads per block at a number measured on the developer's card. The best block size depends on the card (SM count, registers per SM, shared
memory per SM, maximum threads per SM) and on the kernel (registers and shared memory per thread); a 6-SM consumer card and a 100-SM data-centre card want different geometry, and the
literal is wrong on every other card. The block size has to come from the device: occupancy over the kernel's measured ``num_regs`` / ``shared_size_bytes`` and the device attributes, a
tuning-cache sweep over those hardware-valid candidates, or a helper doing that.

* ``cuda-literal-block-size`` (hard): a call shaped like a launch, ``f(grid_tuple, block_tuple, args_tuple_or_list)``, whose block tuple holds an integer literal other than 1, a module
  constant bound to such a literal, or a local name bound once to one. A block of 1 is a one-thread kernel (nothing to choose) and is not reported.

Not reported: a block built from a call, an attribute or arithmetic over names (``(min(256, dev.max_threads),)``, ``(width,)`` from a helper), and any name bound more than once or to
anything but an integer literal.

Suppression: ``# launch-ok: <reason>`` on the first or last line of the call. The reason is the review: why this geometry cannot move (a warp-per-item kernel, a shared array sized by
the block at compile time, a tile shape that is the algorithm).

Usage in a consumer's meta test::

    from py_ci_shared.cuda_launch_geometry import assert_cuda_launch_geometry

    def test_cuda_launches_take_their_block_size_from_the_device():
        assert_cuda_launch_geometry("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, nodes_of, scan_python

__all__ = ["MARKER", "RULE", "assert_cuda_launch_geometry", "find_cuda_launch_geometry"]

RULE = "cuda-literal-block-size"
MARKER = "launch-ok:"


def _int_literal(node: ast.AST) -> Optional[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _bindings(body_nodes: "list[ast.Assign]") -> "dict[str, Optional[int]]":
    """``{name: integer literal}`` for every name bound exactly once, by a plain assignment to an integer literal; a name bound any other way maps to ``None``."""
    seen: "dict[str, list[Optional[int]]]" = {}
    for node in body_nodes:
        value = _int_literal(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                seen.setdefault(target.id, []).append(value)
    return {name: (values[0] if len(values) == 1 else None) for name, values in seen.items()}


def _is_launch(call: ast.Call) -> bool:
    return (
        len(call.args) >= 3
        and isinstance(call.args[0], ast.Tuple)
        and isinstance(call.args[1], ast.Tuple)
        and bool(call.args[1].elts)
        and isinstance(call.args[2], (ast.Tuple, ast.List))
    )


def _fixed_sizes(call: ast.Call, scopes: "list[dict[str, Optional[int]]]") -> "list[int]":
    """The block sizes (other than 1) of a launch call that are an integer literal or a name bound once to one in the innermost scope that binds it."""
    sizes = []
    for element in call.args[1].elts:  # type: ignore[attr-defined]
        value = _int_literal(element)
        if value is None and isinstance(element, ast.Name):
            for scope in scopes:
                if element.id in scope:
                    value = scope[element.id]
                    break
        if value is not None and value != 1:
            sizes.append(value)
    return sizes


def _marked(call: ast.Call, py_lines: "list[str]") -> bool:
    for number in {call.lineno, getattr(call, "end_lineno", None) or call.lineno}:
        if 0 < number <= len(py_lines):
            line = py_lines[number - 1]
            if "#" in line and MARKER in line[line.index("#") :]:
                return True
    return False


def _findings_in(tree: ast.Module, rel: str, py_lines: "list[str]") -> "list[Finding]":
    module_scope = _bindings([n for n in tree.body if isinstance(n, ast.Assign)])
    out: "list[Finding]" = []
    reported: "set[int]" = set()

    def check(call: ast.Call, scopes: "list[dict[str, Optional[int]]]") -> None:
        if id(call) in reported or not _is_launch(call):
            return
        reported.add(id(call))
        sizes = _fixed_sizes(call, scopes)
        if sizes and not _marked(call, py_lines):
            out.append(
                Finding(
                    rel,
                    call.lineno,
                    RULE,
                    f"the block size {tuple(sizes)} of this kernel launch is a literal: derive it from the device (occupancy over the kernel's registers and shared memory and the card's limits) or mark `# launch-ok: <reason>`",
                )
            )

    for fn in nodes_of(tree, ast.FunctionDef, ast.AsyncFunctionDef):
        local = _bindings([n for n in nodes_of(fn, ast.Assign)])
        for call in nodes_of(fn, ast.Call):
            check(call, [local, module_scope])
    for call in nodes_of(tree, ast.Call):
        check(call, [module_scope])
    return out


def find_cuda_launch_geometry(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every kernel launch under *root* whose block size is a literal, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read
    the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        _ = ImportAliases.from_tree(parsed.tree)
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_launch_geometry(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any kernel launch with a literal block size, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_launch_geometry(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "take the block size from the device (occupancy over num_regs / shared_size_bytes and the card's limits, or a helper that does), or mark the launch `# launch-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_launch_geometry", refresh_command="PY_CI_SHARED_REFRESH=cuda_launch_geometry")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA launch(es) with a literal block size; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
