"""ci_install_covers_conftest: a pytest job must install what the conftests it loads import at collection."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Optional

import pytest

from py_ci_shared import ci_install_covers_conftest as gate
from py_ci_shared._ci_install_parts import eval_marker

REPO = Path(__file__).resolve().parents[1]

PYPROJECT = """
[project]
name = "seedpkg"
version = "0.1.0"
dependencies = ["numpy>=1.22", "colorama; sys_platform == 'win32'"]

[project.optional-dependencies]
dev = ["pytest>=7", "seedpkg[lint]"]
lint = ["ruff==0.16.1"]
all = ["seedpkg[dev]", "polars"]

[dependency-groups]
base = ["hypothesis"]
dev = [{include-group = "base"}, "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared@abc"]
"""

CONFTEST_PYUTILZ_SHAPE = """
import sys

import numpy as np


def pytest_addoption(parser):
    if sys.version_info >= (3, 9):
        from py_ci_shared.code_audit_meta import register_refresh_option

        register_refresh_option(parser)
"""


@pytest.fixture(autouse=True)
def _no_installed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Map imports only through the tables and the repo itself, whatever this interpreter happens to have installed."""
    monkeypatch.setattr(gate, "installed_map", lambda: {})
    monkeypatch.setattr(gate, "installed_dist", lambda name: False)


def _job(install: str, test: str = "pytest tests/", python: str = '"3.11"', extra_steps: str = "", matrix: str = "") -> str:
    install_block = textwrap.indent(textwrap.dedent(install).strip(), " " * 10)
    test_block = textwrap.indent(textwrap.dedent(test).strip(), " " * 10)
    return (
        "name: ci\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        + (f"    strategy:\n      matrix:\n{matrix}" if matrix else "")
        + "    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n"
        + f"          python-version: {python}\n"
        + extra_steps
        + f"      - name: Install\n        run: |\n{install_block}\n"
        + f"      - name: Test\n        run: |\n{test_block}\n"
    )


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    defaults = {"pyproject.toml": PYPROJECT, "tests/conftest.py": CONFTEST_PYUTILZ_SHAPE}
    for rel, text in {**defaults, **files}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(textwrap.dedent(text).lstrip("\n").encode("utf-8"))
    return tmp_path


def _findings(root: Path, **kwargs) -> list[gate.Finding]:
    findings, problems, _ = gate.collect_ci_install_gaps(root, **kwargs)
    assert not problems, problems
    return findings


def _missing(root: Path, **kwargs) -> dict[str, str]:
    """``{distribution: rule}`` of every finding."""
    out: dict[str, str] = {}
    for f in _findings(root, **kwargs):
        dist = f.key.split("::")[-1]
        out[dist] = f.rule
    return out


# --------------------------------------------------------------------------------------------------------------------
# the motivating regression


def test_pyutilz_numba_coverage_shape_is_reported_with_both_locations(tmp_path: Path) -> None:
    workflow = _job('uv pip install -e ".[dev]"', "pytest -p no:randomly \\\n  --timeout=120 \\\n  tests/")
    root = _repo(tmp_path, {".github/workflows/numba.yml": workflow, "requirements-dev.txt": "py-ci-shared @ git+https://x/y.git ; python_version >= '3.9'\n"})
    findings = _findings(root)
    assert [f.rule for f in findings] == [gate.RULE_MISSING]
    finding = findings[0]
    lines = workflow.splitlines()
    assert lines[finding.line - 1].strip().startswith("pytest -p no:randomly"), finding.render()
    assert finding.path == ".github/workflows/numba.yml"
    assert "'py-ci-shared'" in finding.message and "tests/conftest.py:8" in finding.message and "job 'test'" in finding.message
    assert "(python 3.11)" in finding.message


def test_installing_the_requirements_file_fixes_it(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            ".github/workflows/numba.yml": _job('uv pip install -e ".[dev]" -r requirements-dev.txt'),
            "requirements-dev.txt": "py-ci-shared @ git+https://x/y.git ; python_version >= '3.9'\n",
        },
    )
    assert _findings(root) == []


def test_the_assert_fails_naming_the_job_and_passes_once_fixed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job('pip install -e ".[dev]"')})
    with pytest.raises(pytest.fail.Exception, match="py-ci-shared"):
        gate.assert_ci_install_covers_conftest(root)
    (root / ".github/workflows/ci.yml").write_text(_job('pip install -e ".[dev]" py-ci-shared'), encoding="utf-8")
    gate.assert_ci_install_covers_conftest(root)


def test_find_returns_rendered_findings(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job('pip install -e ".[dev]"')})
    found = gate.find_ci_install_gaps(root)
    assert len(found) == 1 and found[0].startswith(".github/workflows/ci.yml:") and "[ci-install-missing]" in found[0]


# --------------------------------------------------------------------------------------------------------------------
# install forms


@pytest.mark.parametrize(
    "install",
    [
        "pip install numpy py-ci-shared",
        "python -m pip install 'numpy>=1.0' 'py_ci_shared==1.19'",
        "uv pip install --system numpy py-ci-shared[extra]",
        "python3 -m pip install -U numpy 'py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared@abc'",
        "pip install numpy git+https://github.com/fingoldo/py-ci-shared@abc#egg=py-ci-shared",
        "pip install -e . && pip install --group dev",
        "pip install --group pyproject.toml:dev .",
        "pip install -r requirements/ci.txt",
        "pip install -rrequirements/ci.txt",
        "UV_SYSTEM_PYTHON=1 uv pip install -e '.[all]' --index-url https://pypi.org/simple py-ci-shared",
        "uv pip sync requirements/ci.txt",
    ],
)
def test_every_install_form_provides_its_distributions(tmp_path: Path, install: str) -> None:
    files = {
        ".github/workflows/ci.yml": _job(install + "\npip install pytest"),
        "requirements/ci.txt": "-r base.txt\n-c constraints.txt\n--index-url https://example.invalid\npy-ci-shared @ git+https://x/y.git\n",
        "requirements/base.txt": "numpy==2.0  # pinned\n",
    }
    assert _missing(_repo(tmp_path, files)) == {}


@pytest.mark.parametrize(
    ("install", "missing"),
    [
        ("pip install -e .", {"py-ci-shared"}),  # the project and its [project].dependencies only
        ('pip install -e ".[lint]"', {"py-ci-shared"}),
        ("pip install py-ci-shared", {"numpy"}),
        ("pip install -r requirements/ci.txt", {"numpy"}),  # base.txt is not included this time
    ],
)
def test_what_an_install_leaves_out_is_reported(tmp_path: Path, install: str, missing: set[str]) -> None:
    files = {".github/workflows/ci.yml": _job(install), "requirements/ci.txt": "py-ci-shared\n"}
    assert set(_missing(_repo(tmp_path, files))) == missing


def test_extras_expand_through_self_references(tmp_path: Path) -> None:
    conftest = "import polars\nimport ruff\nimport pytest\nimport seedpkg\n"
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job('pip install -e ".[all]"'), "tests/conftest.py": conftest})
    assert _missing(root) == {}  # all -> dev -> lint, and the project's own distribution


def test_a_marker_that_excludes_the_job_python_is_not_provided(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": _job("pip install numpy 'py-ci-shared; python_version >= \"3.12\"'"), "tests/conftest.py": "import py_ci_shared\n"}
    assert _missing(_repo(tmp_path, files)) == {"py-ci-shared": gate.RULE_MISSING}


def test_matrix_versions_are_evaluated_one_by_one(tmp_path: Path) -> None:
    matrix = '        python-version: ["3.8", "3.11"]\n'
    workflow = _job("pip install numpy -r requirements-dev.txt", python="${{ matrix.python-version }}", matrix=matrix)
    files = {".github/workflows/ci.yml": workflow, "requirements-dev.txt": "py-ci-shared ; python_version >= '3.9'\n"}
    assert _findings(_repo(tmp_path, files)) == []  # 3.8: the conftest guard skips the import; 3.11: the marker admits it
    files["tests/conftest.py"] = "import py_ci_shared\n"
    (finding,) = _findings(_repo(tmp_path, files))
    assert "(python 3.8)" in finding.message


def test_uv_lock_provides_everything_in_it(tmp_path: Path) -> None:
    lock = '[[package]]\nname = "numpy"\n\n[[package]]\nname = "py-ci-shared"\n'
    for install in ("uv sync --frozen --extra dev", "uv export --locked -o req.txt"):
        root = _repo(tmp_path / install.split()[1], {".github/workflows/ci.yml": _job(install), "uv.lock": lock})
        assert _findings(root) == [], install
    root = _repo(tmp_path / "run", {".github/workflows/ci.yml": _job("echo no install", test="uv run --frozen pytest tests"), "uv.lock": lock})
    assert _findings(root) == []


def test_uv_sync_without_a_lock_cannot_be_evaluated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("uv sync")})
    assert set(_missing(root).values()) == {gate.RULE_UNEVALUATED}


def test_a_path_outside_the_checkout_is_read_as_its_directory_name(tmp_path: Path) -> None:
    install = """
    git clone https://github.com/fingoldo/pyutilz.git "${{ runner.temp }}/pyutilz"
    uv pip install --system "${{ runner.temp }}/pyutilz[llm]" numpy
    cd "${{ runner.temp }}/py-ci-shared"
    uv pip install --system -e .
    """
    assert _missing(_repo(tmp_path, {".github/workflows/ci.yml": _job(install), "tests/conftest.py": "import numpy, pyutilz, py_ci_shared\n"})) == {}


def test_a_local_composite_action_with_inputs_is_read(tmp_path: Path) -> None:
    action = """
    name: install
    inputs:
      extras:
        default: ".[dev]"
    runs:
      using: composite
      steps:
        - shell: bash
          env:
            EXTRAS: ${{ inputs.extras }}
          run: pip install -e "${EXTRAS}" py-ci-shared
    """
    steps = "      - uses: ./.github/actions/install\n        with:\n          extras: '.[all]'\n"
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("echo", extra_steps=steps), ".github/actions/install/action.yml": action})
    assert _missing(root) == {}


def test_the_install_pyutilz_action_matches_the_one_this_repo_ships() -> None:
    import yaml

    action = yaml.safe_load((REPO / ".github" / "actions" / "install-pyutilz" / "action.yml").read_text(encoding="utf-8"))
    runs = [s["run"].strip() for s in action["runs"]["steps"] if "uv pip install" in s.get("run", "")]
    command, inputs = gate.KNOWN_ACTIONS["fingoldo/py-ci-shared/.github/actions/install-pyutilz"]
    assert runs == [command]
    for step in action["runs"]["steps"]:
        for var, value in (step.get("env") or {}).items():
            if var in inputs:
                assert value == "${{ inputs." + inputs[var] + " }}"


def test_the_remote_install_pyutilz_action_provides_the_project_extras(tmp_path: Path) -> None:
    steps = (
        "      - uses: fingoldo/py-ci-shared/.github/actions/install-pyutilz@abc\n        with:\n"
        "          pyutilz-extras: system\n          project-extras: '.[dev]'\n"
    )
    files = {
        ".github/workflows/ci.yml": _job("pip install py-ci-shared", extra_steps=steps),
        "tests/conftest.py": "import numpy, pyutilz, pytest, py_ci_shared\n",
    }
    assert _missing(_repo(tmp_path, files)) == {}


def test_a_local_shell_script_is_read_in_place(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": _job("bash scripts/install.sh dev"), "scripts/install.sh": 'set -e\nextra="$1"\npip install numpy py-ci-shared\n'}
    assert _missing(_repo(tmp_path, files)) == {}


def test_install_after_the_pytest_step_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("pip install numpy", test="pytest tests\npip install py-ci-shared")})
    assert _missing(root) == {"py-ci-shared": gate.RULE_MISSING}


# --------------------------------------------------------------------------------------------------------------------
# what cannot be evaluated


@pytest.mark.parametrize(
    "install", ["poetry install --with dev", 'pip install -r "$REQS"', "conda install numpy", 'pip install ".[${{ matrix.extra }}]" numpy']
)
def test_an_unreadable_install_form_is_a_finding_of_its_own(tmp_path: Path, install: str) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job(install)})
    assert set(_missing(root).values()) == {gate.RULE_UNEVALUATED}


def test_an_acknowledgement_silences_unevaluated_but_never_missing(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("poetry install")})
    assert _missing(root, acknowledge={"ci.yml::test": "poetry installs the dev group, py-ci-shared included"}) == {}
    assert _missing(root, acknowledge={"ci.yml::test": "  "}) != {}, "an empty reason is not an acknowledgement"
    plain = _repo(tmp_path / "plain", {".github/workflows/ci.yml": _job("pip install numpy")})
    assert _missing(plain, acknowledge={"ci.yml::test": "why"}) == {"py-ci-shared": gate.RULE_MISSING}


def test_an_unknown_third_party_action_cannot_be_evaluated(tmp_path: Path) -> None:
    steps = "      - uses: someone/install-everything@v1\n"
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("pip install numpy", extra_steps=steps)})
    assert _missing(root) == {"py-ci-shared": gate.RULE_UNEVALUATED}


def test_an_import_with_no_known_distribution_is_unmapped(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("pip install numpy"), "tests/conftest.py": "import zzz_unheard_of\n"})
    assert _missing(root) == {"zzz-unheard-of": gate.RULE_UNMAPPED}
    assert _missing(root, aliases={"zzz_unheard_of": "zzz-dist"}) == {"zzz-dist": gate.RULE_MISSING}
    assert _missing(root, aliases={"zzz_unheard_of": ["numpy", "other"]}) == {}


def test_builtin_aliases_and_normalisation(tmp_path: Path) -> None:
    conftest = "import yaml\nimport PIL.Image\nfrom sklearn.base import clone\nimport py_ci_shared\nimport dateutil\n"
    install = "pip install PyYAML Pillow scikit_learn Py.CI.Shared python-dateutil"
    assert _missing(_repo(tmp_path, {".github/workflows/ci.yml": _job(install), "tests/conftest.py": conftest})) == {}


# --------------------------------------------------------------------------------------------------------------------
# what a conftest requires


@pytest.mark.parametrize(
    "conftest",
    [
        "try:\n    import zzz\nexcept ImportError:\n    zzz = None\n",
        "try:\n    import zzz\nexcept (ModuleNotFoundError, OSError):\n    pass\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import zzz\n",
        "def helper():\n    import zzz\n",
        "import pytest\n\n@pytest.fixture\ndef thing():\n    import zzz\n",
        "import sys\nif sys.version_info < (3, 9):\n    import zzz\n",
        "import os, json, pathlib\nfrom . import sibling\nimport helpers\nimport seedpkg\n",
    ],
)
def test_imports_that_do_not_run_at_collection_are_not_required(tmp_path: Path, conftest: str) -> None:
    files = {".github/workflows/ci.yml": _job("pip install pytest"), "tests/conftest.py": conftest, "tests/helpers.py": ""}
    assert _missing(_repo(tmp_path, files)) == {}


@pytest.mark.parametrize(
    "conftest",
    [
        "import zzz.sub\n",
        "def pytest_configure(config):\n    from zzz import x\n",
        "class Plugin:\n    import zzz\n",
        "import sys\nif sys.version_info >= (3, 9):\n    import zzz\nelse:\n    pass\n",
        "try:\n    import zzz\nexcept KeyError:\n    pass\n",
        "import os\nif os.environ.get('CI'):\n    import zzz\n",
    ],
)
def test_imports_that_run_at_collection_are_required(tmp_path: Path, conftest: str) -> None:
    files = {".github/workflows/ci.yml": _job("pip install pytest"), "tests/conftest.py": conftest}
    assert set(_missing(_repo(tmp_path, files), aliases={"zzz": "zzz"})) == {"zzz"}


def test_only_the_conftests_a_run_reaches_are_required(tmp_path: Path) -> None:
    files = {
        ".github/workflows/ci.yml": _job("pip install pytest", test="pytest tests/unit/test_a.py --ignore=tests/live tests/live"),
        "tests/conftest.py": "import pytest\n",
        "tests/unit/conftest.py": "import numpy\n",
        "tests/unit/test_a.py": "",
        "tests/unit/deeper/conftest.py": "import never_loaded\n",
        "tests/live/conftest.py": "import also_never_loaded\n",
        "tests/meta/conftest.py": "import py_ci_shared\n",
    }
    assert _missing(_repo(tmp_path, files), aliases={"never_loaded": "x", "also_never_loaded": "y"}) == {"numpy": gate.RULE_MISSING}


def test_working_directory_selects_the_subproject(tmp_path: Path) -> None:
    workflow = _job('pip install -e ".[dev]"').replace("jobs:\n", "defaults:\n  run:\n    working-directory: apps/web\njobs:\n")
    files = {
        ".github/workflows/web.yml": workflow,
        "apps/web/pyproject.toml": '[project]\nname = "web"\nversion = "1"\n[project.optional-dependencies]\ndev = ["pytest", "httpx"]\n',
        "apps/web/tests/conftest.py": "import httpx\nimport web\n",
        "tests/conftest.py": "import never_loaded_here\n",
    }
    assert _missing(_repo(tmp_path, files)) == {}


@pytest.mark.parametrize(
    "test",
    [
        "python -m pytest tests",
        "xvfb-run -a python -m pytest tests",
        "timeout 600 coverage run -m pytest tests",
        "python -m coverage run --rcfile=.coveragerc -m pytest tests",
        "if true; then pytest tests; fi",
        "poetry run pytest",
        'python - <<"PY"\nimport subprocess, sys\nsys.exit(subprocess.call([sys.executable, "-m", "pytest", "-m", "gpu"]))\nPY',
    ],
)
def test_every_pytest_launcher_is_recognised(tmp_path: Path, test: str) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("pip install numpy", test=test)})
    assert "py-ci-shared" in _missing(root)


def test_a_job_without_pytest_is_not_judged(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("pip install numpy", test="python -m mypy src\necho pytest")})
    assert _findings(root) == []


# --------------------------------------------------------------------------------------------------------------------
# floors, unreadable files, baseline


def test_no_workflow_fails_the_floor(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    with pytest.raises(pytest.fail.Exception, match="workflow file"):
        gate.assert_ci_install_covers_conftest(root)


@pytest.mark.parametrize(
    ("rel", "text"),
    [(".github/workflows/bad.yml", "jobs: [unclosed\n"), ("tests/conftest.py", "def broken(:\n"), ("pyproject.toml", "[project\n")],
)
def test_an_unreadable_file_fails_by_name_unless_allowed(tmp_path: Path, rel: str, text: str) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job("pip install numpy py-ci-shared pytest"), rel: text})
    with pytest.raises(pytest.fail.Exception, match=rel.replace(".", r"\.")):
        gate.assert_ci_install_covers_conftest(root)
    if rel != "pyproject.toml":
        gate.assert_ci_install_covers_conftest(root, allow_unparsed=True)


def test_a_bom_prefixed_workflow_and_conftest_read_like_plain_ones(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job('pip install -e ".[dev]"')})
    plain = [f.render() for f in _findings(root)]
    for rel in (".github/workflows/ci.yml", "tests/conftest.py"):
        (root / rel).write_bytes(b"\xef\xbb\xbf" + (root / rel).read_bytes())
    assert [f.render() for f in _findings(root)] == plain and plain


def test_a_baseline_accepts_the_known_finding_and_fails_when_it_goes_stale(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _job('pip install -e ".[dev]"')})
    (finding,) = _findings(root)
    assert str(finding.line) not in finding.key.split("::")[-1], "the key must survive an edit above the step"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"schema": 1, "gate": "ci-install-covers-conftest", "entries": {finding.key: {"count": 1, "note": "tracked in #1"}}}))
    gate.assert_ci_install_covers_conftest(root, baseline_path=baseline)
    (root / ".github/workflows/ci.yml").write_text(_job('pip install -e ".[dev]" py-ci-shared'), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        gate.assert_ci_install_covers_conftest(root, baseline_path=baseline)


# --------------------------------------------------------------------------------------------------------------------
# markers


@pytest.mark.parametrize(
    ("marker", "python", "expected"),
    [
        ("python_version >= '3.9'", (3, 8), False),
        ("python_version >= '3.9'", (3, 11), True),
        ("python_version < '3.12' and sys_platform == 'linux'", (3, 11), None),
        ("python_version < '3.12' and sys_platform == 'linux'", (3, 12), False),
        ("python_version < '3.10' or python_version >= '3.13'", (3, 13), True),
        ("'3.10' <= python_version", (3, 9), False),
        ("python_full_version >= '3.11.2'", (3, 11), None),
        ("python_full_version >= '3.11.2'", (3, 12), True),
        ("(python_version == '3.11')", (3, 11), True),
        ("python_version != '3.11'", (3, 11), False),
        ("python_version >= '3.9'", None, None),
        ("platform_machine in 'x86_64 aarch64'", (3, 11), None),
        ("python_version ~= '3.10'", (3, 12), True),
        ("garbage ((", (3, 11), None),
    ],
)
def test_markers(marker: str, python: Optional[tuple[int, int]], expected: Optional[bool]) -> None:
    assert eval_marker(marker, python) is expected
