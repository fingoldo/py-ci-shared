"""Small helpers that several scanners each copied with an identical body (52-T4). A scanner imports them from here under its
old private name, so its call sites do not change.

Each function is the body the copies shared: the AST helpers take a node or a body, the text helpers take the text and a
position. Nothing here depends on a scanner's own constants (a suppression marker, say); those stay in the scanner.
"""

from __future__ import annotations

import ast
import logging
import re
import shlex
from collections.abc import Iterable, Iterator
from typing import Optional

logger = logging.getLogger(__name__)


def final_name(func: ast.AST) -> Optional[str]:
    """The last name of a call target: ``a.b.c`` gives ``c``, ``f`` gives ``f``, anything else ``None``."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def head_name(node: ast.AST) -> Optional[str]:
    """The root name of an attribute chain: ``a.b.c`` gives ``a``; a chain not rooted at a name gives ``None``."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def is_test_path(rel: str) -> bool:
    """Whether a repo-relative path (``/`` separated) is a test file: under ``tests``, named ``test_*``, or a ``conftest.py``."""
    parts = rel.split("/")
    return "tests" in parts[:-1] or parts[-1].startswith("test_") or parts[-1] == "conftest.py"


def is_type_checking_test(test: ast.expr) -> bool:
    """Whether an ``if`` test is ``TYPE_CHECKING`` (bare or as ``typing.TYPE_CHECKING``)."""
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def line_of_index(source: str, index: int) -> int:
    """The 1-based line that holds character ``index`` of ``source``."""
    return source.count("\n", 0, index) + 1


def module_statements(body: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
    """Module-scope statements, descending into ``if``/``try``/``with`` blocks but never into a def or class."""
    for node in body:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for name in ("body", "orelse", "finalbody"):
            yield from module_statements(getattr(node, name, None) or [])
        for handler in getattr(node, "handlers", None) or []:
            yield from module_statements(handler.body)


def normalize_distribution_name(name: str) -> str:
    """A distribution name as PEP 503 writes it: runs of ``-``, ``_`` and ``.`` become one ``-``, lower case."""
    return re.sub(r"[-_.]+", "-", name).lower()


def shell_tokens(command: str) -> list[str]:
    """``command`` split the way a POSIX shell would; a line that does not shell-split falls back to a whitespace split."""
    try:
        return shlex.split(command, posix=True)
    except ValueError as exc:
        logger.debug("could not shell-split command, falling back to whitespace split: %s", type(exc).__name__)
        return command.split()


def toml_quoted(text: str) -> str:
    """``text`` as a TOML basic string, with backslashes and double quotes escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
