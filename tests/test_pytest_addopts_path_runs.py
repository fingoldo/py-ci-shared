from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.pytest_addopts_path_runs import RULE, assert_path_runs_select_tests, find_path_runs_selecting_nothing, read_addopts


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


PYPROJECT = "[tool.pytest.ini_options]\naddopts = \"-ra -m 'not integration'\"\n"


def _pkg(root: Path, *, pyproject: str = PYPROJECT, bom: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    tests = root / "tests"
    (tests / "integration").mkdir(parents=True, exist_ok=True)
    prefix = b"\xef\xbb\xbf" if bom else b""
    (tests / "integration" / "test_db.py").write_bytes(
        prefix + b"import pytest\npytestmark = pytest.mark.integration\ndef test_a():\n    pass\ndef test_b():\n    pass\n"
    )
    (tests / "test_mixed.py").write_bytes(
        b"import pytest\n@pytest.mark.integration\ndef test_live():\n    pass\nclass TestUnit:\n    def test_u(self):\n        pass\n"
    )
    (tests / "test_only_live.py").write_bytes(b"import pytest\n@pytest.mark.integration\ndef test_live():\n    pass\n")
    return root


def _found(root: Path, command: str, **kw: object) -> list[str]:
    findings = find_path_runs_selecting_nothing(root, commands={"pre-commit::hook": (command, "hook")}, **kw)  # type: ignore[arg-type]
    return [f.message for f in findings if f.rule == RULE]


def test_path_run_inheriting_addopts_marker_selects_nothing(tmp_path: Path) -> None:
    root = _pkg(tmp_path)
    assert _found(root, "pytest tests/integration/test_db.py") == [
        "pre-commit::hook: `tests/integration/test_db.py` reaches 2 test(s) and -m 'not integration' (inherited from addopts) deselects every one"
    ]


def test_each_named_path_is_judged_on_its_own(tmp_path: Path) -> None:
    root = _pkg(tmp_path)
    found = _found(root, "pytest tests/test_mixed.py tests/test_only_live.py tests/integration")
    assert [m.split("`")[1] for m in found] == ["tests/integration", "tests/test_only_live.py"]


def test_node_ids_are_resolved(tmp_path: Path) -> None:
    root = _pkg(tmp_path)
    assert len(_found(root, "pytest tests/test_mixed.py::test_live")) == 1
    assert _found(root, "pytest tests/test_mixed.py::TestUnit") == []


@pytest.mark.parametrize(
    "command",
    [
        "pytest -m integration tests/integration/test_db.py",
        "pytest tests/integration -m 'integration or not integration'",
        'pytest -o "addopts=" tests/integration',
        "pytest --override-ini=addopts= tests/integration",
        "pytest tests/test_mixed.py",
        "pytest -m integration",
        "pytest tests/missing_dir",
        "pip install pytest",
    ],
)
def test_negative_controls(tmp_path: Path, command: str) -> None:
    assert _found(_pkg(tmp_path), command) == []


def test_own_marker_expression_that_deselects_everything_is_reported_as_its_own(tmp_path: Path) -> None:
    root = _pkg(tmp_path, pyproject='[tool.pytest.ini_options]\naddopts = "-ra"\n')
    assert _found(root, "pytest -m slow tests/test_mixed.py") == [
        "pre-commit::hook: `tests/test_mixed.py` reaches 2 test(s) and -m 'slow' (its own) deselects every one"
    ]
    assert _found(root, "pytest tests/test_mixed.py") == []


def test_keyword_selection(tmp_path: Path) -> None:
    root = _pkg(tmp_path)
    assert len(_found(root, "pytest -m integration -k nomatch tests/test_only_live.py")) == 1
    assert _found(root, "pytest -m integration -k live tests/test_only_live.py") == []


def test_monorepo_cd_and_package_relative_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    pkg = _pkg(repo / "apps" / "svc")
    cmd = "bash -c \"cd 'apps/svc' && python -m pytest tests/integration/test_db.py -q\""
    assert len(_found(pkg, cmd, repo_root=repo)) == 1
    assert _found(pkg, "bash -c \"cd 'apps/other' && python -m pytest tests/integration -q\"", repo_root=repo) == []


def test_discovery_from_precommit_and_workflow_level_working_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    pkg = _pkg(repo / "apps" / "svc")
    (repo / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n      - id: db\n        name: db\n        language: system\n"
        "        entry: bash -c \"cd 'apps/svc' && python -m pytest tests/integration\"\n",
        encoding="utf-8",
    )
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "svc.yml").write_text(
        "defaults:\n  run:\n    working-directory: apps/svc\njobs:\n  t:\n    runs-on: x\n    steps:\n      - name: live\n        run: pytest tests/test_only_live.py\n",
        encoding="utf-8",
    )
    found = find_path_runs_selecting_nothing(pkg, repo_root=repo)
    assert sorted((f.path, f.message.split(":")[0]) for f in found) == [(".github/workflows/svc.yml", "svc.yml"), (".pre-commit-config.yaml", "pre-commit")]


def test_read_addopts_variants(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = ["-ra", "-m", "not slow"]\n', encoding="utf-8")
    assert read_addopts(tmp_path) == "-ra -m not slow"
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -ra\n  -m 'not live'\n", encoding="utf-8")
    assert read_addopts(tmp_path) == "-ra -m 'not live'"


def test_bom_test_file_is_parsed(tmp_path: Path) -> None:
    assert len(_found(_pkg(tmp_path, bom=True), "pytest tests/integration/test_db.py")) == 1


def test_unparsable_test_file_is_reported_and_fails(tmp_path: Path) -> None:
    root = _pkg(tmp_path)
    (root / "tests" / "test_bad.py").write_text("def (:\n", encoding="utf-8")
    rules = [f.rule for f in find_path_runs_selecting_nothing(root, commands={"h": ("pytest tests/integration", "")})]
    assert "unparsed-file" in rules
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_path_runs_select_tests(root, commands={"h": ("pytest -m integration tests/integration", "")})


def test_no_test_files_fails_the_floor(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_path_runs_select_tests(tmp_path, commands={"h": ("pytest tests", "")})


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    root = _pkg(tmp_path / "pkg")
    good = {"pre-commit::hook": ("pytest -m integration tests/integration", "")}
    bad = {"pre-commit::hook": ("pytest tests/integration", "")}
    assert_path_runs_select_tests(root, commands=good)
    with pytest.raises(pytest.fail.Exception, match="inherited from addopts"):
        assert_path_runs_select_tests(root, commands=bad)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_path_runs_select_tests(root, commands=bad, baseline_path=baseline, refresh=False)
    with pytest.raises(pytest.skip.Exception):
        assert_path_runs_select_tests(root, commands=bad, baseline_path=baseline, refresh=True)
    assert_path_runs_select_tests(root, commands=bad, baseline_path=baseline, refresh=False)
    worse = {"pre-commit::hook": ("pytest tests/integration tests/test_only_live.py", "")}
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_path_runs_select_tests(root, commands=worse, baseline_path=baseline, refresh=False)
