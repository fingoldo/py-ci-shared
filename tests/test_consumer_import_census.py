"""Tests for py_ci_shared.consumer_import_census."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared._core import CoreError, EmptyScanError, UnparsedFilesError, clear_parse_cache
from py_ci_shared.consumer_import_census import (
    RULE,
    LibrarySurface,
    assert_consumer_imports_resolve,
    find_consumer_import_breaks,
    library_alias_map,
    main,
    package_dir,
)

BOM = b"\xef\xbb\xbf"

INIT = '''"""lib"""
_MODULE_ALIASES = {
    "oldmod": "lib.core.newmod",
    "short": "lib.core",
}
_NOT_ALIASES = {"x": 1}
_SUBPACKAGES = ("core",)
'''


def _write(root: Path, rel: str, text: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    clear_parse_cache()


def _library(root: Path) -> Path:
    _write(root, "src/lib/__init__.py", INIT)
    _write(root, "src/lib/core/__init__.py", "from .newmod import public\n")
    _write(root, "src/lib/core/newmod.py", "public = 1\n_private = 2\n\n\ndef helper():\n    pass\n")
    _write(root, "src/lib/dyn.py", "def __getattr__(name):\n    return name\n")
    return root


def _found(lib: Path, app: Path, **kw: object) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.message) for f in find_consumer_import_breaks(lib, "lib", [("app", app)], use_git=False, **kw)]  # type: ignore[arg-type]


def test_reports_the_seeded_violation(tmp_path: Path) -> None:
    lib = _library(tmp_path / "lib")
    _write(tmp_path / "app", "main.py", "import os\nfrom lib.core.newmod import public, _moved\n")
    assert _found(lib, tmp_path / "app") == [("app/main.py", 2, "`from lib.core.newmod import _moved`: lib.core.newmod does not define '_moved'")]
    assert find_consumer_import_breaks(lib, "lib", [("app", tmp_path / "app")], use_git=False)[0].rule == RULE


def test_a_missing_module_is_reported_for_both_import_forms(tmp_path: Path) -> None:
    lib = _library(tmp_path / "lib")
    _write(tmp_path / "app", "main.py", "import lib.gone\nfrom lib.core.gone import x\n")
    assert [m for _, _, m in _found(lib, tmp_path / "app")] == [
        "`import lib.gone`: module lib.gone does not exist",
        "`from lib.core.gone import x`: module lib.core.gone does not exist",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "from lib.core.newmod import public, _private, helper\n",  # underscore names count: consumers import them
        "from lib.core import newmod, public\n",  # a submodule, and a re-export
        "import lib.core.newmod\nimport lib\n",
        "from lib.oldmod import public, _private\n",  # alias of lib.core.newmod
        "import lib.short.newmod\n",  # alias prefix
        "from lib.dyn import anything\n",  # module __getattr__: not judgeable
        "from lib import whatever, core\n",  # a name the package __init__ binds, and a subpackage
        "try:\n    from lib.core.newmod import _gone\nexcept ImportError:\n    _gone = None\n",  # the consumer expects absence
        "from libother.core import x\nfrom . import lib\n",  # not this library
    ],
)
def test_negative_controls(tmp_path: Path, text: str) -> None:
    lib = _library(tmp_path / "lib")
    _write(lib, "src/lib/__init__.py", INIT + "whatever = 1\n")
    _write(tmp_path / "app", "main.py", text)
    assert _found(lib, tmp_path / "app") == []


def test_the_alias_map_is_read_from_dict_literals_of_module_names_only(tmp_path: Path) -> None:
    lib = _library(tmp_path / "lib")
    assert library_alias_map(package_dir(lib, "lib"), "lib") == {"lib.oldmod": "lib.core.newmod", "lib.short": "lib.core"}
    surface = LibrarySurface(lib, "lib", aliases={"lib.extra": "lib.core"})
    assert surface.canonical("lib.extra.newmod") == "lib.core.newmod"
    assert surface.canonical("lib.oldmodule") == "lib.oldmodule"  # dotted boundary, not a string prefix


def test_an_alias_names_its_target_when_the_target_lost_the_name(tmp_path: Path) -> None:
    lib = _library(tmp_path / "lib")
    _write(tmp_path / "app", "main.py", "from lib.oldmod import gone\n")
    assert [m for _, _, m in _found(lib, tmp_path / "app")] == ["`from lib.oldmod import gone`: lib.oldmod (alias of lib.core.newmod) does not define 'gone'"]


def test_with_a_reference_only_regressions_are_reported(tmp_path: Path) -> None:
    old = _library(tmp_path / "old")
    _write(old, "src/lib/core/newmod.py", "public = 1\n_moved = 2\n")
    new = _library(tmp_path / "new")
    _write(tmp_path / "app", "main.py", "from lib.core.newmod import _moved\nfrom lib.core.newmod import never_there\n")
    assert [line for _, line, _ in _found(new, tmp_path / "app")] == [1, 2]
    assert [line for _, line, _ in _found(new, tmp_path / "app", reference_root=old)] == [1]


def test_a_library_without_the_package_raises(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    _write(tmp_path / "app", "main.py", "import os\n")
    with pytest.raises(CoreError, match="no package 'lib'"):
        _found(tmp_path / "lib", tmp_path / "app")


def test_a_package_at_the_root_is_found(tmp_path: Path) -> None:
    _write(tmp_path / "lib", "lib/__init__.py", "x = 1\n")
    assert package_dir(tmp_path / "lib", "lib") == tmp_path / "lib" / "lib"


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path: Path) -> None:
    lib = _library(tmp_path / "lib")
    _write(tmp_path / "a", "main.py", "from lib.core.newmod import _moved\n")
    _write(tmp_path / "b", "main.py", "from lib.core.newmod import _moved\n", bom=True)
    assert _found(lib, tmp_path / "a") == _found(lib, tmp_path / "b") != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path: Path) -> None:
    lib = _library(tmp_path / "lib")
    _write(tmp_path / "app", "ok.py", "from lib.core import public\n")
    _write(tmp_path / "app", "broken.py", "def broken(:\n")
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _found(lib, tmp_path / "app")
    assert [
        (p, f)
        for p, _, f in [
            (f.path, f.line, f.rule) for f in find_consumer_import_breaks(lib, "lib", [("app", tmp_path / "app")], allow_unparsed=True, use_git=False)
        ]
    ] == [("app/broken.py", "unparsed-file")]


def test_an_empty_consumer_fails_the_floor(tmp_path: Path) -> None:
    lib = _library(tmp_path / "lib")
    (tmp_path / "app").mkdir()
    with pytest.raises(EmptyScanError):
        _found(lib, tmp_path / "app")


def test_assert_names_every_break(tmp_path: Path) -> None:
    lib = _library(tmp_path / "lib")
    _write(tmp_path / "app", "main.py", "from lib.core.newmod import _moved\n")
    with pytest.raises(AssertionError, match=r"(?s)1 consumer import\(s\) of lib.*_moved"):
        assert_consumer_imports_resolve(lib, "lib", [("app", tmp_path / "app")], use_git=False)
    _write(tmp_path / "app", "main.py", "from lib.core.newmod import public\n")
    assert_consumer_imports_resolve(lib, "lib", [("app", tmp_path / "app")], use_git=False)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)


def test_cli_since_ref_reports_only_what_broke_after_the_ref(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    lib = _library(tmp_path / "lib")
    _write(lib, "src/lib/core/newmod.py", "public = 1\n_moved = 2\n")
    _git(lib, "init", "-q")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-q", "-m", "v1")
    _git(lib, "tag", "v1")
    _write(lib, "src/lib/core/newmod.py", "public = 1\n")
    app = tmp_path / "app"
    _write(app, "main.py", "from lib.core.newmod import _moved\nfrom lib.core.newmod import never_there\n")
    assert main(["--library", str(lib), "--package", "lib", "--consumer", str(app), "--since-ref", "v1"]) == 1
    out = capsys.readouterr().out
    assert "1 import(s) resolved at v1 and not now" in out and "_moved" in out and "never_there" not in out
    _write(lib, "src/lib/core/newmod.py", "public = 1\n_moved = 2\n")
    assert main(["--library", str(lib), "--package", "lib", "--consumer", str(app), "--since-ref", "v1"]) == 0
    assert main(["--library", str(lib), "--package", "lib", "--consumer", str(app), "--since-ref", "no-such-ref"]) == 1
    assert "git archive no-such-ref failed" in capsys.readouterr().err


def test_cli_reads_a_repos_file_and_skips_the_library_itself(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    lib = _library(tmp_path / "lib")
    _write(tmp_path / "app", "main.py", "from lib.core import public\n")
    repos = tmp_path / "repos.toml"
    repos.write_text(f'[[repo]]\nname = "app"\npath = "{(tmp_path / "app").as_posix()}"\n\n[[repo]]\nname = "lib"\npath = "{lib.as_posix()}"\n')
    assert main(["--library", str(lib), "--package", "lib", "--repos-file", str(repos)]) == 0
    assert "1 consumer(s) of lib, 0 import(s) unresolved" in capsys.readouterr().out
    assert main(["--library", str(lib), "--package", "lib"]) == 1
