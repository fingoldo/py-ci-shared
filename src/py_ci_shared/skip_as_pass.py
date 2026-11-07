"""A script that prints SKIPPED and then exits 0 reports a pass. Opt-in ratchet over ``tool/``, ``tools/``, ``scripts/``, ``.githooks/`` and ``.github/``.

A guard that cannot run here (a missing tool, an absent credential, a platform it does not cover) should fail or exit
non-zero with its reason. Printing ``SKIPPED`` and exiting 0 lets the check go green while nothing was checked, and CI
shows the same green as a real pass. The rule is structural, not the word alone: a line that prints ``SKIPPED``
(``echo``, ``printf``, ``print``) is a finding when the same block also exits 0. In shell the block ends at ``fi``,
``esac``, ``done``, ``else``/``elif``, a closing ``}`` or the next function header; in Python it ends where the indentation
drops below the print. A ``return``, ``raise`` or non-zero exit ends the block first, so ``echo SKIPPED; exit 1`` and
``echo SKIPPED`` followed by ``fi`` and then ``exit 0`` are not findings.

Why a ratchet: a repo that already has such a script cannot adopt the rule without rewriting it first. The baseline
lists the existing ones, each with a note saying what the skip is and why it is acceptable. Only a NEW skip-as-pass fails.

Enable it in the consumer's ``pyproject.toml`` (it is NOT in the default set; a repo must name it)::

    [tool.py_ci_shared]
    enable = ["skip_as_pass"]

Record the existing findings once, by hand, in ``tool/meta/baselines/skip_as_pass.json``::

    {"accepted": {"tool/run_x.sh: echo \\"SKIPPED: no docker\\"": "docker is optional here; the report says so"}}

The key is ``<repo-relative path>: <normalized line text>``. Every entry needs a note. A ``*_dir`` keyword
(``baseline_dir``) is resolved against the repo root.

Shell is read by the block parser in :mod:`py_ci_shared._shell_blocks` (bodies of ``if``/``case``/loops/groups/functions, heredocs and
quotes), and a workflow's ``run:`` steps are separate scripts, so a ``SKIPPED`` in one step is never paired with the ``exit 0`` of the next.
A ``SKIPPED`` printed unconditionally at the top level of a script that already ran real commands is a progress message about an optional
step, not a guard that checked nothing: ``pytest; echo "SKIPPED stale cache"; exit 0`` is not a finding, while ``echo SKIPPED; exit 0``
as the first command, or a print inside an ``if``/``||`` guard, is (audit 41-F12).

Approximation: a ``SKIPPED`` printed by a helper that another file
exits from is not seen; a Python block whose exit sits at a shallower indent (after the ``if`` that holds the print) is
not seen either, by design of "same block".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ._core.source import indent_width
from ._shell_blocks import TOP_LEVEL, Statement, parse_shell, workflow_run_scripts
from ._text_ratchet import DEFAULT_BASELINE_DIR, enforce_findings, normalize_line, record_finding, text_scan

__all__ = ["SKIP_DIRS", "find_skip_as_pass", "assert_no_new_skip_as_pass"]

#: Top-level directories whose scripts are guards.
SKIP_DIRS = ("tool", "tools", "scripts", ".githooks", ".github")

# The printed message begins with the status word: a warning that merely contains "skipped" is not a skip marker.
_PRINTS_SKIPPED = re.compile(r"\b(?:echo|printf|print|puts|Write-Host|Write-Output)\b\s*\(?\s*(?:-[a-zA-Z]+\s+)?[\"']?(?i:skipped)\b")
# Zero by status: an explicit 0, or none at all (a bare `exit` returns the last command's status, `sys.exit()` and `SystemExit(None)` are 0).
_EXIT_ZERO = re.compile(
    r"(?<![\w.])(?:exit\s+0(?![\w.])|exit(?=\s*(?:;|&|\||#|$))|(?:sys\.)?(?:exit|quit)\(\s*(?:0|None)?\s*\)|raise\s+SystemExit(?:\(\s*(?:0|None)?\s*\))?(?![\w.(]))"
)
_PY_BLOCK_END = re.compile(r"^\s*(?:return|raise|continue|break)\b")
_SH_STOP = re.compile(r"^\s*(?:return|exit)\b")
# Commands that prepare or probe and check nothing: a script that only ran these before printing SKIPPED has checked nothing.
_PREAMBLE = frozenset(
    "set export unset local readonly declare typeset cd pushd popd source . trap shopt umask echo printf print puts true false : test [ [[ "
    "command which type hash exit return read shift getopts alias mkdir Write-Host Write-Output".split()
)


def _checks_ran_before(statements: "list[Statement]", index: int) -> bool:
    """True when an earlier statement of the script that is not a function definition ran a real command (audit 41-F12)."""
    return any(s.head not in _PREAMBLE and s.head != "" and not s.in_function for s in statements[:index])


def _statement_exits_zero(statements: "list[Statement]", index: int) -> bool:
    """The SKIPPED print at *index* is followed, in the same block, by an exit 0 before any return or other exit."""
    me = statements[index]
    if _EXIT_ZERO.search(me.text):
        return True
    for later in statements[index + 1 :]:
        if later.block != me.block:
            continue
        if _EXIT_ZERO.search(later.text):
            return True
        if _SH_STOP.match(later.text):
            return False
    return False


def _shell_skip_lines(statements: "list[Statement]") -> "list[Statement]":
    """The statements that print SKIPPED and then exit 0 in their block. A print at the top level of a script that already ran real
    commands is a progress message about an optional step, not a guard that checked nothing."""
    hits = []
    for index, st in enumerate(statements):
        if not _PRINTS_SKIPPED.search(st.text) or not _statement_exits_zero(statements, index):
            continue
        if st.block == TOP_LEVEL and not st.conditional and _checks_ran_before(statements, index):
            continue
        hits.append(st)
    return hits


def _python_block_exits_zero(lines: list[str], index: int) -> bool:
    if _EXIT_ZERO.search(lines[index]):
        return True
    depth = indent_width(lines[index])
    for line in lines[index + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if indent_width(line) < depth:
            return False
        if _EXIT_ZERO.search(line):
            return True
        if indent_width(line) == depth and _PY_BLOCK_END.match(line):
            return False
    return False


def _scan_script(rel: str, lines: "list[str]", offset: int, python: bool, found: dict[str, str]) -> None:
    if python:
        for index, line in enumerate(lines):
            if _PRINTS_SKIPPED.search(line) and _python_block_exits_zero(lines, index):
                _record(rel, line, offset + index, found)
        return
    for st in _shell_skip_lines(parse_shell(lines)):
        _record(rel, lines[st.line], offset + st.line, found)


def _record(rel: str, line: str, index: int, found: dict[str, str]) -> None:
    record_finding(found, f"{rel}: {normalize_line(line)}", f"line {index + 1} prints SKIPPED and its block exits 0, which reads as a pass")


def _skip_lines(rel: str, text: str, found: dict[str, str]) -> None:
    if rel.endswith((".yml", ".yaml")):
        for script in workflow_run_scripts(text):
            _scan_script(rel, script.lines, script.first_line, script.shell.startswith("python"), found)
        return
    _scan_script(rel, text.splitlines(), 0, rel.endswith(".py"), found)


def find_skip_as_pass(root: Union[str, Path]) -> dict[str, str]:
    """``{key: description}`` for every SKIPPED-then-exit-0 block under :data:`SKIP_DIRS` of *root*."""
    files, found = text_scan(root, ("*",), under=SKIP_DIRS)
    for file in files:
        _skip_lines(file.rel, file.text, found)
    return found


def assert_no_new_skip_as_pass(repo_root: Union[str, Path], *, baseline_dir: Optional[str] = None) -> None:
    """Fail on a SKIPPED-then-exit-0 block that the baseline does not accept (``tool/meta/baselines/skip_as_pass.json``)."""
    enforce_findings(
        "skip_as_pass",
        find_skip_as_pass(repo_root),
        repo_root=repo_root,
        baseline_dir=baseline_dir or DEFAULT_BASELINE_DIR,
        label="skip_as_pass",
        guidance="A guard that prints SKIPPED and exits 0 passes with nothing checked. Exit non-zero with the reason, or accept it with a note.",
    )
