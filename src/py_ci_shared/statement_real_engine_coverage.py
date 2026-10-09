"""Every SQL statement constant a package defines is exercised by a real-engine test, not only a fake cursor.

A unit suite that drives SQL through a fake cursor proves nothing about the SQL: the cursor accepts any string. A
statement that selects no row by construction, a back-off test that seeded an empty query and a poll that took 175 s
were each visible only on a real engine. So every statement the package defines (module-level constants that
``sql_verifier_coverage.sql_constants`` recognises: SELECT/WITH/INSERT/UPDATE/DELETE/REFRESH) must be REFERENCED BY NAME
from at least one test of the real-engine tier, and a statement without one is a finding.

The tier is the caller's definition, as either or both of:

* ``engine_test_dirs`` (required; ``()`` when only markers define the tier): directories (relative to *root*, or absolute) whose Python files all run on a real engine
  (``tests/integration``). A file there counts whole.
* ``engine_markers``: pytest marker names (``integration``). A module-level ``pytestmark`` carrying one counts the
  whole file; ``@pytest.mark.<marker>`` on a test function or class counts what that function or class references.
  Files are searched under ``marker_search_dirs`` (default: all of *root*).

"Referenced" means the constant's bare NAME appears as an identifier in an engine-tier file: an ``import`` of it, a bare
name, or an attribute (``queries.SELECT_X``). One level of import alias is followed: ``from pkg.queries import SELECT_X
as Q`` in a production module lets an engine test that uses ``Q`` credit ``SELECT_X``. A name that two modules define is
credited to both (a reference carries no module), so keep statement names unique per package.

A reference proves the test can reach the statement, not that it asserts anything about the rows. The gate is the floor
under that: no engine test at all.

Findings are keyed ``module.NAME`` and ratcheted by a shrink-only baseline mapping each to its reason; a listed name that
now has an engine test (or is gone) fails until removed, and a refresh never adds an entry without the growth opt-in.

Usage in a consumer's meta test::

    from py_ci_shared.statement_real_engine_coverage import assert_every_statement_has_an_engine_test

    def test_every_statement_has_an_engine_test():
        assert_every_statement_has_an_engine_test(
            "src", "tests/_engine_baseline.json", engine_test_dirs=["tests/integration"], exclude_top_dirs=["tests", "scripts"]
        )
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import UNJUSTIFIED_MARKER, Baseline, DEFAULT_EXCLUDE, EmptyScanError, Finding, ParsedFile, scan_python
from ._core.scan import ScanResult
from .sql_verifier_coverage import DEFAULT_STATEMENT_STARTS, _module_name, _scan, sql_constants

__all__ = ["RULE", "assert_every_statement_has_an_engine_test", "find_statements_without_engine_test"]

RULE = "statement-without-engine-test"
GATE = "statement_real_engine_coverage"


def _names_in(node: ast.AST) -> set[str]:
    """Every identifier *node* reads or imports: names, attributes and the original name of an imported symbol."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
        elif isinstance(sub, ast.alias):
            out.add(sub.name.rsplit(".", 1)[-1])
            if sub.asname:
                out.add(sub.asname)
    return out


def _is_marker(expr: ast.expr, markers: frozenset[str]) -> bool:
    """``pytest.mark.<m>`` or ``pytest.mark.<m>(...)`` for an *m* in *markers*."""
    if isinstance(expr, ast.Call):
        expr = expr.func
    return isinstance(expr, ast.Attribute) and expr.attr in markers and isinstance(expr.value, ast.Attribute) and expr.value.attr == "mark"


def _module_marked(tree: ast.Module, markers: frozenset[str]) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets) and node.value is not None:
                values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
                if any(_is_marker(v, markers) for v in values):
                    return True
    return False


def _marked_names(tree: ast.Module, markers: frozenset[str]) -> set[str]:
    """What a marker-tier file references: all of it under a module ``pytestmark``, else the marked defs and classes only."""
    if _module_marked(tree, markers):
        return _names_in(tree)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and any(_is_marker(d, markers) for d in node.decorator_list):
            out |= _names_in(node)
    return out


def _resolve(root: Path, entry: Union[str, Path]) -> Path:
    p = Path(entry)
    return p if p.is_absolute() else root / p


def _engine_names(
    root: Path,
    engine_test_dirs: tuple[Union[str, Path], ...],
    engine_markers: frozenset[str],
    marker_search_dirs: tuple[Union[str, Path], ...],
    *,
    min_engine_files: int,
    allow_unparsed: bool,
    use_git: Optional[bool],
) -> set[str]:
    names: set[str] = set()
    tier_files = 0
    unparsed = []
    for entry in engine_test_dirs:
        scan = scan_python(_resolve(root, entry), exclude=DEFAULT_EXCLUDE, use_git=use_git)
        unparsed += scan.unparsed
        tier_files += scan.parsed_count
        for parsed in scan:
            names |= _names_in(parsed.tree)
    if engine_markers:
        scan = scan_python([_resolve(root, e) for e in marker_search_dirs], exclude=DEFAULT_EXCLUDE, use_git=use_git)
        unparsed += scan.unparsed
        for parsed in scan:
            marked = _marked_names(parsed.tree, engine_markers)
            if marked:
                tier_files += 1
                names |= marked
    if unparsed and not allow_unparsed:
        ScanResult(root=root, unparsed=unparsed).check_unparsed()
    if tier_files < min_engine_files:
        raise EmptyScanError(
            f"only {tier_files} real-engine test file(s) found (engine_test_dirs={[str(d) for d in engine_test_dirs]}, "
            f"engine_markers={sorted(engine_markers)}); expected at least {min_engine_files}. "
            "Point engine_test_dirs/engine_markers at the tests that run on a real engine: with none, every statement is a finding."
        )
    return names


def _import_aliases(files: Iterable[ParsedFile], constant_names: set[str]) -> dict[str, set[str]]:
    """``{alias: {original constant names}}`` for ``from m import NAME as alias`` where NAME is a known statement constant."""
    out: dict[str, set[str]] = {}
    for parsed in files:
        for node in ast.walk(parsed.tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname and alias.name in constant_names:
                        out.setdefault(alias.asname, set()).add(alias.name)
    return out


def _lines(scan: ScanResult) -> dict[str, tuple[str, int]]:
    """``{"module.NAME": (file, line)}`` for the module-level assignments of *scan*."""
    out: dict[str, tuple[str, int]] = {}
    for parsed in scan:
        module = _module_name(parsed.rel)
        for node in parsed.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        out.setdefault(f"{module}.{target.id}", (parsed.rel, node.lineno))
    return out


def find_statements_without_engine_test(
    root: Union[str, Path],
    *,
    engine_test_dirs: Iterable[Union[str, Path]],
    engine_markers: Iterable[str] = (),
    marker_search_dirs: Iterable[Union[str, Path]] = (".",),
    exclude_top_dirs: Iterable[str] = (),
    statement_starts: Iterable[str] = DEFAULT_STATEMENT_STARTS,
    min_files: int = 1,
    min_statements: int = 1,
    min_engine_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """One finding per SQL statement constant under *root* that no real-engine test references by name.

    *exclude_top_dirs* are top-level directories of *root* that hold no production statements (tests, scripts, probes).
    Raises ``ValueError`` when both *engine_test_dirs* (required, may be empty) and *engine_markers* are empty, ``EmptyScanError`` when fewer
    than *min_files* production files parsed, fewer than *min_statements* statements were found (the scan lost its subject), or
    fewer than *min_engine_files* engine-tier files were found, and ``UnparsedFilesError`` for a file that cannot be read or
    parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    dirs, markers = tuple(engine_test_dirs), frozenset(engine_markers)
    if not dirs and not markers:
        raise ValueError("define the real-engine tier: pass engine_test_dirs and/or engine_markers")
    base, skip = Path(root), set(exclude_top_dirs)
    starts = tuple(s.upper() for s in statement_starts)
    scan = _scan(base, skip, use_git)
    scan.min_files = min_files
    scan.assert_ok(allow_unparsed=allow_unparsed)
    constants = sql_constants(base, exclude_top_dirs=skip, statement_starts=starts, allow_unparsed=allow_unparsed, use_git=use_git)
    if len(constants) < min_statements:
        raise EmptyScanError(
            f"only {len(constants)} SQL statement constant(s) found under {base}; expected at least {min_statements}. Check the scan, it has lost its subject."
        )
    referenced = _engine_names(
        base,
        dirs,
        markers,
        tuple(marker_search_dirs),
        min_engine_files=min_engine_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    bare = {c.rsplit(".", 1)[-1] for c in constants}
    for alias, originals in _import_aliases(scan, bare).items():
        if alias in referenced:
            referenced |= originals
    where = _lines(scan)
    tier = ", ".join([str(d) for d in dirs] + [f"@pytest.mark.{m}" for m in sorted(markers)])
    out: list[Finding] = []
    for constant in sorted(constants):
        name = constant.rsplit(".", 1)[-1]
        if name in referenced:
            continue
        path, line = where.get(constant, (constant.replace(".", "/") + ".py", 1))
        message = f"{constant} has no real-engine test; Add one ({tier}) that runs it on a real engine and seeds a row it must select and one it must not"
        out.append(Finding(path, line, RULE, message, key=constant))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_every_statement_has_an_engine_test(
    root: Union[str, Path],
    baseline_path: Optional[Union[str, Path]] = None,
    *,
    engine_test_dirs: Iterable[Union[str, Path]],
    engine_markers: Iterable[str] = (),
    marker_search_dirs: Iterable[Union[str, Path]] = (".",),
    exclude_top_dirs: Iterable[str] = (),
    statement_starts: Iterable[str] = DEFAULT_STATEMENT_STARTS,
    refresh: bool = False,
    min_files: int = 1,
    min_statements: int = 1,
    min_engine_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any statement without an engine test, or with *baseline_path* on any the baseline does not accept.

    The baseline maps ``module.NAME`` to the reason it has no engine test yet. A refresh only removes entries; a new entry
    is written with a ``NEEDS-JUSTIFICATION`` note (only under the growth opt-in) that a normal run rejects until it is replaced by a reason.
    """
    found = find_statements_without_engine_test(
        root,
        engine_test_dirs=engine_test_dirs,
        engine_markers=engine_markers,
        marker_search_dirs=marker_search_dirs,
        exclude_top_dirs=exclude_top_dirs,
        statement_starts=statement_starts,
        min_files=min_files,
        min_statements=min_statements,
        min_engine_files=min_engine_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "Add a real-engine test that references each statement, or list it in the baseline with the reason it has none"
    if baseline_path is not None:
        baseline = Baseline(
            baseline_path,
            gate=GATE,
            refresh_command=f"PY_CI_SHARED_REFRESH={GATE} (shrinks only; new entries need PY_CI_SHARED_REFRESH_ALLOW_GROW=1)",
            new_note=UNJUSTIFIED_MARKER + ": say why this statement has no real-engine test",
        )
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} statement(s) without a real-engine test; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
