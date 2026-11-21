"""Scanner tests for salted_hash_seed: the builtin hash() feeding a random seed."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.code_audit import scan_salted_hash_seeds

from ._helpers import _write


def test_hash_passed_to_default_rng_is_flagged(tmp_path: Path):
    _write(
        tmp_path,
        "bench.py",
        """
import numpy as np

def run(name):
    rng = np.random.default_rng(hash(name) % 2**32)
    return rng.random()
""",
    )
    findings = scan_salted_hash_seeds(tmp_path)
    assert [f.check for f in findings] == ["salted_hash_seed"]
    assert findings[0].line == 4


def test_hash_assigned_to_seed_name_is_flagged_once(tmp_path: Path):
    _write(
        tmp_path,
        "b.py",
        """
seed = hash("fold-a") & 0xFFFF
""",
    )
    assert len(scan_salted_hash_seeds(tmp_path)) == 1


def test_hash_as_seed_keyword_is_flagged(tmp_path: Path):
    _write(
        tmp_path,
        "b.py",
        """
def make(model_cls, key):
    return model_cls(random_state=hash(key))
""",
    )
    assert len(scan_salted_hash_seeds(tmp_path)) == 1


def test_crc32_seed_and_unrelated_hash_are_clean(tmp_path: Path):
    _write(
        tmp_path,
        "ok.py",
        """
import zlib
import numpy as np

def run(name):
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    bucket = hash(name) % 7
    return rng, bucket
""",
    )
    assert scan_salted_hash_seeds(tmp_path) == []


def test_audit_exempt_marker_suppresses_the_line(tmp_path: Path):
    _write(
        tmp_path,
        "ok.py",
        """
import numpy as np

def run(i):
    return np.random.default_rng(hash(i) % 99)  # audit-exempt: salted_hash_seed -- integer-only input, hash(int) is stable
""",
    )
    assert scan_salted_hash_seeds(tmp_path) == []
