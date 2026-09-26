from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared.coverage_config_parity import RULE_NARROW, RULE_NUMBA, assert_coverage_config_parity, coverage_fail_under, find_coverage_config_violations


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


PYPROJECT = '[tool.coverage.report]\nfail_under = 80\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'


def _repo(tmp_path: Path, run: str, *, pyproject: str = PYPROJECT, extra: str = "", bom: bool = False) -> Path:
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    body = (
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        + extra
        + "    steps:\n      - name: Run\n        run: |\n"
        + textwrap.indent(textwrap.dedent(run).strip() + "\n", " " * 10)
    )
    (wf / "ci.yml").write_bytes((b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8"))
    return tmp_path


def _rules(root: Path, **kw: object) -> list[str]:
    return [f.rule for f in find_coverage_config_violations(root, **kw)]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "run",
    [
        "pytest tests/test_numbalib.py --cov=src/pkg --cov-report=xml",
        "pytest -m integration --cov=src/pkg",
        'pytest -k "slow" --cov=src/pkg',
        "pytest tests/ --splits 6 --group 1 --cov=src/pkg",
        "python -m pytest --lf --cov=src/pkg",
        "NUMBA_DISABLE_JIT=1 pytest tests/unit --cov=src/pkg \\\n  --cov-report=term",
        'cov_args=(--cov=pkg --cov-report=xml)\npytest tests/ --splits 3 --group 2 "${cov_args[@]}"',
        'other=(--cov-config=.rc)\ncov_args=(--cov=pkg)\npytest tests/unit "${cov_args[@]}"',
        "coverage combine data\ncoverage xml -o out.xml",
        "coverage report",
    ],
)
def test_narrow_coverage_run_inheriting_fail_under_is_flagged(tmp_path: Path, run: str) -> None:
    findings = find_coverage_config_violations(_repo(tmp_path, run), check_numba=False)
    assert [f.rule for f in findings] == [RULE_NARROW] * len(findings) and len(findings) >= 1
    assert "fail_under=80" in findings[0].message


@pytest.mark.parametrize(
    "run",
    [
        "pytest --cov=src/pkg --cov-report=xml",
        "pytest tests --cov=src/pkg",
        'pytest -m "not gpu" --cov=src/pkg',
        "pytest tests/unit",
        "pytest tests/unit --cov=src/pkg --cov-config=.coveragerc-derived",
        "pytest tests/unit --cov=src/pkg --cov-fail-under=80",
        "coverage report --rcfile=.coveragerc-merged",
        "pytest tests/unit --cov=src/pkg || true",
        "pip install pytest pytest-cov coverage",
        "pytest --cov=src/pkg\ncoverage xml",
        'cov_args=(--cov=pkg --cov-config=.coveragerc.narrow)\npytest tests/ --splits 3 --group 2 "${cov_args[@]}"',
        'cov_args=(\n  --cov=pkg\n  --cov-config="$DERIVED"\n)\npytest tests/unit "${cov_args[@]}"',
        'export COV="--cov=pkg --rcfile=.rc"\npytest tests/unit $COV',
    ],
)
def test_whole_suite_own_config_or_non_blocking_runs_are_not_flagged(tmp_path: Path, run: str) -> None:
    assert _rules(_repo(tmp_path, run), check_numba=False) == []


def test_continue_on_error_and_coverage_rcfile_env_exempt(tmp_path: Path) -> None:
    root = _repo(tmp_path, "pytest tests/unit --cov=pkg", extra="    continue-on-error: true\n")
    assert _rules(root, check_numba=False) == []
    root = _repo(tmp_path, "pytest tests/unit --cov=pkg", extra="    env:\n      COVERAGE_RCFILE: .coveragerc-derived\n")
    assert _rules(root, check_numba=False) == []


def test_no_fail_under_means_no_narrow_findings(tmp_path: Path) -> None:
    root = _repo(tmp_path, "pytest tests/unit --cov=pkg", pyproject="[tool.coverage.run]\nbranch = true\n")
    assert _rules(root, check_numba=False) == []


def test_fail_under_read_from_coveragerc_and_setup_cfg(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "setup.cfg").write_text("[coverage:report]\nfail_under = 60\n", encoding="utf-8")
    assert coverage_fail_under(tmp_path) == 60
    (tmp_path / ".coveragerc").write_text("[report]\nfail_under = 75\n", encoding="utf-8")
    assert coverage_fail_under(tmp_path) == 75


def test_numba_blind_coverage(tmp_path: Path) -> None:
    root = _repo(tmp_path, "pytest --cov=src/pkg")
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "k.py").write_text("from numba import njit\n", encoding="utf-8")
    assert _rules(root) == [RULE_NUMBA]
    root = _repo(tmp_path, "pytest --cov=src/pkg", extra="    env:\n      NUMBA_DISABLE_JIT: '1'\n")
    assert _rules(root) == []
    root = _repo(tmp_path, "NUMBA_DISABLE_JIT=1 pytest --cov=src/pkg")
    assert _rules(root) == []
    (root / "src" / "pkg" / "k.py").write_text("import numpy\n", encoding="utf-8")
    root = _repo(tmp_path, "pytest --cov=src/pkg")
    assert _rules(root) == []


def test_numba_imported_only_by_tests_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, "pytest --cov=src/pkg")
    (root / "tests").mkdir()
    (root / "tests" / "test_k.py").write_text("import numba\n", encoding="utf-8")
    assert _rules(root) == []


def test_bom_workflow_is_parsed(tmp_path: Path) -> None:
    assert _rules(_repo(tmp_path, "pytest tests/unit --cov=pkg", bom=True), check_numba=False) == [RULE_NARROW]


def test_unparsable_workflow_is_reported_and_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, "pytest --cov=pkg")
    (root / ".github" / "workflows" / "bad.yml").write_text("jobs: [unclosed\n", encoding="utf-8")
    assert "unparsed-file" in _rules(root, check_numba=False)
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_coverage_config_parity(root, check_numba=False)


def test_no_workflows_fails_the_floor(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_coverage_config_parity(tmp_path)


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    root = _repo(tmp_path, "pytest --cov=pkg")
    assert_coverage_config_parity(root, check_numba=False)
    root = _repo(tmp_path, "pytest tests/unit --cov=pkg")
    with pytest.raises(pytest.fail.Exception, match="inherits fail_under=80"):
        assert_coverage_config_parity(root, check_numba=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_coverage_config_parity(root, check_numba=False, baseline_path=baseline, refresh=False)
    with pytest.raises(pytest.skip.Exception):
        assert_coverage_config_parity(root, check_numba=False, baseline_path=baseline, refresh=True)
    assert_coverage_config_parity(root, check_numba=False, baseline_path=baseline, refresh=False)
    root = _repo(tmp_path, "pytest tests/unit --cov=pkg\npytest tests/other --cov=pkg")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_coverage_config_parity(root, check_numba=False, baseline_path=baseline, refresh=False)
