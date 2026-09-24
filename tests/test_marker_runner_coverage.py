"""Unit tests for marker_runner_coverage: a marked test no runner selects never runs."""

from __future__ import annotations

import re
from pathlib import Path

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


def test_an_unparsable_expression_is_rejected_not_read_as_selecting(project):
    """A typo pytest itself rejects must not count as "selects everything"."""
    with pytest.raises(ValueError, match="ends early"):
        expression_selects("integration and", ["integration"])
    problems = find_unselected_marked_tests(
        project / "tests", project, marker="integration", commands=[("typo.yml", 'pytest -m "slow andd integration"')], addopts=""
    )
    assert any(p.startswith("typo.yml::<bad expression>") for p in problems)
    assert {p.split(": ")[0] for p in problems} >= {"tests/test_named.py::test_wire", "tests/integration/test_llm.py::<whole file>"}
    ok = find_unselected_marked_tests(project / "tests", project, marker="integration", commands=[("ok.yml", 'pytest -m "integration"')], addopts="")
    assert ok == []


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
    assert_every_marked_test_is_selected(project / "tests", project, known={"tests/integration/test_db.py::<whole file>"}, **kwargs)
    with pytest.raises(pytest.fail.Exception, match="now selected"):
        assert_every_marked_test_is_selected(
            project / "tests", project, known={"tests/integration/test_db.py::<whole file>", "tests/test_named.py::test_wire"}, **kwargs
        )


class TestAuditRegressions:
    def test_class_markers_class_pytestmark_annassign_and_from_pytest_import_mark(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_shapes.py").write_text(
            "import pytest\n"
            "from pytest import mark\n"
            "import pytest as pt\n"
            "slow_db = pytest.mark.integration\n\n"
            "def test_a():\n    pass\n\n"
            "@pytest.mark.integration\nclass TestDecorated:\n    def test_b(self):\n        pass\n\n"
            "class TestBody:\n    pytestmark = [pytest.mark.integration]\n    def test_c(self):\n        pass\n\n"
            "@mark.integration\ndef test_d():\n    pass\n\n"
            "@pt.mark.integration(reason='x')\ndef test_e():\n    pass\n\n"
            "@slow_db\ndef test_f():\n    pass\n\n"
            "@pytest.mark.other\ndef test_g():\n    pass\n",
            encoding="utf-8",
        )
        (tests / "test_ann.py").write_text("import pytest\npytestmark: list = [pytest.mark.integration]\n\ndef test_h():\n    pass\n", encoding="utf-8")

        keys = {t.key for t in marked_tests(tests, tmp_path, marker="integration")}

        assert keys == {
            "tests/test_shapes.py::TestDecorated::test_b",
            "tests/test_shapes.py::TestBody::test_c",
            "tests/test_shapes.py::test_d",
            "tests/test_shapes.py::test_e",
            "tests/test_shapes.py::test_f",
            "tests/test_ann.py::<whole file>",
        }

    def test_a_bom_file_is_read_and_an_unparsable_one_is_reported(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_bom.py").write_bytes(b"\xef\xbb\xbfimport pytest\n\n@pytest.mark.integration\ndef test_x():\n    pass\n")
        (tests / "test_broken.py").write_text("import pytest\npytestmark = pytest.mark.integration\ndef (:\n", encoding="utf-8")

        problems = find_unselected_marked_tests(tests, tmp_path, marker="integration", commands=[("ci", "pytest tests -m 'not integration'")])

        assert {p.split(": ")[0] for p in problems} == {"tests/test_bom.py::test_x", "tests/test_broken.py::<unparsed>"}
        with pytest.raises(AssertionError, match=re.escape("test_broken.py")):
            marked_tests(tests, tmp_path, marker="integration")

    def test_a_bare_positional_directory_is_a_path_not_everything(self):
        (runner,) = runners([("ci", "pytest tests -m 'not integration'")])
        assert runner.paths == ("tests",) and not runner.is_pathless
        (chained,) = runners([("ci", "pytest unit && echo done > log.txt")])
        assert chained.paths == ("unit",)

    def test_a_directory_outside_the_marked_file_does_not_reach_it(self, project):
        problems = find_unselected_marked_tests(
            project / "tests", project, marker="integration", commands=[("ci", "pytest tests/integration -m integration")], addopts=""
        )
        assert [p.split(": ")[0] for p in problems] == ["tests/test_named.py::test_wire"]

    def test_dot_slash_and_cd_are_normalised(self, project):
        (dotted,) = runners([("ci", "pytest ./tests/ -m integration")])
        assert dotted.paths == ("tests",)
        (sub,) = runners([("ci", "cd tests && pytest integration -m integration")])
        assert sub.cwd == "tests" and sub.paths == ("integration",)
        problems = find_unselected_marked_tests(
            project / "tests", project, marker="integration", commands=[("ci", "cd tests && pytest integration -m integration")]
        )
        assert [p.split(": ")[0] for p in problems] == ["tests/test_named.py::test_wire"]
        assert find_unselected_marked_tests(project / "tests", project, marker="integration", commands=[("ci", "cd tests && pytest . -m integration")]) == []

    def test_the_last_dash_m_wins_and_every_spelling_parses(self):
        (runner,) = runners([("ci", "pytest tests -m integration -m 'not integration'")])
        assert runner.expression == "not integration"
        (equals,) = runners([("ci", "pytest tests -m=integration")])
        assert equals.expression == "integration"
        (glued,) = runners([("ci", "pytest tests -mintegration")])
        assert glued.expression == "integration"

    def test_a_node_id_runner_reaches_only_that_test(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text(
            "import pytest\n\n@pytest.mark.integration\ndef test_x():\n    pass\n\n@pytest.mark.integration\ndef test_y():\n    pass\n", encoding="utf-8"
        )
        problems = find_unselected_marked_tests(tests, tmp_path, marker="integration", commands=[("hook", "pytest tests/test_a.py::test_x")])
        assert [p.split(": ")[0] for p in problems] == ["tests/test_a.py::test_y"]

    def test_dash_k_is_applied(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text(
            "import pytest\npytestmark = pytest.mark.integration\n\ndef test_fast():\n    pass\n\ndef test_slowpath():\n    pass\n", encoding="utf-8"
        )
        (tests / "test_b.py").write_text("import pytest\n\n@pytest.mark.integration\ndef test_q():\n    pass\n", encoding="utf-8")
        problems = find_unselected_marked_tests(tests, tmp_path, marker="integration", commands=[("ci", "pytest tests -k 'not slowpath'")])
        assert [p.split(": ")[0] for p in problems] == ["tests/test_a.py::<whole file>"]
        assert find_unselected_marked_tests(tests, tmp_path, marker="integration", commands=[("ci", "pytest tests -k 'test_'")]) == []

    def test_the_ratchet_is_keyed_by_test_not_by_file(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text(
            "import pytest\n\n@pytest.mark.integration\ndef test_x():\n    pass\n\n@pytest.mark.integration\ndef test_new():\n    pass\n", encoding="utf-8"
        )
        cmd = [("ci", "pytest tests -m 'not integration'")]
        with pytest.raises(pytest.fail.Exception, match="test_new"):
            assert_every_marked_test_is_selected(tests, tmp_path, marker="integration", commands=cmd, known={"tests/test_a.py::test_x"})
        with pytest.raises(pytest.fail.Exception, match="file-level keys"):
            assert_every_marked_test_is_selected(tests, tmp_path, marker="integration", commands=cmd, known={"tests/test_a.py"})
        assert_every_marked_test_is_selected(
            tests, tmp_path, marker="integration", commands=cmd, known={"tests/test_a.py::test_x", "tests/test_a.py::test_new"}
        )

    def test_expression_parser_matches_pytest_grammar(self):
        assert expression_selects("(slow or gpu) and not flaky", ["gpu"])
        assert not expression_selects("(slow or gpu) and not flaky", ["gpu", "flaky"])
        assert expression_selects("not not integration", ["integration"])
        for bad in ("slow andd integration", "(slow", "slow)", "and slow", "__import__('os')"):
            with pytest.raises(ValueError):
                expression_selects(bad, ["slow", "integration"])


class TestShellAndActionsWords:
    """A shell variable or Actions expression is not a path, and a Python argument list is tokenised as one."""

    def test_sharding_values_and_template_tokens_are_not_paths(self):
        cmd = 'pytest -m "not gpu" \\\n  --splits "$SHARD_SPLITS" --group "$SHARD_GROUP" --junitxml=j-${{ matrix.group }}.xml $EXTRA tests/a.py::$t\n'
        cmd += "pytest tests/b.py --splits 4 --group ${{ matrix.group }} -n 1\n"
        got = runners([("ci", cmd)])
        assert [(r.paths, r.expression) for r in got] == [(("tests/a.py",), "not gpu"), (("tests/b.py",), None)]

    def test_a_python_heredoc_contributes_its_subprocess_argument_list(self):
        cmd = (
            "python - <<'PY'\nimport subprocess, sys\nprint('pytest wrote one.')\n"
            'sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-m", "gpu", "--no-cov", "-n", "0", "tests/gpu"]))\nPY\n'
        )
        got = runners([("gpu", cmd)])
        assert [(r.paths, r.expression) for r in got] == [(("tests/gpu",), "gpu")]

    def test_a_heredoc_body_that_is_not_python_is_not_read_as_commands(self):
        assert runners([("s", "cat > notes.md <<END\npytest -m slow ran here\nEND\necho done\n")]) == []

    def test_sharded_runner_still_selects_and_a_deselecting_one_still_reports(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_s.py").write_text("import pytest\n\n\n@pytest.mark.slow\ndef test_x():\n    pass\n", encoding="utf-8")
        sharded = [("deep", 'pytest -m "not gpu" --splits 20 --group "$SHARD_GROUP" -n auto')]
        assert find_unselected_marked_tests(tests, tmp_path, marker="slow", commands=sharded) == []
        deselecting = [("ci", 'pytest -m "not slow" --splits "$S" --group "$G" $ARGS')]
        assert len(find_unselected_marked_tests(tests, tmp_path, marker="slow", commands=deselecting)) == 1
