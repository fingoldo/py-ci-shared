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


class TestAModuleThatOpensItsOwnConnection:
    """A structural fact that decides what evidence is even POSSIBLE.

    A module handed a `conn` can be given a mock, and a test that then asserts nothing about it is
    the case this whole check exists for. A module that calls `sqlite3.connect(...)` itself offers no
    such seam: a test either patches the driver -- visibly -- or runs the real thing.

    Measured on autopsia: `loinc_ru` installs into a temp SQLite and asserts through `display_ru()`,
    its own production read path, which never touches a mock and never opens a connection of its own.
    Demanding a mock assertion there is demanding something the design does not permit.
    """

    _SELF_CONNECTING = "import sqlite3\n\n\ndef install(db_path):\n    db = sqlite3.connect(db_path)\n    db.execute('CREATE TABLE t (a)')\n    db.commit()\n\n\ndef read(db_path):\n    return sqlite3.connect(db_path).execute('SELECT a FROM t').fetchone()\n"

    def test_a_test_that_reads_back_through_the_modules_own_reader_counts(self, tmp_path):
        _write(tmp_path, "store.py", self._SELF_CONNECTING)
        _write(
            tmp_path,
            "tests/test_store.py",
            "import store\n\n\ndef test_it(tmp_path):\n    store.install(tmp_path / 'v.sqlite')\n    assert store.read(tmp_path / 'v.sqlite') is None\n",
        )

        assert find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]}) == {}

    def test_a_test_that_patches_the_driver_is_mocking_after_all(self, tmp_path):
        """The escape hatch has to close, or a self-connecting module would be excused by the very
        tests that mock it away."""
        _write(tmp_path, "store.py", self._SELF_CONNECTING)
        _write(
            tmp_path,
            "tests/test_store.py",
            "from unittest.mock import patch\n\nimport store\n\n\ndef test_it():\n"
            "    with patch('sqlite3.connect'):\n        store.install('x')\n",
        )

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == [
            "store.py::commit",
            "store.py::execute",
        ]

    def test_patch_object_on_the_driver_closes_it_too(self, tmp_path):
        _write(tmp_path, "store.py", self._SELF_CONNECTING)
        _write(
            tmp_path,
            "tests/test_store.py",
            "import sqlite3\nfrom unittest.mock import patch\n\nimport store\n\n\ndef test_it():\n"
            "    with patch.object(sqlite3, 'connect'):\n        store.install('x')\n",
        )

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == [
            "store.py::commit",
            "store.py::execute",
        ]

    def test_a_module_handed_a_connection_is_still_held_to_the_mock_assertion(self, tmp_path):
        """The relaxation is scoped to modules with no seam. One that takes `conn` HAS a seam, and a
        test that ignores it is exactly what this check was built to report."""
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(tmp_path, "tests/test_store.py", "import store\n\n\ndef test_it(conn):\n    store.save(conn)\n")

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == ["store.py::commit"]

    def test_a_module_with_no_importing_test_at_all_is_still_reported(self, tmp_path):
        """Owning the connection is not a licence; something still has to run it."""
        _write(tmp_path, "store.py", self._SELF_CONNECTING)

        assert list(find_unasserted_effects(tmp_path, {"store.py": []})) == [
            "store.py::commit",
            "store.py::execute",
        ]


class TestATestThatRunsAgainstARealDatabase:
    """The case this module's own docstring told the operator to BASELINE by hand.

    Measured on autopsia: 22 vocabulary installers connect to SQLite themselves and each has a test
    that installs into a temp file and SELECTs the rows back -- 82 of its 93 reported effects, every
    one a false report, and every one strictly better evidence than the mock assertion the check was
    asking for.
    """

    def test_opening_a_database_counts_as_exercising_every_effect(self, tmp_path):
        _write(tmp_path, "store.py", "import sqlite3\n\n\ndef install(path):\n    db = sqlite3.connect(path)\n    db.execute('CREATE TABLE t (a)')\n    db.executemany('INSERT INTO t VALUES (?)', [(1,)])\n    db.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "import sqlite3\n\nimport store\n\n\ndef test_it(tmp_path):\n"
            "    store.install(tmp_path / 'v.sqlite')\n"
            "    db = sqlite3.connect(tmp_path / 'v.sqlite')\n"
            "    assert db.execute('SELECT count(*) FROM t').fetchone()[0] == 1\n",
        )

        assert find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]}) == {}

    def test_a_postgres_driver_counts_the_same_way(self, tmp_path):
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "import psycopg2\n\nimport store\n\n\ndef test_it(dsn):\n    conn = psycopg2.connect(dsn)\n    store.save(conn)\n",
        )

        assert find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]}) == {}

    def test_patching_a_drivers_connect_is_not_running_against_one(self, tmp_path):
        """The distinction the whole relaxation rests on. `patch("sqlite3.connect")` is a call to
        `patch`, not to `connect` -- a test that mocks the driver is exactly the case this check
        exists for, and must stay reported."""
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "from unittest.mock import patch\n\nimport store\n\n\ndef test_it(conn):\n"
            "    with patch('sqlite3.connect'):\n        store.save(conn)\n",
        )

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == ["store.py::commit"]

    def test_an_unrelated_connect_does_not_count(self, tmp_path):
        """`self.pool.connect()` or a socket's `connect` is not a database driver, and crediting it
        would excuse a module because a test connected to something."""
        _write(tmp_path, "store.py", "def save(conn):\n    conn.commit()\n")
        _write(
            tmp_path,
            "tests/test_store.py",
            "import socket\n\nimport store\n\n\ndef test_it(conn):\n    socket.socket().connect(('localhost', 1))\n    store.save(conn)\n",
        )

        assert list(find_unasserted_effects(tmp_path, {"store.py": ["tests/test_store.py"]})) == ["store.py::commit"]
