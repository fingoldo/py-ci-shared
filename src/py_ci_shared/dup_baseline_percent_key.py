"""A duplicate-body baseline must not hold one pair twice because its similarity percentage moved. Opt-in ratchet.

A duplicate-body baseline records each near-identical pair of bodies under a key that carries the measured similarity,
for example ``"a/x.py::f ~ b/y.py::g (87.5%)"``. A re-measure that moves the figure to ``(87.6%)`` then writes a second
entry for the same pair, and the file lists one pair twice. :func:`strip_similarity_percent` removes the ``(NN.N%)``
suffix; two entries that normalize to the same key are the same pair, and that is the finding.

Keys are reported by their percent-free form, so a fix is to keep one entry of each pair. The gate reads the duplicate
baseline file named by ``duplicate_baseline_path`` (repo-relative); a ``*_path`` keyword is resolved against the repo root.
Entries are read as ``{"accepted": {...}}`` or a bare ``{key: note}`` object, skipping keys that start with ``_``.

Enable it explicitly (NOT in the default set)::

    [tool.py_ci_shared]
    enable = ["dup_baseline_percent_key"]

    [tool.py_ci_shared.gates.dup_baseline_percent_key]
    module = "dup_baseline_percent_key"
    entry = "assert_no_percent_only_duplicate_entries"
    duplicate_baseline_path = "tool/meta/baselines/duplicate_bodies.json"

Accepted pairs go in ``tool/meta/baselines/dup_baseline_percent_key.json`` as ``{"accepted": {normalized_key: note}}``, with a note
saying why the pair is listed twice. Only a NEW doubled pair fails.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional, Union

from ._core.baseline import load_json
from ._text_ratchet import DEFAULT_BASELINE_DIR, enforce_findings

__all__ = ["SIMILARITY_PERCENT", "strip_similarity_percent", "find_percent_only_duplicates", "assert_no_percent_only_duplicate_entries"]

#: A trailing ``(NN.N%)`` (or ``(NN%)``) as a duplicate-body baseline writes it.
SIMILARITY_PERCENT = re.compile(r"\s*\(\s*\d+(?:\.\d+)?\s*%\s*\)")


def strip_similarity_percent(key: str) -> str:
    """*key* without its similarity percentage: ``"a::f ~ b::g (87.5%)"`` becomes ``"a::f ~ b::g"``. Other text is untouched."""
    return SIMILARITY_PERCENT.sub("", key).strip()


def _entry_keys(data: object) -> list[str]:
    if isinstance(data, dict):
        accepted = data.get("accepted", data)
        if isinstance(accepted, dict):
            return [key for key in accepted if not str(key).startswith("_")]
        if isinstance(accepted, list):
            return [str(key) for key in accepted]
        return []
    if isinstance(data, list):
        return [str(key) for key in data]
    return []


def find_percent_only_duplicates(duplicate_baseline: Union[str, Path]) -> dict[str, str]:
    """``{normalized key: description}`` for each pair the duplicate baseline lists more than once (the raw keys differ only by percent)."""
    path = Path(duplicate_baseline)
    if not path.exists():
        raise FileNotFoundError(f"duplicate baseline {os.fspath(path)} does not exist; the gate has nothing to read")
    groups: dict[str, list[str]] = defaultdict(list)
    for key in _entry_keys(load_json(path)):
        groups[strip_similarity_percent(key)].append(key)
    return {
        normalized: f"listed {len(raw)} times ({'; '.join(sorted(raw))}): the entries differ only by the similarity percentage"
        for normalized, raw in groups.items()
        if len(raw) > 1
    }


def assert_no_percent_only_duplicate_entries(
    repo_root: Union[str, Path],
    *,
    duplicate_baseline_path: str,
    baseline_dir: Optional[str] = None,
) -> None:
    """Fail on a duplicate-body pair listed twice under percent-only-different keys and not accepted by ``dup_baseline_percent_key.json``."""
    if os.path.isabs(duplicate_baseline_path):
        target = Path(duplicate_baseline_path)
    else:
        target = Path(repo_root) / duplicate_baseline_path
    enforce_findings(
        "dup_baseline_percent_key",
        find_percent_only_duplicates(target),
        repo_root=repo_root,
        baseline_dir=baseline_dir or DEFAULT_BASELINE_DIR,
        label="dup_baseline_percent_key",
        guidance="One pair listed twice under two similarity percentages. Keep one entry per pair, with the current figure.",
    )
