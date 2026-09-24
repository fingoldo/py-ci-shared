"""Shared check: every environment variable production code reads via
``os.environ.get(...)``/``os.getenv(...)``/``os.environ[...]`` is documented in the project's
README.

Generalizes a 2026-07-21 audit finding (O-13): an env var this code
actually reads but never documents means an operator can't discover it
exists to set it -- and if the code fails closed when it's unset (an
auth check, a feature gate), the failure is silent. Two entry points:

- ``assert_readme_documents_every_env_var`` -- hard-fail on ANY
  undocumented var. Use once a repo is already at (or near) zero gap.
- ``assert_no_new_undocumented_env_vars`` -- baseline/grandfather style
  (same API shape as ``code_audit_meta``/``loc_budget``), for a repo
  adopting this check with existing undocumented-var debt: only a NEW
  gap (introduced after the baseline was captured) fails, and a
  baselined var that is now documented (or no longer read) fails as
  stale until the baseline is refreshed, so the baseline only shrinks.

Reads are recognised however ``os`` was imported (``import os as _os``,
``from os import environ, getenv``), as a call, a subscript, a
``setdefault`` or an ``in os.environ`` test, with the name positional or
``key=``. A file that cannot be parsed fails the check rather than
silently contributing nothing.

Deliberately dependency-light: ``pytest`` is imported lazily inside the
functions, matching this package's other modules.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import Baseline, ImportAliases, ScanResult, refresh_requested, register_refresh_options, scan_python

DEFAULT_HEADING = "## Environment variables"
REFRESH_FLAG = "--refresh-readme-env-var-baseline"

#: Import-resolved callables whose first (or ``key=``) argument is an env-var name.
_READ_CALLS = frozenset({"os.environ.get", "os.getenv", "os.environ.setdefault", "os.environ.pop", "os.getenvb"})
_ENVIRON = "os.environ"
#: Calls that WRITE the environment and only read it through their return value: ``os.environ.setdefault("OMP_NUM_THREADS",
#: "1")`` as a statement sets a default for a child process, and ``os.environ.pop("X", None)`` as a statement removes one.
_WRITE_WHEN_DISCARDED = frozenset({"os.environ.setdefault", "os.environ.pop"})


def _is_environ_call(node: ast.AST, aliases: Optional[ImportAliases] = None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return (aliases or ImportAliases()).qualified_name(node) in _READ_CALLS


def _is_environ(node: ast.AST, aliases: ImportAliases) -> bool:
    return isinstance(node, (ast.Name, ast.Attribute)) and aliases.qualified_name(node) == _ENVIRON


def _literal_str_elts(node: ast.expr) -> set[str] | None:
    """``{"A", "B"}`` if ``node`` is a Tuple/List/Set of string constants, else None."""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    out: set[str] = set()
    for elt in node.elts:
        if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
            return None
        out.add(elt.value)
    return out


def _loop_target_name(target: ast.expr) -> str | None:
    return target.id if isinstance(target, ast.Name) else None


def _module_level_name_literals(tree: ast.AST) -> dict[str, set[str]]:
    """``{"KEY_NAMES": {"A", "B"}}`` for every ``NAME = (LITERAL, ...)``-shaped
    assignment anywhere in ``tree``."""
    name_literals: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            literals = _literal_str_elts(node.value)
            if literals is not None:
                name_literals[node.targets[0].id] = literals
    return name_literals


def _loop_var_literal_bindings(tree: ast.AST, name_literals: dict[str, set[str]]) -> dict[str, set[str]]:
    """``{"name": {"A", "B"}}`` for every ``for``/comprehension loop whose
    target is a bare name and whose iterable resolves (directly, or via
    ``name_literals``) to a literal string tuple/list/set."""
    loop_var_literals: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            pairs = [(node.target, node.iter)]
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            pairs = [(gen.target, gen.iter) for gen in node.generators]
        else:
            continue
        for target, iter_expr in pairs:
            var_name = _loop_target_name(target)
            if var_name is None:
                continue
            literals = _literal_str_elts(iter_expr)
            if literals is None and isinstance(iter_expr, ast.Name):
                literals = name_literals.get(iter_expr.id)
            if literals:
                loop_var_literals.setdefault(var_name, set()).update(literals)
    return loop_var_literals


def _module_level_str_constants(tree: ast.AST) -> dict[str, str]:
    """``{"_ENV_VAR": "PROJ_SWITCH"}`` for every ``NAME = "LITERAL"`` (or annotated) assignment in ``tree``.

    Lets ``os.environ.get(_ENV_VAR)`` resolve: without it every name bound to a constant, a common way to keep one
    spelling for a read and its error message, was invisible to the scan. A name bound to two different strings is
    dropped rather than guessed.
    """
    consts: dict[str, str] = {}
    ambiguous: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
            if consts.get(target.id, value.value) != value.value:
                ambiguous.add(target.id)
            consts[target.id] = value.value
    for name in ambiguous:
        consts.pop(name, None)
    return consts


def _names_of(arg: Optional[ast.expr], loop_var_literals: dict[str, set[str]], str_consts: Optional[dict[str, str]] = None) -> set[str]:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return {arg.value}
    if isinstance(arg, ast.Name) and arg.id in loop_var_literals:
        return set(loop_var_literals[arg.id])
    if isinstance(arg, ast.Name) and str_consts and arg.id in str_consts:
        return {str_consts[arg.id]}
    return set()


def _is_reader_call(node: ast.Call, reader_funcs: frozenset[str]) -> bool:
    """A call to one of the project's own env readers (``env_flag(NAME)``, ``env_flags.env_int(NAME, 0)``), by name."""
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
    return name is not None and name in reader_funcs


def _key_arg(call: ast.Call) -> Optional[ast.expr]:
    if call.args:
        return call.args[0]
    return next((k.value for k in call.keywords if k.arg == "key"), None)


def _env_var_reads_in_file(
    tree: ast.AST,
    loop_var_literals: dict[str, set[str]],
    aliases: Optional[ImportAliases] = None,
    reader_funcs: frozenset[str] = frozenset(),
) -> list[tuple[str, ast.AST]]:
    """``(name, node)`` for every env-var read in ``tree``, in source order."""
    aliases = aliases if aliases is not None else ImportAliases.from_tree(tree)
    consts = _module_level_str_constants(tree)
    found: list[tuple[str, ast.AST]] = []
    discarded = {id(stmt.value) for stmt in ast.walk(tree) if isinstance(stmt, ast.Expr)}
    for node in ast.walk(tree):
        names: set[str] = set()
        if isinstance(node, ast.Call) and id(node) in discarded and aliases.qualified_name(node) in _WRITE_WHEN_DISCARDED:
            continue
        if isinstance(node, ast.Call) and (_is_environ_call(node, aliases) or _is_reader_call(node, reader_funcs)):
            names = _names_of(_key_arg(node), loop_var_literals, consts)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _is_environ(node.value, aliases):
            names = _names_of(node.slice, loop_var_literals, consts)
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
            if _is_environ(node.comparators[0], aliases):
                names = _names_of(node.left, loop_var_literals, consts)
        found.extend((name, node) for name in sorted(names))
    found.sort(key=lambda item: (getattr(item[1], "lineno", 0), getattr(item[1], "col_offset", 0)))
    return found


def _env_var_names_in_file(
    tree: ast.AST, loop_var_literals: dict[str, set[str]], aliases: Optional[ImportAliases] = None, reader_funcs: frozenset[str] = frozenset()
) -> set[str]:
    return {name for name, _ in _env_var_reads_in_file(tree, loop_var_literals, aliases, reader_funcs)}


def _scan(files: Iterable[Path], min_files: int = 0) -> ScanResult:
    return scan_python([Path(p) for p in files], min_files=min_files)


def _reads_in(scan: ScanResult, reader_funcs: Iterable[str] = ()):
    """``(parsed_file, name, node)`` for every env-var read, file by file in scan order."""
    readers = frozenset(reader_funcs)
    for parsed in scan:
        name_literals = _module_level_name_literals(parsed.tree)
        loop_var_literals = _loop_var_literal_bindings(parsed.tree, name_literals)
        for name, node in _env_var_reads_in_file(parsed.tree, loop_var_literals, ImportAliases.from_tree(parsed.tree), readers):
            yield parsed, name, node


def _vars_in(scan: ScanResult, reader_funcs: Iterable[str] = ()) -> set[str]:
    return {name for _, name, _ in _reads_in(scan, reader_funcs)}


def find_env_var_reads(files: Iterable[Path], *, reader_funcs: Iterable[str] = (), allow_unparsed: bool = False) -> dict[str, tuple[Path, int, Optional[str]]]:
    """``{name: (path, line, default_source)}`` for the first read of every env var in ``files`` (in the given order),
    for generating an inventory. ``default_source`` is the source text of the call's second positional argument (or
    ``default=`` keyword) when the read is a call that passes one, else None."""
    scan = _scan(files)
    if not allow_unparsed:
        scan.check_unparsed()
    out: dict[str, tuple[Path, int, Optional[str]]] = {}
    for parsed, name, node in _reads_in(scan, reader_funcs):
        if name in out:
            continue
        default = None
        if isinstance(node, ast.Call):
            default = node.args[1] if len(node.args) > 1 else next((kw.value for kw in node.keywords if kw.arg == "default"), None)
        out[name] = (Path(parsed.path), node.lineno, ast.unparse(default) if default is not None else None)
    return out


def find_env_vars_read(files: Iterable[Path], *, allow_unparsed: bool = False, reader_funcs: Iterable[str] = ()) -> set[str]:
    """Every env-var name production code reads across ``files``, AST-based. Handles:

    1. A literal string name: ``os.environ.get("NAME")``, ``os.getenv(key="NAME")``,
       ``os.environ["NAME"]``, ``os.environ.setdefault("NAME", ...)``, ``"NAME" in os.environ``,
       with ``os``/``environ``/``getenv`` imported under any alias.
    2. A ``for name in (LITERAL, ...): ... os.environ.get(name)`` /
       ``[... for name in (LITERAL, ...) if os.environ.get(name)]`` shape
       (covers both a ``for`` statement and any comprehension form), where
       the iterable is either a literal tuple/list/set of strings, or a
       bare name previously assigned such a literal at module level (e.g.
       ``KEY_NAMES = ("A", "B"); [n for n in KEY_NAMES if os.environ.get(n)]``).

    3. A name bound to a string constant in the same file: ``_ENV = "NAME"; os.environ.get(_ENV)``.

    ``reader_funcs`` adds the project's own wrappers (``env_flag(NAME)``, ``env_int(NAME, 0)``) to the recognised
    calls, so moving a read behind a helper does not drop it from the inventory.

    Shape 2 is a best-effort heuristic, not full scope analysis: it maps a
    loop-target name to its resolved literal set WITHOUT verifying a given
    ``os.environ.get(name)`` call site sits lexically inside that specific
    loop (a same-named variable reused unrelated elsewhere in the same
    file could over-associate) -- acceptable for a documentation-
    completeness linter where the failure mode is "one extra var to
    document," never a false negative on the shape that matters.

    A file that cannot be read or parsed raises ``UnparsedFilesError`` (an ``AssertionError``) unless
    *allow_unparsed*: its reads are unknown, and an empty contribution would read as "documented".
    """
    scan = _scan(files)
    if not allow_unparsed:
        scan.check_unparsed()
    return _vars_in(scan, reader_funcs)


def find_readme_documented_vars(readme_path: Path, heading: str = DEFAULT_HEADING) -> set[str]:
    """Every ```VAR``` documented in ``readme_path``'s markdown table under
    ``heading`` -- a row's first cell may list more than one name joined by
    e.g. " / " (``\\`GIT_SHA\\` / \\`COMMIT_SHA\\```)."""
    lines = readme_path.read_text(encoding="utf-8-sig").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == heading.strip()), None)
    if start is None:
        raise ValueError(f"{readme_path}'s {heading!r} section/table not found -- renamed, or heading doesn't match?")
    # Scanned line by line rather than matched as one regex: any number of explanatory lines may sit between the
    # heading and the table, and a `.*?` bridge silently matches only when the table starts on the second line,
    # which turns a real check into a no-op for every README that introduces its table with a sentence.
    table: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("|"):
            table.append(line)
        elif table:
            break
    if not table:
        raise ValueError(f"{readme_path}'s {heading!r} section/table not found -- renamed, or heading doesn't match?")
    names: set[str] = set()
    for line in table:
        cells = line.split("|")
        if len(cells) < 2:
            continue
        names.update(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells[1]))
    return names


def _read_vars_or_fail(files: Iterable[Path], min_files: int, reader_funcs: Iterable[str] = ()) -> set[str]:
    import pytest

    scan = _scan(files, min_files)
    problems = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    try:
        scan.check_unparsed()
    except AssertionError as exc:
        problems.append(str(exc))
    if problems:
        pytest.fail("\n".join(problems), pytrace=False)
    return _vars_in(scan, reader_funcs)


def assert_readme_documents_every_env_var(
    files: Iterable[Path],
    readme_path: Path,
    heading: str = DEFAULT_HEADING,
    third_party_vars: frozenset[str] = frozenset(),
    *,
    min_files: int = 1,
    reader_funcs: Iterable[str] = (),
) -> None:
    """Fail if any env var read by production code (``files``) isn't
    documented in ``readme_path``'s table. ``third_party_vars`` excludes
    vars consumed only by a third-party library the project depends on
    (never read by the project's own code, so this AST scan can't find
    them anyway, and they're expected to be documented by hand instead).
    Also fails when a file cannot be parsed or fewer than ``min_files`` parsed. ``reader_funcs`` names the
    project's own env-reading helpers (see ``find_env_vars_read``).
    """
    import pytest

    read_vars = _read_vars_or_fail(files, min_files, reader_funcs) - third_party_vars
    documented = find_readme_documented_vars(readme_path, heading)
    undocumented = sorted(read_vars - documented)
    if undocumented:
        pytest.fail(f"Env var(s) read by production code but missing from {readme_path}'s {heading!r} table:\n  " + "\n  ".join(undocumented))


def assert_no_new_undocumented_env_vars(
    files: Iterable[Path],
    readme_path: Path,
    baseline_path: Path,
    heading: str = DEFAULT_HEADING,
    third_party_vars: frozenset[str] = frozenset(),
    *,
    request: Any = None,
    min_files: int = 1,
    reader_funcs: Iterable[str] = (),
) -> None:
    """Baseline/grandfather variant of ``assert_readme_documents_every_env_var``,
    for a repo adopting this check with pre-existing undocumented-var debt.

    A refresh (``--refresh-readme-env-var-baseline``, ``PY_CI_SHARED_REFRESH=readme-env-var`` or ``all``;
    pass the pytest ``request`` so the option is read under xdist and ``pytest.main``) writes the CURRENT
    undocumented set to ``baseline_path`` and ``pytest.skip()``s that run. Otherwise the run fails on a var
    undocumented now that is not in the baseline, on a baselined var that is now documented or no longer
    read (stale: refresh to shrink the baseline), and on a missing baseline file (nothing would be
    enforced). Call directly as a ``test_*`` body.

    Unlike ``assert_readme_documents_every_env_var``, a missing ``heading``
    section is NOT an error here -- a repo adopting this check may not have
    an env-var table at all yet, in which case every var it reads is
    grandfathered into the baseline by the first refresh (documenting them is then a
    separate, deliberate improvement, not something this check demands
    up front).
    """
    read_vars = _read_vars_or_fail(files, min_files, reader_funcs) - third_party_vars
    try:
        documented = find_readme_documented_vars(readme_path, heading)
    except ValueError:
        documented = set()
    current_undocumented = sorted(read_vars - documented)
    baseline = Baseline(baseline_path, gate="readme-env-var", refresh_command=f"pytest {REFRESH_FLAG} (or PY_CI_SHARED_REFRESH=readme-env-var)")
    outcome = baseline.enforce(
        current_undocumented,
        refresh=refresh_requested(REFRESH_FLAG, request),
        guidance=f"env var(s) read by production code but not documented in {readme_path}'s {heading!r} table",
    )
    outcome.raise_for_pytest()


def register_refresh_option(parser) -> None:
    """Register ``--refresh-readme-env-var-baseline`` (and the generic ``--py-ci-refresh``) as boolean
    flags. Call from a consuming repo's own ``pytest_addoption``."""
    register_refresh_options(parser, [REFRESH_FLAG], help_suffix="README env-var baseline JSON")
