"""A test that asserts on the TEXT of a SQL statement, a query string or a rendered report instead of running it.

``assert "WHERE" in sql`` passes for a query that filters nothing useful, for one that mentions WHERE in a comment, and for a
query that returns every row. ``assert "OR TRUE" not in sql`` passes for a predicate that is always true under another
spelling. The check reads the SPELLING of the statement, so it cannot tell whether the statement does what it should. The
defect this rule exists for: a tenant filter was asserted with ``"WHERE" in`` and the query that shipped had no tenant
predicate at all.

Flagged: an ``assert`` in a test file whose test is a membership test with a string literal on the left and a value on the
right (``"SELECT" in x``, ``"OR 1=1" not in x``), when the literal contains an upper-case SQL keyword or probe: SELECT, INSERT,
UPDATE, DELETE, FROM, WHERE, JOIN, GROUP BY, ORDER BY, OR TRUE, OR 1=1. Keywords are matched whole and in upper case, so an
English word in a message is not a finding. A right operand that is a collection (a set, list, tuple or dict display or comprehension, `.keys()`, a name ending in `s`, anything read from `.headers`) is a membership test, not a probe.

Not judged here, because ``source_text_ban`` already bans them: ``inspect.getsource`` and reads of a Python file's text in a
test. Use the two gates together. A test that compares the rows a query returns, or the parsed AST of the statement, is the
nearest correct form and is not a finding.

Usage::

    from py_ci_shared.no_source_text_tests import assert_no_source_text_tests

    def test_no_sql_text_probes():
        assert_no_source_text_tests(_py_files(REPO / "tests"))
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, ParsedFile, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "assert_no_source_text_tests", "find_source_text_tests"]

RULE = "source-text-test"
SQL_PROBE = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|GROUP BY|ORDER BY|OR TRUE)\b|OR\s+1\s*=\s*1\b")
SQL_SHAPE = re.compile(r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|WITH)\b|\b(?:DELETE\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|SELECT\b.*\bFROM)\b", re.DOTALL)


def _is_sql_text(literal: str) -> bool:
    """A bare probe keyword, or a literal shaped like a statement; ``FROM`` in an English sentence is neither."""
    stripped = literal.strip()
    return SQL_PROBE.fullmatch(stripped) is not None or SQL_SHAPE.search(stripped) is not None


_COLLECTION_DISPLAYS = (ast.Set, ast.List, ast.Tuple, ast.Dict, ast.SetComp, ast.ListComp, ast.DictComp, ast.GeneratorExp)


def _is_collection(operand: ast.expr) -> bool:
    """True for a right operand that is a collection or a header mapping, where ``"DELETE" in x`` is a membership test of an element
    (an HTTP ``Allow`` header, an event list, ``.keys()``) and not a probe of statement text."""
    if isinstance(operand, _COLLECTION_DISPLAYS):
        return True
    if isinstance(operand, ast.Call) and isinstance(operand.func, ast.Attribute):
        return operand.func.attr in ("keys", "values", "items")
    if isinstance(operand, ast.Name):
        return operand.id.endswith("s") and not operand.id.endswith("ss")
    return any(isinstance(n, ast.Attribute) and n.attr.lower().endswith("headers") for n in shared_walk(operand))


def _text_literals(node: ast.Assert) -> Iterator[str]:
    """The string literal on the left of each membership test inside an assert (``"SELECT" in x``), unless the right operand is a collection."""
    for sub in shared_walk(node.test):
        if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Constant) and isinstance(sub.left.value, str):
            for op, right in zip(sub.ops, sub.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and not _is_collection(right):
                    yield sub.left.value
                    break


def _findings_in(parsed: ParsedFile) -> list[Finding]:
    out: list[Finding] = []
    for node in shared_walk(parsed.tree):
        if not isinstance(node, ast.Assert):
            continue
        for literal in _text_literals(node):
            hit = SQL_PROBE.search(literal)
            if hit is not None and _is_sql_text(literal):
                out.append(
                    Finding(
                        parsed.rel,
                        node.lineno,
                        RULE,
                        f"asserts on the text {hit.group(0)!r} of a statement or report: the spelling of a query does not show what it "
                        "returns; assert on the rows it returns, or on the parsed statement",
                    )
                )
    return out


def find_source_text_tests(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every assert in *files* that probes SQL keyword text, sorted by path and line.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for an unreadable file (unless
    *allow_unparsed*).
    """
    scan = scan_python(list(files), min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_no_source_text_tests(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any test that asserts on the text of a SQL statement (see the module docstring)."""
    found = find_source_text_tests(files, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(
            f"{len(found)} source-text-test finding(s): a test that asserts on query text cannot show what the query returns:\n  "
            + "\n  ".join(f.render() for f in found)
        )
