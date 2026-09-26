"""Text-mode writes that put CRLF into a file that must stay LF.

On Windows, ``Path.write_text`` and ``open(path, "w")`` translate every ``\\n`` to ``\\r\\n`` unless ``newline=`` is
given. For most files that is harmless. For a shell script it is a crash (``$'\\r': command not found``), for a YAML
file checked byte-wise by yamllint it is a red hook, and for a committed baseline it is a whole-file diff that the
mixed-line-ending hook then flips back. The shipped instance: a hook config repaired to LF and then rewritten with
``write_text`` on the next edit, which put the CRLF straight back.

The check flags a text-mode write with no ``newline=`` argument when the file written is LF-required:

* ``X.write_text(...)``, ``open(X, "w"|"a"|"x"...)``, ``io.open``/``codecs.open`` and ``X.open("w")``; binary modes
  (``"wb"``) and ``write_bytes`` are the fix, never findings;
* the target is LF-required when its expression (or, for a plain name, a value assigned to that name in the same
  function or at module scope) contains a string literal ending in one of *lf_suffixes* (``.sh``, ``.yml``...), or
  an identifier in it matches *name_pattern* (by default ``baseline``: committed ratchet files).
* a target built from ``tempfile``/``tmp_path``/``gettempdir``... is a scratch file and never a finding.

Test directories and ``test_*.py`` files are skipped by default: a fixture written into ``tmp_path`` is the subject
of a test, not a committed file. A line carrying ``# lf-ok`` is an explicit, reviewable opt-out.

Usage::

    from py_ci_shared.lf_file_writes import assert_no_crlf_writes

    def test_lf_files_are_written_with_newline():
        assert_no_crlf_writes(REPO / "src", baseline_path=HERE / "_lf_file_writes_baseline.json")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._core.node_index import walk as _fast_walk
from ._gate_report import enclosing_functions, line_has_marker, report, scan_tree, skip_set

__all__ = ["DEFAULT_LF_SUFFIXES", "RULE", "REFRESH_FLAG", "find_crlf_writes", "assert_no_crlf_writes"]

RULE = "crlf-write"
REFRESH_FLAG = "--refresh-lf-file-writes-baseline"
MARKER = "lf-ok"
DEFAULT_LF_SUFFIXES: tuple[str, ...] = (".sh", ".bash", ".yml", ".yaml", ".gitattributes", ".editorconfig")
DEFAULT_NAME_PATTERN = r"baseline"
_OPENERS = frozenset({"open", "io.open", "codecs.open", "builtins.open"})
_MAX_SHOWN = 80


def _mode_of(call: ast.Call, position: int) -> Optional[str]:
    """The literal mode argument, or ``None`` when absent (read mode) or not a literal."""
    node: Optional[ast.expr] = None
    for kw in call.keywords:
        if kw.arg == "mode":
            node = kw.value
    if node is None and len(call.args) > position:
        node = call.args[position]
    if node is None:
        return "r"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _has_newline(call: ast.Call, position: int) -> bool:
    return any(kw.arg == "newline" or kw.arg is None for kw in call.keywords) or len(call.args) > position


def _writes_text(mode: Optional[str]) -> bool:
    return mode is not None and "b" not in mode and any(c in mode for c in "wax+")


def _bindings(node: ast.AST) -> list[tuple[ast.expr, ast.expr]]:
    """``(target, value)`` pairs a statement binds: assignments, walrus, ``with ... as``, ``for`` targets."""
    if isinstance(node, ast.Assign):
        return [(t, node.value) for t in node.targets]
    if isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
        return [(node.target, node.value)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [(i.optional_vars, i.context_expr) for i in node.items if i.optional_vars is not None]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [(node.target, node.iter)]
    return []


def _assignments(tree: ast.Module) -> dict[int, dict[str, list[ast.expr]]]:
    """``{id(scope): {name: [assigned values]}}`` for the module and every function."""
    out: dict[int, dict[str, list[ast.expr]]] = {}

    def visit(node: ast.AST, scope: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child)
                continue
            for target, value in _bindings(child):
                if isinstance(target, ast.Name):
                    out.setdefault(id(scope), {}).setdefault(target.id, []).append(value)
            visit(child, scope)

    visit(tree, tree)
    return out


_TEMP = re.compile(r"\b(?:tempfile|mkdtemp|mkstemp|gettempdir|TemporaryDirectory|NamedTemporaryFile|tmp_path|tmp_path_factory|tmpdir)\b")


class _Resolver:
    def __init__(self, tree: ast.Module, suffixes: Sequence[str], name_pattern: Optional[str]) -> None:
        self.assigned = _assignments(tree)
        alt = "|".join(re.escape(s.lstrip(".")) for s in suffixes)
        self.suffix_re = re.compile(rf"""['"][^'"\n]*\.(?:{alt})['"]""", re.IGNORECASE) if alt else None
        self.name_re = re.compile(name_pattern, re.IGNORECASE) if name_pattern else None

    def exprs(self, expr: ast.expr, scopes: Sequence[int], depth: int = 0) -> list[ast.expr]:
        """*expr* and, for every name in it, the values assigned to that name (followed a few hops)."""
        out = [expr]
        if depth >= 6:
            return out
        for sub in _fast_walk(expr):
            if isinstance(sub, ast.Name):
                for scope in scopes:
                    for value in self.assigned.get(scope, {}).get(sub.id, []):
                        if value is not expr:
                            out += self.exprs(value, scopes, depth + 1)
        return out

    def lf_required(self, expr: ast.expr, scopes: Sequence[int]) -> bool:
        chain = self.exprs(expr, scopes)
        texts = [ast.unparse(e) for e in chain]
        if any(_TEMP.search(t) for t in texts):
            return False  # a scratch file in a temp directory is never committed or linted
        if self.suffix_re is not None and any(self.suffix_re.search(t) for t in texts):
            return True
        if self.name_re is not None:
            # Matched against identifiers only: `baseline_path` names a committed ratchet file, while a literal
            # such as "bench_baseline.json" usually names a benchmark subject.
            names = {n.id for e in chain for n in _fast_walk(e) if isinstance(n, ast.Name)}
            names |= {n.attr for e in chain for n in _fast_walk(e) if isinstance(n, ast.Attribute)}
            return any(self.name_re.search(n) for n in names)
        return False


def _scopes_by_node(tree: ast.Module) -> dict[int, list[int]]:
    """``{id(node): [innermost function id, ..., module id]}``."""
    out: dict[int, list[int]] = {}

    def visit(node: ast.AST, chain: list[int]) -> None:
        for child in ast.iter_child_nodes(node):
            out[id(child)] = chain
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, [id(child), *chain])
            else:
                visit(child, chain)

    visit(tree, [id(tree)])
    return out


def _target(call: ast.Call, aliases: ImportAliases) -> Optional[ast.expr]:
    """The file expression of a text-mode write without ``newline=``, or ``None``."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "write_text":
        # Path.write_text(data, encoding=None, errors=None, newline=None): newline is the 4th positional.
        return None if _has_newline(call, 3) else func.value
    qualified = aliases.qualified_name(call)
    if qualified in _OPENERS or (isinstance(func, ast.Name) and func.id == "open" and "open" not in aliases):
        if not call.args:
            return None
        # open(file, mode, buffering, encoding, errors, newline); codecs.open never translates, but is flagged
        # only in text mode like the rest, which is where a reader expects the translation.
        if qualified == "codecs.open":
            return None
        return call.args[0] if _writes_text(_mode_of(call, 1)) and not _has_newline(call, 5) else None
    if isinstance(func, ast.Attribute) and func.attr == "open" and qualified not in _OPENERS:
        # Path.open(mode, buffering, encoding, errors, newline)
        if isinstance(func.value, ast.Name) and func.value.id in aliases:
            return None  # `module.open(...)` of some other library
        return func.value if _writes_text(_mode_of(call, 0)) and not _has_newline(call, 4) else None
    return None


def _file_findings(parsed: ParsedFile, suffixes: Sequence[str], name_pattern: Optional[str]) -> list[Finding]:
    tree = parsed.tree
    aliases = ImportAliases.from_tree(tree)
    resolver = _Resolver(tree, suffixes, name_pattern)
    scopes = _scopes_by_node(tree)
    functions = enclosing_functions(tree)
    lines = parsed.source.splitlines()
    out: list[Finding] = []
    for node in _fast_walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _target(node, aliases)
        if target is None or line_has_marker(lines, node.lineno, MARKER):
            continue
        if not resolver.lf_required(target, scopes.get(id(node), [id(tree)])):
            continue
        shown = ast.unparse(target)
        shown = shown if len(shown) <= _MAX_SHOWN else shown[: _MAX_SHOWN - 3] + "..."
        where = functions.get(id(node), "<module>")
        out.append(Finding(parsed.rel, node.lineno, RULE, f"{where}: text-mode write to {shown} without newline=, so Windows writes CRLF"))
    return out


def _collect(
    root: Union[str, Path],
    *,
    lf_suffixes: Sequence[str],
    name_pattern: Optional[str],
    skip_dir_names: Iterable[str],
    include_tests: bool,
    use_git: Optional[bool],
) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    findings = [f for parsed in scan for f in _file_findings(parsed, lf_suffixes, name_pattern)]
    return sorted(findings, key=lambda f: (f.path, f.line)), scan


def find_crlf_writes(
    root: Union[str, Path],
    *,
    lf_suffixes: Sequence[str] = DEFAULT_LF_SUFFIXES,
    name_pattern: Optional[str] = DEFAULT_NAME_PATTERN,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every text-mode write without ``newline=`` to an LF-required file under *root*, plus one finding per file
    that could not be parsed (rule ``unparsed-file``)."""
    findings, scan = _collect(
        root, lf_suffixes=lf_suffixes, name_pattern=name_pattern, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git
    )
    return findings + scan.unparsed_findings()


def assert_no_crlf_writes(
    root: Union[str, Path],
    *,
    lf_suffixes: Sequence[str] = DEFAULT_LF_SUFFIXES,
    name_pattern: Optional[str] = DEFAULT_NAME_PATTERN,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a CRLF-producing write to an LF-required file (new against *baseline_path* when given), on fewer than
    *min_files* parsed files, and on any unparsable file. Refresh with ``--refresh-lf-file-writes-baseline``."""
    findings, scan = _collect(
        root, lf_suffixes=lf_suffixes, name_pattern=name_pattern, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git
    )
    report(
        findings,
        gate="lf-file-writes",
        flag=REFRESH_FLAG,
        guidance='write with write_bytes(text.encode("utf-8")), or pass newline="\\n" to open()/write_text()',
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
