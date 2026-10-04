"""Tests for py_ci_shared.unexecuted_function_bodies: a function whose body no test executed, read from a coverage.py JSON report."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.unexecuted_function_bodies import (
    RULE,
    STALE_EXEMPT_RULE,
    CoverageReportError,
    assert_unexecuted_function_bodies,
    find_unexecuted_function_bodies,
    main,
)

BOM = b"\xef\xbb\xbf"

PROBE = "def probe(conn):\n    cur = conn.cursor()\n    return cur.fetchone()\n"


def _lines(source: str, *needles: str) -> list[int]:
    """1-based line numbers of the lines whose stripped text equals each needle (the first match; fails when absent)."""
    rows = [row.strip() for row in source.splitlines()]
    return [rows.index(n) + 1 for n in needles]


def _write_report(
    tmp: Path, entries: dict[str, tuple[list[int], list[int]]], *, excluded: Optional[dict[str, list[int]]] = None, name: str = "coverage.json"
) -> Path:
    files = {key: {"executed_lines": ex, "missing_lines": miss, "excluded_lines": (excluded or {}).get(key, [])} for key, (ex, miss) in entries.items()}
    path = tmp / name
    path.write_text(json.dumps({"meta": {"format": 3, "version": "7.14.1"}, "files": files}), encoding="utf-8")
    return path


def _tree(tmp: Path, sources: dict[str, str]) -> Path:
    for rel, text in sources.items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text(text, encoding="utf-8")
    return tmp


def _find(tmp: Path, report: Path, **kw):
    return find_unexecuted_function_bodies(report, [tmp / "pkg"], base=tmp, use_git=False, **kw)


def _one_file(tmp: Path, source: str, executed: list[str], missing: list[str], **kw):
    """A package with one module; the report lists the statement lines named by *executed* and *missing* (stripped line text)."""
    _tree(tmp, {"pkg/mod.py": source})
    report = _write_report(tmp, {"pkg/mod.py": (_lines(source, *executed), _lines(source, *missing))})
    return _find(tmp, report, **kw)


# ---- the seeded defect ----


def test_reports_the_seeded_violation(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    report = _write_report(tmp_path, {"pkg/seed.py": ([1], [2, 3])})
    found = _find(tmp_path, report)
    assert [(f.path, f.line, f.rule) for f in found] == [("pkg/seed.py", 1, RULE)]
    assert found[0].message == "probe: body never executed by this test run (no body statement ran)"
    assert found[0].key == f"{RULE}::pkg/seed.py::probe"


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    assert _find(tmp_path, _write_report(tmp_path, {"pkg/seed.py": ([1, 2, 3], [])})) == []


def test_one_executed_body_line_is_enough(tmp_path):
    """The gate asks whether the body ever ran, not how much of it: partial coverage is the coverage floor's business."""
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    assert _find(tmp_path, _write_report(tmp_path, {"pkg/seed.py": ([1, 2], [3])})) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = tmp_path / "plain"
    bom = tmp_path / "bom"
    for d, prefix in ((plain, b""), (bom, BOM)):
        (d / "pkg").mkdir(parents=True)
        (d / "pkg" / "seed.py").write_bytes(prefix + PROBE.encode())
        (d / "coverage.json").write_bytes(prefix + json.dumps({"files": {"pkg/seed.py": {"executed_lines": [1], "missing_lines": [2, 3]}}}).encode())
    got_plain = find_unexecuted_function_bodies(plain / "coverage.json", [plain / "pkg"], base=plain, use_git=False)
    got_bom = find_unexecuted_function_bodies(bom / "coverage.json", [bom / "pkg"], base=bom, use_git=False)
    assert [(f.path, f.line, f.message) for f in got_bom] == [(f.path, f.line, f.message) for f in got_plain] != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE, "pkg/broken.py": "def broken(:\n"})
    report = _write_report(tmp_path, {"pkg/seed.py": ([1, 2, 3], [])})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        _find(tmp_path, report)
    assert _find(tmp_path, report, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    (tmp_path / "pkg").mkdir()
    report = _write_report(tmp_path, {"pkg/seed.py": ([1], [])})
    with pytest.raises(EmptyScanError):
        _find(tmp_path, report)


# ---- what a function body is ----


def test_stubs_docstrings_and_pragma_functions_are_skipped_but_real_bodies_are_not(tmp_path):
    source = (
        "def stub_pass():\n    pass\n\n"
        "def stub_dots():\n    ...\n\n"
        "def stub_doc():\n    '''only a docstring'''\n\n"
        "def stub_raise():\n    raise NotImplementedError\n\n"
        "def stub_raise_call():\n    raise NotImplementedError('later')\n\n"
        "def real():\n    '''doc'''\n    return 1\n"
    )
    found = _one_file(tmp_path, source, ["def real():"], ["return 1"])
    assert [f.message.split(":")[0] for f in found] == ["real"]


def test_a_docstring_and_pass_are_not_body_statements_even_beside_a_real_one(tmp_path):
    source = "def f(x):\n    '''doc'''\n    pass\n    return x\n"
    # `pass` ran in coverage's eyes but is inert: only `return x` decides, and it did not run.
    found = _one_file(tmp_path, source, ["def f(x):", "pass"], ["return x"])
    assert [f.message.split(":")[0] for f in found] == ["f"]


def test_a_function_with_every_statement_excluded_by_pragma_is_skipped(tmp_path):
    source = "def f():  # pragma: no cover\n    return 1\n\ndef g():\n    return 2\n"
    _tree(tmp_path, {"pkg/mod.py": source})
    report = _write_report(tmp_path, {"pkg/mod.py": ([4], [])}, excluded={"pkg/mod.py": [1, 2]})
    assert _find(tmp_path, report) == []


def test_a_pragma_on_the_body_only_leaves_the_rest_to_decide(tmp_path):
    source = "def f(x):\n    if x:\n        return 1  # pragma: no cover\n    return 2\n"
    _tree(tmp_path, {"pkg/mod.py": source})
    report = _write_report(tmp_path, {"pkg/mod.py": ([1], [2, 4])}, excluded={"pkg/mod.py": [3]})
    assert [f.message.split(":")[0] for f in _find(tmp_path, report)] == ["f"]


def test_methods_nested_and_async_functions_are_each_judged_with_their_qualified_name(tmp_path):
    source = (
        "class Outer:\n"
        "    class Inner:\n"
        "        def meth(self):\n"
        "            return 1\n"
        "    async def coro(self):\n"
        "        return 2\n"
        "def outer():\n"
        "    def inner():\n"
        "        return 3\n"
        "    return inner\n"
    )
    found = _one_file(
        tmp_path,
        source,
        ["class Outer:", "class Inner:", "def meth(self):", "async def coro(self):", "def outer():", "def inner():", "return inner"],
        ["return 1", "return 2", "return 3"],
    )
    # outer runs (its `def inner` and `return inner` statements) but the nested body never does.
    assert sorted(f.message.split(":")[0] for f in found) == ["Outer.Inner.meth", "Outer.coro", "outer.<locals>.inner"]


def test_the_outer_function_is_executed_even_when_only_its_nested_def_ran(tmp_path):
    source = "def outer():\n    def inner():\n        return 3\n    return inner\n"
    found = _one_file(tmp_path, source, ["def outer():", "def inner():", "return inner"], ["return 3"])
    assert [f.message.split(":")[0] for f in found] == ["outer.<locals>.inner"]


def test_a_lambda_is_not_a_function_here(tmp_path):
    source = "def f():\n    g = lambda: 1\n    return g\n"
    assert _one_file(tmp_path, source, ["def f():", "g = lambda: 1", "return g"], []) == []


# ---- default exemptions ----


def test_default_exemptions_abstract_overload_protocol_type_checking_and_display_dunders(tmp_path):
    source = (
        "import abc\nfrom typing import Protocol, TYPE_CHECKING, overload\n"
        "class Base(abc.ABC):\n"
        "    @abc.abstractmethod\n    def a(self):\n        return 1\n"
        "    @overload\n    def b(self, x):\n        return 2\n"
        "    def __repr__(self):\n        return 'x'\n"
        "class P(Protocol):\n"
        "    @property\n    def prop(self):\n        return 3\n"
        "if TYPE_CHECKING:\n"
        "    def only_for_types():\n        return 4\n"
    )
    missing = ["return 1", "return 2", "return 'x'", "return 3", "return 4"]
    assert _one_file(tmp_path, source, ["import abc", "class Base(abc.ABC):", "class P(Protocol):"], missing) == []


def test_init_is_checked_and_display_dunders_can_be_switched_on(tmp_path):
    source = "class C:\n    def __init__(self):\n        self.x = 1\n    def __repr__(self):\n        return 'c'\n"
    found = _one_file(tmp_path, source, ["class C:"], ["self.x = 1", "return 'c'"])
    assert [f.message.split(":")[0] for f in found] == ["C.__init__"]
    found = _find(tmp_path, tmp_path / "coverage.json", skip_dunders=())
    assert sorted(f.message.split(":")[0] for f in found) == ["C.__init__", "C.__repr__"]


def test_numba_decorated_bodies_are_skipped_by_default_and_checked_when_asked(tmp_path):
    source = "from numba import njit\n@njit\ndef kernel(x):\n    return x + 1\n"
    assert _one_file(tmp_path, source, ["from numba import njit", "@njit"], ["return x + 1"]) == []
    found = _find(tmp_path, tmp_path / "coverage.json", skip_decorators=())
    assert [f.message.split(":")[0] for f in found] == ["kernel"]


# ---- the report ----


def test_a_file_absent_from_the_report_counts_as_wholly_unexecuted_unless_skipped(tmp_path):
    _tree(tmp_path, {"pkg/seen.py": PROBE, "pkg/never_imported.py": "def lost():\n    return 1\n"})
    report = _write_report(tmp_path, {"pkg/seen.py": ([1, 2, 3], [])})
    found = _find(tmp_path, report)
    assert [(f.path, f.message) for f in found] == [
        ("pkg/never_imported.py", "lost: body never executed by this test run (its module is absent from the coverage report)")
    ]
    assert _find(tmp_path, report, absent_files="skip") == []
    with pytest.raises(ValueError, match="absent_files"):
        _find(tmp_path, report, absent_files="ignore")


def test_an_absent_file_honours_pragma_no_cover(tmp_path):
    _tree(tmp_path, {"pkg/seen.py": PROBE, "pkg/lost.py": "def lost():  # pragma: no cover\n    return 1\n"})
    assert _find(tmp_path, _write_report(tmp_path, {"pkg/seen.py": ([1, 2, 3], [])})) == []


def test_windows_separators_absolute_keys_and_a_separate_coverage_base_resolve(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    win = _write_report(tmp_path, {"pkg\\seed.py": ([1], [2, 3])}, name="win.json")
    assert [f.path for f in _find(tmp_path, win)] == ["pkg/seed.py"]
    absolute = _write_report(tmp_path, {str(tmp_path / "pkg" / "seed.py"): ([1], [2, 3])}, name="abs.json")
    assert [f.path for f in _find(tmp_path, absolute)] == ["pkg/seed.py"]
    elsewhere = tmp_path / "run"
    elsewhere.mkdir()
    relative_to_run = _write_report(elsewhere, {"../pkg/seed.py": ([1], [2, 3])})
    assert [f.path for f in _find(tmp_path, relative_to_run, coverage_base=elsewhere)] == ["pkg/seed.py"]


def test_a_missing_report_is_an_error_naming_the_fix(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    with pytest.raises(CoverageReportError, match=r"does not exist.*--cov-report=json"):
        _find(tmp_path, tmp_path / "coverage.json")


@pytest.mark.parametrize("text", ["", "{not json", "[]", '{"files": {}}', '{"meta": {}}'])
def test_an_unreadable_or_empty_report_is_an_error_never_a_pass(tmp_path, text):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    (tmp_path / "coverage.json").write_text(text, encoding="utf-8")
    with pytest.raises(CoverageReportError):
        _find(tmp_path, tmp_path / "coverage.json")


def test_a_report_without_line_data_is_an_error(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    (tmp_path / "coverage.json").write_text(json.dumps({"files": {"pkg/seed.py": {"summary": {}}}}), encoding="utf-8")
    with pytest.raises(CoverageReportError, match="executed_lines"):
        _find(tmp_path, tmp_path / "coverage.json")


def test_a_report_matching_too_few_source_files_is_an_error(tmp_path):
    _tree(tmp_path, {"pkg/a.py": PROBE, "pkg/b.py": PROBE})
    report = _write_report(tmp_path, {"pkg/a.py": ([1, 2, 3], []), "elsewhere/x.py": ([1], [])})
    assert _find(tmp_path, report, absent_files="skip") == []  # one match meets the default floor of 1
    with pytest.raises(CoverageReportError, match=r"only 1 source file\(s\) appear"):
        _find(tmp_path, report, min_files=2, absent_files="skip")


def test_a_report_from_another_checkout_matches_nothing_and_says_so(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    report = _write_report(tmp_path, {"/ci/workspace/pkg/seed.py": ([1], [2, 3])})
    with pytest.raises(CoverageReportError, match="does not describe this source tree"):
        _find(tmp_path, report)


def test_a_report_written_for_an_older_revision_of_a_file_is_an_error(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    report = _write_report(tmp_path, {"pkg/seed.py": ([1, 2, 3, 40], [])})
    with pytest.raises(CoverageReportError, match="different revision"):
        _find(tmp_path, report)
    assert _find(tmp_path, report, check_report_matches_source=False) == []


def test_a_report_older_than_the_newest_source_is_an_error_when_a_tolerance_is_set(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    report = _write_report(tmp_path, {"pkg/seed.py": ([1, 2, 3], [])})
    data = json.loads(report.read_text(encoding="utf-8"))
    data["meta"]["timestamp"] = "2001-01-01T00:00:00.000000"
    report.write_text(json.dumps(data), encoding="utf-8")
    assert _find(tmp_path, report) == []  # off by default
    with pytest.raises(CoverageReportError, match="older tree"):
        _find(tmp_path, report, stale_tolerance_seconds=60)


# ---- exempt ----


def test_exempt_removes_a_finding_and_a_stale_or_reasonless_entry_is_itself_a_finding(tmp_path):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    report = _write_report(tmp_path, {"pkg/seed.py": ([1], [2, 3])})
    assert _find(tmp_path, report, exempt={"pkg/seed.py::probe": "covered by the real-server test in tests/integration"}) == []
    stale = _find(tmp_path, report, exempt={"pkg/seed.py::probe": "reason", "pkg/seed.py::gone": "was removed"})
    assert [(f.rule, f.path) for f in stale] == [(STALE_EXEMPT_RULE, "pkg/seed.py")]
    assert "pkg/seed.py::gone" in stale[0].message
    blank = _find(tmp_path, report, exempt={"pkg/seed.py::probe": "  "})
    assert [f.rule for f in blank] == ["exempt-without-reason"]
    executed = _write_report(tmp_path, {"pkg/seed.py": ([1, 2, 3], [])}, name="ran.json")
    ran = _find(tmp_path, executed, exempt={"pkg/seed.py::probe": "reason"})
    assert [f.rule for f in ran] == [STALE_EXEMPT_RULE]


# ---- the ratchet ----


def _ratchet_tree(tmp: Path) -> tuple[Path, Path]:
    source = PROBE + "\n\ndef other(x):\n    return x\n"
    _tree(tmp, {"pkg/seed.py": source})
    report = _write_report(tmp, {"pkg/seed.py": ([1, 6], [2, 3, 7])})
    return report, tmp / "baseline.json"


def _assert(tmp: Path, report: Path, baseline: Path, **kw) -> None:
    assert_unexecuted_function_bodies(report, [tmp / "pkg"], baseline_path=baseline, base=tmp, use_git=False, **kw)


def test_without_a_baseline_every_finding_fails_and_the_message_names_it(tmp_path):
    report, _ = _ratchet_tree(tmp_path)
    with pytest.raises(AssertionError) as exc:
        assert_unexecuted_function_bodies(report, [tmp_path / "pkg"], base=tmp_path, use_git=False)
    assert "probe: body never executed" in str(exc.value) and "other: body never executed" in str(exc.value)


def test_a_missing_baseline_fails_and_names_the_refresh_command(tmp_path):
    report, baseline = _ratchet_tree(tmp_path)
    with pytest.raises(AssertionError, match="--refresh-baseline"):
        _assert(tmp_path, report, baseline)


def test_the_ratchet_accepts_the_baseline_fails_on_a_new_function_and_demands_removal_of_a_covered_one(tmp_path):
    report, baseline = _ratchet_tree(tmp_path)
    with pytest.raises(pytest.skip.Exception):
        _assert(tmp_path, report, baseline, refresh=True, grow=True)
    _assert(tmp_path, report, baseline)  # clean: both accepted
    # one function gains coverage: the baseline entry for it is now stale and must be removed
    covered = _write_report(tmp_path, {"pkg/seed.py": ([1, 2, 3, 6], [7])}, name="after.json")
    with pytest.raises(AssertionError, match=r"gained coverage.*\n.*unexecuted-function-bodies::pkg/seed.py::probe"):
        _assert(tmp_path, covered, baseline)
    # a refresh only shrinks: it drops the covered one ...
    with pytest.raises(pytest.skip.Exception):
        _assert(tmp_path, covered, baseline, refresh=True)
    assert list(json.loads(baseline.read_text(encoding="utf-8"))["entries"]) == [f"{RULE}::pkg/seed.py::other"]
    # ... and a new never-executed function is refused both by the check and by a refresh
    again = _write_report(tmp_path, {"pkg/seed.py": ([1, 6], [2, 3, 7])}, name="again.json")
    with pytest.raises(AssertionError, match=r"(?s)new finding.*probe"):
        _assert(tmp_path, again, baseline)
    with pytest.raises(AssertionError, match="probe"):
        _assert(tmp_path, again, baseline, refresh=True)


# ---- CLI ----


def _cli(tmp: Path, *extra: str) -> int:
    return main(["--coverage", str(tmp / "coverage.json"), "--src", str(tmp / "pkg"), "--base", str(tmp), *extra])


def test_cli_exit_codes_clean_finding_and_unusable_report(tmp_path, capsys):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    assert _cli(tmp_path) == 2 and "does not exist" in capsys.readouterr().err
    _write_report(tmp_path, {"pkg/seed.py": ([1, 2, 3], [])})
    assert _cli(tmp_path) == 0
    _write_report(tmp_path, {"pkg/seed.py": ([1], [2, 3])})
    assert _cli(tmp_path) == 1 and "probe" in capsys.readouterr().err


def test_cli_baseline_refresh_seeds_with_allow_grow_then_passes(tmp_path, capsys):
    _tree(tmp_path, {"pkg/seed.py": PROBE})
    _write_report(tmp_path, {"pkg/seed.py": ([1], [2, 3])})
    baseline = tmp_path / "b.json"
    assert _cli(tmp_path, "--baseline", str(baseline)) == 1  # missing baseline
    assert _cli(tmp_path, "--baseline", str(baseline), "--refresh-baseline") != 0  # seeding needs the growth opt-in
    capsys.readouterr()
    assert _cli(tmp_path, "--baseline", str(baseline), "--refresh-baseline", "--allow-grow") == 0
    assert _cli(tmp_path, "--baseline", str(baseline)) == 0


# ---- a real coverage run ----


@pytest.mark.skipif(
    shutil.which("coverage") is None and subprocess.run([sys.executable, "-m", "coverage", "--version"], capture_output=True).returncode != 0,
    reason="coverage.py is not installed",
)
def test_a_real_coverage_run_over_a_function_every_test_stubs_is_flagged(tmp_path):
    """The incident end to end: the only test patches the probe out, so coverage.py itself reports its body unexecuted."""
    _tree(
        tmp_path,
        {
            "minipkg/__init__.py": "",
            "minipkg/outcomes.py": (
                "def outcome_column_exists(conn):\n    cur = conn.cursor()\n    cur.execute('select 1 from moved_table')\n    return cur.fetchone() is not None\n\n\n"
                "def label_outcomes(conn):\n    if not outcome_column_exists(conn):\n        return 0\n    return 1\n"
            ),
            "tests/test_outcomes.py": (
                "from unittest.mock import patch\n\nfrom minipkg import outcomes\n\n\n"
                "def test_label_with_the_probe_stubbed():\n"
                "    with patch('minipkg.outcomes.outcome_column_exists', return_value=True):\n        assert outcomes.label_outcomes(None) == 1\n"
            ),
            "pytest.ini": "[pytest]\n",
        },
    )
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_FILE": str(tmp_path / ".coverage")}
    env.pop("PYTEST_ADDOPTS", None)
    run = subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--source=minipkg", "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly", "tests"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    out = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "coverage.json"], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8"
    )
    assert out.returncode == 0, out.stdout + out.stderr
    found = find_unexecuted_function_bodies(tmp_path / "coverage.json", [tmp_path / "minipkg"], base=tmp_path, use_git=False)
    assert [(f.path, f.message.split(":")[0]) for f in found] == [("minipkg/outcomes.py", "outcome_column_exists")]
