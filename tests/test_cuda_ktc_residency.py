"""Tests for py_ci_shared.cuda_ktc_residency."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_ktc_residency import RULE, assert_cuda_ktc_residency, find_cuda_ktc_residency

IMP = "from pyutilz.performance.kernel_tuning.registry import kernel_tuner\n\n"


def _find(tmp_path: Path, text: str, **kw):
    (tmp_path / "m.py").write_text(text, encoding="utf-8")
    return find_cuda_ktc_residency(tmp_path, use_git=False, **kw)


def _names(found):
    return [f.message.split("`")[1] for f in found]


def test_undeclared_gpu_registration_is_reported_with_its_name(tmp_path):
    found = _find(tmp_path, IMP + 'S = kernel_tuner(kernel_name="pair_mi", tuner=t, gpu_capable=True)\n')
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 3, RULE)]
    assert "pair_mi" in found[0].message
    assert found[0].key == f"{RULE}::m.py::pair_mi"


def test_multiline_call_reports_the_first_line(tmp_path):
    found = _find(tmp_path, IMP + 'S = kernel_tuner(\n    kernel_name="a",\n    gpu_capable=True,\n    salt=1,\n)\n')
    assert [f.line for f in found] == [3]


def test_declarations_and_cpu_only_are_clean(tmp_path):
    text = (
        IMP
        + 'A = kernel_tuner(kernel_name="a", gpu_capable=True, residencies=(("host", "host"),))\n'
        + 'B = kernel_tuner(kernel_name="b", gpu_capable=True)  # residency-ok: one host boundary\n'
        + 'C = kernel_tuner(kernel_name="c")\n'
        + 'D = kernel_tuner(kernel_name="d", gpu_capable=False)\n'
    )
    assert _find(tmp_path, text) == []


def test_empty_marker_reason_does_not_suppress(tmp_path):
    assert len(_find(tmp_path, IMP + 'B = kernel_tuner(kernel_name="b", gpu_capable=True)  # residency-ok:\n')) == 1


def test_module_level_declaration_covers_the_module(tmp_path):
    text = IMP + '_SWEEP_RESIDENCIES = [("host", "host"), ("device", "device")]\nB = kernel_tuner(kernel_name="b", gpu_capable=True)\n'
    assert _find(tmp_path, text) == []


def test_import_aliases_and_module_attribute_calls(tmp_path):
    aliased = "from pyutilz.performance.kernel_tuning.registry import kernel_tuner as kt\n\nA = kt(kernel_name='a', gpu_capable=True)\n"
    module = "from pyutilz.performance.kernel_tuning import registry\n\nA = registry.kernel_tuner(kernel_name='a', gpu_capable=True)\n"
    assert len(_find(tmp_path, aliased)) == 1
    assert len(_find(tmp_path, module)) == 1


def test_spec_built_in_a_variable(tmp_path):
    undeclared = IMP + 'def reg():\n    spec = dict(kernel_name="a", gpu_capable=True)\n    return kernel_tuner(**spec)\n'
    declared = IMP + 'def reg():\n    spec = {"kernel_name": "a", "gpu_capable": True, "residencies": (("host", "host"),)}\n    return kernel_tuner(**spec)\n'
    unresolved = IMP + "def reg(spec):\n    return kernel_tuner(**spec)\n"
    assert _names(_find(tmp_path, undeclared)) == ["a"]
    assert _find(tmp_path, declared) == []
    assert "cannot be read" in _find(tmp_path, unresolved)[0].message


def test_gpu_flag_in_a_name_and_a_non_constant(tmp_path):
    named = IMP + 'GPU = True\nA = kernel_tuner(kernel_name="a", gpu_capable=GPU)\n'
    off = IMP + 'GPU = False\nA = kernel_tuner(kernel_name="a", gpu_capable=GPU)\n'
    dynamic = IMP + 'A = kernel_tuner(kernel_name="a", gpu_capable=_HAS_CUPY)\n'
    assert len(_find(tmp_path, named)) == 1
    assert _find(tmp_path, off) == []
    assert len(_find(tmp_path, dynamic)) == 1


def test_api_and_keyword_names_are_parameters(tmp_path):
    text = 'from other.cache import tune\n\nA = tune(name="a", use_gpu=True)\nB = tune(name="b", use_gpu=True, placements=(1,))\n'
    assert _find(tmp_path, text) == []
    found = _find(tmp_path, text, api_names=("tune",), gpu_keyword="use_gpu", name_keyword="name", declaring_keywords=("placements",))
    assert _names(found) == ["a"]


def test_assert_message_and_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    corpus = tmp_path / "src"
    corpus.mkdir()
    (corpus / "m.py").write_text(IMP + 'A = kernel_tuner(kernel_name="a", gpu_capable=True)\n', encoding="utf-8")
    with pytest.raises(AssertionError, match=r"m\.py:3: \[cuda-ktc-residency-undeclared\].*residency-ok"):
        assert_cuda_ktc_residency(corpus, use_git=False)
    base = tmp_path / "base.json"
    with pytest.raises(pytest.skip.Exception):
        assert_cuda_ktc_residency(corpus, baseline_path=base, refresh=True, use_git=False)
    assert_cuda_ktc_residency(corpus, baseline_path=base, use_git=False)
    (corpus / "n.py").write_text(IMP + 'A = kernel_tuner(kernel_name="z", gpu_capable=True)\n', encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_cuda_ktc_residency(corpus, baseline_path=base, use_git=False)


def test_unparsable_file_and_floor(tmp_path):
    (tmp_path / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError):
        find_cuda_ktc_residency(tmp_path, use_git=False)
    with pytest.raises(EmptyScanError):
        find_cuda_ktc_residency(tmp_path, use_git=False, min_files=5, allow_unparsed=True)
