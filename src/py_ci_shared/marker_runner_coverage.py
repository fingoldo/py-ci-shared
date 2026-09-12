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
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_MARK = re.compile(r"pytest\.mark\.([A-Za-z_][A-Za-z0-9_]*)")
_DASH_M = re.compile(r"-m\s+(\"[^\"]+\"|'[^']+'|[A-Za-z_][A-Za-z0-9_]*)")
_TOKEN = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s|&;><]+")
#: A dependency-install line NAMES pytest without running it (`pip install pytest pytest-cov ...`).
#: Read as a runner it looks PATHLESS, which would mean "selects everything" and hide every real
#: finding behind it -- the same trap `gate_config_honesty._invokes` exists for.
_INSTALL = re.compile(r"\b(?:pip3?|uv|uvx|poetry|conda|pdm|hatch)\b[^\n]*\binstall\b|\binstall\b[^\n]*\bpytest\b")
_VALUE_TAKING = frozenset({"-m", "-k", "-p", "-n", "-o", "-c", "-W", "-r", "--deselect", "--ignore", "--ignore-glob"})


@dataclass(frozen=True)
class MarkedTest:
    """One test function (or one whole file, via ``pytestmark``) and the markers it carries."""

    file: str
    name: str
    markers: frozenset[str]

    @property
    def key(self) -> str:
        return f"{self.file}::{self.name}"


@dataclass(frozen=True)
class Runner:
    """One ``pytest`` invocation: where it came from, what paths it names, what it selects."""

    label: str
    paths: tuple[str, ...]
    expression: "str | None"

    @property
    def is_pathless(self) -> bool:
        return not self.paths


def marked_tests(tests_dir: Path, repo_root: Path, *, marker: str) -> list[MarkedTest]:
    """Every test carrying *marker*, as a file-level ``pytestmark`` or a decorator."""
    found: list[MarkedTest] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if marker not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = path.relative_to(repo_root).as_posix()
        module_markers: set[str] = set()
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in statement.targets):
                module_markers |= set(_MARK.findall(ast.unparse(statement.value)))
        if marker in module_markers:
            found.append(MarkedTest(rel, "<whole file>", frozenset(module_markers)))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test"):
                continue
            own = {m for d in node.decorator_list for m in _MARK.findall(ast.unparse(d))}
            if marker in own:
                found.append(MarkedTest(rel, node.name, frozenset(own | module_markers)))
    return found


def _paths_and_expression(command: str) -> tuple[tuple[str, ...], "str | None"]:
    tokens = _TOKEN.findall(command)
    paths: list[str] = []
    skip = False
    for tok in tokens:
        if skip:
            skip = False
            continue
        if tok in _VALUE_TAKING:
            skip = True
            continue
        if tok.startswith("-"):
            continue
        if "/" in tok or "\\" in tok or tok.endswith(".py") or "::" in tok:
            paths.append(tok.strip("\"'").replace("\\", "/").rstrip("/"))
    m = _DASH_M.search(command)
    return tuple(paths), (m.group(1).strip("\"'") if m else None)


def runners(commands: "Iterable[tuple[str, str]]", *, addopts: str = "") -> list[Runner]:
    """``(label, command)`` pairs -> runners, with *addopts* supplying a default ``-m``.

    pytest applies ``addopts`` to every run, and a command's own ``-m`` REPLACES the one addopts
    carries (the last ``-m`` on the line wins), which is why a project can deselect a marker by
    default and still have a job that asks for it.
    """
    _, default_expression = _paths_and_expression(f"pytest {addopts}")
    out: list[Runner] = []
    for label, command in commands:
        if "pytest" not in command:
            continue
        # Fold shell line-continuations first, as `ci_test_dir_reachability` does: an install step's
        # `pip install foo \` + newline + `  pytest pytest-cov` puts the word `pytest` on a line that
        # no longer carries `install`, so a per-line rule reads the continuation as its own command.
        for line in re.sub(r"\\\s*\n\s*", " ", command).splitlines():
            if _INSTALL.search(line):
                continue
            for part in re.findall(r"pytest\s[^\n]*", line):
                paths, expression = _paths_and_expression(part)
                out.append(Runner(label, paths, expression if expression is not None else default_expression))
    return out


def expression_selects(expression: "str | None", markers: Iterable[str]) -> bool:
    """Does pytest's ``-m`` *expression* select a test carrying exactly *markers*?

    Evaluated as the boolean expression pytest itself evaluates: ``and`` / ``or`` / ``not`` over
    marker names, where a name the test does not carry is False. An expression this cannot parse is
    treated as selecting, so a shape nobody here uses cannot invent a finding.
    """
    if not expression:
        return True
    held = set(markers)
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression)) - {"and", "or", "not"}
    try:
        return bool(eval(expression, {"__builtins__": {}}, {n: (n in held) for n in names}))
    except Exception:  # an unparsable expression must not manufacture a finding
        return True


def _reaches(runner: Runner, file: str) -> bool:
    if runner.is_pathless:
        return True
    return any(file == p or file.startswith(p.rstrip("/") + "/") or p.startswith(file) for p in runner.paths)


def find_unselected_marked_tests(
    tests_dir: Path,
    repo_root: Path,
    *,
    marker: str,
    commands: "Iterable[tuple[str, str]]",
    addopts: str = "",
) -> list[str]:
    """``<file>::<test>: <why>`` for every *marker*-carrying test no runner selects."""
    every = runners(commands, addopts=addopts)
    problems: list[str] = []
    for test in marked_tests(tests_dir, repo_root, marker=marker):
        reaching = [r for r in every if _reaches(r, test.file)]
        if any(expression_selects(r.expression, test.markers) for r in reaching):
            continue
        if reaching:
            why = "every runner that reaches it deselects its markers: " + ", ".join(sorted({f"{r.label} (-m {r.expression!r})" for r in reaching}))
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
    """Shrink-only against *known*: a new unselected test fails, and so does a *known* one now run."""
    import pytest

    assert commands, "no runner commands given -- this would report every marked test as unrun"
    total = len(marked_tests(tests_dir, repo_root, marker=marker))
    if total < min_marked:
        pytest.fail(f"only {total} test(s) carry `{marker}`; expected at least {min_marked} -- this would check nothing")
    found = set(find_unselected_marked_tests(tests_dir, repo_root, marker=marker, commands=commands, addopts=addopts))
    keys = {p.split(":", 1)[0].strip(): p for p in found}
    new, stale = sorted(set(keys) - set(known)), sorted(set(known) - set(keys))
    if new or stale:
        pytest.fail(
            (
                f"{len(new)} `{marker}` test(s) that no runner selects, so they never run -- name them in a hook or a "
                "workflow, or give them the marks the runner asks for:\n  " + "\n  ".join(keys[k] for k in new)
                if new
                else ""
            )
            + (f"\n{len(stale)} accepted entr(ies) now selected -- remove them:\n  " + "\n  ".join(stale) if stale else "")
        )
