"""Fingerprinting for :mod:`py_ci_shared.mutation_teeth`, so an unchanged check is not re-run.

Split out of ``mutation_teeth`` so each part stays readable; everything here is re-exported from there.
"""

from __future__ import annotations

import ast
import configparser
import fnmatch
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import SourceError, parse_file

#: pytest's own default for ``python_files``.
_DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")


def _add_package_parents(path: Path, repo_root: Path, seen: set[Path]) -> None:
    """Every ``__init__.py`` between *path* and *repo_root*: importing a module executes each of them."""
    parent = path.parent
    while parent != parent.parent and parent.is_relative_to(repo_root) and parent != repo_root:
        init = parent / "__init__.py"
        if init.is_file() and init != path:
            _first_party_imports(init, repo_root, seen)
        parent = parent.parent


def _first_party_imports(path: Path, repo_root: Path, seen: set[Path]) -> set[Path]:
    """Every repo-local module reachable from *path* by static import, transitively.

    Package ``__init__.py`` files above each reached module are included, because importing
    ``pkg.mod`` runs ``pkg/__init__.py`` first and an edit there changes what the tests see.
    """
    path = path.resolve()
    repo_root = repo_root.resolve()
    if path in seen or not path.is_file():
        return seen
    seen.add(path)
    _add_package_parents(path, repo_root, seen)
    try:
        tree = parse_file(path)
    except SourceError:
        # The file itself still counts. Dropping it silently removed it AND everything it imports
        # from the fingerprint, so the very edit that fixes a syntax error would not invalidate the
        # cached verdict that was measured without it.
        return seen
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
        elif isinstance(node, ast.ImportFrom) and node.level:
            base = path.parent
            for _ in range(node.level - 1):
                base = base.parent
            # `from . import mod` puts the MODULE in node.names; `from .mod import func` puts the
            # FUNCTION there and the module in node.module. Probing only node.names resolved the
            # first form and silently dropped the second -- which is the dominant style inside a
            # package, so `user_prompt.py`'s closure omitted all five siblings it actually calls
            # (experience, formatting, smiley, truncation, word_count). A cached verdict then
            # survived any edit to them, which is exactly the hole this closure exists to close.
            if node.module:
                part = base.joinpath(*node.module.split("."))
                for candidate in (part.with_suffix(".py"), part / "__init__.py"):
                    if candidate.is_file():
                        _first_party_imports(candidate, repo_root, seen)
            for alias in node.names:
                for candidate in (base / f"{alias.name}.py", base / alias.name / "__init__.py"):
                    if candidate.is_file():
                        _first_party_imports(candidate, repo_root, seen)
            if node.module:
                names.append(node.module)
    for name in names:
        parts = name.split(".")
        # `src/` is probed as well as the root. A src-layout package installed editable imports as
        # `pkg.mod` while living at `src/pkg/mod.py`, so probing the root alone found nothing and
        # the closure silently collapsed to the target file -- which is the state this very package
        # would be fingerprinted in.
        for base in (repo_root, repo_root / "src"):
            for candidate in (base.joinpath(*parts).with_suffix(".py"), base.joinpath(*parts, "__init__.py")):
                if candidate.is_file():
                    _first_party_imports(candidate, repo_root, seen)
    return seen


def _split_patterns(value: object) -> list[str]:
    if isinstance(value, str):
        return value.split()
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _python_files_patterns(repo_root: Path) -> tuple[str, ...]:
    """The repo's ``python_files`` setting, read from the config file pytest itself would use.

    pytest takes the first of ``pytest.ini``, ``pyproject.toml`` (``[tool.pytest.ini_options]``),
    ``tox.ini`` (``[pytest]``) and ``setup.cfg`` (``[tool:pytest]``) that carries a pytest section.
    """
    ini = repo_root / "pytest.ini"
    if ini.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(ini, encoding="utf-8")
        except configparser.Error:
            return _DEFAULT_PYTHON_FILES
        return tuple(_split_patterns(parser.get("pytest", "python_files", fallback=""))) or _DEFAULT_PYTHON_FILES
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        from ._toml_compat import tomllib

        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            data = {}
        section = data.get("tool", {}).get("pytest", {}).get("ini_options")
        if isinstance(section, dict):
            return tuple(_split_patterns(section.get("python_files"))) or _DEFAULT_PYTHON_FILES
    for name, section_name in (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        cfg = repo_root / name
        if not cfg.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(cfg, encoding="utf-8")
        except configparser.Error:
            continue
        if parser.has_section(section_name):
            return tuple(_split_patterns(parser.get(section_name, "python_files", fallback=""))) or _DEFAULT_PYTHON_FILES
    return _DEFAULT_PYTHON_FILES


def _test_file_of(test: Union[Path, str]) -> str:
    """The file part of a pytest argument: ``tests/x.py::TestC::test_f`` -> ``tests/x.py``."""
    return str(test).split("::", 1)[0]


def fingerprint(
    repo_root: Union[Path, str],
    target: Union[Path, str],
    test_paths: Sequence[Union[Path, str]],
    extra_fingerprint_paths: Sequence[Union[Path, str]] = (),
    scope: object = None,
) -> str:
    """A digest of everything that could change this check's answer -- as far as static analysis sees.

    Covers the target module's TRANSITIVE first-party import closure (with the package
    ``__init__.py`` files each module's import runs), every test file (a node id counts as its
    file; a directory as every file matching the repo's ``python_files``), every ``conftest.py``
    between the repo root and each test file, anything named in *extra_fingerprint_paths*, and
    :data:`HARNESS_VERSION`.

    The import closure is the important part and the reason a naive "hash the file and the tests"
    cache is wrong: the function under test can be untouched while a function it CALLS is not.

    WHAT IT STILL CANNOT SEE, stated rather than papered over:

    * **Data files.** A prompt in a ``.txt``, a ``config.toml``, a fixture JSON -- changing one
      changes behaviour without touching a single ``.py``. This is not hypothetical in the repo this
      was written for, where the system prompt is a text file. Pass them in
      *extra_fingerprint_paths*.
    * **Dynamic imports.** ``importlib.import_module(name)``, plugin registries, entry points.
    * **Third-party versions.** An upgraded dependency changes behaviour with no local diff.
    * **The environment.** Environment variables, the clock, the database.

    So a cache hit means "nothing statically reachable changed", not "the answer is certainly the
    same". Deleting the cache file is always a valid reset, and the caller can pass
    ``use_cache=False``.
    """
    # Looked up through the public module, so a test (or a consumer) that re-binds
    # `mutation_teeth.HARNESS_VERSION` changes the key as it did before the split.
    from . import mutation_teeth

    repo_root = Path(repo_root).resolve()
    files = _first_party_imports(repo_root / target, repo_root, set())
    patterns: Optional[tuple[str, ...]] = None
    for test in test_paths:
        test_path = (repo_root / _test_file_of(test)).resolve()
        # A directory is a legal pytest target and `read_bytes()` on one raises, which the digest
        # loop swallowed into a constant -- leaving the fingerprint blind to every test file under
        # it, permanently. Expand it to the files pytest would actually collect.
        if test_path.is_dir():
            if patterns is None:
                patterns = _python_files_patterns(repo_root)
            files.update(p for p in test_path.rglob("*.py") if any(fnmatch.fnmatch(p.name, pat) for pat in patterns))
            files.update(test_path.rglob("conftest.py"))
        else:
            files.add(test_path)
        parent = test_path.parent
        while parent.is_relative_to(repo_root):
            conftest = parent / "conftest.py"
            if conftest.is_file():
                files.add(conftest)
            if parent == repo_root or parent == parent.parent:
                break
            parent = parent.parent
    for extra in extra_fingerprint_paths:
        files.add((repo_root / extra).resolve())

    digest = hashlib.sha256(str(mutation_teeth.HARNESS_VERSION).encode())
    # The machinery the answer was measured on. A Python upgrade or an installed/removed pytest
    # plugin changes what the tests do without touching a byte of the repo, and a replay under
    # different machinery is a verdict about a run that never happened. Cheap to include, and it
    # converts a whole class of silent staleness into a cache miss.
    digest.update(sys.version.encode("utf-8"))
    digest.update(repr(sorted(_installed_pytest_plugins())).encode("utf-8"))
    # The SCOPE of a run is part of its answer, not part of its inputs. A sweep narrowed by
    # `lines=` or capped by `limit=` examines a SUBSET of the mutants, and its verdict is only
    # about that subset -- so replaying it for a wider scope reports "no survivors" about mutants
    # that were never run. `lines` in particular moves on its own: it comes from `git diff`, which
    # changes across an amend, a rebase or a branch switch while every file byte stays identical.
    digest.update(repr(scope).encode("utf-8"))
    for file in sorted(files):
        try:
            body = file.read_bytes()
        except OSError:
            # Distinct per path: one constant for every unreadable file makes two different
            # mistakes (a typo'd extra_fingerprint_paths, a deleted module) digest identically.
            body = b"<unreadable:" + str(file).encode("utf-8", "replace") + b">"
        try:
            name = file.relative_to(repo_root).as_posix()
        except ValueError:
            name = file.name
        digest.update(name.encode("utf-8"))
        digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


#: Memoised for the life of the process. `importlib.metadata.distributions()` walks every installed
#: package and parses its metadata with the email parser -- measured at 0.8s and 825,000 `readline`
#: calls. `fingerprint` calls this twice per sweep, and it is the ENTIRE cost of a cache hit, so the
#: uncached version made the common case (nothing changed, replay the answer) cost most of a second
#: for no reason. The installed set cannot change while the process runs.
_PLUGIN_CACHE: Optional[list[str]] = None


def _installed_pytest_plugins() -> list[str]:
    """Names and versions of installed pytest plugins, for the fingerprint.

    Read from installed distribution metadata rather than by starting pytest: this runs on every
    fingerprint, including cache hits, and must not cost a process.
    """
    global _PLUGIN_CACHE
    if _PLUGIN_CACHE is not None:
        return _PLUGIN_CACHE
    found: list[str] = []
    try:
        from importlib.metadata import distributions
    except ImportError:  # pragma: no cover - stdlib since 3.8
        return found
    for dist in distributions():
        try:
            name = dist.metadata["Name"] or ""
        except (KeyError, TypeError):  # pragma: no cover - broken metadata in the wild
            continue
        if name.startswith("pytest") or name.startswith("pytest-"):
            found.append(f"{name}=={dist.version}")
    _PLUGIN_CACHE = found
    return found
