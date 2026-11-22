"""(internal) part of pyutilz.dev.code_audit; see package __init__ for docs."""

from __future__ import annotations

import ast
from pathlib import Path

from .._core.node_index import walk as walk_nodes
from ._base import Finding, _DEFAULT_EXCLUDE_DIRS, _iter_py_files, _line_text, _read_src_lines, _safe_parse, walk_tree, is_line_exempt

__all__ = ["scan_salted_hash_seeds"]

# --- the builtin hash() feeding a random seed ---------------------------------------------------------
#
# ``hash(str)`` is salted per process (PYTHONHASHSEED), so a seed derived from it changes on every run: the benchmark
# that "reproduces" a result reproduces it only inside one interpreter (mlframe audit 01-F2, 40-F12). ``zlib.crc32`` or
# ``hashlib`` is the stable substitute. The scanner cannot tell a str from an int argument, and ``hash(int)`` is stable,
# so it reports every ``hash(...)`` that feeds a seed. Exempt a genuinely integer-only use with a reasoned marker:
# ``# audit-exempt: salted_hash_seed -- <reason of 19+ chars>``.

_SEED_CALLEES = frozenset({"default_rng", "RandomState", "seed", "manual_seed", "SeedSequence", "Random"})
_SEED_KEYWORDS = frozenset({"seed", "random_state", "rng_seed"})
_CHECK = "salted_hash_seed"


def _callee_name(call: ast.Call) -> str | None:
    """The last name of a call's callee: ``f`` for ``f(...)`` and for ``mod.f(...)``."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _has_builtin_hash(node: ast.AST) -> bool:
    """True when ``hash(...)`` (the builtin, by bare name) is called anywhere inside *node*."""
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "hash" for n in walk_nodes(node))


def _target_names(stmt: ast.Assign | ast.AnnAssign) -> list[str]:
    """Plain or attribute names an assignment writes to, lower-cased."""
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id.lower())
        elif isinstance(target, ast.Attribute):
            names.append(target.attr.lower())
    return names


def _feeds_seed(node: ast.AST) -> bool:
    """True when *node* is a seed-consuming call or a seed assignment whose value calls the builtin ``hash``."""
    if isinstance(node, ast.Call):
        if _callee_name(node) in _SEED_CALLEES and any(_has_builtin_hash(arg) for arg in node.args):
            return True
        return any(kw.arg in _SEED_KEYWORDS and _has_builtin_hash(kw.value) for kw in node.keywords)
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
        return any("seed" in name for name in _target_names(node)) and _has_builtin_hash(node.value)
    return False


def scan_salted_hash_seeds(
    root: Path,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS,
) -> list[Finding]:
    """Find builtin ``hash(...)`` calls whose value reaches a random seed, in a call's seed argument or keyword or in an
    assignment to a ``seed``-named target.

    Each offending line is reported once. Severity P2: the run is reproducible only inside one interpreter, so a result
    quoted from it cannot be re-derived in another process.
    """
    findings: list[Finding] = []
    for py in _iter_py_files(root, exclude_dirs):
        tree = _safe_parse(py)
        if tree is None:
            continue
        src_lines = _read_src_lines(py)
        rel = py.relative_to(root).as_posix()
        reported: set[int] = set()
        for node in walk_tree(tree):
            if not isinstance(node, (ast.Call, ast.Assign, ast.AnnAssign)) or not _feeds_seed(node):
                continue
            lineno = node.lineno
            if lineno in reported or is_line_exempt(src_lines, lineno, _CHECK):
                continue
            reported.add(lineno)
            findings.append(
                Finding(
                    check=_CHECK,
                    severity="P2",
                    file=rel,
                    line=lineno,
                    snippet=_line_text(src_lines, lineno),
                    detail=(
                        "builtin hash() feeds a random seed. hash(str) is salted per process, so the seed (and any result it "
                        "produces) changes on every run. Use zlib.crc32 or hashlib for a stable seed."
                    ),
                )
            )
    return findings
