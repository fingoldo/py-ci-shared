"""Private helpers of :mod:`py_ci_shared.optional_imports_guarded` and :mod:`py_ci_shared.ci_install_covers_entry_imports`.

What runs when a module is imported (its module-level imports, minus the guarded ones), which first-party files that pulls in,
what the project declares (``[project.dependencies]`` and each extra) and which distribution an import name belongs to.
"""

from __future__ import annotations

import ast
import functools
import re
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional, Union

from ._ci_install_parts import Provided, Repo, add_project, catches_import_error, is_stdlib, is_type_checking, norm
from ._core import UnparsedFilesError
from .ci_install_covers_conftest import _ALTERNATIVES, BUILTIN_ALIASES

ALLOW = re.compile(r"#\s*optional-import-ok:\s*\S")


class ModuleImport(NamedTuple):
    """One import statement name that runs at module level: ``from ..a import b, c`` is ``(line, "a", 2, ("b", "c"))``."""

    line: int
    module: str
    level: int
    names: tuple[str, ...]


# --------------------------------------------------------------------------------------------------------------------
# module-level imports


def _is_flag_test(test: ast.expr, flags: frozenset[str]) -> bool:
    if is_type_checking(test):
        return True
    for node in ast.walk(test):
        if isinstance(node, ast.Name) and (node.id in flags or node.id == "TYPE_CHECKING"):
            return True
        if isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING":
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "find_spec") or (isinstance(func, ast.Name) and func.id == "find_spec"):
                return True
    return False


def _is_try(node: ast.AST) -> bool:
    return isinstance(node, ast.Try) or type(node).__name__ == "TryStar"


def _import_flags(tree: ast.Module) -> frozenset[str]:
    """Module-level names assigned inside a ``try`` that catches ImportError: availability flags such as ``_HAS_CATBOOST``."""
    flags: set[str] = set()
    for node in tree.body:
        if _is_try(node) and catches_import_error(node):  # type: ignore[arg-type]
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    flags.update(t.id for t in sub.targets if isinstance(t, ast.Name))
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    flags.add(sub.target.id)
    return frozenset(flags)


def _suppresses_import_error(node: Union[ast.With, ast.AsyncWith]) -> bool:
    for item in node.items:
        call = item.context_expr
        if isinstance(call, ast.Call) and ast.unparse(call.func).split(".")[-1] == "suppress":
            if any(ast.unparse(a).split(".")[-1] in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException") for a in call.args):
                return True
    return False


def _importorskip_names(node: ast.stmt) -> list[str]:
    """``pytest.importorskip("x")`` as a statement or as ``x = pytest.importorskip("x")``: the file is skipped when ``x`` is missing,
    so a later module-level import of ``x`` cannot fail."""
    value = node.value if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
    if not isinstance(value, ast.Call) or not value.args:
        return []
    func = value.func
    called = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    first = value.args[0]
    if called != "importorskip" or not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return []
    return [first.value.split(".")[0]]


class _Collector:
    """Walks a module body keeping whether an enclosing construct guards each import."""

    def __init__(self, tree: ast.Module, lines: Sequence[str]) -> None:
        self.flags = _import_flags(tree)
        self.lines = lines
        self.found: list[ModuleImport] = []
        self.skipped: set[str] = set()  # top-level names a module-level ``pytest.importorskip`` has already vouched for

    def visit(self, body: Sequence[ast.stmt], guarded: bool) -> None:
        for node in body:
            self.node(node, guarded)

    def node(self, node: ast.stmt, guarded: bool) -> None:
        self.skipped.update(_importorskip_names(node))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self.record(node, guarded)
        elif _is_try(node):
            self.try_(node, guarded)
        elif isinstance(node, ast.If):
            self.visit(node.body, guarded or _is_flag_test(node.test, self.flags))
            self.visit(node.orelse, guarded)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            self.visit(node.body, guarded or _suppresses_import_error(node))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self.visit([*node.body, *node.orelse], guarded)
        elif isinstance(node, ast.ClassDef):
            self.visit(node.body, guarded)

    def record(self, node: Union[ast.Import, ast.ImportFrom], guarded: bool) -> None:
        span = self.lines[node.lineno - 1 : getattr(node, "end_lineno", node.lineno) or node.lineno]
        if guarded or any(ALLOW.search(line) for line in span):
            return
        if isinstance(node, ast.ImportFrom):
            imports = [ModuleImport(node.lineno, node.module or "", node.level, tuple(a.name for a in node.names))]
        else:
            imports = [ModuleImport(node.lineno, a.name, 0, ()) for a in node.names]
        self.found.extend(i for i in imports if i.level or i.module.split(".")[0] not in self.skipped)

    def try_(self, node: ast.stmt, guarded: bool) -> None:
        caught = catches_import_error(node)  # type: ignore[arg-type]
        self.visit(node.body, guarded or caught)  # type: ignore[attr-defined]
        for handler in node.handlers:  # type: ignore[attr-defined]
            self.visit(handler.body, guarded)
        self.visit(node.orelse, guarded)  # type: ignore[attr-defined]
        self.visit(node.finalbody, guarded)  # type: ignore[attr-defined]


def module_level_imports(tree: ast.Module, lines: Sequence[str]) -> list[ModuleImport]:
    """Every import that runs when the module is imported and that no guard covers.

    Guards: a ``try`` whose handlers catch ``ImportError`` (``Exception``, bare ``except`` included), ``if TYPE_CHECKING``,
    ``if <flag>`` where the flag is assigned in such a ``try``, a ``find_spec`` test, ``with contextlib.suppress(ImportError)``,
    a function body (never visited) and a ``# optional-import-ok: <reason>`` comment on the statement.
    """
    collector = _Collector(tree, lines)
    collector.visit(tree.body, False)
    return collector.found


# --------------------------------------------------------------------------------------------------------------------
# what the project declares, and which distribution an import is


@dataclass(frozen=True)
class Declared:
    core: frozenset[str]
    extras: dict[str, frozenset[str]]  # extra name -> distributions it adds beyond the core ones
    project: str
    problems: tuple[str, ...]

    @functools.cached_property
    def every_extra(self) -> frozenset[str]:
        return frozenset().union(*self.extras.values()) if self.extras else frozenset()

    @functools.cached_property
    def known(self) -> tuple[str, ...]:
        """Every declared distribution, sorted: the key of the interpreter-metadata lookup."""
        return tuple(sorted(self.core | self.every_extra))

    def extras_of(self, dist: str) -> list[str]:
        return sorted(name for name, dists in self.extras.items() if dist in dists)


def read_declared(root: Path, pyproject: Path) -> Declared:
    """The project's own dependencies and each extra, read through the same reader the CI-install gates use."""
    repo = Repo(root.resolve())
    data = repo.toml(pyproject.resolve()) or {}
    if repo.problems or not data:
        what = "; ".join(p.render() for p in repo.problems) or f"{pyproject} is missing or empty"
        raise UnparsedFilesError(f"cannot read the project's dependencies, so no import can be judged: {what}")
    raw_project = data.get("project")
    project: dict[str, Any] = raw_project if isinstance(raw_project, dict) else {}
    directory = pyproject.resolve().parent
    names: list[str] = [str(n) for n in (project.get("optional-dependencies") or {})]
    tool = data.get("tool")
    setuptools = tool.get("setuptools") if isinstance(tool, dict) else None
    dynamic = setuptools.get("dynamic") if isinstance(setuptools, dict) else None
    dynamic_extras = dynamic.get("optional-dependencies") if isinstance(dynamic, dict) else None
    names += [str(n) for n in (dynamic_extras if isinstance(dynamic_extras, dict) else {})]
    base = Provided()
    if not add_project(repo, directory, [], base):
        raise UnparsedFilesError(f"{pyproject} has no [project] table with a name, so its dependencies cannot be read")
    own = norm(str(project.get("name")))
    core = frozenset(set(base.dists) - {own})
    extras: dict[str, frozenset[str]] = {}
    problems = list(base.unresolved)
    for name in dict.fromkeys(names):
        provided = Provided()
        add_project(repo, directory, [name], provided)
        problems.extend(provided.unresolved)
        extras[name] = frozenset(set(provided.dists) - core - {own})
    return Declared(core, extras, own, tuple(dict.fromkeys(problems)))


#: import name -> distribution for packages whose two names differ and that ``BUILTIN_ALIASES`` of the conftest gate does not list.
MORE_ALIASES: dict[str, str] = {
    "Bio": "biopython",
    "Crypto": "pycryptodome",
    "docx": "python-docx",
    "git": "gitpython",
    "imblearn": "imbalanced-learn",
    "iterstrat": "iterative-stratification",
    "mpl_toolkits": "matplotlib",
    "nacl": "pynacl",
    "pptx": "python-pptx",
    "pywt": "pywavelets",
    "ruamel": "ruamel-yaml",
    "sksurv": "scikit-survival",
    "umap": "umap-learn",
    "usb": "pyusb",
    "wx": "wxpython",
}


def _table_hit(module: str, name_map: Mapping[str, Any]) -> Optional[list[str]]:
    dotted = module.split(".")
    keys = [".".join(dotted[:depth]) for depth in range(len(dotted), 0, -1)]
    for table in (name_map, BUILTIN_ALIASES, MORE_ALIASES):
        hit = next((table[k] for k in keys if k in table), None)
        if hit is not None:
            named = [hit] if isinstance(hit, str) else list(hit)
            out: list[str] = []
            for name in named:
                out.extend(norm(alt) for alt in _ALTERNATIVES.get(norm(name), (name,)) if norm(alt) not in out)
            return out
    return None


@functools.lru_cache(maxsize=8)
def installed_top_levels(dists: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Import name -> the declared distributions among *dists* that provide it, from the running interpreter's metadata.

    Only the declared distributions are read (``importlib.metadata.packages_distributions`` walks every installed one and takes
    half a minute on a large environment); it is the last resort after the name map, the built-in table and the normalised
    name, because the verdict must not depend on what happens to be installed where the gate runs."""
    from importlib import metadata

    out: dict[str, set[str]] = {}
    for name in dists:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        listed = (str(f) for f in (dist.files or []))
        tops = set((dist.read_text("top_level.txt") or "").split()) or {
            re.split(r"[\\/]", f)[0].removesuffix(".py") for f in listed if re.search(r"[\\/]", f) or f.endswith(".py")
        }
        for top in tops:
            out.setdefault(top, set()).add(name)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def distributions(module: str, declared: Declared, name_map: Mapping[str, Any]) -> list[str]:
    """The candidate distributions of an import: the name map, the built-in table, the normalised name, then the metadata
    of the declared distributions installed in this interpreter."""
    named = _table_hit(module, name_map)
    if named is not None:
        return named
    top = module.split(".")[0]
    candidates = [norm(top)]
    if candidates[0] not in declared.core and candidates[0] not in declared.every_extra:
        candidates.extend(d for d in installed_top_levels(declared.known).get(top, ()) if d not in candidates)
    return candidates


# --------------------------------------------------------------------------------------------------------------------
# first-party modules


class ThirdPartyImport(NamedTuple):
    path: Path
    line: int
    module: str


class ModuleIndex:
    """Where first-party modules live: each root is a directory that ``sys.path`` can start from."""

    def __init__(self, roots: Iterable[Path]) -> None:
        self.roots = [r for r in dict.fromkeys(Path(r).resolve() for r in roots) if r.is_dir()]
        self._first_party: dict[str, bool] = {}
        self._resolved: dict[str, list[Path]] = {}

    def is_first_party(self, top: str) -> bool:
        if top not in self._first_party:
            self._first_party[top] = any((root / f"{top}.py").is_file() or (root / top).is_dir() for root in self.roots)
        return self._first_party[top]

    def resolve(self, dotted: str) -> list[Path]:
        """The files that run when *dotted* is imported: each parent package's ``__init__`` and the module itself."""
        if dotted not in self._resolved:
            self._resolved[dotted] = self._locate(dotted)
        return list(self._resolved[dotted])

    def _locate(self, dotted: str) -> list[Path]:
        parts = dotted.split(".")
        files: list[Path] = []
        for root in self.roots:
            if not ((root / f"{parts[0]}.py").is_file() or (root / parts[0]).is_dir()):
                continue
            current = root
            for part in parts:
                package = current / part
                module = current / f"{part}.py"
                if package.is_dir():
                    init = package / "__init__.py"
                    if init.is_file():
                        files.append(init)
                    current = package
                elif module.is_file():
                    files.append(module)
                    break
                else:
                    break
            break
        return files

    def dotted_of(self, path: Path) -> Optional[tuple[str, bool]]:
        """``(dotted name, is a package)`` of a file under a root; None outside every root."""
        resolved = path.resolve()
        for root in self.roots:
            try:
                rel = resolved.relative_to(root)
            except ValueError:
                continue
            parts = list(rel.with_suffix("").parts)
            is_package = bool(parts) and parts[-1] == "__init__"
            if is_package:
                parts.pop()
            return ".".join(parts), is_package
        return None

    def absolute_targets(self, current: Path, imp: ModuleImport) -> list[str]:
        """The dotted names an import statement loads: the module and, for ``from m import x``, ``m.x`` when that names a module."""
        base = imp.module
        if imp.level:
            located = self.dotted_of(current)
            if located is None:
                return []
            dotted, is_package = located
            package = [p for p in (dotted.split(".") if is_package else dotted.split(".")[:-1]) if p]
            keep = len(package) - (imp.level - 1)
            if keep < 0:
                return []
            package = package[:keep]
            base = ".".join([*package, *([imp.module] if imp.module else [])])
        if not base:
            return []
        return [base, *(f"{base}.{n}" for n in imp.names if n != "*")]


def is_sibling(path: Path, top: str) -> bool:
    """*top* is a module or package next to *path*, which is a script: its directory (no ``__init__.py``) is on ``sys.path``."""
    folder = path.parent
    return not (folder / "__init__.py").is_file() and ((folder / f"{top}.py").is_file() or (folder / top).is_dir())


def _third_party(index: ModuleIndex, current: Path, imp: ModuleImport, top: str) -> bool:
    """Is the import a third-party package (not stdlib, not first-party, not a sibling script module)?"""
    if imp.level or is_sibling(current, top) or index.is_first_party(top):
        return False
    return top != "__future__" and not is_stdlib(top)


def _followed(index: ModuleIndex, current: Path, targets: Sequence[str], top: str, level: int) -> list[Path]:
    found: list[Path] = []
    for target in targets:
        found.extend(index.resolve(target))
    if level == 0 and is_sibling(current, top):
        found.extend(p for p in (current.parent / f"{top}.py", current.parent / top / "__init__.py") if p.is_file())
    return found


def reach(
    index: ModuleIndex,
    starts: Iterable[Path],
    imports_of: Callable[[Path], Sequence[ModuleImport]],
) -> tuple[dict[Path, Optional[Path]], list[ThirdPartyImport]]:
    """Walk the first-party files that *starts* import at module level.

    *imports_of* maps a file to its :class:`ModuleImport` list (raising on a file it cannot read). Returns ``(parents, third
    party imports)``: ``parents[file]`` is the file that first imported it (None for a start), for the chain in a message.
    """
    parents: dict[Path, Optional[Path]] = {}
    queue: deque[Path] = deque()
    for start in starts:
        resolved = start.resolve()
        if resolved not in parents:
            parents[resolved] = None
            queue.append(resolved)
    third: list[ThirdPartyImport] = []
    while queue:
        current = queue.popleft()
        for imp in imports_of(current):
            targets = index.absolute_targets(current, imp)
            if not targets:
                continue
            top = targets[0].split(".")[0]
            if _third_party(index, current, imp, top):
                third.append(ThirdPartyImport(current, imp.line, imp.module))
                continue
            for nxt in _followed(index, current, targets, top, imp.level):
                resolved = nxt.resolve()
                if resolved not in parents:
                    parents[resolved] = current
                    queue.append(resolved)
    return parents, third


def chain(parents: Mapping[Path, Optional[Path]], leaf: Path, index: ModuleIndex) -> str:
    """``a -> b -> c``: how the entry reaches *leaf*, as dotted module names."""
    names: list[str] = []
    cursor: Optional[Path] = leaf.resolve()
    while cursor is not None and len(names) < 12:
        located = index.dotted_of(cursor)
        names.append(located[0] if located and located[0] else cursor.name)
        cursor = parents.get(cursor)
    return " -> ".join(reversed(names))
