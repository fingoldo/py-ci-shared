"""The array-copy-for-hash check must catch the hashing shapes and leave the unfixable ones alone."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.hash_fed_by_array_copy import (
    assert_no_hash_fed_by_array_copy,
    find_hashes_fed_by_array_copy,
)

NEWLINE = chr(10)


def _module(tmp_path: Path, line: str, name: str = "m.py") -> Path:
    """Write a one-expression function and return the directory holding it."""
    body = ["def f():", "    " + line, "    return 0"]
    (tmp_path / name).write_bytes((NEWLINE.join(body) + NEWLINE).encode("utf-8"))
    return tmp_path


FLAGGED = [
    ("incremental_update", "h.update(X_train.tobytes())"),
    ("indexed_receiver", "h.update(arrT[j].tobytes())"),
    ("sliced_receiver", "h.update(arr[:head_n].tobytes())"),
    ("blake2b_constructor", "d = hashlib.blake2b(arr.tobytes(), digest_size=16)"),
    ("sha256_constructor", "d = hashlib.sha256(arr.tobytes())"),
    ("already_contiguous", "h.update(np.ascontiguousarray(x).tobytes())"),
]


@pytest.mark.parametrize("label,line", FLAGGED, ids=[label for label, _ in FLAGGED])
def test_a_hash_fed_by_a_copy_is_reported(tmp_path: Path, label: str, line: str):
    """Every one of these shipped somewhere; `already_contiguous` pays the copy for no reason at all."""
    root = _module(tmp_path, line)
    found = find_hashes_fed_by_array_copy([root])
    assert len(found) == 1, f"{label}: expected one finding, got {[str(x) for x in found]}"
    assert found[0].lineno == 2


NOT_FLAGGED = [
    ("buffer_read", "h.update(np.ascontiguousarray(a).data)"),
    ("plain_bytes", "h.update(b'literal')"),
    ("encoded_string", "h.update(str(arr.dtype).encode())"),
    ("builtin_hash", "key = hash(arr.tobytes())"),
    ("dict_key", "key = inv.astype(np.int64).tobytes()"),
    ("tuple_member", "sig = (arr.shape, arr.dtype.str, hash(arr.tobytes()))"),
    ("concatenated", "payload = head.tobytes() + b'|' + tail.tobytes()"),
    ("tobytes_with_order", "h.update(arr.tobytes('F'))"),
]


@pytest.mark.parametrize("label,line", NOT_FLAGGED, ids=[label for label, _ in NOT_FLAGGED])
def test_a_site_the_rewrite_does_not_reach_is_not_reported(tmp_path: Path, label: str, line: str):
    """Reporting a site whose fix does not apply sends the reader to a change they cannot make.

    `builtin_hash`, `dict_key` and `tuple_member` all need a hashable object, and a memoryview is not one.
    `concatenated` cannot be joined with `+`; its fix is to feed the hash incrementally, which is a
    restructuring rather than a substitution. `tobytes_with_order` is not the C-order serialisation the
    buffer read reproduces.
    """
    root = _module(tmp_path, line)
    assert find_hashes_fed_by_array_copy([root]) == [], f"{label} should not be reported"


def test_the_assertion_names_every_site_and_carries_the_rewrite(tmp_path: Path):
    """A reader should not have to look up the replacement."""
    root = _module(tmp_path, "h.update(arr.tobytes())")
    with pytest.raises(AssertionError) as exc:
        assert_no_hash_fed_by_array_copy([root])
    message = str(exc.value)
    assert "m.py:2" in message
    assert "np.ascontiguousarray(a).data" in message


def test_an_allowed_site_is_not_reported(tmp_path: Path):
    """The escape hatch exists, though a site needing it is usually one this check should not have reported."""
    root = _module(tmp_path, "h.update(arr.tobytes())")
    assert_no_hash_fed_by_array_copy([root], allow=[f"{(root / 'm.py').as_posix()}:2"])


def test_excluded_paths_are_skipped(tmp_path: Path):
    """Benchmark trees compare implementations and are meant to keep the shape they were written with."""
    bench = tmp_path / "_benchmarks"
    bench.mkdir()
    _module(bench, "h.update(arr.tobytes())", name="b.py")
    assert find_hashes_fed_by_array_copy([tmp_path]) != []
    assert find_hashes_fed_by_array_copy([tmp_path], exclude=("_benchmarks",)) == []


def test_a_file_that_does_not_parse_is_skipped_rather_than_raising(tmp_path: Path):
    """A scanner that dies on one unparseable file takes the whole gate down with it."""
    (tmp_path / "broken.py").write_bytes(b"def f(:" + NEWLINE.encode("utf-8"))
    _module(tmp_path, "h.update(arr.tobytes())", name="good.py")
    found = find_hashes_fed_by_array_copy([tmp_path])
    assert [f.path.name for f in found] == ["good.py"]
