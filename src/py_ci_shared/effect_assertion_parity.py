"""Shared check: a side effect the code performs must be one some test actually inspects.

WHERE THIS CAME FROM
--------------------
A mutation sweep of ``realtime_applications`` measured it. Deleting the statement ``cur.execute(...)``
went unnoticed 28 times across 11 files; deleting ``conn.commit()`` went unnoticed 14 times across 6;
deleting ``conn.rollback()`` 7 times across 5. The suite is 3,600 tests and green, and it stayed green
with the database writes removed.

The cause is not laziness. It is that a mock stands in for the database, the code calls it, and the
test then asserts on the RETURN VALUE -- which a mock supplies whether or not the call was made. The
effect is the whole point of those functions, and it is the one thing nothing looks at.

WHAT THIS CHECKS, AND WHAT IT CANNOT
------------------------------------
For every module that performs an effect (a call to ``commit``, ``rollback``, ``execute`` -- the set is
the caller's), at least one of the TESTS THAT IMPORT that module must inspect that effect on a mock:
``.commit.assert_called_once()``, ``.execute.call_args``, ``.rollback.called`` and so on.

It is a proxy, and a cheap one -- an AST walk over both sides, no execution. Mutation testing is what
actually measures whether a test would notice; this answers the far narrower question "does anything
even look?", in seconds rather than hours, which is what makes it usable in a hook.

Two limits, stated rather than papered over:

* **A pass is not proof.** A test can assert ``execute.called`` and check nothing about the SQL it was
  given. That satisfies this check and would still miss a mutated query.
* **A failure is not always a defect.** A module may be exercised end-to-end against a real database
  in an integration test that never touches a mock. Such modules belong in the baseline with a note
  saying so, which is why the ratchet takes descriptions rather than a bare set of names.

The point is the direction: the list can only shrink, and every entry in it names a real effect that
no test currently inspects.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Optional

from ._core import DEFAULT_EXCLUDE, ImportAliases, SourceError, iter_files, parse_file, relative_posix, resolve_relative

__all__ = [
    "DEFAULT_EFFECTS",
    "assert_effects_are_asserted",
    "build_import_map",
    "find_unasserted_effects",
]

#: Directories whose contents are not this repository's own source.
#:
#: ``.claude`` earns its place the hard way. Claude Code puts agent worktrees under
#: ``.claude/worktrees/``, and a worktree is a FULL SECOND CHECKOUT of the repository. On
#: mlframe, five of them made 56,500 of the tree's 62,765 ``.py`` files copies -- the scan
#: walked the repository six times over, took more than the 900s test timeout, and read as a
#: hang rather than as a directory that should never have been entered. ``.tox`` and
#: ``site-packages`` are the same mistake wearing different names.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".claude",
        ".tox",
        ".eggs",
        "site-packages",
    }
)

#: The effects worth pairing by default: a transaction boundary and a statement execution. Each is a
#: call whose entire purpose is what it does elsewhere, so a return-value assertion cannot see it.
DEFAULT_EFFECTS: tuple[str, ...] = ("commit", "rollback", "execute", "executemany", "execute_values")

#: How a test says it looked at a mock's call. ``called``/``call_count``/``call_args`` are reads;
#: ``assert_*`` are the assertion helpers ``unittest.mock`` provides.
#: await_* are the AsyncMock equivalents, and the list held only await_args -- so a test
#: reading session.execute.await_args_list or .await_count read as inspecting nothing.
#: Found on glossum, whose sessions are all async: eight assertions on the awaited calls, and
#: the module still reported.
_INSPECTIONS = (
    "called",
    "call_count",
    "call_args",
    "call_args_list",
    "mock_calls",
    "await_args",
    "await_args_list",
    "await_count",
)


#: Import roots that are definitely NOT this project, so an attribute call through them is a real
#: driver effect rather than a call into a sibling module. Deliberately a small allow-list of the
#: database libraries this check is about: anything else is treated as possibly-first-party, which
#: errs toward NOT reporting -- the direction that sends nobody to write a wrong assertion.
_THIRD_PARTY_ROOTS = frozenset(
    {
        "sqlalchemy",
        "psycopg2",
        "psycopg",
        "asyncpg",
        "sqlite3",
        "pymysql",
        "MySQLdb",
        "duckdb",
        "databases",
        "aiomysql",
        "aiosqlite",
        "cx_Oracle",
        "pyodbc",
    }
)


class _Parser:
    """Parses through ``_core.parse_file`` (BOM-safe, cached on mtime) and remembers every file it could not parse, so
    the caller reports them instead of treating an unreadable file as one with nothing in it."""

    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self.repo_root = repo_root
        self.unparsed: dict[str, str] = {}

    def __call__(self, path: Path) -> Optional[ast.Module]:
        try:
            return parse_file(path)
        except SourceError as exc:
            rel = relative_posix(path, self.repo_root) if self.repo_root is not None else Path(path).as_posix()
            self.unparsed[rel] = f"{exc.kind} at line {exc.line or 1}: {exc.message}"
            return None


def _tree(path: Path) -> Optional[ast.Module]:
    try:
        return parse_file(path)
    except SourceError:
        return None


def _binds_a_module(repo_root: Path, module_path: Path, node: ast.ImportFrom, name: str) -> bool:
    """Whether ``from <node.module> import <name>`` in *module_path* names a MODULE FILE of this repository."""
    parts = node.module.split(".") if node.module else []
    if node.level:
        base = module_path.parent
        for _ in range(node.level - 1):
            base = base.parent
        roots = [base]
    else:
        roots = [repo_root, repo_root / "src"]
        if parts and parts[0] == repo_root.name:
            roots.append(repo_root.parent)
    for root in roots:
        target = root.joinpath(*parts, name)
        if target.with_suffix(".py").is_file() or (target / "__init__.py").is_file():
            return True
    return False


def _local_module_names(tree: ast.AST, path: Path, repo_root: Optional[Path]) -> set[str]:
    """Names bound to a first-party MODULE: ``from . import graphql``, ``from pkg import graphql``, ``import pkg.graphql
    as graphql``, ``import helpers``. ``from pkg.db import conn`` binds whatever ``conn`` is -- an object, not a module --
    unless ``pkg/db/conn.py`` exists; without *repo_root* every non-driver ``from`` import is assumed to be a module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").split(".")[0] not in _THIRD_PARTY_ROOTS):
            for alias in node.names:
                if alias.name == "*":
                    continue
                if repo_root is None or _binds_a_module(repo_root, path, node, alias.name):
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _THIRD_PARTY_ROOTS:
                    continue
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _performs(path: Path, effects: Sequence[str], *, repo_root: Optional[Path] = None, tree: Optional[ast.Module] = None) -> set[str]:
    """Effects this module performs, as method calls: ``conn.commit()``, ``cur.execute(sql)``.

    ``execute`` is also an ordinary word. A module that DEFINES ``def execute(...)`` -- a GraphQL
    client wrapper, a command runner, a rules engine -- is not touching a database when it calls its
    own function, and neither is a caller reaching it as ``graphql.execute(...)``. Reporting those
    sends the reader to write an assertion about a driver that is not there, which is the same
    "fix that does not apply" failure this module already avoids for ``hash()`` and dict keys.

    So a name the file defines itself is not an effect, and neither is an attribute call whose base
    is a first-party module the file imports (see :func:`_local_module_names`). Everything else is unchanged:
    ``cur.execute(...)`` on a parameter or an attribute is still an effect, and so is a bare ``execute_values(...)``
    imported from psycopg2, and so is ``conn.commit()`` on an object imported from a first-party module.
    """
    if tree is None:
        tree = _tree(path)
        if tree is None:
            return set()

    defined_here = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in effects}
    local_modules = _local_module_names(tree, path, repo_root)

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in effects:
            base = node.func.value
            if isinstance(base, ast.Name) and base.id in local_modules:
                continue
            found.add(node.func.attr)
        # A bare call, `execute_values(cur, sql, rows)`, is an effect too -- unless this module is
        # the one that defines it.
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in effects:
            if node.func.id in defined_here:
                continue
            found.add(node.func.id)
    return found


def _patch_kind(func: ast.AST) -> Optional[str]:
    """``"patch"`` / ``"object"`` for ``patch(...)`` / ``patch.object(...)`` (any receiver: ``mock.patch``), ``"other"``
    for ``patch.dict``/``patch.multiple`` (which inject no positional mock), else None."""
    if isinstance(func, ast.Name) and func.id == "patch":
        return "patch"
    if isinstance(func, ast.Attribute):
        if func.attr == "patch":
            return "patch"
        receiver = func.value
        on_patch = (isinstance(receiver, ast.Name) and receiver.id == "patch") or (isinstance(receiver, ast.Attribute) and receiver.attr == "patch")
        if on_patch:
            return "object" if func.attr == "object" else "other"
    return None


def _patch_target(call: ast.Call, effects: Sequence[str]) -> str | None:
    """The effect a ``patch(...)`` call replaces, if it replaces one.

    ``patch("mod.execute_values")`` names it in the dotted string; ``patch.object(mod, "commit")``
    names it in the second argument. Anything else -- a patch of something that is not an effect, or
    one built from a variable -- answers None.
    """
    kind = _patch_kind(call.func)
    if kind not in ("patch", "object"):
        return None
    index = 1 if kind == "object" else 0
    if len(call.args) <= index:
        return None
    arg = call.args[index]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    name = arg.value.rsplit(".", 1)[-1]
    return name if name in effects else None


def _injects_a_mock(call: ast.Call) -> bool:
    """Whether a ``@patch``/``@patch.object`` decorator passes a mock to the test: not when ``new`` is given."""
    kind = _patch_kind(call.func)
    if kind not in ("patch", "object"):
        return False
    if any(k.arg == "new" for k in call.keywords):
        return False
    return len(call.args) < (3 if kind == "object" else 2)


def _patch_aliases(tree: ast.AST, effects: Sequence[str]) -> dict[str, str]:
    """``{local name: effect}`` for mocks a test binds under a name of its own.

    ``with patch("mod.execute_values") as mock_ev:`` is the ordinary idiom and it defeats a
    name-based match completely: the assertion below it reads ``mock_ev.assert_called_once()``, which
    says nothing about which effect it is. Without this the check reported an effect as uninspected
    while a well-written test three lines away asserted the cursor, the SQL and the rows.

    Decorator form (``@patch("mod.commit")``) binds the mock to a PARAMETER instead. ``unittest.mock`` injects the
    BOTTOM decorator's mock first, into the leading parameters (after ``self``/``cls``), and every injecting patch takes
    one -- including patches of things that are not effects, and not ``new=`` patches, which inject nothing. Mapping
    them onto the trailing parameters credited a fixture such as ``tmp_path`` with the mock.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.withitem):
            if isinstance(node.context_expr, ast.Call) and isinstance(node.optional_vars, ast.Name):
                effect = _patch_target(node.context_expr, effects)
                if effect:
                    aliases[node.optional_vars.id] = effect
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            injecting = [d for d in reversed(node.decorator_list) if isinstance(d, ast.Call) and _injects_a_mock(d)]
            if not injecting:
                continue
            params = [a.arg for a in (*node.args.posonlyargs, *node.args.args) if a.arg not in {"self", "cls"}]
            for param, decorator in zip(params, injecting):
                effect = _patch_target(decorator, effects)
                if effect:
                    aliases.setdefault(param, effect)
    return aliases


#: Drivers whose ``connect`` a test only calls when it means to talk to a REAL database.
_REAL_DB_MODULES = frozenset({"sqlite3", "psycopg2", "duckdb", "pymysql", "MySQLdb"})


def _is_real_connect(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect"):
        return False
    root = node.func.value
    # `sqlite3.connect`, and `psycopg2.extras.connect`-style chains / `from x import y; y.db.connect()`.
    return (isinstance(root, ast.Name) and root.id in _REAL_DB_MODULES) or (isinstance(root, ast.Attribute) and root.attr in _REAL_DB_MODULES)


def _exercises_against_a_real_database(tree: ast.AST) -> bool:
    """True when this test opens a database of its own -- so it is not mocking, it is running.

    A test that calls ``sqlite3.connect(tmp_path / "x.sqlite")``, drives the module, and then queries
    the result back is STRONGER evidence than any mock assertion: it observes the rows, not the call.
    The check could not see that, so it reported such modules as uninspected -- and this module's own
    docstring says those "belong in the baseline with a note", which means hand-maintaining an entry
    per module for the one case that is actually well tested.

    Measured on autopsia: 22 vocabulary installers connect to SQLite directly and are each covered by
    a test that installs into a temp file and SELECTs the rows back. That is 66 of its 93 reported
    effects, every one of them a false report.

    The signal is deliberately narrow -- a `connect` on a real driver, called BY THE TEST. A mocking
    test patches `connect` instead, and `patch("sqlite3.connect")` is a call to `patch`, not to
    `connect`. Same limitation as everything else here: it answers "does anything exercise this",
    not "does the assertion afterwards check the right thing". :func:`_real_database_tests` narrows it to the test
    functions that also call into the module under test.
    """
    return any(_is_real_connect(node) for node in ast.walk(tree))


#: How a fixture says it is building a real database handle, beyond a driver's own ``connect``.
_REAL_DB_FACTORIES = frozenset({"create_engine", "create_async_engine"})


def _is_fixture(node: "ast.FunctionDef | ast.AsyncFunctionDef") -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "fixture":
            return True
    return False


def _fixtures_backed_by_a_real_database_in(tree: ast.AST) -> set[str]:
    direct: set[str] = set()
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_fixture(n)]
    for node in functions:
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _REAL_DB_FACTORIES or (
                name == "connect" and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in _REAL_DB_MODULES
            ):
                direct.add(node.name)
                break

    backed = set(direct)
    for node in functions:
        params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        if params & direct:
            backed.add(node.name)
    return backed


def _fixture_names_backed_by_a_real_database(conftest: Path) -> set[str]:
    """Fixture names in *conftest* that hand out a handle to a REAL database.

    The third shape of the same false report, and the one with the best evidence behind it. A
    project with a `db_session` fixture bound to a live server -- glossum requires `_test` in the
    URL and wraps every test in a SAVEPOINT it rolls back -- exercises its writes against Postgres
    itself. There is no mock anywhere to assert on, and the check reported all 55 of its effects.

    Resolved one level: a fixture that opens the database, and a fixture that merely REQUESTS one
    that does. That covers the ordinary `_db_engine` -> `db_session` split without turning this into
    a dependency solver.
    """
    tree = _tree(conftest)
    return set() if tree is None else _fixtures_backed_by_a_real_database_in(tree)


def _test_functions(tree: ast.AST) -> list["ast.FunctionDef | ast.AsyncFunctionDef"]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]


def _requests_a_real_database_fixture(tree: ast.AST, fixtures: frozenset[str]) -> bool:
    """True when a test function in *tree* asks for one of *fixtures* by parameter name."""
    if not fixtures:
        return False
    for node in _test_functions(tree):
        params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        if params & fixtures:
            return True
    return False


def _calls_into(node: ast.AST, aliases: ImportAliases, module_names: Collection[str]) -> bool:
    """Whether *node* calls something that resolves into one of *module_names* (``store.save(...)``, ``save(...)`` after
    ``from pkg.store import save``)."""
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            target = aliases.qualified_name(call)
            if target and any(target == m or target.startswith(m + ".") for m in module_names):
                return True
    return False


def _real_database_tests(tree: ast.Module, db_fixtures: frozenset[str], module_names: Collection[str]) -> bool:
    """True when ONE test function both runs against a real database (its own driver ``connect``, or a real-database
    fixture from conftest or this file) and calls into the module under test.

    A file-wide answer credited every module the file imports whenever any fixture in it opened SQLite, including a
    module whose test only reads a constant.
    """
    fixtures = frozenset(db_fixtures) | frozenset(_fixtures_backed_by_a_real_database_in(tree))
    aliases = ImportAliases.from_tree(tree)
    for fn in _test_functions(tree):
        params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        runs_real = bool(params & fixtures) or any(_is_real_connect(n) for n in ast.walk(fn))
        if runs_real and _calls_into(fn, aliases, module_names):
            return True
    return False


def _owns_its_connection(path: Path, tree: Optional[ast.Module] = None) -> bool:
    """True when the MODULE opens its own database rather than being handed one.

    This is a structural fact about the code, and it decides what evidence is even possible. A
    module that takes `conn` as a parameter can be handed a mock, and a test that does not assert on
    that mock is the case this whole check exists for. A module that calls
    `sqlite3.connect(db_path or DB_PATH)` itself offers no such seam: a test either patches the
    driver -- and is then mocking, which is visible -- or runs the real thing against a temp file.

    So for a self-connecting module, "no test inspects a mock" does not mean "nothing checks the
    effect". It usually means the tests read the rows back through the module's own reader, which is
    better evidence and is invisible here. Measured on autopsia: `loinc_ru` installs into a temp
    SQLite and asserts through `display_ru()`, the production read path, and was still reported.

    The escape hatch stays honest: a test that PATCHES the driver's connect is mocking after all, and
    `_exercises_against_a_real_database` is not what credits it -- `_patches_a_real_driver` below
    takes that case back out.
    """
    if tree is None:
        tree = _tree(path)
        if tree is None:
            return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect":
            root = node.func.value
            if isinstance(root, ast.Name) and root.id in _REAL_DB_MODULES:
                return True
    return False


def _patches_a_real_driver(tree: ast.AST) -> bool:
    """True when a test replaces a database driver's ``connect`` -- i.e. it IS mocking the database.

    `patch("sqlite3.connect")` is a call to `patch` whose argument names the driver, so it is
    distinguishable from calling `connect` itself. Without this, a self-connecting module would be
    excused by the very tests that mock it away.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                head = arg.value.split(".")[0]
                if head in _REAL_DB_MODULES and arg.value.endswith(".connect"):
                    return True
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "object" and len(node.args) >= 2:
            target, attribute = node.args[0], node.args[1]
            if isinstance(target, ast.Name) and target.id in _REAL_DB_MODULES and isinstance(attribute, ast.Constant) and attribute.value == "connect":
                return True
    return False


def _inspection_helpers(repo_root: Path, effects: Sequence[str], parse: Optional[_Parser] = None) -> dict[str, set[str]]:
    """``{helper name: effects it inspects}`` for helpers defined in non-test modules of the tree.

    A suite that grows past a handful of effect tests extracts its session doubles and its statement
    accessors into a shared module -- `duplicate_function_body` and every reviewer ask for exactly
    that. The extraction moves `session.execute.await_args_list` out of the test file, and matching
    only the test's own attribute chains then reports the module as uninspected: the check would
    punish the refactor it should reward, and the punishment arrives a week later when someone
    "fixes" the report by inlining the helper back.

    So the helpers are resolved. A function whose own body reads one of the inspection attributes off
    an effect name is recorded here, and a test that CALLS it inspects whatever it inspects. Scoped
    to the repository's own files, so an unrelated third-party function of the same name cannot
    silently satisfy the check.
    """
    parse = parse or _Parser(repo_root)
    helpers: dict[str, set[str]] = {}
    for path in _py_files(repo_root):
        if path.name.startswith("test_"):
            continue
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            inspected: set[str] = set()
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Attribute):
                    continue
                if not (inner.attr.startswith("assert_") or inner.attr in _INSPECTIONS):
                    continue
                target = inner.value
                if isinstance(target, ast.Attribute) and target.attr in effects:
                    inspected.add(target.attr)
                elif isinstance(target, ast.Name) and target.id in effects:
                    inspected.add(target.id)
            if inspected:
                helpers.setdefault(node.name, set()).update(inspected)
    return helpers


def _mock_inspections(tree: ast.Module, effects: Sequence[str], helpers: Optional[Mapping[str, Collection[str]]]) -> set[str]:
    found: set[str] = set()
    aliases = _patch_aliases(tree, effects)
    # A call to a helper the suite extracted counts as whatever that helper inspects. Restricted to
    # helpers this file actually IMPORTS: a same-named local function is a different function, and
    # crediting it would let a rename quietly satisfy the check.
    if helpers:
        imported_here = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in imported_here:
                found.update(helpers.get(node.func.id, ()))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not (node.attr.startswith("assert_") or node.attr in _INSPECTIONS):
            continue
        target = node.value
        # `conn.commit.assert_called_once()` -- the mock reached through the object it patches.
        if isinstance(target, ast.Attribute) and target.attr in effects:
            found.add(target.attr)
        # `with patch.object(mod, "execute_values") as execute_values: ... execute_values.assert_called()`
        # binds the mock to a BARE NAME, and that is the commoner idiom for a module-level function.
        # Matching only the attribute chain missed it, and the check then reported an effect as
        # uninspected while a test three lines long was inspecting it.
        elif isinstance(target, ast.Name) and target.id in effects:
            found.add(target.id)
        # `with patch("mod.execute_values") as mock_ev: ... mock_ev.assert_called_once()`.
        elif isinstance(target, ast.Name) and target.id in aliases:
            found.add(aliases[target.id])
    return found


def _inspects(
    path: Path,
    effects: Sequence[str],
    db_fixtures: frozenset[str] = frozenset(),
    helpers: Mapping[str, Collection[str]] | None = None,
    *,
    module_names: Optional[Collection[str]] = None,
) -> set[str]:
    """Effects this test inspects on a mock: ``x.commit.assert_called()``, ``x.execute.call_args``.

    Matched on the ATTRIBUTE CHAIN rather than on text, so ``# commit is asserted below`` in a comment
    and a local variable called ``commit`` are both correctly ignored. With *module_names* (the dotted names of the
    module under test) a real-database run is credited only when one test function both runs against the database and
    calls into that module; without it, any real-database use in the file credits every effect.
    """
    tree = _tree(path)
    if tree is None:
        return set()
    # A test that opens its own database is exercising every effect the module performs, and
    # observing the ROWS rather than the call. Nothing more specific is needed or available.
    if module_names is not None:
        if _real_database_tests(tree, db_fixtures, module_names):
            return set(effects)
    elif _exercises_against_a_real_database(tree) or _requests_a_real_database_fixture(tree, db_fixtures):
        return set(effects)
    return _mock_inspections(tree, effects, helpers)


def _cached_imported_names(path: Path) -> frozenset[str]:
    """``_imported_names`` for *path* (absolute imports only). Kept for callers; parsing is cached by ``_core``."""
    return frozenset(_imported_names(path))


def _py_files(root: Path) -> "list[Path]":
    """Every ``.py`` under *root*, with skipped directories never entered (``_core.iter_files``: git's listing inside a
    work tree, a pruned walk outside one)."""
    return iter_files(root, ("*.py",), exclude=DEFAULT_EXCLUDE | _SKIP_DIRS)


_ImportRecord = tuple[int, Optional[str], tuple[str, ...]]


def _import_records(path: Path) -> list[_ImportRecord]:
    """``(level, module, names)`` per import statement; ``ast.Import`` gives level 0, module None."""
    tree = _tree(path)
    if tree is None:
        return []
    records: list[_ImportRecord] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            records.append((0, None, tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.ImportFrom):
            records.append((node.level, node.module, tuple(alias.name for alias in node.names if alias.name != "*")))
    return records


def _names_from_records(records: Sequence[_ImportRecord], package: Optional[str] = None) -> set[str]:
    names: set[str] = set()
    for level, module, imported in records:
        if module is None and level == 0:
            names.update(imported)
            continue
        base = resolve_relative(module, level, package) if level else module
        if not base:
            continue
        names.add(base)
        names.update(f"{base}.{name}" for name in imported)
    return names


def _imported_names(path: Path, package: Optional[str] = None) -> set[str]:
    """Every dotted name the file imports, in both forms.

    `from pipeline import replay` names a MODULE when the submodule exists, so `pipeline.replay` is
    emitted alongside `pipeline` and the caller resolves both against the real file set. A relative import
    (`from . import writer`) is resolved against *package*, the importing module's package; without it, it is skipped.
    """
    return _names_from_records(_import_records(path), package)


def build_import_map(repo_root: Path, *, package_name: str = "", src_dir: str = "") -> dict[str, list[str]]:
    """``{module path: [test paths importing it]}``, both relative to *repo_root*.

    Import edges rather than measured coverage, and deliberately so. Coverage attributes an executed
    line to the test that was running, but a module's top level runs during COLLECTION, so a test that
    imports a module and reads its constants executes none of its lines and would not appear. For
    "does any test even look at this module", the import edge is the honest relation.

    One level of transitivity through the project's own modules, so a test importing ``pipeline`` is
    credited with ``pipeline/replay.py`` as well; relative imports inside the package count as edges.
    *package_name* covers repositories whose tests import themselves as a package (``from dashboard import data``);
    *src_dir* covers a src layout, where the file at ``src/pkg/x.py`` is imported as ``pkg.x``.

    Both are DETECTED when not given, because forgetting them is silent and total. A src-layout repo
    passed ``build_import_map(root)`` and got an empty map: no module resolved, so no module could be
    reported, so the check passed having examined nothing. That is the failure mode the check itself
    exists to prevent, and it was reported as a clean result on a repository with seven real
    findings. Detection makes the default correct; an explicit argument still wins. A src directory holding several
    packages maps every one of them.
    """
    # A repository that IS a package -- `dashboard/__init__.py` at its root, tests importing
    # `from dashboard import data` -- maps its files as bare `data.py` while every test names
    # `dashboard.data`, so almost nothing matches. Measured on one: 2 modules resolved out of 250
    # files, and the check reported it clean. Same silent-empty-map failure as the src layout below,
    # through a different door.
    if not package_name and (repo_root / "__init__.py").is_file():
        package_name = repo_root.name
    if not src_dir and (repo_root / "src").is_dir():
        packages = [d for d in (repo_root / "src").iterdir() if d.is_dir() and (d / "__init__.py").is_file() and not d.name.startswith((".", "_"))]
        if packages:
            src_dir = "src"
        if len(packages) == 1:
            package_name = package_name or packages[0].name

    def module_name(path: Path) -> str:
        rel = path.relative_to(repo_root).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    files = _py_files(repo_root)
    sources = [p for p in files if "tests" not in p.relative_to(repo_root).parts]
    tests = [p for p in files if p.name.startswith("test_")]

    by_module: dict[str, Path] = {}
    for path in sources:
        flat = module_name(path)
        by_module[flat] = path
        if package_name:
            by_module[f"{package_name}.{flat}" if flat else package_name] = path
        if src_dir and flat.startswith(f"{src_dir}."):
            by_module[flat[len(src_dir) + 1 :]] = path

    records: dict[Path, list[_ImportRecord]] = {}

    def records_of(path: Path) -> list[_ImportRecord]:
        if path not in records:
            records[path] = _import_records(path)
        return records[path]

    def package_of(name: str, path: Path) -> str:
        return name if path.stem == "__init__" else name.rpartition(".")[0]

    own_edges = {name: {i for i in _names_from_records(records_of(path), package_of(name, path)) if i in by_module} for name, path in by_module.items()}

    hits: dict[str, set[str]] = {}
    for test in tests:
        reached = {i for i in _names_from_records(records_of(test)) if i in by_module}
        reached |= {n for m in list(reached) for n in own_edges.get(m, set())}
        for name in reached:
            hits.setdefault(by_module[name].relative_to(repo_root).as_posix(), set()).add(test.relative_to(repo_root).as_posix())
    return {source: sorted(found) for source, found in sorted(hits.items())}


def _patches_a_real_driver_cached(path: Path) -> bool:
    """``_patches_a_real_driver`` for a file (parsing is cached by ``_core`` on the file's mtime and size)."""
    tree = _tree(path)
    return False if tree is None else _patches_a_real_driver(tree)


def _module_dotted_names(module: str, repo_root: Path) -> set[str]:
    """The dotted names a test may import *module* (a repo-relative path) by: flat, src-stripped, package-prefixed."""
    parts = list(Path(module).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    names = {".".join(parts)} if parts else set()
    if len(parts) > 1 and parts[0] == "src":
        names.add(".".join(parts[1:]))
    names.add(".".join([repo_root.name, *parts]))
    return {n for n in names if n}


def find_unasserted_effects(
    repo_root: Path,
    import_map: Mapping[str, Sequence[str]],
    *,
    effects: Sequence[str] = DEFAULT_EFFECTS,
) -> dict[str, str]:
    """``{"<module>::<effect>": description}`` for effects no importing test inspects.

    *import_map* is ``{module path: [test paths that import it]}``, both relative to *repo_root*. The
    importing tests are the right population rather than all tests: a suite-wide "somebody somewhere
    asserts on commit" would be satisfied by one unrelated test and would gate nothing. A module or test that cannot
    be parsed is reported as ``"<path>::<unparsable>"``: nothing about it can be vouched for.
    """
    parse = _Parser(repo_root)
    mock_inspected: dict[str, set[str]] = {}
    helpers = _inspection_helpers(repo_root, effects, parse)
    problems: dict[str, str] = {}
    # Every conftest in the tree, because a `db_session` may be defined in the root one and used
    # three packages down. Collected once: this is an AST parse per conftest, not per test.
    db_fixtures: set[str] = set()
    for conftest in _py_files(repo_root):
        if conftest.name == "conftest.py":
            tree = parse(conftest)
            if tree is not None:
                db_fixtures |= _fixtures_backed_by_a_real_database_in(tree)
    fixtures = frozenset(db_fixtures)
    patches_driver: dict[str, bool] = {}

    for module, tests in sorted(import_map.items()):
        module_path = repo_root / module
        if not module_path.is_file():
            continue
        module_tree = parse(module_path)
        if module_tree is None:
            continue
        performed = _performs(module_path, effects, repo_root=repo_root, tree=module_tree)
        if not performed:
            continue
        module_names = _module_dotted_names(module, repo_root)
        test_trees: dict[str, ast.Module] = {}
        for test in tests:
            test_path = repo_root / test
            if test_path.is_file():
                tree = parse(test_path)
                if tree is not None:
                    test_trees[test] = tree
        # A module that opens its own connection offers no seam to hand a mock through, so a test
        # either patches the driver -- visibly -- or runs the real thing. One importing test that
        # does neither, and actually CALLS into the module, is running it, and the assertion it makes will be
        # about the ROWS, through the module's own reader. See `_owns_its_connection`.
        if _owns_its_connection(module_path, module_tree):
            for test, tree in test_trees.items():
                if test not in patches_driver:
                    patches_driver[test] = _patches_a_real_driver(tree)
            if any(not patches_driver[t] and _calls_into(tree, ImportAliases.from_tree(tree), module_names) for t, tree in test_trees.items()):
                continue
        for effect in sorted(performed):
            checked_by = None
            for test, tree in test_trees.items():
                if test not in mock_inspected:
                    mock_inspected[test] = _mock_inspections(tree, effects, helpers)
                if effect in mock_inspected[test] or _real_database_tests(tree, fixtures, module_names):
                    checked_by = test
                    break
            if checked_by is None:
                problems[f"{module}::{effect}"] = (
                    f"{module} calls `{effect}(...)` and none of its {len(tests)} importing test(s) "
                    f"ever inspects that call, so deleting it would not fail a single one"
                )
    for rel, why in sorted(parse.unparsed.items()):
        problems[f"{rel}::<unparsable>"] = f"{rel} could not be parsed ({why}), so its effects and inspections are unknown"
    return problems


def assert_effects_are_asserted(
    repo_root: Path,
    import_map: Mapping[str, Sequence[str]],
    accepted: Iterable[str] = (),
    *,
    effects: Sequence[str] = DEFAULT_EFFECTS,
    min_modules: int = 1,
) -> None:
    """Fail on an unasserted effect that is not already accepted. Ratchet, not a gate.

    *accepted* is the baseline: what was already true when the check was wired. Existing debt does not
    block a commit, a NEW unasserted effect does, and the set can only shrink: an accepted entry that is no longer
    found fails until it is removed. An *import_map* with fewer than *min_modules* modules fails too -- an empty map
    means nothing was examined, not that everything is inspected.
    """
    import pytest

    if len(import_map) < min_modules:
        pytest.fail(
            f"the import map holds {len(import_map)} module(s), expected at least {min_modules}; check the repo root and layout passed to build_import_map"
        )
    found = find_unasserted_effects(repo_root, import_map, effects=effects)
    accepted = set(accepted)
    new = {key: why for key, why in found.items() if key not in accepted}
    stale = sorted(accepted - set(found))
    messages: list[str] = []
    if new:
        messages.append(
            f"{len(new)} effect(s) performed but inspected by no importing test:\n  " + "\n  ".join(f"{key}: {why}" for key, why in sorted(new.items()))
        )
    if stale:
        messages.append(f"{len(stale)} accepted entr(ies) no longer found -- remove them so the list keeps shrinking:\n  " + "\n  ".join(stale))
    if messages:
        pytest.fail("\n".join(messages))
