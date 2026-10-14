"""A test that asserts on the TEXT of a SQL statement, a query string or a rendered report instead of running it.

``assert "WHERE" in sql`` passes for a query that filters nothing useful, for one that mentions WHERE in a comment, and for a
query that returns every row. ``assert "OR TRUE" not in sql`` passes for a predicate that is always true under another
spelling. The check reads the SPELLING of the statement, so it cannot tell whether the statement does what it should. The
defect this rule exists for: a tenant filter was asserted with ``"WHERE" in`` and the query that shipped had no tenant
predicate at all.

Flagged: an ``assert`` in a test file whose test is a membership test with a string literal on the left and a value on the
right (``"SELECT" in x``, ``"OR 1=1" not in x``), when the literal contains an upper-case SQL keyword or probe: SELECT, INSERT,
UPDATE, DELETE, FROM, WHERE, JOIN, GROUP BY, ORDER BY, OR TRUE, OR 1=1. Keywords are matched whole and in upper case, so an
English word in a message is not a finding.

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

__all__ = ["RULE", "assert_no_source_text_tests", "find_source_text_tests"]

RULE = "source-text-test"
SQL_PROBE = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|GROUP BY|ORDER BY|OR TRUE)\b|OR\s+1\s*=\s*1\b")


def _text_literals(node: ast.Assert) -> Iterator[str]:
    """The string literal on the left of each membership test inside an assert (``"SELECT" in x``)."""
    for sub in ast.walk(node.test):
        if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Constant) and isinstance(sub.left.value, str):
            if any(isinstance(op, (ast.In, ast.NotIn)) for op in sub.ops):
                yield sub.left.value


def _findings_in(parsed: ParsedFile) -> list[Finding]:
    out: list[Finding] = []
    for node in ast.walk(parsed.tree):
        if not isinstance(node, ast.Assert):
            continue
        for literal in _text_literals(node):
            hit = SQL_PROBE.search(literal)
            if hit is not None:
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
