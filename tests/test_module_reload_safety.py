"""Unit tests for the shared module-reload check, on real scratch test trees."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import CorpusError
from py_ci_shared.module_reload_safety import assert_no_reloads_in_code, assert_no_unpaired_reloads, find_reloads_in_code, find_unpaired_reloads


def _file(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return root


class TestTestSideReloads:
    def test_a_bare_reload_is_flagged_with_its_line(self, tmp_path):
        tests = _file(
            tmp_path,
            "test_a.py",
            """
            import importlib, mod
            def test_x():
                importlib.reload(mod)
            """,
        )

        sites = find_unpaired_reloads(tests)

        assert [(s.path, s.line, s.primitive) for s in sites] == [("test_a.py", 4, "importlib.reload")]

    @pytest.mark.parametrize(
        "body",
        [
            # restore in the same function
            """
            import sys
            def test_x():
                saved = sys.modules["m"]
                try:
                    del sys.modules["m"]
                finally:
                    sys.modules["m"] = saved
            """,
            # a finally that reloads again after the patch is undone
            """
            import importlib, mod
            def test_x(monkeypatch):
                try:
                    importlib.reload(mod)
                finally:
                    monkeypatch.undo()
                    importlib.reload(mod)
            """,
            # a requested fixture that restores
            """
            import sys, pytest
            @pytest.fixture
            def fresh():
                saved = dict(sys.modules)
                yield
                sys.modules.update(saved)
            def test_x(fresh):
                sys.modules.pop("m", None)
            """,
            # an autouse fixture that restores
            """
            import sys, pytest
            @pytest.fixture(autouse=True)
            def _restore(request):
                saved = dict(sys.modules)
                request.addfinalizer(lambda: sys.modules.update(saved))
            def test_x():
                sys.modules.pop("m", None)
            """,
        ],
        ids=["same-scope-restore", "finally-reload", "requested-fixture", "autouse-fixture"],
    )
    def test_each_restore_mechanism_is_recognised(self, tmp_path, body):
        assert find_unpaired_reloads(_file(tmp_path, "test_a.py", body)) == []

    def test_a_restore_in_an_UNRELATED_function_does_not_count(self, tmp_path):
        """What whole-file matching got wrong: the restore sits in a different test."""
        tests = _file(
            tmp_path,
            "test_a.py",
            """
            import sys, importlib, mod
            def test_restores():
                sys.modules.update({})
            def test_reloads():
                importlib.reload(mod)
            """,
        )

        assert [s.line for s in find_unpaired_reloads(tests)] == [6]

    def test_a_singleton_owner_is_marked(self, tmp_path):
        tests = _file(
            tmp_path,
            "test_a.py",
            """
            import importlib
            import pkg.cache
            def test_x():
                importlib.reload(pkg.cache)
            """,
        )

        (site,) = find_unpaired_reloads(tests, singleton_modules=("pkg.cache",))

        assert site.singleton and "subprocess" in str(site)

    def test_the_assertion_fails_on_a_stale_exemption(self, tmp_path):
        tests = _file(tmp_path, "test_a.py", "def test_x():\n    pass\n")

        with pytest.raises(pytest.fail.Exception, match="no longer exist"):
            assert_no_unpaired_reloads(tests, stub_only_files=("gone.py",))


def test_production_code_may_not_reload_at_all(tmp_path):
    root = _file(
        tmp_path,
        "pkg/mod.py",
        """
        import importlib, sys
        def f(m):
            importlib.reload(m)
            del sys.modules["x"]
        """,
    )

    sites = find_reloads_in_code([root / "pkg"], root)

    assert {(s.path, s.primitive) for s in sites} == {("pkg/mod.py", "importlib.reload"), ("pkg/mod.py", "del sys.modules")}
    assert find_reloads_in_code([root / "pkg"], root, allowed={("pkg/mod.py", 4), ("pkg/mod.py", 5)}) == []


class TestAuditRegressions:
    def _lines(self, tmp_path: Path, body: str, rel: str = "test_a.py") -> "list[int]":
        return [s.line for s in find_unpaired_reloads(_file(tmp_path / Path(rel).stem, rel, body))]

    @pytest.mark.parametrize(
        "body",
        [
            "from importlib import reload\nimport sys\ndef test_x():\n    reload(sys)\n",
            "import importlib as il, mod\ndef test_x():\n    il.reload(mod)\n",
            "import sys as _sys\ndef test_x():\n    del _sys.modules['m']\n",
            "from sys import modules\ndef test_x():\n    modules.pop('m')\n",
        ],
        ids=["from-import-reload", "importlib-as-il", "sys-as-_sys", "from-sys-import-modules"],
    )
    def test_aliased_primitives_are_found(self, tmp_path, body):
        assert len(find_unpaired_reloads(_file(tmp_path, "test_a.py", body))) == 1

    def test_installing_a_fake_is_not_a_restore(self, tmp_path):
        body = "import sys, importlib\ndef test_x():\n    sys.modules['foo'] = object()\n    importlib.reload(sys)\n"
        assert self._lines(tmp_path, body) == [4]
        restored = "import sys, importlib\ndef test_x():\n    saved = sys.modules['foo']\n    importlib.reload(sys)\n    sys.modules['foo'] = saved\n"
        assert self._lines(tmp_path, restored, "test_b.py") == []

    def test_an_enclosing_function_s_finally_pairs_a_nested_reload(self, tmp_path):
        body = (
            "import importlib, mod\n"
            "def test_x(monkeypatch):\n"
            "    def inner():\n"
            "        importlib.reload(mod)\n"
            "    try:\n"
            "        inner()\n"
            "    finally:\n"
            "        monkeypatch.undo()\n"
            "        importlib.reload(mod)\n"
        )
        assert self._lines(tmp_path, body) == []
        unpaired = "import importlib, mod\ndef test_x():\n    def inner():\n        importlib.reload(mod)\n    inner()\n"
        assert self._lines(tmp_path, unpaired, "test_b.py") == [4]

    def test_conftest_and_usefixtures_fixtures_are_seen(self, tmp_path):
        _file(
            tmp_path,
            "conftest.py",
            "import sys, pytest\n@pytest.fixture\ndef fresh_modules():\n    saved = dict(sys.modules)\n    yield\n    sys.modules.clear()\n    sys.modules.update(saved)\n",
        )
        _file(tmp_path, "sub/test_arg.py", "import sys\ndef test_x(fresh_modules):\n    sys.modules.pop('m', None)\n")
        _file(
            tmp_path,
            "sub/test_mark.py",
            "import sys, pytest\n@pytest.mark.usefixtures('fresh_modules')\ndef test_x():\n    sys.modules.pop('m', None)\ndef test_y():\n    sys.modules.pop('m', None)\n",
        )
        sites = find_unpaired_reloads(tmp_path)
        assert [(s.path, s.line) for s in sites] == [("sub/test_mark.py", 6)]

    def test_an_autouse_restore_in_one_class_does_not_clear_the_file(self, tmp_path):
        body = (
            "import sys, pytest\n"
            "class TestA:\n"
            "    @pytest.fixture(autouse=True)\n"
            "    def _restore(self):\n"
            "        saved = dict(sys.modules)\n"
            "        yield\n"
            "        sys.modules.update(saved)\n"
            "    def test_x(self):\n"
            "        sys.modules.pop('m', None)\n"
            "def test_outside():\n"
            "    sys.modules.pop('m', None)\n"
        )
        assert self._lines(tmp_path, body) == [11]

    @pytest.mark.parametrize(
        "body",
        [
            "import sys, mod\ndef test_x(request):\n    request.addfinalizer(close_db)\n    del sys.modules['m']\n",
            "import sys, mod\ndef test_x():\n    mod.__dict__.update({'a': 1})\n    del sys.modules['m']\n",
            "import sys, subprocess, importlib, mod\ndef test_x():\n    subprocess.run(['python', '-c', 'pass'])\n    importlib.reload(mod)\n",
        ],
        ids=["unrelated-finalizer", "dict-update-without-snapshot", "subprocess-beside-in-process-reload"],
    )
    def test_look_alike_restores_do_not_count(self, tmp_path, body):
        assert len(find_unpaired_reloads(_file(tmp_path, "test_a.py", body))) == 1

    def test_real_finalizer_and_dict_restores_count(self, tmp_path):
        body = (
            "import sys, importlib, mod\n"
            "def test_a(request):\n"
            "    request.addfinalizer(lambda: importlib.reload(mod))\n"
            "    importlib.reload(mod)\n"
            "def test_b():\n"
            "    saved = dict(mod.__dict__)\n"
            "    del sys.modules['m']\n"
            "    mod.__dict__.update(saved)\n"
        )
        assert self._lines(tmp_path, body) == []

    def test_production_allowlist_by_statement_text_with_stale_check(self, tmp_path):
        root = _file(tmp_path, "pkg/mod.py", "import importlib\n\ndef f(m):\n    importlib.reload(m)\n")
        allowed = {("pkg/mod.py", "importlib.reload(m)")}
        assert find_reloads_in_code([root / "pkg"], root, allowed=allowed) == []
        (root / "pkg" / "mod.py").write_text("import importlib\n# moved\n\n\ndef f(m):\n    importlib.reload(m)\n", encoding="utf-8")
        assert find_reloads_in_code([root / "pkg"], root, allowed=allowed) == []
        assert_no_reloads_in_code([root / "pkg"], root, allowed=allowed)
        with pytest.raises(pytest.fail.Exception, match="match no site"):
            assert_no_reloads_in_code([root / "pkg"], root, allowed=allowed | {("pkg/mod.py", "importlib.reload(gone)")})
        with pytest.raises(pytest.fail.Exception, match="production code"):
            assert_no_reloads_in_code([root / "pkg"], root)

    def test_a_missing_root_raises(self, tmp_path):
        with pytest.raises(CorpusError):
            find_reloads_in_code([tmp_path / "nope"], tmp_path)
        with pytest.raises(CorpusError):
            assert_no_unpaired_reloads(tmp_path / "nope")

    def test_an_empty_tests_dir_fails_the_floor(self, tmp_path):
        with pytest.raises(AssertionError, match="parsed"):
            assert_no_unpaired_reloads(tmp_path)
        _file(tmp_path, "test_a.py", "def test_x():\n    pass\n")
        assert_no_unpaired_reloads(tmp_path)

    def test_bom_and_unparsable_files(self, tmp_path):
        (tmp_path / "test_bom.py").write_bytes(b"\xef\xbb\xbfimport importlib, mod\ndef test_x():\n    importlib.reload(mod)\n")
        _file(tmp_path, "test_bad.py", "import importlib\ndef (:\n")
        sites = find_unpaired_reloads(tmp_path)
        assert sorted((s.path, s.primitive.split(":")[0]) for s in sites) == [("test_bad.py", "unparsable"), ("test_bom.py", "importlib.reload")]
