"""(internal) part of pyutilz.dev.code_audit; see package __init__ for docs."""

from __future__ import annotations
import logging

import ast
import os
import re
import threading
from fnmatch import fnmatch
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Iterator
from typing import Optional

from .._core.node_index import child_nodes, walk as walk_nodes
from .._core.source import SourceParseError, SourceReadError, parse_file
from .._core.node_index import dotted_name as dotted_name  # the one copy lives in _core

# Parse trees live in ONE cache, ``_core.source`` (see ``_safe_parse``); this module keeps only the source-line cache below.

# One full corpus is the working set a single run_all() needs (it bounds the source-line cache); the cap is generous enough that a
# realistic repo (a few thousand files) never evicts mid-run, and small enough to bound a daemon.
_PARSE_CACHE_MAX_ENTRIES = 20000


# Keyed on (path, mtime_ns, size) and bounded (LRU) like the parse cache once was, for the source TEXT the scanners quote in their snippets: the tree cache
# keeps no line table, so every scanner that wanted a snippet re-read and re-split the whole corpus itself.
_SRC_LINES_CACHE: "OrderedDict[tuple[str, int, int], list[str]]" = OrderedDict()

# Guards both caches: a hit is move_to_end-then-read and a miss is insert-then-evict, and a scan run from a thread
# pool interleaves them (a popitem between another thread's membership test and its read raised KeyError).
_CACHE_LOCK = threading.Lock()


logger = logging.getLogger(__name__)


def clear_parse_cache() -> None:
    """Drop every cached parse tree and cached source text. Call it when a long-lived process is done with a scan."""
    from .._core.source import clear_parse_cache as clear_core_parse_cache

    with _CACHE_LOCK:
        _SRC_LINES_CACHE.clear()
    clear_core_parse_cache()


# --- public types --------------------------------------------------------


# The ONE definition of the severity vocabulary and of its ordering. ``Finding.__post_init__``
# rejects anything outside it, ``severity_rank`` orders it, and the CLI filters on that rank -- so a
# stray literal can no longer make a whole scanner's output invisible (a ``severity="Medium"`` did
# exactly that: it sorted below ``Low`` and every ``--min-severity`` setting dropped it).
SEVERITIES = ("P0", "P1", "P2", "Low")
_SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITIES)}

# An unrecognised severity ranks ABOVE P0, so it renders first and gates everything, instead of
# sorting to the bottom and being filtered away unseen.
UNKNOWN_SEVERITY_RANK = -1


def severity_rank(severity: str) -> int:
    """Sort/filter rank of ``severity``: 0..3 for P0/P1/P2/Low, ``UNKNOWN_SEVERITY_RANK`` otherwise."""
    return _SEVERITY_RANK.get(severity, UNKNOWN_SEVERITY_RANK)


@dataclass(frozen=True)
class Finding:
    """One audit hit.

    ``check`` is the scanner name (``"mutable_default"``,
    ``"late_binding_closure"``, ``"default_via_or"``,
    ``"broad_except_swallow"``).

    ``severity`` follows the project audit convention (P0/P1/P2/Low).

    ``file`` is relative to the audit root; ``line`` is the 1-based
    line number of the offending node. ``snippet`` is the source line
    text (one line, stripped) for context. ``detail`` is a short
    free-text reason.
    """

    check: str
    severity: str
    file: str
    line: int
    snippet: str
    detail: str

    def __post_init__(self) -> None:
        """Reject a severity outside ``SEVERITIES`` at construction, where the stray literal is."""
        if self.severity not in _SEVERITY_RANK:
            raise ValueError(f"Finding.severity must be one of {SEVERITIES}, got {self.severity!r} (check={self.check!r})")

    def as_md_row(self) -> str:
        """Render this finding as one Markdown table row (``| severity | check | file:line | snippet | detail |``), escaping pipe characters in the snippet and detail."""
        snip = self.snippet.replace("|", "\\|")
        detail = self.detail.replace("|", "\\|")
        return f"| {self.severity} | {self.check} | {self.file}:{self.line} | " f"`{snip}` | {detail} |"


# --- helpers -------------------------------------------------------------


_PY_EXTS = (".py",)
_DEFAULT_EXCLUDE_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "env",
        ".env",
        ".tox",
        "build",
        "dist",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "htmlcov",
        # `.claude` holds Claude Code's agent worktrees, each a COMPLETE checkout of the repo being audited.
        # Without this, every scanner reports each violation once for the real tree and once more per nested
        # worktree - measured on autopsia with 49 of them, which turned 12 meta-ratchet tests red with findings
        # whose paths all read `<repo>/.claude/worktrees/agent-*/...`. A nested checkout is never the code under
        # audit, and its own tests do not run from here.
        ".claude",
    }
)


# The ONE set of decorator names that count as "this function memoizes its result". Two scanners
# each carried their own, one of them two names long, so widening it after a false positive fixed
# one scanner and left the other reporting the same function.
CACHING_DECORATOR_NAMES = frozenset({"lru_cache", "cache", "cached_property", "cached", "memoize", "once"})


def decorator_name(node: ast.expr) -> str:
    """The bare name of a decorator expression: ``@lru_cache`` / ``@functools.lru_cache(maxsize=1)`` -> ``lru_cache``."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def is_cached(func: "ast.FunctionDef | ast.AsyncFunctionDef", decorator_names: Iterable[str] = CACHING_DECORATOR_NAMES) -> bool:
    """True if ``func`` carries a decorator that memoizes its result, so repeated calls cost nothing."""
    names = set(decorator_names)
    return any(decorator_name(d) in names for d in getattr(func, "decorator_list", ()))


def is_locals_or_globals_call(node: ast.expr) -> bool:
    """True for a bare, no-argument ``locals()``/``globals()`` call node."""
    return isinstance(node, ast.Call) and not node.args and not node.keywords and isinstance(node.func, ast.Name) and node.func.id in ("locals", "globals")


def module_level_names(tree: ast.Module) -> "frozenset[str]":
    """Every name bound at module scope: assignments, imports, and def/class names.

    Tells a write into a shared module-level structure (``_inflight[key] = ...``) apart from one
    into a function-local (``local_map[key] = ...``).
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets: list = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname if a.asname is not None else a.name.split(".")[0] for a in node.names)
            continue
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return frozenset(names)


_TEST_DIR_NAMES = frozenset({"tests", "test"})


def is_test_file(path: "Path | str", root: Optional[Path] = None, extra_test_globs: Iterable[str] = ()) -> bool:
    """Whether ``path`` is test code. ONE definition, called from every scanner that needs it.

    True when the file name matches either pytest naming convention (``test_*.py`` / ``*_test.py``)
    OR the file lives under a directory component named ``tests``/``test`` OR its name matches one
    of ``extra_test_globs`` (the escape hatch for a project whose tests are named something else,
    e.g. ``("check_*.py",)``).

    Thirteen scanners each grew their own version of this predicate, with six mutually incompatible
    semantics -- name-prefix only, prefix-or-suffix, prefix-or-``tests``-in-parts, and so on. The
    cost is invisible: a project whose tests live in ``tests/check_pricing.py`` was scanned by some
    of the test-focused catalogue and skipped entirely by the rest, with no error either way, so
    coverage and cleanliness looked identical. Same reason ``_is_excluded`` is one function.

    ``root``, when given, makes the directory test RELATIVE to the scan root: ``Path.parts`` is
    absolute, so a checkout living anywhere beneath a directory named ``tests`` would otherwise
    classify the whole tree, production modules included, as test code. ``str`` paths are taken to
    be root-relative posix paths already.
    """
    if isinstance(path, Path):
        parts = path.parts
        if root is not None:
            try:
                parts = path.relative_to(root).parts
            except ValueError:
                pass
    else:
        parts = tuple(str(path).replace("\\", "/").split("/"))
    if not parts:
        return False
    name = parts[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    if any(part in _TEST_DIR_NAMES for part in parts[:-1]):
        return True
    return any(fnmatch(name, pattern) for pattern in extra_test_globs)


def _iter_py_files(root: Path, exclude_dirs: frozenset[str]) -> Iterable[Path]:
    """Yield every ``.py`` file under ``root`` in a stable, sorted-by-path order, skipping
    files whose path BELOW ``root`` has a component matching ``exclude_dirs``. Where the root
    itself lives is never a reason to skip anything - see the comment on the check below.

    ``Path.rglob`` iteration order is filesystem-dependent (not guaranteed, and differs
    between platforms/filesystems in practice) -- scanners that compare files pairwise
    (e.g. duplicate_function_body, near_duplicate_function_body) pick whichever file they
    see first as the "reference" and flag the other as the duplicate. Without a stable
    order, which file gets flagged is nondeterministic across machines/CI runners, breaking
    reproducible findings and any test asserting on which file a finding names.
    """
    # A PRUNED ``os.walk``, not ``root.rglob("*")`` + ``p.is_file()`` + a per-file ``_is_excluded``.
    # The old shape descended INTO every excluded directory and rejected its files one at a time,
    # each rejection paying a ``path.resolve()`` -- so a repo carrying nested ``.claude`` worktrees
    # (each a complete checkout) resolved every file of every checkout only to throw it away. This
    # walk never enters them, and everything ``os.walk`` yields is already below ``root``, so no
    # ``resolve()`` is needed to decide exclusion at all. Repeated once per scanner, ~74 times per
    # ``run_all()``: 0.173 s -> 0.006 s over src/pyutilz's 242 files, same 242 paths in the same
    # order. Deliberately NOT memoised across calls: a scanner's own test writes a file into a tmp
    # tree and rescans within one process, and a stale file list would silently miss it.
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        candidates.extend(Path(dirpath) / name for name in filenames if name.endswith(_PY_EXTS))
    candidates.sort(key=lambda p: p.as_posix())
    yield from candidates


def _safe_parse(path: Path) -> Optional[ast.Module]:
    """The tree of ``path`` from the one process-wide parse cache (``_core.source.parse_source``), or None when the file cannot be read, decoded or parsed.

    Decoding is the interpreter's own (BOM, PEP 263 cookie), so a file saved with a BOM parses here exactly as it does for every other gate; this layer used to keep a second cache
    keyed on (path, mtime_ns, size) and read plain UTF-8, which could disagree with ``_core`` about whether a file parses and parsed every file twice per run.
    """
    try:
        return parse_file(path)
    except (SourceReadError, SourceParseError) as exc:
        logger.debug("_safe_parse: %s skipped by the code audit: %s", path, exc)
        return None
    except (RecursionError, MemoryError) as exc:
        logger.warning("_safe_parse: %s skipped, parser resources exhausted: %s", path, type(exc).__name__)
        # a syntactically VALID file with a deeply nested expression (a generated parser table, a long `a + a + ...`) blows the recursion limit in ast.parse; one input's parser-resource
        # failure is handled like an unparseable file instead of killing the whole run (and, under the process pool, an opaque worker death naming no file).
        return None


def walk_tree(tree: ast.AST) -> list[ast.AST]:
    """``list(ast.walk(tree))``, computed once per tree and shared with every other gate (``_core.node_index``); callers must not mutate the list."""
    return list(walk_nodes(tree))


def _read_src_lines(path: Path) -> list[str]:
    """Read ``path`` and split it into lines the way CPython's tokenizer numbers them.

    ``str.splitlines()`` also breaks on ``\x0b``, ``\x0c`` (form feed - a conventional Python
    section separator), ``\x1c``-``\x1e``, ``\x85``, ``\u2028`` and ``\u2029``, none of which
    advance ``lineno``. Using it shifts every snippet after such a character by one line per
    occurrence while ``Finding.line`` stays right, so the report cites the wrong source text.
    Returns ``[]`` if the file cannot be read.

    Memoised per (path, mtime_ns, size), and for the same reason as the parse cache: 77 of
    the scanner modules call this, so one ``run_all()`` re-read and re-split every file of the
    corpus once per scanner -- I/O and decoding the process had already done dozens of times over.
    The returned list is shared between callers; scanners only ever index into it (via
    ``_line_text``), never mutate it.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        logger.warning("_read_src_lines: stat failed, file skipped by the code audit: %s: %s", type(exc).__name__, exc)
        return []
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        try:
            cached = _SRC_LINES_CACHE[cache_key]
        except KeyError:
            pass
        else:
            _SRC_LINES_CACHE.move_to_end(cache_key)
            return cached
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("_read_src_lines: read failed, file skipped by the code audit: %s: %s", type(exc).__name__, exc)
        return []
    lines = split_src_lines(src)
    with _CACHE_LOCK:
        _SRC_LINES_CACHE[cache_key] = lines
        while len(_SRC_LINES_CACHE) > _PARSE_CACHE_MAX_ENTRIES:
            _SRC_LINES_CACHE.popitem(last=False)
    return lines


def split_src_lines(src: str) -> list[str]:
    """Split already-read source text on the line terminators the tokenizer counts (``\n`` / ``\r\n`` / ``\r``)."""
    return src.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _line_text(src_lines: list[str], lineno: int) -> str:
    """Return the stripped text of 1-based line ``lineno`` from ``src_lines``, or ``""`` if out of range."""
    if 1 <= lineno <= len(src_lines):
        return src_lines[lineno - 1].strip()
    return ""


# Inline exemption marker: ``# audit-exempt: <check_id> -- <reason>`` on the finding's own line.
# The reason is mandatory and must be non-empty; a marker without one is not an exemption.
_EXEMPT_RE = re.compile(r"#\s*audit-exempt:\s*(?P<check>[A-Za-z0-9_]+)\s*--\s*(?P<reason>\S.{18,}?)\s*$")


def is_line_exempt(src_lines: list[str], lineno: int, check: str) -> bool:
    """Whether 1-based line ``lineno`` carries a valid ``audit-exempt`` marker naming ``check``.

    A marker for a different check does not suppress, and a marker whose reason is empty or missing
    does not suppress either. Scanners call this on the line they would report, so the exemption
    lives beside the code it excuses and survives edits elsewhere in the file.
    """
    if not 1 <= lineno <= len(src_lines):
        return False
    match = _EXEMPT_RE.search(src_lines[lineno - 1])
    return match is not None and match.group("check") == check


def _arg_names(func_node: ast.AST) -> list[str]:
    """Extract every parameter name from a FunctionDef / AsyncFunctionDef."""
    args: list[str] = []
    if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return args
    a = func_node.args
    args.extend(arg.arg for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs))
    if a.vararg:
        args.append(a.vararg.arg)
    if a.kwarg:
        args.append(a.kwarg.arg)
    return args


# An f-string interpolation renders as this in recovered SQL, never as nothing. Dropping it
# splices the text on either side together: `f"SELECT COUNT(*) FROM {table} WHERE ..."` collapsed
# to "... FROM  WHERE ...", and a scanner read the table name as `where`. `?` cannot start an
# identifier, so a pattern declines the query instead of misreading it.
_SQL_INTERPOLATION = "?"


def _string_constants_in_body(body: Iterable[ast.stmt]) -> dict[str, str]:
    """Names bound to a plain string literal by a direct statement of ``body``."""
    out: dict[str, str] = {}
    for stmt in body:
        targets = list(stmt.targets) if isinstance(stmt, ast.Assign) else ([stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
        value = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
        for target in targets:
            if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                out[target.id] = value.value
    return out


def _module_sql_constants(tree: ast.Module) -> dict[str, str]:
    """Names bound to a string literal, for scanners that follow SQL held in a constant.

    Covers module level AND class bodies - keeping SQL in a ``class Queries:`` is the common
    alternative layout, and scanning only ``tree.body`` left every constant-resolving scanner
    silent on such a repository. A class constant is recorded under both its qualified
    ``Queries.SELECT_X`` name (how call sites spell it) and its bare name; module level wins
    any collision.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for name, text in _string_constants_in_body(node.body).items():
                out[f"{node.name}.{name}"] = text
                out.setdefault(name, text)
    out.update(_string_constants_in_body(tree.body))
    return out


def _sql_text(node: ast.expr, constants: dict[str, str]) -> "Optional[str]":
    """The SQL an argument carries: a literal, an f-string, a concatenation, or a module constant.

    Shared rather than copied, because two scanners had drifting copies of it and this package's
    own duplicate_function_body check said so.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return constants.get(f"{node.value.id}.{node.attr}")
    if isinstance(node, ast.JoinedStr):
        return "".join(value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else _SQL_INTERPOLATION for value in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_text(node.left, constants)
        right = _sql_text(node.right, constants)
        if left is not None and right is not None:
            return left + right
    return None


def _subscript_index(node: ast.Subscript) -> ast.expr:
    """The expression inside ``obj[...]``, on every supported python.

    Up to 3.8 the parser wrapped a plain subscript in ``ast.Index``
    (``Subscript(slice=Index(value=Constant("k")))``); 3.9 removed the wrapper and stores the
    expression on ``.slice`` directly (bpo-34822). A scanner that reads ``.slice`` and expects a
    ``Constant`` therefore matched nothing at all on 3.8 -- no error, just a rule that quietly
    stopped firing. Shared so every scanner unwraps the same way instead of each rediscovering it.
    """
    index = node.slice
    if index.__class__.__name__ == "Index":  # ast.Index exists only pre-3.9
        return getattr(index, "value", index)  # type: ignore[no-any-return]
    return index


def _dotted_module_path(rel: str) -> str:
    """The dotted module path a repo-relative file corresponds to.

    Shared so `spy_arity` and `patch_target_is_a_reexport` (which both resolve a scanned file
    back to the module name a patch target / call site would spell) agree on the mapping instead
    of carrying copies that can drift apart.
    """
    stem = rel[:-3] if rel.endswith(".py") else rel
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def _module_aliases(tree: ast.Module) -> "dict[str, str]":
    """Local name -> dotted MODULE path, from this file's own ``import`` statements.

    Both `import x.y as z` and `from x.y import z` bind a local name that a later dotted
    expression has to be resolved through; shared by every scanner that needs to turn a local
    name back into what it actually refers to.
    """
    aliases: "dict[str, str]" = {}
    for node in walk_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _own_nodes(func: ast.AST) -> "Iterator[ast.AST]":
    """Walk ``func``'s own body without descending into a nested ``def``/``lambda`` scope.

    A nested function is its own function, visited in its own right: descending into it both
    double-reports every finding inside it and leaks names out of the nested scope into the
    enclosing one, so an unrelated same-named outer variable gets flagged.
    """
    todo: "list[ast.AST]" = child_nodes(func)
    while todo:
        node = todo.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        todo.extend(child_nodes(node))


_SQL_FROM = re.compile(r"\bFROM\s+([A-Za-z_][\w.\"]*)", re.I)


def _sql_table_of(sql: str) -> "Optional[str]":
    """The table a statement reads, as written after FROM. Shared: two scanners had copies of it."""
    match = _SQL_FROM.search(sql)
    return match.group(1).strip('"').lower() if match else None
