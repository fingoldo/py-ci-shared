"""Tests for py_ci_shared.import_time_global_side_effects."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.import_time_global_side_effects import (
    assert_import_time_global_side_effects,
    find_import_time_global_side_effects,
)

BOM = b"\xef\xbb\xbf"
CANARY = Path(__file__).resolve().parent / "canary" / "import_time_global_side_effects"


def _corpus(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(textwrap.dedent(text).encode("utf-8"))
    return tmp_path


def _shapes(tmp_path: Path, body: str) -> list[str]:
    root = _corpus(tmp_path, {"m.py": body})
    return [f.key.split("::", 1)[1] for f in find_import_time_global_side_effects(root, use_git=False)]


@pytest.mark.parametrize(
    "body, shape",
    [
        ("import logging\nlogging.disable(logging.WARNING)\n", "logging-disable"),
        ("import logging\nlogging.basicConfig(level=10, force=True)\n", "logging-basicconfig-force"),
        ("import logging\nlogging.getLogger().setLevel(10)\n", "root-logger"),
        ("import logging\nlogging.getLogger('root').addHandler(h)\n", "root-logger"),
        ("import logging\nlogging.root.handlers.clear()\n", "root-logger"),
        ("import logging\nlogging.getLogger().handlers = []\n", "root-logger"),
        ("import logging\nroot = logging.getLogger()\nroot.removeHandler(h)\n", "root-logger"),
        ("import warnings\nwarnings.filterwarnings('ignore')\n", "blanket-warnings-filter"),
        ("import warnings\nwarnings.simplefilter('ignore')\n", "blanket-warnings-filter"),
        ("import warnings\nwarnings.simplefilter('ignore', Warning)\n", "blanket-warnings-filter"),
        ("import warnings\nwarnings.filterwarnings(action='ignore', category=Warning)\n", "blanket-warnings-filter"),
        ("import sys, io\nsys.stdout = io.TextIOWrapper(sys.stdout.buffer)\n", "sys.stdout-assign"),
        ("import sys\nsys.stderr = sys.stdout\n", "sys.stderr-assign"),
        ("import sys\nsys.stdin = open('x')\n", "sys.stdin-assign"),
        ("import os\nos.chdir('/tmp')\n", "os-chdir"),
        ("import locale\nlocale.setlocale(locale.LC_ALL, 'C')\n", "locale-setlocale"),
        ("import signal\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n", "signal-signal"),
    ],
)
def test_every_shape_is_reported(tmp_path, body, shape):
    assert _shapes(tmp_path, body) == [shape]


@pytest.mark.parametrize(
    "body",
    [
        "import logging\nlogging.basicConfig(level=10)\n",
        "import logging\nlogging.basicConfig(level=10, force=False)\n",
        "import logging\nlogging.getLogger('app').setLevel(10)\nlogging.getLogger(__name__).addHandler(h)\n",
        "import logging\nlog = logging.getLogger('app')\nlog.setLevel(10)\n",
        "import warnings\nwarnings.filterwarnings('ignore', category=DeprecationWarning)\n",
        "import warnings\nwarnings.filterwarnings('ignore', message='noisy')\n",
        "import warnings\nwarnings.filterwarnings('ignore', module='sklearn')\n",
        "import warnings\nwarnings.simplefilter('default')\n",
        "import warnings\nwith warnings.catch_warnings():\n    warnings.simplefilter('ignore')\n    import noisy\n",
        "import sys\nsys.stdout.reconfigure(encoding='utf-8')\nprint(sys.stdout)\n",
        "import os\nos.environ['X'] = '1'\nos.getcwd()\n",
        "import sys\nsys.path.insert(0, 'x')\n",
    ],
)
def test_nearest_correct_code_is_not_reported(tmp_path, body):
    assert _shapes(tmp_path, body) == []


def test_aliases_resolve(tmp_path):
    body = "import logging as lg\nfrom logging import disable\nfrom os import chdir as cd\nimport warnings as w\nlg.disable(1)\ndisable(2)\ncd('x')\nw.filterwarnings('ignore')\n"
    assert _shapes(tmp_path, body) == ["logging-disable", "logging-disable", "os-chdir", "blanket-warnings-filter"]


def test_main_guard_and_function_bodies_are_exempt(tmp_path):
    body = """
        import logging, sys, os
        def configure():
            logging.disable(logging.WARNING)
            sys.stdout = open('x', 'w')
        class K:
            def m(self):
                os.chdir('/')
        f = lambda: os.chdir('/')
        if __name__ == "__main__":
            logging.disable(logging.WARNING)
            configure()
        if "__main__" == __name__:
            os.chdir('/')
    """
    assert _shapes(tmp_path, body) == []


def test_a_main_guard_does_not_exempt_the_statements_outside_it(tmp_path):
    body = """
        import logging, os
        logging.disable(logging.WARNING)
        if __name__ == "__main__":
            os.chdir('/')
        else:
            os.chdir('/else')
        if __name__ != "__main__":
            logging.disable(1)
    """
    assert _shapes(tmp_path, body) == ["logging-disable", "os-chdir", "logging-disable"]


def test_statements_nested_in_module_level_blocks_are_reported(tmp_path):
    body = """
        import logging, os, sys
        try:
            import colorama
        except ImportError:
            logging.disable(logging.WARNING)
        if sys.platform == "win32":
            os.chdir('C:/')
        with open('x') as f:
            sys.stdout = f
        class K:
            logging.getLogger().setLevel(10)
    """
    assert sorted(_shapes(tmp_path, body)) == ["logging-disable", "os-chdir", "root-logger", "sys.stdout-assign"]


def test_the_key_is_line_free(tmp_path):
    a = find_import_time_global_side_effects(_corpus(tmp_path / "a", {"m.py": "import os\nos.chdir('x')\n"}), use_git=False)
    b = find_import_time_global_side_effects(_corpus(tmp_path / "b", {"m.py": "import os\n\n\n# moved\nos.chdir('x')\n"}), use_git=False)
    assert [f.key for f in a] == [f.key for f in b] == ["m.py::os-chdir"] and a[0].line != b[0].line


def _repo(tmp_path: Path, importer: str) -> tuple[Path, Path]:
    _corpus(
        tmp_path,
        {
            "scripts/__init__.py": "",
            "scripts/tool.py": "import logging\nlogging.disable(1)\n",
            "scripts/sub/__init__.py": "",
            "scripts/sub/deep.py": "import os\nos.chdir('x')\n",
            "scripts/chain.py": "from scripts import leaf\n",
            "scripts/leaf.py": "import locale\nlocale.setlocale(0, 'C')\n",
            "scripts/unused.py": "import signal\nsignal.signal(2, 3)\n",
            "tests/test_a.py": importer,
        },
    )
    return tmp_path / "scripts", tmp_path / "tests"


def _imported(tmp_path: Path, importer: str) -> list[str]:
    root, tests = _repo(tmp_path, importer)
    return sorted(f.key for f in find_import_time_global_side_effects(root, imported_by=tests, use_git=False))


def test_without_imported_by_every_module_is_checked(tmp_path):
    root, _ = _repo(tmp_path, "x = 1\n")
    keys = sorted(f.key for f in find_import_time_global_side_effects(root, use_git=False))
    assert keys == ["leaf.py::locale-setlocale", "sub/deep.py::os-chdir", "tool.py::logging-disable", "unused.py::signal-signal"]


@pytest.mark.parametrize(
    "importer",
    [
        "from scripts.tool import helper\n",
        "import scripts.tool\n",
        "from scripts import tool\n",
        "import scripts.tool as t\n",
        "def test():\n    from scripts.tool import f\n",
    ],
)
def test_imported_by_keeps_only_modules_a_test_imports(tmp_path, importer):
    assert _imported(tmp_path, importer) == ["tool.py::logging-disable"]


def test_imported_by_follows_dotted_packages_and_module_level_chains(tmp_path):
    assert _imported(tmp_path, "from scripts.sub.deep import x\nimport scripts.chain\n") == ["leaf.py::locale-setlocale", "sub/deep.py::os-chdir"]


def test_imported_by_matches_a_sys_path_style_import_and_relative_imports(tmp_path):
    assert _imported(tmp_path, "import tool\nfrom sub import deep\n") == ["sub/deep.py::os-chdir", "tool.py::logging-disable"]
    root, tests = _repo(tmp_path / "rel", "x = 1\n")
    (root / "chain.py").write_text("from . import tool\n", encoding="utf-8")
    (tests / "test_a.py").write_text("import scripts.chain\n", encoding="utf-8")
    assert [f.key for f in find_import_time_global_side_effects(root, imported_by=tests, use_git=False)] == ["tool.py::logging-disable"]


def test_imported_by_ignores_an_import_inside_a_function_of_a_script(tmp_path):
    root, tests = _repo(tmp_path, "import scripts.chain\n")
    (root / "chain.py").write_text("def later():\n    from scripts import leaf\n", encoding="utf-8")
    assert find_import_time_global_side_effects(root, imported_by=tests, use_git=False) == []


def test_the_original_glossum_bug_is_reported_in_all_three_shapes_and_the_fix_is_clean(tmp_path):
    bug = find_import_time_global_side_effects(_canary(tmp_path / "bug", "violation"), use_git=False)
    assert sorted(f.key.split("::")[1] for f in bug) == ["blanket-warnings-filter", "logging-disable", "sys.stdout-assign"]
    assert find_import_time_global_side_effects(_canary(tmp_path / "fix", "clean"), use_git=False) == []


def _canary(dest: Path, variant: str) -> Path:
    dest.mkdir(parents=True)
    for p in (CANARY / variant).glob("*.canary"):
        (dest / p.name[: -len(".canary")]).write_bytes(p.read_bytes())
    return dest


def test_the_baseline_accepts_known_findings_refuses_new_ones_and_only_shrinks(tmp_path, monkeypatch):
    root = _corpus(tmp_path / "src", {"m.py": "import os\nos.chdir('x')\n"})
    baseline = tmp_path / "baseline.json"
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    with pytest.raises(pytest.skip.Exception):
        assert_import_time_global_side_effects(root, baseline_path=baseline, refresh=True, use_git=False)
    monkeypatch.delenv("PY_CI_SHARED_REFRESH_ALLOW_GROW")
    assert list(json.loads(baseline.read_text(encoding="utf-8"))["entries"]) == ["m.py::os-chdir"]
    assert_import_time_global_side_effects(root, baseline_path=baseline, use_git=False)
    (root / "n.py").write_text("import logging\nlogging.disable(1)\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"n.py:2: .*move it into a function"):
        assert_import_time_global_side_effects(root, baseline_path=baseline, use_git=False)


def test_assert_without_a_baseline_fails_and_names_the_remedy(tmp_path):
    root = _corpus(tmp_path, {"m.py": "import os\nos.chdir('x')\n"})
    with pytest.raises(AssertionError, match=r"1 import-time-global-side-effects finding.*__main__"):
        assert_import_time_global_side_effects(root, use_git=False)


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root = tmp_path
    (root / "m.py").write_bytes(BOM + b"import os\nos.chdir('x')\n")
    assert [f.key for f in find_import_time_global_side_effects(root, use_git=False)] == ["m.py::os-chdir"]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"m.py": "import os\nos.chdir('x')\n", "broken.py": "def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_import_time_global_side_effects(root, use_git=False)
    assert [f.key for f in find_import_time_global_side_effects(root, allow_unparsed=True, use_git=False)] == ["m.py::os-chdir"]


def test_an_unparsable_test_file_fails_in_imported_by_mode(tmp_path):
    root, tests = _repo(tmp_path, "def broken(:\n")
    with pytest.raises(UnparsedFilesError, match=r"test_a.py"):
        find_import_time_global_side_effects(root, imported_by=tests, use_git=False)


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_import_time_global_side_effects(tmp_path, use_git=False)
