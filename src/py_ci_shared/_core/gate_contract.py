"""The keyword contract of a corpus-scanning gate entry: a ``min_*`` floor and an ``allow_unparsed`` switch.

CLAUDE.md asks every gate for both, but by audit 2026-10-03 (K-15) only 4 of 135 ``assert_*`` entries accepted
``allow_unparsed`` and floors were spelled 23 ways, so ``[tool.py_ci_shared.gates.X] allow_unparsed = true`` worked
for one gate and was a ``ConfigError`` for its neighbour. Rewriting every gate at once would change 100+ public
signatures; instead :func:`contract_gaps` names what an entry lacks, ``tests/test_gate_entry_contract.py`` holds the
entries that lacked it on that date as a shrink-only baseline, and every NEW entry of a corpus-scanning module must
take both. Retrofitting an entry removes it from the baseline.

Canonical names: ``min_files`` (an older entry may keep another ``min_*`` spelling: any one floor satisfies the
contract) and ``allow_unparsed``. :func:`check_scan` is the one-line body for both: the floor, then the unparsed list.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any, Callable

from .scan import ScanResult

__all__ = ["CORPUS_FUNCTIONS", "FLOOR_PARAM", "UNPARSED_PARAM", "check_scan", "contract_gaps", "scans_a_corpus"]

FLOOR_PARAM = "min_files"
UNPARSED_PARAM = "allow_unparsed"
#: A module that calls one of these enumerates a corpus, so its entries owe the contract.
CORPUS_FUNCTIONS = frozenset({"scan_python", "scan_tree", "iter_files", "read_text_corpus"})


def contract_gaps(func: Callable[..., Any]) -> tuple[str, ...]:
    """What *func*'s signature lacks: ``"floor"`` (no ``min_*`` parameter) and/or ``"allow_unparsed"``. A ``**kwargs``
    entry is judged on its named parameters only, since a config table cannot tell what it forwards."""
    params = inspect.signature(func).parameters
    gaps = []
    if not any(name.startswith("min_") for name in params):
        gaps.append("floor")
    if UNPARSED_PARAM not in params:
        gaps.append(UNPARSED_PARAM)
    return tuple(gaps)


def scans_a_corpus(source: str) -> bool:
    """*source* (a module's text) calls or imports one of :data:`CORPUS_FUNCTIONS`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in CORPUS_FUNCTIONS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in CORPUS_FUNCTIONS:
            return True
        if isinstance(node, ast.alias) and node.name in CORPUS_FUNCTIONS:
            return True
    return False


def check_scan(scan: ScanResult, *, min_files: int, allow_unparsed: bool) -> None:
    """The contract's body: raise ``EmptyScanError`` below *min_files* parsed files, then (unless *allow_unparsed*)
    ``UnparsedFilesError`` for every file that could not be read or parsed."""
    scan.min_files = min_files
    scan.assert_ok(allow_unparsed=allow_unparsed)
