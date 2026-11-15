"""A CUDA kernel's global reads are coalesced: neighbouring threads read neighbouring elements, not elements a row length, a stratum length or a variable offset apart.

A warp load touches one 128-byte line when its 32 lanes read consecutive elements and up to 32 lines when they are ``S`` elements apart (about ``1/S`` of the traffic is useful). The check reads the CUDA C++ in every Python
string that contains ``__global__``, finds the variables that depend on ``threadIdx.x`` (the thread index, a global id, a loop started from one), and reports a kernel that reads global memory through them with a stride:

* ``cuda-strided-row-read``: an index ``<thread var> * <non-constant name>`` (a row-major matrix read by the thread-derived row ``X[i * n_features + col]``), also when the row offset is hoisted into a variable first
  (``long long off = i * F; ... X[off + c]``) and also in a kernel with no ``for`` loop (``i = blockIdx.x * blockDim.x + threadIdx.x; X[i * F + c]``). A group index ``threadIdx.x / tile`` next to a column
  ``threadIdx.x % tile`` is not a stride: the ``tile`` neighbouring threads share the row and read adjacent columns of it.
  Also reported (53-T4): the bare thread index as a row (``X[threadIdx.x * n]``), a pointer to a thread's row (``const float* row = X + i * F; row[c]``), a gather row
  (``X[idx[i] * F]``) and a one-argument function-like macro whose replacement multiplies (``#define ROW(i) ((i) * F)`` then ``X[ROW(i) + c]``).
* ``cuda-indirect-run-read``: a thread-derived index into a run table, ``lo = starts[s]; hi = starts[s + 1]; for (r = lo; r < hi; ++r) ... X[r]``: the lanes read runs that start one run length apart (a stratum of 20 rows
  is 20 elements apart: measured gld efficiency 6.7% and 9.3 transactions per request on the kernel this rule was written for).

Shared-memory accesses are not global reads and are ignored (a pointer alias of a shared array too); a fill of a statically sized ``__shared__`` buffer is bounded by its size and is not reported, a fill of a dynamic
``extern __shared__`` array is.

Suppression: ``// coalescing-ok: <reason>`` in the comments of the kernel or in the 400 characters before its ``__global__``. The reason is the review: why this access pattern stays (a kept version with a measured win axis,
a layout shared with a host consumer, a matrix small enough to sit in cache). A baseline (``baseline_path``) holds accepted findings with a note each, keyed ``<path>::<kernel>`` (a second kernel of the same name in one file: ``<path>::<kernel>#2``).
The kernel text is read from the Python AST (the string literals of ``cupy`` kernel classes and of constants holding ``__global__``), so a kernel inside a Python comment or a
docstring is not scanned and the reported line is the line of the kernel's own ``__global__`` (53-T5). The marker needs a reason of at least 12 characters (53-T9 holds already).

Usage in a consumer's meta test::

    from py_ci_shared.cuda_coalesced_reads import assert_cuda_coalesced_reads

    def test_kernel_reads_are_coalesced():
        assert_cuda_coalesced_reads("src", exclude=("_benchmarks",), min_files=100)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from .cuda_kernel_integer_width import _units

__all__ = ["MARKER", "RULE_INDIRECT", "RULE_STRIDED", "assert_cuda_coalesced_reads", "find_cuda_coalesced_reads", "uncoalesced_kernels"]

RULE_STRIDED = "cuda-strided-row-read"
RULE_INDIRECT = "cuda-indirect-run-read"
MARKER = "coalescing-ok:"

_KERNEL_RE = re.compile(r"__global__\s+(?:void\s+)?(?:__launch_bounds__\([^)]*\)\s*)?(?:void\s+)?(\w+)\s*\(")
_TYPES = r"(?:\b(?:unsigned|long|int|size_t|const)\b\s*)"
_ASSIGN_RE = re.compile(rf"{_TYPES}+(\w+)\s*=\s*([^;]+);")
_FOR_RE = re.compile(rf"for\s*\(\s*{_TYPES}*(\w+)\s*=\s*([^;]*);\s*\w+\s*<\s*([^;]*);")
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_CAST = r"(?:\(\s*[A-Za-z_][\w ]*\)\s*)?"
_GROUP_INDEX_RE = re.compile(r"threadIdx\.x\s*(?:/\s*(?!1\b)[A-Za-z_]\w*|/\s*\d+|>>\s*\d+)")
_MARKER_RE = re.compile(rf"{re.escape(MARKER)}[ \t]*\S[^\n]{{11,}}")
_MARKER_WINDOW = 400


def _kernel_bodies(text: str) -> "list[tuple[str, str, int]]":
    """``[(kernel name, body text, offset of its ``__global__``)]`` for every ``__global__`` function in *text* (brace-matched, comments stripped)."""
    out: "list[tuple[str, str, int]]" = []
    for m in _KERNEL_RE.finditer(text):
        # comments are stripped first: a comment in the parameter list can hold an unbalanced ")" such as "[0,Kx)"
        seg = _COMMENT_RE.sub(" ", text[m.end() :])
        depth, i = 1, 0
        while i < len(seg) and depth:
            depth += (seg[i] == "(") - (seg[i] == ")")
            i += 1
        open_at = seg.find("{", i)
        if open_at < 0:
            continue
        depth, j = 0, open_at
        while j < len(seg):
            ch = seg[j]
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                break
            j += 1
        out.append((m.group(1), seg[open_at : j + 1], m.start()))
    return out


def _assignments(body: str) -> "list[tuple[str, str]]":
    """``[(name, expression)]`` of every scalar declaration or initialisation, with a comma list (``const int lo = a[s], hi = a[s + 1];``) split at the top bracket level."""
    out: "list[tuple[str, str]]" = []
    for name, rest in _ASSIGN_RE.findall(body):
        parts: "list[str]" = []
        cur: "list[str]" = []
        depth = 0
        for ch in rest:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        parts.append("".join(cur))
        out.append((name, parts[0].strip()))
        for extra in parts[1:]:
            m = re.fullmatch(r"\s*(\w+)\s*=\s*(.+)", extra, re.S)
            if m:
                out.append((m.group(1), m.group(2).strip()))
    return out


_GROUP_OF_VAR_RE = re.compile(r"(\w+)\s*(?:>>\s*\d+|/\s*\d+|/\s*(?!1\b)[A-Za-z_]\w*)")


def _group_of_derived(expr: str, derived: "set[str]") -> bool:
    """Whether *expr* is a group index of an already thread-derived variable (``tid >> 5``, ``tid / tile``): the neighbouring threads of the group share the value."""
    m = _GROUP_OF_VAR_RE.fullmatch(expr.strip())
    return bool(m and m.group(1) in derived)


def _thread_vars(body: str) -> "set[str]":
    """Variables whose value depends on ``threadIdx.x``, closed over simple assignments."""
    derived: "set[str]" = set()
    changed = True
    while changed:
        changed = False
        for name, expr in _assignments(body):
            if _GROUP_INDEX_RE.fullmatch(expr.strip()) or _group_of_derived(expr, derived):
                continue  # ``threadIdx.x / tile`` or ``threadIdx.x >> 5`` (a warp index): the ``tile`` or 32 neighbouring threads share the value, so they read adjacent columns of one row
            if name not in derived and ("threadIdx.x" in expr or any(re.search(rf"\b{re.escape(v)}\b", expr) for v in derived)):
                derived.add(name)
                changed = True
    return derived


_SHARED_DECL_RE = re.compile(r"__shared__[^;]*;")
_STATIC_SHARED_DECL_RE = re.compile(r"(?<!extern )__shared__[^;]*;")
_SHARED_NAME_RE = re.compile(r"(\w+)\s*\[")
_ALIAS_RE = re.compile(r"[\w}]+\s*\*\s*(\w+)\s*=\s*(?:\([^()]*\*\s*\)\s*)?\(?\s*(\w+)\s*(?:\+[^;]*)?;")  # also ``int* t = (int*)smem;`` and ``{ACC}* se = ({ACC}*)(smem + o);``


def _without_shared_subscripts(body: str) -> str:
    """*body* with every ``__shared__`` array access blanked: shared memory is not a global read. A store into a statically sized shared buffer is dropped as a whole statement (its global read is bounded by the size)."""
    static_names = {n for decl in _STATIC_SHARED_DECL_RE.findall(body) for n in _SHARED_NAME_RE.findall(decl)}
    for name in static_names:
        body = re.sub(rf"\b{re.escape(name)}\s*\[[^\]]*\]\s*=(?!=)[^;]*;", ";", body)
    all_names = {n for decl in _SHARED_DECL_RE.findall(body) for n in _SHARED_NAME_RE.findall(decl)}
    for _ in range(3):  # ``int* ones = sh;`` / ``int* tot = sh + P * nbins;``: a pointer into a shared array is shared memory too
        all_names |= {a for a, base in _ALIAS_RE.findall(body) if base in all_names}
    for name in all_names:
        body = re.sub(rf"\b{re.escape(name)}\s*\[[^\]]*\]", "0", body)
    return body


def _global_ids(body: str, derived: "set[str]") -> "set[str]":
    """Derived variables that are a whole thread id (their expression holds ``threadIdx.x`` and a ``blockIdx`` / ``blockDim`` term, or ``threadIdx.x`` alone): the index of a kernel with no loop."""
    ids = set()
    for name, expr in _assignments(body):
        if name in derived and "threadIdx.x" in expr and not re.search(r"threadIdx\.x\s*[%/&>]", expr):
            ids.add(name)
    return ids


def _opens_with_product(expr: str, var: str) -> bool:
    """Whether *expr* is ``var * name ...`` or ``name * var ...`` (optionally cast or parenthesised) from its first token: a row offset, not a term in the middle of a difference."""
    text = expr.strip().lstrip("(").strip()
    text = re.sub(rf"^{_CAST}", "", text).strip().lstrip("(").strip()
    v = re.escape(var)
    return bool(re.match(rf"{v}\s*\)?\s*\*\s*{_CAST}[A-Za-z_]\w*|[A-Za-z_]\w*\s*\*\s*{_CAST}{v}\b", text))


_POINTER_ASSIGN_RE = re.compile(r"[\w}]+\s*\*\s*(?:const\s+)?(?:__restrict__\s+)?(\w+)\s*=\s*([^;=][^;]*);")
_MACRO_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)\([ \t]*(\w+)[ \t]*\)[ \t]+(.+?)[ \t]*$", re.M)


def _pointer_rows(body: str) -> "list[tuple[str, str]]":
    """``[(pointer name, initialiser)]`` of every pointer declaration with an initialiser (``const float* row = X + i * F;``)."""
    return [(name, expr) for name, expr in _POINTER_ASSIGN_RE.findall(body)]


def _expand_macros(text: str, body: str) -> str:
    """*body* with every one-argument function-like ``#define`` of *text* whose replacement multiplies expanded (``#define ROW(i) ((i) * F)`` makes ``X[ROW(i)]`` a visible stride)."""
    for name, arg, repl in _MACRO_RE.findall(_COMMENT_RE.sub(" ", text)):
        if "*" not in repl:
            continue
        call = re.compile(rf"\b{re.escape(name)}\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)")

        def expand(m: "re.Match[str]", arg: str = arg, repl: str = repl) -> str:
            given = m.group(1).strip()
            value = given if re.fullmatch(r"\w+", given) else "(" + given + ")"
            return "(" + re.sub(rf"\b{re.escape(arg)}\b", lambda _m: value, repl) + ")"

        body = call.sub(expand, body)
    return body


def _strided(body: str, derived: "set[str]") -> bool:
    """Whether a global read is indexed by ``<thread var> * <non-constant>``, directly or through a hoisted offset."""
    loop_vars = [var for var, init, _bound in _FOR_RE.findall(body) if "threadIdx.x" in init or any(re.search(rf"\b{re.escape(v)}\b", init) for v in derived)]
    candidates = list(dict.fromkeys([*loop_vars, *_global_ids(body, derived), "threadIdx.x"]))  # the bare thread index is a row too: ``X[threadIdx.x * n]`` (53-T4)
    pointers = _pointer_rows(body)
    for var in candidates:
        v = re.escape(var)
        stride = re.compile(rf"\b{v}\s*\)?\s*\*\s*{_CAST}[A-Za-z_]\w*|(?<![\w.])[A-Za-z_]\w*\s*\*\s*{_CAST}\b{v}\b")
        if re.search(rf"\[[^\]\[]*?(?:{stride.pattern})[^\]\[]*?\](?!\s*=[^=])", body):
            return True
        # a gather row: ``X[idx[i] * F]``
        gather = rf"\w+\s*\[\s*{v}\s*\]\s*\)?\s*\*\s*{_CAST}[A-Za-z_]\w*|(?<![\w.])[A-Za-z_]\w*\s*\*\s*{_CAST}\w+\s*\[\s*{v}\s*\]"
        if re.search(rf"\[[^\]\[]*?(?:{gather})[^\]\[]*?\](?!\s*=[^=])", body):
            return True
        # a pointer to a thread's row: ``const float* row = X + i * F;`` then ``row[c]``
        for name, expr in pointers:
            if stride.search(expr) and re.search(rf"\b{re.escape(name)}\s*\[[^\]\[]*\](?!\s*=[^=])", body):
                return True
        # a hoisted offset: ``long long off = i * F;`` then ``X[off + c]``
        for name, expr in _assignments(body):
            if name != var and _opens_with_product(expr, var) and re.search(rf"\[[^\]\[]*\b{re.escape(name)}\b[^\]\[]*\](?!\s*=[^=])", body):
                return True
    return False


def _indirect(body: str, derived: "set[str]") -> bool:
    """Whether a loop runs over ``[P[s], P[s + 1])`` of a run table indexed by a thread-derived ``s`` and reads global memory by its variable."""
    assigned = {name: expr.strip() for name, expr in _assignments(body)}
    for s in derived:
        run_start = re.compile(rf"\b\w+\s*\[\s*{re.escape(s)}\s*\]")
        for var, init, bound in _FOR_RE.findall(body):
            init_expr = assigned.get(init.strip(), init.strip())
            bound_expr = assigned.get(bound.strip(), bound.strip())
            if not (run_start.fullmatch(init_expr) and re.fullmatch(rf"\w+\s*\[\s*{re.escape(s)}\s*\+\s*1\s*\]", bound_expr)):
                continue
            if re.search(rf"\b\w+\s*\[[^\]\[]*\b{re.escape(var)}\b[^\]\[]*\](?!\s*=[^=])", body):
                return True
    return False


def _has_marker(text: str, at: int, starts: "list[int]") -> bool:
    """Whether the kernel whose ``__global__`` is at *at* carries a ``coalescing-ok:`` reason: in its own body (up to its closing brace at the start of a line), or in the comments above it that follow the
    closing brace of the previous kernel (at most ``_MARKER_WINDOW`` characters back). A reason written for another kernel does not count."""
    previous = max((s for s in starts if s < at), default=None)
    above_from = max(0, at - _MARKER_WINDOW)
    if previous is not None:
        close = text.rfind("\n}", previous, at)
        above_from = max(above_from, close + 2 if close >= 0 else previous)
    end = text.find("\n}", at)
    own = text[at : (end + 2 if end >= 0 else len(text))]
    return bool(_MARKER_RE.search(text[above_from:at]) or _MARKER_RE.search(own))


def _scan_kernels(text: str) -> "list[tuple[str, str, int]]":
    """``[(kernel name, rule, offset of its ``__global__`` in text)]`` for the uncoalesced kernels of *text*, in source order."""
    if "__global__" not in text:
        return []
    out: "list[tuple[str, str, int]]" = []
    starts = [m.start() for m in _KERNEL_RE.finditer(text)]
    for name, raw_body, at in _kernel_bodies(text):
        if _has_marker(text, at, starts):
            continue
        body = _expand_macros(text, raw_body)
        derived = _thread_vars(body)
        clean = _without_shared_subscripts(body)
        if _strided(clean, derived):
            out.append((name, RULE_STRIDED, at))
        elif _indirect(clean, derived):
            out.append((name, RULE_INDIRECT, at))
    return out


def uncoalesced_kernels(text: str) -> "list[tuple[str, str]]":
    """``[(kernel name, rule)]`` of the kernels in *text* that read global memory with a stride, in source order; a kernel with a ``coalescing-ok:`` reason is left out."""
    return [(name, rule) for name, rule, _at in _scan_kernels(text)]


def find_cuda_coalesced_reads(
    root: Union[str, Path],
    *,
    exclude: "Iterable[str]" = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every kernel under *root* that reads global memory with a stride, one finding per kernel (key ``<path>::<kernel>``), sorted by path and line.

    *exclude* names path parts (``("_benchmarks",)``) out of scope. Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless
    *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    skip = tuple(exclude)
    out: "list[Finding]" = []
    for parsed in scan:
        if "__global__" not in parsed.source or any(part in parsed.rel.split("/") for part in skip):
            continue
        # the kernel strings of the AST, not the raw file text: a comment or a docstring holding a kernel is not a kernel, and the line is the string's own (53-T5)
        hits = sorted(
            (unit.line + unit.text.count("\n", 0, at), name, rule)
            for unit in _units(parsed.tree, ImportAliases.from_tree(parsed.tree))
            for name, rule, at in _scan_kernels(unit.text)
        )
        seen: "dict[str, int]" = {}
        for line, name, rule in hits:
            seen[name] = seen.get(name, 0) + 1
            key = f"{parsed.rel}::{name}" if seen[name] == 1 else f"{parsed.rel}::{name}#{seen[name]}"  # a second kernel of the same name needs its own baseline entry
            why = (
                "indexes a global array by a thread-derived row times a non-constant stride, so neighbouring threads read elements a row apart: read a column-major copy (or tile the columns)"
                if rule == RULE_STRIDED
                else "loops over a run [P[s], P[s + 1]) of a thread-derived s and reads global memory by the loop variable, so neighbouring lanes read runs one run length apart: stage the rows coalesced or give the block the rows"
            )
            out.append(Finding(parsed.rel, line, rule, f"the kernel {name} {why}", key=key))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_coalesced_reads(
    root: Union[str, Path],
    *,
    exclude: "Iterable[str]" = (),
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any strided global read, or with *baseline_path* on any finding the baseline does not accept (each entry carries a note; a stale entry fails too)."""
    found = find_cuda_coalesced_reads(root, exclude=exclude, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = f"read a column-major copy or stage the rows coalesced, or review the kernel with a profile and mark it `// {MARKER} <reason>` (or baseline it with a note)"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_coalesced_reads", refresh_command="PY_CI_SHARED_REFRESH=cuda_coalesced_reads")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} kernel(s) with a strided global read; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
