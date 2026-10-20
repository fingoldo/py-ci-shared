"""Standard-library API newer than the ``requires-python`` floor, outside a version guard (vermin, guard-aware).

pyutilz declares ``requires-python = ">=3.8"`` and its 3.8/3.9 CI legs went red after merges six times in 30 days:
14dcfc5 (``Path.write_text(newline=)``, 3.10), d660504 (``hashlib.md5(usedforsecurity=)`` and ``ast.unparse``, 3.9),
24486a0, 3e8f2bc, ee24e49, 00aef09. Each cost a CI round on the matrix, after the push. Nothing checked API level
statically.

The gate runs `vermin <https://github.com/netromdk/vermin>`_ with ``--target=<floor>- --violations
--no-parse-comments --backport typing_extensions`` over the parsed files (no ``--eval-annotations``: with it every
``list[...]`` under ``from __future__ import annotations`` is reported, 465 hits on pyutilz), then drops each hit that
sits behind a version guard:

* inside an ``if``/``elif``/conditional expression/``while`` whose test reads ``sys.version_info`` or calls
  ``hasattr``/``getattr`` (either branch: ``if sys.version_info < (3, 9): fallback else: new_api`` is guarded too),
  or ``TYPE_CHECKING``;
* the right-hand side of an ``and`` whose earlier operand is such a test (``hasattr(ast, "unparse") and ast.unparse(x)``);
* inside a ``try`` body whose handlers catch ``ImportError``, ``ModuleNotFoundError``, ``AttributeError``,
  ``TypeError`` or ``Exception``.

vermin is an optional dependency (``pip install py-ci-shared[api]``). When it is not importable the gate does NOT pass:
it reports one ``api-floor-unavailable`` finding saying so. vermin does not know every API (it misses ``ast.Match``),
so the gate shortens the CI round trip for what it knows; the version matrix stays the backstop.

Usage in a consumer's meta test::

    from py_ci_shared.api_floor import assert_api_floor

    def test_stdlib_api_fits_requires_python():
        assert_api_floor(REPO_ROOT / "src", pyproject=REPO_ROOT / "pyproject.toml")
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, CorpusError, Finding, ParsedFile, nodes_of, read_source, scan_python
from ._core import walk as shared_walk
from ._toml_compat import tomllib

logger = logging.getLogger(__name__)

__all__ = ["RULE", "RULE_UNAVAILABLE", "assert_api_floor", "find_api_floor", "guarded_lines", "parse_vermin_output", "target_from_pyproject"]

RULE = "api-floor"
RULE_UNAVAILABLE = "api-floor-unavailable"

PathLike = Union[str, Path]
_GUARD_EXCEPTIONS = frozenset({"ImportError", "ModuleNotFoundError", "AttributeError", "TypeError", "Exception", "BaseException"})
_GUARD_CALLS = frozenset({"hasattr", "getattr"})
# Features vermin names by method alone, which an older type has under the same name: ``x.is_integer()`` is read as
# ``int.is_integer`` (3.12) though ``float.is_integer`` is in every Python 3 (glossum ``parse_answer.py:107``).
_AMBIGUOUS_FEATURES = frozenset({"'int.is_integer' member"})
_CMDLINE_BUDGET = 24000  # characters of file paths per vermin run; Windows caps a command line at 32767
_VERMIN = "import sys; from vermin.main import main; sys.argv[0] = 'vermin'; sys.exit(main())"


def target_from_pyproject(pyproject: PathLike) -> str:
    """``3.8`` from ``requires-python = ">=3.8"``; ``CorpusError`` when there is no lower bound to check against."""
    data = tomllib.loads(read_source(Path(pyproject)))
    spec = str((data.get("project", {}) or {}).get("requires-python", "") or "")
    m = re.search(r">=\s*(\d+)\.(\d+)", spec) or re.search(r"~=\s*(\d+)\.(\d+)", spec)
    if not m:
        raise CorpusError(f"{pyproject}: requires-python {spec!r} has no >= lower bound; pass target= explicitly")
    return f"{m.group(1)}.{m.group(2)}"


def _is_version_test(test: ast.AST) -> bool:
    for node in shared_walk(test):
        if isinstance(node, ast.Attribute) and node.attr in ("version_info", "TYPE_CHECKING"):
            return True
        if isinstance(node, ast.Name) and node.id == "TYPE_CHECKING":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _GUARD_CALLS:
            return True
    return False


def _span(node: ast.AST) -> range:
    start = getattr(node, "lineno", 0)
    return range(start, (getattr(node, "end_lineno", None) or start) + 1)


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {t.attr if isinstance(t, ast.Attribute) else t.id if isinstance(t, ast.Name) else "" for t in types}


def _guarded_spans(tree: ast.Module) -> Iterator[range]:
    for node in nodes_of(tree, ast.If, ast.IfExp, ast.While):
        if _is_version_test(getattr(node, "test")):
            yield _span(node)
    for node in nodes_of(tree, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for i, value in enumerate(node.values[:-1]):
                if _is_version_test(value):
                    for later in node.values[i + 1 :]:
                        yield _span(later)
    yield from _try_spans(tree)


def _try_spans(tree: ast.Module) -> Iterator[range]:
    try_types: tuple[type, ...] = (ast.Try,) + ((getattr(ast, "TryStar"),) if hasattr(ast, "TryStar") else ())
    for node in nodes_of(tree, *try_types):
        if any(_handler_names(h) & _GUARD_EXCEPTIONS for h in getattr(node, "handlers")):
            for stmt in getattr(node, "body"):
                yield _span(stmt)


def guarded_lines(tree: ast.Module) -> set[int]:
    """Every line of *tree* that sits behind a version guard (see the module doc)."""
    return {line for span in _guarded_spans(tree) for line in span}


def parse_vermin_output(text: str) -> list[tuple[str, int, str, str]]:
    """``(path, line, minimum, feature)`` from vermin's ``--format parsable`` lines; summary lines are skipped."""
    out = []
    for raw in text.splitlines():
        parts = raw.rsplit(":", 5)
        if len(parts) != 6 or not parts[0] or not parts[1].isdigit():
            continue
        path, line, _col, _py2, py3, feature = parts
        if py3.startswith("!"):
            continue  # "!3": vermin read a Python 2 builtin (``tensor.long()`` as ``long``); there is no 3.x floor to break
        if feature.strip() in _AMBIGUOUS_FEATURES:
            continue
        out.append((path, int(line), py3, feature.strip()))
    return out


def _batches(paths: Sequence[str]) -> Iterator[list[str]]:
    batch: list[str] = []
    size = 0
    for p in paths:
        if batch and size + len(p) + 1 > _CMDLINE_BUDGET:
            yield batch
            batch, size = [], 0
        batch.append(p)
        size += len(p) + 1
    if batch:
        yield batch


def _vermin_available() -> bool:
    try:
        import vermin  # noqa: F401
    except ImportError as exc:
        logger.debug("vermin is not installed, floor check skipped: %s", type(exc).__name__)
        return False
    return True


def _vermin_args(target: str) -> list[str]:
    return ["-t=" + target + "-", "--violations", "--no-parse-comments", "--backport", "typing_extensions", "--format", "parsable"]


def _vermin_version() -> str:
    try:
        return importlib.metadata.version("vermin")
    except importlib.metadata.PackageNotFoundError:  # best-effort: the version only keys the cache, so an unknown one costs a re-analysis, never a wrong verdict
        return "unknown"


def _result_key(data: bytes, target: str, version: str) -> str:
    """What vermin's output for one file depends on: its bytes, the floor, vermin's version and the flags this module passes."""
    digest = hashlib.sha256()
    for part in (target, version, " ".join(_vermin_args(target)), _VERMIN):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    digest.update(data)
    return digest.hexdigest()


def _result_dir() -> Path:
    """Per-user cache of vermin results, under the home directory (not the shared temp directory: another user must not plant entries)."""
    path = Path.home() / ".cache" / "py_ci_shared" / "vermin"
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
    return path


def _load_result(key: str) -> Optional[list[str]]:
    try:
        with open(_result_dir() / f"{key}.json", encoding="utf-8") as handle:
            lines = json.load(handle)
    except (OSError, ValueError):  # swallow-ok: an unreadable entry is a miss; the file is analysed again  # best-effort: a miss only costs a re-analysis
        return None
    return lines if isinstance(lines, list) and all(isinstance(line, str) for line in lines) else None


def _store_result(key: str, lines: list[str]) -> None:
    """Keep each line without its path: the key is the file's bytes, so another file with the same bytes must get its own path."""
    tails = [":".join(line.rsplit(":", 5)[1:]) for line in lines]
    try:
        target = _result_dir() / f"{key}.json"
        staged = target.with_suffix(".tmp")
        with open(staged, "w", encoding="utf-8") as handle:
            json.dump(tails, handle)
        os.replace(staged, target)
    except OSError as exc:
        logger.warning("vermin result cache not written (%s); the next run analyses these files again", exc)


def _path_of(line: str) -> str:
    """The file path of one parsable vermin line (the path may hold ':' on Windows, so split from the right)."""
    return line.rsplit(":", 5)[0]


def _run_vermin_raw(paths: Sequence[str], target: str) -> tuple[dict[str, list[str]], bool]:
    """vermin's parsable violation lines for each of *paths* (an empty list for a file with none), and whether every
    output line named one of *paths*. When one did not, the per-file split cannot be trusted, so nothing is cached."""
    by_path: dict[str, list[str]] = {os.path.normcase(os.path.normpath(p)): [] for p in paths}
    complete = True
    for batch in _batches(paths):
        proc = subprocess.run(
            [sys.executable, "-c", _VERMIN, *_vermin_args(target), *batch], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=1800
        )
        if proc.returncode not in (0, 1):  # 1 = violations found
            raise CorpusError(f"vermin exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:500]}")
        for line in proc.stdout.splitlines():
            if len(line.rsplit(":", 5)) != 6 or not _path_of(line):
                continue  # a summary line (":::!2:3.12:" names no file)
            line_key = os.path.normcase(os.path.normpath(_path_of(line)))
            if line_key not in by_path:
                complete = False
            by_path.setdefault(line_key, []).append(line)
    return by_path, complete


def _file_key(path: str, target: str, version: str) -> Optional[str]:
    """The cache key of the file at *path*, or None (logged at WARNING) when it cannot be read."""
    try:
        return _result_key(Path(path).read_bytes(), target, version)
    except OSError as exc:
        logger.warning("could not read %s to key the vermin cache (%s); it is analysed without the cache", path, type(exc).__name__)
        return None


def _run_vermin(paths: Sequence[str], target: str) -> list[tuple[str, int, str, str]]:
    """Hits for *paths*. A file whose bytes, floor and vermin version were seen before is not analysed again."""
    version = _vermin_version()
    keys: dict[str, str] = {}
    lines_for: dict[str, list[str]] = {}
    missing: list[str] = []
    for path in paths:
        norm = os.path.normcase(os.path.normpath(path))
        key = _file_key(path, target, version)
        if key is None:  # unreadable here: vermin gets the path and reports it itself
            missing.append(path)
            continue
        keys[norm] = key
        cached = _load_result(key)
        if cached is None:
            missing.append(path)
        else:
            lines_for[norm] = [f"{path}:{tail}" for tail in cached]
    if missing:
        fresh, complete = _run_vermin_raw(missing, target)
        for norm, lines in fresh.items():
            lines_for[norm] = lines
            if complete and norm in keys:
                _store_result(keys[norm], lines)
    return parse_vermin_output("\n".join(line for lines in lines_for.values() for line in lines))


def _findings(files: Iterable[ParsedFile], hits: Iterable[tuple[str, int, str, str]], target: str) -> list[Finding]:
    by_path = {str(f.path.resolve()): f for f in files}
    guards: dict[str, set[int]] = {}
    out = []
    for path, line, minimum, feature in hits:
        parsed = by_path.get(str(Path(path).resolve()))
        if parsed is None:
            continue
        if parsed.rel not in guards:
            guards[parsed.rel] = guarded_lines(parsed.tree)
        if line in guards[parsed.rel]:
            continue
        message = f"{feature} requires Python {minimum}, above the floor {target}; guard it (sys.version_info / hasattr / try-except) or raise the floor"
        out.append(Finding(parsed.rel, line, RULE, message))
    return out


def find_api_floor(
    root: PathLike,
    *,
    pyproject: Optional[PathLike] = None,
    target: Optional[str] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Unguarded API uses newer than the floor (*target*, else ``requires-python`` of *pyproject*, else of
    ``<root>/pyproject.toml``). Without vermin: one ``api-floor-unavailable`` finding, never an empty list."""
    floor = target or target_from_pyproject(Path(pyproject) if pyproject is not None else Path(root) / "pyproject.toml")
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    if not _vermin_available():
        message = "vermin is not installed, so no API level was checked: pip install 'py-ci-shared[api]' (or vermin) in this environment"
        return [Finding("<environment>", 1, RULE_UNAVAILABLE, message)]
    files = list(scan)
    hits = _run_vermin([str(f.path.resolve()) for f in files], floor)
    return sorted(_findings(files, hits, floor), key=lambda f: (f.path, f.line, f.message))


def assert_api_floor(
    root: PathLike,
    *,
    pyproject: Optional[PathLike] = None,
    target: Optional[str] = None,
    baseline_path: Optional[PathLike] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any unguarded too-new API, or with *baseline_path* on one the (shrink-only) baseline does not accept. A
    missing vermin always fails."""
    found = find_api_floor(root, pyproject=pyproject, target=target, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    unavailable = [f for f in found if f.rule == RULE_UNAVAILABLE]
    if unavailable:
        raise AssertionError(unavailable[0].render())
    guidance = "guard the call on sys.version_info / hasattr, use the older spelling, or raise requires-python"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="api_floor", refresh_command="PY_CI_SHARED_REFRESH=api_floor")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} API use(s) newer than requires-python; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
