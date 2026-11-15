"""Tests for py_ci_shared.cuda_parity_test_shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import Baseline, EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_parity_test_shape import RULE, assert_cuda_parity_test_shape, find_cuda_parity_test_shape

HEAD = "import numpy as np\nimport pytest\n\n\n"


def _found(tmp_path: Path, body: str):
    (tmp_path / "test_m.py").write_text(HEAD + body, encoding="utf-8")
    return find_cuda_parity_test_shape(tmp_path, use_git=False)


def _names(found):
    return [f.message.split("`")[1] for f in found]


def test_single_literal_seed_is_reported(tmp_path):
    found = _found(
        tmp_path,
        "def test_gpu_matches_cpu():\n    x = np.random.default_rng(0).normal(size=9)\n    assert np.allclose(score_gpu(x), score_cpu(x))\n",
    )
    assert [(f.rule, f.line) for f in found] == [(RULE, 5)]


def test_size_loop_on_one_seed_is_reported(tmp_path):
    body = (
        "def test_cuda_equals_njit_for_every_size():\n    rng = np.random.default_rng(7)\n    for n in (1, 31):\n        x = rng.normal(size=n)\n"
        "        assert np.allclose(mi_cuda(x), mi_njit(x))\n"
    )
    assert _names(_found(tmp_path, body)) == ["test_cuda_equals_njit_for_every_size"]


def test_seed_sources_make_a_test_clean(tmp_path):
    body = (
        '@pytest.mark.parametrize("seed", [0, 1])\ndef test_a(seed):\n    x = np.random.default_rng(seed).normal(size=9)\n    assert np.allclose(f_gpu(x), f_cpu(x))\n\n\n'
        "def test_b():\n    for seed in (1, 2):\n        x = np.random.default_rng(seed).normal(size=9)\n        assert np.allclose(f_gpu(x), f_cpu(x))\n\n\n"
        "def test_c():\n    a = np.random.default_rng(1).normal(size=9)\n    b = np.random.default_rng(2).normal(size=9)\n    assert np.allclose(f_gpu(a), f_cpu(a))\n    assert np.allclose(f_gpu(b), f_cpu(b))\n\n\n"
        "def test_d(rng):\n    x = rng.normal(size=9)\n    assert np.allclose(f_gpu(x), f_cpu(x))\n"
    )
    assert _found(tmp_path, body) == []


def test_not_parity_shapes_are_ignored(tmp_path):
    body = (
        "def test_dispatch_falls_back():\n    x = np.random.default_rng(0).normal(size=9)\n    assert np.allclose(f_gpu(x), f_cpu(x))\n\n\n"
        "def test_no_random_data():\n    assert np.allclose(f_gpu(np.ones(3)), f_cpu(np.ones(3)))\n\n\n"
        "def test_gpu_only():\n    x = np.random.default_rng(0).normal(size=9)\n    assert np.allclose(f_gpu(x), x)\n\n\n"
        "def test_no_comparison():\n    x = np.random.default_rng(0).normal(size=9)\n    f_gpu(x)\n    f_cpu(x)\n\n\n"
        "def helper_gpu_vs_cpu():\n    x = np.random.default_rng(0).normal(size=9)\n    assert np.allclose(f_gpu(x), f_cpu(x))\n"
    )
    assert _found(tmp_path, body) == []


def test_backend_keyword_strings_count_as_the_two_sides(tmp_path):
    body = 'def test_equal():\n    x = np.random.default_rng(0).normal(size=9)\n    assert np.allclose(run(x, backend="cuda"), run(x, backend="njit"))\n'
    assert _names(_found(tmp_path, body)) == ["test_equal"]


def test_marker_needs_a_reason(tmp_path):
    base = "def test_gpu_vs_cpu():  # parity-ok:{r}\n    x = np.random.default_rng(0).normal(size=9)\n    assert np.allclose(f_gpu(x), f_cpu(x))\n"
    assert _found(tmp_path, base.format(r=" a fixed published dataset")) == []
    assert len(_found(tmp_path, base.format(r=""))) == 1


def test_assert_message_and_baseline(tmp_path):
    (tmp_path / "test_m.py").write_text(
        HEAD + "def test_gpu_vs_cpu():\n    x = np.random.default_rng(0).normal(size=9)\n    assert np.allclose(f_gpu(x), f_cpu(x))\n", encoding="utf-8"
    )
    with pytest.raises(AssertionError, match=r"test_m.py:5: \[cuda-parity-single-seed\] parity test `test_gpu_vs_cpu`"):
        assert_cuda_parity_test_shape(tmp_path, use_git=False)
    base = tmp_path / "b.json"
    found = find_cuda_parity_test_shape(tmp_path, use_git=False)
    Baseline(base, gate="cuda_parity_test_shape").save(Baseline.count(found), {k: "known" for k in Baseline.count(found)})
    assert_cuda_parity_test_shape(tmp_path, baseline_path=base, use_git=False)
    (tmp_path / "test_n.py").write_text(
        HEAD + "def test_other_gpu_vs_cpu():\n    x = np.random.default_rng(0).normal(size=9)\n    assert np.allclose(f_gpu(x), f_cpu(x))\n", encoding="utf-8"
    )
    with pytest.raises(BaseException, match="test_other_gpu_vs_cpu"):
        assert_cuda_parity_test_shape(tmp_path, baseline_path=base, use_git=False)


def test_floor_and_unparsed(tmp_path):
    with pytest.raises(EmptyScanError):
        find_cuda_parity_test_shape(tmp_path, use_git=False)
    (tmp_path / "test_bad.py").write_text("def test_(:\n", encoding="utf-8")
    (tmp_path / "test_ok.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError):
        find_cuda_parity_test_shape(tmp_path, use_git=False)
