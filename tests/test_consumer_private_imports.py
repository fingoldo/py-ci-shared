"""43-F6: a consumer that imports an underscore module or name of the library is reported."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.consumer_import_census import PRIVATE_RULE, assert_no_consumer_private_imports, find_consumer_private_imports, main

CONSUMER = """\
from seedlib.core import public_name
from seedlib.core import _hidden_name
from seedlib._inner import thing
from seedlib import _inner as inner
import seedlib._inner
import seedlib.core
from seedlib.core import __version__
from other import _not_ours
try:
    from seedlib.core import _guarded
except ImportError:
    _guarded = None
"""


def _library(root: Path) -> Path:
    pkg = root / "lib" / "seedlib"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""lib"""\n', encoding="utf-8")
    (pkg / "core.py").write_text("public_name = 1\n_hidden_name = 2\n__version__ = '1'\n_guarded = 0\n", encoding="utf-8")
    (pkg / "_inner.py").write_text("thing = 1\n", encoding="utf-8")
    return root / "lib"


def _consumer(root: Path, text: str = CONSUMER) -> Path:
    app = root / "app"
    app.mkdir()
    (app / "mod.py").write_text(text, encoding="utf-8")
    clear_parse_cache()
    return app


def test_private_modules_and_names_are_reported_public_dunder_guarded_and_foreign_are_not(tmp_path):
    found = find_consumer_private_imports(_library(tmp_path), "seedlib", [("app", _consumer(tmp_path))], use_git=False)
    assert {(f.line, f.rule) for f in found} == {(2, PRIVATE_RULE), (3, PRIVATE_RULE), (4, PRIVATE_RULE), (5, PRIVATE_RULE)}
    assert "`_hidden_name` is private to seedlib" in found[0].message
    assert found[0].path == "app/mod.py"


def test_clean_consumer_has_no_findings(tmp_path):
    app = _consumer(tmp_path, "from seedlib.core import public_name\nimport seedlib.core\n")
    assert find_consumer_private_imports(_library(tmp_path), "seedlib", [("app", app)], use_git=False) == []
    assert_no_consumer_private_imports(tmp_path / "lib", "seedlib", [("app", app)], use_git=False)


def test_assert_fails_with_the_lines(tmp_path):
    lib = _library(tmp_path)
    with pytest.raises(AssertionError, match="reach private names of seedlib"):
        assert_no_consumer_private_imports(lib, "seedlib", [("app", _consumer(tmp_path))], use_git=False)


@pytest.mark.parametrize(("mode", "code"), [("report", 0), ("fail", 1), (None, 0)])
def test_cli_modes(tmp_path, capsys, mode, code):
    lib = _library(tmp_path)
    app = _consumer(tmp_path)
    flags = ["--private-imports", mode] if mode else []
    assert main(["--library", str(lib), "--package", "seedlib", "--consumer", str(app), *flags]) == code
    out = capsys.readouterr().out
    assert ("consumer-private-import" in out) is bool(mode)


def test_only_private_skips_the_resolution_check(tmp_path, capsys):
    lib = _library(tmp_path)
    app = _consumer(tmp_path, "from seedlib.core import missing_name\nfrom seedlib.core import _hidden_name\n")
    assert main(["--library", str(lib), "--package", "seedlib", "--consumer", str(app)]) == 1
    capsys.readouterr()
    assert main(["--library", str(lib), "--package", "seedlib", "--consumer", str(app), "--only-private", "--private-imports", "report"]) == 0
    out = capsys.readouterr().out
    assert "_hidden_name" in out and "missing_name" not in out
    with pytest.raises(SystemExit):
        main(["--library", str(lib), "--package", "seedlib", "--consumer", str(app), "--only-private"])


def test_the_consumers_of_the_registry_are_scanned_by_the_scheduled_job():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "consumer-pins.yml").read_text(encoding="utf-8")
    assert "consumer_import_census" in workflow and "--package py_ci_shared --only-private --private-imports report" in workflow
