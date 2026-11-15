"""A floating-point ``atomicAdd`` in a CUDA kernel carries a written determinism note: an atomic float sum is order-dependent, an integer count is exact.

Floating-point addition is not associative and the order in which the threads of a launch reach an ``atomicAdd`` is unspecified (NVIDIA CUDA C++ Programming Guide, atomic functions), so two runs
over the same input can differ in the last bits of the sum. An integer atomic count is exact in any order. A project that states "our integer counts are exact and the reduction has a fixed
order" (mlframe ``CUDA.md``:48, and the tensormux "document it" rule at ``CUDA.md``:254) is silent about every kernel that sums floats atomically. Audit 2026-11-16, report 54, gate G5 and
finding 54-T6.

* ``cuda-float-atomic-add`` (advisory by nature, reported by default): a kernel (``__global__`` or ``__device__`` function) that calls ``atomicAdd`` (also ``_block`` / ``_system``) on a ``float``,
  ``double``, ``{REAL}`` or ``{ACC}`` target, a global parameter, a ``__shared__`` array or a local pointer declared with such a type, or through a cast to one of them. One finding per kernel, at its
  first such call, with the number of call sites. An integer atomic (``atomicAdd(&h[c[i]], 1)`` on ``int*``, ``(unsigned long long*)&counts[i]``) is never reported.
* ``cuda-float-atomic-unresolved`` (only with ``include_unresolved``): an ``atomicAdd`` whose target type cannot be read from the declarations (a ternary or arithmetic pointer, a member). In the
  measured corpus 69 of 185 atomic sites were unresolved, so they are not counted by default.

Suppression, the determinism note: ``// atomic-fp-ok: <reason>`` anywhere in the kernel text (one reason per kernel source), or ``# atomic-fp-ok: <reason>`` as a Python comment, or in the module
docstring, of the file that holds the source. The reason says why the order does not matter or why the tolerance covers it; an empty reason does not suppress.

Measured (the audit's prototype, mlframe, 208 kernels): 10 kernels in 9 sources / 6 files with a resolvable floating-point atomic, 5 of 5 judged true; none carried a note. The non-associativity of
float addition is NVIDIA's documented fact; that the project should document it is a project claim (CUDA.md), hence the gate is meant to be used with a baseline or markers, not as a ban.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_atomic_determinism import assert_cuda_atomic_determinism

    def test_float_atomics_are_documented():
        assert_cuda_atomic_determinism("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from collections.abc import Iterator
from typing import NamedTuple, Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from .cuda_kernel_integer_width import _strip_comments, _units

__all__ = [
    "MARKER",
    "RULE",
    "RULE_UNRESOLVED",
    "assert_cuda_atomic_determinism",
    "find_cuda_atomic_determinism",
]

RULE = "cuda-float-atomic-add"
RULE_UNRESOLVED = "cuda-float-atomic-unresolved"
MARKER = "atomic-fp-ok:"

_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
_FUNC = re.compile(r"\b([A-Za-z_]\w*)\s*\(([^(){};]*)\)\s*(?:const\s*)?\{")
_NOT_FUNCTIONS = frozenset({"if", "for", "while", "switch", "catch", "return", "else"})
_ATOMIC = re.compile(r"\batomicAdd(?:_block|_system)?\s*\(")
_FP = r"(?:float|double|@REAL@|@ACC@)"
_INT = r"(?:int|unsigned|long|short|char|size_t|bool|u?int\d+_t)"
_QUALS = r"(?:[\s\*&]|\bconst\b|\bvolatile\b|__restrict__)*"


class Func(NamedTuple):
    """One function of a kernel source: its name, parameter text, body span (indexes of ``{`` and ``}``) and whether it is a ``__global__`` entry."""

    name: str
    params: str
    sig_start: int
    body_start: int
    body_end: int
    is_global: bool


def prepare(text: str) -> str:
    """Kernel text with comments blanked (newlines kept, so line numbers hold) and ``{WORD}`` placeholders turned into ``@WORD@`` so brace matching works."""
    return _PLACEHOLDER.sub(lambda m: f"@{m.group(1)}@", _strip_comments(text))


def match(code: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at *start*, balanced over nesting; ``-1`` when it never closes."""
    depth = 0
    for index in range(start, len(code)):
        if code[index] == open_ch:
            depth += 1
        elif code[index] == close_ch:
            depth -= 1
            if depth == 0:
                return index
    return -1


def functions(code: str) -> Iterator[Func]:
    """Every function of the (prepared) kernel text, found by balanced braces; bodies are skipped, so a nested block is never read as a function."""
    pos = 0
    while True:
        found = _FUNC.search(code, pos)
        if found is None:
            return
        if found.group(1) in _NOT_FUNCTIONS:
            pos = found.end()
            continue
        end = match(code, found.end() - 1, "{", "}")
        end = len(code) - 1 if end < 0 else end
        boundary = max(code.rfind(ch, 0, found.start()) for ch in ";{}")
        prefix = code[boundary + 1 : found.start()]
        yield Func(found.group(1), found.group(2), found.start(), found.end() - 1, end, "__global__" in prefix)
        pos = end + 1


def _first_argument(code: str, open_index: int) -> "Optional[str]":
    close = match(code, open_index, "(", ")")
    if close < 0:
        return None
    depth = 0
    for index in range(open_index + 1, close):
        ch = code[index]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            return code[open_index + 1 : index].strip()
    return code[open_index + 1 : close].strip()


def _classify(argument: str, scope: str, whole: str) -> str:
    """``"fp"``, ``"int"`` or ``"unresolved"`` for the target pointer expression of one ``atomicAdd``."""
    rest = argument
    if rest.startswith("("):
        close = match(rest, 0, "(", ")")
        inner = rest[1:close] if close > 0 else ""
        if "*" in inner and re.fullmatch(r"[\w\s\*@]+", inner):
            return "fp" if re.search(_FP, inner) else "int"
    name = re.match(r"&?\s*([A-Za-z_]\w*)\s*(?:\[|\+|$)", rest)
    if name is None:
        return "unresolved"
    word = name.group(1)
    for text in (scope, whole):
        fp = re.search(rf"(?<![\w@]){_FP}(?![\w@]){_QUALS}\b{word}\b(?!\s*\()", text)
        integer = re.search(rf"\b{_INT}\b{_QUALS}\b{word}\b(?!\s*\()", text)
        if fp and (integer is None or fp.start() < integer.start()):
            return "fp"
        if integer:
            return "int"
    return "unresolved"


class Site(NamedTuple):
    """One ``atomicAdd`` call classified as ``fp``, ``int`` or ``unresolved``; ``offset`` indexes the prepared text."""

    offset: int
    kind: str


def atomic_sites(code: str, func: Func) -> "list[Site]":
    """The classified ``atomicAdd`` calls inside *func*'s body."""
    scope = func.params + " " + code[func.body_start : func.body_end + 1]
    out = []
    for call in _ATOMIC.finditer(code, func.body_start, func.body_end + 1):
        argument = _first_argument(code, call.end() - 1)
        out.append(Site(call.start(), "unresolved" if argument is None else _classify(argument, scope, code)))
    return out


def _has_reason(text: str) -> bool:
    """``atomic-fp-ok:`` with a non-empty reason inside a C ``//`` or ``/* */`` comment."""
    for found in re.finditer(r"(?://|/\*)[^\n]*?atomic-fp-ok:[ \t]*([^\s*/][^\n]*)", text):
        if found.group(1).strip():
            return True
    return False


def _python_note(source: str, tree: ast.Module) -> bool:
    """A ``# atomic-fp-ok: <reason>`` comment, or the marker in the module docstring, of the file."""
    for line in source.splitlines():
        if "#" in line:
            found = re.search(r"atomic-fp-ok:[ \t]*(\S.*)", line[line.index("#") :])
            if found and found.group(1).strip():
                return True
    doc = ast.get_docstring(tree) or ""
    return bool(re.search(r"atomic-fp-ok:[ \t]*\S", doc))


def find_cuda_atomic_determinism(
    root: Union[str, Path],
    *,
    include_unresolved: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every kernel with a floating-point ``atomicAdd`` and no determinism note under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code
    must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        noted_file: "Optional[bool]" = None
        for unit in _units(parsed.tree, aliases):
            if "atomicAdd" not in unit.text or _has_reason(unit.text):
                continue
            if noted_file is None:
                noted_file = _python_note(parsed.source, parsed.tree)
            if noted_file:
                continue
            code = prepare(unit.text)
            for func in functions(code):
                sites = atomic_sites(code, func)
                for kind, rule, what in (("fp", RULE, "floating-point"), ("unresolved", RULE_UNRESOLVED, "target type unresolved")):
                    hits = [s for s in sites if s.kind == kind]
                    if not hits or (kind == "unresolved" and not include_unresolved):
                        continue
                    out.append(
                        Finding(
                            parsed.rel,
                            unit.line + code.count("\n", 0, hits[0].offset),
                            rule,
                            f"kernel `{func.name}` has {len(hits)} `atomicAdd` call(s) ({what}) and no determinism note: a floating-point atomic sum is order-dependent "
                            "(an integer count is exact); state why the order does not matter with `// atomic-fp-ok: <reason>`",
                        )
                    )
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_cuda_atomic_determinism(
    root: Union[str, Path],
    *,
    include_unresolved: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any kernel with an undocumented floating-point ``atomicAdd``, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_atomic_determinism(root, include_unresolved=include_unresolved, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "add `// atomic-fp-ok: <why the summation order does not matter or why the tolerance covers it>` to the kernel, or sum in a fixed order (per-block partials, then a tree reduction)"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_atomic_determinism", refresh_command="PY_CI_SHARED_REFRESH=cuda_atomic_determinism")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} undocumented floating-point atomicAdd kernel(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
