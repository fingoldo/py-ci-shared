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

import ast
import json
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from py_ci_shared._core.source import parse_file

__all__ = [
    "ALLOWED_STATUSES",
    "assert_documents_identical",
    "assert_fixture_fresh",
    "assert_ledger_covers",
    "canonical_json",
    "changed_files",
    "document_diff",
    "fixture_problems",
    "ledger_entry_problems",
    "ledger_gaps",
    "load_ledger",
    "missing_test_references",
    "promise_message_ids",
    "value_mismatches",
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


def canonical_json(document: Any) -> str:
    """The one serialisation two documents are compared by: sorted keys, 2-space indent, non-ASCII kept."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False)


def document_diff(fixture: Any, live: Any, *, _path: str = "") -> list[tuple[str, str, str]]:
    """Every difference between two JSON documents as ``(kind, dotted_path, detail)``, sorted by path.

    ``kind`` is ``added`` (in ``live`` only), ``removed`` (in ``fixture`` only) or ``changed`` (a leaf, or a list, that
    differs; ``detail`` shows both sides). Mappings recurse key by key, so a new property on an existing schema is
    reported as ``("added", "components.schemas.Foo.properties.bar", ...)`` instead of as "the document differs".
    """
    out: list[tuple[str, str, str]] = []
    if isinstance(fixture, Mapping) and isinstance(live, Mapping):
        for key in sorted(set(fixture) | set(live), key=str):
            path = f"{_path}.{key}" if _path else str(key)
            if key not in fixture:
                out.append(("added", path, canonical_json(live[key])[:120]))
            elif key not in live:
                out.append(("removed", path, canonical_json(fixture[key])[:120]))
            else:
                out.extend(document_diff(fixture[key], live[key], _path=path))
    elif fixture != live:
        out.append(("changed", _path, f"fixture {canonical_json(fixture)[:60]} -> live {canonical_json(live)[:60]}"))
    return sorted(out, key=lambda row: row[1])


def assert_documents_identical(
    *,
    fixture: Mapping[str, Any],
    live: Mapping[str, Any],
    regenerate_hint: str,
    max_lines: int = 40,
) -> None:
    """Fail unless the committed document equals the live one in full (canonical JSON), naming what differs.

    A weaker check (a version string, a list of top-level names) lets a new field on an existing object pass with a
    stale fixture; this one lists each added, removed and changed path so the failure says what drifted.
    """
    import pytest

    if canonical_json(fixture) == canonical_json(live):
        return
    rows = document_diff(fixture, live)
    shown = [f"{kind} {path}" + (f" ({detail})" if kind == "changed" else "") for kind, path, detail in rows[:max_lines]]
    more = f" (+{len(rows) - max_lines} more)" if len(rows) > max_lines else ""
    pytest.fail(f"committed fixture differs from the live document in {len(rows)} place(s): {shown}{more}. {regenerate_hint}", pytrace=False)


def promise_message_ids(catalogue: Mapping[str, str], words: Iterable[str]) -> list[str]:
    """Ids of catalogue messages containing any of ``words`` as a whole word (case-insensitive, hyphen kept inside a
    word), sorted. A message with a promise word ("retrying", "will", "automatically") needs a ledger row."""
    pattern = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w-])", re.IGNORECASE)
    return sorted(key for key, text in catalogue.items() if pattern.search(text))


def missing_test_references(references: Mapping[str, Mapping[str, str]], root: Path) -> list[str]:
    """Ledger rows whose named test does not exist. ``references`` maps a row key to ``{"path": ..., "name": ...}``
    (path relative to ``root``); the file must exist and define a top-level function of that name. Resolved by
    parsing the file, never by importing it, so a heavy test module costs nothing."""
    problems: list[str] = []
    for key, ref in sorted(references.items()):
        path = root / ref.get("path", "")
        name = ref.get("name", "")
        if not ref.get("path") or not name:
            problems.append(f"{key}: test reference needs 'path' and 'name'")
        elif not path.is_file():
            problems.append(f"{key}: test file {ref['path']} does not exist")
        else:
            tree = parse_file(path)
            if name not in {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}:
                problems.append(f"{key}: {ref['path']} defines no test {name}")
    return problems


def value_mismatches(expected: Mapping[str, Any], observed: Mapping[str, Any], *, rel_tol: float = 1e-9) -> list[str]:
    """Keys where the ledger's expected value differs from the value the code emitted. Numbers compare with a
    relative tolerance, a key missing from ``observed`` is a mismatch, and a key only in ``observed`` is reported
    too (a shape the ledger does not know)."""
    problems: list[str] = []
    for key in sorted(set(expected) | set(observed), key=str):
        if key not in observed:
            problems.append(f"{key}: ledger expects {expected[key]!r}, nothing was emitted")
        elif key not in expected:
            problems.append(f"{key}: emitted {observed[key]!r}, no ledger row")
        else:
            want, got = expected[key], observed[key]
            numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (want, got))
            if isinstance(want, bool) != isinstance(got, bool):
                same = False  # True == 1 in Python; a flag and a count are different shapes
            else:
                same = abs(want - got) <= rel_tol * max(abs(want), abs(got), 1e-300) if numeric else want == got
            if not same:
                problems.append(f"{key}: ledger expects {want!r}, emitted {got!r}")
    return problems
