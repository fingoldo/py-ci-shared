"""A package does not import or reach underscore-private names of another package's modules (by bare module name, from-import or alias).

The defect: a dashboard put the pipeline's directory on ``sys.path`` and imported its modules by BARE name, then reached
underscore-private names inside them (``from show_top_jobs import _get_dsn``, ``import top_jobs_query as tq; tq._counts_cache.clear()``).
A routine private rename on the pipeline side broke the dashboard with no pipeline test failing, and one call site swallowed the
resulting ImportError as "no database configured". ``private_imports`` cannot see it: it checks underscore MODULES (``pkg._mod``),
not underscore NAMES inside public modules, and it does not follow aliases.

What is reported, in package A's non-test files, when the module's owner is another package B:

* ``from M import _name`` (M a bare module or dotted module of B);
* ``import M [as x]`` / ``from pkg import M [as x]`` followed by ``x._name`` as a read, call, write or ``del``.

Never reported: dunder names and a bare ``_``; anything under ``if TYPE_CHECKING:`` (it never binds at run time); relative imports
(same package). A private name that M re-exports under a public
name (``NAME = _NAME`` or ``from .x import _name as name`` at module level) is still a finding, but its message names the alias to
use. ``allowed`` accepts a reach with a reason; an entry that no longer occurs, or has no reason, is itself a failure.

Module ownership: *packages* maps a label to ``{"root": dir, "sys_path": [labels]}`` (or just the dir). A module name ``M`` is owned
by the package whose root holds ``M.py`` or ``M/__init__.py``. ``sys_path`` lists, in order, the labels whose roots package A can
import bare names from (first hit wins, like ``sys.path``); omit it and every package counts, so a name found in several roots is
AMBIGUOUS and is reported as a finding of rule ``cross-package-ambiguous-module`` (skipped, listed, never a pass) until the
consumer states the resolution order.

Out of scope, by design: ``importlib.import_module("M")._name``, ``getattr(M, "_name")`` and ``__import__``: the name is a string.
A local variable that shadows an import alias is not told from the alias (it errs toward finding).

Usage in a consumer's meta test::

    from py_ci_shared.cross_package_private_names import assert_cross_package_private_names

    def test_no_cross_package_private_names():
        assert_cross_package_private_names(
            {
                "realtime_applications": REPO / "realtime_applications",
                "dashboard": {"root": REPO / "dashboard", "sys_path": ["realtime_applications"]},
                "production_scrapers": {"root": REPO / "production_scrapers", "sys_path": ["production_scrapers", "realtime_applications"]},
            },
            min_files=5,
        )
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ._core import DEFAULT_EXCLUDE, CorpusError, Finding, ImportAliases, ParsedFile, parse_file, scan_python
from ._core.errors import SourceError
from ._core.gate_contract import check_scan

__all__ = ["AMBIGUOUS_RULE", "RULE", "assert_cross_package_private_names", "find_cross_package_private_names"]

RULE = "cross-package-private-names"
AMBIGUOUS_RULE = "cross-package-ambiguous-module"

#: Directory names that hold test-adjacent or non-production code; skipped unless ``include_tests``.
TEST_PARTS = frozenset({"tests", "test", "audits"})
PackageSpec = Union[str, Path, Mapping[str, object]]


@dataclass(frozen=True)
class _Package:
    label: str
    root: Path
    sys_path: Optional[tuple[str, ...]]  # None: every package's root, so an ambiguous name stays ambiguous


def _resolve_specs(packages: Mapping[str, PackageSpec]) -> dict[str, _Package]:
    out: dict[str, _Package] = {}
    for label, spec in packages.items():
        if isinstance(spec, Mapping):
            if "root" not in spec:
                raise ValueError(f"package {label!r}: the spec needs a 'root' directory")
            raw_path = spec.get("sys_path")
            root = Path(str(spec["root"]))
            sys_path = None if raw_path is None else tuple(str(x) for x in raw_path)  # type: ignore[attr-defined]
        else:
            root, sys_path = Path(spec), None
        out[label] = _Package(label, root, sys_path)
    for pkg in out.values():
        unknown = [x for x in pkg.sys_path or () if x not in out]
        if unknown:
            raise ValueError(f"package {pkg.label!r}: sys_path names unknown package(s) {unknown}; known: {sorted(out)}")
    return out


def _is_test_file(path: Path) -> bool:
    name = path.name
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


def _top_level_names(root: Path) -> frozenset[str]:
    if not root.is_dir():
        raise CorpusError(f"package root does not exist or is not a directory: {root}")
    names = {p.stem for p in root.glob("*.py")}
    names |= {p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    return frozenset(names)


def _module_file(root: Path, dotted: str) -> Optional[Path]:
    base = root.joinpath(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _is_private(name: str) -> bool:
    return name.startswith("_") and name != "_" and not (name.startswith("__") and name.endswith("__"))


def _type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _runtime_nodes(tree: ast.AST) -> Iterator[ast.AST]:
    """Every node that can execute: the body of ``if TYPE_CHECKING:`` is skipped, its ``else`` kept."""
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.If) and _type_checking(node.test):
            stack.extend(reversed(node.orelse))
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


class _Owners:
    """Which package owns a bare module name, from the point of view of one importing package."""

    def __init__(self, specs: Mapping[str, _Package]) -> None:
        self.specs = specs
        self.names = {label: _top_level_names(p.root) for label, p in specs.items()}

    def candidates(self, importer: _Package, top: str) -> list[str]:
        """Owner labels of *top* seen from *importer*: one (first on its sys_path) or, with no sys_path, every holder."""
        visible = importer.sys_path if importer.sys_path is not None else tuple(self.specs)
        holders = [label for label in visible if top in self.names[label]]
        if importer.sys_path is not None:
            return holders[:1]
        return holders

    def is_module(self, owner: str, dotted: str) -> bool:
        return _module_file(self.specs[owner].root, dotted) is not None


@dataclass(frozen=True)
class _Reach:
    importer: str
    owner: str
    module: str
    name: str
    path: str
    line: int
    alias: Optional[str]

    @property
    def key(self) -> str:
        return f"{self.importer}->{self.owner}:{self.module}.{self.name}"

    def finding(self) -> Finding:
        hint = f"a public alias exists: use {self.module}.{self.alias}" if self.alias else f"ask {self.owner} for a public name and use that"
        message = f"{self.key}: {self.importer} reaches a private name of {self.owner}'s module {self.module}; {hint}"
        return Finding(self.path, self.line, RULE, message)


def _public_alias(module_file: Path, private: str, cache: "_AliasCache") -> Optional[str]:
    """The public name *module_file* binds to the same object as *private* at module level, if any."""
    key = (module_file, private)
    if key in cache:
        return cache[key]
    found: Optional[str] = None
    try:
        tree = parse_file(module_file)
    except SourceError:
        tree = None  # the owner's own scan reports the unreadable file; here the hint is merely absent
    for node in tree.body if tree is not None else ():
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == private and alias.asname and not alias.asname.startswith("_"):
                    found = alias.asname
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Name) and value.id == private:
                found = next((t.id for t in targets if isinstance(t, ast.Name) and not t.id.startswith("_")), found)
    cache[key] = found
    return found


def _head(node: ast.expr) -> Optional[str]:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


_AliasCache = dict[tuple[Path, str], Optional[str]]


def _reach_finding(parsed: ParsedFile, importer: _Package, owners: _Owners, cache: "_AliasCache", module: str, name: str, line: int) -> Optional[Finding]:
    """The finding for ``module.name`` reached from *importer*, or ``None`` when the module is the importer's own or unknown."""
    cands = owners.candidates(importer, module.split(".", 1)[0])
    if not cands or cands == [importer.label]:
        return None
    if len(cands) > 1:
        where = f"{importer.label} -> {{{', '.join(cands)}}}:{module}.{name}"
        why = f"skipped, the module name is found in several packages; give {importer.label} a sys_path to say which one it imports"
        return Finding(parsed.rel, line, AMBIGUOUS_RULE, f"{where}: {why}")
    owner = cands[0]
    file = _module_file(owners.specs[owner].root, module)
    alias = _public_alias(file, name, cache) if file is not None else None
    return _Reach(importer.label, owner, module, name, parsed.rel, line, alias).finding()


def _module_of_attribute(node: ast.Attribute, aliases: ImportAliases, importer: _Package, owners: _Owners) -> Optional[str]:
    """The dotted module an attribute is read from, when an import binds its head and the dotted path IS a module of some package."""
    head = _head(node.value)
    if head is None or not aliases.is_imported(head):
        return None
    dotted = aliases.qualified_name(node.value)
    if dotted is None or dotted.startswith("."):
        return None
    cands = owners.candidates(importer, dotted.split(".", 1)[0])
    # A module attribute only: `Klass._x` or `mod.func._attr` hang off an object, not off a module.
    return dotted if any(owners.is_module(c, dotted) for c in cands) else None


def _reaches_in(parsed: ParsedFile, importer: _Package, owners: _Owners, cache: "_AliasCache") -> list[Finding]:
    nodes = list(_runtime_nodes(parsed.tree))
    imports = [n for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom))]
    aliases = ImportAliases.from_tree(ast.Module(body=imports, type_ignores=[]))  # type: ignore[arg-type]
    reaches: list[tuple[str, str, int]] = []
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            reaches.extend((node.module, a.name, node.lineno) for a in node.names if _is_private(a.name))
        elif isinstance(node, ast.Attribute) and _is_private(node.attr):
            module = _module_of_attribute(node, aliases, importer, owners)
            if module is not None:
                reaches.append((module, node.attr, node.lineno))
    found = (_reach_finding(parsed, importer, owners, cache, m, n, line) for m, n, line in reaches)
    return [f for f in found if f is not None]


def find_cross_package_private_names(
    packages: Mapping[str, PackageSpec],
    *,
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every reach (rule ``cross-package-private-names``) and every unresolvable module name (rule ``cross-package-ambiguous-module``).

    Raises ``EmptyScanError`` when a package parsed fewer than *min_files* files, ``UnparsedFilesError`` for a file that cannot be
    read or parsed (unless *allow_unparsed*) and ``CorpusError`` for a missing package root.
    """
    specs = _resolve_specs(packages)
    owners = _Owners(specs)
    exclude = DEFAULT_EXCLUDE if include_tests else DEFAULT_EXCLUDE | TEST_PARTS
    cache: _AliasCache = {}
    out: list[Finding] = []
    for pkg in specs.values():
        scan = scan_python(pkg.root, exclude=exclude, use_git=use_git)
        check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
        for parsed in scan:
            if not include_tests and _is_test_file(parsed.path):
                continue
            out.extend(_reaches_in(parsed, pkg, owners, cache))
    return sorted(out, key=lambda f: (f.path, f.line, f.message))


def _allow_key(finding: Finding) -> str:
    return finding.message.split(": ", 1)[0]


def assert_cross_package_private_names(
    packages: Mapping[str, PackageSpec],
    *,
    include_tests: bool = False,
    allowed: Optional[Mapping[str, str]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any reach not in *allowed* (``{"A->B:M._name": reason}``), on an ambiguous module name, on an allowed entry without a
    reason and on an allowed entry that no longer occurs (delete it: the list may only shrink)."""
    found = find_cross_package_private_names(packages, include_tests=include_tests, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    allow = dict(allowed or {})
    problems: list[str] = []
    reasonless = sorted(k for k, why in allow.items() if not str(why).strip())
    if reasonless:
        problems.append("allowed entries without a reason (say why the reach is acceptable):\n  " + "\n  ".join(reasonless))
    seen_keys = {_allow_key(f) for f in found if f.rule == RULE}
    new = [f for f in found if f.rule != RULE or _allow_key(f) not in allow]
    stale = sorted(k for k in allow if k not in seen_keys)
    if new:
        problems.append(
            f"{len(new)} cross-package private name finding(s); export a public name from the owning package and use it, "
            "or allow the reach with a reason:\n  " + "\n  ".join(f.render() for f in new)
        )
    if stale:
        problems.append("allowed entries that no longer occur (delete them):\n  " + "\n  ".join(stale))
    if problems:
        raise AssertionError("\n".join(problems))
