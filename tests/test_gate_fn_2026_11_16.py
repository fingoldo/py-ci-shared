"""Gate false negatives of audit 2026-11-16 (40-T2, 40-T3, 40-T10, 40-T14, 40-T17, 40-T18), one test group per finding."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core.errors import CorpusError
from py_ci_shared.code_audit import scan_bare_except
from py_ci_shared.guard_without_fixture import find_guards_without_fixture
from py_ci_shared.git_child_env import find_unscrubbed_git_spawns
from py_ci_shared.no_xfail_to_defer import find_xfail_to_defer
from py_ci_shared.repo_hygiene import find_workflows_dir_problems
from py_ci_shared.sentinel_or_fallback import find_sentinel_or_fallback
from py_ci_shared.skip_as_pass import find_skip_as_pass
from py_ci_shared.wallclock_test_waits import find_wallclock_test_waits


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")


# ---- 40-T2: text ratchets have a file floor -------------------------------------------------


def test_t2_skip_as_pass_wrong_root_is_an_error_not_a_pass(tmp_path: Path):
    _write(tmp_path, "src/app.py", "x = 1\n")  # no scripts/, tool/, .githooks/ or .github/
    with pytest.raises(CorpusError):
        find_skip_as_pass(tmp_path)


def test_t2_skip_as_pass_min_files_zero_accepts_a_repo_without_guards(tmp_path: Path):
    _write(tmp_path, "src/app.py", "x = 1\n")
    assert find_skip_as_pass(tmp_path, min_files=0) == {}


@pytest.mark.parametrize(
    "finder",
    [find_wallclock_test_waits, find_unscrubbed_git_spawns],
    ids=["wallclock_test_waits", "git_child_env"],
)
def test_t2_test_ratchets_refuse_a_root_with_no_test_files(tmp_path: Path, finder):
    _write(tmp_path, "src/app.py", "x = 1\n")
    with pytest.raises(CorpusError):
        finder(tmp_path)


def test_t2_guard_without_fixture_refuses_a_root_with_no_guards(tmp_path: Path):
    _write(tmp_path, "src/app.py", "x = 1\n")
    with pytest.raises(CorpusError):
        find_guards_without_fixture(tmp_path)


# ---- 40-T3: a mistyped workflows_dir is a problem -------------------------------------------


def test_t3_a_workflows_dir_that_does_not_exist_is_reported(tmp_path: Path):
    problems = find_workflows_dir_problems(tmp_path / ".github" / "workflow")
    assert problems and "does not exist" in problems[0]


def test_t3_an_existing_workflows_dir_is_not_a_problem(tmp_path: Path):
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "ci.yml").write_text("on: push\n", encoding="utf-8")
    assert find_workflows_dir_problems(d) == []


# ---- 40-T10: skip wording beyond "SKIPPED" --------------------------------------------------


@pytest.mark.parametrize("word", ["Skipping the check", "SKIP: no X", "skips the check"])
def test_t10_skip_wordings_are_recognised(tmp_path: Path, word: str):
    _write(tmp_path, "scripts/check.sh", f'#!/bin/sh\nif [ -z "$X" ]; then\n  echo "{word}"\n  exit 0\nfi\n')
    assert find_skip_as_pass(tmp_path), word


def test_t10_a_warning_mentioning_a_skip_is_still_not_a_skip_marker(tmp_path: Path):
    _write(tmp_path, "scripts/check.sh", '#!/bin/sh\necho "warning: deps were not checked"\nexit 0\n')
    assert find_skip_as_pass(tmp_path) == {}


# ---- 40-T14: more zero-is-a-value setting names ---------------------------------------------


@pytest.mark.parametrize("name", ["max_new_tokens", "num_workers", "n_estimators", "max_depth", "learning_rate"])
def test_t14_common_zero_is_a_value_settings_are_declared(tmp_path: Path, name: str):
    (tmp_path / "m.py").write_text(f"def f(x):\n    return x.{name} or 100\n", encoding="utf-8")
    findings, _ = find_sentinel_or_fallback(tmp_path, use_git=False)  # type: ignore[arg-type]
    assert findings, name


# ---- 40-T17: skipif with a constant-true condition is an unconditional skip -----------------


@pytest.mark.parametrize("condition", ["True", "1", "1 == 1"])
def test_t17_constant_true_skipif_is_judged_as_skip(tmp_path: Path, condition: str):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        f'import pytest\n@pytest.mark.skipif({condition}, reason="unfinished work")\ndef test_a():\n    pass\n',
        encoding="utf-8",
    )
    findings, _ = find_xfail_to_defer(tests, use_git=False)  # type: ignore[arg-type]
    assert findings, condition


def test_t17_an_environment_skipif_is_still_not_judged(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        'import sys, pytest\n@pytest.mark.skipif(sys.platform == "win32", reason="unfinished work")\ndef test_a():\n    pass\n',
        encoding="utf-8",
    )
    findings, _ = find_xfail_to_defer(tests, use_git=False)  # type: ignore[arg-type]
    assert findings == []


# ---- 40-T18: an upper-case .PY file is a Python source file for code_audit ------------------


def test_t18_upper_case_py_extension_is_scanned(tmp_path: Path):
    _write(tmp_path, "upper.PY", "def f():\n    try:\n        g()\n    except:\n        pass\n")
    assert [f.file for f in scan_bare_except(tmp_path)] == ["upper.PY"]


# ---- 40-T7: a re-raise must be the handler's last top-level statement -----------------------


def _bare_hits(tmp_path: Path, body: str) -> list:
    _write(tmp_path, "m.py", body)
    return scan_bare_except(tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        "try:\n    g()\nexcept* BaseException:\n    pass\n",
        "try:\n    g()\nexcept (BaseException, ValueError):\n    pass\n",
        "try:\n    g()\nexcept builtins.BaseException:\n    pass\n",
        "try:\n    g()\nexcept:\n    if x:\n        raise\n",
        "try:\n    g()\nexcept:\n    def h():\n        raise\n    pass\n",
    ],
    ids=["except-star", "tuple-with-base", "dotted-base", "conditional-reraise", "nested-def-reraise"],
)
def test_t7_a_handler_that_can_swallow_is_flagged(tmp_path: Path, body: str):
    assert _bare_hits(tmp_path, body), body


@pytest.mark.parametrize(
    "body",
    [
        "try:\n    g()\nexcept BaseException:\n    cleanup()\n    raise\n",
        "try:\n    g()\nexcept:\n    if x:\n        cleanup()\n    raise\n",
        "try:\n    g()\nexcept* BaseException:\n    cleanup()\n    raise\n",
    ],
    ids=["plain-cleanup-reraise", "conditional-then-reraise", "except-star-reraise"],
)
def test_t7_a_handler_that_always_re_raises_is_not_flagged(tmp_path: Path, body: str):
    assert _bare_hits(tmp_path, body) == []


def test_t7_conditional_reraise_in_broad_except_swallow_is_flagged(tmp_path: Path):
    from py_ci_shared.code_audit.broad_except import scan_broad_except_swallows

    _write(tmp_path, "m.py", "try:\n    g()\nexcept Exception:\n    if DEBUG:\n        raise\n")
    assert scan_broad_except_swallows(tmp_path)


# ---- 40-T8: mutable defaults beyond a def with a list/dict/set literal ----------------------


@pytest.mark.parametrize(
    "default",
    [
        "lambda x=[]: x",
        "def f(x=[0] * 3):\n    return x\n",
        "def f(x=[] + []):\n    return x\n",
        "def f(x=[i for i in r]):\n    return x\n",
        "def f(x={k: 1 for k in r}):\n    return x\n",
        "def f(x={k for k in r}):\n    return x\n",
        "def f(x=bytearray()):\n    return x\n",
        "def f(x=np.zeros(3)):\n    return x\n",
        "def f(x=np.array([1])):\n    return x\n",
        "def f(x=numpy.ones(3)):\n    return x\n",
    ],
    ids=["lambda", "list-times-int", "list-plus-list", "list-comp", "dict-comp", "set-comp", "bytearray", "np-zeros", "np-array", "numpy-ones"],
)
def test_t8_mutable_default_forms_are_flagged(tmp_path: Path, default: str):
    from py_ci_shared.code_audit.mutable_defaults import scan_mutable_defaults

    (tmp_path / "m.py").write_text(default, encoding="utf-8")
    assert scan_mutable_defaults(tmp_path), default


def test_t8_immutable_defaults_are_not_flagged(tmp_path: Path):
    from py_ci_shared.code_audit.mutable_defaults import scan_mutable_defaults

    (tmp_path / "m.py").write_text("def f(x=(1, 2), y=None, z=1 + 2):\n    return x\nlam = lambda a=3: a\n", encoding="utf-8")
    assert scan_mutable_defaults(tmp_path) == []


# ---- 40-T9: the alias-proof AST rule is the default -----------------------------------------


def test_t9_default_flags_a_from_time_import_sleep(tmp_path: Path):
    _write(tmp_path, "tests/test_a.py", "from time import sleep\n\n\ndef test_a():\n    sleep(5)\n")
    assert find_wallclock_test_waits(tmp_path), "the default must be the AST rule, which sees the alias"


def test_t7_except_star_broad_swallow_is_flagged(tmp_path: Path):
    from py_ci_shared.code_audit.broad_except import scan_broad_except_swallows

    _write(tmp_path, "m.py", "try:\n    g()\nexcept* Exception:\n    pass\n")
    assert scan_broad_except_swallows(tmp_path)
