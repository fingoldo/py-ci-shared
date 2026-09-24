from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.numba_seed_range import RULE, assert_numba_seeds_fit_int64, find_wide_numba_seeds

HEADER = "import os, struct, random, secrets, hashlib\nimport numpy as np\nfrom mylib import set_numba_random_seed\n"


def _write(root: Path, rel: str, body: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + (HEADER + textwrap.dedent(body)).encode("utf-8"))

    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _found(root: Path, **kw: object) -> list[str]:
    return [f.message for f in find_wide_numba_seeds(root, use_git=False, **kw) if f.rule == RULE]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "source",
    [
        'struct.unpack("<Q", os.urandom(8))[0]',
        'struct.unpack_from(">Q", buf)[0]',
        "random.getrandbits(64)",
        "secrets.randbits(128)",
        'int.from_bytes(os.urandom(8), "little")',
        'int.from_bytes(hashlib.blake2b(b"x").digest(), "big")',
        "random.randint(0, 2**64 - 1)",
        "random.randrange(1 << 64)",
        "rng.integers(0, np.iinfo(np.uint64).max)",
        "rng.integers(0, 10, dtype=np.uint64)",
    ],
)
def test_each_wide_source_into_a_numba_seed_is_flagged(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "m.py", f"def f(rng, buf):\n    set_numba_random_seed({source})\n")
    assert len(_found(tmp_path)) == 1


def test_flow_through_a_local_name_and_int_wrapper(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        def scope():
            restore = struct.unpack("<Q", os.urandom(8))[0]
            try:
                pass
            finally:
                set_numba_random_seed(int(restore))
        """,
    )
    assert _found(tmp_path) == ["scope: set_numba_random_seed(int(restore)) can receive a seed >= 2**63 (restore = struct.unpack('<Q')); mask it to 63 bits"]


def test_njit_seed_function_defined_in_the_same_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        from numba import njit
        @njit(cache=True)
        def _seed(s):
            np.random.seed(s)
        def f():
            _seed(random.getrandbits(64))
        """,
    )
    assert len(_found(tmp_path)) == 1


@pytest.mark.parametrize(
    "source",
    [
        'struct.unpack("<Q", os.urandom(8))[0] & ((1 << 63) - 1)',
        'struct.unpack("<Q", os.urandom(8))[0] & _SEED_MASK',
        "random.getrandbits(64) % 2**63",
        "random.getrandbits(64) >> 1",
        "random.getrandbits(63)",
        'struct.unpack("<q", os.urandom(8))[0]',
        'int.from_bytes(os.urandom(4), "little")',
        "random.randint(0, 2**63 - 1)",
        "rng.integers(0, 2**31)",
        "42",
    ],
)
def test_narrow_or_masked_sources_are_not_flagged(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "m.py", f"_SEED_MASK = (1 << 63) - 1\ndef f(rng):\n    set_numba_random_seed({source})\n")
    assert _found(tmp_path) == []


@pytest.mark.parametrize(
    "call",
    [
        'np.random.seed(struct.unpack("<Q", os.urandom(8))[0] % 2**32)',
        "random.seed(random.getrandbits(64))",
        'cache_key(struct.unpack("<Q", os.urandom(8))[0])',
        'set_numba_random_seed(struct.unpack("<Q", os.urandom(8))[0])  # seed-range-ok: this build types the seed as uint64',
    ],
)
def test_non_numba_sinks_and_marker_are_not_flagged(tmp_path: Path, call: str) -> None:
    _write(tmp_path, "m.py", f"def f():\n    {call}\n")
    assert _found(tmp_path) == []


def test_tests_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "tests/h.py", "def f():\n    set_numba_random_seed(random.getrandbits(64))\n")
    assert _found(tmp_path) == []
    assert len(_found(tmp_path, include_tests=True)) == 1


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f():\n    set_numba_random_seed(random.getrandbits(64))\n", bom=True)
    assert len(_found(tmp_path)) == 1


def test_unparsable_and_empty_corpus_fail(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_numba_seeds_fit_int64(tmp_path, use_git=False)
    _write(tmp_path, "ok.py", "x = 1\n")
    (tmp_path / "bad.py").write_text("def (:\n", encoding="utf-8")
    assert [f.rule for f in find_wide_numba_seeds(tmp_path, use_git=False)] == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_numba_seeds_fit_int64(tmp_path, use_git=False)


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", "def f():\n    set_numba_random_seed(random.getrandbits(63))\n")
    assert_numba_seeds_fit_int64(src, use_git=False)
    _write(src, "m.py", "def f():\n    set_numba_random_seed(random.getrandbits(64))\n")
    with pytest.raises(pytest.fail.Exception, match="getrandbits"):
        assert_numba_seeds_fit_int64(src, use_git=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_numba_seeds_fit_int64(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_numba_seeds_fit_int64(src, baseline_path=baseline, refresh=True, use_git=False)
    assert_numba_seeds_fit_int64(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", "def f():\n    set_numba_random_seed(random.getrandbits(64))\n    set_numba_random_seed(random.getrandbits(64))\n")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_numba_seeds_fit_int64(src, baseline_path=baseline, refresh=False, use_git=False)
