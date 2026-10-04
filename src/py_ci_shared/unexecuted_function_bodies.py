"""A function whose body never runs under the unit coverage run.

The defect this reports: ``db/outcomes.py: outcome_column_exists`` probed a table that had moved three weeks earlier, so it
always answered False and every outcome pass quit before reading anything; outcome labelling was dead for weeks. Every offline
test STUBBED that function (``patch(...outcome_column_exists...)``), so its body ran in none of 650+ tests. ``uncalled_functions``
cannot see it (the function IS called, from the code under test) and a whole-suite coverage floor does not look per function.
This gate reads the coverage report of the unit run and reports every function whose body statements have ZERO executed lines.

HOW A CONSUMER PRODUCES THE REPORT
----------------------------------
From the UNIT run only (the run that stubs the database; that is the run whose blind spots matter)::

    pytest --cov=mypkg --cov-report=json:coverage.json          # pytest-cov
    coverage run -m pytest && coverage json -o coverage.json    # plain coverage.py

then, in a meta test that runs AFTER it (or in CI as a step after the unit job)::

    from py_ci_shared.unexecuted_function_bodies import assert_unexecuted_function_bodies

    def test_no_new_unexecuted_function_bodies():
        assert_unexecuted_function_bodies("coverage.json", ["src/mypkg"], baseline_path="tests/baselines/unexecuted_bodies.json", base=REPO)

or ``python -m py_ci_shared.unexecuted_function_bodies --coverage coverage.json --src src/mypkg --baseline tests/baselines/unexecuted_bodies.json``
(``--refresh-baseline`` rewrites the baseline, shrink-only unless ``--allow-grow``). Exit status: 0 clean, 1 findings, 2 the
gate could not run (missing, empty, stale or mismatching report; an unparsable source file).

A report from an integration run, a shard or a ``-k`` selection is NOT the right input: a function covered only by a real-server
test would read as never executed (that is exactly the finding the gate exists for, so the unit run is the point), and a
narrow selection reports every function it did not reach. Keep the unit job's report; do not merge integration data into it.

Relation to ``coverage_config_parity``: that gate checks the coverage CONFIG a CI run inherits (a whole-suite ``fail_under`` on a
narrow run, ``@njit`` bodies no run can see). This gate consumes the REPORT. The njit blind spot matters here too: a compiled body
never reads as executed, so numba decorators are skipped by default (:data:`DEFAULT_SKIP_DECORATORS`), or run the unit job with
``NUMBA_DISABLE_JIT=1`` and pass ``skip_decorators`` without them. A ``fail_under`` inherited by ``coverage json`` can also make
the report step exit non-zero after the file was written; the file is still valid.

WHAT COUNTS
-----------
For every function and method (nested and async included; lambdas are not functions here) the BODY statement lines are those of
its own statements: the ``def``/decorator lines are excluded, so are docstrings, ``pass``, ``...`` and ``raise NotImplementedError``
stubs; the body of a nested function belongs to that nested function (its ``def`` line is a statement of the outer one). A line
counts only when coverage lists it as a statement (executed or missing), so comments, ``else:`` and continuation lines never do.
A function with no body statement left (a stub, or every line ``# pragma: no cover``, which coverage lists as excluded) is
skipped. A function with at least one body statement and none executed is a finding, rule ``unexecuted-function-bodies``, key
``rule::path::qualname`` (no line number, so an edit above does not invalidate the baseline).

Skipped by default: ``@abstractmethod``/``@overload``, any method of a ``Protocol`` class, anything under ``if TYPE_CHECKING:``,
numba decorators, and the display dunders in :data:`DEFAULT_SKIP_DUNDERS` (``__repr__`` and kin run only when something prints the
object; every other dunder, ``__init__`` included, is checked). A source file with NO entry in the report is treated as wholly
unexecuted (``absent_files="findings"``, the default: a module no test imports has dead functions); ``absent_files="skip"`` ignores
such files and is for a deliberately partial report. ``exempt`` is ``{"path::qualname": "reason"}``: the reason is mandatory, and
an entry that names a function which is executed, skipped or gone is itself a finding (``stale-exempt``).

GUARDS AGAINST A BAD REPORT, never a silent pass: a missing/unreadable/empty report, fewer than ``min_files`` source files present
in the report, a report whose statement lines are not statements of the current source (written for an older revision), and
optionally (``stale_tolerance_seconds``) a report older than the newest source file are all errors with the fix named.

RATCHET: with a baseline (``_core.Baseline``, the multiset every gate uses) the set of never-executed functions may only shrink.
A function not in it fails; a function that GAINED coverage must be removed from it (the message says so) or the baseline stops
meaning anything for it. A baseline that does not exist fails and names the refresh command.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Baseline, CoreError, Finding, scan_python

__all__ = [
    "DEFAULT_SKIP_DECORATORS",
    "DEFAULT_SKIP_DUNDERS",
    "RULE",
    "STALE_EXEMPT_RULE",
    "CoverageReportError",
    "assert_unexecuted_function_bodies",
    "find_unexecuted_function_bodies",
    "main",
]

RULE = "unexecuted-function-bodies"
STALE_EXEMPT_RULE = "stale-exempt"
EXEMPT_REASON_RULE = "exempt-without-reason"

DEFAULT_SKIP_DECORATORS = frozenset(
    {
        "abstractmethod",
        "abstractproperty",
        "abstractclassmethod",
        "abstractstaticmethod",
        "overload",
        "njit",
        "jit",
        "vectorize",
        "guvectorize",
        "cfunc",
        "stencil",
    }
)
DEFAULT_SKIP_DUNDERS = frozenset(
    {
        "__repr__",
        "__str__",
        "__format__",
        "__hash__",
        "__del__",
        "__sizeof__",
        "__dir__",
        "__getstate__",
        "__setstate__",
        "__reduce__",
        "__reduce_ex__",
        "__rich_repr__",
    }
)
_PRAGMA = re.compile(r"#\s*pragma:\s*no\s*cover")
_FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


class CoverageReportError(CoreError, AssertionError):
    """The coverage report cannot be trusted as input: missing, unreadable, empty, stale or not matching the source."""


@dataclass
class _Report:
    """The parts of a coverage.py JSON report the gate reads, keyed by normalised absolute path."""

    files: dict[str, dict[str, Any]]
    timestamp: Optional[datetime]
    path: Path


def _ident(path: Path) -> str:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def load_report(coverage_json: Union[str, Path], coverage_base: Path) -> _Report:
    """Read a ``coverage json`` report (``files`` -> ``executed_lines``/``missing_lines``/``excluded_lines``).

    Relative keys (``relative_files = true``, or the default for files under the working directory) resolve against
    *coverage_base*, the directory the coverage run started in; backslashes from a Windows run are accepted anywhere.
    """
    path = Path(coverage_json)
    hint = "Produce it from the unit run: `pytest --cov=<pkg> --cov-report=json:coverage.json` (or `coverage json -o coverage.json`)."
    if not path.is_file():
        raise CoverageReportError(f"coverage report {path} does not exist, so nothing was checked. {hint}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CoverageReportError(f"coverage report {path} is unreadable ({type(exc).__name__}: {exc}). {hint}") from exc
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or not files:
        raise CoverageReportError(f"coverage report {path} has no `files` entries: the run measured nothing (wrong --cov target, or an empty run). {hint}")
    out: dict[str, dict[str, Any]] = {}
    for key, entry in files.items():
        if not isinstance(entry, dict) or "executed_lines" not in entry or "missing_lines" not in entry:
            raise CoverageReportError(
                f"coverage report {path}: entry {key!r} lacks executed_lines/missing_lines (not a `coverage json` report, or too old a format). {hint}"
            )
        p = Path(str(key).replace("\\", "/"))
        out[_ident(p if p.is_absolute() else coverage_base / p)] = entry
    stamp: Optional[datetime] = None
    meta = data.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("timestamp"), str):
        try:
            stamp = datetime.fromisoformat(meta["timestamp"])
        except ValueError:
            stamp = None
    return _Report(out, stamp, path)


def _decorator_names(node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> set[str]:
    names: set[str] = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _is_protocol(node: ast.ClassDef) -> bool:
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        if (isinstance(target, ast.Name) and target.id == "Protocol") or (isinstance(target, ast.Attribute) and target.attr == "Protocol"):
            return True
    return False


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


@dataclass(frozen=True)
class _Fn:
    qualname: str
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    skipped: str  # "" when the function is checked, else why it is not


def _skip_reason(
    fn: Union[ast.FunctionDef, ast.AsyncFunctionDef], protocol: bool, type_checking: bool, decorators: "frozenset[str]", dunders: "frozenset[str]"
) -> str:
    """Why *fn* is not checked, or "" when it is."""
    if protocol:
        return "method of a Protocol"
    if type_checking:
        return "under TYPE_CHECKING"
    hit = _decorator_names(fn) & decorators
    if hit:
        return "decorated " + ",".join(sorted(hit))
    return "display dunder" if fn.name in dunders else ""


def _functions(
    nodes: "Iterable[ast.AST]", prefix: str, protocol: bool, type_checking: bool, decorators: "frozenset[str]", dunders: "frozenset[str]"
) -> Iterator[_Fn]:
    """Every function under *nodes* (siblings), with its qualified name; nested ones carry ``<locals>`` like ``__qualname__``."""
    for child in nodes:
        if isinstance(child, _FuncDef):
            qual = prefix + child.name
            yield _Fn(qual, child, _skip_reason(child, protocol, type_checking, decorators, dunders))
            yield from _functions(ast.iter_child_nodes(child), qual + ".<locals>.", False, type_checking, decorators, dunders)
        elif isinstance(child, ast.ClassDef):
            yield from _functions(ast.iter_child_nodes(child), prefix + child.name + ".", _is_protocol(child), type_checking, decorators, dunders)
        elif isinstance(child, ast.If) and _is_type_checking(child.test):
            yield from _functions(child.body, prefix, protocol, True, decorators, dunders)
            yield from _functions(child.orelse, prefix, protocol, type_checking, decorators, dunders)
        elif not isinstance(child, ast.expr):
            yield from _functions(ast.iter_child_nodes(child), prefix, protocol, type_checking, decorators, dunders)


def _is_inert(stmt: ast.stmt) -> bool:
    """A statement that carries no behaviour a test would have to run: ``pass``, a docstring or ``...``, a NotImplementedError stub."""
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and (isinstance(stmt.value.value, str) or stmt.value.value is Ellipsis):
        return True
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def _collect(node: ast.AST, out: "set[int]") -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            if _is_inert(child):
                continue
            out.add(child.lineno)
            if isinstance(child, (*_FuncDef, ast.ClassDef)):
                out.update(d.lineno for d in child.decorator_list)
            if not isinstance(child, _FuncDef):
                _collect(child, out)  # a nested function's body belongs to that function
        elif not isinstance(child, ast.expr):
            _collect(child, out)  # ExceptHandler, match_case


def _own_lines(fn: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> "set[int]":
    out: set[int] = set()
    _collect(fn, out)
    return out


def _stmt_starts(tree: ast.Module) -> "set[int]":
    starts: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            starts.add(node.lineno)
            if isinstance(node, (*_FuncDef, ast.ClassDef)):
                starts.update(d.lineno for d in node.decorator_list)
        elif isinstance(node, ast.ExceptHandler):
            starts.add(node.lineno)
    return starts


def _pragma_lines(source: str) -> "set[int]":
    return {i for i, line in enumerate(source.splitlines(), 1) if _PRAGMA.search(line)}


@dataclass
class _Evaluation:
    unexecuted: list[Finding] = field(default_factory=list)  # before exempt
    others: list[Finding] = field(default_factory=list)  # stale-exempt / exempt-without-reason: never baselined
    matched: int = 0
    absent: int = 0
    functions: int = 0


def _check_floor_and_report(matched: int, absent: int, report: _Report, min_files: int) -> None:
    if matched < min_files:
        raise CoverageReportError(
            f"only {matched} source file(s) appear in {report.path} ({absent} did not); expected at least {min_files}. "
            "The report does not describe this source tree: wrong --cov target, a report from another checkout (check `relative_files` and `coverage_base`), "
            "or the run imported nothing. Regenerate it from the unit run."
        )


def _check_matches_source(entry: Mapping[str, Any], parsed: Any, report: _Report) -> None:
    if not parsed.tree.body:
        return  # an empty module has no statements to disagree about; coverage on Python 3.9 still lists its line 1 as executed
    # excluded_lines is left out: an excluded region (a `...` method, a whole `if TYPE_CHECKING:` block) is listed with its blank lines
    listed = set(entry["executed_lines"]) | set(entry["missing_lines"])
    bad = sorted(listed - _stmt_starts(parsed.tree))
    if bad:
        raise CoverageReportError(
            f"{report.path} lists statement line(s) {bad[:5]} of {parsed.rel} that are not statements in the current source: "
            "the report was written for a different revision of the file. Re-run the unit tests with coverage."
        )


def _file_findings(parsed: Any, entry: Optional[Mapping[str, Any]], dec: "frozenset[str]", dunders: "frozenset[str]", ev: _Evaluation) -> list[Finding]:
    """The never-executed functions of one file. *entry* None means the file is absent from the report: every statement counts as unexecuted."""
    pragma = _pragma_lines(parsed.source)
    statements: Optional[set[int]] = None
    executed: set[int] = set()
    if entry is not None:
        executed = set(entry["executed_lines"])
        statements = executed | set(entry["missing_lines"])
    out: list[Finding] = []
    for fn in _functions([parsed.tree], "", False, False, dec, dunders):
        if fn.skipped or fn.node.lineno in pragma or any(d.lineno in pragma for d in fn.node.decorator_list):
            continue
        own = _own_lines(fn.node)
        relevant = (own & statements) if statements is not None else own - pragma
        if not relevant:
            continue  # a stub, or every statement excluded
        ev.functions += 1
        if relevant & executed:
            continue
        why = "its module is absent from the coverage report" if entry is None else "no body statement ran"
        message = f"{fn.qualname}: body never executed by this test run ({why})"
        out.append(Finding(parsed.rel, fn.node.lineno, RULE, message, key=f"{RULE}::{parsed.rel}::{fn.qualname}"))
    return out


def _evaluate(
    coverage_json: Union[str, Path],
    src_roots: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    base: Optional[Path],
    coverage_base: Optional[Path],
    exempt: Optional[Mapping[str, str]],
    min_files: int,
    allow_unparsed: bool,
    use_git: Optional[bool],
    skip_decorators: Iterable[str],
    skip_dunders: Iterable[str],
    absent_files: str,
    stale_tolerance_seconds: Optional[float],
    check_report_matches_source: bool,
) -> _Evaluation:
    if absent_files not in ("findings", "skip"):
        raise ValueError(f"absent_files must be 'findings' or 'skip', not {absent_files!r}")
    root = Path(base) if base is not None else Path.cwd()
    roots = [Path(src_roots)] if isinstance(src_roots, (str, os.PathLike)) else [Path(r) for r in src_roots]
    scan = scan_python(roots, min_files=min_files, root=root, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    report = load_report(coverage_json, Path(coverage_base) if coverage_base is not None else root)
    if stale_tolerance_seconds is not None and report.timestamp is not None:
        newest = max((p.path.stat().st_mtime for p in scan), default=0.0)
        if newest - report.timestamp.timestamp() > stale_tolerance_seconds:
            raise CoverageReportError(
                f"{report.path} was written at {report.timestamp.isoformat()}, more than {stale_tolerance_seconds:g}s before the newest source file changed: "
                "it describes an older tree. Re-run the unit tests with coverage before this gate."
            )
    ev = _Evaluation()
    dec, dunders = frozenset(skip_decorators), frozenset(skip_dunders)
    for parsed in scan:
        entry = report.files.get(_ident(parsed.path))
        if entry is None:
            ev.absent += 1
            if absent_files == "skip":
                continue
        else:
            ev.matched += 1
            if check_report_matches_source:
                _check_matches_source(entry, parsed, report)
        ev.unexecuted.extend(_file_findings(parsed, entry, dec, dunders, ev))
    _check_floor_and_report(ev.matched, ev.absent, report, min_files)
    _apply_exempt(ev, exempt)
    return ev


def _apply_exempt(ev: _Evaluation, exempt: Optional[Mapping[str, str]]) -> None:
    if not exempt:
        return
    live = {f.key.split("::", 1)[1]: f for f in ev.unexecuted}  # "path::qualname"
    for key, reason in sorted(exempt.items()):
        path = key.split("::", 1)[0]
        if not isinstance(reason, str) or not reason.strip():
            ev.others.append(Finding(path, 1, EXEMPT_REASON_RULE, f"exempt entry {key!r} has no reason; say why the body may stay unexecuted"))
        if key not in live:
            ev.others.append(
                Finding(path, 1, STALE_EXEMPT_RULE, f"exempt entry {key!r} names no never-executed function (it ran, was skipped, or is gone); remove it")
            )
    ev.unexecuted = [f for f in ev.unexecuted if f.key.split("::", 1)[1] not in exempt]


def find_unexecuted_function_bodies(
    coverage_json: Union[str, Path],
    src_roots: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    base: Optional[Union[str, Path]] = None,
    coverage_base: Optional[Union[str, Path]] = None,
    exempt: Optional[Mapping[str, str]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    skip_decorators: Iterable[str] = DEFAULT_SKIP_DECORATORS,
    skip_dunders: Iterable[str] = DEFAULT_SKIP_DUNDERS,
    absent_files: str = "findings",
    stale_tolerance_seconds: Optional[float] = None,
    check_report_matches_source: bool = True,
) -> list[Finding]:
    """Every never-executed function (after *exempt*) plus every bad exempt entry, sorted by path and line.

    *base* is what finding paths (and *exempt* keys) are relative to, default the working directory; *coverage_base* is the
    directory the coverage run started in (default *base*), against which relative report keys resolve. Raises
    :class:`CoverageReportError` for a report that cannot be trusted, ``EmptyScanError`` below *min_files* parsed sources and
    ``UnparsedFilesError`` for a source that cannot be parsed (unless *allow_unparsed*): a gate that cannot read its input must not pass.
    """
    ev = _evaluate(
        coverage_json,
        src_roots,
        base=Path(base) if base is not None else None,
        coverage_base=Path(coverage_base) if coverage_base is not None else None,
        exempt=exempt,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
        skip_decorators=skip_decorators,
        skip_dunders=skip_dunders,
        absent_files=absent_files,
        stale_tolerance_seconds=stale_tolerance_seconds,
        check_report_matches_source=check_report_matches_source,
    )
    return sorted([*ev.unexecuted, *ev.others], key=lambda f: (f.path, f.line))


GUIDANCE = (
    "these functions have no executed body statement in the unit run (every test that reaches them stubs or never calls them), so a defect inside "
    "one is invisible to the suite. Cover the real body with a test that does not stub it, or add it to `exempt` with the reason"
)


def _run(
    coverage_json: Union[str, Path],
    src_roots: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]],
    refresh: bool,
    grow: Optional[bool],
    **options: Any,
) -> "tuple[str, str]":
    """``(status, message)`` with status ``ok``, ``refreshed`` or ``fail``."""
    found = find_unexecuted_function_bodies(coverage_json, src_roots, **options)
    others = [f for f in found if f.rule != RULE]
    functions = [f for f in found if f.rule == RULE]
    lines = [f.render() for f in others]
    if baseline_path is None:
        if functions:
            lines.append(f"{len(functions)} unexecuted-function-bodies finding(s); {GUIDANCE}:\n  " + "\n  ".join(f.render() for f in functions))
        return ("fail", "\n".join(lines)) if lines else ("ok", f"unexecuted_function_bodies: clean ({len(functions)} finding(s))")
    baseline = Baseline(
        baseline_path,
        gate="unexecuted_function_bodies",
        refresh_command="python -m py_ci_shared.unexecuted_function_bodies --coverage <report> --src <dirs> --baseline <file> --refresh-baseline [--allow-grow]",
    )
    outcome = baseline.enforce(functions, refresh=refresh, guidance=GUIDANCE, grow=grow)
    if outcome.refreshed:
        return "refreshed", outcome.message
    if outcome.stale:
        lines.append(
            f"{len(outcome.stale)} function(s) in {Path(baseline_path).name} gained coverage (their bodies now run); the ratchet only shrinks, so remove them "
            "by refreshing the baseline (--refresh-baseline), or the baseline stops meaning anything for them:\n    " + "\n    ".join(outcome.stale)
        )
    if not outcome.ok:
        lines.append(outcome.message)
    return ("fail", "\n".join(lines)) if lines else ("ok", outcome.message)


def assert_unexecuted_function_bodies(
    coverage_json: Union[str, Path],
    src_roots: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    grow: Optional[bool] = None,
    base: Optional[Union[str, Path]] = None,
    coverage_base: Optional[Union[str, Path]] = None,
    exempt: Optional[Mapping[str, str]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    skip_decorators: Iterable[str] = DEFAULT_SKIP_DECORATORS,
    skip_dunders: Iterable[str] = DEFAULT_SKIP_DUNDERS,
    absent_files: str = "findings",
    stale_tolerance_seconds: Optional[float] = None,
    check_report_matches_source: bool = True,
) -> None:
    """Fail on any never-executed function, or with *baseline_path* on any the baseline does not accept (a ratchet: shrink only).

    A refresh (*refresh*) rewrites the baseline (growth needs *grow* or ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``) and skips the test.
    """
    status, message = _run(
        coverage_json,
        src_roots,
        baseline_path=baseline_path,
        refresh=refresh,
        grow=grow,
        base=base,
        coverage_base=coverage_base,
        exempt=exempt,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
        skip_decorators=skip_decorators,
        skip_dunders=skip_dunders,
        absent_files=absent_files,
        stale_tolerance_seconds=stale_tolerance_seconds,
        check_report_matches_source=check_report_matches_source,
    )
    if status == "refreshed":
        import pytest

        pytest.skip(message)
    if status == "fail":
        raise AssertionError(message)


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m py_ci_shared.unexecuted_function_bodies", description="Report functions whose body never ran in the unit coverage run."
    )
    parser.add_argument("--coverage", default="coverage.json", help="coverage.py JSON report of the UNIT run (default coverage.json)")
    parser.add_argument("--src", nargs="+", required=True, help="source directories or files to check")
    parser.add_argument("--base", default=None, help="finding paths are relative to this directory (default: cwd)")
    parser.add_argument("--coverage-base", default=None, help="directory the coverage run started in (default: --base)")
    parser.add_argument("--baseline", default=None, help="shrink-only baseline file; without it any finding fails")
    parser.add_argument("--refresh-baseline", action="store_true", help="rewrite the baseline (shrink-only unless --allow-grow)")
    parser.add_argument("--allow-grow", action="store_true", help="let --refresh-baseline add entries (first seeding)")
    parser.add_argument("--min-files", type=int, default=1)
    parser.add_argument("--allow-unparsed", action="store_true")
    parser.add_argument("--include-dunders", action="store_true", help="also check __repr__ and the other display dunders")
    parser.add_argument("--absent-files", choices=("findings", "skip"), default="findings")
    parser.add_argument("--stale-tolerance", type=float, default=None, help="seconds the report may predate the newest source file")
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    try:
        status, message = _run(
            args.coverage,
            args.src,
            baseline_path=args.baseline,
            refresh=args.refresh_baseline,
            grow=True if args.allow_grow else None,
            base=args.base,
            coverage_base=args.coverage_base,
            min_files=args.min_files,
            allow_unparsed=args.allow_unparsed,
            skip_dunders=frozenset() if args.include_dunders else DEFAULT_SKIP_DUNDERS,
            absent_files=args.absent_files,
            stale_tolerance_seconds=args.stale_tolerance,
        )
    except CoreError as exc:  # every input problem (report, floor, unparsed source, baseline) is a CoreError
        print(f"unexecuted_function_bodies: ERROR: {exc}", file=sys.stderr)
        return 2
    print(message, file=sys.stderr if status == "fail" else sys.stdout)
    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
