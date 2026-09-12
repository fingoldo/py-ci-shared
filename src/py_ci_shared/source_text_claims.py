"""Tests that assert on SOURCE TEXT instead of running the code: one AST detector for every repo.

A test that reads the code under test and checks a substring passes against dead code, an inverted
condition and a comment. Four repositories banned the practice independently and each ban caught a
different subset of the spellings, because each was widened only for the evasion its own repo had
just hit (2026-09-11 comparison, recorded in the py-ci-shared history of this module):

* ``inspect.getsource`` bare or aliased, ``getsourcelines``, ``ast.unparse``, ``dis`` and ``co_code``
  -- one repo saw the bare name, one the aliases, one the ``dis`` family;
* a ``.py`` or ``.sql`` file read through ``__file__``, a module constant, or a ``glob("*.py")`` loop
  variable or a generator over one -- one repo saw the variables, another only a literal on the same line;
* the text travelling under a new name, sliced (``src[src.index("def f"):]``), or returned by a
  module-level ``def _read(rel)`` helper -- each seen by exactly one repo.

This module is the union, as an AST walk rather than a regex so that aliases, scopes and helpers
resolve. ``.sql`` counts as source: a SQL-substring assertion passes against a constant the call site
never uses, which is how one of the two shipped defects that started all this happened.

What is deliberately NOT a claim: ``ast.parse(text)`` and a walk over its nodes. That is how every
meta-linter in these repositories works, and a rule that flagged it would be switched off the day it
landed. Reading a fixture, a JSON cache, a README or a prompt file is not a claim either.

Two modes. ``"assertion"`` (the default) flags an ``assert`` -- or an ``if`` whose body calls
``.fail(...)`` -- that tests source text's CONTENT (``in``, ``==``, ``.count``, a regex, ``.find`` /
``.index`` / ``.startswith``, or the truth of a regex match taken over the source). ``"read"`` flags every read of source in a test, asserted on or not,
for a repo that wants the stricter rule.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_READERS",
    "REFRESH_FLAG",
    "SourceTextClaim",
    "assert_no_new_source_text_claims",
    "find_source_text_claims",
    "write_source_text_baseline",
]

REFRESH_FLAG = "--refresh-source-text-baseline"

#: Calls whose result is program text about the program itself.
DEFAULT_READERS: frozenset[str] = frozenset({"getsource", "getsourcelines", "getsourcefile", "getclosurevars", "unparse"})
_DISASSEMBLERS = frozenset({"dis", "get_instructions", "code_info"})
#: Attributes that hand back a function's own bytecode or constants. ``__code__`` alone is not here: reaching
#: through it to CALL a wrapped function is behavioural testing.
_CODE_ATTRS = frozenset({"co_code", "co_consts", "co_names"})
_FILE_READS = frozenset({"read_text", "read_bytes", "read", "readlines"})
#: Results of these are structure, not text: parsing is how a meta-linter works, and a regex over source
#: that yields a list of matches has already stopped being a substring claim.
_STRUCTURAL = frozenset({"parse", "walk", "iter_child_nodes", "literal_eval", "findall", "finditer", "splitlines"})
_CONTENT_METHODS = frozenset({"count", "search", "match", "fullmatch", "findall", "index", "find", "rfind", "rindex", "startswith", "endswith"})
#: Substrings at least one of which any claim's file must contain: the file reads, `open(`, the code attributes and
#: the `dis` functions. Reader names are added per call.
_TRIGGER_TOKENS = ("read_text", "read_bytes", "readlines", ".read(", "open(", "co_code", "co_consts", "co_names", "get_instructions", "code_info", "dis(")
_NON_SOURCE_SUFFIXES = (".json", ".toml", ".md", ".txt", ".csv", ".ini", ".cfg", ".yml", ".yaml", ".html", ".css", ".js", ".log", ".env", ".lock", ".jsonl")


@dataclass(frozen=True)
class SourceTextClaim:
    """One claim: where it is, which function holds it, and how the source was read."""

    line: int
    function: str
    kind: str

    def key(self, rel: str) -> str:
        """``rel::function::kind`` -- stable across line shifts, which is what a baseline needs."""
        return f"{rel}::{self.function}::{self.kind}"


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _string_constants(node: ast.AST) -> Iterator[str]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


class _Detector:
    def __init__(self, tree: ast.Module, *, readers: frozenset[str], treat_sql_as_source: bool, follow_helpers: bool) -> None:
        self.tree = tree
        self.readers = readers
        self.suffixes = (".py", ".sql") if treat_sql_as_source else (".py",)
        self.dis_names = {"dis"} | {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names if a.name == "dis"}
        self.module_paths = self._path_names(tree.body)
        self.helpers: set[str] = set()
        if follow_helpers:
            self.helpers = self._reader_helpers()

    # -- what counts as a path to source ------------------------------------------------------------
    def _is_source_path_expr(self, node: ast.AST, path_names: set[str]) -> bool:
        literals = list(_string_constants(node))
        if any(lit.lower().endswith(self.suffixes) for lit in literals):
            return True
        if any(lit.lower().endswith(_NON_SOURCE_SUFFIXES) for lit in literals):
            return False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and (sub.id == "__file__" or sub.id in path_names):
                return True
            if isinstance(sub, ast.Attribute) and sub.attr == "__file__":  # `Path(module.__file__)`
                return True
        return False

    def _path_names(self, body: Iterable[ast.stmt], inherited: frozenset[str] = frozenset()) -> set[str]:
        """Names bound to a source path: ``_SCRIPT = ROOT / "x.py"`` and ``for p in DIR.glob("*.py")``."""
        names: set[str] = set(inherited)
        for stmt in body:
            for node in _walk_scope([stmt]):
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and not _contains_read(node.value):
                    if self._is_source_path_expr(node.value, names):
                        names.update(_target_names(node))
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    # `with open("pkg/mod.py") as fh:` -- `fh.read()` then reads source through a name with no path of its own.
                    for item in node.items:
                        expr = item.context_expr
                        if item.optional_vars is not None and isinstance(expr, ast.Call) and _call_name(expr) == "open" and expr.args:
                            if self._is_source_path_expr(expr.args[0], names):
                                names.update(n.id for n in ast.walk(item.optional_vars) if isinstance(n, ast.Name))
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    # `ast.comprehension` covers `"".join(p.read_text() for p in DIR.glob("*.py"))`, which reads the same files.
                    globs = [c for c in ast.walk(node.iter) if isinstance(c, ast.Call) and _call_name(c) in ("glob", "rglob", "iterdir")]
                    if any(lit.lower().endswith(self.suffixes) for c in globs for lit in _string_constants(c)):
                        names.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
        return names

    # -- what counts as reading source --------------------------------------------------------------
    def reader_kind(self, node: ast.AST, path_names: set[str], tainted: set[str]) -> str | None:
        """How *node* yields source text, or None. Checks readers, file reads, helpers and tainted names."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = _call_name(sub)
                if name in self.readers:
                    return f"{name}()"
                if name in _DISASSEMBLERS and isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name) and sub.func.value.id in self.dis_names:
                    return f"dis.{name}()"
                if name in _FILE_READS and isinstance(sub.func, ast.Attribute) and self._is_source_path_expr(sub.func.value, path_names):
                    return "reads a source file"
                if name == "open" and sub.args and self._is_source_path_expr(sub.args[0], path_names):
                    return "reads a source file"
                if isinstance(sub.func, ast.Name) and sub.func.id in self.helpers:
                    return f"via helper {sub.func.id}()"
            elif isinstance(sub, ast.Attribute) and sub.attr in _CODE_ATTRS:
                return sub.attr
        return None

    def _reader_helpers(self) -> set[str]:
        """Module-level functions whose return value is source text -- the ubiquitous ``def _read(rel)``."""
        out: set[str] = set()
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                paths = self._path_names(node.body, frozenset(self.module_paths) | _param_names(node))
                tainted = self._tainted(node.body, paths, set())
                for ret in ast.walk(node):
                    if isinstance(ret, ast.Return) and ret.value is not None and (self.reader_kind(ret.value, paths, tainted) or _uses(ret.value, tainted)):
                        out.add(node.name)
        return out

    def _tainted(self, body: list[ast.stmt], path_names: set[str], inherited: set[str]) -> set[str]:
        """Names holding source text in this scope, propagated through derivations until nothing changes."""
        tainted = set(inherited)
        for _ in range(6):
            before = len(tainted)
            for node in _walk_scope(body):
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                    value = node.value
                    if isinstance(value, ast.Call) and _call_name(value) in _STRUCTURAL:
                        continue
                    if self.reader_kind(value, path_names, tainted) or _uses(value, tainted):
                        tainted.update(_target_names(node))
                elif isinstance(node, (ast.For, ast.AsyncFor)) and _uses(node.iter, tainted) and not (isinstance(node.iter, ast.Call) and _call_name(node.iter) in _STRUCTURAL):
                    tainted.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
            if len(tainted) == before:
                break
        return tainted

    # -- scopes -------------------------------------------------------------------------------------
    def scopes(self) -> Iterator[tuple[str, list[ast.stmt], set[str], set[str]]]:
        """``(function name, body, source-path names, tainted names)`` for the module and every function."""
        module_tainted = self._tainted(self.tree.body, self.module_paths, set())
        yield "<module>", self.tree.body, self.module_paths, module_tainted
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                paths = self._path_names(node.body, frozenset(self.module_paths))
                yield node.name, node.body, paths, self._tainted(node.body, paths, module_tainted)


_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_scope(body: Iterable[ast.stmt]) -> Iterator[ast.AST]:
    """Every node in *body* without descending into nested function or class definitions -- they are scopes of their own."""
    stack: list[ast.AST] = [node for node in body if not isinstance(node, _NESTED_SCOPES)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, _NESTED_SCOPES))


def _target_names(node: ast.AST) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]  # type: ignore[attr-defined]
    return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    args = fn.args
    return frozenset(a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs])


def _contains_read(node: ast.AST) -> bool:
    return any(isinstance(s, ast.Call) and _call_name(s) in _FILE_READS | {"open"} for s in ast.walk(node))


def _uses(node: ast.AST, names: set[str]) -> bool:
    """Does *node* read one of *names* without CALLING it -- calling a name exercises behaviour."""
    if not names:
        return False
    called = {c.func.id for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    return any(isinstance(s, ast.Name) and s.id in names and s.id not in called for s in ast.walk(node))


def _tests_content(test: ast.AST) -> bool:
    for sub in ast.walk(test):
        if isinstance(sub, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq)) for op in sub.ops):
            return True
        if isinstance(sub, ast.Call) and _call_name(sub) in _CONTENT_METHODS:
            return True
    return False


_MATCHERS = frozenset({"search", "match", "fullmatch"})


def _match_names(body: list[ast.stmt], det: _Detector, paths: set[str], tainted: set[str]) -> set[str]:
    """Names bound to a regex match over source: ``m = re.search(pat, src)`` makes a bare ``assert m`` a content check."""
    out: set[str] = set()
    for node in _walk_scope(body):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and isinstance(node.value, ast.Call) and _call_name(node.value) in _MATCHERS:
            if det.reader_kind(node.value, paths, tainted) or _uses(node.value, tainted):
                out.update(_target_names(node))
    return out


def _fails_in_body(node: ast.If) -> bool:
    return any(isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and isinstance(s.value.func, ast.Attribute) and s.value.func.attr == "fail" for s in node.body)


def find_source_text_claims(
    path: Path,
    *,
    mode: str = "assertion",
    readers: Iterable[str] = DEFAULT_READERS,
    treat_sql_as_source: bool = True,
    follow_helpers: bool = True,
) -> list[SourceTextClaim]:
    """Every source-text claim in *path*, innermost function first, one per statement."""
    if mode not in ("assertion", "read"):
        raise ValueError(f"mode must be 'assertion' or 'read', not {mode!r}")
    reader_names = frozenset(readers)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Every claim needs a reader or a file read somewhere in the file, so a file naming none of them is skipped
    # unparsed: on mlframe's 3,597 test files that is the difference between a scan measured in minutes and one
    # that fits a pre-commit hook.
    if not any(token in text for token in (*reader_names, *_TRIGGER_TOKENS)):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    det = _Detector(tree, readers=reader_names, treat_sql_as_source=treat_sql_as_source, follow_helpers=follow_helpers)
    claims: dict[int, SourceTextClaim] = {}
    for function, body, paths, tainted in det.scopes():
        matches = _match_names(body, det, paths, tainted)
        for node in _walk_scope(body):
            if mode == "read":
                if isinstance(node, (ast.Call, ast.Attribute)) and node.lineno not in claims:
                    kind = det.reader_kind(node, paths, set())
                    # Parsing a file's content as Python says the file IS Python, wherever its path came from: a
                    # parameter, or a list a helper built. A read mode that tracked only literal paths missed both.
                    if kind is None and isinstance(node, ast.Call) and _call_name(node) == "parse" and any(_contains_read(a) for a in node.args):
                        kind = "parses a file as Python"
                    if kind:
                        claims[node.lineno] = SourceTextClaim(node.lineno, function, kind)
                continue
            # `node.test` is read through the isinstance itself, not through an `is_check` flag: mypy
            # narrows the former and not the latter, and the flag hid that the branch also accepts an
            # `ast.If` whose body fails.
            if not isinstance(node, (ast.Assert, ast.If)) or (isinstance(node, ast.If) and not _fails_in_body(node)) or node.lineno in claims:
                continue
            test = node.test
            kind = det.reader_kind(test, paths, tainted)
            if kind is None:
                used = next((s.id for s in ast.walk(test) if isinstance(s, ast.Name) and s.id in tainted), None)
                kind = f"text held in `{used}`" if used and _uses(test, tainted) else None
            if kind and (_tests_content(test) or _uses(test, matches)):
                claims[node.lineno] = SourceTextClaim(node.lineno, function, kind)
    return sorted(claims.values(), key=lambda c: c.line)


def _keys(files: Iterable[Path], repo_root: Path, **kwargs: object) -> tuple[dict[str, list[int]], int]:
    found: dict[str, list[int]] = {}
    scanned = 0
    for path in files:
        scanned += 1
        rel = path.relative_to(repo_root).as_posix() if path.is_relative_to(repo_root) else path.as_posix()
        for claim in find_source_text_claims(path, **kwargs):  # type: ignore[arg-type]
            found.setdefault(claim.key(rel), []).append(claim.line)
    return found, scanned


def write_source_text_baseline(baseline_path: Path, keys: Iterable[str]) -> None:
    """Record today's claims as accepted debt, sorted, one key per entry."""
    import json

    baseline_path.write_text(json.dumps(sorted(set(keys)), indent=2) + "\n", encoding="utf-8")


def assert_no_new_source_text_claims(
    files: Iterable[Path],
    repo_root: Path,
    *,
    allowlist: Mapping[str, str] | None = None,
    baseline_path: Path | None = None,
    min_files: int = 1,
    **detector_kwargs: object,
) -> None:
    """Fail on any claim not accepted by *allowlist* (``rel path -> reason``, whole files) or *baseline_path*.

    Both lists shrink only: an allowlisted file that no longer holds a claim, or a baseline key that no longer
    matches one, fails too -- an entry that suppresses nothing would absorb the same claim if it came back.
    ``REFRESH_FLAG`` on the pytest command line rewrites the baseline and skips.
    """
    import json

    import pytest

    files = list(files)
    found, scanned = _keys(files, repo_root, **detector_kwargs)
    if scanned < min_files:
        pytest.fail(f"only {scanned} file(s) scanned; expected at least {min_files} -- the file list lost its subject")
    allow = dict(allowlist or {})
    short = [f"allowlist entry has no reason: {rel}" for rel, why in allow.items() if len(why.strip()) < 20]
    by_file: dict[str, list[str]] = {}
    for key in found:
        by_file.setdefault(key.split("::", 1)[0], []).append(key)
    unallowed = {k: v for k, v in found.items() if k.split("::", 1)[0] not in allow}
    stale_allow = sorted(rel for rel in allow if rel not in by_file)

    if baseline_path is not None and (REFRESH_FLAG in sys.argv or not baseline_path.exists()):
        write_source_text_baseline(baseline_path, unallowed)
        pytest.skip(f"source-text baseline written: {len(unallowed)} key(s) in {baseline_path.name}")
    accepted = set(json.loads(baseline_path.read_text(encoding="utf-8"))) if baseline_path is not None else set()
    new = sorted(f"{k} (line {', '.join(map(str, v))})" for k, v in unallowed.items() if k not in accepted)
    stale_base = sorted(accepted - set(unallowed))

    problems: list[str] = []
    if new:
        problems.append(
            f"{len(new)} test(s) assert on SOURCE TEXT -- they pass against dead code and against a comment. Call the code and "
            "assert on what it returns or does:\n    " + "\n    ".join(new)
        )
    if stale_allow:
        problems.append("allowlisted file(s) that no longer read source; remove them:\n    " + "\n    ".join(stale_allow))
    if stale_base:
        problems.append(f"baseline entr(ies) that no longer match a claim; remove them (or pass {REFRESH_FLAG}):\n    " + "\n    ".join(stale_base))
    problems.extend(short)
    if problems:
        pytest.fail("\n".join(problems))
