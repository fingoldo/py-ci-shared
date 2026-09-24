"""Shared check: a test carrying a marker must be SELECTED by some runner, or it never runs.

WHERE THIS CAME FROM
---------------------
2026-09-12, the ``new_scraper`` monorepo, found while wiring an integration hook for a second
package. Four tests across two packages were selected by nothing at all:

* ``realtime_applications`` deselects the marker by default (``addopts = "... -m 'not integration'"``),
  its nightly workflow runs ``pytest -m "slow and integration"``, and its pre-push hook names four
  files by path. ``tests/integration/test_idf_queries_run_against_postgres.py`` and
  ``test_client_info_batch_uses_the_partial_index.py`` carry ``integration`` WITHOUT ``slow``, so the
  default run drops them, the nightly expression does not match them, and the hook does not name
  them. Both pass in ~2 minutes against the real database, and both exist because the queries they
  cover were broken in production while 4,328 green tests said otherwise.
* ``production_scrapers`` runs its unit job as ``pytest tests/ -m "not integration"`` and its
  integration job as ``pytest tests/integration/ -m integration``. Two ``integration``-marked tests
  live OUTSIDE that directory (a real-``gql()``-over-loopback request, and the network guard's own
  exemption self-test), so the unit job deselects them and the integration job's path argument
  never reaches them.

This is the same defect the 2026-09-03 round found as "three integration files that had never
executed", and the same shape as ``ci_test_dir_reachability`` -- which cannot see it, because that
check works on DIRECTORIES and is blind to markers: a pathless ``pytest -m "slow and integration"``
makes ``tests/integration`` look reached while every file in it whose marks do not satisfy the
expression is dropped.

A marker is a promise that something runs the marked tier. This turns the promise into a claim the
tree answers: for each marked test, is there a runner whose path arguments reach its file AND whose
marker expression selects it?
"""

from __future__ import annotations

import ast
import posixpath
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._core import DEFAULT_EXCLUDE, ImportAliases, UnparsedFilesError, relative_posix, scan_python

_TOKEN = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s|&;><]+")
#: A dependency-install line NAMES pytest without running it (`pip install pytest pytest-cov ...`).
#: Read as a runner it looks PATHLESS, which would mean "selects everything" and hide every real
#: finding behind it -- the same trap `gate_config_honesty._invokes` exists for.
_INSTALL = re.compile(r"\b(?:pip3?|uv|uvx|poetry|conda|pdm|hatch)\b[^\n]*\binstall\b|\binstall\b[^\n]*\bpytest\b")
_VALUE_TAKING = frozenset(
    {
        "-m",
        "-k",
        "-p",
        "-n",
        "-o",
        "-c",
        "-W",
        "-r",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--cov",
        "--cov-report",
        "--cov-config",
        "--cov-fail-under",
        "--durations",
        "--maxfail",
        "--rootdir",
        "--basetemp",
        "--junitxml",
        "--junit-xml",
        "--timeout",
        "--tb",
        "--dist",
        "--confcutdir",
        "--override-ini",
        "--log-level",
        "--randomly-seed",
        "--reruns",
        "--splits",
        "--group",
        "--splitting-algorithm",
        "--durations-path",
        "--timeout-method",
    }
)
#: Where a shell command line ends and the next begins; the pytest invocation stops there.
_SHELL_BREAK = re.compile(r"&&|\|\||[;|<>]")
_PYTEST = re.compile(r"(?<![\w./\\-])pytest(?![\w./\\-])")
_CD = re.compile(r"\bcd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)\s*(?:&&|;)")
#: A GitHub Actions expression, spaces and all (`${{ matrix.group }}`): one opaque word, never three.
_ACTIONS_EXPR = re.compile(r"\$\{\{.*?\}\}")
#: A here-document: the introducing line, its delimiter and its body. The body is data for the command it feeds
#: (a Python script, a file), not shell lines, so a `pytest` word in it is not an invocation.
_HEREDOC = re.compile(r"^([^\n]*?)<<-?[ \t]*(['\"]?)(\w+)\2[^\n]*\n(.*?)^[ \t]*\3[ \t]*$", re.MULTILINE | re.DOTALL)
_PYTHON_CMD = re.compile(r"(?<![\w.-])python[\d.]*(?![\w.-])")
_WHOLE_FILE = "<whole file>"
_UNPARSED = "<unparsed>"


@dataclass(frozen=True)
class MarkedTest:
    """One test function (or one whole file, via ``pytestmark``) and the markers it carries.

    *name* is the pytest node path inside the file (``test_x`` or ``TestC::test_x``). A whole-file entry
    lists the tests it covers in *members*, so a node-id or ``-k`` runner is judged on every one of them.
    """

    file: str
    name: str
    markers: frozenset[str]
    members: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.file}::{self.name}"


@dataclass(frozen=True)
class Runner:
    """One ``pytest`` invocation: where it came from, what paths it names, what it selects.

    *paths* are relative to *cwd* (a ``cd dir &&`` before the command); *keyword* is its ``-k``.
    """

    label: str
    paths: tuple[str, ...]
    expression: "str | None"
    keyword: "str | None" = None
    cwd: str = ""

    @property
    def is_pathless(self) -> bool:
        return not self.paths


# ---------------------------------------------------------------------------------------------------------------------
# marker collection


def _markers_in(node: ast.AST, aliases: ImportAliases, named: "dict[str, frozenset[str]]") -> set[str]:
    """Marker names applied by an expression: ``pytest.mark.x``, ``mark.x(...)``, aliases, lists of them, or a
    module-level name bound to one (``slow = pytest.mark.slow``)."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute):
            qualified = aliases.qualified_name(sub)
            if qualified and qualified.startswith("pytest.mark.") and qualified.count(".") == 2:
                out.add(qualified.split(".")[2])
        elif isinstance(sub, ast.Name) and sub.id in named:
            out |= named[sub.id]
    return out


def _pytestmark_value(statement: ast.stmt) -> "ast.expr | None":
    if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in statement.targets):
        return statement.value
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.target.id == "pytestmark":
        return statement.value
    return None


def _named_marks(tree: ast.Module, aliases: ImportAliases) -> "dict[str, frozenset[str]]":
    named: dict[str, frozenset[str]] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            marks = _markers_in(statement.value, aliases, named)
            if marks and statement.targets[0].id != "pytestmark":
                named[statement.targets[0].id] = frozenset(marks)
    return named


def _tests_in(
    body: "list[ast.stmt]", prefix: str, inherited: "frozenset[str]", aliases: ImportAliases, named: "dict[str, frozenset[str]]"
) -> "list[tuple[str, frozenset[str]]]":
    """``(node path, markers)`` for every test function in *body*, recursing into ``Test*`` classes only."""
    out: list[tuple[str, frozenset[str]]] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            own = {m for d in node.decorator_list for m in _markers_in(d, aliases, named)}
            out.append((prefix + node.name, frozenset(own) | inherited))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            marks = {m for d in node.decorator_list for m in _markers_in(d, aliases, named)}
            for statement in node.body:
                value = _pytestmark_value(statement)
                if value is not None:
                    marks |= _markers_in(value, aliases, named)
            out.extend(_tests_in(node.body, f"{prefix}{node.name}::", inherited | frozenset(marks), aliases, named))
    return out


def _collect(tests_dir: Path, repo_root: Path, marker: str) -> "tuple[list[MarkedTest], list[str]]":
    scan = scan_python(tests_dir, min_files=0, patterns=("test_*.py", "*_test.py"), exclude=DEFAULT_EXCLUDE)
    found: list[MarkedTest] = []
    for parsed in scan:
        rel = relative_posix(parsed.path, repo_root)
        aliases = ImportAliases.from_tree(parsed.tree)
        named = _named_marks(parsed.tree, aliases)
        module_markers: set[str] = set()
        for statement in parsed.tree.body:
            value = _pytestmark_value(statement)
            if value is not None:
                module_markers |= _markers_in(value, aliases, named)
        tests = _tests_in(parsed.tree.body, "", frozenset(module_markers), aliases, named)
        if marker in module_markers:
            found.append(MarkedTest(rel, _WHOLE_FILE, frozenset(module_markers), tuple(name for name, _ in tests)))
            continue
        found.extend(MarkedTest(rel, name, markers) for name, markers in tests if marker in markers)
    problems = [f"{relative_posix(p.path, repo_root)}::{_UNPARSED}: {p.kind} at line {p.line} ({p.message}), so its markers are unknown" for p in scan.unparsed]
    return found, problems


def marked_tests(tests_dir: Path, repo_root: Path, *, marker: str) -> list[MarkedTest]:
    """Every test carrying *marker*, as a module or class ``pytestmark``, or a class or function decorator.

    Raises :class:`py_ci_shared._core.UnparsedFilesError` when a test file cannot be read or parsed: its markers
    are unknown, and silently dropping it would hide exactly the tests this check exists for.
    """
    found, problems = _collect(tests_dir, repo_root, marker)
    if problems:
        raise UnparsedFilesError("test file(s) could not be parsed:\n  " + "\n  ".join(problems))
    return found


# ---------------------------------------------------------------------------------------------------------------------
# command parsing


def _option_value(tokens: "list[str]", i: int, short: str) -> "tuple[str | None, int]":
    """Value of the short option *short* at ``tokens[i]`` (``-m x``, ``-m=x``, ``-mx``) and how many tokens it used."""
    tok = tokens[i]
    if tok == short:
        return (tokens[i + 1] if i + 1 < len(tokens) else None), 2
    if tok.startswith(short + "="):
        return tok[len(short) + 1 :], 1
    return tok[len(short) :], 1


def _normalise(path: str) -> str:
    cleaned = path.strip("\"'").replace("\\", "/")
    node = ""
    if "::" in cleaned:
        cleaned, node = cleaned.split("::", 1)
    cleaned = posixpath.normpath(cleaned) if cleaned else "."
    return f"{cleaned}::{node}" if node else cleaned


def _path_argument(tok: str) -> "str | None":
    """A positional argument as a path, or None. A shell variable or Actions expression is not a path: its value is
    unknown, so it narrows nothing (``$SHARD_GROUP``); a node id whose test part is one keeps its file (``f.py::$t``)."""
    if "$" not in tok:
        return tok
    head = tok.split("$", 1)[0]
    if head.endswith("::") and len(head) > 2:
        return head[:-2]
    return None


def _parse_invocation(command: str) -> "tuple[tuple[str, ...], str | None, str | None]":
    """``(paths, -m expression, -k expression)`` of one ``pytest ...`` invocation. The LAST ``-m``/``-k`` wins."""
    command = _ACTIONS_EXPR.sub("$ACTIONS_EXPR", command)
    command = _SHELL_BREAK.split(command, 1)[0]
    tokens = [t.strip("\"'") if t[:1] in "\"'" else t for t in _TOKEN.findall(command)]
    if tokens and _PYTEST.fullmatch(tokens[0]):
        tokens = tokens[1:]
    paths: list[str] = []
    expression: Optional[str] = None
    keyword: Optional[str] = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-m" or (tok.startswith("-m") and not tok.startswith("--")):
            expression, used = _option_value(tokens, i, "-m")
            i += used
            continue
        if tok == "-k" or (tok.startswith("-k") and not tok.startswith("--")):
            keyword, used = _option_value(tokens, i, "-k")
            i += used
            continue
        if tok in _VALUE_TAKING:
            i += 2
            continue
        i += 1
        if tok.startswith("-") or tok.isdigit():
            continue
        path = _path_argument(tok.rstrip("\"'"))
        if path:
            paths.append(_normalise(path))
    return tuple(paths), expression, keyword


def _paths_and_expression(command: str) -> "tuple[tuple[str, ...], str | None]":
    paths, expression, _ = _parse_invocation(command)
    return paths, expression


def _python_invocations(source: str) -> "list[str]":
    """``pytest ...`` command lines a Python script runs as an argument list
    (``subprocess.call([sys.executable, "-m", "pytest", "-m", "gpu", ...])``): the string elements after the ``pytest``
    element, each quoted as one word. Elements that are not string literals are unknown and left out."""
    import shlex

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        words = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None for e in node.elts]
        start = next((i for i, w in enumerate(words) if w is not None and _PYTEST.fullmatch(w)), None)
        if start is None:
            continue
        out.append("pytest " + " ".join(shlex.quote(w) for w in words[start + 1 :] if w is not None))
    return out


def _shell_lines(command: str) -> "list[str]":
    """The command's shell lines with here-document bodies taken out; a body fed to ``python`` contributes the pytest
    invocations its argument lists spell, as extra lines."""
    extra: list[str] = []

    def lift(match: "re.Match[str]") -> str:
        if _PYTHON_CMD.search(match.group(1)):
            extra.extend(_python_invocations(match.group(4)))
        return match.group(1)

    text = _HEREDOC.sub(lift, command)
    return [*re.sub(r"\\s*\n\s*", " ", text).splitlines(), *extra]


def runners(commands: "Iterable[tuple[str, str]]", *, addopts: str = "") -> list[Runner]:
    """``(label, command)`` pairs -> runners, with *addopts* supplying a default ``-m``/``-k``.

    pytest applies ``addopts`` to every run, and a command's own ``-m`` REPLACES the one addopts
    carries (the last ``-m`` on the line wins), which is why a project can deselect a marker by
    default and still have a job that asks for it. Every non-option argument is a path (``pytest tests``
    included), and a ``cd dir &&`` before the command makes its paths relative to ``dir``.
    """
    _, default_expression, default_keyword = _parse_invocation(f"pytest {addopts}")
    out: list[Runner] = []
    for label, command in commands:
        if "pytest" not in command:
            continue
        # Fold shell line-continuations first, as `ci_test_dir_reachability` does: an install step's
        # `pip install foo \` + newline + `  pytest pytest-cov` puts the word `pytest` on a line that
        # no longer carries `install`, so a per-line rule reads the continuation as its own command.
        for line in _shell_lines(command):
            if _INSTALL.search(line):
                continue
            for match in _PYTEST.finditer(line):
                paths, expression, keyword = _parse_invocation(line[match.start() :])
                cds = [m.group(1).strip("\"'") for m in _CD.finditer(line[: match.start()])]
                out.append(
                    Runner(
                        label,
                        paths,
                        expression if expression is not None else default_expression,
                        keyword if keyword is not None else default_keyword,
                        _normalise(cds[-1]) if cds else "",
                    )
                )
    return out


# ---------------------------------------------------------------------------------------------------------------------
# expression evaluation

_EXPR_TOKEN = re.compile(r"\s*(\(|\)|[A-Za-z_][\w.\-\[\]]*|\S)")


class _BoolExpr:
    """pytest's ``-m``/``-k`` grammar: ``or`` < ``and`` < ``not`` < ``( expr )`` | identifier."""

    def __init__(self, text: str) -> None:
        self.tokens = [t for t in _EXPR_TOKEN.findall(text) if t]
        self.pos = 0
        self.text = text

    def parse(self) -> "tuple":
        if not self.tokens:
            raise ValueError(f"empty expression {self.text!r}")
        tree = self._or()
        if self.pos != len(self.tokens):
            raise ValueError(f"unexpected {self.tokens[self.pos]!r} in {self.text!r}")
        return tree

    def _peek(self) -> "str | None":
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _or(self) -> "tuple":
        left = self._and()
        while self._peek() == "or":
            self.pos += 1
            left = ("or", left, self._and())
        return left

    def _and(self) -> "tuple":
        left = self._not()
        while self._peek() == "and":
            self.pos += 1
            left = ("and", left, self._not())
        return left

    def _not(self) -> "tuple":
        if self._peek() == "not":
            self.pos += 1
            return ("not", self._not())
        return self._atom()

    def _atom(self) -> "tuple":
        tok = self._peek()
        if tok is None:
            raise ValueError(f"expression {self.text!r} ends early")
        self.pos += 1
        if tok == "(":
            inner = self._or()
            if self._peek() != ")":
                raise ValueError(f"unbalanced parenthesis in {self.text!r}")
            self.pos += 1
            return inner
        if tok in ("and", "or", ")") or not re.match(r"[A-Za-z_]", tok):
            raise ValueError(f"unexpected {tok!r} in {self.text!r}")
        return ("id", tok)


def _evaluate(tree: "tuple", holds: "object") -> bool:
    op = tree[0]
    if op == "id":
        return bool(holds(tree[1]))  # type: ignore[operator]
    if op == "not":
        return not _evaluate(tree[1], holds)
    if op == "and":
        return _evaluate(tree[1], holds) and _evaluate(tree[2], holds)
    return _evaluate(tree[1], holds) or _evaluate(tree[2], holds)


def expression_selects(expression: "str | None", markers: Iterable[str]) -> bool:
    """Does pytest's ``-m`` *expression* select a test carrying exactly *markers*?

    Parsed with pytest's grammar (``and`` / ``or`` / ``not`` / parentheses over marker names; a name the test
    does not carry is False). Raises ``ValueError`` on an expression pytest would reject too: reading it as
    "selects" would hide every test behind a typo such as ``slow andd integration``.
    """
    if not expression:
        return True
    held = set(markers)
    return _evaluate(_BoolExpr(expression).parse(), lambda name: name in held)


def keyword_selects(keyword: "str | None", names: Iterable[str]) -> bool:
    """Does pytest's ``-k`` *keyword* select a node whose own and parent names (and markers) are *names*?

    Like pytest, an identifier matches when it is a case-insensitive SUBSTRING of any of those names.
    Raises ``ValueError`` on an unparsable expression.
    """
    if not keyword:
        return True
    lowered = [n.lower() for n in names]
    return _evaluate(_BoolExpr(keyword).parse(), lambda ident: any(ident.lower() in n for n in lowered))


# ---------------------------------------------------------------------------------------------------------------------
# reachability


def _anchored(runner: Runner, repo_root: "Path | None") -> "list[str]":
    """The runner's paths relative to the repo root. A ``cd`` into a directory that does not exist under the root
    means the command runs from an enclosing directory whose ``dir`` IS the root, so the cd is dropped."""
    cwd = runner.cwd
    if cwd and repo_root is not None and not (repo_root / cwd).is_dir():
        cwd = ""
    paths = runner.paths or (".",)
    return [_normalise(posixpath.join(cwd, p)) if cwd else p for p in paths]


def _node_reached(path: str, file: str, name: str) -> bool:
    if "::" in path:
        node_file, node = path.split("::", 1)
        return node_file == file and (name == node or name.startswith(node + "::"))
    return path == "." or file == path or file.startswith(path + "/")


def _names_of(test: MarkedTest, name: str) -> "list[str]":
    return [*test.file.split("/"), *name.split("::"), *sorted(test.markers)]


def _selects(runner: Runner, test: MarkedTest, repo_root: "Path | None") -> "tuple[bool, bool]":
    """``(reaches, selects)``: does *runner* collect *test* (every member, for a whole file), and select it?"""
    paths = _anchored(runner, repo_root)
    members = test.members if test.name == _WHOLE_FILE and test.members else (test.name,)
    if not all(any(_node_reached(p, test.file, m) for p in paths) for m in members):
        return False, False
    if not expression_selects(runner.expression, test.markers):
        return True, False
    return True, all(keyword_selects(runner.keyword, _names_of(test, m)) for m in members)


def _reaches(runner: Runner, file: str) -> bool:
    return any(_node_reached(p.split("::", 1)[0], file, "") for p in _anchored(runner, None))


def find_unselected_marked_tests(
    tests_dir: Path,
    repo_root: Path,
    *,
    marker: str,
    commands: "Iterable[tuple[str, str]]",
    addopts: str = "",
) -> list[str]:
    """``<file>::<test>: <why>`` for every *marker*-carrying test no runner selects.

    Also reported: a test file that cannot be parsed (``<file>::<unparsed>``) and a runner whose ``-m``/``-k``
    expression pytest would reject (``<label>::<bad expression>``); such a runner selects nothing.
    """
    every = runners(commands, addopts=addopts)
    tests, problems = _collect(tests_dir, repo_root, marker)
    usable: list[Runner] = []
    for runner in every:
        try:
            expression_selects(runner.expression, ())
            keyword_selects(runner.keyword, ())
        except ValueError as exc:
            problems.append(f"{runner.label}::<bad expression>: {exc}; pytest rejects it, so this runner selects nothing")
            continue
        usable.append(runner)
    for test in tests:
        verdicts = [(r, *_selects(r, test, repo_root)) for r in usable]
        if any(selects for _, _, selects in verdicts):
            continue
        reaching = [r for r, reaches, _ in verdicts if reaches]
        if reaching:
            why = "every runner that reaches it deselects its markers: " + ", ".join(
                sorted({f"{r.label} (-m {r.expression!r}" + (f", -k {r.keyword!r})" if r.keyword else ")") for r in reaching})
            )
        else:
            why = "no runner names a path that reaches it"
        problems.append(f"{test.key}: carries `{marker}` and {why}")
    return sorted(problems)


def assert_every_marked_test_is_selected(
    tests_dir: Path,
    repo_root: Path,
    *,
    marker: str,
    commands: "Sequence[tuple[str, str]]",
    addopts: str = "",
    known: Iterable[str] = (),
    min_marked: int = 1,
) -> None:
    """Shrink-only against *known* (``<file>::<test>`` keys, as reported): a new unselected test fails, and so
    does a *known* one now run. A bare-file key no longer excuses every test in that file; it is reported stale."""
    import pytest

    assert commands, "no runner commands given -- this would report every marked test as unrun"
    total = len(_collect(tests_dir, repo_root, marker)[0])
    if total < min_marked:
        pytest.fail(f"only {total} test(s) carry `{marker}`; expected at least {min_marked} -- this would check nothing")
    found = set(find_unselected_marked_tests(tests_dir, repo_root, marker=marker, commands=commands, addopts=addopts))
    keys = {p.split(": ", 1)[0].strip(): p for p in found}
    known = set(known)
    new, stale = sorted(set(keys) - known), sorted(known - set(keys))
    if new or stale:
        legacy = [k for k in stale if "::" not in k]
        pytest.fail(
            (
                f"{len(new)} `{marker}` test(s) that no runner selects, so they never run -- name them in a hook or a "
                "workflow, or give them the marks the runner asks for:\n  " + "\n  ".join(keys[k] for k in new)
                if new
                else ""
            )
            + (f"\n{len(stale)} accepted entr(ies) now selected or not a test key -- remove them:\n  " + "\n  ".join(stale) if stale else "")
            + ("\n(file-level keys are no longer accepted: list each `<file>::<test>` the report prints instead)" if legacy else ""),
            pytrace=False,
        )
