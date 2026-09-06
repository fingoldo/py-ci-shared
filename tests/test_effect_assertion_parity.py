"""Tests for the effect/assertion pairing check.

Each one is written against the shape that motivated the check: a module that writes to a database
through a mock, and a test that runs it and looks only at what came back.
"""

from __future__ import annotations

import pytest

from py_ci_shared.effect_assertion_parity import (
    assert_effects_are_asserted,
    build_import_map,
    find_unasserted_effects,
)


def _write(tmp_path, name, body):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestItFindsTheShapeThatMotivatedIt:
    def test_a_commit_no_importing_test_inspects_is_reported(self, tmp_path):
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n    return 1\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "import store\n\n\ndef test_it_returns_one(mocker):\n    assert store.save(object()) == 1\n",
        )

        problems = find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})

        assert list(problems) == ["store.py::commit"]
        assert "deleting it would not fail a single one" in problems["store.py::commit"]

    def test_a_commit_an_importing_test_asserts_on_is_not_reported(self, tmp_path):
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "import store\nfrom unittest.mock import MagicMock\n\n\n"
            "def test_it_commits():\n    conn = MagicMock()\n    store.save(conn)\n    conn.commit.assert_called_once()\n",
        )

        assert find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]}) == {}

    @pytest.mark.parametrize("inspection", ["called", "call_count", "call_args", "assert_called", "assert_not_called"])
    def test_every_way_of_looking_at_a_mock_counts(self, tmp_path, inspection):
        _write(tmp_path, "store.py", "def save(cur):\n    cur.execute('INSERT')\n")
        suffix = "()" if inspection.startswith("assert_") else ""
        _write(
            tmp_path,
            "tests/test_store.py",
            f"import store\n\n\ndef test_it(mock_cur):\n    store.save(mock_cur)\n    mock_cur.execute.{inspection}{suffix}\n",
        )

        assert find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]}) == {}


class TestItDoesNotGuessFromText:
    def test_the_word_commit_in_a_comment_does_not_count(self, tmp_path):
        """A text search would pass this. The check walks attribute chains, so it does not."""
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "import store\n\n\ndef test_it(conn):\n    # commit.assert_called_once() would go here\n    store.save(conn)\n",
        )

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == ["store.py::commit"]

    def test_a_local_variable_named_commit_does_not_count(self, tmp_path):
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "import store\n\n\ndef test_it(conn):\n    commit = True\n    assert commit\n    store.save(conn)\n",
        )

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == ["store.py::commit"]


class TestThePopulationIsTheImportingTests:
    def test_an_unrelated_test_asserting_on_commit_does_not_excuse_the_module(self, tmp_path):
        """Suite-wide "somebody asserts on commit somewhere" would be satisfied by one unrelated test
        and would gate nothing, which is why the importing tests are the population."""
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(tmp_path, "other.py", "def other(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_other.py",
            "import other\n\n\ndef test_it(conn):\n    other.other(conn)\n    conn.commit.assert_called_once()\n",
        )
        _write(tmp_path, "tests/test_store.py", "import store\n\n\ndef test_it(conn):\n    store.save(conn)\n")

        problems = find_unasserted_effects(
            tmp_path,
            {"store.py": ["tests/test_store.py"], "other.py": ["tests/test_other.py"]},
        )

        assert list(problems) == ["store.py::commit"]


class TestTheRatchet:
    def test_an_accepted_entry_does_not_fail(self, tmp_path):
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(tmp_path, "tests/test_store.py", "import store\n\n\ndef test_it(conn):\n    store.save(conn)\n")

        assert_effects_are_asserted(tmp_path, {"store.py": ["tests/test_store.py"]}, accepted=["store.py::commit"])

    def test_a_new_entry_fails_and_names_it(self, tmp_path):
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n    conn.rollback()\n")
        _write(tmp_path, "tests/test_store.py", "import store\n\n\ndef test_it(conn):\n    store.save(conn)\n")

        # `pytest.fail` raises `Failed`, which derives from BaseException rather than Exception,
        # so catching `Exception` here would let the failure through and the test would pass
        # for the wrong reason -- while reporting that the ratchet does not work.
        with pytest.raises(pytest.fail.Exception, match=r"store\.py::rollback"):
            assert_effects_are_asserted(tmp_path, {"store.py": ["tests/test_store.py"]}, accepted=["store.py::commit"])


class TestTheImportMapItBuilds:
    def test_a_direct_import_is_an_edge(self, tmp_path):
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(tmp_path, "tests/test_store.py", "import store\n\n\ndef test_it():\n    assert store\n")

        assert build_import_map(tmp_path) == {"store.py": ["tests/test_store.py"]}

    def test_one_level_of_transitivity_is_followed(self, tmp_path):
        """A test that imports `pipeline` reaches `pipeline/replay.py` without naming it, and the
        effects live in the submodule."""
        _write(tmp_path, "pipeline/__init__.py", "from pipeline import replay\n")
        _write(tmp_path, "pipeline/replay.py", "def go(conn):\n    conn.commit()\n")
        _write(tmp_path, "tests/test_pipeline.py", "import pipeline\n\n\ndef test_it():\n    assert pipeline\n")

        assert build_import_map(tmp_path)["pipeline/replay.py"] == ["tests/test_pipeline.py"]

    def test_a_src_layout_module_is_reached_by_its_installed_name(self, tmp_path):
        """`src/pkg/x.py` is imported as `pkg.x`; without `src_dir` the map would be empty and the
        check would silently report nothing at all."""
        _write(tmp_path, "src/pkg/__init__.py", "")
        _write(tmp_path, "src/pkg/store.py", "def save(conn):\n    conn.commit()\n")
        _write(tmp_path, "tests/test_store.py", "from pkg import store\n\n\ndef test_it():\n    assert store\n")

        assert build_import_map(tmp_path, src_dir="src")["src/pkg/store.py"] == ["tests/test_store.py"]

    def test_a_repository_imported_as_a_package_is_reached(self, tmp_path):
        _write(tmp_path, "data.py", "def save(conn):\n    conn.commit()\n")
        _write(tmp_path, "tests/test_data.py", "from dashboard import data\n\n\ndef test_it():\n    assert data\n")

        assert build_import_map(tmp_path, package_name="dashboard")["data.py"] == ["tests/test_data.py"]

    def test_tests_are_not_reported_as_modules(self, tmp_path):
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(tmp_path, "tests/helpers.py", "import store\n")
        _write(tmp_path, "tests/test_store.py", "import store\n\n\ndef test_it():\n    assert store\n")

        assert all(not key.startswith("tests/") for key in build_import_map(tmp_path))


class TestTheOtherMockingIdiom:
    """`patch.object(...) as name` binds the mock to a BARE NAME, not to an attribute chain.

    It is the commoner idiom for a module-level function, and matching only `x.effect.assert_*`
    missed it: the check reported `execute_values` as uninspected while a three-line test was
    inspecting it. Found by running the check against a fix written for its own finding.
    """

    def test_a_patched_module_function_asserted_by_bare_name_counts(self, tmp_path):
        _write(tmp_path, "store.py", "def save(cur, rows):\n    execute_values(cur, 'INSERT', rows)\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "from unittest.mock import patch\n\nimport store\n\n\n"
            "def test_it(cur):\n"
            "    with patch.object(store, 'execute_values') as execute_values:\n"
            "        store.save(cur, [])\n"
            "    execute_values.assert_called_once()\n",
        )

        assert find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]}) == {}

    def test_a_mock_bound_under_a_name_of_its_own_counts(self, tmp_path):
        """`as mock_ev` is what real tests write, and the bare-name rule above cannot see it: the
        assertion reads `mock_ev.assert_called_once()`, which names no effect.

        Measured on production_scrapers: five modules were reported while five real tests were
        asserting the cursor, the SQL and the rows under a name of their own. The check has to
        follow the binding or it spends the operator's time on its own blind spot.
        """
        _write(tmp_path, "store.py", "def save(cur, rows):\n    execute_values(cur, 'INSERT', rows)\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "from unittest.mock import patch\n\nimport store\n\n\n"
            "def test_it(cur):\n"
            "    with patch('store.execute_values') as mock_ev:\n"
            "        store.save(cur, [])\n"
            "    assert mock_ev.call_args.args[2] == []\n",
        )

        assert find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]}) == {}

    def test_the_decorator_form_counts_too(self, tmp_path):
        """`@patch(...)` injects the mock as a PARAMETER, which is the same binding problem with a
        different syntax."""
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "from unittest.mock import patch\n\nimport store\n\n\n"
            "@patch('store.commit')\n"
            "def test_it(fake):\n"
            "    fake.assert_called_once()\n",
        )

        assert find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]}) == {}

    def test_a_patch_of_something_else_bound_under_a_name_does_not_count(self, tmp_path):
        """The alias is credited with the effect it PATCHES, not with being a mock at all. A patched
        unrelated collaborator asserted by bare name leaves the module reported -- otherwise the
        relaxation would excuse every test that mocks anything."""
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "from unittest.mock import patch\n\nimport store\n\n\n"
            "def test_it(conn):\n"
            "    with patch('store.notify') as mock_notify:\n"
            "        store.save(conn)\n"
            "    mock_notify.assert_called_once()\n",
        )

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == ["store.py::commit"]

    def test_a_bare_name_that_is_not_an_effect_still_does_not_count(self, tmp_path):
        """The relaxation is scoped to the effect names themselves, so an unrelated mock asserted
        by bare name does not excuse the module."""
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "import store\n\n\ndef test_it(conn, notifier):\n    store.save(conn)\n    notifier.assert_called_once()\n",
        )

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == ["store.py::commit"]
