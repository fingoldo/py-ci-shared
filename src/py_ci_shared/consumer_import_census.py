"""A library repo checks that every name its consumers import from it still resolves in THIS checkout.

pyutilz 1.1 moved ``_RATE_LIMIT_PATTERN`` and two sibling constants into ``claude_code_cli`` without re-exporting them
from ``llm.claude_code_provider``; glossum failed at import (pyutilz 89eb1f6). ``unresolved_imports`` would have caught
it, but only inside glossum, after the library had shipped. A library's own ``_api_snapshot.json`` pins the API it
DECLARES; consumers import what they find, underscore names included. This gate measures what they really import.

For each consumer checkout it collects every absolute ``from <package>[.x] import name`` and ``import <package>.x``
and resolves them against the library checkout with :class:`py_ci_shared.unresolved_imports.ModuleIndex` (parse only,
nothing is imported). It honours:

* the library's module alias map: a module-level dict literal in ``<package>/__init__.py`` whose values are all
  dotted module names inside the package (pyutilz ``_MODULE_ALIASES = {"pythonlib": "pyutilz.core.pythonlib"}``,
  registered as ``sys.modules`` proxies), so ``from pyutilz.pythonlib import x`` is judged against
  ``pyutilz.core.pythonlib``. Pass *aliases* to override or extend it;
* a module whose names a parse cannot know (a module ``__getattr__``, ``globals()`` writes, an unresolved star
  import): its imports are not judged;
* an import guarded by ``try``/``except ImportError`` in the consumer, which expects the absence: not judged.

With *reference_root* (the library at its last release tag; the CLI's ``--since-ref`` extracts it with
``git archive``), only REGRESSIONS are reported: imports that resolved there and do not resolve now. Without it,
every unresolved import is reported.

CLI, for a library repo's CI or a scheduled job (``_consumers checkout`` writes the repos file)::

    python -m py_ci_shared.consumer_import_census --library . --package pyutilz \\
        --repos-file "$RUNNER_TEMP/consumers/repos.toml" --since-ref "$(git describe --tags --abbrev=0)"

Exit 1 on any finding, on a consumer with no parsable file, and on an unparsable consumer file (``--allow-unparsed``
reports those without failing).
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tarfile
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import CoreError, Finding, ParsedFile, ScanResult, parse_source, scan_python
from ._core.git import run_git
from .unresolved_imports import ModuleIndex, _guarded_import_ids

__all__ = [
    "RULE",
    "LibrarySurface",
    "assert_consumer_imports_resolve",
    "find_consumer_import_breaks",
    "library_alias_map",
    "main",
    "package_dir",
]

RULE = "consumer-import-break"
PathLike = Union[str, Path]


def package_dir(library_root: PathLike, package: str) -> Path:
    """``<root>/src/<package>`` or ``<root>/<package>``, whichever holds an ``__init__.py``; CoreError otherwise."""
    root = Path(library_root)
    for candidate in (root / "src" / package, root / package):
        if (candidate / "__init__.py").is_file():
            return candidate
    raise CoreError(f"no package {package!r} under {root} (looked for src/{package}/__init__.py and {package}/__init__.py)")


def library_alias_map(pkg_dir: PathLike, package: str) -> dict[str, str]:
    """``{"<package>.<alias>": "<package>.<real.module>"}`` from module-level dict literals in ``<package>/__init__.py``
    whose every key is a plain identifier and every value a dotted module name inside *package*."""
    _, tree = parse_source(Path(pkg_dir) / "__init__.py")
    out: dict[str, str] = {}
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not isinstance(value, ast.Dict) or not value.keys:
            continue
        pairs = [(k.value, v.value) for k, v in zip(value.keys, value.values) if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)]
        if len(pairs) != len(value.keys):
            continue
        if all(isinstance(k, str) and k.isidentifier() and isinstance(v, str) and v.startswith(package + ".") for k, v in pairs):
            out.update({f"{package}.{k}": str(v) for k, v in pairs if isinstance(k, str)})
    return out


class LibrarySurface:
    """The importable surface of one library checkout: its modules, their top-level names, and its alias map."""

    def __init__(self, library_root: PathLike, package: str, *, aliases: Optional[Mapping[str, str]] = None) -> None:
        self.package = package
        self.pkg_dir = package_dir(library_root, package)
        self.index = ModuleIndex([self.pkg_dir], [self.pkg_dir.parent])
        self.aliases = {**library_alias_map(self.pkg_dir, package), **dict(aliases or {})}

    def owns(self, module: str) -> bool:
        return module == self.package or module.startswith(self.package + ".")

    def canonical(self, module: str) -> str:
        """*module* with an alias prefix replaced by its real module (``pyutilz.pythonlib.x`` -> ``pyutilz.core.pythonlib.x``)."""
        for alias in sorted(self.aliases, key=len, reverse=True):
            if module == alias or module.startswith(alias + "."):
                return self.aliases[alias] + module[len(alias) :]
        return module

    def problem(self, module: str, name: Optional[str]) -> Optional[str]:
        """Why ``from module import name`` (or ``import module`` when *name* is None) does not resolve; None when it does
        or cannot be judged."""
        real = self.canonical(module)
        shown = module if real == module else f"{module} (alias of {real})"
        if not self.index.knows(real):
            return f"module {shown} does not exist"
        if name is None or self.index.is_dynamic(real) or "*" in self.index.names(real):
            return None
        if name in self.index.names(real) or self.index.knows(f"{real}.{name}"):
            return None
        return f"{shown} does not define {name!r}"


def _imports(parsed: ParsedFile, surface: LibrarySurface) -> Iterator[tuple[int, str, Optional[str]]]:
    """``(line, module, name or None)`` for every absolute import of the library in one consumer file, guards excluded."""
    guarded = _guarded_import_ids(parsed.tree)
    for node in ast.walk(parsed.tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and surface.owns(node.module):
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, node.module, alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if surface.owns(alias.name) and alias.name != surface.package:
                    yield node.lineno, alias.name, None


def _consumer_findings(
    name: str, root: Path, surface: LibrarySurface, reference: Optional[LibrarySurface], use_git: Optional[bool]
) -> "tuple[list[Finding], ScanResult]":
    scan = scan_python(root, min_files=1, use_git=use_git)
    findings: list[Finding] = []
    for parsed in scan:
        for line, module, imported in _imports(parsed, surface):
            problem = surface.problem(module, imported)
            if problem is None or (reference is not None and reference.problem(module, imported) is not None):
                continue
            what = f"from {module} import {imported}" if imported else f"import {module}"
            findings.append(Finding(f"{name}/{parsed.rel}", line, RULE, f"`{what}`: {problem}"))
    return findings, scan


def find_consumer_import_breaks(
    library_root: PathLike,
    package: str,
    consumers: Sequence[tuple[str, PathLike]],
    *,
    reference_root: Optional[PathLike] = None,
    aliases: Optional[Mapping[str, str]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every import of *package* in *consumers* (``(name, root)`` pairs) that this library checkout does not satisfy.

    A finding's path is ``<consumer name>/<path in that consumer>``. With *reference_root*, only imports that resolved
    in that checkout are reported. Raises ``EmptyScanError`` for a consumer with fewer than *min_files* parsed files
    and ``UnparsedFilesError`` for an unparsable consumer file unless *allow_unparsed* (those are then findings too).
    """
    surface = LibrarySurface(library_root, package, aliases=aliases)
    reference = LibrarySurface(reference_root, package, aliases=aliases) if reference_root is not None else None
    out: list[Finding] = []
    for name, root in consumers:
        findings, scan = _consumer_findings(name, Path(root), surface, reference, use_git)
        scan.min_files = min_files
        scan.assert_ok(allow_unparsed=allow_unparsed)
        out += findings + [Finding(f"{name}/{f.path}", f.line, f.rule, f.message) for f in scan.unparsed_findings()]
    return sorted(out, key=lambda f: (f.path, f.line, f.message))


def assert_consumer_imports_resolve(
    library_root: PathLike,
    package: str,
    consumers: Sequence[tuple[str, PathLike]],
    *,
    reference_root: Optional[PathLike] = None,
    aliases: Optional[Mapping[str, str]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail when a consumer imports a name or module of *package* this checkout no longer provides."""
    found = find_consumer_import_breaks(
        library_root, package, consumers, reference_root=reference_root, aliases=aliases, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git
    )
    if found:
        raise AssertionError(
            f"{len(found)} consumer import(s) of {package} do not resolve in this checkout; re-export the name from its old "
            "module (or keep an alias) until the consumers move:\n  " + "\n  ".join(f.render() for f in found)
        )


def _extract_ref(library_root: Path, ref: str, dest: Path) -> Path:
    """The tracked files of *library_root* at *ref*, written under *dest* with ``git archive`` (no checkout is touched)."""
    proc = run_git(library_root, "archive", "--format=tar", ref)
    if proc.returncode != 0:
        raise CoreError(f"git archive {ref} failed in {library_root}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]  # no links: nothing escapes dest
        tar.extractall(dest, members=members)  # nosec B202 - the archive is the library's own tracked tree, regular files only
    return dest


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .adoption_matrix import load_repo_list

    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.consumer_import_census", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--library", default=".", help="the library checkout (default: .)")
    parser.add_argument("--package", required=True, help="the library's import package, e.g. pyutilz")
    parser.add_argument("--repos-file", help="[[repo]] list of consumer checkouts (python -m py_ci_shared._consumers checkout writes one)")
    parser.add_argument("--consumer", action="append", default=[], help="a consumer checkout (repeatable)")
    parser.add_argument("--since-ref", help="report only imports that resolved at this library ref (e.g. the last release tag)")
    parser.add_argument("--allow-unparsed", action="store_true", help="report unparsable consumer files without failing on them")
    args = parser.parse_args(argv)
    consumers: list[tuple[str, PathLike]] = [(Path(c).resolve().name, Path(c)) for c in args.consumer]
    if args.repos_file:
        consumers += list(load_repo_list(args.repos_file))
    library = Path(args.library).resolve()
    consumers = [(n, r) for n, r in consumers if Path(r).resolve() != library]
    if not consumers:
        sys.stderr.write("consumer_import_census: no consumer checkout given (--consumer or --repos-file)" + "\n")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        try:
            reference = _extract_ref(library, args.since_ref, Path(tmp)) if args.since_ref else None
            found = find_consumer_import_breaks(library, args.package, consumers, reference_root=reference, allow_unparsed=args.allow_unparsed)
        except (CoreError, AssertionError) as exc:
            sys.stderr.write(f"consumer_import_census: {exc}" + "\n")
            return 1
    breaks = [f for f in found if f.rule == RULE]
    scope = f"resolved at {args.since_ref} and not now" if args.since_ref else "unresolved"
    sys.stdout.write(f"consumer_import_census: {len(consumers)} consumer(s) of {args.package}, {len(breaks)} import(s) {scope}" + "\n")
    for f in found:
        sys.stdout.write("  " + f.render() + "\n")
    return 1 if breaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
