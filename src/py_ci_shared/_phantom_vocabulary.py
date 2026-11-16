"""Declared vocabulary for ``phantom_code_references``: names a comment may cite that the repo does not declare, and that are
not phantoms either. Private to that gate (registered in ``registry.INTERNAL_MODULES``).

Four sources, each with its own red test in ``tests/test_phantom_code_references.py``:

1. the standard library (``sys.stdlib_module_names``) and the builtins (:func:`stdlib_names`);
2. names a file binds with ``import`` (handled by the gate itself, which asks :func:`is_sql_token` and :func:`local_package_prefixes`
   only for the other sources);
3. SQL keywords and catalogue names in backticked SQL (:func:`sql_keywords`, :func:`is_sql_token`);
4. the repo-local prefix rule: the top-level packages of the repo (:func:`local_package_prefixes`).
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

_SQL_KEYWORDS = frozenset(
    {
        "SELECT", "FROM", "WHERE", "LIKE", "ILIKE", "AND", "OR", "NOT", "NULL", "IS", "IN", "BETWEEN", "EXISTS",
        "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "ON", "USING", "AS", "DISTINCT", "UNION", "ALL",
        "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET", "ASC", "DESC", "WITH", "RETURNING", "CASE", "WHEN",
        "THEN", "ELSE", "END", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "INDEX",
        "VIEW", "ALTER", "DROP", "TRUNCATE", "PRIMARY", "KEY", "FOREIGN", "REFERENCES", "UNIQUE", "DEFAULT",
        "CHECK", "CONSTRAINT", "CONFLICT", "DO", "NOTHING", "EXCLUDED", "UPSERT", "COALESCE", "CAST", "COUNT",
        "SUM", "AVG", "MIN", "MAX", "BEGIN", "COMMIT", "ROLLBACK", "TRANSACTION", "EXPLAIN", "ANALYZE", "PRAGMA",
        "IF", "TRUE", "FALSE", "ESCAPE", "COLLATE", "CONCURRENTLY", "ANY", "SOME", "ARRAY", "ROW", "OVER",
        "PARTITION", "FILTER", "WINDOW", "RECURSIVE", "NULLS", "FIRST", "LAST", "FETCH", "ROWS", "ONLY",
    }
)  # fmt: skip

#: Schema catalogues a backticked SQL reference starts with (``information_schema.columns.data_type``).
_SQL_CATALOGUES = frozenset({"information_schema", "pg_catalog"})


def stdlib_names() -> frozenset[str]:
    """The standard library's top-level module names (``sys.stdlib_module_names``) and every builtin."""
    return frozenset(sys.stdlib_module_names) | frozenset(dir(builtins))


def sql_keywords() -> frozenset[str]:
    """SQL keywords as they are written in SQL (upper case). Matched exactly: ``set``/``count`` in Python code are not keywords."""
    return _SQL_KEYWORDS


def is_sql_token(token: str) -> bool:
    """Whether a backticked *token* is SQL: its head is an upper-case keyword (``LIKE``, ``EXCLUDED.col``) or a catalogue."""
    head = token.split("(", 1)[0].split(".", 1)[0]
    return head in _SQL_KEYWORDS or head in _SQL_CATALOGUES


def local_package_prefixes(repo_root: Path) -> tuple[str, ...]:
    """The repo's own package names: each ``src/<pkg>`` directory and each top-level directory holding an ``__init__.py``,
    sorted. A reference whose head starts with one of these is a claim about this repo."""
    found: set[str] = set()
    for base in (repo_root / "src", repo_root):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and child.name not in ("tests", "test") and (child / "__init__.py").is_file():
                found.add(child.name)
    return tuple(sorted(found))
