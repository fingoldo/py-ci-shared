"""A module-level import of a package that is only an optional extra must be guarded, or ``import package`` breaks.

``pip install mypkg`` installs ``[project.dependencies]`` and nothing else; every other library lives in an extra
(``pip install mypkg[db]``). A module that does ``import zstandard`` at the top of the file, where ``zstandard`` is only
in the ``db`` extra, makes every importer of that module fail with a bare ``ModuleNotFoundError: No module named
'zstandard'`` for a user who did not ask for the extra, even when the code path in use never touches it. In the shipped
case ``zstandard`` (``db`` extra), ``catboost`` and ``lightgbm`` (``boosting``) and ``properscoring`` (``calibration``)
were imported at module level in modules the base package imports, so ``import mlframe.training`` failed on a core-only
install while the full-extras CI passed::

    import zstandard                      # <- reported: only in the `db` extra

    try:
        import catboost
    except ImportError:                   # accepted: guarded
        catboost = None

    def load():
        import zstandard                  # accepted: lazy, runs only on the code path that needs it

A module-level import (also inside ``if``/``with``/class bodies, which run at import) of a third-party top-level package
is accepted when any of these holds:

* its distribution is in ``[project.dependencies]`` (environment markers included);
* it sits in a ``try`` whose handlers catch ``ImportError``/``ModuleNotFoundError`` (or ``Exception``, ``BaseException``,
  a bare ``except``), or under ``with contextlib.suppress(ImportError)``;
* it sits under ``if TYPE_CHECKING:``, under ``if <flag>:`` where ``<flag>`` is a module-level name assigned inside such a
  ``try`` (``_HAS_CATBOOST``), or under a test that calls ``find_spec``;
* it is inside a function body (lazy by construction);
* a line of the statement carries ``# optional-import-ok: <reason>``;
* the module's path contains an ``ignore`` fragment (``_benchmarks``, ``_vendored``, ``benchmarks`` and ``profiling`` by
  default: scripts and vendored code nobody imports), or an ``extra_namespaces`` fragment that lists the import name
  (``{"training/neural": ["torch", "lightning"]}``: a subpackage that IS an extra's implementation).

The distribution of an import name comes from ``name_map`` (``{"sklearn": "scikit-learn"}``), the built-in table of the
usual mismatches (``yaml`` -> ``pyyaml``, ``PIL`` -> ``pillow``), the normalised import name, and last the metadata of the
declared distributions installed in the running interpreter. An import whose distribution is declared nowhere in
``pyproject.toml`` (not a dependency, not in any extra) is reported too, as undeclared, unless ``flag_undeclared=False``;
list its distribution in ``name_map`` when the project gets it transitively (``{"llvmlite": "numba"}``). A sibling file next
to the importing one (a script run as ``python dir/script.py``) is first-party.

By default every module under the packages is judged. ``reachable_from=["mypkg"]`` narrows that to the modules that
``import mypkg`` pulls in through module-level first-party imports: the files a core-only user actually loads, which is where
a bare extra import breaks them, while a subpackage dedicated to an extra (``mypkg.llm`` importing ``httpx``) that nothing in
the base import reaches is left alone.

``root`` is the repo root (its ``pyproject.toml`` is read, or the one named by ``pyproject``, which must be named
``pyproject.toml``). ``packages`` names the package directories to scan, relative to ``root``; by default each ``src/<pkg>``
and each top-level directory with an ``__init__.py`` other than tests, docs, scripts and the like.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._ci_install_parts import is_stdlib
from ._core import Baseline, CorpusError, Finding, ParsedFile, ScanResult, parse_source, scan_python
from ._core.gate_contract import check_scan
from ._import_graph import Declared, ModuleImport, ModuleIndex, distributions, module_level_imports, is_sibling, read_declared, reach

__all__ = [
    "DEFAULT_IGNORE",
    "RULE",
    "assert_optional_imports_guarded",
    "find_unguarded_optional_imports",
]

RULE = "optional-import-unguarded"
DEFAULT_IGNORE = ("/_benchmarks/", "/benchmarks/", "/profiling/", "/_vendored/")
_NOT_PACKAGES = frozenset(
    {"tests", "test", "testing", "docs", "doc", "scripts", "script", "examples", "example", "benchmarks", "build", "dist", "tools", "notebooks", "node_modules"}
)


def _package_dirs(root: Path, packages: Optional[Sequence[Union[str, Path]]]) -> list[Path]:
    if packages is not None:
        return [Path(p) if Path(p).is_absolute() else root / p for p in packages]
    found: list[Path] = []
    for base in (root / "src", root):
        if base.is_dir():
            found.extend(
                sorted(
                    child
                    for child in base.iterdir()
                    if child.is_dir() and (child / "__init__.py").is_file() and not child.name.startswith((".", "_")) and child.name not in _NOT_PACKAGES
                )
            )
    return found


def _first_party(root: Path, pkgs: Sequence[Path], project: str) -> frozenset[str]:
    names = {project.replace("-", "_")} | {p.name for p in pkgs}
    for base in (root, root / "src"):
        if base.is_dir():
            names.update(c.stem for c in base.iterdir() if not c.name.startswith(".") and (c.is_dir() or c.suffix == ".py"))
    return frozenset(names)


def _reachable(base: Path, pkgs: Sequence[Path], scan: ScanResult, modules: Sequence[str]) -> frozenset[Path]:
    """The scanned files that importing each of *modules* loads through module-level first-party imports."""
    index = ModuleIndex([base, base / "src", *(p.parent for p in pkgs)])
    parsed = {p.path.resolve(): p for p in scan}

    def imports_of(path: Path) -> list[ModuleImport]:
        known = parsed.get(path)
        if known is not None:
            return module_level_imports(known.tree, known.source.splitlines())
        source, tree = parse_source(path)
        return module_level_imports(tree, source.splitlines())

    starts: list[Path] = []
    for dotted in modules:
        files = index.resolve(dotted)
        if not files:
            raise CorpusError(f"reachable_from names {dotted!r}, which is not a module under {base}")
        starts.extend(files)
    parents, _ = reach(index, starts, imports_of)
    return frozenset(parents)


def find_unguarded_optional_imports(
    root: Union[str, Path],
    *,
    packages: Optional[Sequence[Union[str, Path]]] = None,
    pyproject: Optional[Union[str, Path]] = None,
    name_map: Optional[Mapping[str, Any]] = None,
    ignore: Iterable[str] = DEFAULT_IGNORE,
    extra_namespaces: Optional[Mapping[str, Iterable[str]]] = None,
    reachable_from: Optional[Sequence[str]] = None,
    flag_undeclared: bool = True,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every module-level import of a non-core third-party package that no guard covers, sorted by path and line.

    Raises ``CorpusError`` when no package directory is found, ``EmptyScanError`` below *min_files* parsed modules and
    ``UnparsedFilesError`` for a module (or the pyproject) that cannot be read, unless *allow_unparsed* for the modules.
    """
    base = Path(root)
    toml = Path(pyproject) if pyproject is not None else base / "pyproject.toml"
    declared = read_declared(base, toml)
    pkgs = _package_dirs(base, packages)
    if not pkgs:
        raise CorpusError(f"no package directory found under {base} (looked for src/<pkg> and top-level directories with __init__.py); pass packages=")
    scan = scan_python(pkgs, root=base, use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    skipped = tuple(ignore)
    local = _first_party(base, pkgs, declared.project)
    table = dict(name_map or {})
    namespaces = {fragment: frozenset(names) for fragment, names in (extra_namespaces or {}).items()}
    reached = _reachable(base, pkgs, scan, reachable_from) if reachable_from is not None else None
    findings: list[Finding] = []
    for parsed in scan:
        if any(fragment in f"/{parsed.rel}" for fragment in skipped) or (reached is not None and parsed.path.resolve() not in reached):
            continue
        allowed = frozenset().union(*(names for fragment, names in namespaces.items() if fragment in parsed.rel))
        findings.extend(_module_findings(parsed, declared, local, table, allowed, flag_undeclared))
    return sorted(findings, key=lambda f: (f.path, f.line, f.message))


def _module_findings(
    parsed: ParsedFile, declared: Declared, local: frozenset[str], table: Mapping[str, Any], allowed: frozenset[str], flag_undeclared: bool
) -> list[Finding]:
    out: list[Finding] = []
    every_extra = declared.every_extra
    for imp in module_level_imports(parsed.tree, parsed.source.splitlines()):
        if imp.level or not imp.module:
            continue
        top = imp.module.split(".")[0]
        if top == "__future__" or top in allowed or top in local or is_stdlib(top) or is_sibling(parsed.path, top):
            continue
        candidates = distributions(imp.module, declared, table)
        if any(c in declared.core for c in candidates):
            continue
        in_extras = [c for c in candidates if c in every_extra]
        if in_extras:
            extras = ", ".join(repr(e) for e in declared.extras_of(in_extras[0]))
            why = f"distribution {in_extras[0]!r} is only in the {extras} extra"
        elif flag_undeclared:
            why = f"distribution {candidates[0]!r} is declared nowhere in pyproject.toml"
        else:
            continue
        message = (
            f"module-level import of {top!r}: {why}, so importing this module fails with ModuleNotFoundError for a user who did not "
            "install it. Import it inside the function that needs it, guard it with try/except ImportError, or mark the line "
            "`# optional-import-ok: <reason>`"
        )
        out.append(Finding(parsed.rel, imp.line, RULE, message))
    return out


def assert_optional_imports_guarded(
    root: Union[str, Path],
    *,
    packages: Optional[Sequence[Union[str, Path]]] = None,
    pyproject: Optional[Union[str, Path]] = None,
    name_map: Optional[Mapping[str, Any]] = None,
    ignore: Iterable[str] = DEFAULT_IGNORE,
    extra_namespaces: Optional[Mapping[str, Iterable[str]]] = None,
    reachable_from: Optional[Sequence[str]] = None,
    flag_undeclared: bool = True,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any unguarded module-level optional import, or with *baseline_path* on any the baseline does not accept."""
    found = find_unguarded_optional_imports(
        root,
        packages=packages,
        pyproject=pyproject,
        name_map=name_map,
        ignore=ignore,
        extra_namespaces=extra_namespaces,
        reachable_from=reachable_from,
        flag_undeclared=flag_undeclared,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "import it lazily, guard it with try/except ImportError, or mark the line `# optional-import-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="optional_imports_guarded", refresh_command="PY_CI_SHARED_REFRESH=optional_imports_guarded")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(
            f"{len(found)} module-level import(s) of an optional package without a guard; {guidance}:\n  " + "\n  ".join(f.render() for f in found)
        )
