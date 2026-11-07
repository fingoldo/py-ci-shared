"""Tests for py_ci_shared.njit_twin_untested: one case per rule, the test-evidence forms that satisfy it, and the shapes that must stay silent."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.njit_twin_untested import (
    RULE_FLAG,
    RULE_MARKER,
    RULE_NAME,
    RULE_REDEFINED,
    assert_njit_twin_untested,
    find_njit_twin_untested,
)

BOM = b"\xef\xbb\xbf"

FRAC_DIFF = """\
from numba import njit

_HAS_NUMBA = True


@njit
def _kernel(t, w):
    inv = 1.0 / w
    return t * inv


def inverse(t, w):
    if _HAS_NUMBA:
        return _kernel(t, w)
    out = t.copy()
    for i in range(t.size):
        out[i] = t[i] / w
    return out
"""


def _corpus(tmp_path: Path, files: "dict[str, bytes | str]") -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return tmp_path


def _rules(root: Path, **kw) -> "list[tuple[str, str, int]]":
    return [(f.rule, f.path, f.line) for f in find_njit_twin_untested(root, use_git=False, **kw)]


def test_the_frac_diff_shape_is_reported_with_the_flag_and_the_kernel(tmp_path):
    root = _corpus(tmp_path, {"mod.py": FRAC_DIFF})
    (finding,) = find_njit_twin_untested(root, use_git=False)
    assert (finding.rule, finding.path, finding.line) == (RULE_FLAG, "mod.py", 12)
    assert "`inverse` branches on _HAS_NUMBA and calls the njit kernel _kernel" in finding.message


def test_a_test_that_toggles_the_flag_and_names_the_dispatcher_satisfies_it(tmp_path):
    test = "import mod\n\ndef test_fallback_matches(monkeypatch):\n    monkeypatch.setattr(mod, '_HAS_NUMBA', False)\n    mod.inverse(1, 2)\n"
    assert _rules(_corpus(tmp_path, {"mod.py": FRAC_DIFF, "tests/test_mod.py": test})) == []


def test_a_test_that_only_names_the_dispatcher_does_not(tmp_path):
    test = "import mod\n\ndef test_runs():\n    mod.inverse(1, 2)\n"
    assert [r[0] for r in _rules(_corpus(tmp_path, {"mod.py": FRAC_DIFF, "tests/test_mod.py": test}))] == [RULE_FLAG]


def test_py_func_on_the_kernel_satisfies_a_flag_dispatcher(tmp_path):
    test = "import mod\n\ndef test_twin():\n    assert mod._kernel.py_func(1.0, 2.0) == mod._kernel(1.0, 2.0)\n"
    assert _rules(_corpus(tmp_path, {"mod.py": FRAC_DIFF, "tests/test_mod.py": test})) == []


def test_the_parity_helper_call_counts_as_evidence(tmp_path):
    test = "from py_ci_shared.njit_twin_parity import assert_njit_twin_parity\nimport mod\n\ndef test_p():\n    assert_njit_twin_parity(mod._kernel, lambda rng: (1.0, 2.0))\n"
    assert _rules(_corpus(tmp_path, {"mod.py": FRAC_DIFF, "tests/test_mod.py": test})) == []


def test_evidence_in_a_non_test_file_does_not_count(tmp_path):
    helper = "import mod\n\ndef check():\n    mod._kernel.py_func(1.0, 2.0)\n"
    assert len(_rules(_corpus(tmp_path, {"mod.py": FRAC_DIFF, "helpers.py": helper}))) == 1


def test_tests_root_supplies_the_evidence_when_tests_live_elsewhere(tmp_path):
    src = _corpus(tmp_path / "src", {"mod.py": FRAC_DIFF})
    tests = _corpus(tmp_path / "tests", {"test_mod.py": "import mod\n\ndef test_t():\n    mod._kernel.py_func(1.0, 2.0)\n"})
    assert _rules(src) != []
    assert _rules(src, tests_root=tests) == []


def test_a_flag_branch_that_calls_no_njit_function_is_not_a_twin(tmp_path):
    src = "_HAS_NUMBA = True\n\ndef f(x):\n    if _HAS_NUMBA:\n        return helper(x)\n    return x\n\ndef helper(x):\n    return x\n"
    assert _rules(_corpus(tmp_path, {"mod.py": src})) == []


def test_flag_spellings_are_recognised(tmp_path):
    for flag in ("HAS_NUMBA", "_NUMBA_AVAILABLE", "numba_available", "use_numba", "_have_numba"):
        src = f"from numba import njit\n\n@njit\ndef k(x):\n    return x\n\ndef f(x):\n    return k(x) if {flag} else x\n"
        root = _corpus(tmp_path / flag, {"mod.py": src})
        assert [r[0] for r in _rules(root)] == [RULE_FLAG], flag


def test_a_nested_function_is_judged_by_its_own_scope(tmp_path):
    src = (
        "from numba import njit\n\n@njit\ndef k(x):\n    return x\n\n"
        "def outer(x):\n    def inner(y):\n        return k(y)\n    return inner(x)\n\n"
        "def other(flag_numba):\n    if flag_numba:\n        return 1\n"
    )
    assert _rules(_corpus(tmp_path, {"mod.py": src})) == []


def test_name_pair_reported_and_satisfied_by_a_test_naming_both(tmp_path):
    src = "from numba import njit\n\n@njit\ndef fisher(x):\n    return x + 1\n\ndef _fisher_py(x):\n    return x + 1\n"
    assert _rules(_corpus(tmp_path / "a", {"mod.py": src})) == [(RULE_NAME, "mod.py", 4)]
    both = "import mod\n\ndef test_t():\n    assert mod.fisher(1) == mod._fisher_py(1)\n"
    assert _rules(_corpus(tmp_path / "b", {"mod.py": src, "tests/test_mod.py": both})) == []
    one = "import mod\n\ndef test_t():\n    assert mod.fisher(1) == 2\n"
    assert _rules(_corpus(tmp_path / "c", {"mod.py": src, "tests/test_mod.py": one})) == [(RULE_NAME, "mod.py", 4)]


def test_name_pair_in_reverse_njit_suffix_and_wrapper_exemption(tmp_path):
    twin = "from numba import njit\n\n@njit\ndef softmax_njit(x):\n    return x\n\ndef softmax(x):\n    return x\n"
    assert _rules(_corpus(tmp_path / "a", {"mod.py": twin})) == [(RULE_NAME, "mod.py", 4)]
    wrapper = "from numba import njit\n\n@njit\ndef softmax_njit(x):\n    return x\n\ndef softmax(x):\n    return softmax_njit(x)\n"
    assert _rules(_corpus(tmp_path / "b", {"mod.py": wrapper})) == []


def test_redefined_name_reported_and_satisfied_by_py_func(tmp_path):
    src = "from numba import njit\n\nif True:\n\n    @njit\n    def f(x):\n        return x\n\nelse:\n\n    def f(x):\n        return x\n"
    assert _rules(_corpus(tmp_path / "a", {"mod.py": src})) == [(RULE_REDEFINED, "mod.py", 6)]
    both_njit = "from numba import njit\n\n@njit\ndef f(x):\n    return x\n\n@njit\ndef f(x):\n    return x\n"
    assert _rules(_corpus(tmp_path / "b", {"mod.py": both_njit})) == []
    test = "import mod\n\ndef test_t():\n    mod.f.py_func(1)\n"
    assert _rules(_corpus(tmp_path / "c", {"mod.py": src, "tests/test_mod.py": test})) == []


def test_parity_marker_is_an_obligation_with_no_other_twin_signal(tmp_path):
    src = "from numba import njit\n\n@njit  # parity-test: the notebook loop\ndef lonely(x):\n    return x\n"
    assert _rules(_corpus(tmp_path / "a", {"mod.py": src})) == [(RULE_MARKER, "mod.py", 4)]
    test = "import mod\n\ndef test_t():\n    mod.lonely.py_func(1)\n"
    assert _rules(_corpus(tmp_path / "b", {"mod.py": src, "tests/test_mod.py": test})) == []


def test_an_njit_function_with_no_twin_signal_is_never_reported(tmp_path):
    src = "from numba import njit\n\n@njit(cache=True)\ndef lonely(x):\n    return x\n"
    assert _rules(_corpus(tmp_path, {"mod.py": src})) == []


def test_waiver_marker_silences_each_rule(tmp_path):
    flag = FRAC_DIFF.replace("def inverse(t, w):", "def inverse(t, w):  # parity-ok: the fallback is a documented approximation")
    assert _rules(_corpus(tmp_path / "a", {"mod.py": flag})) == []
    pair = "from numba import njit\n\n# parity-ok: reference is for plots only\n@njit\ndef fisher(x):\n    return x\n\ndef _fisher_py(x):\n    return x\n"
    assert _rules(_corpus(tmp_path / "b", {"mod.py": pair})) == []


def test_skip_dirs_and_bom(tmp_path):
    assert _rules(_corpus(tmp_path / "a", {"benchmarks/mod.py": FRAC_DIFF, "ok.py": "x = 1"})) == []
    bom = _corpus(tmp_path / "b", {"mod.py": BOM + FRAC_DIFF.encode("utf-8")})
    assert [r[0] for r in _rules(bom)] == [RULE_FLAG]


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"mod.py": FRAC_DIFF})
    with pytest.raises(AssertionError):
        assert_njit_twin_untested(root, use_git=False)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_njit_twin_untested(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_njit_twin_untested(root, baseline_path=baseline, use_git=False)
    (root / "other.py").write_bytes(FRAC_DIFF.replace("inverse", "other_inverse").encode("utf-8"))
    with pytest.raises(pytest.fail.Exception):
        assert_njit_twin_untested(root, baseline_path=baseline, use_git=False)


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"mod.py": FRAC_DIFF, "broken.py": "def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_njit_twin_untested(root, use_git=False)
    assert [r[0] for r in _rules(root, allow_unparsed=True)] == [RULE_FLAG]


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_njit_twin_untested(tmp_path, use_git=False)
