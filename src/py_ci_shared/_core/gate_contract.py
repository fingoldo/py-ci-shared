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
#: Entries that enumerate a corpus but never parse a Python source file, so an unparsed file cannot happen and
#: ``allow_unparsed`` would be a parameter nothing reads. Keyed ``module.function``; each reason says what the entry
#: reads instead. ``tests/test_gate_entry_contract.py`` fails when a key names nothing, so the list cannot rot.
NEVER_PARSES: dict[str, str] = {
    "audit_wave_filenames.assert_no_new_audit_wave_filenames": "judges file names only; nothing is parsed",
    "docs_inventory_parity.assert_no_inventory_drift": "takes a precomputed problem list; reads no source",
    "pickle_state_completeness.assert_pickle_round_trips": "runtime pickle round trip; no source is parsed",
    "repo_hygiene.assert_repo_hygiene": "checks file names, bytes and BOMs; no parse step",
    "timezone_honest.assert_timezone_honest": "text scan of call sites; no parse step, so no file can be unparsed",
}


#: Entries whose input is handed in ready-made, so there is no corpus to floor: the floor would count nothing.
NO_CORPUS: dict[str, str] = {
    "docs_inventory_parity.assert_no_inventory_drift": "takes a precomputed problem list; enumerates no corpus of its own",
    "pickle_state_completeness.assert_pickle_round_trips": "takes its factories from the caller; enumerates no corpus of its own",
}


def contract_gaps(func: Callable[..., Any]) -> tuple[str, ...]:
    """What *func*'s signature lacks: ``"floor"`` (no ``min_*`` parameter) and/or ``"allow_unparsed"``. A ``**kwargs``
    entry is judged on its named parameters only, since a config table cannot tell what it forwards."""
    params = inspect.signature(func).parameters
    gaps = []
    key = f"{func.__module__.rpartition('.')[2]}.{func.__name__}"
    if not any(name.startswith("min_") for name in params) and key not in NO_CORPUS:
        gaps.append("floor")
    if UNPARSED_PARAM not in params and key not in NEVER_PARSES:
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
