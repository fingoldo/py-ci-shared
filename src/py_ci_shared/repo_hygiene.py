"""Shared check: the repository tracks nothing it generates, and carries the files its gates need.

Small, boring rules that each cost one line to satisfy and are invisible until they bite:

1. **No generated artefacts tracked.** ``__pycache__/``, ``*.pyc``, ``.dart_tool/``, ``node_modules/``
   and coverage output in ``git ls-files`` mean every contributor's byte-compiled cache lands in
   diffs and merge conflicts. Found on 2026-09-02 as flutter_app_core C03-18 (two ``__pycache__``
   files committed alongside the tooling that generates them).

2. **Required files exist.** A repo-specific list: a linter config (``analysis_options.yaml`` for
   Dart, ``pyproject.toml`` for Python), a non-empty ``.github/workflows/``, a ``.gitignore``.
   flutter_app_core C03-2 was exactly this: the package had no ``analysis_options.yaml``, so
   ``flutter analyze`` ran with the SDK defaults and the lint set the consuming app enforced was
   simply absent from the package everything else depends on.

3. **A numeric CI gate cannot pass on an empty value.** ``if (( $(echo "$COV < 80" | bc) ))``
   evaluates to *pass* when ``$COV`` is empty because the parse failed -- the gate reports success
   precisely when its input broke. Require an explicit emptiness check (``[ -n "$VAR" ]``,
   ``: "${VAR:?}"``) in any block that compares a shell variable numerically. Found as glossum
   P04-5, where a coverage percentage that failed to parse read as "coverage fine".

Deliberately dependency-free (``git ls-files`` via subprocess, regex over workflow text), and
language-agnostic: rules 1 and 3 fire on any repo, rule 2 takes the caller's own list.

Usage::

    from py_ci_shared.repo_hygiene import assert_repo_hygiene

    def test_repo_hygiene():
        assert_repo_hygiene(
            REPO,
            required_files=("analysis_options.yaml", ".gitignore"),
            workflows_dir=REPO / ".github" / "workflows",
        )
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

_DEFAULT_GENERATED_PATTERNS: tuple[str, ...] = (
    "__pycache__/",
    ".pyc",
    ".dart_tool/",
    "node_modules/",
    ".pytest_cache/",
    "/build/",
    ".coverage",
    "coverage/lcov.info",
)
_RUN_LINE_RE = re.compile(r"^\s*(?:-\s*)?run:\s*(?P<first>.*)$")
# A numeric comparison of a shell variable: `$X < 80` inside bc, `[ "$X" -lt 80 ]`, `(( X < 80 ))`.
_NUMERIC_COMPARE_RE = re.compile(
    r"""(?:\$\{?(?P<bc>\w+)\}?\s*(?:<|>|<=|>=)\s*[\d.]+)"""
    r"""|(?:\[\s*[\"']?\$\{?(?P<test>\w+)\}?[\"']?\s+-(?:lt|gt|le|ge|eq|ne)\s+)"""
    r"""|(?:\(\(\s*\$?\{?(?P<arith>\w+)\}?\s*(?:<|>|<=|>=)\s*[\d.]+)"""
)
_EMPTINESS_GUARD_TMPL = (
    r"""\[\s*-n\s+[\"']?\$\{{?{var}\}}?[\"']?\s*\]"""
    r"""|\[\s*-z\s+[\"']?\$\{{?{var}\}}?[\"']?\s*\]"""
    r"""|:\s*[\"']?\$\{{{var}:[?-]"""
    r"""|\$\{{{var}:-"""
    r"""|if\s+\[\s*[\"']?\$\{{?{var}\}}?[\"']?\s*=\s*[\"']{{2}}"""
)


def _tracked_files(repo_root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - environment
        raise RuntimeError(f"git ls-files failed in {repo_root}: {exc}") from exc
    return [line for line in out.splitlines() if line]


def find_tracked_generated_files(
    repo_root: Path,
    patterns: Sequence[str] = _DEFAULT_GENERATED_PATTERNS,
) -> list[str]:
    """Return every tracked path containing one of ``patterns`` (a generated artefact)."""
    hits: list[str] = []
    for rel in _tracked_files(repo_root):
        normalized = "/" + rel.replace("\\", "/")
        for pattern in patterns:
            if pattern in normalized:
                hits.append(f"{rel} (matches {pattern!r})")
                break
    return hits


def find_missing_required_files(repo_root: Path, required_files: Iterable[str]) -> list[str]:
    """Return every entry of ``required_files`` that does not exist under ``repo_root``."""
    return [name for name in required_files if not (repo_root / name).exists()]


def find_unguarded_numeric_gates(workflows_dir: Path) -> list[str]:
    """Return one problem string per workflow ``run:`` block that compares a shell variable
    numerically without first proving the variable is non-empty."""
    problems: list[str] = []
    if not workflows_dir.is_dir():
        return problems
    for path in sorted(workflows_dir.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            m = _NUMERIC_COMPARE_RE.search(line)
            if not m:
                continue
            var = m.group("bc") or m.group("test") or m.group("arith")
            if not var or var.isdigit():
                continue
            # Look at the whole run block this line belongs to: the guard is usually a few lines up.
            start = i - 1
            while start > 0 and not _RUN_LINE_RE.match(lines[start - 1]):
                start -= 1
            block = "\n".join(lines[max(start - 1, 0) : i])
            if re.search(_EMPTINESS_GUARD_TMPL.format(var=re.escape(var)), block):
                continue
            problems.append(
                f"{path.name}:{i}: `{line.strip()[:90]}` compares ${var} numerically without "
                f"proving it is non-empty first. When the value fails to parse the comparison is "
                f"skipped or false, so the gate reports success exactly when its input broke. Add "
                f'`[ -n "${var}" ] || exit 1` (or `: "${{{var}:?}}"`) before the comparison.'
            )
    return problems


def assert_repo_hygiene(
    repo_root: Path,
    *,
    required_files: Iterable[str] = (),
    generated_patterns: Sequence[str] = _DEFAULT_GENERATED_PATTERNS,
    workflows_dir: "Path | None" = None,
) -> None:
    """Fail on tracked generated files, missing required files, or an unguarded numeric CI gate."""
    import pytest

    problems: list[str] = []
    tracked = find_tracked_generated_files(repo_root, generated_patterns)
    if tracked:
        problems.append(
            f"{len(tracked)} generated file(s) are tracked by git - they land in every diff and "
            f"conflict on every merge:\n    " + "\n    ".join(tracked[:20])
        )
    missing = find_missing_required_files(repo_root, required_files)
    if missing:
        problems.append("required file(s) missing: " + ", ".join(missing))
    if workflows_dir is not None:
        if workflows_dir.is_dir() and not list(workflows_dir.glob("*.y*ml")):
            problems.append(f"{workflows_dir} has no workflow files - this repo has no CI at all.")
        problems.extend(find_unguarded_numeric_gates(workflows_dir))
    if problems:
        pytest.fail(f"{len(problems)} repo hygiene problem(s):\n  " + "\n  ".join(problems))
