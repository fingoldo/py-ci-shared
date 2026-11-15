"""Tests for py_ci_shared.cuda_offline_arch (pure string functions, no GPU)."""

from __future__ import annotations

import pytest

from py_ci_shared.cuda_offline_arch import offline_archs, require_enabled, resource_problems, toolchain_problem

PTX = ".visible .entry kern(\n)\n"
CLEAN = "ptxas info    : Function properties for kern\n    0 bytes stack frame, 0 bytes spill stores, 0 bytes spill loads\n"


def _report(stack=0, st=0, ld=0, helper=None):
    text = f"ptxas info    : Function properties for kern\n    {stack} bytes stack frame, {st} bytes spill stores, {ld} bytes spill loads\n"
    if helper:
        text += f"ptxas info    : Function properties for helper\n    {helper} bytes stack frame, 4 bytes spill stores, 4 bytes spill loads\n"
    return text


def test_archs_default_all_list_and_bad_value():
    kw = {"default": (61, 120), "every": (61, 70, 120)}
    assert offline_archs("V", environ={}, **kw) == (61, 120)
    assert offline_archs("V", environ={"V": "ALL"}, **kw) == (61, 70, 120)
    assert offline_archs("V", environ={"V": "75, 86"}, **kw) == (75, 86)
    with pytest.raises(ValueError, match="V="):
        offline_archs("V", environ={"V": "sm_75"}, **kw)


def test_require_enabled():
    assert require_enabled("R", environ={"R": "1"}) and not require_enabled("R", environ={"R": "0"}) and not require_enabled("R", environ={})


def test_clean_build_has_no_problems():
    assert resource_problems(PTX, CLEAN, arch=61) == []


def test_no_entry_and_missing_resource_block():
    assert "no .entry" in resource_problems("", CLEAN, arch=61)[0]
    assert "without a ptxas resource block" in resource_problems(PTX, "", arch=61)[0]


def test_entry_spill_and_stack_frame_reported_unless_allowed():
    assert "per entry" in resource_problems(PTX, _report(st=8), arch=70)[0]
    assert resource_problems(PTX, _report(stack=16), arch=70) == []  # a frame alone is not a spill (the original check compares spill stores and loads)
    assert resource_problems(PTX, _report(st=8), arch=70, allow_spill=True) == []


def test_helper_spill_is_reported():
    assert "device helper" in resource_problems(PTX, _report(helper=8), arch=61)[0]


def test_warning_allowlist():
    report = CLEAN + "nvrtc: warning: Architectures prior to sm_70 are deprecated\n"
    assert "unlisted" in resource_problems(PTX, report, arch=61)[0]
    assert resource_problems(PTX, report, arch=61, warning_allowlist=("Architectures prior to",)) == []


def test_launch_bounds_check_is_delegated_to_the_report_engine(monkeypatch):
    import py_ci_shared.cuda_offline_arch as mod

    monkeypatch.setattr(mod, "launch_bounds_violations", lambda ptx, report: [("kern", 255, 32)])
    problems = resource_problems(PTX, CLEAN, arch=61)
    assert len(problems) == 1 and "__launch_bounds__ not honoured" in problems[0]


def test_toolchain_problem_is_none_or_a_reason():
    why = toolchain_problem()
    assert why is None or (isinstance(why, str) and why)
