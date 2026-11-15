"""Tests for py_ci_shared.code_audit.near_duplicate_function_body.scan_near_duplicate_function_body.

Kept in its own file, separate from the main tests/test_code_audit.py, so it doesn't
collide with unrelated in-flight work in that file.
"""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.code_audit import scan_near_duplicate_function_body

_NEAR_DUP_PAIR = """
def process_records(records, threshold):
    results = []
    for r in records:
        if r.get("score", 0) > threshold:
            value = r["score"] * 2
            if value > 100:
                value = 100
            results.append({"id": r["id"], "value": value, "flag": True})
        else:
            results.append({"id": r["id"], "value": 0, "flag": False})
    return results
"""

_NEAR_DUP_DRIFTED = """
def process_entries(entries, cutoff):
    results = []
    for r in entries:
        if r.get("score", 0) > cutoff:
            value = r["score"] * 2
            if value > 100:
                value = 100
            results.append({"id": r["id"], "value": value, "flag": True})
        else:
            results.append({"id": r["id"], "value": 0, "flag": False})
    return results
"""

_SUBSET_SUPERSET = """
def process_entries(entries, cutoff):
    results = []
    for r in entries:
        if r.get("score", 0) > cutoff:
            value = r["score"] * 2
            if value > 100:
                value = 100
            results.append({"id": r["id"], "value": value, "flag": True})
        else:
            results.append({"id": r["id"], "value": 0, "flag": False})

    extra_total = 0
    for k in range(50):
        extra_total += k * k
        if extra_total > 10000:
            extra_total -= 5000
        for m in range(10):
            extra_total += m
            if extra_total % 7 == 0:
                extra_total *= 2
    print("done", extra_total, len(results))
    return results
"""

_UNRELATED = """
def unrelated_function_totally_different(x, y, z):
    total = 0
    for i in range(x):
        for j in range(y):
            total += i * j + z
    return total
"""


def _write(root: Path, name: str, content: str) -> None:
    (root / name).write_text(content, encoding="utf-8")


def test_flags_near_duplicate_pair(tmp_path):
    _write(tmp_path, "a.py", _NEAR_DUP_PAIR)
    _write(tmp_path, "b.py", _NEAR_DUP_DRIFTED)

    findings = scan_near_duplicate_function_body(tmp_path, min_nodes=5)

    near_dup = [f for f in findings if f.check == "near_duplicate_function_body"]
    assert len(near_dup) == 1
    assert near_dup[0].file == "b.py"


def test_flags_subset_containment(tmp_path):
    _write(tmp_path, "a.py", _NEAR_DUP_PAIR)
    _write(tmp_path, "c.py", _SUBSET_SUPERSET)

    findings = scan_near_duplicate_function_body(tmp_path, min_nodes=5)

    subset = [f for f in findings if f.check == "duplicate_function_body_subset"]
    assert len(subset) == 1
    assert subset[0].file == "c.py"
    assert "100%" in subset[0].detail


def test_ignores_unrelated_functions(tmp_path):
    _write(tmp_path, "a.py", _NEAR_DUP_PAIR)
    _write(tmp_path, "d.py", _UNRELATED)

    findings = scan_near_duplicate_function_body(tmp_path, min_nodes=5)

    assert findings == []


def test_ignores_exact_duplicates(tmp_path):
    # An EXACT duplicate is scan_duplicate_function_body's job, not this scanner's --
    # a byte-identical body must not also be reported here.
    _write(tmp_path, "a.py", _NEAR_DUP_PAIR)
    _write(tmp_path, "a_copy.py", _NEAR_DUP_PAIR)

    findings = scan_near_duplicate_function_body(tmp_path, min_nodes=5)

    assert findings == []


def test_respects_min_nodes_floor(tmp_path):
    _write(tmp_path, "a.py", "def f(x):\n    return x + 1\n")
    _write(tmp_path, "b.py", "def g(y):\n    return y + 1\n")

    findings = scan_near_duplicate_function_body(tmp_path, min_nodes=20)

    assert findings == []


def test_prefix_filtered_candidates_equal_the_all_postings_reference():
    """35-F5: the prefix-filtered generator returns the same pairs as counting every posting, on bodies with shared runs, drift and generic tokens."""
    import random

    from py_ci_shared.code_audit import near_duplicate_function_body as m

    rng = random.Random(7)
    vocab = [f"t{i}" for i in range(40)]
    base = [tuple(rng.choice(vocab) for _ in range(rng.randint(8, 60))) for _ in range(25)]
    entries = []
    for tokens in base:
        for _ in range(rng.randint(1, 4)):
            drifted = list(tokens)
            for _ in range(rng.randint(0, 6)):
                drifted[rng.randrange(len(drifted))] = rng.choice(vocab)
            cut = rng.randint(0, len(drifted) // 3)
            entries.append((None, None, None, tuple(drifted[cut:] + [rng.choice(vocab)] * rng.randint(0, 5))))
    entries.append((None, None, None, ()))
    assert m._candidate_pairs(entries) == m._candidate_pairs_all_postings(entries)
    assert len(m._candidate_pairs_all_postings(entries)) > 10, "the corpus must produce real candidates, or the equality is vacuous"


def _splat_module(names: str, fn: str) -> str:
    return f"def {fn}():\n    return [{', '.join('*' + n for n in names.split())}]\n"


def test_data_splat_bodies_over_different_module_names_are_not_reported(tmp_path):
    _write(tmp_path, "a.py", _splat_module("A1 A2 A3 A4 A5 A6 A7 A8", "build"))
    _write(tmp_path, "b.py", _splat_module("A1 A2 A3 A4 A5 A6 A7 B8", "build"))
    assert scan_near_duplicate_function_body(tmp_path, min_nodes=5) == []


def test_negative_control_a_call_in_the_return_is_still_compared(tmp_path):
    body = "def build():\n    return [*load(A1), *load(A2), *load(A3), *load(A4), *load(A5)]\n"
    _write(tmp_path, "a.py", body)
    _write(tmp_path, "b.py", body.replace("def build", "def build2"))
    findings = scan_near_duplicate_function_body(tmp_path, min_nodes=5)
    assert findings == []  # identical bodies are the exact-duplicate scanner's job; a near pair must differ
    _write(tmp_path, "b.py", body.replace("def build", "def build2").replace("A5", "Z9"))
    assert [f.check for f in scan_near_duplicate_function_body(tmp_path, min_nodes=5)] == ["near_duplicate_function_body"]


N_STEPS = 6
_SHORT_FRAGMENT_HOST = """
def host(entries, cutoff):
    results = []
    for r in entries:
        if r.get("score", 0) > cutoff:
            value = r["score"] * 2
            if value > 100:
                value = 100
            results.append({"id": r["id"], "value": value, "flag": True})
        else:
            results.append({"id": r["id"], "value": 0, "flag": False})
""" + "".join(f"    step_{i} = sum(k * {i} for k in range(entries_count_{i})) + len(results) * {i}\n    results.append(step_{i})\n" for i in range(N_STEPS)) + "    return results\n"


def test_a_short_fragment_of_a_long_function_is_not_a_subset_finding(tmp_path):
    _write(tmp_path, "a.py", _NEAR_DUP_PAIR)
    _write(tmp_path, "c.py", _SHORT_FRAGMENT_HOST)
    assert [f for f in scan_near_duplicate_function_body(tmp_path, min_nodes=5) if f.check == "duplicate_function_body_subset"] == []


def test_a_subset_finding_honours_the_audit_exempt_marker_on_the_container(tmp_path):
    _write(tmp_path, "a.py", _NEAR_DUP_PAIR)
    marker = "  # audit-exempt: duplicate_function_body_subset -- independent numerics, the module must stay a leaf"
    _write(tmp_path, "c.py", _SUBSET_SUPERSET.replace("def process_entries(entries, cutoff):", "def process_entries(entries, cutoff):" + marker))
    assert scan_near_duplicate_function_body(tmp_path, min_nodes=5) == []
    _write(tmp_path, "c.py", _SUBSET_SUPERSET)
    assert [f.check for f in scan_near_duplicate_function_body(tmp_path, min_nodes=5)] == ["duplicate_function_body_subset"]
