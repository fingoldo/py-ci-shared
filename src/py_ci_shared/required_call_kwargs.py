"""A configured call must pass one of its configured keywords.

``asyncpg.create_pool(dsn, min_size, max_size)`` without ``command_timeout`` lets one hung query hold a pool connection
forever, until the fixed-size pool starves. ``asyncpg.connect(dsn)`` without ``timeout`` waits without bound. Each is a
call that compiles and runs, so no other gate notices the missing keyword.

A rule is ``{"call": "<dotted name>", "any_of": [keywords]}``. The callee is resolved through the module's imports
(``import asyncpg as apg`` and ``from asyncpg import connect as open_conn`` both resolve). A call is accepted when it
passes at least one keyword of *any_of* by name, or unpacks ``**mapping`` (the mapping may carry it, so the gate cannot
call it missing). The default preset :data:`ASYNCPG` holds the two asyncpg rows; a consumer passes its own table
(``rules=[{"call": "stanza.Pipeline", "any_of": ["package"]}]``) and replaces the preset.

Accepted deliberately with ``# call-kwarg-ok: <reason>`` on the call's line or the line above. A marker with no reason is
itself a finding.

Usage::

    from py_ci_shared.required_call_kwargs import ASYNCPG, assert_required_call_kwargs

    def test_every_pool_has_a_command_timeout():
        assert_required_call_kwargs("mypackage", min_files=20)
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases
from ._core import walk as shared_walk
from ._core.gate_contract import check_scan
from ._gate_report import line_has_marker, scan_tree, skip_set

__all__ = ["ASYNCPG", "MARKER", "RULE", "assert_required_call_kwargs", "find_required_call_kwargs"]

RULE = "required-call-kwargs"
MARKER = "call-kwarg-ok"
#: asyncpg's pool and connect functions take their timeouts as keyword-only arguments, so the keyword is the only way to set them.
ASYNCPG: list[dict[str, Any]] = [
    {"call": "asyncpg.create_pool", "any_of": ("command_timeout",)},
    {"call": "asyncpg.connect", "any_of": ("timeout",)},
]


def _table(rules: Sequence[Mapping[str, Any]]) -> dict[str, frozenset[str]]:
    """``{dotted callee: keywords}``; a rule that names no call or no keyword is a usage error, not a pass."""
    table: dict[str, frozenset[str]] = {}
    for rule in rules:
        call = str(rule.get("call", "")).strip()
        names = frozenset(str(k) for k in rule.get("any_of", ()))
        if not call or not names:
            raise ValueError(f"required_call_kwargs: each rule needs a call and at least one keyword, got {dict(rule)!r}")
        table[call] = table.get(call, frozenset()) | names
    return table


def _passes(call: ast.Call, names: frozenset[str]) -> bool:
    """True when one keyword of *names* is passed by name, or a ``**mapping`` is unpacked (``kw.arg is None``)."""
    return any(kw.arg is None or kw.arg in names for kw in call.keywords)


def _marker(source: str, line: int) -> Optional[str]:
    """The reason after ``# call-kwarg-ok:`` on *line* or the one above; "" for a marker with no reason; None without one."""
    lines = source.splitlines()
    for number in (line, line - 1):
        if line_has_marker(lines, number, MARKER):
            comment = lines[number - 1].split("#", 1)[1]
            return comment.split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def find_required_call_kwargs(
    root: Union[str, Path],
    *,
    rules: Optional[Sequence[Mapping[str, Any]]] = None,
    skip_dir_names: Sequence[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every call under *root* to a configured callee that passes none of its keywords, sorted by path and line.

    *rules* defaults to :data:`ASYNCPG`. Raises ``EmptyScanError`` below *min_files* parsed files and
    ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*)."""
    table = _table(ASYNCPG if rules is None else rules)
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        for node in shared_walk(parsed.tree):
            if not isinstance(node, ast.Call):
                continue
            qualified = aliases.qualified_name(node.func)
            names = table.get(qualified or "")
            if names is None or _passes(node, names):
                continue
            reason = _marker(parsed.source, node.lineno)
            if reason:
                continue
            suffix = "; the # call-kwarg-ok marker needs a reason after it" if reason == "" else ""
            out.append(
                Finding(
                    parsed.rel,
                    node.lineno,
                    RULE,
                    f"`{qualified}(...)` passes none of {', '.join(sorted(names))}; pass one of them, or forward **kwargs that carry it{suffix}",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_required_call_kwargs(
    root: Union[str, Path],
    *,
    rules: Optional[Sequence[Mapping[str, Any]]] = None,
    skip_dir_names: Sequence[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any configured call that passes none of its keywords."""
    found = find_required_call_kwargs(
        root,
        rules=rules,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    if found:
        guidance = "a call without its configured keyword runs on the library default, which the rule says is wrong for this call"
        raise AssertionError(f"{len(found)} {RULE} finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
