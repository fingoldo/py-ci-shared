"""Unit tests for the shared module-reload check, on real scratch test trees."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.module_reload_safety import assert_no_unpaired_reloads, find_reloads_in_code, find_unpaired_reloads


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
                request.addfinalizer(lambda: None)
            def test_x():
                sys.modules.pop("m", None)
            """,
            # process isolation
            """
            import subprocess, importlib, mod
            def test_x():
                subprocess.run(["python", "-c", "pass"])
                importlib.reload(mod)
            """,
        ],
        ids=["same-scope-restore", "finally-reload", "requested-fixture", "autouse-fixture", "subprocess"],
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
