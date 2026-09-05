"""The drifted-duplicate-function check must find copies that moved apart and ignore ordinary polymorphism."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.drifted_duplicate_functions import (
    assert_no_drifted_duplicate_functions,
    find_drifted_duplicate_functions,
)

NEWLINE = chr(10)


def _write(tmp_path: Path, name: str, body_lines) -> None:
    """Write one module from a list of source lines."""
    (tmp_path / name).write_bytes((NEWLINE.join(body_lines) + NEWLINE).encode("utf-8"))


def _baseline(extra: str = "") -> list:
    """A helper long enough that one changed statement still leaves the copies highly similar."""
    head = [
        "def _fit_baseline(x, y, task, seed):",
        "    n = len(x)",
        "    total = 0",
        "    for i in range(n):",
        "        total += x[i] * y[i]",
        "    mean = total / n",
        "    spread = sum((v - mean) ** 2 for v in x)",
        "    scaled = spread / max(n - 1, 1)",
    ]
    tail = ["    return mean, scaled"]
    return head + ([extra] if extra else []) + tail


def test_a_copy_that_drifted_is_reported(tmp_path: Path):
    """One statement added to one copy: the shape that let an out-of-fold fix reach four modules of eight."""
    _write(tmp_path, "a.py", _baseline())
    _write(tmp_path, "b.py", _baseline("    scaled = scaled * 2"))
    groups = find_drifted_duplicate_functions([tmp_path])
    assert len(groups) == 1, [str(g) for g in groups]
    assert groups[0].name == "_fit_baseline"
    assert groups[0].variants == 2
    assert sorted(p.name for p, _ in groups[0].sites) == ["a.py", "b.py"]


def test_identical_copies_are_not_reported(tmp_path: Path):
    """Plain duplication is a different problem with different tools; this check is about DRIFT."""
    _write(tmp_path, "a.py", _baseline())
    _write(tmp_path, "b.py", _baseline())
    assert find_drifted_duplicate_functions([tmp_path]) == []


def test_a_docstring_difference_alone_is_not_drift(tmp_path: Path):
    """Re-wording a docstring must not read as a diverged body, or the check becomes noise."""
    quote = chr(34) * 3
    a = _baseline()
    b = _baseline()
    a.insert(1, "    " + quote + "One wording." + quote)
    b.insert(1, "    " + quote + "A different wording entirely, at some length." + quote)
    _write(tmp_path, "a.py", a)
    _write(tmp_path, "b.py", b)
    assert find_drifted_duplicate_functions([tmp_path]) == []


def test_unrelated_functions_sharing_a_name_are_not_reported(tmp_path: Path):
    """Two implementations that merely share a name are not copies of each other."""
    _write(tmp_path, "a.py", ["def run(x):", "    return x + 1"])
    _write(
        tmp_path,
        "b.py",
        ["def run(x):", "    total = 0", "    for i in x:", "        total += i * i", "    return sorted(set(x)), total"],
    )
    assert find_drifted_duplicate_functions([tmp_path]) == []


def test_methods_are_out_of_scope(tmp_path: Path):
    """Methods sharing a name and signature are an interface, not a copy.

    On a ~3500-module repository this restriction is what takes the report from 250 groups to 14:
    `predict`, `forward` and `__repr__` alone accounted for most of the noise.
    """
    _write(tmp_path, "a.py", ["class A:", "    def predict(self, x):", "        return x + 1"])
    _write(tmp_path, "b.py", ["class B:", "    def predict(self, x):", "        return x + 2"])
    assert find_drifted_duplicate_functions([tmp_path]) == []


def test_a_different_signature_is_a_different_function(tmp_path: Path):
    """Same name, different arguments: not a copy that drifted."""
    _write(tmp_path, "a.py", ["def f(x, y):", "    return x + y"])
    _write(tmp_path, "b.py", ["def f(x, y, z):", "    return x + y"])
    assert find_drifted_duplicate_functions([tmp_path]) == []


def test_the_similarity_floor_is_honoured(tmp_path: Path):
    """Raising the floor past the measured similarity must silence the group, and lowering it restore it."""
    _write(tmp_path, "a.py", _baseline())
    _write(tmp_path, "b.py", _baseline("    scaled = scaled * 2"))
    measured = find_drifted_duplicate_functions([tmp_path])[0].similarity
    assert find_drifted_duplicate_functions([tmp_path], similarity=measured + 0.01) == []
    assert find_drifted_duplicate_functions([tmp_path], similarity=measured - 0.01) != []


def test_the_assertion_names_every_site(tmp_path: Path):
    """A reader has to be able to open the copies; a count sends them searching."""
    _write(tmp_path, "a.py", _baseline())
    _write(tmp_path, "b.py", _baseline("    scaled = scaled * 2"))
    with pytest.raises(AssertionError) as exc:
        assert_no_drifted_duplicate_functions([tmp_path])
    message = str(exc.value)
    assert "a.py:1" in message and "b.py:1" in message
    assert "_fit_baseline" in message


def test_an_allowed_name_is_not_reported(tmp_path: Path):
    """A group judged legitimately separate can be listed by name."""
    _write(tmp_path, "a.py", _baseline())
    _write(tmp_path, "b.py", _baseline("    scaled = scaled * 2"))
    assert_no_drifted_duplicate_functions([tmp_path], allow=["_fit_baseline"])


def test_excluded_paths_are_skipped(tmp_path: Path):
    """A frozen baseline copy is meant to keep the shape it was frozen with."""
    frozen = tmp_path / "_baselines"
    frozen.mkdir()
    _write(tmp_path, "a.py", _baseline())
    _write(frozen, "b.py", _baseline("    scaled = scaled * 2"))
    assert find_drifted_duplicate_functions([tmp_path]) != []
    assert find_drifted_duplicate_functions([tmp_path], exclude=("_baselines",)) == []


def test_a_file_that_does_not_parse_is_skipped_rather_than_raising(tmp_path: Path):
    """A scanner that dies on one unparseable file takes the whole gate down with it."""
    _write(tmp_path, "broken.py", ["def f(:"])
    _write(tmp_path, "a.py", _baseline())
    _write(tmp_path, "b.py", _baseline("    scaled = scaled * 2"))
    assert len(find_drifted_duplicate_functions([tmp_path])) == 1
