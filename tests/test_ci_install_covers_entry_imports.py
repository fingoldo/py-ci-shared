"""Tests for py_ci_shared.ci_install_covers_entry_imports."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Optional

import pytest

from py_ci_shared._core import Baseline, EmptyScanError, UnparsedFilesError
from py_ci_shared.ci_install_covers_entry_imports import (
    assert_ci_install_covers_entry_imports,
    assert_entry_imports_without_extras,
    blocked_import_names,
    find_ci_install_covers_entry_imports,
)

BOM = b"\xef\xbb\xbf"
PYPROJECT = """[project]
name = "mypkg"
version = "0.1.0"
dependencies = ["numpy>=1.20"]

[project.optional-dependencies]
dev = ["pytest"]
signal = ["pywavelets"]
db = ["zstandard"]
all = ["mypkg[signal,db]"]

[dependency-groups]
dev = ["pytest", "ruff"]
"""
PACKAGE = {
    "mypkg/__init__.py": "",
    "mypkg/nightly.py": "from . import features\n\n\ndef main():\n    return features.transform([1])\n",
    "mypkg/features.py": "import numpy\nimport pywt\n\n\ndef transform(x):\n    return pywt.dwt(numpy.asarray(x), 'haar')\n",
}


def _workflow(steps: list[str], *, name: str = "nightly", extra: str = "") -> str:
    body = "".join(f"      - run: {s}\n" if "\n" not in s else "      - run: |\n" + textwrap.indent(s, "          ") + "\n" for s in steps)
    return (
        f"name: {name}\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  nightly:\n    runs-on: ubuntu-latest\n{extra}"
        "    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.11'\n" + body
    )


def _repo(tmp_path: Path, steps: list[str], *, files: Optional[dict[str, str]] = None, pyproject: str = PYPROJECT, workflow: Optional[str] = None) -> Path:
    for rel, text in {**PACKAGE, **(files or {})}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    wf = tmp_path / ".github" / "workflows" / "nightly.yml"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(workflow if workflow is not None else _workflow(steps), encoding="utf-8")
    return tmp_path


def _line_of(root: Path, needle: str) -> int:
    lines = (root / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8").splitlines()
    return next(i for i, line in enumerate(lines, 1) if needle in line)


def test_reports_the_seeded_violation_naming_the_extra_the_import_and_the_chain(tmp_path):
    root = _repo(tmp_path, ['pip install -e ".[dev]"', "python -m mypkg.nightly"])
    (finding,) = find_ci_install_covers_entry_imports(root)
    assert (finding.path, finding.line, finding.rule) == (
        ".github/workflows/nightly.yml",
        _line_of(root, "python -m mypkg.nightly"),
        "ci-install-missing-entry-import",
    )
    message = finding.message
    assert "job 'nightly'" in message and "`python -m mypkg.nightly`" in message and "'pywt'" in message
    assert "mypkg/features.py:2" in message and "mypkg.nightly -> mypkg.features" in message
    assert "'pywavelets'" in message and "'all', 'signal' extra" in message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    assert find_ci_install_covers_entry_imports(_repo(tmp_path, ['pip install -e ".[dev,signal]"', "python -m mypkg.nightly"])) == []


def test_a_bom_prefixed_workflow_is_reported_like_the_plain_one(tmp_path):
    plain = find_ci_install_covers_entry_imports(_repo(tmp_path / "a", ['pip install -e ".[dev]"', "python -m mypkg.nightly"]))
    root = _repo(tmp_path / "b", [], workflow=_workflow(['pip install -e ".[dev]"', "python -m mypkg.nightly"]))
    wf = root / ".github" / "workflows" / "nightly.yml"
    wf.write_bytes(BOM + wf.read_bytes())
    bom = find_ci_install_covers_entry_imports(root)
    assert [(f.line, f.message) for f in bom] == [(f.line, f.message) for f in plain] and len(plain) == 1


def test_an_unparsable_workflow_or_module_fails_by_name_unless_allowed(tmp_path):
    root = _repo(tmp_path, ['pip install -e ".[dev]"', "python -m mypkg.nightly"])
    (root / ".github" / "workflows" / "broken.yml").write_text("name: x\non: [push\njobs: {\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match=r"broken\.yml"):
        find_ci_install_covers_entry_imports(root)
    assert len(find_ci_install_covers_entry_imports(root, allow_unparsed=True)) == 1
    (root / ".github" / "workflows" / "broken.yml").unlink()
    (root / "mypkg" / "features.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match=r"features\.py"):
        find_ci_install_covers_entry_imports(root)


def test_the_floor_counts_readable_workflows_and_a_missing_pyproject_cannot_be_judged_around(tmp_path):
    root = _repo(tmp_path, ["python -m mypkg.nightly"])
    with pytest.raises(EmptyScanError, match="only 1 workflow"):
        find_ci_install_covers_entry_imports(root, min_files=2)
    (root / ".github" / "workflows" / "nightly.yml").unlink()
    with pytest.raises(EmptyScanError, match="only 0 workflow"):
        find_ci_install_covers_entry_imports(root)
    (root / "pyproject.toml").unlink()
    with pytest.raises(UnparsedFilesError, match="cannot read the project's dependencies"):
        find_ci_install_covers_entry_imports(root)


@pytest.mark.parametrize(
    "install",
    [
        pytest.param('pip install -e ".[dev,signal]"', id="two-extras"),
        pytest.param('pip install ".[signal]"', id="non-editable"),
        pytest.param('python -m pip install -e ".[all]"', id="self-referencing-extra"),
        pytest.param('uv pip install --system -e ".[signal]"', id="uv-pip"),
        pytest.param("pip install -e . && pip install pywavelets", id="the-distribution-by-name"),
        pytest.param("pip install -e . pywavelets>=1.4", id="the-distribution-with-a-specifier"),
        pytest.param('pip install -e ".[dev]" "pywavelets; python_version >= \'3.8\'"', id="the-distribution-with-a-marker"),
        pytest.param("pip install -r requirements.txt", id="requirements-file"),
        pytest.param("uv sync --extra signal", id="uv-sync-extra"),
        pytest.param("uv sync --extra dev --extra signal", id="uv-sync-two-extras"),
        pytest.param("uv sync --all-extras", id="uv-sync-all-extras"),
    ],
)
def test_every_install_form_that_provides_the_distribution_is_clean(tmp_path, install):
    root = _repo(tmp_path, [install, "python -m mypkg.nightly"], files={"requirements.txt": "-e .\npywavelets\n"})
    assert find_ci_install_covers_entry_imports(root) == []


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param(['pip install -e ".[dev]"', "python -m mypkg.nightly --dry-run"], id="python-m"),
        pytest.param(["pip install -e .", "python -m mypkg.nightly"], id="no-extras"),
        pytest.param(['pip install -e ".[db]"', "python3.11 -m mypkg.nightly"], id="other-extra-and-versioned-python"),
        pytest.param(['pip install -e ".[dev]"', "python -u -m mypkg.nightly"], id="python-flag"),
        pytest.param(['pip install -e ".[dev]"', "FOO=1 python -m mypkg.nightly"], id="env-prefix"),
        pytest.param(['pip install -e ".[dev]"', "coverage run -m mypkg.nightly"], id="coverage-run"),
        pytest.param(['pip install -e ".[dev]"', "python scripts/run.py"], id="script"),
        pytest.param(['pip install -e ".[dev]"', "scripts/run.py --flag"], id="bare-script"),
        pytest.param(["uv sync", "uv run python -m mypkg.nightly"], id="uv-sync-without-extras"),
        pytest.param(["uv sync --extra dev", "uv run python -m mypkg.nightly"], id="uv-sync-other-extra"),
        pytest.param(["uv run python -m mypkg.nightly"], id="uv-run-alone"),
        pytest.param(["uv run --extra dev python -m mypkg.nightly"], id="uv-run-other-extra"),
        pytest.param(['pip install -e ".[dev]"', "python -m mypkg.nightly", 'pip install -e ".[signal]"'], id="the-install-comes-after-the-run"),
        pytest.param(['pip install -e ".[dev]"', "cd scripts && python run.py"], id="after-cd"),
        pytest.param(
            ['pip install -e ".[dev]"\npython -m mypkg.nightly \\\n  --tier "${{ inputs.tier || \'nightly\' }}"'],
            id="multi-line-with-an-unknown-expression-in-the-arguments",
        ),
    ],
)
def test_every_entry_form_that_runs_without_the_distribution_is_reported(tmp_path, steps):
    files = {"scripts/run.py": "import mypkg.features\n"}
    root = _repo(tmp_path, steps, files=files)
    found = find_ci_install_covers_entry_imports(root)
    assert len(found) == 1 and "'pywavelets'" in found[0].message


def test_uv_sync_installs_the_default_dev_group_unless_told_not_to(tmp_path):
    ruff = {"mypkg/features.py": "import ruff\n"}
    only = PYPROJECT.replace('signal = ["pywavelets"]', 'signal = ["ruff"]')
    assert find_ci_install_covers_entry_imports(_repo(tmp_path / "a", ["uv sync", "uv run python -m mypkg.nightly"], files=ruff, pyproject=only)) == []
    (finding,) = find_ci_install_covers_entry_imports(
        _repo(tmp_path / "b", ["uv sync --no-dev", "uv run --no-sync python -m mypkg.nightly"], files=ruff, pyproject=only)
    )
    assert "'ruff'" in finding.message and "'signal' extra" in finding.message


def test_a_job_that_does_not_install_the_project_or_reads_an_install_it_cannot_evaluate_is_not_judged(tmp_path):
    assert find_ci_install_covers_entry_imports(_repo(tmp_path / "a", ["pip install numpy", "python -m mypkg.nightly"])) == []
    assert find_ci_install_covers_entry_imports(_repo(tmp_path / "b", ['pip install -e ".[dev]"', "poetry install", "python -m mypkg.nightly"])) == []
    assert find_ci_install_covers_entry_imports(_repo(tmp_path / "c", ['pip install -e ".[dev]" -r "$REQS"', "python -m mypkg.nightly"])) == []


def test_only_declared_distributions_are_judged_never_core_stdlib_or_undeclared_imports(tmp_path):
    features = "import os\nimport json\nimport numpy\nimport requests\nfrom . import nightly\n"
    root = _repo(tmp_path, ['pip install -e ".[dev]"', "python -m mypkg.nightly"], files={"mypkg/features.py": features})
    assert find_ci_install_covers_entry_imports(root) == []


@pytest.mark.parametrize(
    "features",
    [
        pytest.param("try:\n    import pywt\nexcept ImportError:\n    pywt = None\n", id="try-except"),
        pytest.param("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pywt\n", id="TYPE_CHECKING"),
        pytest.param("def transform(x):\n    import pywt\n    return pywt\n", id="function-level"),
        pytest.param("import pywt  # optional-import-ok: only used by the signal extra's own entry\n", id="allow-comment"),
        pytest.param("try:\n    import pywt\n    HAS = True\nexcept ImportError:\n    HAS = False\nif HAS:\n    import pywt as p2\n", id="flag"),
    ],
)
def test_guarded_imports_do_not_make_the_entry_need_the_extra(tmp_path, features):
    root = _repo(tmp_path, ['pip install -e ".[dev]"', "python -m mypkg.nightly"], files={"mypkg/features.py": features})
    assert find_ci_install_covers_entry_imports(root) == []


def test_a_test_file_that_importorskips_the_package_first_is_skipped_not_broken(tmp_path):
    files = {"tests/test_a.py": 'import pytest\n\npytest.importorskip("pywt")\n\nimport pywt\n'}
    assert find_ci_install_covers_entry_imports(_repo(tmp_path, ['pip install -e ".[dev]"', "pytest tests"], files=files)) == []
    unguarded = {"tests/test_a.py": "import pytest\n\nimport pywt\n"}
    assert len(find_ci_install_covers_entry_imports(_repo(tmp_path / "b", ['pip install -e ".[dev]"', "pytest tests"], files=unguarded))) == 1


def test_only_what_the_entry_reaches_at_module_level_counts(tmp_path):
    lazy = {"mypkg/nightly.py": "def main():\n    from . import features\n    return features\n"}
    assert find_ci_install_covers_entry_imports(_repo(tmp_path / "a", ['pip install -e ".[dev]"', "python -m mypkg.nightly"], files=lazy)) == []
    other = {"mypkg/nightly.py": "x = 1\n", "mypkg/elsewhere.py": "import pywt\n"}
    assert find_ci_install_covers_entry_imports(_repo(tmp_path / "b", ['pip install -e ".[dev]"', "python -m mypkg.nightly"], files=other)) == []
    deep = {"mypkg/nightly.py": "from .a import b\n", "mypkg/a/__init__.py": "", "mypkg/a/b.py": "from .. import features\n"}
    (finding,) = find_ci_install_covers_entry_imports(_repo(tmp_path / "c", ['pip install -e ".[dev]"', "python -m mypkg.nightly"], files=deep))
    assert "mypkg.nightly -> mypkg.a.b -> mypkg.features" in finding.message


def test_a_package_entry_runs_its_main_module_and_the_package_init(tmp_path):
    files = {"mypkg/__main__.py": "from . import features\n", "mypkg/nightly.py": "x = 1\n"}
    (finding,) = find_ci_install_covers_entry_imports(_repo(tmp_path, ['pip install -e ".[dev]"', "python -m mypkg"], files=files))
    assert "'pywt'" in finding.message
    init = {"mypkg/__init__.py": "import pywt\n"}
    assert len(find_ci_install_covers_entry_imports(_repo(tmp_path / "b", ['pip install -e ".[dev]"', "python -m mypkg.nightly"], files=init))) == 1


def test_two_imports_and_two_entries_of_one_distribution_make_one_finding_at_the_first_entry(tmp_path):
    files = {"mypkg/nightly.py": "from . import features, more\n", "mypkg/more.py": "import pywt\n"}
    root = _repo(tmp_path, ['pip install -e ".[dev]"', "python -m mypkg.nightly", "python -m mypkg.nightly --again"], files=files)
    (finding,) = find_ci_install_covers_entry_imports(root)
    assert "and 1 more import(s)" in finding.message
    assert finding.line == _line_of(root, "python -m mypkg.nightly")


def test_a_composite_action_that_installs_the_extra_counts(tmp_path):
    action = tmp_path / ".github" / "actions" / "setup" / "action.yml"
    action.parent.mkdir(parents=True)
    action.write_text('name: setup\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: pip install -e ".[signal]"\n', encoding="utf-8")
    workflow = _workflow([]) + "      - uses: ./.github/actions/setup\n      - run: python -m mypkg.nightly\n"
    assert find_ci_install_covers_entry_imports(_repo(tmp_path, [], workflow=workflow)) == []
    action.write_text(action.read_text(encoding="utf-8").replace(".[signal]", ".[dev]"), encoding="utf-8")
    assert len(find_ci_install_covers_entry_imports(_repo(tmp_path, [], workflow=workflow))) == 1


def test_working_directory_and_a_script_next_to_its_own_modules(tmp_path):
    files = {"tools/run.py": "import helper\n", "tools/helper.py": "import pywt\n"}
    workflow = _workflow(['pip install -e ".[dev]"', "python run.py"]).replace(
        "      - run: python run.py\n", "      - run: python run.py\n        working-directory: tools\n"
    )
    (finding,) = find_ci_install_covers_entry_imports(_repo(tmp_path, [], files=files, workflow=workflow))
    assert "tools/helper.py:1" in finding.message


def test_pytest_entries_follow_each_test_file_unless_switched_off_or_ignored(tmp_path):
    files = {
        "tests/test_a.py": "import pywt\n\n\ndef test_a():\n    pass\n",
        "tests/slow/test_b.py": "import zstandard\n",
        "tests/conftest.py": "import zstandard\n",
    }
    steps = ['pip install -e ".[dev]"', "pytest tests"]
    root = _repo(tmp_path, steps, files=files)
    found = find_ci_install_covers_entry_imports(root)
    assert len(found) == 2
    assert {f.message.split("imports ")[1].split(" ")[0] for f in found} == {"'pywt'", "'zstandard'"}
    assert find_ci_install_covers_entry_imports(root, include_pytest=False) == []
    ignored = _repo(tmp_path / "b", ['pip install -e ".[dev]"', "pytest tests --ignore=tests/slow --ignore tests/conftest.py"], files=files)
    (only,) = find_ci_install_covers_entry_imports(ignored)
    assert "'pywt'" in only.message
    one_file = _repo(tmp_path / "c", ['pip install -e ".[dev]"', "python -m pytest tests/slow/test_b.py::test_x -q -k expr"], files=files)
    (single,) = find_ci_install_covers_entry_imports(one_file)
    assert "'zstandard'" in single.message and "tests/slow/test_b.py" in single.message
    no_paths = _repo(tmp_path / "d", ['pip install -e ".[dev]"', "pytest -q"], files=files)
    assert find_ci_install_covers_entry_imports(no_paths) == []


def test_name_map_and_exclude_and_the_assert(tmp_path):
    files = {"mypkg/features.py": "import fancywavelets\n"}
    root = _repo(tmp_path, ['pip install -e ".[dev]"', "python -m mypkg.nightly"], files=files)
    assert find_ci_install_covers_entry_imports(root) == []
    (finding,) = find_ci_install_covers_entry_imports(root, name_map={"fancywavelets": "pywavelets"})
    assert "'fancywavelets'" in finding.message
    assert find_ci_install_covers_entry_imports(root, name_map={"fancywavelets": "pywavelets"}, exclude=("nightly.yml",), min_files=0) == []
    with pytest.raises(AssertionError, match=r"nightly\.yml:\d+"):
        assert_ci_install_covers_entry_imports(root, name_map={"fancywavelets": "pywavelets"})
    baseline = tmp_path / "baseline.json"
    Baseline(baseline, gate="ci_install_covers_entry_imports").enforce(
        find_ci_install_covers_entry_imports(root, name_map={"fancywavelets": "pywavelets"}), refresh=True, grow=True
    )
    assert_ci_install_covers_entry_imports(root, name_map={"fancywavelets": "pywavelets"}, baseline_path=baseline)


# --------------------------------------------------------------------------------------------------------------------
# the dynamic twin


def test_blocked_import_names_cover_the_extras_beyond_the_core_dependencies(tmp_path):
    root = _repo(tmp_path, [])
    names = blocked_import_names(root, ["signal"], name_map={"pywt": "pywavelets"})
    assert "pywt" in names and "pywavelets" in names and "zstandard" not in names and "numpy" not in names
    assert "pywt" in blocked_import_names(root, ["signal"]), "the built-in alias table knows pywt without any installed metadata"
    both = blocked_import_names(root, ["signal", "db"], name_map={"pywt": "pywavelets"})
    assert {"pywt", "zstandard"} <= set(both)
    through_all = blocked_import_names(root, ["all"], name_map={"pywt": "pywavelets"})
    assert {"pywt", "zstandard"} <= set(through_all)


def test_blocked_import_names_refuse_an_unknown_extra_and_a_block_that_blocks_nothing(tmp_path):
    root = _repo(tmp_path, [])
    with pytest.raises(AssertionError, match="no extra named"):
        blocked_import_names(root, ["nope"])
    with pytest.raises(AssertionError, match="nothing would be blocked"):
        blocked_import_names(root, [])


def _dynamic_repo(tmp_path: Path, features: str, pyproject: Optional[str] = None) -> Path:
    files = {
        "fakewavelets.py": "VALUE = 1\n",
        "mypkg/__init__.py": "",
        "mypkg/features.py": features,
        "mypkg/cli.py": "import sys\nfrom . import features\n\nif __name__ == '__main__':\n    assert sys.argv[1:] == ['--dry-run'], sys.argv\n    raise SystemExit(0)\n",
    }
    root = _repo(tmp_path, [], files=files, pyproject=(pyproject or PYPROJECT).replace('"pywavelets"', '"fake-wavelets"'))
    return root


def test_an_entry_that_imports_a_blocked_extra_fails_to_start_and_names_it(tmp_path):
    root = _dynamic_repo(tmp_path, "import fakewavelets\n")
    mapping = {"fakewavelets": "fake-wavelets"}
    with pytest.raises(AssertionError, match=r"import mypkg.features exited 1[\s\S]*blocked by the test: fakewavelets"):
        assert_entry_imports_without_extras(root, ["mypkg.features"], ["signal"], name_map=mapping)
    assert_entry_imports_without_extras(root, ["mypkg.features"], ["db"], name_map=mapping)


def test_a_guarded_import_starts_without_the_extra_and_arguments_reach_the_entry(tmp_path):
    root = _dynamic_repo(tmp_path, "try:\n    import fakewavelets\nexcept ImportError:\n    fakewavelets = None\n")
    assert_entry_imports_without_extras(root, ["mypkg", ["mypkg.cli", "--dry-run"]], ["signal"], name_map={"fakewavelets": "fake-wavelets"})
    with pytest.raises(AssertionError, match=r"run mypkg\.cli --wrong exited 1"):
        assert_entry_imports_without_extras(root, [["mypkg.cli", "--wrong"]], ["signal"], name_map={"fakewavelets": "fake-wavelets"})


def test_every_failing_entry_is_listed_not_just_the_first(tmp_path):
    root = _dynamic_repo(tmp_path, "import fakewavelets\n")
    with pytest.raises(AssertionError, match=r"2 entry\(ies\) fail") as info:
        assert_entry_imports_without_extras(root, ["mypkg.features", ["mypkg.cli", "--dry-run"]], ["signal"], name_map={"fakewavelets": "fake-wavelets"})
    assert "import mypkg.features" in str(info.value) and "run mypkg.cli --dry-run" in str(info.value)
