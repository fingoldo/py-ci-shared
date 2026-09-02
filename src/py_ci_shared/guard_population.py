"""Shared check: a guard script is actually looking at something.

The most expensive failure mode of a home-grown guard is not a false positive, it is a guard whose
file-selection command matches nothing. It prints its success line, the hook goes green, and the
rule it was written to enforce has not been checked since whatever renamed the directory.

Three real cases from the 2026-09-02 glossum round, all of which had been passing for weeks:

* ``check-supabase-timeouts.sh`` and ``check-repository-layering.sh`` selected files with a
  ``grep -rl`` for a pattern that no longer appeared anywhere (P04-2).
* ``check-dead-code.sh`` scanned a hardcoded list of three directories, so everything under
  ``lib/core/`` -- half the codebase, including the whole landing layer -- was never examined
  (P07-23).
* ``check-painter-size-guard.sh`` scanned per FILE rather than per ``paint()`` body, so a file
  with one guarded painter certified every other painter in it (P04-4).

The rule: run each guard's own population command and fail when it yields nothing, unless the
script says out loud that it is allowed to be empty (a self-assert such as ``examining nothing`` or
an explicit ``exit 0`` with a SKIPPED message). Guards are shell scripts everywhere, so this is
language-agnostic.

Usage::

    from py_ci_shared.guard_population import assert_guards_examine_something

    def test_no_guard_scans_an_empty_population():
        assert_guards_examine_something(REPO / "tool", REPO)
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

# The first file-SELECTING command in a guard: `grep -rl PATTERN DIR` (list the files to
# examine), `find DIR -name ...`, `ls DIR/*.dart`. Captured whole so it can be re-run verbatim.
#
# `grep -rn 'forbidden'` is deliberately NOT a selector: that shape searches for a violation, so
# empty output is the guard PASSING. Treating it as an empty population reported two healthy
# guards as broken, which is the same false-positive class this check exists to remove.
_POPULATION_RE = re.compile(r"^\s*(?:for\s+\w+\s+in\s+)?\$?\(?\s*((?:grep\s+-[a-zA-Z]*l[a-zA-Z]*\s[^\n|;)]*|find\s+[^\n|;)]*|ls\s+[^\n|;)]*))")
# A script that admits it may legitimately match nothing.
_SELF_ASSERT_RE = re.compile(
    r"examining nothing|examined nothing|SKIPPED|no files to check|population is empty",
    re.IGNORECASE,
)


_ASSIGNMENT_RE = re.compile(r"^\s*(\w+)=(?!\s)(\S.*)$")


def _population_command(text: str) -> "str | None":
    """The guard's first file-selection command, with the variable assignments it depends on.

    The command is re-run verbatim, so it has to carry its own context: nearly every guard here
    opens with ``root="$(cd "$(dirname "$0")/.." && pwd)"`` and then selects files under
    ``"$root/lib"``. Running that line alone expands ``$root`` to the empty string, ``find "/lib"``
    matches nothing, and this check reports a healthy guard as broken -- which is exactly the
    false-positive shape it exists to prevent in others. Assignments seen before the selection
    line are prepended, and ``root`` falls back to the repository directory.
    """
    assignments: list[str] = []
    lines = text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assign = _ASSIGNMENT_RE.match(line)
        if assign:
            # An assignment derived from the script's own location ($0, dirname) cannot be
            # replayed here - `bash -c` has no script path - so it is dropped and `root` is
            # seeded from the repository directory instead, which is what it would have been.
            if "$0" not in stripped and "dirname" not in stripped:
                assignments.append(stripped)
            continue
        m = _POPULATION_RE.match(line)
        if m:
            command = m.group(1).strip()
            # A guard's selection command routinely spans lines with a trailing backslash; taking
            # the first line alone leaves an unbalanced `\(` and matches nothing.
            idx = lines.index(line) if line in lines else -1
            while command.endswith("\\") and idx != -1 and idx + 1 < len(lines):
                idx += 1
                command = command[:-1].strip() + " " + lines[idx].strip()
            command = command.rstrip("\\").strip()
            # A command piped into something else keeps only the selection half.
            command = command.split("|")[0].strip()
            # After stripping continuations and pipes there may be nothing selective left (the
            # regex can latch onto an assignment that merely contains the word). Reporting that
            # as "matches nothing" would be a false positive of exactly the kind this check is
            # meant to remove from other guards.
            if not re.match(r"^(grep|find|ls)\b", command):
                return None
            prefix = "".join(f"{a}; " for a in assignments)
            return f'root="$PWD"; {prefix}{command}'
    return None


def find_guards_with_empty_population(
    tool_dir: Path,
    repo_root: Path,
    *,
    glob: str = "check*.sh",
    skip: Iterable[str] = (),
) -> list[str]:
    """Return one problem string per guard whose first file-selection command matches nothing.

    A guard containing a self-assert phrase (``examining nothing``, ``SKIPPED``) is exempt: it has
    already been taught to be loud about an empty population, which is the outcome this check wants.
    """
    skip_names = set(skip)
    problems: list[str] = []
    scripts = sorted(p for p in tool_dir.glob(glob) if p.is_file())
    if not scripts:
        return [f"{tool_dir}: no {glob} scripts found - this check examined nothing itself."]

    for path in scripts:
        if path.name in skip_names:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _SELF_ASSERT_RE.search(text):
            continue
        command = _population_command(text)
        if not command:
            continue
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode not in (0, 1):
            continue
        if not result.stdout.strip():
            problems.append(
                f"{path.name}: its file-selection command `{command[:90]}` matches nothing, so the "
                f"guard passes without examining a single file. Add a self-assert that fails on an "
                f"empty population, or fix the selection."
            )
    return problems


def assert_guards_examine_something(
    tool_dir: Path,
    repo_root: Path,
    *,
    glob: str = "check*.sh",
    skip: Iterable[str] = (),
) -> None:
    """Fail when a guard's population command matches nothing."""
    import pytest

    problems = find_guards_with_empty_population(tool_dir, repo_root, glob=glob, skip=skip)
    if problems:
        pytest.fail(f"{len(problems)} guard(s) examine an empty population:\n  " + "\n  ".join(problems))
