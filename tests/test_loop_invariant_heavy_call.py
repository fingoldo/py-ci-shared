"""Tests for the opt-in heavy-call rule set of py_ci_shared.loop_invariant_cast (``heavy=True``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import UnparsedFilesError
from py_ci_shared.loop_invariant_cast import (
    DEFAULT_HEAVY_FUNCS,
    HEAVY_RULE,
    assert_loop_invariant_cast,
    find_loop_invariant_cast,
)

BOM = b"\xef\xbb\xbf"

VIOLATION = "import numpy as np\n\n\ndef rank_all(names, y):\n    for name in names:\n        print(name, np.argsort(y))\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str, **kw) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_loop_invariant_cast(root, use_git=False, heavy=True, **kw) if f.rule == HEAVY_RULE]


def _loop(body: str, params: str = "names, y, q", header: str = "for name in names:", pre: str = "") -> str:
    return "import numpy as np\n\n\ndef f(" + params + "):\n" + pre + f"    {header}\n" + "".join(f"        {ln}\n" for ln in body.splitlines())


def test_reports_the_seeded_violation_and_is_off_by_default(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_loop_invariant_cast(root, use_git=False, heavy=True)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 6, HEAVY_RULE)
    assert "np.argsort(y)" in finding.message
    assert find_loop_invariant_cast(root, use_git=False) == []
    assert_loop_invariant_cast(root, use_git=False)
    with pytest.raises(AssertionError, match=r"m\.py:6"):
        assert_loop_invariant_cast(root, use_git=False, heavy=True)


@pytest.mark.parametrize(
    "call",
    [
        "np.argsort(y)",
        "np.sort(y)",
        "np.unique(y)",
        "np.lexsort((y, z))",
        "np.quantile(y, 0.5)",
        "np.percentile(y, 50)",
        "np.median(y)",
        "np.bincount(y)",
        "np.cumsum(y)",
        "np.corrcoef(y, z)",
        "rankdata(y)",
        "y.argsort()",
        "y.unique()",
        "y.cumsum()",
    ],
)
def test_every_heavy_spelling_is_reported_when_all_operands_are_invariant(tmp_path, call):
    src = "import numpy as np\nfrom scipy.stats import rankdata\n\n\ndef f(names, y, z):\n    for name in names:\n        print(" + call + ")\n"
    assert _lines(tmp_path, src) == [7]


def test_aliases_are_resolved(tmp_path):
    src = "import numpy as xp\nfrom numpy import argsort as order\n\n\ndef f(names, y):\n    for n in names:\n        xp.sort(y)\n        order(y)\n"
    assert _lines(tmp_path, src) == [7, 8]


def test_a_while_loop_and_a_nested_attribute_operand_are_reported(tmp_path):
    src = "import numpy as np\n\n\ndef f(self, y):\n    while self.go:\n        np.median(self.vals)\n        self.step()\n"
    assert _lines(tmp_path, src) == [6]


def test_a_variant_operand_is_not_reported(tmp_path):
    assert _lines(tmp_path, _loop("np.sort(name)")) == []
    assert _lines(tmp_path, _loop("y = y + 1\nnp.sort(y)")) == []
    assert _lines(tmp_path, _loop("y[0] = 1\nnp.sort(y)")) == []
    assert _lines(tmp_path, _loop("y += 1\nnp.sort(y)")) == []
    assert _lines(tmp_path, _loop("y.append(1)\nnp.sort(y)")) == []
    assert _lines(tmp_path, _loop("rng.shuffle(y)\nnp.sort(y)", params="names, y, rng")) == []
    assert _lines(tmp_path, _loop("np.sort(y[name])")) == []
    assert _lines(tmp_path, _loop("np.sort(y * name)")) == []
    assert _lines(tmp_path, _loop("np.sort(y)", header="for name, y in pairs:", params="pairs")) == []
    assert _lines(tmp_path, _loop("with open(p) as y:\n    np.sort(y)", params="names, p")) == []


def test_one_variant_positional_operand_among_invariant_ones_blocks_the_report(tmp_path):
    assert _lines(tmp_path, _loop("np.lexsort((y, name))")) == []
    assert _lines(tmp_path, _loop("np.corrcoef(y, name)")) == []


def test_a_call_operand_is_not_reported(tmp_path):
    assert _lines(tmp_path, _loop("np.sort(rng.choice(y, 3))", params="names, y, rng")) == []
    assert _lines(tmp_path, _loop("np.sort(np.asarray(y))")) == []
    assert _lines(tmp_path, _loop("np.sort(f2(y))", params="names, y, f2")) == []


def test_an_operand_with_a_call_hides_only_the_outer_call(tmp_path):
    assert _lines(tmp_path, _loop("np.sort(np.unique(y))")) == [6]


def test_a_variant_keyword_value_blocks_the_report_and_an_invariant_one_does_not(tmp_path):
    assert _lines(tmp_path, _loop("np.sort(y, axis=name)")) == []
    assert _lines(tmp_path, _loop("np.quantile(y, q=name)")) == []
    assert _lines(tmp_path, _loop("np.bincount(y, weights=w[name], minlength=3)", params="names, y, w")) == []
    assert _lines(tmp_path, _loop("np.bincount(y, weights=w, minlength=3)", params="names, y, w")) == [6]
    assert _lines(tmp_path, _loop('np.argsort(y, kind="mergesort")')) == [6]


def test_a_call_with_only_keyword_operands_is_not_reported(tmp_path):
    assert _lines(tmp_path, _loop("np.unique(ar=y)")) == []


def test_the_loop_iterable_is_not_the_loop_body(tmp_path):
    assert _lines(tmp_path, _loop("print(name)", header="for name in np.unique(y):")) == []


def test_the_iterable_of_an_inner_loop_runs_once_per_outer_iteration(tmp_path):
    src = "import numpy as np\n\n\ndef f(outer, inner_src):\n    for o in outer:\n        for i in np.unique(inner_src):\n            print(i)\n"
    assert _lines(tmp_path, src) == [6]
    varying = src.replace("np.unique(inner_src)", "np.unique(o)")
    assert _lines(tmp_path, varying) == []


def test_invariance_is_judged_against_the_innermost_loop(tmp_path):
    src = "import numpy as np\n\n\ndef f(outer, items):\n    for o in outer:\n        y = make(o)\n        for it in items:\n            np.sort(y)\n"
    assert _lines(tmp_path, src) == [8]


def test_if_elif_dispatch_on_a_loop_variable_is_not_reported(tmp_path):
    body = "if name == 'a':\n    np.sort(y)\nelif name == 'b':\n    np.unique(y)"
    assert _lines(tmp_path, _loop(body)) == []
    ternary = "out = np.sort(y) if name else None"
    assert _lines(tmp_path, _loop(ternary)) == []


def test_an_if_on_an_invariant_flag_is_still_reported(tmp_path):
    body = "if flag:\n    np.sort(y)"
    assert _lines(tmp_path, _loop(body, params="names, y, flag")) == [7]
    in_else_of_variant = "if name:\n    pass\nelse:\n    np.sort(y)"
    assert _lines(tmp_path, _loop(in_else_of_variant)) == []


def test_a_function_defined_inside_the_loop_is_not_a_loop_body(tmp_path):
    body = "def helper():\n    return np.sort(y)"
    assert _lines(tmp_path, _loop(body)) == []
    lam = "g = lambda: np.sort(y)"
    assert _lines(tmp_path, _loop(lam)) == []


def test_a_loop_inside_a_nested_function_is_reported_once(tmp_path):
    src = "import numpy as np\n\n\ndef outer(y):\n    def inner(names):\n        for n in names:\n            np.sort(y)\n    return inner\n"
    assert _lines(tmp_path, src) == [7]


def test_a_call_outside_any_loop_is_not_reported(tmp_path):
    assert _lines(tmp_path, "import numpy as np\n\n\ndef f(y):\n    return np.sort(y)\n") == []
    assert _lines(tmp_path, "import numpy as np\n\nY = [np.sort(y) for y in range(3)]\n") == []


def test_unrelated_names_are_not_heavy(tmp_path):
    assert _lines(tmp_path, "import numpy as np\n\n\ndef f(names, y):\n    for n in names:\n        np.zeros(3)\n        sorted(y)\n") == []
    assert _lines(tmp_path, "def f(names, y):\n    for n in names:\n        y.sort()\n") == []
    assert _lines(tmp_path, "import json\n\n\ndef f(names, y):\n    for n in names:\n        json.median(y)\n") == []


def test_a_method_on_an_imported_module_alias_is_not_a_receiver(tmp_path):
    src = "import mylib\n\n\ndef f(names):\n    for n in names:\n        mylib.unique()\n"
    assert _lines(tmp_path, src) == []


def test_marker_on_any_line_of_the_call_suppresses_with_or_without_a_reason(tmp_path):
    same = VIOLATION.replace("np.argsort(y))", "np.argsort(y))  # loop-invariant-ok")
    assert _lines(tmp_path, same) == []
    reasoned = VIOLATION.replace("np.argsort(y))", "np.argsort(y))  # loop-invariant-ok: y mutated by the callee")
    assert _lines(tmp_path, reasoned) == []
    multiline = "import numpy as np\n\n\ndef f(names, y):\n    for n in names:\n        np.quantile(\n            y,  # loop-invariant-ok\n            0.5,\n        )\n"
    assert _lines(tmp_path, multiline) == []
    elsewhere = VIOLATION + "\n# loop-invariant-ok\n"
    assert _lines(tmp_path, elsewhere) == [6]


def test_custom_heavy_funcs(tmp_path):
    src = "import numpy as np\n\n\ndef f(names, y):\n    for n in names:\n        np.sort(y)\n        mylib.slow(y)\n"
    assert _lines(tmp_path, src, heavy_funcs={"mylib.slow"}) == [7]
    assert _lines(tmp_path, src) == [6]
    src2 = "import mylib\n\n\ndef f(names, y):\n    for n in names:\n        mylib.slow(y)\n"
    assert _lines(tmp_path, src2, heavy_funcs={"mylib.slow"}) == [6]
    assert "numpy.argsort" in DEFAULT_HEAVY_FUNCS


def test_heavy_findings_come_beside_cast_findings_and_the_cast_rule_is_unchanged(tmp_path):
    src = "import numpy as np\n\n\ndef f(names, y):\n    for n in names:\n        a = y.astype('int64')\n        b = np.argsort(y)\n" "        print(a, b)\n"
    root = _corpus(tmp_path, {"m.py": src.encode()})
    both = find_loop_invariant_cast(root, use_git=False, heavy=True)
    assert [(f.line, f.rule) for f in both] == [(6, "loop-invariant-cast"), (7, HEAVY_RULE)]
    assert [(f.line, f.rule) for f in find_loop_invariant_cast(root, use_git=False)] == [(6, "loop-invariant-cast")]


def test_a_bom_prefixed_and_a_crlf_file_are_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"bom.py": BOM + VIOLATION.encode(), "crlf.py": VIOLATION.replace("\n", "\r\n").encode()})
    found = find_loop_invariant_cast(root, use_git=False, heavy=True)
    assert sorted((f.path, f.line) for f in found) == [("bom.py", 6), ("crlf.py", 6)]


def test_a_crlf_file_still_honours_the_marker(tmp_path):
    src = VIOLATION.replace("np.argsort(y))", "np.argsort(y))  # loop-invariant-ok").replace("\n", "\r\n")
    root = _corpus(tmp_path, {"m.py": src.encode()})
    assert find_loop_invariant_cast(root, use_git=False, heavy=True) == []


def test_exclude_applies_to_the_heavy_rule(tmp_path):
    root = _corpus(tmp_path, {"bench/m.py": VIOLATION.encode(), "lib/m.py": VIOLATION.encode()})
    found = find_loop_invariant_cast(root, use_git=False, heavy=True, exclude=("bench/",))
    assert [f.path for f in found] == ["lib/m.py"]


def test_a_baseline_accepts_the_known_heavy_finding_and_rejects_a_new_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": VIOLATION.encode()})
    base = tmp_path / "baseline.json"
    with pytest.raises(pytest.fail.Exception):
        assert_loop_invariant_cast(root, baseline_path=base, use_git=False, heavy=True)
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten"):
        assert_loop_invariant_cast(root, baseline_path=base, refresh=True, use_git=False, heavy=True)
    assert_loop_invariant_cast(root, baseline_path=base, use_git=False, heavy=True)
    (root / "n.py").write_bytes(VIOLATION.encode())
    with pytest.raises(pytest.fail.Exception):
        assert_loop_invariant_cast(root, baseline_path=base, use_git=False, heavy=True)


def test_an_unparsable_file_still_fails_by_name_with_heavy(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_loop_invariant_cast(root, use_git=False, heavy=True)
