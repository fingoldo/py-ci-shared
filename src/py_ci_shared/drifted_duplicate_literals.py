"""One set of numbers written out in several modules: the literal analogue of ``drifted_duplicate_functions``.

A threshold, a band edge or a measured figure restated by hand in a second module is a copy nothing keeps in step:
the next re-measurement reaches whichever copies the author opened. Three real cases from one dashboard, all fixed by
deriving every surface from one constant:

* the last-viewed band cut-offs ``(20, 70, 221)`` hours, written in the colour code, in the tooltip text that explains
  the colours, and in a verify script's SQL;
* the score caveat (top fifth hired 22.7% against 26.0% for the bottom fifth, over 1,652 postings), written in three
  surfaces, so a re-run of the measurement would have left two of them quoting the old numbers;
* the 48 h staleness threshold, computed separately on three tabs.

Three rules, each quiet on ordinary code:

* ``literal-set``: the same set of at least two numbers (after dropping the common ones in *ignore*) in two modules.
  A site is a tuple/list/set literal that is bound to a name, used as a default or compared against
  (``x in (20, 70)``), an ``if``/``elif`` ladder's thresholds (``h <= 20 ... elif h <= 70 ... elif h <= 221``), or a
  string (a tooltip, an SQL text) quoting the numbers. Two sites match when the smaller set is contained in the
  larger, so ``"22.7% vs 26.0%"`` matches a caveat that also quotes ``1,652``. A literal passed straight to a call
  (``figsize=(12, 8)``, ``np.zeros((3, 4))``) is a shape, not a threshold, and is not a site; neither are docstrings,
  version tuples, dates, times and dotted versions inside strings.
* ``named-constant``: one value bound to ALL_CAPS module constants with similar names in two modules
  (``_STALE_AFTER_H = 48`` and ``STALENESS_LIMIT_H = 48``): names share a word, or a stem of at least four letters.
* ``threshold``: one value compared against similarly named operands in at least *min_threshold_modules* modules
  (``age_h > 48`` on three tabs).

Test files are skipped by default (a test restating a figure pins it). Keep one source of truth that the other
modules import or format in, or accept a group in the baseline with the reason it is legitimately separate: a
refreshed entry is written as ``NEEDS-JUSTIFICATION`` and fails until a reason replaces the marker.

Usage::

    from py_ci_shared.drifted_duplicate_literals import assert_no_drifted_duplicate_literals

    def test_no_drifted_duplicate_literals(request):
        assert_no_drifted_duplicate_literals(REPO / "dashboard", baseline_path=HERE / "_duplicate_literals_baseline.json", request=request)
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from ._core import UNJUSTIFIED_MARKER, Baseline, Finding, ParsedFile, ScanResult, refresh_requested
from ._gate_report import report, scan_tree, skip_set

__all__ = [
    "DEFAULT_IGNORE",
    "REFRESH_FLAG",
    "RULE_CONSTANT",
    "RULE_SET",
    "RULE_THRESHOLD",
    "LiteralSite",
    "assert_no_drifted_duplicate_literals",
    "find_drifted_duplicate_literals",
]

RULE_SET = "literal-set"
RULE_CONSTANT = "named-constant"
RULE_THRESHOLD = "threshold"
REFRESH_FLAG = "--refresh-duplicate-literals-baseline"
GATE = "drifted-duplicate-literals"
#: Numbers too common to mean anything when two modules share them: counters, quantiles, powers of two, clock and calendar units.
DEFAULT_IGNORE: frozenset[float] = frozenset(
    {-1, 0, 0.25, 0.5, 0.75, 0.95, 0.99, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 24, 30, 32, 50, 60, 64, 100, 128, 255, 256, 365, 512, 1000, 1024, 3600, 86400}
    # HTTP statuses: every client of one API compares against the same ones, and they cannot drift.
    | {200, 201, 202, 204, 301, 302, 304, 400, 401, 403, 404, 405, 409, 410, 413, 422, 429, 500, 502, 503, 504}
)
_VERSION_NAME = re.compile(r"(?i)version|_info$")
# Numbers inside text: a thousands-grouped integer, or a plain integer or decimal; not part of a word, date, time or dotted version.
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?::\d{2})?|\d+\.\d+\.\d+")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\d.]*\d)(?![A-Za-z_]*\()")
_UNIT_ALIASES = {"h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours", "s": "seconds", "sec": "seconds", "secs": "seconds", "d": "days", "day": "days"}
#: Words that say what kind of number a constant is, not what it is about: sharing one does not make two names similar.
_GENERIC_WORDS = frozenset(
    {
        "max",
        "min",
        "default",
        "n",
        "num",
        "limit",
        "size",
        "count",
        "threshold",
        "value",
        "total",
        "pct",
        "percent",
        "the",
        "of",
        "per",
        "ms",
        "minutes",
        "chars",
        "bytes",
        "rows",
        "items",
    }
    | set(_UNIT_ALIASES.values())
)
_COMPARE_SKIP_WORDS = frozenset({"x", "v", "n", "i", "j", "k", "value", "len", "self"})


@dataclass(frozen=True)
class LiteralSite:
    """Where one set of numbers was written: ``kind`` is ``tuple``, ``ladder``, ``text``, ``constant`` or ``compare``."""

    path: str
    line: int
    kind: str
    values: frozenset[float]
    name: str = ""

    def render(self) -> str:
        label = f" {self.name}" if self.name else ""
        return f"{self.path}:{self.line} ({self.kind}{label})"


def _number(node: ast.AST) -> Optional[float]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _number(node.operand)
        return -inner if inner is not None else None
    return None


def _numeric_collection(node: ast.AST) -> Optional[list[float]]:
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and len(node.elts) >= 2:
        values = [_number(e) for e in node.elts]
        if all(v is not None for v in values):
            return [v for v in values if v is not None]
    return None


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [_UNIT_ALIASES.get(w, w) for w in spaced.lower().split("_") if w]


def _similar(a: str, b: str, generic: frozenset[str]) -> bool:
    """Names share a non-generic word, or two such words share a stem of four letters (``stale``/``staleness``)."""
    if a == b:
        return True
    wa = [w for w in _words(a) if w not in generic]
    wb = [w for w in _words(b) if w not in generic]
    return any(x == y or (len(x) >= 4 and len(y) >= 4 and x[:4] == y[:4] and (x.startswith(y) or y.startswith(x) or x[:5] == y[:5])) for x in wa for y in wb)


def _text_numbers(text: str, ignore: frozenset[float]) -> frozenset[float]:
    cleaned = _DATE_TIME.sub(" ", text)
    out = set()
    for match in _NUMBER.finditer(cleaned):
        raw = match.group(1).replace(",", "")
        value = float(raw) if "." in raw else int(raw)
        if value not in ignore:
            out.add(value)
    return frozenset(out)


def _docstring_ids(tree: ast.Module) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _ladder_values(node: ast.If, ignore: frozenset[float]) -> tuple[list[float], set[int]]:
    """The thresholds an ``if``/``elif`` chain compares against, and the ids of the chain's ``If`` nodes."""
    values: list[float] = []
    chain: set[int] = set()
    current: Optional[ast.stmt] = node
    while isinstance(current, ast.If):
        chain.add(id(current))
        for test in ast.walk(current.test):
            if isinstance(test, ast.Compare):
                for operand in [test.left, *test.comparators]:
                    value = _number(operand)
                    if value is not None and value not in ignore:
                        values.append(value)
        current = current.orelse[0] if len(current.orelse) == 1 else None
    return values, chain


def _versionish(node: ast.Compare) -> bool:
    return any(_VERSION_NAME.search(ast.unparse(op)) for op in [node.left, *node.comparators] if _number(op) is None and _numeric_collection(op) is None)


def _kept(values: Optional[list[float]], ignore: frozenset[float]) -> Optional[frozenset[float]]:
    """The values that are not ignored, when at least two are left."""
    if values is None:
        return None
    kept = frozenset(v for v in values if v not in ignore)
    return kept if len(kept) >= 2 else None


def _collection_site(node: ast.AST, rel: str, ignore: frozenset[float]) -> Iterator[LiteralSite]:
    """A tuple/list/set literal bound to a name, compared against, or used as a parameter default."""
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
        name = ",".join(_target_name(t) for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))
        kept = _kept(_numeric_collection(node.value), ignore)
        if kept is not None and not _VERSION_NAME.search(name):
            yield LiteralSite(rel, node.lineno, "tuple", kept, name)
    elif isinstance(node, ast.Compare) and not _versionish(node):
        for operand in node.comparators:
            kept = _kept(_numeric_collection(operand), ignore)
            if kept is not None:
                yield LiteralSite(rel, node.lineno, "tuple", kept)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for default in [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]:
            kept = _kept(_numeric_collection(default), ignore)
            if kept is not None:
                yield LiteralSite(rel, default.lineno, "tuple", kept, node.name)


def _text_site(node: ast.AST, rel: str, ignore: frozenset[float], docstrings: set[int]) -> Iterator[LiteralSite]:
    """A string (an f-string's literal parts) quoting at least two numbers; docstrings are commentary, not surfaces."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else " " for v in node.values)
    else:
        return
    found = _text_numbers(text, ignore)
    if len(found) >= 2:
        yield LiteralSite(rel, getattr(node, "lineno", 1), "text", found)


def _sites(parsed: ParsedFile, ignore: frozenset[float]) -> Iterator[LiteralSite]:
    docstrings = _docstring_ids(parsed.tree)
    in_ladder: set[int] = set()
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.If):
            if id(node) in in_ladder:
                continue
            values, chain = _ladder_values(node, ignore)
            in_ladder |= chain
            if len(set(values)) >= 2 and len(chain) >= 2:
                yield LiteralSite(parsed.rel, node.lineno, "ladder", frozenset(values))
        else:
            yield from _collection_site(node, parsed.rel, ignore)
            yield from _text_site(node, parsed.rel, ignore, docstrings)


def _constants(parsed: ParsedFile, ignore: frozenset[float]) -> Iterator[LiteralSite]:
    for node in parsed.tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _number(node.value)
            for target in targets:
                if value is not None and value not in ignore and isinstance(target, ast.Name) and target.id.lstrip("_").isupper():
                    yield LiteralSite(parsed.rel, node.lineno, "constant", frozenset({value}), target.id)


def _comparisons(parsed: ParsedFile, ignore: frozenset[float]) -> Iterator[LiteralSite]:
    for node in ast.walk(parsed.tree):
        if not isinstance(node, ast.Compare) or len(node.comparators) != 1:
            continue
        left, right = node.left, node.comparators[0]
        value, other = (_number(right), left) if _number(right) is not None else (_number(left), right)
        if value is None or value in ignore or _number(other) is not None:
            continue
        name = _target_name(other)
        if name and not any(w in _COMPARE_SKIP_WORDS for w in _words(name)):
            yield LiteralSite(parsed.rel, node.lineno, "compare", frozenset({value}), name)


def _distinctive(value: float) -> bool:
    """Written like a measurement (``22.7``, ``26.0``, ``1,652``) rather than a round setting."""
    return isinstance(value, float) or abs(value) >= 1000


def _fmt(values: Iterable[float]) -> str:
    return "(" + ", ".join(repr(v) for v in sorted(values)) + ")"


def _seed_groups(sites: list[LiteralSite]) -> dict[frozenset[float], set[int]]:
    """Each site whose whole set also appears (inside a set at least as large) in another module starts a group."""
    by_value: dict[float, set[int]] = defaultdict(set)
    for i, site in enumerate(sites):
        for v in site.values:
            by_value[v].add(i)
    groups: dict[frozenset[float], set[int]] = {}
    for i, site in enumerate(sites):
        if site.kind == "text" and not all(_distinctive(v) for v in site.values):
            continue  # prose shares small round numbers all the time; text alone starts a group only on measured-looking figures
        holders = set.intersection(*(by_value[v] for v in site.values))
        members = {j for j in holders if sites[j].path != site.path}
        if members:
            groups.setdefault(site.values, set()).update(members | {i})
    return groups


def _set_groups(sites: list[LiteralSite]) -> list[tuple[frozenset[float], list[LiteralSite]]]:
    groups = _seed_groups(sites)
    spans = []
    for key, members in groups.items():
        unique = {(sites[j].path, sites[j].line, sites[j].kind): sites[j] for j in members}
        listed = sorted(unique.values(), key=lambda s: (s.path, s.line))
        paths = frozenset(s.path for s in listed)
        if len(paths) >= 2:
            spans.append((key, listed, paths))
    # Of two nested groups, one that reaches fewer modules says nothing new; on the same modules, keep the larger set.
    return [
        (key, listed)
        for key, listed, paths in spans
        if not any((key < other or other < key) and (paths < reach or (paths == reach and key < other)) for other, _, reach in spans)
    ]


def _components(members: list[LiteralSite], generic: frozenset[str]) -> list[list[LiteralSite]]:
    """Connected components of "similar name, other module"."""
    parent = list(range(len(members)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            if members[i].path != members[j].path and _similar(members[i].name, members[j].name, generic):
                parent[find(i)] = find(j)
    comps: dict[int, list[LiteralSite]] = defaultdict(list)
    for i, site in enumerate(members):
        comps[find(i)].append(site)
    return list(comps.values())


def _named_groups(sites: list[LiteralSite], generic: frozenset[str], min_modules: int) -> list[tuple[float, list[LiteralSite]]]:
    by_value: dict[float, list[LiteralSite]] = defaultdict(list)
    for site in sites:
        by_value[next(iter(site.values))].append(site)
    return [
        (value, sorted(comp, key=lambda s: (s.path, s.line)))
        for value, members in sorted(by_value.items())
        for comp in _components(members, generic)
        if len({s.path for s in comp}) >= min_modules
    ]


def _finding(rule: str, label: str, listed: list[LiteralSite]) -> Finding:
    modules = sorted({s.path for s in listed})
    where = ", ".join(s.render() for s in listed)
    first = listed[0]
    return Finding(first.path, first.line, rule, f"{label} written in {len(modules)} modules: {where}", key=f"{rule}::{label}::{' '.join(modules)}")


def _file_sites(parsed: ParsedFile, ignore: frozenset[float]) -> tuple[list[LiteralSite], list[LiteralSite], list[LiteralSite]]:
    return list(_sites(parsed, ignore)), list(_constants(parsed, ignore)), list(_comparisons(parsed, ignore))


def _collect(
    root: Union[str, Path],
    *,
    ignore: Iterable[float],
    rules: Iterable[str],
    min_threshold_modules: int,
    skip_dir_names: Iterable[str],
    include_tests: bool,
    use_git: Optional[bool],
) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    ignored = frozenset(ignore)
    wanted = frozenset(rules)
    sets: list[LiteralSite] = []
    constants: list[LiteralSite] = []
    compares: list[LiteralSite] = []
    for parsed in scan:
        a, b, c = _file_sites(parsed, ignored)
        sets += a
        constants += b
        compares += c
    findings: list[Finding] = []
    if RULE_SET in wanted:
        findings += [_finding(RULE_SET, _fmt(key), listed) for key, listed in _set_groups(sets)]
    if RULE_CONSTANT in wanted:
        findings += [
            _finding(RULE_CONSTANT, f"{value!r} as {'/'.join(sorted({s.name for s in listed}))}", listed)
            for value, listed in _named_groups(constants, _GENERIC_WORDS, 2)
        ]
    if RULE_THRESHOLD in wanted:
        named = _named_groups(compares, frozenset(_GENERIC_WORDS - set(_UNIT_ALIASES.values())), max(min_threshold_modules, 2))
        findings += [_finding(RULE_THRESHOLD, f"{value!r} compared against {'/'.join(sorted({s.name for s in listed}))}", listed) for value, listed in named]
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule)), scan


def find_drifted_duplicate_literals(
    root: Union[str, Path],
    *,
    ignore: Iterable[float] = DEFAULT_IGNORE,
    rules: Iterable[str] = (RULE_SET, RULE_CONSTANT, RULE_THRESHOLD),
    min_threshold_modules: int = 3,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """One finding per group of modules that restate one set of numbers, plus one ``unparsed-file`` finding per file
    that could not be parsed. *root* is one package: groups never span two calls."""
    findings, scan = _collect(
        root,
        ignore=ignore,
        rules=rules,
        min_threshold_modules=min_threshold_modules,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        use_git=use_git,
    )
    return findings + scan.unparsed_findings()


def assert_no_drifted_duplicate_literals(
    root: Union[str, Path],
    *,
    ignore: Iterable[float] = DEFAULT_IGNORE,
    rules: Iterable[str] = (RULE_SET, RULE_CONSTANT, RULE_THRESHOLD),
    min_threshold_modules: int = 3,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a restated set of numbers (new against *baseline_path* when given), on an accepted group whose note is
    still ``NEEDS-JUSTIFICATION``, on fewer than *min_files* parsed files, and on an unparsable file unless
    *allow_unparsed*. Refresh with ``--refresh-duplicate-literals-baseline`` or ``PY_CI_SHARED_REFRESH=duplicate-literals``."""
    findings, scan = _collect(
        root,
        ignore=ignore,
        rules=rules,
        min_threshold_modules=min_threshold_modules,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        use_git=use_git,
    )
    guidance = "keep one source of truth the other modules import or format in, or accept the group in the baseline with the reason"
    if baseline_path is None:
        report(
            findings,
            gate=GATE,
            flag=REFRESH_FLAG,
            guidance=guidance,
            parsed_count=scan.parsed_count,
            problems=scan.unparsed,
            min_files=min_files,
            root=root,
            allow_unparsed=allow_unparsed,
        )
        return
    # The walk first, with no findings: a broken walk must fail before a baseline is read or written.
    report(
        [],
        gate=GATE,
        flag=REFRESH_FLAG,
        guidance=guidance,
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        allow_unparsed=allow_unparsed,
    )
    baseline = Baseline(
        baseline_path,
        gate=GATE,
        refresh_command=f"pytest {REFRESH_FLAG} (or PY_CI_SHARED_REFRESH=duplicate-literals)",
        new_note=f"{UNJUSTIFIED_MARKER}: why these modules may each write the numbers",
    )
    do_refresh = refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request)
    baseline.enforce(findings, refresh=do_refresh, guidance=guidance, request=request).raise_for_pytest()
