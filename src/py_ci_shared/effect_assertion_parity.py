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
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

__all__ = [
    "DEFAULT_EFFECTS",
    "assert_effects_are_asserted",
    "build_import_map",
    "find_unasserted_effects",
]

_SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"})

#: The effects worth pairing by default: a transaction boundary and a statement execution. Each is a
#: call whose entire purpose is what it does elsewhere, so a return-value assertion cannot see it.
DEFAULT_EFFECTS: tuple[str, ...] = ("commit", "rollback", "execute", "executemany", "execute_values")

#: How a test says it looked at a mock's call. ``called``/``call_count``/``call_args`` are reads;
#: ``assert_*`` are the assertion helpers ``unittest.mock`` provides.
_INSPECTIONS = ("called", "call_count", "call_args", "call_args_list", "mock_calls", "await_args")


def _performs(path: Path, effects: Sequence[str]) -> set[str]:
    """Effects this module performs, as method calls: ``conn.commit()``, ``cur.execute(sql)``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in effects:
            found.add(node.func.attr)
        # A bare call, `execute_values(cur, sql, rows)`, is an effect too.
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in effects:
            found.add(node.func.id)
    return found


def _patch_target(call: ast.Call, effects: Sequence[str]) -> str | None:
    """The effect a ``patch(...)`` call replaces, if it replaces one.

    ``patch("mod.execute_values")`` names it in the dotted string; ``patch.object(mod, "commit")``
    names it in the second argument. Anything else -- a patch of something that is not an effect, or
    one built from a variable -- answers None.
    """
    func = call.func
    is_patch = (isinstance(func, ast.Name) and func.id == "patch") or (
        isinstance(func, ast.Attribute) and (func.attr == "patch" or (func.attr == "object" and isinstance(func.value, ast.Name) and func.value.id == "patch"))
    )
    if not is_patch:
        return None
    is_object = isinstance(func, ast.Attribute) and func.attr == "object"
    index = 1 if is_object else 0
    if len(call.args) <= index:
        return None
    arg = call.args[index]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    name = arg.value.rsplit(".", 1)[-1]
    return name if name in effects else None


def _patch_aliases(tree: ast.AST, effects: Sequence[str]) -> dict[str, str]:
    """``{local name: effect}`` for mocks a test binds under a name of its own.

    ``with patch("mod.execute_values") as mock_ev:`` is the ordinary idiom and it defeats a
    name-based match completely: the assertion below it reads ``mock_ev.assert_called_once()``, which
    says nothing about which effect it is. Without this the check reported an effect as uninspected
    while a well-written test three lines away asserted the cursor, the SQL and the rows.

    Decorator form (``@patch("mod.commit")``) binds the mock to a PARAMETER instead, injected
    bottom-up, so the parameters are credited as a set rather than positionally -- the alternative is
    silently wrong whenever a test stacks two patches and inspects only one.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.withitem):
            if isinstance(node.context_expr, ast.Call) and isinstance(node.optional_vars, ast.Name):
                effect = _patch_target(node.context_expr, effects)
                if effect:
                    aliases[node.optional_vars.id] = effect
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            patched = [_patch_target(d, effects) for d in node.decorator_list if isinstance(d, ast.Call)]
            patched = [e for e in patched if e]
            if not patched:
                continue
            params = [a.arg for a in node.args.args if a.arg not in {"self", "cls"}]
            for param in params[-len(patched) :] if len(params) >= len(patched) else params:
                for effect in patched:
                    aliases.setdefault(param, effect)
    return aliases


#: Drivers whose ``connect`` a test only calls when it means to talk to a REAL database.
_REAL_DB_MODULES = frozenset({"sqlite3", "psycopg2", "duckdb", "pymysql", "MySQLdb"})


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
    not "does the assertion afterwards check the right thing".
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "connect":
            root = func.value
            if isinstance(root, ast.Name) and root.id in _REAL_DB_MODULES:
                return True
            # `psycopg2.extras.connect`-style chains, and `from x import y; y.db.connect()`.
            if isinstance(root, ast.Attribute) and root.attr in _REAL_DB_MODULES:
                return True
    return False


#: How a fixture says it is building a real database handle, beyond a driver's own ``connect``.
_REAL_DB_FACTORIES = frozenset({"create_engine", "create_async_engine"})


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
    try:
        tree = ast.parse(conftest.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()

    def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name == "fixture":
                return True
        return False

    direct: set[str] = set()
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and _is_fixture(n)]
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


def _requests_a_real_database_fixture(tree: ast.AST, fixtures: frozenset[str]) -> bool:
    """True when a test function in *tree* asks for one of *fixtures* by parameter name."""
    if not fixtures:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test"):
            continue
        params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        if params & fixtures:
            return True
    return False


def _owns_its_connection(path: Path) -> bool:
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
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
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
            if (
                isinstance(target, ast.Name)
                and target.id in _REAL_DB_MODULES
                and isinstance(attribute, ast.Constant)
                and attribute.value == "connect"
            ):
                return True
    return False


def _inspects(path: Path, effects: Sequence[str], db_fixtures: frozenset[str] = frozenset()) -> set[str]:
    """Effects this test inspects on a mock: ``x.commit.assert_called()``, ``x.execute.call_args``.

    Matched on the ATTRIBUTE CHAIN rather than on text, so ``# commit is asserted below`` in a comment
    and a local variable called ``commit`` are both correctly ignored.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    # A test that opens its own database is exercising every effect the module performs, and
    # observing the ROWS rather than the call. Nothing more specific is needed or available.
    if _exercises_against_a_real_database(tree) or _requests_a_real_database_fixture(tree, db_fixtures):
        return set(effects)
    found: set[str] = set()
    aliases = _patch_aliases(tree, effects)
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


def _imported_names(path: Path) -> set[str]:
    """Every dotted name the file imports, in both forms.

    `from pipeline import replay` names a MODULE when the submodule exists, so `pipeline.replay` is
    emitted alongside `pipeline` and the caller resolves both against the real file set.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def build_import_map(repo_root: Path, *, package_name: str = "", src_dir: str = "") -> dict[str, list[str]]:
    """``{module path: [test paths importing it]}``, both relative to *repo_root*.

    Import edges rather than measured coverage, and deliberately so. Coverage attributes an executed
    line to the test that was running, but a module's top level runs during COLLECTION, so a test that
    imports a module and reads its constants executes none of its lines and would not appear. For
    "does any test even look at this module", the import edge is the honest relation.

    One level of transitivity through the project's own modules, so a test importing ``pipeline`` is
    credited with ``pipeline/replay.py`` as well. *package_name* covers repositories whose tests
    import themselves as a package (``from dashboard import data``); *src_dir* covers a src layout,
    where the file at ``src/pkg/x.py`` is imported as ``pkg.x``.
    """

    def module_name(path: Path) -> str:
        rel = path.relative_to(repo_root).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    sources = [
        p
        for p in repo_root.rglob("*.py")
        if not _SKIP_DIRS & set(p.parts) and "tests" not in p.relative_to(repo_root).parts
    ]
    tests = [p for p in repo_root.rglob("test_*.py") if not _SKIP_DIRS & set(p.parts)]

    by_module: dict[str, Path] = {}
    for path in sources:
        flat = module_name(path)
        by_module[flat] = path
        if package_name:
            by_module[f"{package_name}.{flat}" if flat else package_name] = path
        if src_dir and flat.startswith(f"{src_dir}."):
            by_module[flat[len(src_dir) + 1 :]] = path

    own_edges = {name: {i for i in _imported_names(path) if i in by_module} for name, path in by_module.items()}

    hits: dict[str, set[str]] = {}
    for test in tests:
        reached = {i for i in _imported_names(test) if i in by_module}
        reached |= {n for m in list(reached) for n in own_edges.get(m, set())}
        for name in reached:
            hits.setdefault(by_module[name].relative_to(repo_root).as_posix(), set()).add(
                test.relative_to(repo_root).as_posix()
            )
    return {source: sorted(found) for source, found in sorted(hits.items())}


def find_unasserted_effects(
    repo_root: Path,
    import_map: Mapping[str, Sequence[str]],
    *,
    effects: Sequence[str] = DEFAULT_EFFECTS,
) -> dict[str, str]:
    """``{"<module>::<effect>": description}`` for effects no importing test inspects.

    *import_map* is ``{module path: [test paths that import it]}``, both relative to *repo_root*. The
    importing tests are the right population rather than all tests: a suite-wide "somebody somewhere
    asserts on commit" would be satisfied by one unrelated test and would gate nothing.
    """
    inspected: dict[str, set[str]] = {}
    problems: dict[str, str] = {}
    # Every conftest in the tree, because a `db_session` may be defined in the root one and used
    # three packages down. Collected once: this is an AST parse per conftest, not per test.
    db_fixtures = frozenset(
        name
        for conftest in repo_root.rglob("conftest.py")
        if not _SKIP_DIRS & set(conftest.parts)
        for name in _fixture_names_backed_by_a_real_database(conftest)
    )

    for module, tests in sorted(import_map.items()):
        module_path = repo_root / module
        if not module_path.is_file():
            continue
        performed = _performs(module_path, effects)
        if not performed:
            continue
        # A module that opens its own connection offers no seam to hand a mock through, so a test
        # either patches the driver -- visibly -- or runs the real thing. One importing test that
        # does neither of those two things is still running it, and the assertion it makes will be
        # about the ROWS, through the module's own reader. See `_owns_its_connection`.
        if _owns_its_connection(module_path) and any(
            (repo_root / test).is_file() and not _patches_a_real_driver(ast.parse((repo_root / test).read_text(encoding="utf-8", errors="replace")))
            for test in tests
        ):
            continue
        for effect in sorted(performed):
            checked_by = None
            for test in tests:
                test_path = repo_root / test
                if not test_path.is_file():
                    continue
                if test not in inspected:
                    inspected[test] = _inspects(test_path, effects, db_fixtures)
                if effect in inspected[test]:
                    checked_by = test
                    break
            if checked_by is None:
                problems[f"{module}::{effect}"] = (
                    f"{module} calls `{effect}(...)` and none of its {len(tests)} importing test(s) "
                    f"ever inspects that call, so deleting it would not fail a single one"
                )
    return problems


def assert_effects_are_asserted(
    repo_root: Path,
    import_map: Mapping[str, Sequence[str]],
    accepted: Iterable[str] = (),
    *,
    effects: Sequence[str] = DEFAULT_EFFECTS,
) -> None:
    """Fail on an unasserted effect that is not already accepted. Ratchet, not a gate.

    *accepted* is the baseline: what was already true when the check was wired. Existing debt does not
    block a commit, a NEW unasserted effect does, and the set can only shrink.
    """
    import pytest

    found = find_unasserted_effects(repo_root, import_map, effects=effects)
    accepted = set(accepted)
    new = {key: why for key, why in found.items() if key not in accepted}
    if new:
        pytest.fail(
            f"{len(new)} effect(s) performed but inspected by no importing test:\n  "
            + "\n  ".join(f"{key}: {why}" for key, why in sorted(new.items()))
        )
