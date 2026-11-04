"""LLM output ceilings written as a number someone chose by eye.

``max_tokens`` is a ceiling, not a reservation: billing follows the tokens actually produced, so a low cap saves
nothing and only truncates a paid answer mid-JSON. The recurring shape is a secondary call site passing 512, 2048
or 8192 against a model that serves 32k to 128k, and its quieter cousin, a DEFAULTED capability number
(``getattr(provider, "max_output_tokens", 8192)``, ``cfg.max_tokens or 4096``) that is indistinguishable from a
measured one at the call site. Both were shipped: a dozen call sites in one project, and a cold catalogue that made
a provider report 8192 for a 65,536-token model.

Flagged, for each name in *names* (``max_tokens``, ``max_output_tokens``, ``max_completion_tokens``,
``max_tokens_to_sample``, ``maxOutputTokens``):

* a keyword argument with an int literal: ``create(..., max_tokens=4096)``;
* a dict entry with an int literal: ``{"max_tokens": 4096}``;
* a parameter default: ``def call(prompt, max_tokens=4096)``;
* a literal fallback: ``max_tokens=cfg.limit or 4096``, ``cfg.max_tokens or 4096`` wherever it appears (assignment, ``return``),
  ``d.get("max_tokens", 4096)``, ``d.setdefault("max_tokens", 4096)``, ``getattr(p, "max_output_tokens", 4096)``;
* an assignment to such a name: ``self.max_tokens = 4096``, ``MAX_TOKENS = 4096``;
* a module constant bound once to an int, passed on (``CAP = 4096; create(max_tokens=CAP)``); ``2**12`` and ``4096.0`` count as literals.

Names match case-insensitively and as camelCase (``maxTokens``).

Not flagged: ``0`` (every provider in use reads it as "use your maximum"), values below *allow_below* (default 16:
a liveness probe asking for one token is deliberate), test directories (a literal there is the subject of a test),
and lines carrying ``# token-ceiling-ok``.

Usage::

    from py_ci_shared.hardcoded_token_ceilings import assert_no_hardcoded_token_ceilings

    def test_no_hardcoded_token_ceilings():
        assert_no_hardcoded_token_ceilings(REPO / "src", baseline_path=HERE / "_token_ceilings_baseline.json")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ParsedFile, ScanResult
from ._core.node_index import walk as _fast_walk
from ._gate_report import enclosing_functions, line_has_marker, report, scan_tree, skip_set

__all__ = ["DEFAULT_NAMES", "RULE", "REFRESH_FLAG", "find_hardcoded_token_ceilings", "assert_no_hardcoded_token_ceilings"]

RULE = "hardcoded-token-ceiling"
REFRESH_FLAG = "--refresh-token-ceilings-baseline"
MARKER = "token-ceiling-ok"
DEFAULT_NAMES: frozenset[str] = frozenset(
    {"max_tokens", "max_output_tokens", "max_completion_tokens", "max_tokens_to_sample", "maxOutputTokens", "max_new_tokens", "num_predict"}
)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _canon(name: object) -> str:
    """``maxOutputTokens`` / ``MAX_OUTPUT_TOKENS`` -> ``max_output_tokens``."""
    return _CAMEL.sub("_", str(name)).lower()


_Consts = dict[str, int]


def _int_literal(node: Optional[ast.expr], consts: Optional[_Consts] = None) -> Optional[int]:
    """The int *node* is: a literal, an int-valued float, ``a ** b`` / ``a * b`` / ``a + b`` of those, or a module constant bound once."""
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None
    if isinstance(node, ast.Name):
        return (consts or {}).get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Pow, ast.Mult, ast.Add)):
        left, right = _int_literal(node.left, consts), _int_literal(node.right, consts)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Pow):
            return left**right if 0 <= right <= 64 and abs(left) <= 1 << 16 else None
        return left * right if isinstance(node.op, ast.Mult) else left + right
    return None


def _module_consts(tree: ast.Module) -> _Consts:
    """Module-level names assigned exactly once, to an int."""
    counts: dict[str, int] = {}
    values: dict[str, ast.expr] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            pairs = [(tg, stmt.value) for tg in stmt.targets]
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            pairs = [(stmt.target, stmt.value)]
        else:
            continue
        for target, value in pairs:
            if isinstance(target, ast.Name):
                counts[target.id] = counts.get(target.id, 0) + 1
                values[target.id] = value
    out: _Consts = {}
    for name, value in values.items():
        if counts[name] == 1:
            literal = _int_literal(value, out)
            if literal is not None:
                out[name] = literal
    return out


def _literal_in(value: ast.expr, consts: Optional[_Consts] = None) -> Iterator[tuple[int, str]]:
    """``(literal, shape)`` for an int literal *value* or an int literal fallback inside it."""
    direct = _int_literal(value, consts)
    if direct is not None:
        yield direct, "literal"
        return
    if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
        for operand in value.values:
            fallback = _int_literal(operand, consts)
            if fallback is not None:
                yield fallback, "fallback"
    if isinstance(value, ast.IfExp):
        for branch in (value.body, value.orelse):
            fallback = _int_literal(branch, consts)
            if fallback is not None:
                yield fallback, "fallback"


def _defaulted_lookup(call: ast.Call, names: frozenset[str]) -> Optional[tuple[str, int]]:
    """``d.get("max_tokens", 4096)`` / ``getattr(p, "max_output_tokens", 4096)`` -> ``(name, 4096)``."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in ("get", "setdefault") and len(call.args) == 2:
        key, default = call.args
    elif isinstance(func, ast.Name) and func.id == "getattr" and len(call.args) == 3:
        key, default = call.args[1], call.args[2]
    else:
        return None
    value = _int_literal(default)
    if isinstance(key, ast.Constant) and _canon(key.value) in names and value is not None:
        return str(key.value), value
    return None


_Site = tuple[ast.AST, str, int, str]


def _call_sites(node: ast.Call, names: frozenset[str], consts: _Consts) -> Iterator[_Site]:
    for kw in node.keywords:
        if kw.arg is not None and _canon(kw.arg) in names:
            for literal, shape in _literal_in(kw.value, consts):
                yield kw.value, kw.arg, literal, shape
    lookup = _defaulted_lookup(node, names)
    if lookup is not None:
        yield node, lookup[0], lookup[1], "fallback"


def _dict_sites(node: ast.Dict, names: frozenset[str], consts: _Consts) -> Iterator[_Site]:
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and _canon(key.value) in names:
            for literal, shape in _literal_in(value, consts):
                yield value, str(key.value), literal, shape


def _default_sites(node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda], names: frozenset[str]) -> Iterator[_Site]:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    pairs = list(zip(positional[len(positional) - len(args.defaults) :], args.defaults))
    pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
    for arg, default in pairs:
        if _canon(arg.arg) in names:
            for literal, _ in _literal_in(default):
                yield default, arg.arg, literal, "default"


def _read_name(node: ast.AST, names: frozenset[str]) -> Optional[str]:
    """The ceiling setting *node* reads (``cfg.max_tokens``, ``max_tokens``, ``d["max_tokens"]``, ``d.get("max_tokens")``)."""
    if isinstance(node, ast.Name):
        return node.id if _canon(node.id) in names else None
    if isinstance(node, ast.Attribute):
        return node.attr if _canon(node.attr) in names else None
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and _canon(node.slice.value) in names:
        return str(node.slice.value)
    if isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        key = node.args[0]
        if isinstance(key, ast.Constant) and _canon(key.value) in names:
            return str(key.value)
    return None


def _or_sites(node: ast.BoolOp, names: frozenset[str], consts: _Consts) -> Iterator[_Site]:
    """``cfg.max_tokens or 4096`` anywhere (a ``return``, an assignment, an argument): the defaulted capability number."""
    if not isinstance(node.op, ast.Or):
        return
    name = next((n for n in (_read_name(v, names) for v in node.values) if n), None)
    if name is None:
        return
    for operand in node.values:
        fallback = _int_literal(operand, consts)
        if fallback is not None:
            yield node, name, fallback, "fallback"


def _assign_sites(node: Union[ast.Assign, ast.AnnAssign], names: frozenset[str], consts: _Consts) -> Iterator[_Site]:
    """``self.max_tokens = 4096`` / ``MAX_TOKENS = 4096`` / ``max_tokens = cfg.max_tokens or 4096``."""
    value = node.value
    if value is None:
        return
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        name = _read_name(target, names)
        if name is not None:
            for literal, shape in _literal_in(value, consts):
                yield value, name, literal, shape


def _sites(tree: ast.Module, names: frozenset[str]) -> Iterator[_Site]:
    """``(node, name, literal, shape)`` for every literal ceiling in *tree*, each node and literal once."""
    consts = _module_consts(tree)
    seen: set[tuple[int, int]] = set()
    for node in _fast_walk(tree):
        found: Iterator[_Site] = iter(())
        if isinstance(node, ast.Call):
            found = _call_sites(node, names, consts)
        elif isinstance(node, ast.Dict):
            found = _dict_sites(node, names, consts)
        elif isinstance(node, ast.BoolOp):
            found = _or_sites(node, names, consts)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            found = _assign_sites(node, names, consts)
        for site in found:
            if (id(site[0]), site[2]) not in seen:
                seen.add((id(site[0]), site[2]))
                yield site
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield from _default_sites(node, names)


_SHAPES = {
    "literal": "{name}={value} is a hand-picked output ceiling",
    "fallback": "{name} falls back to {value} when the real ceiling is unknown",
    "default": "parameter {name} defaults to {value}",
}


def _file_findings(parsed: ParsedFile, names: frozenset[str], allow_below: int) -> list[Finding]:
    lines = parsed.source.splitlines()
    functions = enclosing_functions(parsed.tree)
    out: list[Finding] = []
    for node, name, value, shape in _sites(parsed.tree, names):
        line = getattr(node, "lineno", 1)
        if value == 0 or value < allow_below or line_has_marker(lines, line, MARKER):
            continue
        where = functions.get(id(node), "<module>")
        out.append(Finding(parsed.rel, line, RULE, f"{where}: " + _SHAPES[shape].format(name=name, value=value)))
    return out


def _collect(
    root: Union[str, Path], *, names: Iterable[str], allow_below: int, skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]
) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    wanted = frozenset(_canon(n) for n in names)
    findings = [f for parsed in scan for f in _file_findings(parsed, wanted, allow_below)]
    return sorted(findings, key=lambda f: (f.path, f.line)), scan


def find_hardcoded_token_ceilings(
    root: Union[str, Path],
    *,
    names: Iterable[str] = DEFAULT_NAMES,
    allow_below: int = 16,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every literal or literal-fallback output ceiling under *root*, plus one ``unparsed-file`` finding per file
    that could not be parsed."""
    findings, scan = _collect(root, names=names, allow_below=allow_below, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    return findings + scan.unparsed_findings()


def assert_no_hardcoded_token_ceilings(
    root: Union[str, Path],
    *,
    names: Iterable[str] = DEFAULT_NAMES,
    allow_below: int = 16,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
    allow_unparsed: bool = False,
) -> None:
    """Fail on a hardcoded ceiling (new against *baseline_path* when given), on fewer than *min_files* parsed files,
    and on any unparsable file. Refresh with ``--refresh-token-ceilings-baseline``."""
    findings, scan = _collect(root, names=names, allow_below=allow_below, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    report(
        findings,
        gate="hardcoded-token-ceilings",
        flag=REFRESH_FLAG,
        guidance="derive the ceiling from the provider/model (its advertised max output, fitted to the context window), never a round guess",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
