"""A psycopg2 call whose statement has N placeholders but whose literal parameter tuple or dict has a different count.

WHY. ``cur.execute(SQL, (a, b))`` against a statement with three ``%s`` raises ``IndexError: tuple index out of range`` only when that call runs, and a
statement edited to gain or lose a placeholder leaves every caller one parameter off. A SQL verifier that PREPAREs the statement cannot see this: the
statement is correct, the CALL is wrong. A literal parameter tuple or dict next to a resolvable statement is the case a static check can settle.

WHAT IT CHECKS. For ``<anything>.execute(sql, params)`` (and ``mogrify``; see ``sql_calls``), when ``sql`` resolves to a string -- a literal, a ``+`` chain
of literals, or a module-level constant bound exactly once to such text -- and ``params`` is a literal tuple, list or dict:

* positional ``%s``: the tuple/list length must equal the placeholder count (a ``*splat`` makes the length unknown, so the call is skipped);
* named ``%(name)s``: every name must be a key of the dict literal (an extra key is harmless to psycopg2 and is not reported); a dict with a ``**`` merge or
  a non-constant key is skipped;
* a tuple against a named statement, a dict against a positional one, and both styles in one statement are reported.

A statement with no ``%`` placeholder but a ``?``, ``$1`` or ``:name`` one is another driver's (DuckDB, sqlite3) and is skipped. A doubled ``%%`` is a literal percent sign and is not a placeholder. A statement that is an f-string or ``.format`` template is skipped (its text is not
final), and so is a call with no parameters argument (psycopg2 does not interpolate then). Parameters passed as a name, a call or a comprehension are
skipped: the gate reports only what it can prove.

Usage in a consumer's meta test::

    from py_ci_shared.psycopg2_param_arity import assert_psycopg2_param_arity

    def test_every_literal_parameter_tuple_matches_its_statement():
        assert_psycopg2_param_arity(ROOT, min_files=20)
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python

__all__ = ["DEFAULT_SQL_CALLS", "RULE", "assert_psycopg2_param_arity", "find_psycopg2_param_arity", "placeholders"]

RULE = "psycopg2-param-arity"

#: Method name -> (index of the SQL argument, index of the parameters argument).
DEFAULT_SQL_CALLS: Mapping[str, tuple[int, int]] = {"execute": (0, 1), "mogrify": (0, 1)}

_LITERAL_PERCENT = "@@literal-percent@@"
_NAMED = re.compile(r"%\((\w+)\)s")
_POSITIONAL = re.compile(r"%s")
_OTHER_DRIVER = re.compile(r"\?|\$\d|(?<![:\w]):[A-Za-z_]\w*")


def placeholders(sql: str) -> tuple[list[str], int]:
    """The named placeholders (in order, repeats kept) and the count of bare ``%s`` in *sql*, a doubled ``%%`` set aside first."""
    text = sql.replace("%%", _LITERAL_PERCENT)
    named = _NAMED.findall(text)
    return named, len(_POSITIONAL.findall(_NAMED.sub("", text)))


def _text(node: ast.expr | None, names: Mapping[str, str]) -> str | None:
    """The final text of a string expression, or ``None`` when any part of it is not known statically."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left, names), _text(node.right, names)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Name):
        return names.get(node.id)
    return None


def _module_texts(tree: ast.Module) -> dict[str, str]:
    """Module-level names bound exactly once, to a string expression made of literals and other such names."""
    assigned: dict[str, list[ast.expr | None]] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(stmt.value)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            assigned.setdefault(stmt.target.id, []).append(stmt.value)
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            assigned.setdefault(stmt.target.id, []).append(None)
    names: dict[str, str] = {}
    for _ in range(len(assigned) + 1):  # a constant built from another needs the other first
        grew = False
        for name, values in assigned.items():
            if name in names or len(values) != 1:
                continue
            text = _text(values[0], names)
            if text is not None:
                names[name] = text
                grew = True
        if not grew:
            break
    return names


def _arity_problem(sql: str, params: ast.expr) -> str | None:
    """Why *params* cannot satisfy *sql*, or ``None`` (including when it cannot be told)."""
    named, positional = placeholders(sql)
    if not named and not positional and _OTHER_DRIVER.search(sql):
        return None  # `?` / `$1` / `:name` belong to sqlite3, DuckDB or asyncpg, not psycopg2
    if named and positional:
        return "mixes %s and %(name)s placeholders, which psycopg2 refuses"
    if isinstance(params, (ast.Tuple, ast.List)):
        if any(isinstance(e, ast.Starred) for e in params.elts):
            return None
        if named:
            return f"has {len(set(named))} named placeholder(s) but is given a {type(params).__name__.lower()} of {len(params.elts)}"
        if len(params.elts) != positional:
            return f"has {positional} %s placeholder(s) but is given {len(params.elts)} parameter(s)"
        return None
    if isinstance(params, ast.Dict):
        if any(k is None or not (isinstance(k, ast.Constant) and isinstance(k.value, str)) for k in params.keys):
            return None
        keys = {k.value for k in params.keys if isinstance(k, ast.Constant)}
        if positional:
            return f"has {positional} positional %s placeholder(s) but is given a dict"
        missing = sorted(set(named) - keys)
        if missing:
            return f"names %({', '.join(missing)})s but the dict has no such key"
    return None


def _findings_in(tree: ast.Module, rel: str, sql_calls: Mapping[str, tuple[int, int]]) -> list[Finding]:
    """The findings in one parsed file."""
    names = _module_texts(tree)
    out: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in sql_calls):
            continue
        sql_at, params_at = sql_calls[node.func.attr]
        if len(node.args) <= max(sql_at, params_at) or any(isinstance(a, ast.Starred) for a in node.args):
            continue
        sql = _text(node.args[sql_at], names)
        if sql is None:
            continue
        problem = _arity_problem(sql, node.args[params_at])
        if problem:
            label = node.args[sql_at].id if isinstance(node.args[sql_at], ast.Name) else "the statement"
            out.append(Finding(rel, node.lineno, RULE, f"`{node.func.attr}({label}, ...)`: the statement {problem}"))
    return out


def find_psycopg2_param_arity(
    root: Union[str, Path],
    *,
    sql_calls: Optional[Mapping[str, tuple[int, int]]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every call whose literal parameters cannot satisfy its statement, sorted by path and line.

    *sql_calls* maps a method name to ``(sql_index, params_index)``; the default covers ``execute`` and ``mogrify``. Raises ``EmptyScanError`` when fewer than
    *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*).
    """
    calls = DEFAULT_SQL_CALLS if sql_calls is None else sql_calls
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, calls))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_psycopg2_param_arity(
    root: Union[str, Path],
    *,
    sql_calls: Optional[Mapping[str, tuple[int, int]]] = None,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_psycopg2_param_arity(root, sql_calls=sql_calls, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "make the parameters match the statement's placeholders (one value per %s, one key per %(name)s), or fix the statement"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="psycopg2_param_arity", refresh_command="PY_CI_SHARED_REFRESH=psycopg2_param_arity")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} psycopg2-param-arity finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
