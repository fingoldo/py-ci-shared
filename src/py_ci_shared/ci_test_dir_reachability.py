"""Shared check: every subdirectory under a consumer repo's ``tests/`` must be
reachable by at least one CI job -- or be explicitly whitelisted as
intentionally excluded (e.g. a paid-API "live" tier).

Generalizes a pattern first written directly in glossum_backend_scripts
(``tests/test_meta/test_all_test_dirs_reachable_in_ci.py``, 2026-07-23
audit-verification meta-test round): a new test subdirectory can be added
and never wired into any CI job's ``pytest`` invocation, so its tests only
ever run locally -- CI stays green while a whole test category silently
stops gating merges. Text-based (not a YAML parser), matching this
package's other ``ci_*``/``code_audit`` scanners' established convention.

What counts: a ``pytest`` COMMAND (at the start of a command, optionally behind
``python -m``, ``uv run``, ``coverage run -m``, env assignments and similar wrappers;
never ``pip install pytest`` or a step name), read one command at a time (YAML
comments stripped, shell line-continuations folded, ``;``/``&&``/``||``/``|`` split).
Each invocation is judged on its own: its positional paths reach a subdir only on a
path-segment boundary (``tests/unit_slow`` does not reach ``tests/unit``), a pathless
run reaches everything its OWN ``--ignore``s (both ``=`` and space forms) leave, and
an ignore in one job never hides a subdir another job runs.

Usage (in a consuming repo's test suite)::

    from pathlib import Path
    from py_ci_shared.ci_test_dir_reachability import assert_every_test_subdir_reachable

    def test_every_test_subdir_reachable_by_some_ci_job():
        assert_every_test_subdir_reachable(
            repo_root=Path(__file__).resolve().parents[2],
            workflows_dir=Path(__file__).resolve().parents[2] / ".github" / "workflows",
            intentionally_unreached={"live"},
        )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ._core import read_source

_PYTEST_INVOKE_RE = re.compile(r"pytest\s+([^\s|&><;]+)")
_PYTEST_IGNORE_RE = re.compile(r"--ignore(?:-glob)?=(\S+)")
# A pytest invocation with NO positional path argument runs whatever `testpaths`/rootdir
# resolves to -- i.e. the whole `tests/` tree -- so it covers every subdir just as a literal
# `pytest tests/` does. Without this, a repo whose CI runs `pytest -m "not gpu" --cov=...`
# (no path at all, the most common shape) was reported as reaching NO test subdir whatsoever,
# turning the check into a guaranteed false positive on exactly the repos it should pass.
_TOKEN_RE = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s|&;><]+")
# Short flags that consume the following token as their VALUE (`-m "not gpu"`), which must not
# be mistaken for a positional path. Long flags carry their value as `--flag=value` in most
# CI invocation shapes; the ones below are also read in their space-separated form.
_VALUE_TAKING_SHORT_FLAGS = frozenset({"-m", "-k", "-p", "-n", "-o", "-c", "-W", "-r", "--deselect", "--ignore", "--ignore-glob"})
_IGNORE_FLAGS = ("--ignore", "--ignore-glob")
# Separators between shell commands on one line.
_COMMAND_SPLIT_RE = re.compile(r"&&|\|\||;|\||\$\(|`")
# Tokens that may precede `pytest` in the same command without making it an argument of something else.
_PREFIX_WORDS = frozenset({"-", "run:", "exec", "time", "env", "sudo", "xvfb-run", "nice", "command"})
_RUNNERS = {"uv": "run", "poetry": "run", "pipenv": "run", "hatch": "run", "pdm": "run", "rye": "run", "pixi": "run"}
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"(?:^|[/\\])(?:python|python3|py|pypy3?)(?:[\d.]*)(?:\.exe)?$", re.IGNORECASE)
_PYTEST_WORD_RE = re.compile(r"(?:^|[/\\])(?:pytest|py\.test)(?:\.exe)?$", re.IGNORECASE)


def _strip_yaml_comment(line: str) -> str:
    """*line* without a trailing ``# comment`` (a ``#`` at the start or after whitespace, outside quotes)."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _all_ci_run_lines(workflows_dir: Path) -> str:
    if not workflows_dir.is_dir():
        return ""
    workflow_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    text = "\n".join("\n".join(_strip_yaml_comment(line) for line in read_source(wf).splitlines()) for wf in workflow_files)
    # Fold shell line-continuations so a multi-line `pytest ... \` + newline + `tests/foo` invocation is
    # analysed as the single command it is -- otherwise its first line looks pathless and its
    # continuation lines look like commands of their own.
    return re.sub(r"\\[ \t]*\n\s*", " ", text)


def _looks_like_a_path(token: str) -> bool:
    """True when a bare token could be a pytest target path rather than some flag's value.

    Deliberately shape-based rather than filesystem-based: the workflow may name a path that exists only in
    CI, and this check runs against the repo, not the runner. A test target is a directory, a module, or a
    node id -- all of which carry a separator, a ``.py``, or a ``::``. A bare ``10`` carries none.
    """
    return "/" in token or "\\" in token or token.endswith(".py") or "::" in token or token in (".", "tests")


def _norm_path(token: str) -> str:
    p = token.strip("\"'").replace("\\", "/")
    p = p.split("::", 1)[0]
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/") or "."


@dataclass
class PytestInvocation:
    """One ``pytest`` command: its positional targets and its own ignores, POSIX-normalised."""

    paths: list[str] = field(default_factory=list)
    ignores: list[str] = field(default_factory=list)

    def reaches(self, rel: str) -> bool:
        """True when this run collects the directory *rel* (e.g. ``tests/unit``)."""
        if any(_covers(ignored, rel) for ignored in self.ignores):
            return False
        if not self.paths:
            return True
        return any(_covers(target, rel) or _covers(rel, target) for target in self.paths)


def _covers(outer: str, inner: str) -> bool:
    """*outer* is *inner* or one of its ancestors, on segment boundaries (``*``/``**`` glob tails accepted)."""
    for tail in ("/**", "/*"):
        if outer.endswith(tail):
            outer = outer[: -len(tail)]
    return outer == "." or inner == outer or inner.startswith(outer + "/")


def _pytest_args(tokens: list[str]) -> Optional[list[str]]:
    """The tokens after ``pytest`` when *tokens* is a pytest COMMAND, else None."""
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if _PYTEST_WORD_RE.search(tok):
            return tokens[i + 1 :]
        if tok in _PREFIX_WORDS or _ENV_ASSIGN_RE.match(tok):
            i += 1
        elif tok == "timeout" and i + 1 < n:
            i += 2
        elif tok in _RUNNERS and i + 1 < n and tokens[i + 1] == _RUNNERS[tok]:
            i += 2
        elif _PYTHON_RE.search(tok) or tok == "coverage":
            # `python [-X opts] -m pytest`, `coverage run [--opts] -m pytest`
            j = i + 1
            while j < n and tokens[j] != "-m" and tokens[j].startswith("-"):
                j += 1
            if tok == "coverage" and j < n and tokens[j] == "run":
                j += 1
                while j < n and tokens[j] != "-m" and tokens[j].startswith("-"):
                    j += 1
            if j + 1 < n and tokens[j] == "-m" and _PYTEST_WORD_RE.search(tokens[j + 1]):
                return tokens[j + 2 :]
            return None
        else:
            return None
    return None


def pytest_invocations(ci_text: str) -> list[PytestInvocation]:
    """Every pytest command in (comment-stripped, continuation-folded) workflow text."""
    out: list[PytestInvocation] = []
    for line in ci_text.splitlines():
        for segment in _COMMAND_SPLIT_RE.split(line):
            args = _pytest_args(_TOKEN_RE.findall(segment))
            if args is None:
                continue
            inv = PytestInvocation()
            skip_next = False
            pending_ignore = False
            for tok in args:
                if skip_next:
                    skip_next = False
                    if pending_ignore:
                        inv.ignores.append(_norm_path(tok))
                        pending_ignore = False
                    continue
                if tok in _VALUE_TAKING_SHORT_FLAGS:
                    skip_next = True
                    pending_ignore = tok in _IGNORE_FLAGS
                    continue
                if tok.startswith(tuple(f + "=" for f in _IGNORE_FLAGS)):
                    inv.ignores.append(_norm_path(tok.split("=", 1)[1]))
                    continue
                if tok.startswith("-") or tok == "\\":
                    continue
                if not _looks_like_a_path(tok):
                    # A value belonging to a value-taking flag this module does not know about. Enumerating every
                    # plugin's flags is a losing game -- pytest-split alone contributes `--splits N` and
                    # `--group N`, and their bare numeric values were being read as positional test paths, which
                    # made a genuinely PATHLESS invocation look like a targeted one and reported every tests/
                    # subdir in the repo as unreached. What actually distinguishes a pytest path argument is that
                    # it is a path.
                    continue
                inv.paths.append(_norm_path(tok))
            out.append(inv)
    return out


def _has_pathless_pytest_invocation(ci_text: str) -> bool:
    """True if any ``pytest`` command in ``ci_text`` passes no positional path.

    Such a run collects from ``testpaths``/rootdir, so it reaches every ``tests/`` subdir.
    """
    return any(not inv.paths for inv in pytest_invocations(ci_text))


def _test_subdirs(repo_root: Path) -> set[str]:
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return set()
    return {p.name for p in tests_dir.iterdir() if p.is_dir() and p.name != "__pycache__" and not p.name.startswith(".")}


def find_unreachable_test_subdirs(
    repo_root: Path,
    workflows_dir: Path,
    intentionally_unreached: "set[str] | None" = None,
) -> list[str]:
    """Return every ``tests/<subdir>`` name that no single pytest invocation in any workflow collects,
    minus the caller's explicit whitelist."""
    intentionally_unreached = intentionally_unreached or set()
    invocations = pytest_invocations(_all_ci_run_lines(workflows_dir))
    return [d for d in sorted(_test_subdirs(repo_root) - intentionally_unreached) if not any(inv.reaches(f"tests/{d}") for inv in invocations)]


def assert_every_test_subdir_reachable(
    repo_root: Path,
    workflows_dir: Path,
    intentionally_unreached: "set[str] | None" = None,
) -> None:
    """Pytest-friendly assertion wrapper. Raises ``AssertionError`` (via
    ``pytest.fail``) listing every unreachable subdir, or does nothing if
    all are covered.
    """
    import pytest

    unreachable = find_unreachable_test_subdirs(repo_root, workflows_dir, intentionally_unreached)
    if unreachable:
        pytest.fail(
            f"{len(unreachable)} tests/<subdir> not invoked by any CI workflow and not in "
            f"intentionally_unreached: {unreachable} -- either add it to a workflow's pytest "
            f"invocation, or whitelist it explicitly with a reason if it's deliberately "
            f"excluded (e.g. a paid-API live tier)."
        )
