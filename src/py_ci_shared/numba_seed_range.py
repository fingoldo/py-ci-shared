"""A full-64-bit seed handed to numba, which types seeds as int64 and rejects half of them.

``np.random.seed`` inside ``@njit`` (and helpers such as ``set_numba_random_seed``) types the seed as int64: anything
``>= 2**63`` raises ``OverflowError: int too big to convert``. The idiom for "an RNG stream the caller did not choose",
``struct.unpack("<Q", os.urandom(8))[0]``, crosses that limit about half the time, and because a save/restore scope
must not mask the guarded block's own error, the failure is normally caught into a debug log. The scope then
silently does nothing on half its calls, which surfaced as run-order flakiness across tests and fits.

Flagged: a call to a numba seeding function (a callee whose name contains ``seed`` and ``numba``/``nb``/``njit``, or
a ``seed``-named function defined with ``@njit``/``@jit`` in the same file) whose argument is, or is a local/module
name assigned from, a value that can reach ``2**63``:

* ``struct.unpack("<Q", ...)``/``unpack_from`` of an unsigned 64-bit field;
* ``getrandbits(n)``/``secrets.randbits(n)`` with ``n >= 64``; ``int.from_bytes`` of ``os.urandom(n >= 8)``,
  ``secrets.token_bytes(n >= 8)`` or a hash digest;
* ``randint``/``randrange``/``integers`` with a literal bound above ``2**63`` (``2**64``, ``1 << 64``,
  ``np.iinfo(np.uint64).max``) or ``dtype=np.uint64``.

A value masked to 63 bits (``& ((1 << 63) - 1)``, ``& _SEED_MASK``, ``% 2**63``, ``>> 1``) is safe; ``int(...)``
around a wide value does not narrow it. A line carrying ``# seed-range-ok`` is an explicit opt-out.

Usage::

    from py_ci_shared.numba_seed_range import assert_numba_seeds_fit_int64

    def test_numba_seeds_fit_int64():
        assert_numba_seeds_fit_int64(REPO / "src")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._gate_report import enclosing_functions, line_has_marker, report, scan_tree, skip_set

__all__ = ["RULE", "REFRESH_FLAG", "find_wide_numba_seeds", "assert_numba_seeds_fit_int64"]

RULE = "numba-seed-exceeds-int64"
REFRESH_FLAG = "--refresh-numba-seed-range-baseline"
MARKER = "seed-range-ok"
INT64_LIMIT = 2**63
_SINK_NAME = re.compile(r"(?i)seed")
_NUMBA_NAME = re.compile(r"(?i)numba|njit|(?:^|_)nb(?:_|$)")
_MASK_NAME = re.compile(r"(?i)mask|max_seed|int63|int64_max")
_UNSIGNED_64 = re.compile(r"^[<>=!@]?Q$")


def _const(node: ast.AST, aliases: ImportAliases) -> Optional[int]:
    """Evaluate a small integer constant expression (``2**64``, ``(1 << 63) - 1``, ``np.iinfo(np.uint64).max``)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const(node.operand, aliases)
        return -inner if inner is not None else None
    if isinstance(node, ast.BinOp):
        left, right = _const(node.left, aliases), _const(node.right, aliases)
        if left is None or right is None:
            return None
        ops = {ast.Pow: lambda a, b: a**b if 0 <= b <= 256 else None, ast.LShift: lambda a, b: a << b if 0 <= b <= 256 else None}
        ops.update({ast.Sub: lambda a, b: a - b, ast.Add: lambda a, b: a + b, ast.Mult: lambda a, b: a * b, ast.RShift: lambda a, b: a >> b})
        fn = ops.get(type(node.op))
        return fn(left, right) if fn else None
    if isinstance(node, ast.Attribute) and node.attr == "max" and isinstance(node.value, ast.Call):
        qualified = aliases.qualified_name(node.value) or ""
        if qualified.endswith("iinfo") and node.value.args and "uint64" in ast.unparse(node.value.args[0]):
            return 2**64 - 1
    return None


def _call_name(call: ast.Call, aliases: ImportAliases) -> str:
    return aliases.qualified_name(call) or (call.func.attr if isinstance(call.func, ast.Attribute) else "")


def _byte_width(node: ast.AST, aliases: ImportAliases) -> Optional[int]:
    """Bytes produced by ``os.urandom(n)``/``token_bytes(n)`` (``n``), a digest (8+), else ``None``."""
    if isinstance(node, ast.Call):
        name = _call_name(node, aliases)
        if name.endswith(("urandom", "token_bytes", "randbytes")) and node.args:
            return _const(node.args[0], aliases)
        if name.endswith(("digest",)):
            return 8
    return None


def _wide_source(node: ast.AST, aliases: ImportAliases) -> Optional[str]:
    """A short description of why *node* can reach ``2**63``, or ``None`` when it cannot (or is masked)."""
    if isinstance(node, ast.BinOp):
        return _wide_binop(node, aliases)
    if isinstance(node, ast.Subscript):
        return _wide_source(node.value, aliases)
    if isinstance(node, ast.Call):
        return _wide_call(node, aliases)
    return None


def _wide_binop(node: ast.BinOp, aliases: ImportAliases) -> Optional[str]:
    if isinstance(node.op, ast.RShift):
        return None
    if isinstance(node.op, (ast.BitAnd, ast.Mod)):
        bound = _const(node.right, aliases)
        if bound is not None and bound <= INT64_LIMIT:
            return None
        if isinstance(node.right, (ast.Name, ast.Attribute)) and _MASK_NAME.search(ast.unparse(node.right)):
            return None
    return _wide_source(node.left, aliases)


def _unsigned_64_format(fmt: ast.expr) -> bool:
    if not isinstance(fmt, ast.Constant) or not isinstance(fmt.value, (str, bytes)):
        return False
    text = fmt.value if isinstance(fmt.value, str) else fmt.value.decode("ascii", "replace")
    return bool(_UNSIGNED_64.match(text))


def _wide_bound(node: ast.Call, short: str, aliases: ImportAliases) -> Optional[str]:
    if any(k.arg == "dtype" and "uint64" in ast.unparse(k.value) for k in node.keywords):
        return f"{short}(..., dtype=uint64)"
    candidates = [*node.args[:2], *(k.value for k in node.keywords if k.arg in ("high", "b", "stop"))]
    bounds = [b for b in (_const(a, aliases) for a in candidates) if b is not None]
    return f"{short} up to {max(bounds)}" if bounds and max(bounds) > INT64_LIMIT else None


def _wide_call(node: ast.Call, aliases: ImportAliases) -> Optional[str]:
    short = _call_name(node, aliases).rsplit(".", 1)[-1]
    if not node.args and short not in ("randint", "randrange", "integers", "random_integers"):
        return None
    if short == "int":
        return _wide_source(node.args[0], aliases)
    if short in ("unpack", "unpack_from"):
        return f"struct.{short}({ast.unparse(node.args[0])})" if _unsigned_64_format(node.args[0]) else None
    if short in ("getrandbits", "randbits"):
        bits = _const(node.args[0], aliases)
        return f"{short}({bits})" if bits is not None and bits >= 64 else None
    if short == "from_bytes":
        width = _byte_width(node.args[0], aliases)
        return f"int.from_bytes of {width}+ bytes" if width is not None and width >= 8 else None
    if short in ("randint", "randrange", "integers", "random_integers"):
        return _wide_bound(node, short, aliases)
    return None


def _njit_seeders(tree: ast.Module, aliases: ImportAliases) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and _SINK_NAME.search(node.name):
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                if (aliases.qualified_name(target) or "").rsplit(".", 1)[-1] in ("njit", "jit", "generated_jit"):
                    out.add(node.name)
    return out


def _assigned(tree: ast.Module) -> dict[int, dict[str, list[ast.expr]]]:
    """``{id(scope): {name: [values]}}`` for module scope and each function."""
    out: dict[int, dict[str, list[ast.expr]]] = {}

    def visit(node: ast.AST, scope: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, id(child))
                continue
            if isinstance(child, (ast.Assign, ast.AnnAssign)) and child.value is not None:
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        out.setdefault(scope, {}).setdefault(t.id, []).append(child.value)
            visit(child, scope)

    visit(tree, id(tree))
    return out


class _FileCheck:
    """Numba seeding calls in one file whose argument can reach ``2**63``."""

    def __init__(self, parsed: ParsedFile) -> None:
        self.parsed = parsed
        self.aliases = ImportAliases.from_tree(parsed.tree)
        self.njit_seeders = _njit_seeders(parsed.tree, self.aliases)
        self.assigned = _assigned(parsed.tree)
        self.functions = enclosing_functions(parsed.tree)
        self.lines = parsed.source.splitlines()
        self.out: list[Finding] = []

    def run(self) -> list[Finding]:
        self._scan(self.parsed.tree, [id(self.parsed.tree)])
        return self.out

    def _scan(self, node: ast.AST, scopes: list[int]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = [id(child), *scopes] if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scopes
            if isinstance(child, ast.Call):
                self._check(child, scopes)
            self._scan(child, inner)

    def _source_of(self, arg: ast.expr, scopes: list[int]) -> Optional[str]:
        direct = _wide_source(arg, self.aliases)
        if direct:
            return direct
        unwrapped = arg.args[0] if isinstance(arg, ast.Call) and _call_name(arg, self.aliases) == "int" and arg.args else arg
        if not isinstance(unwrapped, ast.Name):
            return None
        for scope in scopes:
            for value in self.assigned.get(scope, {}).get(unwrapped.id, []):
                why = _wide_source(value, self.aliases)
                if why:
                    return f"{unwrapped.id} = {why}"
        return None

    def _is_sink(self, call: ast.Call) -> bool:
        name = _call_name(call, self.aliases)
        short = name.rsplit(".", 1)[-1]
        return short in self.njit_seeders or bool(_SINK_NAME.search(short) and _NUMBA_NAME.search(name))

    def _check(self, call: ast.Call, scopes: list[int]) -> None:
        if not self._is_sink(call) or line_has_marker(self.lines, call.lineno, MARKER):
            return
        short = _call_name(call, self.aliases).rsplit(".", 1)[-1]
        for arg in [*call.args, *(k.value for k in call.keywords)]:
            why = self._source_of(arg, scopes)
            if why:
                where = self.functions.get(id(call), "<module>")
                msg = f"{where}: {short}({ast.unparse(arg)}) can receive a seed >= 2**63 ({why}); mask it to 63 bits"
                self.out.append(Finding(self.parsed.rel, call.lineno, RULE, msg))
                return


def _file_findings(parsed: ParsedFile) -> list[Finding]:
    return _FileCheck(parsed).run()


def _collect(root: Union[str, Path], *, skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    findings = [f for parsed in scan for f in _file_findings(parsed)]
    return sorted(findings, key=lambda f: (f.path, f.line)), scan


def find_wide_numba_seeds(
    root: Union[str, Path], *, skip_dir_names: Iterable[str] = (), include_tests: bool = False, use_git: Optional[bool] = None
) -> list[Finding]:
    """Every numba seeding call that can receive a value ``>= 2**63`` under *root*, plus one ``unparsed-file``
    finding per unparsable file."""
    findings, scan = _collect(root, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    return findings + scan.unparsed_findings()


def assert_numba_seeds_fit_int64(
    root: Union[str, Path],
    *,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a wide numba seed (new against *baseline_path* when given), on fewer than *min_files* parsed files, and
    on any unparsable file. Refresh with ``--refresh-numba-seed-range-baseline``."""
    findings, scan = _collect(root, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    report(
        findings,
        gate="numba-seed-range",
        flag=REFRESH_FLAG,
        guidance="mask entropy-derived seeds before numba sees them: seed & ((1 << 63) - 1)",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
