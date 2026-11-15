"""CUDA warp intrinsics: legacy unmasked forms that are undefined on sm_70 and later, and the mask rules of the ``_sync`` family.

Kernel source kept in a Python string (``cupy.RawKernel``/``RawModule``/``ElementwiseKernel``/``ReductionKernel``, or a constant named ``*_SRC``/``*_KERNEL``/``*_CODE`` holding
``__global__``) is checked for:

* ``cuda-legacy-shfl`` (hard): ``__shfl``, ``__shfl_up``, ``__shfl_down``, ``__shfl_xor`` without ``_sync``. MEASURED (audit 2026-11-16, finding 54-T2, NVRTC 12.9, no launch): the
  call compiles for ``compute_61`` and is ``identifier "__shfl_down" is undefined`` for ``compute_70``. A kernel written and tested on a Pascal development host therefore breaks, at
  NVRTC compile time, on the first sm_70+ machine; consumers run CPU-only CI, so nothing else sees it. Use the ``_sync`` form with an explicit mask.
* ``cuda-legacy-vote`` (advisory): ``__ballot``, ``__any``, ``__all`` without ``_sync``. MEASURED: deprecated since CUDA 9 but still compiles for ``compute_70`` on NVRTC 12.9, so
  this is a deprecation, not a failure today.
* ``cuda-full-mask-in-divergent-branch`` (hard): a ``_sync`` intrinsic (``__shfl*_sync``, ``__ballot_sync``, ``__any_sync``, ``__all_sync``, ``__uni_sync``, ``__match_*_sync``,
  ``__reduce_*_sync``) whose mask is the full-warp literal (``0xffffffff[u]``, ``~0u``, a ``#define``/``const unsigned`` bound to one) inside an ``if`` (or its ``else``) whose
  condition depends on ``threadIdx`` at lane granularity: the lanes named by the mask do not all execute the call. NVIDIA-DOCUMENTED (programming guide, "Using CUDA Warp-Level
  Primitives"), not measured here.
* ``cuda-activemask-as-mask`` (advisory): ``__activemask()`` (or a variable bound to it) passed as the mask. The NVIDIA blog says it reports the lanes that happen to be
  converged now, so it does not establish that the intended lanes participate. Third-party guidance: advisory.

Thread dependence is a scoped, flow-aware taint (see ``_Walker``), shared with ``cuda_barrier_divergence``: ``threadIdx`` is dependent, ``blockIdx``/``blockDim``/``gridDim``,
kernel parameters and constants are not, and a ``.x``/``.y``/``.z`` member access is never a variable use (the ``blockIdx.y`` case that fooled the audit's first prototype).
``threadIdx.x >> 5`` and ``threadIdx.x / 32`` are warp-uniform: a condition built only from them does not diverge lanes of one warp and is not reported by the mask rule.
Limits, all in the direction of a miss: values loaded from memory at a block-uniform address, results of atomics, ``break``/``continue`` and conditions hidden in function-like
macros are treated as uniform; the mask rule looks at ``if`` only, not at loops.

Suppression: ``// warp-ok: <reason>`` on the offending line, the line above it or the line (or the one above) of the ``if`` that makes the call divergent, inside the kernel string;
``# warp-ok: <reason>`` on the first or last line of the Python statement holding the string. An empty reason does not suppress.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_warp_intrinsics import assert_cuda_warp_intrinsics

    def test_cuda_warp_intrinsics():
        assert_cuda_warp_intrinsics("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from .cuda_kernel_integer_width import _Unit, _strip_comments, _units

__all__ = [
    "MARKER",
    "RULE_ACTIVEMASK",
    "RULE_FULL_MASK",
    "RULE_SHFL",
    "RULE_VOTE",
    "assert_cuda_warp_intrinsics",
    "find_cuda_warp_intrinsics",
]

RULE_SHFL = "cuda-legacy-shfl"
RULE_VOTE = "cuda-legacy-vote"
RULE_FULL_MASK = "cuda-full-mask-in-divergent-branch"
RULE_ACTIVEMASK = "cuda-activemask-as-mask"
MARKER = "warp-ok:"

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# A small C reader: tokens, statements, and a scoped thread-dependence taint. Shared with cuda_barrier_divergence.
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<pp>^[ \t]*\#(?:[^\n\\]|\\\n|\\)*)
    |(?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
    |(?P<id>[A-Za-z_]\w*)
    |(?P<num>0[xX][0-9a-fA-F]+[uUlL]*|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fFuUlL]*)
    |(?P<op><<=|>>=|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|::)
    |(?P<ch>\S)
    """,
    re.VERBOSE | re.MULTILINE,
)
_PLACEHOLDER = re.compile(r"\{[A-Z][A-Z0-9_]*\}")
_ASSIGN_OPS = frozenset({"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="})
_DEFINE = re.compile(r"#\s*define\s+(\w+)(?!\()\s+(.*)", re.DOTALL)
_WARP_SPLIT = re.compile(r"(?:32|warpSize)[uU]?")

UNIFORM, WARP, THREAD = 0, 1, 2  # thread-dependence classes of a value or a condition


class _Tok(NamedTuple):
    kind: str
    text: str
    off: int


class _ParseError(Exception):
    """The kernel uses a construct the reader does not handle; the kernel is skipped, never reported."""


def _tokenize(code: str) -> list[_Tok]:
    return [_Tok(m.lastgroup or "ch", m.group(), m.start()) for m in _TOKEN.finditer(code)]


def _prepare(unit_text: str) -> str:
    """Comments blanked (newlines kept) and ``{REAL}``-style Python placeholders turned into one identifier, so braces balance."""
    return _PLACEHOLDER.sub("REAL_PH", _strip_comments(unit_text))


@dataclass
class _Stmt:
    kind: str  # block | if | for | while | do | switch | return | simple
    off: int
    toks: list[_Tok] = field(default_factory=list)  # condition (if/while/do/switch) or the statement tokens
    parts: list[list[_Tok]] = field(default_factory=list)  # for: init, condition, increment
    kids: list["_Stmt"] = field(default_factory=list)


class _Parser:
    def __init__(self, toks: list[_Tok], start: int, end: int) -> None:
        self.toks, self.pos, self.end = toks, start, end

    def _peek(self) -> Optional[_Tok]:
        return self.toks[self.pos] if self.pos < self.end else None

    def _paren_group(self) -> list[_Tok]:
        tok = self._peek()
        if tok is None or tok.text != "(":
            raise _ParseError("expected (")
        depth, start = 0, self.pos + 1
        for index in range(self.pos, self.end):
            text = self.toks[index].text
            if self.toks[index].kind in ("str", "pp"):
                continue
            depth += (text == "(") - (text == ")")
            if depth == 0:
                self.pos = index + 1
                return self.toks[start:index]
        raise _ParseError("unbalanced (")

    def _until_semicolon(self) -> list[_Tok]:
        depth, start = 0, self.pos
        for index in range(self.pos, self.end):
            tok = self.toks[index]
            if tok.kind in ("str", "pp"):
                continue
            depth += (tok.text in "([{") - (tok.text in ")]}")
            if depth == 0 and tok.text == ";":
                self.pos = index + 1
                return self.toks[start:index]
            if depth < 0:
                break
        raise _ParseError("no ;")

    def block_body(self, close: bool) -> list[_Stmt]:
        out: list[_Stmt] = []
        while True:
            tok = self._peek()
            if tok is None:
                if close:
                    raise _ParseError("unclosed {")
                return out
            if tok.text == "}":
                if not close:
                    raise _ParseError("stray }")
                self.pos += 1
                return out
            out.append(self.stmt())

    def _compound(self, text: str, off: int) -> _Stmt:
        self.pos += 1
        inner = self._paren_group()
        if text == "for":
            return _Stmt("for", off, parts=_split_for(inner), kids=[self.stmt()])
        kids = [self.stmt()]
        nxt = self._peek()
        if text == "if" and nxt is not None and nxt.kind == "id" and nxt.text == "else":
            self.pos += 1
            kids.append(self.stmt())
        return _Stmt(text, off, toks=inner, kids=kids)

    def _do(self, off: int) -> _Stmt:
        self.pos += 1
        body = self.stmt()
        nxt = self._peek()
        if nxt is None or nxt.text != "while":
            raise _ParseError("do without while")
        self.pos += 1
        cond = self._paren_group()
        tail = self._peek()
        if tail is not None and tail.text == ";":
            self.pos += 1
        return _Stmt("do", off, toks=cond, kids=[body])

    def _keyword(self, text: str, off: int) -> Optional[_Stmt]:
        if text in ("if", "while", "switch", "for"):
            return self._compound(text, off)
        if text == "do":
            return self._do(off)
        if text == "return":
            self.pos += 1
            return _Stmt("return", off, toks=self._until_semicolon())
        if text in ("case", "default"):
            for index in range(self.pos, self.end):
                if self.toks[index].text == ":":
                    self.pos = index + 1
                    return _Stmt("simple", off)
            raise _ParseError("label without :")
        if text == "else":
            raise _ParseError("stray else")
        return None

    def stmt(self) -> _Stmt:
        tok = self._peek()
        if tok is None:
            raise _ParseError("eof")
        if tok.kind == "id":
            found = self._keyword(tok.text, tok.off)
            if found is not None:
                return found
        if tok.text == "{":
            self.pos += 1
            return _Stmt("block", tok.off, kids=self.block_body(True))
        if tok.text == ";":
            self.pos += 1
            return _Stmt("simple", tok.off)
        return _Stmt("simple", tok.off, toks=self._until_semicolon())


def _split_for(inner: list[_Tok]) -> list[list[_Tok]]:
    parts: list[list[_Tok]] = [[]]
    depth = 0
    for tok in inner:
        if tok.kind not in ("str", "pp"):
            depth += (tok.text in "([{") - (tok.text in ")]}")
            if depth == 0 and tok.text == ";":
                parts.append([])
                continue
        parts[-1].append(tok)
    if len(parts) != 3:
        raise _ParseError("range-for or malformed for")
    return parts


def _close_paren(toks: list[_Tok], open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(toks)):
        depth += (toks[index].text == "(") - (toks[index].text == ")")
        if depth == 0:
            return index
    return len(toks) - 1


def _match_brace(toks: list[_Tok], open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(toks)):
        if toks[index].kind in ("str", "pp"):
            continue
        depth += (toks[index].text == "{") - (toks[index].text == "}")
        if depth == 0:
            return index
    return -1


class _Func(NamedTuple):
    qualifier: str  # __global__ | __device__
    name: str
    start: int  # first token index inside the body
    end: int  # index of the closing brace


def _header(toks: list[_Tok], start: int) -> tuple[str, int, int]:
    """``(name, index of the body's opening brace or -1, index where the scan stopped)`` for the declaration whose qualifier sits before *start*."""
    depth, name = 0, "?"
    for j in range(start, len(toks)):
        cur = toks[j]
        if cur.kind in ("str", "pp"):
            continue
        if cur.text == "(":
            if depth == 0 and toks[j - 1].kind == "id":
                name = toks[j - 1].text
            depth += 1
        elif cur.text == ")":
            depth -= 1
        elif depth == 0 and cur.text == ";":
            return name, -1, j
        elif depth == 0 and cur.text == "{":
            return name, j, j
    return name, -1, len(toks)


def _functions(toks: list[_Tok]) -> list[_Func]:
    out: list[_Func] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok.kind != "id" or tok.text not in ("__global__", "__device__"):
            i += 1
            continue
        name, body, stop = _header(toks, i + 1)
        end = _match_brace(toks, body) if body >= 0 else -1
        if end >= 0:
            out.append(_Func(tok.text, name, body + 1, end))
            i = end + 1
        else:
            i = stop + 1
    return out


class _Call(NamedTuple):
    """One ``__intrinsic(...)`` call met while walking a function, with the control context it sits in."""

    name: str
    args: list[list[_Tok]]
    off: int
    ctrl: tuple  # ((class, offset-of-condition, kind), ...) outermost first; kind is "if" / "else" / "loop" / "switch"
    exit_off: Optional[int]  # offset of the first thread-dependent `return` met earlier in the function, if any


def _split_args(toks: list[_Tok]) -> list[list[_Tok]]:
    args: list[list[_Tok]] = [[]]
    depth = 0
    for tok in toks:
        if tok.kind not in ("str", "pp"):
            depth += (tok.text in "([{") - (tok.text in ")]}")
            if depth == 0 and tok.text == ",":
                args.append([])
                continue
        args[-1].append(tok)
    return args if any(args) else []


class _Walker:
    """Walks one function body in program order with scoped variables; ``on_call`` sees every ``__intrinsic(...)`` with its control context.

    A variable's class is UNIFORM, WARP (a function of the warp index only) or THREAD. A declaration binds in the current scope; an assignment to a binding declared outside an
    enclosing ``if``/loop/``switch`` is a weak update (max with the old class), so ``x = 0;`` at the same level resets ``x`` but inside a branch it cannot. There is no implicit flow
    (``if (tid == 0) flag = 1;`` does not make ``flag`` thread-dependent), ``__shared__`` names stay uniform, and a call to a ``__device__`` helper that itself contains a barrier
    (a block reduction) yields a block-uniform value whatever its arguments. Loops are walked twice (the first pass silent) so loop-carried values reach the loop condition.
    """

    def __init__(self, macros: dict[str, int], on_call: Callable[[_Call], None], collectives: frozenset = frozenset()) -> None:
        self.collectives = collectives  # `__device__` helpers that contain a barrier: block-wide reductions, whose result is block-uniform
        self.shared: set[str] = set()  # `__shared__` names: written by some threads, read after a barrier; never thread-dependent here
        self.scopes: list[dict[str, int]] = [dict(macros), {}]
        self.ctrl: list[tuple[int, int, str, int]] = []  # (class, offset, kind, scope depth outside the construct)
        self.exit_off: Optional[int] = None
        self.muted = 0
        self.on_call = on_call

    # -- classes -------------------------------------------------------------------------------------------------------------------------------------------------------
    def _lookup(self, name: str) -> tuple[int, int]:
        for index in range(len(self.scopes) - 1, -1, -1):
            if name in self.scopes[index]:
                return index, self.scopes[index][name]
        return -1, UNIFORM

    def expr_class(self, toks: list[_Tok]) -> int:
        cls = UNIFORM
        skipped: set[int] = set()
        for i, tok in enumerate(toks):
            if i in skipped:
                continue
            if tok.kind != "id" or (i > 0 and toks[i - 1].text in (".", "->")):
                continue  # a member access (`.x`, `blockIdx.y`) is never a variable use
            if tok.text == "threadIdx":
                follow = [t.text for t in toks[i + 3 : i + 5]]
                warp = len(follow) == 2 and ((follow[0] == ">>" and follow[1] == "5") or (follow[0] == "/" and bool(_WARP_SPLIT.fullmatch(follow[1]))))
                cls = max(cls, WARP if warp else THREAD)
            elif tok.text in self.collectives and i + 1 < len(toks) and toks[i + 1].text == "(":
                skip_until = _close_paren(toks, i + 1)
                skipped.update(range(i + 1, skip_until + 1))
            else:
                cls = max(cls, self._lookup(tok.text)[1])
        return cls

    def _ctrl_class(self, scope_index: int) -> bool:
        """Whether an enclosing branch or loop opened after the binding in scope *scope_index* was declared: the assignment is then a weak update."""
        return any(c[3] > scope_index for c in self.ctrl)

    def ctrl_snapshot(self) -> tuple:
        return tuple((c[0], c[1], c[2]) for c in self.ctrl)

    # -- statements ----------------------------------------------------------------------------------------------------------------------------------------------------
    def _calls(self, toks: list[_Tok]) -> None:
        if self.muted:
            return
        for i, tok in enumerate(toks[:-1]):
            if tok.kind == "id" and tok.text.startswith("__") and toks[i + 1].text == "(" and (i == 0 or toks[i - 1].text not in (".", "->")):
                depth, start = 0, i + 1
                for j in range(i + 1, len(toks)):
                    depth += (toks[j].text == "(") - (toks[j].text == ")")
                    if depth == 0:
                        self.on_call(_Call(tok.text, _split_args(toks[start + 1 : j]), tok.off, self.ctrl_snapshot(), self.exit_off))
                        break

    @staticmethod
    def _declarator(part: list[_Tok], decl_statement: bool) -> tuple[int, list[_Tok], bool, str]:
        """``(index of the assignment operator or -1, left side, is it a declaration, the name written)``; the name is empty for stores that keep their container's class."""
        k = next((n for n, t in enumerate(part) if t.text in _ASSIGN_OPS and t.kind in ("op", "ch")), -1)
        lhs = part[:k] if k >= 0 else part
        typed = len(lhs) >= 2 and (lhs[-2].kind == "id" or lhs[-2].text in ("*", "&", ">"))
        is_decl = bool(lhs) and lhs[-1].kind == "id" and (typed or (decl_statement and len(lhs) == 1))
        texts = [t.text for t in lhs]
        name = next((t.text for t in reversed(lhs) if t.kind == "id"), "")
        if "[" in texts:
            before = lhs[: texts.index("[")]
            name = before[-1].text if before and before[-1].kind == "id" else ""
        container_store = any(x in texts for x in (".", "->")) or ("[" in texts and not is_decl) or (bool(texts) and texts[0] == "*")
        if not is_decl and container_store:
            name = ""
        return k, lhs, is_decl, name

    def _effects(self, toks: list[_Tok]) -> None:
        decl_statement = False
        for index, part in enumerate(_split_args(toks)):
            if not part:
                continue
            k, lhs, is_decl, name = self._declarator(part, decl_statement)
            if index == 0:
                decl_statement = is_decl
            if not name:
                continue
            value = self.expr_class(part[k + 1 :]) if k >= 0 else UNIFORM
            if is_decl:
                if any(t.text == "__shared__" for t in lhs):
                    self.shared.add(name)
                    value = UNIFORM
                self.scopes[-1][name] = value
            elif k >= 0 and name not in self.shared:
                self._assign(name, value, part[k].text != "=")

    def _assign(self, name: str, value: int, compound: bool) -> None:
        scope_index, old = self._lookup(name)
        if scope_index < 0:
            self.scopes[-1][name] = value
            return
        # no implicit flow: `if (tid == 0) flag = 1;` does not taint `flag`
        self.scopes[scope_index][name] = max(value, old if (compound or self._ctrl_class(scope_index)) else UNIFORM)

    def _control(self, cls: int, off: int, kind: str) -> None:
        self.ctrl.append((cls, off, kind, len(self.scopes)))

    def walk(self, stmts: list[_Stmt]) -> None:
        for stmt in stmts:
            self._stmt(stmt)

    def _stmt(self, s: _Stmt) -> None:
        kind = s.kind
        if kind == "block":
            self.scopes.append({})
            self.walk(s.kids)
            self.scopes.pop()
        elif kind == "simple":
            self._calls(s.toks)
            self._effects(s.toks)
        elif kind == "return":
            self._calls(s.toks)
            if any(c[0] == THREAD for c in self.ctrl) and self.exit_off is None:
                self.exit_off = s.off
        elif kind == "if":
            self._calls(s.toks)
            cond_cls = self.expr_class(s.toks)
            self._control(cond_cls, s.toks[0].off if s.toks else s.off, "if")
            self._stmt(s.kids[0])
            self.ctrl.pop()
            if len(s.kids) > 1:
                self._control(cond_cls, s.toks[0].off if s.toks else s.off, "else")
                self._stmt(s.kids[1])
                self.ctrl.pop()
        elif kind == "switch":
            self._calls(s.toks)
            self._control(self.expr_class(s.toks), s.toks[0].off if s.toks else s.off, "switch")
            self._stmt(s.kids[0])
            self.ctrl.pop()
        elif kind in ("for", "while", "do"):
            self._loop(s)

    def _loop(self, s: _Stmt) -> None:
        depth = len(self.scopes)
        self.scopes.append({})
        if s.kind == "for":
            init, cond, incr = s.parts
            self._calls(init)
            self._effects(init)
        else:
            cond, incr = s.toks, []
        for silent in (True, False):
            self.muted += silent
            self._calls(cond)
            cond_toks = cond or s.toks
            cls = self.expr_class(cond_toks) if cond_toks else UNIFORM
            self.ctrl.append((cls, (cond_toks[0].off if cond_toks else s.off), "loop", depth + 1))
            self._stmt(s.kids[0])
            self._calls(incr)
            self._effects(incr)
            self.ctrl.pop()
            self.muted -= silent
        self.scopes.pop()


class _Prepared(NamedTuple):
    """One kernel string, read: tokens, functions, macro classes and line lookup."""

    toks: list[_Tok]
    funcs: list[_Func]
    macros: dict[str, int]
    macro_text: dict[str, str]
    newlines: list[int]


def _prepare_unit(text: str) -> _Prepared:
    code = _prepare(text)
    toks = _tokenize(code)
    macros: dict[str, int] = {}
    macro_text: dict[str, str] = {}
    probe = _Walker({}, lambda call: None)
    for tok in toks:
        if tok.kind != "pp":
            continue
        m = _DEFINE.match(tok.text.strip())
        if m:
            rhs = m.group(2).replace("\\\n", " ")
            macro_text[m.group(1)] = rhs.strip()
            macros[m.group(1)] = probe.expr_class(_tokenize(rhs))
    newlines = [i for i, ch in enumerate(code) if ch == "\n"]
    code_toks = [t for t in toks if t.kind != "pp"]
    return _Prepared(code_toks, _functions(code_toks), macros, macro_text, newlines)


def _code_tokens(prep: _Prepared) -> list[_Tok]:
    return prep.toks


def line_offset(prep: _Prepared, off: int) -> int:
    """Zero-based line of character offset *off* inside the kernel text."""
    return bisect.bisect_left(prep.newlines, off)


def walk_functions(prep: _Prepared, qualifiers: tuple, make_callback: Callable[[_Func], Callable[[_Call], None]]) -> None:
    """Walk every function of the kernel text whose qualifier is in *qualifiers*; a function the reader cannot parse is skipped."""
    toks = _code_tokens(prep)
    funcs = _functions(toks)
    collectives = frozenset(
        f.name for f in funcs if f.qualifier == "__device__" and any(t.kind == "id" and t.text.startswith("__syncthreads") for t in toks[f.start : f.end])
    )
    for func in funcs:
        if func.qualifier not in qualifiers:
            continue
        try:
            body = _Parser(toks, func.start, func.end).block_body(False)
        except _ParseError:
            continue
        _Walker(prep.macros, make_callback(func), collectives).walk(body)


def marked_lines(text: str, marker: str) -> set[int]:
    """Zero-based lines of *text* carrying ``// <marker> <non-empty reason>``."""
    pattern = re.compile(r"//[^\n]*?" + re.escape(marker) + r"[ \t]*\S")
    return {i for i, line in enumerate(text.split("\n")) if pattern.search(line)}


def python_marked(unit: _Unit, py_lines: list[str], marker: str) -> bool:
    """``# <marker> <reason>`` on the first, last or string line of the Python statement holding the kernel."""
    pattern = re.compile(r"#[^\n]*?" + re.escape(marker) + r"[ \t]*\S")
    for number in {unit.stmt_first, unit.stmt_last, unit.line}:
        if 0 < number <= len(py_lines) and pattern.search(py_lines[number - 1]):
            return True
    return False


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------

_LEGACY_SHFL = re.compile(r"__shfl(?:_up|_down|_xor)?\b")
_LEGACY_VOTE = re.compile(r"__(?:ballot|any|all)\b")
_SYNC_INTRINSIC = re.compile(r"__(?:shfl(?:_up|_down|_xor)?|ballot|any|all|uni|match_any|match_all|reduce_\w+)_sync\Z")
_FULL_LITERAL = re.compile(r"\(*(?:0[xX][fF]{8}[uUlL]*|~0[uUlL]*|4294967295[uUlL]*)\)*\Z")
_ACTIVEMASK_NAMES = re.compile(r"(\w+)\s*=\s*__activemask\s*\(\s*\)")
_FULL_NAMES = re.compile(r"(\w+)\s*=\s*\(*(?:0[xX][fF]{8}[uUlL]*|~0[uUlL]*|4294967295[uUlL]*)\)*\s*;")


def _full_mask_names(prep: _Prepared, body_text: str) -> set[str]:
    names = {m.group(1) for m in _FULL_NAMES.finditer(body_text)}
    names |= {name for name, rhs in prep.macro_text.items() if _FULL_LITERAL.match(rhs.replace(" ", ""))}
    return names


def _legacy_hits(code: str, include_advisory: bool) -> list[tuple[str, int, str]]:
    """``(rule, offset, message)`` of every legacy unmasked intrinsic call in the kernel text."""
    out: list[tuple[str, int, str]] = []
    for match in _LEGACY_SHFL.finditer(code):
        if code[match.end() :].lstrip().startswith("("):
            text = (
                f"legacy `{match.group()}` has no `_sync` form: it compiles for sm_61 and is undefined for sm_70 and later (NVRTC error); "
                f"use `{match.group()}_sync(0xffffffffu, ...)` with the participating-lane mask"
            )
            out.append((RULE_SHFL, match.start(), text))
    votes = [m for m in _LEGACY_VOTE.finditer(code if include_advisory else "") if code[m.end() :].lstrip().startswith("(")]
    out.extend((RULE_VOTE, m.start(), f"deprecated `{m.group()}` (CUDA 9): use `{m.group()}_sync(mask, ...)` with an explicit mask") for m in votes)
    return out


def _unit_findings(unit: _Unit, rel: str, py_lines: list[str], include_advisory: bool) -> list[Finding]:
    if python_marked(unit, py_lines, MARKER):
        return []
    code = _prepare(unit.text)
    prep = _prepare_unit(unit.text)
    marked = marked_lines(unit.text, MARKER)
    out: list[Finding] = []

    def add(rule: str, off: int, message: str, *also: int) -> None:
        line = line_offset(prep, off)
        if any(n in marked or n - 1 in marked for n in (line, *also)):
            return
        out.append(Finding(rel, unit.line + line, rule, message))

    for rule, off, message in _legacy_hits(code, include_advisory):
        add(rule, off, message)
    toks = _code_tokens(prep)
    active_names = {m.group(1) for m in _ACTIVEMASK_NAMES.finditer(code)}

    def make(func: _Func) -> Callable[[_Call], None]:
        body_text = code[toks[func.start].off : toks[func.end].off] if func.start < func.end else ""
        full_names = _full_mask_names(prep, body_text)

        def on_call(call: _Call) -> None:
            if not _SYNC_INTRINSIC.match(call.name) or not call.args:
                return
            mask = "".join(t.text for t in call.args[0])
            divergent = [c for c in call.ctrl if c[0] == THREAD and c[2] in ("if", "else")]
            if divergent and (_FULL_LITERAL.match(mask) or mask in full_names):
                cond_off = divergent[-1][1]
                add(
                    RULE_FULL_MASK,
                    call.off,
                    f"`{call.name}` with the full mask `{mask}` sits inside an `if` whose condition depends on threadIdx (line {unit.line + line_offset(prep, cond_off)}): "
                    "lanes named in the mask do not all execute it, which is undefined; compute the mask of the participating lanes (e.g. `__ballot_sync` before the branch) or hoist the call out of the branch",
                    line_offset(prep, cond_off),
                )
            if include_advisory and ("__activemask" in mask or mask in active_names):
                add(
                    RULE_ACTIVEMASK,
                    call.off,
                    f"`{call.name}` takes `__activemask()` as its mask: it is the set of lanes converged now, not the lanes that must take part; name the participating lanes explicitly",
                )

        return on_call

    walk_functions(prep, ("__global__", "__device__"), make)
    return out


def find_cuda_warp_intrinsics(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every legacy warp intrinsic and every ``_sync`` mask error in a CUDA kernel string under *root*, sorted by path and line.

    Hard rules always; ``cuda-legacy-vote`` and ``cuda-activemask-as-mask`` only with *include_advisory*. Raises ``EmptyScanError`` when fewer than *min_files* files parsed and
    ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        py_lines = parsed.source.splitlines()
        aliases = ImportAliases.from_tree(parsed.tree)
        for unit in _units(parsed.tree, aliases):
            out.extend(_unit_findings(unit, parsed.rel, py_lines, include_advisory))
    return sorted(set(out), key=lambda f: (f.path, f.line, f.rule, f.message))


def assert_cuda_warp_intrinsics(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any legacy ``__shfl*`` or full-warp mask inside a thread-dependent branch (and on the advisory rules with *include_advisory*).

    With *baseline_path*, fail only on findings the baseline does not accept.
    """
    found = find_cuda_warp_intrinsics(root, include_advisory=include_advisory, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "use the `_sync` intrinsics with a mask of exactly the participating lanes, never called from a thread-dependent branch with a full mask; `// warp-ok: <reason>` when the launch makes it safe"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_warp_intrinsics", refresh_command="PY_CI_SHARED_REFRESH=cuda_warp_intrinsics")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA warp-intrinsic finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
