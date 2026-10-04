"""Every call that opens a network database connection sets TCP keepalives.

A tunnel or NAT that silently drops an idle TCP connection leaves the client waiting for the OS keepalive timer, two
hours on Windows. A live integration test hung 7,210 s although its ``statement_timeout`` was 150 s: the server
could not answer on a connection that no longer existed, and libpq never noticed. Passing ``keepalives=1``,
``keepalives_idle``, ``keepalives_interval`` and ``keepalives_count`` makes a dead link fail in about 90 s.

Reported, for each call in *openers* (``psycopg2.connect``, ``psycopg2.pool.{Threaded,Simple,Persistent}ConnectionPool``,
``psycopg.connect``, ``psycopg.AsyncConnection.connect``, ``psycopg_pool.*ConnectionPool``), resolved through ``import ... as``,
``from psycopg2 import connect, pool`` and a simple ``connect = psycopg2.connect`` re-binding: a call that does not supply
every name in *required*. A call is accepted when it

* passes each required name as a literal keyword;
* unpacks ``**NAME`` where NAME is in *shared_dict_names* and its module-level literal definition (found in the scan, or
  :data:`KEEPALIVES` of this module imported from here, or an entry of *trusted_mappings*) holds every required key
  with a non-zero ``keepalives``;
* passes a DSN string literal (``"host=h keepalives=1 ..."``, a URL query, a module-level string constant, an f-string
  or concatenation whose literal parts hold them) that names every required key.

``psycopg_pool`` classes take the connection keywords in ``kwargs=``, so for them the mapping is read from there
(*kwargs_carriers*). A ``**mapping`` of unknown origin, a shared name with no definition in the scan, or a definition
that lacks a key is a finding: the gate cannot see that the keepalives are present. asyncpg has no keepalive keywords
and is not an opener by default.

Accepted deliberately with *exempt* (``{"path.py": reason}`` or ``{"path.py::function": reason}``; an unmatched entry
or an empty reason is itself reported) or ``# liveness-ok: <reason>`` on the call's line or the line above.

Usage::

    from py_ci_shared.connection_liveness_kwargs import KEEPALIVES, assert_every_connection_has_liveness_kwargs

    def test_every_connection_has_keepalives():
        assert_every_connection_has_liveness_kwargs(REPO / "mypackage", min_files=50)

A package defines its own ``KEEPALIVES = {...}`` literal (or imports the one here) and spreads it:
``psycopg2.connect(dsn, **KEEPALIVES)``. Scan a root that contains the module defining the mapping, or list it in
*trusted_mappings*.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Baseline, Finding, ImportAliases, ParsedFile, ScanResult
from ._core.gate_contract import check_scan
from ._gate_report import enclosing_functions, line_has_marker, scan_tree, skip_set

__all__ = [
    "DEFAULT_KWARGS_CARRIERS",
    "DEFAULT_OPENERS",
    "DEFAULT_REQUIRED",
    "DEFAULT_SHARED_DICT_NAMES",
    "KEEPALIVES",
    "MARKER",
    "RULE",
    "assert_every_connection_has_liveness_kwargs",
    "find_connections_without_liveness_kwargs",
]

RULE = "connection-liveness-kwargs"
MARKER = "liveness-ok"
#: Probe after 60 s idle, then every 10 s, give up after 3 misses: a dead link fails in about 90 s, a live one is untouched.
KEEPALIVES: dict[str, int] = {"keepalives": 1, "keepalives_idle": 60, "keepalives_interval": 10, "keepalives_count": 3}
DEFAULT_REQUIRED: tuple[str, ...] = tuple(KEEPALIVES)
DEFAULT_SHARED_DICT_NAMES: frozenset[str] = frozenset({"KEEPALIVES"})
DEFAULT_OPENERS: frozenset[str] = frozenset(
    {
        "psycopg2.connect",
        "psycopg2.pool.ThreadedConnectionPool",
        "psycopg2.pool.SimpleConnectionPool",
        "psycopg2.pool.PersistentConnectionPool",
        "psycopg.connect",
        "psycopg.Connection.connect",
        "psycopg.AsyncConnection.connect",
        "psycopg_pool.ConnectionPool",
        "psycopg_pool.AsyncConnectionPool",
        "psycopg_pool.NullConnectionPool",
        "psycopg_pool.NullAsyncConnectionPool",
    }
)
#: Openers that take the connection keywords as one ``kwargs=`` mapping rather than directly.
DEFAULT_KWARGS_CARRIERS: frozenset[str] = frozenset(
    {"psycopg_pool.ConnectionPool", "psycopg_pool.AsyncConnectionPool", "psycopg_pool.NullConnectionPool", "psycopg_pool.NullAsyncConnectionPool"}
)
_LIBRARY_MAPPING = "py_ci_shared.connection_liveness_kwargs.KEEPALIVES"
_DSN_KEYWORDS = frozenset({"dsn", "conninfo"})
_Definitions = dict[str, list[tuple[str, set[str], bool]]]


class _Keys:
    """What one call supplies: the keys it provably holds, and why part of it could not be read."""

    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.problems: list[str] = []


def _text_parts(node: ast.AST) -> Iterable[str]:
    """Every string literal reachable through ``+``, ``%``, f-strings and a ``.format`` receiver."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        for value in node.values:
            yield from _text_parts(value)
    elif isinstance(node, ast.BinOp):
        yield from _text_parts(node.left)
        yield from _text_parts(node.right)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        yield from _text_parts(node.func.value)


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """``NAME = "literal text"`` at module level, so ``DSN = "host=h keepalives=1 ..."; connect(DSN)`` is read."""
    out: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            text = "".join(_text_parts(stmt.value))
            if text:
                out[stmt.targets[0].id] = text
    return out


def _off(value: Optional[ast.AST]) -> bool:
    """``keepalives=0`` or ``False`` switches the probes off: present, but not a fix."""
    return isinstance(value, ast.Constant) and value.value is not True and value.value in (0, False, None)


def _literal_mapping(node: ast.AST) -> Optional[tuple[set[str], bool]]:
    """``(keys, keepalives_disabled)`` of a dict literal or ``dict(k=v)`` call, ignoring splats; None for anything else."""
    values: dict[str, ast.AST] = {}
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                values[key.value] = value
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        values = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
    else:
        return None
    return set(values), _off(values.get("keepalives"))


def _definitions(scan: ScanResult, names: frozenset[str]) -> _Definitions:
    """``{name: [(rel, keys, disabled), ...]}`` for every module-level definition of a shared mapping name."""
    out: _Definitions = {}
    for parsed in scan:
        stack: list[ast.stmt] = list(parsed.tree.body)
        while stack:
            stmt = stack.pop()
            if isinstance(stmt, (ast.If, ast.Try)):
                stack.extend(stmt.body + stmt.orelse + (stmt.finalbody if isinstance(stmt, ast.Try) else []))
                continue
            target: Optional[ast.AST] = None
            value: Optional[ast.AST] = None
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target, value = stmt.targets[0], stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                target, value = stmt.target, stmt.value
            if isinstance(target, ast.Name) and target.id in names and value is not None:
                literal = _literal_mapping(value)
                keys, off = literal if literal is not None else (set(), False)
                out.setdefault(target.id, []).append((parsed.rel, keys, off))
    return out


class _Gate:
    def __init__(
        self,
        *,
        openers: frozenset[str],
        carriers: frozenset[str],
        required: tuple[str, ...],
        shared: frozenset[str],
        trusted: Mapping[str, Iterable[str]],
        definitions: _Definitions,
    ) -> None:
        self.openers, self.carriers, self.required, self.shared = openers, carriers, required, shared
        self.trusted = {k: frozenset(v) for k, v in trusted.items()}
        self.definitions = definitions

    def _shared_keys(self, node: ast.AST, aliases: ImportAliases, out: _Keys, rel: str) -> None:
        qualified = aliases.qualified_name(node) or ""
        name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
        if qualified == _LIBRARY_MAPPING:
            out.keys.update(KEEPALIVES)
        elif qualified in self.trusted:
            out.keys.update(self.trusted[qualified])
        elif name in self.shared:
            defs = self._candidates(name, qualified, rel, out)
            if not defs:
                return
            for rel, keys, off in defs:
                missing = [k for k in self.required if k not in keys]
                if missing:
                    out.problems.append(f"**{name} as defined in {rel} lacks {', '.join(missing)}")
                elif off:
                    out.problems.append(f"**{name} as defined in {rel} sets keepalives to a falsy value")
                else:
                    out.keys.update(keys)
        else:
            out.problems.append(f"**{ast.unparse(node)} is a mapping of unknown origin; spread a shared mapping ({', '.join(sorted(self.shared))})")

    def _candidates(self, name: str, qualified: str, rel: str, out: _Keys) -> list[tuple[str, set[str], bool]]:
        """The definitions a use of *name* in file *rel* can mean: this file's own, else those of the module it is imported
        from, else (a star import, an attribute chain) every one of that name. Empty, with a problem recorded, when none."""
        every = self.definitions.get(name, [])
        local = [d for d in every if d[0] == rel]
        if local:
            return local
        module = qualified.rsplit(".", 1)[0].lstrip(".") if "." in qualified else ""
        if module:
            suffix = module.replace(".", "/")
            found = [d for d in every if d[0].endswith((f"{suffix}.py", f"{suffix}/__init__.py"))]
            if found:
                return found
            out.problems.append(
                f"**{name} is imported from {module}, which defines no module-level literal {name} in the scanned code; scan a root that"
                " contains it or list it in trusted_mappings"
            )
            return []
        if every:
            return every
        out.problems.append(f"**{name} has no module-level literal definition in the scanned code, so its keys cannot be checked")
        return []

    def _mapping(self, node: ast.AST, aliases: ImportAliases, out: _Keys, rel: str) -> None:
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    self._shared_keys(value, aliases, out, rel)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self._literal_key(key.value, value, out)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
            for kw in node.keywords:
                if kw.arg is None:
                    self._shared_keys(kw.value, aliases, out, rel)
                else:
                    self._literal_key(kw.arg, kw.value, out)
        else:
            self._shared_keys(node, aliases, out, rel)

    def _literal_key(self, key: str, value: ast.AST, out: _Keys) -> None:
        if key == "keepalives" and _off(value):
            out.problems.append("keepalives is passed as a falsy value, which switches the probes off")
        else:
            out.keys.add(key)

    def supplied(self, call: ast.Call, qualified: str, aliases: ImportAliases, strings: dict[str, str], rel: str) -> _Keys:
        out = _Keys()
        carried = qualified in self.carriers
        for kw in call.keywords:
            if kw.arg == "kwargs" and carried:
                self._mapping(kw.value, aliases, out, rel)
            elif kw.arg is None:
                if not carried:
                    self._shared_keys(kw.value, aliases, out, rel)
            elif kw.arg in _DSN_KEYWORDS:
                self._dsn_keys(kw.value, strings, out)
            elif not carried:
                self._literal_key(kw.arg, kw.value, out)
        for arg in call.args:
            self._dsn_keys(arg, strings, out)
        return out

    def _dsn_keys(self, node: ast.AST, strings: dict[str, str], out: _Keys) -> None:
        text = "".join(_text_parts(node)) or (strings.get(node.id, "") if isinstance(node, ast.Name) else "")
        for key in self.required:
            match = re.search(rf"(?<![\w.]){re.escape(key)}\s*=\s*(\w*)", text)
            if match is None:
                continue
            if key == "keepalives" and match.group(1).lower() in ("0", "false", "off", "no"):
                out.problems.append("the DSN sets keepalives=0, which switches the probes off")
            else:
                out.keys.add(key)


def _rebound(tree: ast.Module, aliases: ImportAliases, openers: frozenset[str]) -> dict[str, str]:
    """``connect = psycopg2.connect`` (any scope): the bare name now opens a connection. Two passes reach a chain."""
    out: dict[str, str] = {}
    pairs = [(n.targets[0].id, n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)]
    for _ in range(2):
        for name, value in pairs:
            if not isinstance(value, (ast.Name, ast.Attribute)):
                continue
            qualified = out.get(value.id) if isinstance(value, ast.Name) and value.id in out else aliases.qualified_name(value)
            if qualified in openers:
                out[name] = qualified
    return out


def _marker(parsed: ParsedFile, line: int) -> Optional[str]:
    """The reason after ``# liveness-ok:`` on *line* or the one above; "" for a marker with no reason; None without one."""
    lines = parsed.source.splitlines()
    for number in (line, line - 1):
        if line_has_marker(lines, number, MARKER):
            comment = lines[number - 1].split("#", 1)[1]
            return comment.split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def _file_findings(parsed: ParsedFile, gate: _Gate, exempt: Mapping[str, str], used: set[str]) -> list[Finding]:
    aliases = ImportAliases.from_tree(parsed.tree)
    rebound = _rebound(parsed.tree, aliases, gate.openers)
    strings = _module_strings(parsed.tree)
    functions = enclosing_functions(parsed.tree)
    spread = sorted(gate.shared)[0] if gate.shared else "KEEPALIVES"
    out: list[Finding] = []
    for node in ast.walk(parsed.tree):
        if not isinstance(node, ast.Call):
            continue
        qualified = rebound[node.func.id] if isinstance(node.func, ast.Name) and node.func.id in rebound else aliases.qualified_name(node.func)
        if qualified not in gate.openers:
            continue
        supplied = gate.supplied(node, qualified, aliases, strings, parsed.rel)
        missing = [k for k in gate.required if k not in supplied.keys]
        if not missing and not supplied.problems:
            continue
        where = functions.get(id(node), "<module>")
        entry = next((k for k in (f"{parsed.rel}::{where}", parsed.rel) if k in exempt), None)
        if entry is not None:
            used.add(entry)
            continue
        reason = _marker(parsed, node.lineno)
        if reason:
            continue
        what = f"{qualified}(...) in {where} " + (
            "cannot be shown to set keepalives: " + "; ".join(supplied.problems) if supplied.problems else f"is missing {', '.join(missing)}"
        )
        if reason == "":
            what += f"; the # {MARKER} marker needs a reason after it"
        out.append(Finding(parsed.rel, node.lineno, RULE, f"{what}. Pass **{spread} or the keywords literally"))
    return out


def _collect(
    root: Union[str, Path],
    *,
    openers: Iterable[str],
    kwargs_carriers: Iterable[str],
    required: Iterable[str],
    shared_dict_names: Iterable[str],
    trusted_mappings: Mapping[str, Iterable[str]],
    exempt: Mapping[str, str],
    skip_dir_names: Iterable[str],
    include_tests: bool,
    min_files: int,
    allow_unparsed: bool,
    use_git: Optional[bool],
) -> list[Finding]:
    empty = [key for key, reason in exempt.items() if not str(reason).strip()]
    if empty:
        raise ValueError(f"connection_liveness_kwargs: exempt entries need a reason: {', '.join(sorted(empty))}")
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    names = frozenset(shared_dict_names)
    gate = _Gate(
        openers=frozenset(openers),
        carriers=frozenset(kwargs_carriers),
        required=tuple(required),
        shared=names,
        trusted=trusted_mappings,
        definitions=_definitions(scan, names),
    )
    used: set[str] = set()
    findings = [f for parsed in scan for f in _file_findings(parsed, gate, exempt, used)]
    stale = sorted(set(exempt) - used)
    findings.extend(
        Finding(key.split("::", 1)[0], 0, RULE, f"exempt entry {key!r} matches no connection opener that would be reported; remove it") for key in stale
    )
    return sorted(findings, key=lambda f: (f.path, f.line))


def find_connections_without_liveness_kwargs(
    root: Union[str, Path],
    *,
    openers: Iterable[str] = DEFAULT_OPENERS,
    kwargs_carriers: Iterable[str] = DEFAULT_KWARGS_CARRIERS,
    required: Iterable[str] = DEFAULT_REQUIRED,
    shared_dict_names: Iterable[str] = DEFAULT_SHARED_DICT_NAMES,
    trusted_mappings: Optional[Mapping[str, Iterable[str]]] = None,
    exempt: Optional[Mapping[str, str]] = None,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every connection opener under *root* that does not supply the liveness keywords, sorted by path and line.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for a file that cannot be read or
    parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it."""
    return _collect(
        root,
        openers=openers,
        kwargs_carriers=kwargs_carriers,
        required=required,
        shared_dict_names=shared_dict_names,
        trusted_mappings=trusted_mappings or {},
        exempt=exempt or {},
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )


def assert_every_connection_has_liveness_kwargs(
    root: Union[str, Path],
    *,
    openers: Iterable[str] = DEFAULT_OPENERS,
    kwargs_carriers: Iterable[str] = DEFAULT_KWARGS_CARRIERS,
    required: Iterable[str] = DEFAULT_REQUIRED,
    shared_dict_names: Iterable[str] = DEFAULT_SHARED_DICT_NAMES,
    trusted_mappings: Optional[Mapping[str, Iterable[str]]] = None,
    exempt: Optional[Mapping[str, str]] = None,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    grow: Optional[bool] = None,
    use_git: Optional[bool] = None,
    request: Optional[Any] = None,
) -> None:
    """Fail on any opener without the liveness keywords, or with *baseline_path* on any the baseline does not accept."""
    found = find_connections_without_liveness_kwargs(
        root,
        openers=openers,
        kwargs_carriers=kwargs_carriers,
        required=required,
        shared_dict_names=shared_dict_names,
        trusted_mappings=trusted_mappings,
        exempt=exempt,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "a connection through a silent-drop tunnel hangs for the OS keepalive timer (two hours on Windows); spread KEEPALIVES into the call"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="connection_liveness_kwargs", refresh_command="PY_CI_SHARED_REFRESH=connection_liveness_kwargs")
        baseline.enforce(found, refresh=refresh, guidance=guidance, grow=grow, request=request).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} connection-liveness-kwargs finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
