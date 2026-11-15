"""Kernels written in Python with ``numba.cuda`` are checked for the four mistakes that corrupt results silently; every C++-string CUDA gate is blind to them.

The CUDA gates of this package read ``__global__`` text. A ``@cuda.jit`` function is Python: mlframe holds 21 such kernels (and 5 device helpers) in 9 files, and none of the gates sees
them (audit 2026-11-16, finding 53-T7). This gate reads the Python AST. A kernel is a function decorated with ``numba.cuda.jit`` however the import is spelled: ``from numba import cuda``
(also ``as _nb_cuda``), ``import numba.cuda as cuda``, ``import numba`` + ``numba.cuda.jit``, ``from numba.cuda import jit``; the decorator may carry arguments
(``@cuda.jit(cache=True)``, ``@cuda.jit("void(float32[:])")``). ``cuda.jit(device=True)`` is a device helper, not a kernel: it is never launched, so the launch-shape rules do not
apply to it (only the float rule reads it). Kernels nested in a factory (the mlframe habit: a ``_make_kernel()`` closure) are found; the body of a nested function inside a kernel is not
part of it. ``cuda.jit(sig)(func)`` called as a function, rather than used as a decorator, is not discovered.

Rules (all hard; every one is sound by construction, and narrow: where the code could be right for a reason the AST cannot see, the rule stays silent):

* ``numba-cuda-unguarded-grid-index``: a kernel parameter is subscripted with a global thread index (``i = cuda.grid(1)``, ``blockIdx.x * blockDim.x + threadIdx.x``, or a name derived
  from one, e.g. ``p = tid // n``) and nothing bounds that index: no enclosing ``if``/``while`` or ``for`` whose test or range mentions a global-index name, no earlier ``if`` on one that
  returns, no ``and``/``or``/conditional expression on one in the same statement. The grid is rounded UP to whole blocks, so the last block's tail threads index past the array end: an
  illegal memory access, or silent corruption of the next allocation. A kernel launched with exactly as many threads as elements (``n == blocks * threads``) is right without a guard and
  must say so with the marker.
* ``numba-cuda-divergent-syncthreads``: ``cuda.syncthreads()`` (or ``syncthreads_count/and/or``) inside an ``if``/``while`` whose test depends on ``threadIdx``/``cuda.grid``/``laneid``
  (or a name derived from them), inside a ``for`` whose range does, or after an ``if`` of that kind that contains a ``return``. A barrier that not every thread of the block reaches is
  undefined behaviour: a hang, or a barrier that releases early on the surviving threads and lets them read shared memory before it is written. ``blockIdx``-dependent branches are
  block-uniform and are not reported.
* ``numba-cuda-unsynchronised-rmw``: in a kernel, a read-modify-write (``acc[0] += x`` or ``acc[k] = acc[k] + x``) of a kernel parameter or a ``cuda.shared.array`` where the index is
  built only from constants, kernel parameters that are never rebound, ``blockIdx``/``blockDim``/``gridDim`` and ``.shape`` entries, so EVERY thread of a block hits the same cell, and
  the statement is not under an ``if``/``while`` on a thread-dependent test (``if lane == 0:``) nor after an early ``return`` on one. Concurrent plain ``+=`` loses updates; use
  ``cuda.atomic.add`` or reduce into a per-thread cell first. A plain store that does not read the cell (``out[0] = 1``) is not a read-modify-write and is not reported; a kernel
  launched with one thread per block is right and must say so with the marker.
* ``numba-cuda-float64-in-float32-kernel``: a function that spells float32 (``np.float32``, ``numba.float32``) AND float64 (``np.float64``, ``numba.float64``, ``np.double``), or whose
  explicit ``cuda.jit`` signature string says float32 with no float64, and then spells float64 in the body: on a consumer card FP64 runs at 1/32 to 1/64 of the FP32 rate, and the
  mix is the signature of a literal that was meant to follow the kernel's float type. Every float64 spelling of such a function is reported. A kernel that spells float64 alone is not
  reported (float64 may be the whole point; the C++ gates cover that question), nor is a float type held in a variable (``ft = np.float64`` in a factory).

Evaluated and rejected:

* Literal launch geometry (``kern[blocks, 256](...)``): ``cuda_launch_geometry`` already reports the numba form (``kern[blocks, 256](x)`` is its loose launch); a second rule would
  duplicate every finding.
* A "stride read on ``threadIdx.x``" rule (the proposal text of 53-T7): whether a ``x[tid * k]`` read is uncoalesced depends on the array layout and the launch, which the AST does not
  hold; the measured C++ analogue (``cuda_kernel_coalesced_reads``) needed a hand-tuned exemption list for the same reason.
* A "32-bit index product" rule (``cuda.grid(1) * n``): numba infers int64 for Python integers, so the overflow of the C++ ``int`` does not exist here unless the operand is an explicit
  ``np.int32``; a rule for that spelling was not measured and would be a guess.
* ``cuda.atomic.add`` missing on a data-dependent index (``hist[cls[i]] += 1``): whether two threads can share the cell depends on the data; only the thread-uniform index is decidable
  (rule 3).
* Float64 anywhere in a kernel: several kernels spell float64 on purpose (Pearson accumulators, MI statistics, DTW distances that must equal the CPU result; read in the source, not counted); the unmixed form is not
  decidable from the AST.

Suppression: ``# numba-cuda-ok: <reason>`` on the line of the finding, or on any line of the kernel's decorator and ``def`` header to cover the whole kernel. The reason is the review (why the
launch is exact, why one thread per block, why float64 is meant) and must be non-empty; an empty reason does not suppress.

Measured on the mlframe corpus (2026-11-16 snapshot, 25 kernels and 5 device helpers found by this discovery; the audit counted 21 and 5 on an earlier snapshot): 0 findings from all four rules.
The rules are therefore regression guards on that corpus; their seeded shapes are pinned by ``tests/test_numba_cuda_kernels.py`` and the canary.

Usage in a consumer's meta test::

    from py_ci_shared.numba_cuda_kernels import assert_numba_cuda_kernels

    def test_numba_cuda_kernels():
        assert_numba_cuda_kernels("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = [
    "MARKER",
    "RULE_DIVERGENT_SYNC",
    "RULE_FLOAT",
    "RULE_RMW",
    "RULE_UNGUARDED",
    "assert_numba_cuda_kernels",
    "find_numba_cuda_kernels",
    "kernel_functions",
]

MARKER = "numba-cuda-ok:"
RULE_UNGUARDED = "numba-cuda-unguarded-grid-index"
RULE_DIVERGENT_SYNC = "numba-cuda-divergent-syncthreads"
RULE_RMW = "numba-cuda-unsynchronised-rmw"
RULE_FLOAT = "numba-cuda-float64-in-float32-kernel"

_MARKER_RE = re.compile(r"#[^\n]*numba-cuda-ok:[ \t]*\S")
_JIT = "numba.cuda.jit"
_SYNCS = frozenset({"numba.cuda.syncthreads", "numba.cuda.syncthreads_count", "numba.cuda.syncthreads_and", "numba.cuda.syncthreads_or"})
_THREAD_SOURCES = ("numba.cuda.threadIdx", "numba.cuda.grid", "numba.cuda.laneid")
_BLOCK_SOURCES = ("numba.cuda.blockIdx",)
_UNIFORM_SOURCES = ("numba.cuda.blockIdx", "numba.cuda.blockDim", "numba.cuda.gridDim")
_F32 = frozenset({"numpy.float32", "numba.float32", "numba.types.float32", "cupy.float32"})
_F64 = frozenset({"numpy.float64", "numba.float64", "numba.types.float64", "cupy.float64", "numpy.double"})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _is(q: Optional[str], prefixes: "tuple[str, ...]") -> bool:
    return q is not None and any(q == p or q.startswith(p + ".") for p in prefixes)


def _own_nodes(fn: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> "list[ast.AST]":
    """Every node of the function's own body (nested function, class and lambda bodies are other scopes; the nested definition node itself is included)."""
    out: "list[ast.AST]" = []
    stack: "list[ast.AST]" = list(reversed(fn.body))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, _SCOPES):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))
    return out


def _walk_expr(node: ast.AST) -> "list[ast.AST]":
    """``ast.walk`` that does not enter a nested lambda."""
    out: "list[ast.AST]" = []
    stack = [node]
    while stack:
        cur = stack.pop()
        out.append(cur)
        if isinstance(cur, ast.Lambda):
            continue
        stack.extend(ast.iter_child_nodes(cur))
    return out


@dataclass(frozen=True)
class _Kernel:
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    device: bool
    signature: str  # the text of an explicit string signature given to cuda.jit, "" when none


def _decorator_kind(dec: ast.expr, aliases: ImportAliases) -> "Optional[tuple[bool, str]]":
    """``(is_device, signature_text)`` when *dec* is ``numba.cuda.jit`` (bare or called), else ``None``."""
    call = dec if isinstance(dec, ast.Call) else None
    target = call.func if call is not None else dec
    if aliases.qualified_name(target) != _JIT:
        return None
    device = False
    signature = ""
    if call is not None:
        for kw in call.keywords:
            if kw.arg == "device" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                device = True
        for arg in call.args[:1]:
            parts = [arg] if not isinstance(arg, (ast.List, ast.Tuple)) else list(arg.elts)
            signature = " ".join(p.value for p in parts if isinstance(p, ast.Constant) and isinstance(p.value, str))
    return device, signature


def kernel_functions(tree: ast.Module, aliases: ImportAliases) -> "list[_Kernel]":
    """Every ``numba.cuda.jit`` function of the module (kernels and device helpers), nested ones included, in source order."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                kind = _decorator_kind(dec, aliases)
                if kind is not None:
                    out.append(_Kernel(node, kind[0], kind[1]))
                    break
    return sorted(out, key=lambda k: k.node.lineno)


# ---- taint: which names depend on the thread, the block, the global thread index -------------------------------------------------------


@dataclass
class _Taint:
    thread: "set[str]"  # varies between the threads of one block (threadIdx, cuda.grid, laneid, and names derived from them)
    block: "set[str]"
    glob: "set[str]"  # a global thread index (cuda.grid, or block and thread parts combined)


def _flags(expr: ast.AST, taint: _Taint, aliases: ImportAliases) -> "tuple[bool, bool, bool]":
    thread = block = glob = False
    for n in _walk_expr(expr):
        if isinstance(n, (ast.Name, ast.Attribute)):
            q = aliases.qualified_name(n)
            if _is(q, ("numba.cuda.grid",)):
                thread = glob = True
            elif _is(q, _THREAD_SOURCES):
                thread = True
            elif _is(q, _BLOCK_SOURCES):
                block = True
        if isinstance(n, ast.Name):
            thread = thread or n.id in taint.thread
            block = block or n.id in taint.block
            glob = glob or n.id in taint.glob
    return thread, block, glob or (thread and block)


def _target_names(target: ast.AST) -> "list[str]":
    """The names a target BINDS: a plain name, or the names inside a tuple/list/starred; ``a[i] = ...`` and ``a.x = ...`` bind nothing."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for e in target.elts for name in _target_names(e)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _taint_of(nodes: "list[ast.AST]", aliases: ImportAliases) -> _Taint:
    sites: "list[tuple[list[str], ast.AST, bool]]" = []  # (names, value, is_loop_target)
    for n in nodes:
        if isinstance(n, ast.Assign):
            sites.append(([name for t in n.targets for name in _target_names(t)], n.value, False))
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            sites.append((_target_names(n.target), n.value, False))
        elif isinstance(n, ast.AugAssign):
            sites.append((_target_names(n.target), n.value, False))
        elif isinstance(n, ast.For):
            sites.append((_target_names(n.target), n.iter, True))
    taint = _Taint(set(), set(), set())
    changed = True
    while changed:
        changed = False
        for names, value, loop in sites:
            thread, block, glob = _flags(value, taint, aliases)
            for flag, bag in ((thread, taint.thread), (block, taint.block), (glob and not loop, taint.glob)):
                if flag and not set(names) <= bag:
                    bag.update(names)
                    changed = True
    return taint


# ---- statement walk with the control context ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Ctx:
    conds: "tuple[tuple[str, ast.AST], ...]" = ()  # (kind, expr) of the enclosing if/while tests and for iterators
    exits: "tuple[ast.AST, ...]" = ()  # tests of earlier sibling ifs that contain a return


def _has_return(stmt: ast.AST) -> bool:
    stack = [stmt]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Return):
            return True
        if isinstance(n, _SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return False


def _headers(stmt: ast.stmt) -> "list[ast.AST]":
    """The expressions a statement itself evaluates (a compound statement's header, not its body)."""
    if isinstance(stmt, (ast.If, ast.While)):
        return [stmt.test]
    if isinstance(stmt, ast.For):
        return [stmt.target, stmt.iter]
    if isinstance(stmt, ast.With):
        return [i for i in stmt.items]
    if isinstance(stmt, ast.Try) or isinstance(stmt, _SCOPES):
        return []
    return [stmt]


def _walk_stmts(stmts: "list[ast.stmt]", ctx: _Ctx, visit) -> None:  # type: ignore[no-untyped-def]
    exits = ctx.exits
    for stmt in stmts:
        here = _Ctx(ctx.conds, exits)
        visit(stmt, here)
        if isinstance(stmt, _SCOPES):
            continue
        if isinstance(stmt, ast.If):
            inner = _Ctx((*ctx.conds, ("if", stmt.test)), exits)
            _walk_stmts(stmt.body, inner, visit)
            _walk_stmts(stmt.orelse, inner, visit)
            if _has_return(stmt):
                exits = (*exits, stmt.test)
        elif isinstance(stmt, ast.While):
            inner = _Ctx((*ctx.conds, ("while", stmt.test)), exits)
            _walk_stmts(stmt.body, inner, visit)
            _walk_stmts(stmt.orelse, inner, visit)
        elif isinstance(stmt, ast.For):
            inner = _Ctx((*ctx.conds, ("for", stmt.iter)), exits)
            _walk_stmts(stmt.body, inner, visit)
            _walk_stmts(stmt.orelse, inner, visit)
        elif isinstance(stmt, ast.Try):
            for block in (stmt.body, stmt.orelse, stmt.finalbody, *[h.body for h in stmt.handlers]):
                _walk_stmts(block, here, visit)
        elif isinstance(stmt, ast.With):
            _walk_stmts(stmt.body, here, visit)


def _mentions(expr: ast.AST, names: "set[str]") -> bool:
    return any(isinstance(n, ast.Name) and n.id in names for n in _walk_expr(expr))


def _thread_dependent(expr: ast.AST, taint: _Taint, aliases: ImportAliases) -> bool:
    return _flags(expr, taint, aliases)[0]


# ---- the rules -------------------------------------------------------------------------------------------------------------------------


def _params(fn: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> "set[str]":
    a = fn.args
    return {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}


def _stores(nodes: "list[ast.AST]") -> "dict[str, int]":
    counts: "dict[str, int]" = {}
    for n in nodes:
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            counts[n.id] = counts.get(n.id, 0) + 1
        elif isinstance(n, ast.arg):
            counts[n.arg] = counts.get(n.arg, 0) + 1
    return counts


def _uniform_names(fn: Union[ast.FunctionDef, ast.AsyncFunctionDef], nodes: "list[ast.AST]", aliases: ImportAliases) -> "set[str]":
    """Names with the same value in every thread of a block: parameters never rebound, and names bound once by a plain assignment of a uniform expression."""
    stores = _stores(nodes)
    params = _params(fn)
    uniform = {p for p in params if stores.get(p, 0) == 0}
    single: "dict[str, ast.expr]" = {}
    for n in nodes:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if name not in params and stores.get(name, 0) == 1:
                single[name] = n.value
    changed = True
    while changed:
        changed = False
        for name, value in single.items():
            if name not in uniform and _is_uniform(value, uniform, params, aliases):
                uniform.add(name)
                changed = True
    return uniform


def _is_uniform(expr: ast.AST, uniform: "set[str]", params: "set[str]", aliases: ImportAliases) -> bool:
    if isinstance(expr, ast.Constant):
        return True
    if isinstance(expr, ast.Name):
        return expr.id in uniform
    if isinstance(expr, ast.Attribute):
        return _is(aliases.qualified_name(expr), _UNIFORM_SOURCES)
    if isinstance(expr, ast.Subscript):
        value = expr.value
        if _is(aliases.qualified_name(value), _UNIFORM_SOURCES):
            return True  # blockIdx.x written as a subscript never occurs, kept for completeness
        return (
            isinstance(value, ast.Attribute)
            and value.attr == "shape"
            and isinstance(value.value, ast.Name)
            and value.value.id in uniform
            and _is_uniform(expr.slice, uniform, params, aliases)
        )
    if isinstance(expr, ast.BinOp):
        return _is_uniform(expr.left, uniform, params, aliases) and _is_uniform(expr.right, uniform, params, aliases)
    if isinstance(expr, ast.UnaryOp):
        return _is_uniform(expr.operand, uniform, params, aliases)
    if isinstance(expr, ast.Tuple):
        return all(_is_uniform(e, uniform, params, aliases) for e in expr.elts)
    return False


def _index_elements(sub: ast.Subscript) -> "list[ast.AST]":
    s = sub.slice
    return list(s.elts) if isinstance(s, ast.Tuple) else [s]


@dataclass
class _KernelInfo:
    k: _Kernel
    aliases: ImportAliases
    rel: str
    taint: _Taint
    params: "set[str]"
    shared: "set[str]"
    uniform: "set[str]"

    @property
    def fn(self) -> Union[ast.FunctionDef, ast.AsyncFunctionDef]:
        return self.k.node

    def divergent(self, expr: ast.AST) -> bool:
        return _thread_dependent(expr, self.taint, self.aliases)


def _sync_findings(info: _KernelInfo, walked: "list[ast.AST]", ctx: _Ctx) -> "list[Finding]":
    cond_bad = [c for _kind, c in ctx.conds if info.divergent(c)]
    exit_bad = [c for c in ctx.exits if info.divergent(c)]
    if not (cond_bad or exit_bad):
        return []
    why = "a thread-dependent branch or loop" if cond_bad else "an early `return`"
    shown = _short((cond_bad or exit_bad)[0])
    return [
        Finding(
            info.rel,
            n.lineno,
            RULE_DIVERGENT_SYNC,
            f"`{info.fn.name}`: this barrier sits under {why} (`{shown}`), so not every thread of the block reaches it: a hang or an early release; "
            "hoist the barrier out of the divergent region (guard the work, not the barrier)",
        )
        for n in walked
        if isinstance(n, ast.Call) and info.aliases.qualified_name(n) in _SYNCS
    ]


def _unguarded_findings(info: _KernelInfo, stmt: ast.stmt, walked: "list[ast.AST]", ctx: _Ctx) -> "list[Finding]":
    names = info.taint.glob
    if info.k.device or not names:
        return []
    guarded = (
        any(_mentions(c, names) for _kind, c in ctx.conds)
        or any(_mentions(c, names) for c in ctx.exits)
        or any(isinstance(n, (ast.BoolOp, ast.IfExp)) and _mentions(n, names) for n in walked)
        or isinstance(stmt, ast.Assert)
    )
    if guarded:
        return []
    out: "list[Finding]" = []
    seen: "set[str]" = set()
    for n in walked:
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in info.params and n.value.id not in seen:
            if any(_mentions(e, names) for e in _index_elements(n)):
                seen.add(n.value.id)
                out.append(
                    Finding(
                        info.rel,
                        n.lineno,
                        RULE_UNGUARDED,
                        f"`{info.fn.name}`: parameter `{n.value.id}` is indexed with the global thread index (`{_short(n.slice)}`) and no bound check guards it: the grid is rounded up to "
                        "whole blocks, so the tail threads index past the end; add `if i >= n: return` (or mark an exact launch `# numba-cuda-ok: <reason>`)",
                    )
                )
    return out


def _rmw_findings(info: _KernelInfo, stmt: ast.stmt, ctx: _Ctx) -> "list[Finding]":
    if info.k.device or not isinstance(stmt, (ast.AugAssign, ast.Assign)):
        return []
    if any(kind in ("if", "while") and info.divergent(c) for kind, c in ctx.conds) or any(info.divergent(c) for c in ctx.exits):
        return []
    targets = [stmt.target] if isinstance(stmt, ast.AugAssign) else list(stmt.targets)
    out: "list[Finding]" = []
    for t in targets:
        if not (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and (t.value.id in info.params or t.value.id in info.shared)):
            continue
        if isinstance(stmt, ast.Assign) and ast.unparse(t) not in {ast.unparse(m) for m in _walk_expr(stmt.value) if isinstance(m, ast.Subscript)}:
            continue
        if not all(_is_uniform(e, info.uniform, info.params, info.aliases) for e in _index_elements(t)):
            continue
        out.append(
            Finding(
                info.rel,
                stmt.lineno,
                RULE_RMW,
                f"`{info.fn.name}`: `{ast.unparse(t)}` is read and written with an index every thread of the block shares, so concurrent updates are lost; "
                "use `cuda.atomic.add`, reduce into a per-thread cell first, or restrict the update to one thread (`if tid == 0:`)",
            )
        )
    return out


def _kernel_findings(k: _Kernel, aliases: ImportAliases, rel: str) -> "list[Finding]":
    fn = k.node
    nodes = _own_nodes(fn)
    shared = {
        n.targets[0].id
        for n in nodes
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Call)
        and aliases.qualified_name(n.value) == "numba.cuda.shared.array"
    }
    info = _KernelInfo(k, aliases, rel, _taint_of(nodes, aliases), _params(fn), shared, _uniform_names(fn, nodes, aliases))
    out: "list[Finding]" = []

    def visit(stmt: ast.stmt, ctx: _Ctx) -> None:
        exprs = _headers(stmt)
        if not exprs:
            return
        walked = [m for e in exprs for m in _walk_expr(e)]
        out.extend(_sync_findings(info, walked, ctx))
        out.extend(_unguarded_findings(info, stmt, walked, ctx))
        out.extend(_rmw_findings(info, stmt, ctx))

    _walk_stmts(fn.body, _Ctx(), visit)
    out.extend(_float_findings(k, aliases, rel, nodes))
    return out


def _short(node: ast.AST) -> str:
    text = ast.unparse(node)
    return text if len(text) <= 60 else text[:57] + "..."


def _float_findings(k: _Kernel, aliases: ImportAliases, rel: str, nodes: "list[ast.AST]") -> "list[Finding]":
    f32 = False
    f64: "list[ast.AST]" = []
    for n in (m for root in nodes for m in _walk_expr(root)):
        if isinstance(n, (ast.Name, ast.Attribute)):
            q = aliases.qualified_name(n)
            if q in _F32:
                f32 = True
            elif q in _F64:
                f64.append(n)
    sig = k.signature
    if "float32" in sig and "float64" not in sig:
        f32 = True
    if not (f32 and f64):
        return []
    seen: "set[int]" = set()
    out = []
    for n in f64:
        line = getattr(n, "lineno", k.node.lineno)
        if line in seen:
            continue
        seen.add(line)
        out.append(
            Finding(
                rel,
                line,
                RULE_FLOAT,
                f"`{k.node.name}` is a float32 kernel (it spells float32) but spells float64 here (`{_short(n)}`): FP64 runs at 1/32 to 1/64 of FP32 on a consumer card; "
                "use the kernel's float type, or mark the line `# numba-cuda-ok: <reason>` if the double is meant",
            )
        )
    return out


def _marked(finding_line: int, k: _Kernel, lines: "list[str]") -> bool:
    first = min([k.node.lineno, *[d.lineno for d in k.node.decorator_list]])
    header_end = max(first, k.node.body[0].lineno - 1) if k.node.body else k.node.lineno
    for number in {finding_line, *range(first, header_end + 1)}:
        if 0 < number <= len(lines) and _MARKER_RE.search(lines[number - 1]):
            return True
    return False


def find_numba_cuda_kernels(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every violation of the four numba.cuda kernel rules under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read
    the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        lines = parsed.source.splitlines()
        for k in kernel_functions(parsed.tree, aliases):
            out.extend(f for f in _kernel_findings(k, aliases, parsed.rel) if not _marked(f.line, k, lines))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_numba_cuda_kernels(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any numba.cuda kernel rule violation, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_numba_cuda_kernels(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "fix the kernel as each message says, or mark the line (or the kernel header) `# numba-cuda-ok: <reason>` when the launch or the double is deliberate"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="numba_cuda_kernels", refresh_command="PY_CI_SHARED_REFRESH=numba_cuda_kernels")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} numba.cuda kernel problem(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
