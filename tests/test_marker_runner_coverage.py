"""Unit tests for marker_runner_coverage: a marked test no runner selects never runs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.marker_runner_coverage import (
    assert_every_marked_test_is_selected,
    expression_selects,
    find_unselected_marked_tests,
    marked_tests,
    runners,
)

_NIGHTLY = ("nightly.yml", 'pytest -m "slow and integration" -p no:randomly')
#: Names the file AND asks for the marker. Both halves are needed, which is the point of the next one.
_HOOK = ("hook::live-db", 'bash -c "cd proj && python -m pytest -m integration tests/test_named.py -p no:randomly -q"')
#: The same hook without its own `-m`: pytest applies `addopts` to EVERY run, so the project's
#: `-m 'not integration'` deselects the very test the hook was written to run. Naming the path is not
#: enough, and this is the shape realtime_applications' pre-push hook had.
_HOOK_INHERITING_ADDOPTS = ("hook::live-db", 'bash -c "cd proj && python -m pytest tests/test_named.py -p no:randomly -q"')
_DEFAULT_ADDOPTS = "--strict-markers -m 'not integration'"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    (tests / "integration").mkdir(parents=True)
    (tests / "integration" / "test_db.py").write_text("import pytest\n\npytestmark = pytest.mark.integration\n\n\ndef test_q():\n    pass\n", encoding="utf-8")
    (tests / "integration" / "test_llm.py").write_text(
        "import pytest\n\npytestmark = [pytest.mark.integration, pytest.mark.slow]\n\n\ndef test_m():\n    pass\n", encoding="utf-8"
    )
    (tests / "test_named.py").write_text("import pytest\n\n\n@pytest.mark.integration\ndef test_wire():\n    pass\n", encoding="utf-8")
    (tests / "test_plain.py").write_text("def test_unit():\n    pass\n", encoding="utf-8")
    return tmp_path


def test_the_scan_finds_file_level_and_decorated_markers(project):
    found = {t.key: t.markers for t in marked_tests(project / "tests", project, marker="integration")}
    assert found == {
        "tests/integration/test_db.py::<whole file>": frozenset({"integration"}),
        "tests/integration/test_llm.py::<whole file>": frozenset({"integration", "slow"}),
        "tests/test_named.py::test_wire": frozenset({"integration"}),
    }


def test_a_marker_the_nightly_expression_does_not_match_is_reported(project):
    """The realtime_applications defect: `integration` without `slow`, so `slow and integration` drops it."""
    problems = find_unselected_marked_tests(project / "tests", project, marker="integration", commands=[_NIGHTLY, _HOOK], addopts=_DEFAULT_ADDOPTS)
    assert [p.split(":")[0] for p in problems] == ["tests/integration/test_db.py"]
    assert "slow and integration" in problems[0]


def test_the_slow_and_integration_test_and_the_named_one_are_selected(project):
    problems = find_unselected_marked_tests(project / "tests", project, marker="integration", commands=[_NIGHTLY, _HOOK], addopts=_DEFAULT_ADDOPTS)
    keys = {p.split(": ")[0] for p in problems}
    assert "tests/integration/test_llm.py::<whole file>" not in keys, "matched by the nightly expression"
    assert "tests/test_named.py::test_wire" not in keys, "named by the hook, which asks for the marker too"


def test_a_hook_that_names_the_file_but_inherits_addopts_does_not_select_it(project):
    """`addopts` applies to every run, so a hook with no `-m` of its own still deselects the marker."""
    problems = find_unselected_marked_tests(
        project / "tests", project, marker="integration", commands=[_NIGHTLY, _HOOK_INHERITING_ADDOPTS], addopts=_DEFAULT_ADDOPTS
    )
    assert "tests/test_named.py::test_wire" in {p.split(": ")[0] for p in problems}
    assert "not integration" in next(p for p in problems if p.startswith("tests/test_named.py"))


def test_a_path_argument_that_never_reaches_the_file_is_the_production_scrapers_shape(project):
    """Its integration job runs `pytest tests/integration/ -m integration`, and two marked tests live outside it."""
    commands = [("unit.yml", 'pytest tests/ -m "not integration"'), ("integration.yml", "pytest tests/integration/ -m integration")]
    problems = find_unselected_marked_tests(project / "tests", project, marker="integration", commands=commands, addopts="")
    assert [p.split(": ")[0] for p in problems] == ["tests/test_named.py::test_wire"]
    assert "deselects its markers" in problems[0]


def test_addopts_supplies_the_default_expression_and_a_command_overrides_it():
    (default_only,) = runners([("j", "pytest tests/")], addopts="-m 'not integration'")
    assert default_only.expression == "not integration"
    (explicit,) = runners([("j", 'pytest tests/ -m "integration"')], addopts="-m 'not integration'")
    assert explicit.expression == "integration"


def test_a_dependency_install_line_is_not_a_runner(project):
    """`gate_commands` hands over install steps too, and `pip install pytest pytest-cov` names pytest

    without running it. Read as a runner it looks PATHLESS, which means "selects everything" and would
    hide every real finding behind it.
    """
    install = (
        "ci.yml::test::Install test dependencies",
        "python -m pip install --upgrade pip\npip install psycopg2-binary orjson \\\n  pytest pytest-cov pytest-timeout hypothesis",
    )
    assert runners([install]) == []
    problems = find_unselected_marked_tests(project / "tests", project, marker="integration", commands=[install, _NIGHTLY, _HOOK], addopts=_DEFAULT_ADDOPTS)
    assert [p.split(": ")[0] for p in problems] == ["tests/integration/test_db.py::<whole file>"], "the install step must not mask the finding"


def test_a_real_pathless_runner_still_counts():
    """The install rule must not swallow the common shape it looks like: `pytest -m "..." --cov=pkg`."""
    (only,) = runners([("ci.yml", 'pytest -m "not integration" --cov=pkg')])
    assert only.is_pathless and only.expression == "not integration"


def test_expression_evaluation_covers_the_shapes_in_use():
    assert expression_selects("integration", ["integration"])
    assert not expression_selects("not integration", ["integration"])
    assert expression_selects("not integration", ["slow"])
    assert expression_selects("slow and integration", ["slow", "integration"])
    assert not expression_selects("slow and integration", ["integration"])
    assert expression_selects(None, []), "no -m selects everything"
    assert expression_selects("integration and not (gpu or flaky)", ["integration"])


def test_an_unparsable_expression_does_not_manufacture_a_finding():
    assert expression_selects("integration and", ["integration"])


def test_no_commands_is_refused_rather_than_reporting_everything(project):
    with pytest.raises(AssertionError, match="no runner commands"):
        assert_every_marked_test_is_selected(project / "tests", project, marker="integration", commands=[])


def test_a_marker_nobody_uses_fails_the_floor_instead_of_passing_vacuously(project):
    with pytest.raises(pytest.fail.Exception, match="this would check nothing"):
        assert_every_marked_test_is_selected(project / "tests", project, marker="gpu", commands=[_NIGHTLY], min_marked=1)


def test_assert_is_shrink_only(project):
    kwargs = {"marker": "integration", "commands": [_NIGHTLY, _HOOK], "addopts": _DEFAULT_ADDOPTS}
    with pytest.raises(pytest.fail.Exception, match="no runner selects"):
        assert_every_marked_test_is_selected(project / "tests", project, **kwargs)
    assert_every_marked_test_is_selected(project / "tests", project, known={"tests/integration/test_db.py"}, **kwargs)
    with pytest.raises(pytest.fail.Exception, match="now selected"):
        assert_every_marked_test_is_selected(project / "tests", project, known={"tests/test_named.py"}, **kwargs)
