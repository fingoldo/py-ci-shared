"""The same CLI flag stem registered as ``--dry-run`` in one script and ``--dry_run`` in its siblings.

An operator who types the spelling that works on 19 scripts gets ``unrecognized arguments`` on the 20th in the middle of an incident
(``rescan_active_jobs.py --dry-run`` against every sibling's ``--dry_run``). Closing it takes one ``add_argument("--dry-run", "--dry_run", ...)``.

Reported across the whole corpus: every long option of an ``add_argument`` call is reduced to a stem (``-`` read as ``_``); when two or more
spellings of one stem each appear alone in some call, the call sites of every spelling except the most common one are reported (a tie keeps the
alphabetically first spelling).

Not reported: a call that lists both spellings itself (the alias that closes the finding); a stem with one spelling; single-dash options.

Usage::

    from py_ci_shared.cli_flag_spelling import assert_cli_flag_spelling

    def test_cli_flag_spelling():
        assert_cli_flag_spelling(REPO)
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python, walk

__all__ = ["RULE", "assert_cli_flag_spelling", "find_cli_flag_spelling_splits"]

RULE = "cli-flag-spelling-split"


def _long_options(call: ast.Call) -> list:
    return [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--") and len(a.value) > 2]


def find_cli_flag_spelling_splits(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every call site of a minority flag spelling under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    sites: dict = {}
    for parsed in scan:
        for node in walk(parsed.tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"):
                continue
            options = _long_options(node)
            by_stem: dict = {}
            for option in options:
                by_stem.setdefault(option[2:].replace("-", "_"), set()).add(option)
            for stem, spellings in by_stem.items():
                if len(spellings) == 1:
                    sites.setdefault(stem, []).append((next(iter(spellings)), parsed.rel, node.lineno))
    out: list[Finding] = []
    for entries in sites.values():
        counts = Counter(spelling for spelling, _, _ in entries)
        if len(counts) < 2:
            continue
        keep = sorted(counts, key=lambda sp: (-counts[sp], sp))[0]
        spellings = ", ".join(f"{sp} x{counts[sp]}" for sp in sorted(counts))
        out.extend(
            Finding(
                rel,
                line,
                RULE,
                f"{spelling} is spelled differently from {keep} elsewhere ({spellings}); register both spellings in one add_argument call or rename",
            )
            for spelling, rel, line in entries
            if spelling != keep
        )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cli_flag_spelling(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any split flag spelling, or with *baseline_path* on any the baseline does not accept."""
    found = find_cli_flag_spelling_splits(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "list both spellings in one add_argument call (alias) or use one spelling everywhere"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cli_flag_spelling", refresh_command="PY_CI_SHARED_REFRESH=cli_flag_spelling")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} cli-flag-spelling-split finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
