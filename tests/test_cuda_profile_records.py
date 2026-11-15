"""Tests for py_ci_shared.cuda_profile_records."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import Baseline, EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_profile_records import (
    DEFAULT_REQUIRED,
    EXTENDED_REQUIRED,
    RULE_INCOMPLETE,
    RULE_MISSING,
    assert_cuda_profile_records,
    find_cuda_profile_records,
)

KERNEL = 'import cupy as cp\n\n_SRC = r"""\nextern "C" __global__ void k(float* x) { x[0] = 1.0f; }\n"""\nK = cp.RawKernel(_SRC, "k")\n'
COMPLETE = "cProfile top 20. ncu --set full. 3 launches per call. occupancy 0.9. DRAM throughput 4 GB/s.\n"


def _find(root, **kw):
    kw.setdefault("profiles_dir", "profiles")
    return find_cuda_profile_records(root, use_git=False, **kw)


def _corpus(tmp_path: Path, modules: dict, records: dict) -> Path:
    for name, text in modules.items():
        (tmp_path / "pkg").mkdir(exist_ok=True)
        (tmp_path / "pkg" / name).write_text(text, encoding="utf-8")
    (tmp_path / "profiles").mkdir(exist_ok=True)
    for name, text in records.items():
        (tmp_path / "profiles" / name).write_text(text, encoding="utf-8")
    return tmp_path / "pkg"


def _seed(root, base):
    found = _find(root)
    Baseline(base, gate="cuda_profile_records").save(Baseline.count(found), {k: "known" for k in Baseline.count(found)})


def test_missing_record_is_reported_with_module_and_record_name(tmp_path):
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL}, {})
    found = _find(root)
    assert [(f.path, f.rule) for f in found] == [("_a_gpu.py", RULE_MISSING)]
    assert "profiles/_a_gpu.md" in found[0].message


def test_incomplete_record_names_each_missing_field(tmp_path):
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL}, {"_a_gpu.md": "cProfile and nvprof only.\n"})
    found = _find(root)
    assert sorted(f.rule for f in found) == sorted(f"{RULE_INCOMPLETE}:{n}" for n in ("launches-per-call", "occupancy", "throughput"))
    assert all("_a_gpu.md" in f.path for f in found)


def test_complete_record_and_cpu_only_module_are_clean(tmp_path):
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL, "_cpu.py": "def f():\n    return 1\n"}, {"_a_gpu.md": COMPLETE})
    assert _find(root) == []


def test_benchmarks_are_excluded(tmp_path):
    root = _corpus(tmp_path, {}, {})
    root.mkdir(exist_ok=True)
    (root / "_benchmarks").mkdir()
    (root / "_benchmarks" / "bench_gpu.py").write_text(KERNEL, encoding="utf-8")
    (root / "m.py").write_text("x = 1\n", encoding="utf-8")
    assert _find(root) == []


def test_family_map_uses_the_parent_record_once(tmp_path):
    root = _corpus(tmp_path, {"_p.py": KERNEL, "_child.py": KERNEL}, {"_p.md": "cProfile only"})
    found = _find(root, family_map={"_child": "_p"})
    assert len([f for f in found if f.rule.startswith(RULE_INCOMPLETE)]) == 4  # the shared record is reported once, not once per module
    assert _find(root, family_map={"_child": "_p"}, required={"cprofile": "cProfile"}) == []


def test_exempt_needs_a_reason_and_skips_the_module(tmp_path):
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL}, {})
    assert _find(root, exempt={"_a_gpu": "prototype, never shipped"}) == []
    with pytest.raises(ValueError, match="reason"):
        _find(root, exempt={"_a_gpu": "  "})


def test_required_is_a_parameter_and_extended_set_is_stricter(tmp_path):
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL}, {"_a_gpu.md": COMPLETE})
    assert _find(root, required=DEFAULT_REQUIRED) == []
    rules = {f.rule for f in _find(root, required=EXTENDED_REQUIRED)}
    assert f"{RULE_INCOMPLETE}:occupancy-limiter" in rules and f"{RULE_INCOMPLETE}:kernel-copy-share" in rules


def test_default_profiles_dir_is_found_in_an_ancestor(tmp_path):
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL}, {})
    record_dir = tmp_path / "audits" / "2026-10-22" / "profiles"
    record_dir.mkdir(parents=True)
    (record_dir / "_a_gpu.md").write_text(COMPLETE, encoding="utf-8")
    assert find_cuda_profile_records(root, use_git=False) == []


def test_assert_names_the_record_and_the_field(tmp_path):
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL}, {"_a_gpu.md": "cProfile only"})
    with pytest.raises(AssertionError, match=r"lacks the field `occupancy`"):
        assert_cuda_profile_records(root, profiles_dir="profiles", use_git=False)


def test_baseline_accepts_known_missing_and_refuses_new(tmp_path):
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL}, {})
    base = tmp_path / "baseline.json"
    _seed(root, base)
    assert_cuda_profile_records(root, profiles_dir="profiles", baseline_path=base, use_git=False)
    (root / "_b_gpu.py").write_text(KERNEL, encoding="utf-8")
    with pytest.raises(BaseException, match="_b_gpu"):
        assert_cuda_profile_records(root, profiles_dir="profiles", baseline_path=base, use_git=False)


def test_floor_and_unparsed_are_failures(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(EmptyScanError):
        _find(empty)
    root = _corpus(tmp_path, {"_a_gpu.py": KERNEL, "broken.py": "def f(:\n"}, {"_a_gpu.md": COMPLETE})
    with pytest.raises(UnparsedFilesError):
        _find(root)
