"""A script that prints SKIPPED and then exits 0 reports a pass. Opt-in ratchet over ``tool/``, ``scripts/`` and ``.githooks/``.

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

Approximation: a block is recognised by its shape, not by a parser. A ``SKIPPED`` printed by a helper that another file
exits from is not seen; a Python block whose exit sits at a shallower indent (after the ``if`` that holds the print) is
not seen either, by design of "same block".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ._core.source import indent_width
from ._text_ratchet import DEFAULT_BASELINE_DIR, enforce_findings, normalize_line, text_files

__all__ = ["SKIP_DIRS", "find_skip_as_pass", "assert_no_new_skip_as_pass"]

#: Top-level directories whose scripts are guards.
SKIP_DIRS = ("tool", "scripts", ".githooks")

_PRINTS_SKIPPED = re.compile(r"\b(?:echo|printf|print|puts|Write-Host|Write-Output)\b[^\n]*\bSKIPPED\b")
_EXIT_ZERO = re.compile(r"(?<![\w.])(?:exit\s+0(?![\w.])|sys\.exit\(\s*0\s*\)|exit\(\s*0\s*\))")
# Shell: the block ends at its closing keyword or at the next function header; any return/exit reached first ends it too.
_SH_BLOCK_END = re.compile(r"^\s*(?:fi|esac|done|else|elif|return|exit)\b|^\s*\}|^\s*(?:function\s+)?[A-Za-z_]\w*\s*\(\s*\)\s*\{?\s*$")
_PY_BLOCK_END = re.compile(r"^\s*(?:return|raise|continue|break)\b")


def _shell_block_exits_zero(lines: list[str], index: int) -> bool:
    if _EXIT_ZERO.search(lines[index]):
        return True
    for line in lines[index + 1 :]:
        if _EXIT_ZERO.search(line):
            return True
        if _SH_BLOCK_END.match(line):
            return False
    return False


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


def _skip_lines(rel: str, text: str) -> dict[str, str]:
    lines = text.splitlines()
    python = rel.endswith(".py")
    found: dict[str, str] = {}
    for index, line in enumerate(lines):
        if not _PRINTS_SKIPPED.search(line):
            continue
        exits = _python_block_exits_zero(lines, index) if python else _shell_block_exits_zero(lines, index)
        if exits:
            found[f"{rel}: {normalize_line(line)}"] = f"line {index + 1} prints SKIPPED and its block exits 0, which reads as a pass"
    return found


def find_skip_as_pass(root: Union[str, Path]) -> dict[str, str]:
    """``{key: description}`` for every SKIPPED-then-exit-0 block under :data:`SKIP_DIRS` of *root*."""
    found: dict[str, str] = {}
    for file in text_files(root, ("*",), under=SKIP_DIRS):
        found.update(_skip_lines(file.rel, file.text))
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
