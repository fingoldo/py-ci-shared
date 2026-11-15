"""A CUDA launch grid takes its cap from the device (SM count x resident blocks x waves), not from a literal tuned on one card.

``kernel((min(64, tiles), cols), (256,), args)`` fixes the cap on the number of blocks at a number measured on a 6-SM consumer card. A 100-SM part runs that launch in well under one wave and
idles most of the chip (CUDA.md sections 14, 18.c and 19.3 measured 3.4x for the same class). The cap has to follow ``multiProcessorCount x resident blocks per SM x waves``, or a helper that does.

* ``cuda-literal-grid-cap`` (advisory, use a baseline): in a launch (``f((grid...), (block...), (args...))``, the loose forms of ``cuda_launch_geometry`` and the numba ``kern[grid, block](...)``) the
  grid holds ``min(...)`` with an integer literal operand between 16 and 512 (a module constant bound once to such a literal counts as the literal); the grid may name a local first
  (``blocks = max(1, min(64, ...))`` then ``kernel((blocks,), ...)``: the assignment is followed two hops). The same rule for a module constant named ``*BLOCKS*`` / ``*GRID_X*`` bound to an
  integer literal between 16 and 512 that a launch grid references.

Not reported: a grid expression that derives the SM count (any name or attribute spelled ``sm_count`` / ``multiProcessorCount`` / ``num_sms`` / ``sms`` ...), a cap above 512 (1024 blocks already
fill 128 SMs x 8 and the hardware limits 65535 / 2**31-1 are not caps), a ``max(<literal>, ...)`` (a floor, not a cap), and a constant the launches do not reference (``_GRID_POINTS`` is a numerical grid).
Why advisory: CUDA.md 14.4 records that a cap change reorders float partial sums, so the fix needs a selection-equivalence test and a blind hard gate would force edits of several kernels at once.

Suppression: ``# grid-ok: <reason>`` on any line of the launch call, of the assignment holding the ``min``, or of the constant's assignment. The reason is the review (a hardware limit of
gridDim.y, a grid that is the algorithm's tile count). An empty reason does not suppress.

Measured (finding 55-T3, mlframe snapshot of 2026-11-16): the launch-tuple form gives 6 true hits and 0 false at threshold 512. Following a local name adds the cap sites the first pass missed.
The ``16..512`` window and the name patterns are measured on that corpus; they are not a third-party claim.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_grid_caps import assert_cuda_grid_caps

    def test_cuda_grid_caps_follow_the_device():
        assert_cuda_grid_caps("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, nodes_of, scan_python, walk
from .cuda_launch_geometry import _bindings, _int_literal, _launch_block

__all__ = ["MARKER", "RULE", "assert_cuda_grid_caps", "find_cuda_grid_caps"]

RULE = "cuda-literal-grid-cap"
MARKER = "grid-ok:"
_MIN_CAP, _MAX_CAP = 16, 512
_SM_NAME = re.compile(r"(^|_)(sms?|multiprocessors?|multiprocessorcount)(_|$)|sm_?count|multi_?processor", re.IGNORECASE)
_CONST_NAME = re.compile(r"BLOCKS|GRID_X", re.IGNORECASE)
_HOPS = 2


def _names_in(node: ast.AST) -> "set[str]":
    return {n.id for n in walk(node) if isinstance(n, ast.Name)} | {n.attr for n in walk(node) if isinstance(n, ast.Attribute)}


def _is_sm_aware(node: ast.AST) -> bool:
    if any(_SM_NAME.search(name) for name in _names_in(node)):
        return True
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and _SM_NAME.search(n.value) for n in walk(node))


def _grid_elements(call: ast.Call) -> "list[ast.AST]":
    """The grid expressions of a launch call, or ``[]`` when *call* is not a launch."""
    if _launch_block(call) is None:
        return []
    func = call.func
    if isinstance(func, ast.Subscript) and isinstance(func.slice, ast.Tuple) and func.slice.elts:
        grid: ast.AST = func.slice.elts[0]
    elif call.args:
        grid = call.args[0]
    else:
        return []
    return list(grid.elts) if isinstance(grid, ast.Tuple) else [grid]


def _literal(node: ast.AST, scopes: "list[dict[str, Optional[int]]]") -> Optional[int]:
    if isinstance(node, ast.Name):
        for scope in scopes:
            if node.id in scope:
                return scope[node.id]
        return None
    return _int_literal(node)


def _min_caps(expr: ast.AST, scopes: "list[dict[str, Optional[int]]]") -> "list[tuple[int, ast.Call]]":
    out = []
    for node in walk(expr):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "min" and len(node.args) >= 2:
            for arg in node.args:
                value = _literal(arg, scopes)
                if value is not None and _MIN_CAP <= value <= _MAX_CAP:
                    out.append((value, node))
    return out


def _marked(lines: "list[str]", first: int, last: int) -> bool:
    for number in range(max(first, 1), min(last, len(lines)) + 1):
        line = lines[number - 1]
        if "#" not in line:
            continue
        tail = line[line.index("#") :]
        position = tail.find(MARKER)
        if position >= 0 and tail[position + len(MARKER) :].strip():
            return True
    return False


class _Function:
    """The assignments of one function body: name -> the (value expression, statement) pairs that bind it."""

    def __init__(self, fn: ast.AST) -> None:
        self.assigns: "dict[str, list[tuple[ast.AST, ast.stmt]]]" = {}
        for stmt in nodes_of(fn, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    self.assigns.setdefault(target.id, []).append((stmt.value, stmt))

    def expand(self, expr: ast.AST, owner: ast.AST, skip: "set[str]") -> "list[tuple[ast.AST, ast.AST]]":
        """*expr* and, up to ``_HOPS`` assignments deep, the values of the local names in it; each paired with the statement or call that owns it."""
        out: "list[tuple[ast.AST, ast.AST]]" = [(expr, owner)]
        frontier = [(expr, owner)]
        seen: "set[str]" = set()
        for _ in range(_HOPS):
            nxt: "list[tuple[ast.AST, ast.AST]]" = []
            for sub, _owner in frontier:
                for name in {n.id for n in walk(sub) if isinstance(n, ast.Name)} - seen - skip:
                    seen.add(name)
                    for value, stmt in self.assigns.get(name, []):
                        nxt.append((value, stmt))
            out.extend(nxt)
            frontier = nxt
        return out


def _block_and_range_names(call: ast.Call, fn_node: "Optional[ast.AST]") -> "set[str]":
    names: "set[str]" = set()
    func = call.func
    if isinstance(func, ast.Subscript) and isinstance(func.slice, ast.Tuple) and len(func.slice.elts) >= 2:
        names |= {n.id for n in walk(func.slice.elts[1]) if isinstance(n, ast.Name)}
    elif len(call.args) > 1:
        names |= {n.id for n in walk(call.args[1]) if isinstance(n, ast.Name)}
    if fn_node is not None:
        for node in nodes_of(fn_node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "range":
                names |= {n.id for arg in node.args for n in walk(arg) if isinstance(n, ast.Name)}
    return names


def _span(node: ast.AST) -> "tuple[int, int]":
    first = getattr(node, "lineno", 1)
    return first, getattr(node, "end_lineno", None) or first


def _cap_findings(
    call: ast.Call, fn: "Optional[_Function]", fn_node: "Optional[ast.AST]", module_scope: "dict[str, Optional[int]]", lines: "list[str]", rel: str, referenced: "set[str]"
) -> "list[Finding]":
    out: "list[Finding]" = []
    for grid in _grid_elements(call):
        # a name that is also a block width or a range() step is a thread count or a chunk, not a grid cap
        skip = _block_and_range_names(call, fn_node)
        expanded = fn.expand(grid, call, skip) if fn is not None else [(grid, call)]
        referenced.update(n.id for sub, _ in expanded for n in walk(sub) if isinstance(n, ast.Name))
        if any(_is_sm_aware(sub) for sub, _ in expanded):
            continue
        for index, (sub, owner) in enumerate(expanded):
            # only the grid expression itself may name its cap through a module constant; behind a local name the literal must be written inline (a chunk size is not a cap)
            for value, cap in _min_caps(sub, [module_scope] if index == 0 else []):
                if any(_marked(lines, *span) for span in (_span(call), _span(owner), _span(cap))):
                    continue
                message = f"this launch grid is capped by the literal {value} in min(...): derive the cap from the device (multiProcessorCount x resident blocks x waves) or mark `# grid-ok: <reason>`"
                out.append(Finding(rel, cap.lineno, RULE, message))
    return out


def _constant_findings(module_assigns: "list[ast.Assign]", module_scope: "dict[str, Optional[int]]", referenced: "set[str]", lines: "list[str]", rel: str) -> "list[Finding]":
    out: "list[Finding]" = []
    for stmt in module_assigns:
        value = _int_literal(stmt.value)
        if value is None or not (_MIN_CAP <= value <= _MAX_CAP) or _marked(lines, *_span(stmt)):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and _CONST_NAME.search(target.id) and target.id in referenced and module_scope.get(target.id) is not None:
                message = f"the module constant {target.id} = {value} is a launch grid size: derive it from the device (multiProcessorCount x resident blocks x waves) or mark `# grid-ok: <reason>`"
                out.append(Finding(rel, stmt.lineno, RULE, message))
    return out


def _findings_in(tree: ast.Module, rel: str, lines: "list[str]") -> "list[Finding]":
    module_assigns = [n for n in tree.body if isinstance(n, ast.Assign)]
    module_scope = _bindings(module_assigns)
    found: "list[Finding]" = []
    referenced: "set[str]" = set()
    seen_calls: "set[int]" = set()
    for fn_node in nodes_of(tree, ast.FunctionDef, ast.AsyncFunctionDef):
        scope = _Function(fn_node)
        for call in nodes_of(fn_node, ast.Call):
            if id(call) not in seen_calls:
                seen_calls.add(id(call))
                found.extend(_cap_findings(call, scope, fn_node, module_scope, lines, rel, referenced))
    for call in nodes_of(tree, ast.Call):
        if id(call) not in seen_calls:
            found.extend(_cap_findings(call, None, None, module_scope, lines, rel, referenced))
    found.extend(_constant_findings(module_assigns, module_scope, referenced, lines, rel))
    unique: "dict[int, Finding]" = {}
    for finding in found:
        unique.setdefault(finding.line, finding)
    return list(unique.values())


def find_cuda_grid_caps(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every launch grid under *root* capped by a literal tuned on one card, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*).
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_grid_caps(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any launch grid capped by a literal, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_grid_caps(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "take the grid cap from the device (multiProcessorCount x resident blocks x waves, or a helper that does; a cap change reorders float sums, so add a selection-equivalence test), or mark it `# grid-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_grid_caps", refresh_command="PY_CI_SHARED_REFRESH=cuda_grid_caps")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA launch grid cap(s) are literals; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
