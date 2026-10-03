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
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, CorpusError, Finding, ParsedFile, nodes_of, read_source, scan_python
from ._toml_compat import tomllib

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
    for node in ast.walk(test):
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
    except ImportError:
        return False
    return True


def _run_vermin(paths: Sequence[str], target: str) -> list[tuple[str, int, str, str]]:
    args = ["-t=" + target + "-", "--violations", "--no-parse-comments", "--backport", "typing_extensions", "--format", "parsable"]
    out = []
    for batch in _batches(paths):
        proc = subprocess.run(
            [sys.executable, "-c", _VERMIN, *args, *batch], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=1800
        )
        if proc.returncode not in (0, 1):  # 1 = violations found
            raise CorpusError(f"vermin exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:500]}")
        out += parse_vermin_output(proc.stdout)
    return out


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
