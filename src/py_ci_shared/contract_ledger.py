"""Shared primitives for contract ratchets: a committed ledger of what an API may emit, a fixture-freshness
check, and a before/after writer probe for on-disk stores.

A consuming repo keeps a JSON ledger under ``contracts/`` that lists every value a contract surface can
produce (a response ``kind`` literal, an HTTP status code, a file that stores user text). The repo's own test
enumerates the values BEHAVIOURALLY (import the models, call the routes, run the code path on a fixture) and
hands the observed set to the functions here, which report the gaps. Nothing is allowlisted: a gap is a
failure, and the only way to clear it is to record the value with its status or reason, or to change the code.

The helpers are pure where they can be (``ledger_gaps``, ``ledger_entry_problems``, ``fixture_problems``,
``changed_files``) so a consumer's adversarial test can feed them a doctored copy and assert the failure.

Usage (a consuming repo's meta-test module)::

    from py_ci_shared.contract_ledger import assert_ledger_covers, load_ledger

    ledger = load_ledger(ROOT / "contracts" / "client_literals.json")
    assert_ledger_covers(observed={"context_sex", "finding_pair_sex"}, entries=ledger["literals"], name="kind")

Deliberately dependency-free: ``pytest`` is imported lazily, matching the package's other modules.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "ALLOWED_STATUSES",
    "assert_fixture_fresh",
    "assert_ledger_covers",
    "changed_files",
    "fixture_problems",
    "ledger_entry_problems",
    "ledger_gaps",
    "load_ledger",
    "snapshot_files",
    "store_writers",
]

# A ledger entry says either that the value is mapped (the consumer handles it) or unmapped with a reason.
ALLOWED_STATUSES = frozenset({"mapped", "unmapped"})


def load_ledger(path: Path) -> dict[str, Any]:
    """Parse a committed ledger file. A missing file is a failure with the path named, never an empty ledger."""
    if not path.is_file():
        raise FileNotFoundError(f"contract ledger missing: {path}; commit it, do not skip the check")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def ledger_gaps(observed: Iterable[Any], entries: Mapping[str, Any]) -> list[str]:
    """Observed values with no ledger entry, sorted by their text. Ledger keys are compared as strings."""
    return sorted({str(value) for value in observed} - set(entries))


def ledger_entry_problems(entries: Mapping[str, Any], *, required: Iterable[str] = ()) -> list[str]:
    """Entries that do not say what they must: a status outside ``ALLOWED_STATUSES``, an unmapped entry with no
    reason, or a missing required field (for example ``retry_after`` on a status-code row)."""
    problems: list[str] = []
    needed = tuple(required)
    for key, entry in sorted(entries.items()):
        if not isinstance(entry, Mapping):
            problems.append(f"{key}: entry is not an object")
            continue
        status = entry.get("status")
        if status not in ALLOWED_STATUSES:
            problems.append(f"{key}: status {status!r} is not one of {sorted(ALLOWED_STATUSES)}")
        if status == "unmapped" and not str(entry.get("reason", "")).strip():
            problems.append(f"{key}: unmapped without a reason")
        problems.extend(f"{key}: missing required field {field!r}" for field in needed if field not in entry)
    return problems


def assert_ledger_covers(
    observed: Iterable[Any],
    entries: Mapping[str, Any],
    *,
    name: str,
    required: Iterable[str] = (),
) -> None:
    """Fail when an observed value is absent from the ledger or an entry is malformed."""
    import pytest

    gaps = ledger_gaps(observed, entries)
    problems = ledger_entry_problems(entries, required=required)
    if gaps or problems:
        pytest.fail(
            f"{name} ledger is incomplete. Values not recorded: {gaps}. Malformed entries: {problems}. "
            "Record each value with a status (mapped | unmapped + reason) in the committed ledger, or fix the code.",
            pytrace=False,
        )


def fixture_problems(
    *,
    fixture: Mapping[str, Any],
    live: Mapping[str, Any],
    keys: Iterable[str],
) -> list[str]:
    """Keys whose recorded fixture value differs from the live value. A missing key on either side is a problem."""
    problems: list[str] = []
    for key in keys:
        if key not in fixture:
            problems.append(f"{key}: missing from the committed fixture")
        elif key not in live:
            problems.append(f"{key}: missing from the live schema")
        elif fixture[key] != live[key]:
            problems.append(f"{key}: fixture has {fixture[key]!r}, live has {live[key]!r}")
    return problems


def assert_fixture_fresh(
    *,
    fixture: Mapping[str, Any],
    live: Mapping[str, Any],
    keys: Iterable[str],
    regenerate_hint: str,
) -> None:
    """Fail on a stale committed fixture. A stale fixture is a red test, never a skip."""
    import pytest

    problems = fixture_problems(fixture=fixture, live=live, keys=keys)
    if problems:
        pytest.fail(f"committed fixture is stale: {problems}. {regenerate_hint}", pytrace=False)


def snapshot_files(root: Path, *, skip_dirs: Iterable[str] = ()) -> dict[str, int]:
    """Relative POSIX path to byte size for every file under ``root``. A missing root is an empty snapshot."""
    skip = frozenset(skip_dirs)
    out: dict[str, int] = {}
    if not root.is_dir():
        return out
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in skip for part in rel.parts):
            continue
        out[rel.as_posix()] = path.stat().st_size
    return out


def changed_files(before: Mapping[str, int], after: Mapping[str, int]) -> list[str]:
    """Paths that appeared or grew between two snapshots. A same-size rewrite is not reported: the probe asks which
    files a request path WRITES to, and a file that grows (an append) is the store's signature."""
    return sorted(path for path, size in after.items() if path not in before or size > before[path])


def store_writers(
    *,
    run_probe: Callable[[], None],
    roots: Iterable[Path],
    skip_dirs: Iterable[str] = (),
) -> list[str]:
    """Run a probe (a callable that drives request paths) and return every file under ``roots`` it created or grew.

    The consumer points its cache and data environment variables at a temporary directory BEFORE calling this, so
    the roots it passes are the directories the probe can write into.
    """
    roots = list(roots)

    def _snapshot() -> dict[str, int]:
        out: dict[str, int] = {}
        for root in roots:
            for rel, size in snapshot_files(root, skip_dirs=skip_dirs).items():
                out[f"{root}/{rel}"] = size
        return out

    before = _snapshot()
    run_probe()
    return changed_files(before, _snapshot())
