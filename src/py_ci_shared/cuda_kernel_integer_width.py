"""CUDA kernel source strings must use explicit 64-bit integer types for element indices and offsets.

Kernel source kept in a Python string (``cupy.RawKernel``/``RawModule``/``ElementwiseKernel``/``ReductionKernel``, or a
constant named ``*_SRC``/``*_KERNEL``/``*_CODE`` holding ``__global__``) is C, and C integer widths depend on the platform:

* ``cuda-platform-long`` (hard): the C type ``long``, signed or ``unsigned``. NVRTC on Windows (LLP64) compiles ``long`` as
  4 bytes, so ``const float* row = X + (long)i * n_cols;`` and ``long off = (long)level * width;`` silently wrap once the
  array passes 2**31 elements: an illegal memory address or garbage output, while the same kernel is correct on Linux and
  on every small test array. Spell it ``long long``, ``size_t`` or ``int64_t``. ``long long``, ``unsigned long long`` and
  ``long double`` are not reported. Strings built by f-string or ``+`` of literals count (a ``{placeholder}`` is opaque),
  and a module constant passed to a cupy kernel class counts whatever its name.
* ``cuda-int-index-product`` (advisory): ``int idx = n_rows * n_cols;``, an ``int`` initialised by a product of two
  non-literal operands, where the variable's name reads as an index or offset (``idx``, ``index``, ``off``, ``pos``,
  ``row``, ``col``, ``ptr``, ``addr``, ``base``, ``stride``, ``start``, ``gid``, ``elem``, ``slot``). The product is
  computed in 32 bits, so it overflows at 2**31 elements. Cast one operand (``(long long)n_rows * n_cols``) or declare
  the variable wide. Not reported: the standard grid index ``blockIdx.x * blockDim.x + threadIdx.x``, a product with a
  64-bit cast in it, a product inside a subscript (``int v = a[i * n + j]``), and a variable named for a size or a bin
  count (``int joint_size = nbx * nby;`` is bounded by shared memory, not by the array length). Known false positive:
  an index-named variable whose factors are both small and bounded; suppress it.

Suppression: ``// width-ok: <reason>`` on the offending line (or the line above it) inside the kernel string, or
``# width-ok: <reason>`` on the first or last line of the Python statement holding the string. C comments are not
scanned, so a ``long`` in prose is not a finding.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_kernel_integer_width import assert_cuda_kernel_integer_width

    def test_cuda_kernels_use_64bit_types():
        assert_cuda_kernel_integer_width("src", min_files=50)
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = ["RULE_LONG", "RULE_PRODUCT", "MARKER", "assert_cuda_kernel_integer_width", "find_cuda_kernel_integer_width"]

RULE_LONG = "cuda-platform-long"
RULE_PRODUCT = "cuda-int-index-product"
MARKER = "width-ok:"
_KERNEL_CLASSES = frozenset({"RawKernel", "RawModule", "ElementwiseKernel", "ReductionKernel"})
_NAME_SUFFIXES = ("_SRC", "_KERNEL", "_CODE")
_PLACEHOLDER = "__PLACEHOLDER__"
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_LONG_WORD = re.compile(r"\blong\b")
_LONG_PAIR = re.compile(r"\blong\s+(?:long|double)\b")
_INDEX_NAME = re.compile(r"(?i)idx|index|off|pos|row|col|ptr|addr|base|stride|start|gid|elem|slot")
_INT_INIT = re.compile(r"\bint\s+(\w+)\s*=\s*([^;{}]*);")
_WIDE_CAST = re.compile(r"\(\s*(?:unsigned\s+)?(?:long\s+long|size_t|int64_t|uint64_t|ptrdiff_t|ssize_t|std::size_t)\s*\)")
_GRID_BUILTINS = ("threadIdx", "blockIdx", "blockDim", "gridDim")
_LEFT_OPERAND = re.compile(r"([\w.]+|\)|\])\s*$")
_RIGHT_OPERAND = re.compile(r"^\s*([\w.]+|\()")


def _blank(match: "re.Match[str]") -> str:
    """Replace a comment by as many newlines as it held, so line numbers inside the kernel stay exact."""
    return "\n" * match.group(0).count("\n")


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub(_blank, text))


def _static_text(node: ast.AST, constants: dict[str, ast.AST]) -> Optional[str]:
    """The text of a string expression built from literals (``+``, f-strings, implicit concatenation), else ``None``."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            piece = _static_text(value, constants) if isinstance(value, ast.Constant) else _PLACEHOLDER
            parts.append(piece if piece is not None else _PLACEHOLDER)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _static_text(node.left, constants), _static_text(node.right, constants)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.Name) and node.id in constants:
        return _static_text(constants[node.id], {})
    return None


def _module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


def _kernel_class_call(node: ast.AST, aliases: ImportAliases) -> bool:
    if not isinstance(node, ast.Call):
        return False
    qualified = aliases.qualified_name(node) or ""
    parts = qualified.split(".")
    return parts[-1] in _KERNEL_CLASSES and parts[0] == "cupy"


class _Unit:
    """One kernel source string: its text, where it starts, and the statement it sits in."""

    def __init__(self, text: str, node: ast.AST, stmt: ast.AST) -> None:
        self.text = text
        self.line = getattr(node, "lineno", 1)
        self.stmt_first = getattr(stmt, "lineno", self.line)
        self.stmt_last = getattr(stmt, "end_lineno", None) or self.stmt_first


def _declared_sources(node: ast.AST, aliases: ImportAliases) -> Iterator[tuple[ast.expr, bool]]:
    """``(expression, is_certainly_kernel_source)`` for a ``*_SRC``-style assignment and for every argument of a cupy kernel class."""
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.upper().endswith(_NAME_SUFFIXES) for t in node.targets):
        yield node.value, False
    if isinstance(node, ast.Call) and _kernel_class_call(node, aliases):
        for arg in [*node.args, *(k.value for k in node.keywords)]:
            yield arg, True


def _is_text_expression(node: ast.AST) -> bool:
    return isinstance(node, (ast.Constant, ast.JoinedStr)) or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add))


def _emit(tree: ast.Module, constants: dict[str, ast.AST], seen: set[int], expr: ast.AST, stmt: ast.AST, force: bool) -> Iterator[_Unit]:
    """One unit for *expr* when it is kernel source (always when *force*, else only when it holds ``__global__``), once per string."""
    text = _static_text(expr, constants)
    if text is None or (not force and "__global__" not in text):
        return
    target = constants.get(expr.id, expr) if isinstance(expr, ast.Name) else expr
    if id(target) in seen:
        return
    seen.add(id(target))
    yield _Unit(text, target, stmt if target is expr else _statement_of(tree, target) or stmt)


def _units(tree: ast.Module, aliases: ImportAliases) -> Iterator[_Unit]:
    constants = _module_constants(tree)
    seen: set[int] = set()

    def emit(expr: ast.AST, stmt: ast.AST, force: bool) -> Iterator[_Unit]:
        return _emit(tree, constants, seen, expr, stmt, force)

    def visit(node: ast.AST, stmt: ast.AST) -> Iterator[_Unit]:
        if isinstance(node, ast.stmt):
            stmt = node
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            return  # a bare string statement is a docstring, never kernel source
        for expr, force in _declared_sources(node, aliases):
            yield from emit(expr, stmt, force)
        if _is_text_expression(node):
            if id(node) in seen:
                return
            before = len(seen)
            yield from emit(node, stmt, False)
            if len(seen) != before:
                return  # the whole expression was one unit; its operand strings are not separate units
        for child in ast.iter_child_nodes(node):
            yield from visit(child, stmt)

    yield from visit(tree, tree)


def _statement_of(tree: ast.Module, target: ast.AST) -> Optional[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and any(child is target for child in ast.walk(node)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return node
    return None


def _marked(unit: _Unit, kernel_lines: list[str], offset: int, py_lines: list[str]) -> bool:
    """``// width-ok:`` on the line or the one above, in the kernel text; ``# width-ok:`` on the statement's ends."""
    for index in (offset, offset - 1):
        if 0 <= index < len(kernel_lines) and "//" in kernel_lines[index] and MARKER in kernel_lines[index][kernel_lines[index].index("//") :]:
            return True
    for number in {unit.stmt_first, unit.stmt_last, unit.line}:
        if 0 < number <= len(py_lines):
            line = py_lines[number - 1]
            if "#" in line and MARKER in line[line.index("#") :]:
                return True
    return False


def _split_products(expr: str) -> Iterator[tuple[str, str]]:
    """``(left operand, right operand)`` for each binary ``*`` in *expr* (``**`` and a leading dereference excluded)."""
    for match in re.finditer(r"\*", expr):
        start = match.start()
        if expr[start - 1 : start] == "*" or expr[start + 1 : start + 2] == "*":
            continue
        left, right = _LEFT_OPERAND.search(expr[:start]), _RIGHT_OPERAND.match(expr[start + 1 :])
        if left and right:
            yield left.group(1), right.group(1)


def _is_literal(operand: str) -> bool:
    return operand[0].isdigit()


def _builtin_only(operand: str) -> bool:
    return operand.startswith(_GRID_BUILTINS)


def _outside_brackets(expr: str) -> str:
    """*expr* with every ``[...]`` subscript emptied: a product inside an index is not the value the variable holds."""
    out, depth = [], 0
    for char in expr:
        if char == "[":
            depth += 1
            out.append(char)
        elif char == "]":
            depth = max(0, depth - 1)
            out.append(char)
        elif depth == 0:
            out.append(char)
    return "".join(out)


def _declarators(expr: str) -> list[str]:
    """Split ``a = 1, b = x * y`` initialisers at top-level commas (the first declarator's ``name =`` is already cut off)."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in expr:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.split("=", 1)[1] if i and "=" in part else part for i, part in enumerate(parts)]


def _declared(first_name: str, expr: str) -> list[tuple[str, str]]:
    """``(name, initialiser)`` for each declarator of ``int first_name = expr;`` (``int a = 1, b = x * y;`` has two)."""
    parts = _declarators(expr)
    names = [first_name]
    for part in re.split(r",(?![^()\[\]]*[)\]])", expr)[1:]:
        found = re.match(r"\s*(\w+)\s*=", part)
        names.append(found.group(1) if found else "")
    return list(zip(names, parts))


def _declarator_product(expr: str) -> bool:
    """Does an initialiser multiply two non-literal operands, outside any subscript and with no 64-bit cast?"""
    if _WIDE_CAST.search(expr):
        return False
    expr = _outside_brackets(expr)
    for left, right in _split_products(expr):
        if _is_literal(left) or _is_literal(right) or (_builtin_only(left) and _builtin_only(right)):
            continue
        return True
    return False


def _unit_findings(unit: _Unit, rel: str, py_lines: list[str]) -> list[Finding]:
    code = _strip_comments(unit.text)
    kernel_lines, code_lines = unit.text.split("\n"), code.split("\n")
    out: list[Finding] = []

    def add(rule: str, offset: int, message: str) -> None:
        if not _marked(unit, kernel_lines, offset, py_lines):
            out.append(Finding(rel, unit.line + offset, rule, message))

    wide = {position for m in _LONG_PAIR.finditer(code) for position in (m.start(), m.end() - 4)}  # both words of `long long`
    for match in _LONG_WORD.finditer(code):
        if match.start() in wide:
            continue
        offset = code.count("\n", 0, match.start())
        snippet = code_lines[offset].strip()[:80]
        add(RULE_LONG, offset, f"C type `long` is 32 bits under NVRTC on Windows; use `long long`/`size_t`/`int64_t`: `{snippet}`")
    for match in _INT_INIT.finditer(code):
        for name, init in _declared(match.group(1), match.group(2)):
            if _INDEX_NAME.search(name) and _declarator_product(init):
                offset = code.count("\n", 0, match.start())
                expr = " ".join(init.split())[:80]
                add(RULE_PRODUCT, offset, f"`int {name} = {expr}` multiplies two sizes in 32 bits; cast one operand to `long long`")
    return out


def _findings_in(parsed_tree: ast.Module, source: str, rel: str, aliases: ImportAliases) -> list[Finding]:
    py_lines = source.splitlines()
    out: list[Finding] = []
    for unit in _units(parsed_tree, aliases):
        out.extend(_unit_findings(unit, rel, py_lines))
    return out


def find_cuda_kernel_integer_width(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every platform-width ``long`` and every ``int`` index product in a CUDA kernel string under *root*.

    Sorted by path and line. Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError``
    for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.source, parsed.rel, ImportAliases.from_tree(parsed.tree)))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_cuda_kernel_integer_width(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any ``cuda-platform-long`` finding (and on ``cuda-int-index-product`` with *include_advisory*).

    With *baseline_path*, fail only on findings the baseline does not accept.
    """
    found = find_cuda_kernel_integer_width(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if not include_advisory:
        found = [f for f in found if f.rule == RULE_LONG]
    guidance = (
        "spell 64-bit indices `long long`/`size_t`/`int64_t` and cast one operand of an index product; `// width-ok: <reason>` for a deliberate 32-bit value"
    )
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_kernel_integer_width", refresh_command="PY_CI_SHARED_REFRESH=cuda_kernel_integer_width")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA kernel integer-width finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
