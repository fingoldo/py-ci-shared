"""Shared check: no production module builds a timestamp with `datetime.utcnow()`.

`datetime.utcnow()` is deprecated from Python 3.12 and scheduled for REMOVAL, and it returns a
NAIVE value whose UTC-ness is carried by convention rather than by the object. Both halves matter:
the deprecation is a future breakage, and the naivety is the frame living in a comment instead of in
the expression.

WHY THIS IS AN AST CHECK AND NOT A GREP. production_scrapers tried the substring form twice and
recorded both failures (wave-17, in `test_wave_17_regressions.py`):

* `"datetime.utcnow()" not in src` -- passes for a module that writes no timestamp at all, and says
  nothing about `datetime.now()`, which is naive LOCAL time and the worse hazard.
* widening it to `"utcnow()" not in src` "immediately failed on the module's own COMMENT, which
  explains the fix by naming the function it replaced. That is the substring trap in miniature."

So the package abandoned the sweep and kept one behavioural test for one module -- and on 2026-09-09
it still had two live calls, one of them emitting a DeprecationWarning on every full-suite run.
`dashboard` kept a sweep by skipping lines that `startswith("#")`, which is better and still reads
a docstring's prose as code.

An AST walk has neither problem: a comment is not in the tree, and a docstring is a string.

THE `.date()` CASE IS NOT AN EXCUSE. `utcnow().date()` and `now(UTC).date()` give the same value
today, which is exactly why a value-based test would not catch this one and would not catch the
next. What is wrong is that the frame is stated by convention.

Usage::

    from py_ci_shared.naive_utcnow import assert_no_naive_utcnow

    def test_no_production_code_uses_naive_utcnow():
        assert_no_naive_utcnow(PACKAGE_DIR, skip_dir_names={"tests", "probes"})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from ._core import DEFAULT_EXCLUDE, Finding, ImportAliases, ScanResult, scan_python
from ._core.node_index import walk as _fast_walk

#: Kept for callers that imported it; the walk now uses the shared ``_core.DEFAULT_EXCLUDE`` (a superset).
_DEFAULT_SKIP_DIRS = tuple(sorted(DEFAULT_EXCLUDE))

#: ``datetime`` methods that return a NAIVE value meant as UTC. ``utcfromtimestamp`` has the same deprecation
#: and the same convention-carried frame as ``utcnow``.
NAIVE_UTC_METHODS = frozenset({"utcnow", "utcfromtimestamp"})

#: Libraries whose ``utcnow()`` returns an AWARE value, so the name is not the hazard there.
_AWARE_LIBRARIES = frozenset({"arrow", "pendulum"})

RULE = "naive-utcnow"


def _is_utcnow_call(node: ast.AST) -> bool:
    """`<anything>.utcnow()` -- kept for callers; the scan itself also matches the uncalled reference."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "utcnow"


def _offending_nodes(tree: ast.Module) -> list[ast.expr]:
    """Every ``<x>.utcnow`` / ``<x>.utcfromtimestamp`` REFERENCE, called or not.

    Matched on the ATTRIBUTE rather than on a full dotted path, because the import spelling varies across these
    repos (``import datetime as dt``, ``from datetime import datetime``, ``import datetime as _dt``) and a check
    that enumerated the spellings would miss the next one. Uncalled references matter as much as calls:
    ``Field(default_factory=datetime.utcnow)`` produces the same naive value on every row. The one exclusion goes
    through the import aliases: ``arrow.utcnow()``/``pendulum`` return aware values.
    """
    aliases = ImportAliases.from_tree(tree)
    called: dict[int, ast.Call] = {}
    for node in _fast_walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called[id(node.func)] = node
    out: list[ast.expr] = []
    for node in _fast_walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr in NAIVE_UTC_METHODS):
            continue
        qualified = aliases.qualified_name(node)
        if qualified is not None and qualified.split(".", 1)[0] in _AWARE_LIBRARIES:
            continue
        out.append(called.get(id(node), node))
    return sorted(out, key=lambda n: (n.lineno, n.col_offset))


def collect_naive_utcnow(root: Path, *, skip_dir_names: Iterable[str] = (), use_git: Optional[bool] = None) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)`` for *root*. Unparsed files are in ``scan.unparsed``, not dropped."""
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE | frozenset(skip_dir_names), use_git=use_git)
    findings = [Finding(f.rel, node.lineno, RULE, ast.unparse(node)) for f in scan for node in _offending_nodes(f.tree)]
    return findings, scan


def find_naive_utcnow(root: Path, *, skip_dir_names: Iterable[str] = (), use_git: Optional[bool] = None) -> list[str]:
    """``path:line: source`` for every real ``.utcnow``/``.utcfromtimestamp`` reference under *root*.

    Comments and docstrings cannot match: they are not in the tree. A file that cannot be read or parsed is
    reported as ``path:line: unparsable: <why>`` (or ``unreadable``), never skipped: a BOM, an encoding error
    or syntax newer than the interpreter used to make every AST gate pass that file vacuously. A missing *root*
    raises ``py_ci_shared._core.CorpusError``.
    """
    findings, scan = collect_naive_utcnow(root, skip_dir_names=skip_dir_names, use_git=use_git)
    out = [f"{f.path}:{f.line}: {f.message}" for f in findings] + [p.render() for p in scan.unparsed]
    return sorted(out, key=lambda s: (s.split(":", 1)[0], int(s.split(":", 2)[1])))


def assert_no_naive_utcnow(root: Path, *, skip_dir_names: Iterable[str] = (), min_files: int = 1, use_git: Optional[bool] = None) -> None:
    """Fail if any module under *root* references ``.utcnow``/``.utcfromtimestamp``, if any file could not be
    parsed, or if fewer than *min_files* files parsed (a wrong root is a failure, not a clean tree)."""
    import pytest

    findings, scan = collect_naive_utcnow(root, skip_dir_names=skip_dir_names, use_git=use_git)
    scan.min_files = min_files
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if scan.unparsed:
        problems.append(f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked:\n  " + "\n  ".join(p.render() for p in scan.unparsed))
    if findings:
        problems.append(
            f"{len(findings)} use(s) of `datetime.utcnow()`/`utcfromtimestamp()`, which are deprecated (removal is "
            "scheduled) and return a NAIVE value whose UTC-ness lives in a comment rather than in "
            "the expression. Use `datetime.now(datetime.UTC)` / `datetime.fromtimestamp(ts, datetime.UTC)`; when the "
            "output needs a `Z` suffix use `.strftime('%Y-%m-%dT%H:%M:%SZ')`, because an AWARE `isoformat()` already "
            "emits `+00:00` and appending `Z` produces `...+00:00Z`:\n  " + "\n  ".join(f"{f.path}:{f.line}: {f.message}" for f in findings)
        )
    if problems:
        pytest.fail("\n".join(problems))
