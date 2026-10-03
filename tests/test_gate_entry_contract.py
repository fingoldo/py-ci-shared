"""K-15 (audit 2026-10-03): a ratchet on the corpus-gate keyword contract, a ``min_*`` floor and ``allow_unparsed``.

Entries that lacked either on 2026-10-03 are listed in ``tests/baselines/gate_entry_contract.json``; the list may only
shrink. A NEW entry of a corpus-scanning module must take both, so a consumer's ``allow_unparsed = true`` stops
working for one gate and erroring for its neighbour one gate at a time, never in the other direction.
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest

from py_ci_shared import registry
from py_ci_shared._core import EmptyScanError, UnparsedFilesError, scan_python
from py_ci_shared._core.gate_contract import check_scan, contract_gaps, scans_a_corpus

BASELINE = Path(__file__).resolve().parent / "baselines" / "gate_entry_contract.json"


def _current_gaps() -> dict[str, str]:
    gaps: dict[str, str] = {}
    for spec in registry.GATES:
        if spec.kind != "gate":
            continue
        module = importlib.import_module(spec.module)
        if not scans_a_corpus(inspect.getsource(module)):
            continue
        for entry in spec.entries:
            missing = contract_gaps(getattr(module, entry))
            if missing:
                gaps[f"{spec.name}.{entry}"] = ",".join(missing)
    return gaps


def test_no_new_corpus_gate_entry_lacks_the_floor_or_allow_unparsed():
    accepted = json.loads(BASELINE.read_text(encoding="utf-8"))["entries"]
    current = _current_gaps()
    new = {k: v for k, v in current.items() if k not in accepted or set(v.split(",")) - set(accepted[k].split(","))}
    assert not new, (
        "these gate entries scan a corpus but lack a min_* floor and/or allow_unparsed (use min_files and "
        "allow_unparsed, see py_ci_shared._core.gate_contract):\n  " + "\n  ".join(f"{k}: {v}" for k, v in sorted(new.items()))
    )
    fixed = {k: v for k, v in accepted.items() if current.get(k) != v}
    assert not fixed, "retrofitted or renamed: update or delete these lines in " + BASELINE.name + ":\n  " + "\n  ".join(sorted(fixed))


def test_contract_gaps_reads_the_signature():
    def full(files, *, min_files: int = 1, allow_unparsed: bool = False) -> None: ...

    def other_floor(files, *, min_functions: int = 1) -> None: ...

    def bare(files) -> None: ...

    assert contract_gaps(full) == ()
    assert contract_gaps(other_floor) == ("allow_unparsed",)
    assert contract_gaps(bare) == ("floor", "allow_unparsed")


def test_scans_a_corpus_sees_calls_imports_and_attributes():
    assert scans_a_corpus("from py_ci_shared._core import scan_python\n")
    assert scans_a_corpus("import x\nx.iter_files('.')\n")
    assert not scans_a_corpus("from pathlib import Path\nPath('.').read_text()\n")


def test_check_scan_applies_the_floor_then_the_unparsed_list(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match=r"bad.py"):
        check_scan(scan_python(tmp_path, use_git=False), min_files=1, allow_unparsed=False)
    check_scan(scan_python(tmp_path, use_git=False), min_files=1, allow_unparsed=True)
    with pytest.raises(EmptyScanError):
        check_scan(scan_python(tmp_path, use_git=False), min_files=2, allow_unparsed=True)
