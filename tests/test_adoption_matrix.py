"""Unit tests for the consumer adoption matrix: fake consumer repos on disk, one per pin location and skip shape."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.adoption_matrix import (
    RefResolver,
    classify_ref,
    find_module_usage,
    find_pins,
    find_silent_skips,
    load_repo_list,
    main,
    render_markdown,
    scan_repo,
)

URL = "git+https://github.com/fingoldo/py-ci-shared.git"
SHA = "94b1c0dede79e8444c08288e9832b5d7f2dcf895"
SHA2 = "f50288e5a6f5b7f972efb60f948ce6184c8e9e70"


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(textwrap.dedent(text).lstrip("\n").encode("utf-8"))


def _repo(tmp_path: Path, files: dict[str, str], name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    for rel, text in files.items():
        _write(root, rel, text)
    return root


def _pins(root: Path) -> list[tuple[str, str, str, object, str]]:
    return [(p.path, p.location, p.target, p.ref, p.kind) for p in find_pins(root)]


@pytest.mark.parametrize(
    "ref, kind",
    [(SHA, "sha"), ("94b1c0d", "sha"), ("v1.16.1", "release"), ("v1", "moving-tag"), ("v1.3", "moving-tag"), ("master", "branch"), (None, "unpinned")],
)
def test_classify_ref(ref, kind):
    assert classify_ref(ref) == kind


def test_pyproject_dependency_tables_and_not_other_lists(tmp_path):
    root = _repo(
        tmp_path,
        {"pyproject.toml": f"""
            [project]
            name = "x"
            dependencies = ["requests"]
            [project.optional-dependencies]
            dev = ["py-ci-shared @ {URL}@{SHA}"]
            test = ["py-ci-shared"]
            [dependency-groups]
            lint = ["py_ci_shared @ file:../py-ci-shared"]
            [tool.deptry.per_rule_ignores]
            DEP002 = ["py-ci-shared"]
            [tool.ruff]
            extend = "../py-ci-shared/configs/ruff-base.toml"
            """},
    )
    got = _pins(root)
    assert ("pyproject.toml", "pyproject", "package", SHA, "sha") in got
    assert ("pyproject.toml", "pyproject", "package", None, "unpinned") in got
    assert ("pyproject.toml", "pyproject", "package", None, "sibling") in got
    assert ("pyproject.toml", "pyproject", "ruff-extend", None, "sibling") in got
    assert len(got) == 4, got  # the deptry DEP002 list is not a dependency
    lines = {(p.ref, p.kind): p.line for p in find_pins(root)}
    assert lines[(SHA, "sha")] == 5 and lines[(None, "unpinned")] == 6


def test_env_var_ruff_extend_and_uv_sources(tmp_path):
    root = _repo(
        tmp_path,
        {"pyproject.toml": """
            [tool.ruff]
            extend = "$PY_CI_SHARED_DIR/configs/ruff-base.toml"
            [tool.uv.sources]
            py-ci-shared = { git = "https://github.com/fingoldo/py-ci-shared", branch = "master" }
            """},
    )
    assert _pins(root) == [("pyproject.toml", "pyproject", "package", "master", "branch")]


def test_requirements_lock_and_precommit(tmp_path):
    root = _repo(
        tmp_path,
        {
            "requirements-dev.txt": f"""
            # py-ci-shared @ {URL}@master   (commented out: not a pin)
            numpy
            py-ci-shared @ {URL}@{SHA}
            """,
            "sub/requirements.txt": f"py-ci-shared @ {URL} ; python_version >= '3.9'\n",
            "uv.lock": f"""
            [[package]]
            name = "app"
            dependencies = [{{ name = "py-ci-shared" }}]

            [[package]]
            name = "py-ci-shared"
            source = {{ git = "https://github.com/fingoldo/py-ci-shared.git#{SHA2}" }}
            """,
            ".pre-commit-config.yaml": """
            repos:
              - repo: https://github.com/fingoldo/py-ci-shared
                rev: v1.3.5
                hooks:
                  - id: mypy-full-manual
              - repo: https://github.com/psf/black
                rev: 24.1.0
            """,
        },
    )
    got = {(p.path, p.line, p.ref, p.kind) for p in find_pins(root)}
    assert got == {
        ("requirements-dev.txt", 3, SHA, "sha"),
        ("sub/requirements.txt", 1, None, "unpinned"),
        ("uv.lock", 6, SHA2, "sha"),
        (".pre-commit-config.yaml", 2, "v1.3.5", "release"),
    }


def test_workflow_uses_inputs_installs_clones_and_checkout(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/ci.yml": f"""
            jobs:
              ruff:
                uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@v1
                with:
                  py-ci-shared-ref: v1.16.1
              cov:
                steps:
                  - uses: fingoldo/py-ci-shared/.github/actions/upload-codecov@{SHA2}  # v1.3.4
                  - run: pip install "py-ci-shared @ {URL}@{SHA}"
                  - run: git clone --depth 1 --branch v1 https://github.com/fingoldo/py-ci-shared.git "$RUNNER_TEMP/p"
                  - run: git clone --depth 1 https://github.com/fingoldo/py-ci-shared.git x
                  - run: pip install -e ../py-ci-shared
                  - uses: actions/checkout@v4
                    with:
                      repository: fingoldo/py-ci-shared
                      ref: main
                  # uses: fingoldo/py-ci-shared/.github/workflows/lint-blocking.yml@master
                  - run: echo "${{{{ inputs.py-ci-shared-ref }}}}"
            """},
    )
    got = [(p.line, p.target, p.ref, p.kind) for p in find_pins(root)]
    assert got == [
        (3, "workflow:ruff-blocking.yml", "v1", "moving-tag"),
        (5, "ref-input", "v1.16.1", "release"),
        (8, "action:upload-codecov", SHA2, "sha"),
        (9, "package", SHA, "sha"),
        (10, "config-clone", "v1", "moving-tag"),
        (11, "config-clone", None, "unpinned"),
        (12, "package", None, "sibling"),
        (15, "checkout", "main", "branch"),
    ]


def test_pins_agree_only_when_one_commit(tmp_path):
    same = _repo(
        tmp_path,
        {
            "requirements.txt": f"py-ci-shared @ {URL}@{SHA}\n",
            ".github/workflows/a.yml": f"    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@{SHA[:7]}\n",
        },
        "same",
    )
    rep = scan_repo(same, local_copies=False)
    assert rep.pins_agree and not rep.moving_pins and not rep.failing
    differ = _repo(
        tmp_path,
        {
            "requirements.txt": f"py-ci-shared @ {URL}@{SHA}\n",
            ".github/workflows/a.yml": f"    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@{SHA2}\n",
        },
        "differ",
    )
    rep = scan_repo(differ, local_copies=False)
    assert not rep.pins_agree and rep.failing
    assert [f.rule for f in rep.findings()] == ["pins-disagree"]


def test_resolver_makes_a_tag_and_its_sha_agree(tmp_path):
    pcs = tmp_path / "pcs"
    pcs.mkdir()
    for args in (["init", "-q"], ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"], ["tag", "v1.2.3"]):
        subprocess.run(["git", "-C", str(pcs), *args], check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(pcs), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    root = _repo(
        tmp_path,
        {"requirements.txt": f"py-ci-shared @ {URL}@v1.2.3\n", ".pre-commit-config.yaml": f"- repo: https://github.com/fingoldo/py-ci-shared\n  rev: {head}\n"},
    )
    assert not scan_repo(root, local_copies=False).pins_agree
    rep = scan_repo(root, local_copies=False, resolver=RefResolver(pcs))
    assert rep.pins_agree and rep.distinct_refs == [head[:12]]


def test_moving_pins_fail(tmp_path):
    root = _repo(tmp_path, {".github/workflows/a.yml": '      - run: pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared@master"\n'})
    rep = scan_repo(root, local_copies=False)
    assert rep.pins_agree and rep.failing
    assert [(f.rule, f.line) for f in rep.findings()] == [("moving-pin", 1)]


SKIP_POSITIVE = {
    "importorskip": """
        import pytest
        mod = pytest.importorskip("py_ci_shared.naive_utcnow")
        """,
    "collect-ignore": """
        try:
            import py_ci_shared
        except ImportError:
            collect_ignore = ["test_meta"]
        """,
    "exit-0": """
        import sys
        try:
            from py_ci_shared.arb_checks import find_dead_keys
        except ImportError:
            print("SKIPPED - py-ci-shared is not installed")
            sys.exit(0)
        """,
    "skip": """
        import pytest
        try:
            from py_ci_shared import mypy_gate
        except ModuleNotFoundError:
            pytest.skip("no py_ci_shared", allow_module_level=True)
        """,
    "availability-flag": """
        try:
            from py_ci_shared.dart_scanners import scan_x
            _SHARED = True
        except ImportError:
            _SHARED = False
        if _SHARED:
            SCANS = [scan_x]
        """,
    "swallowed": """
        def pytest_addoption(parser):
            try:
                from py_ci_shared.code_audit_meta import register_refresh_option
            except ImportError:
                return
            register_refresh_option(parser)
        """,
}

SKIP_NEGATIVE = {
    "importorskip": 'import pytest\nnp = pytest.importorskip("numpy")\n',
    "collect-ignore": "try:\n    import numpy\nexcept ImportError:\n    collect_ignore = ['x']\n",
    "exit-1": "import sys\ntry:\n    import py_ci_shared\nexcept ImportError:\n    print('missing')\n    sys.exit(1)\n",
    "return-1": "def main():\n    try:\n        import py_ci_shared\n    except ImportError:\n        return 1\n",
    "raise": "try:\n    import py_ci_shared\nexcept ImportError as exc:\n    raise RuntimeError('install it') from exc\n",
    "fail": "import pytest\ntry:\n    import py_ci_shared\nexcept ImportError:\n    pytest.fail('install py-ci-shared')\n",
    "other-exception": "try:\n    import py_ci_shared\nexcept KeyError:\n    pass\n",
    "flag-checked-loudly": """
        try:
            from py_ci_shared.dart_scanners import scan_x
            SHARED_AVAILABLE = True
        except ImportError:
            SHARED_AVAILABLE = False

        def main():
            if not SHARED_AVAILABLE:
                return 1
            return 0
        """,
}


@pytest.mark.parametrize("rule", sorted(SKIP_POSITIVE))
def test_each_silent_skip_shape_is_found(tmp_path, rule):
    root = _repo(tmp_path, {"tests/conftest.py": SKIP_POSITIVE[rule]})
    assert [f.rule for f in find_silent_skips(root)] == [rule]


@pytest.mark.parametrize("name", sorted(SKIP_NEGATIVE))
def test_loud_fallbacks_are_not_silent_skips(tmp_path, name):
    root = _repo(tmp_path, {"tool/check.py": SKIP_NEGATIVE[name]})
    assert find_silent_skips(root) == []


def test_find_spec_guard_with_collect_ignore(tmp_path):
    src = "import importlib.util\nif importlib.util.find_spec('py_ci_shared') is None:\n    collect_ignore = ['test_meta']\n"
    root = _repo(tmp_path, {"tests/conftest.py": src})
    assert [(f.rule, f.line) for f in find_silent_skips(root)] == [("collect-ignore", 3)]


def test_module_usage_imports_python_m_yaml_config_and_audits_excluded(tmp_path):
    root = _repo(
        tmp_path,
        {
            "tests/test_a.py": "from py_ci_shared import naive_utcnow, function_length as fl\nimport py_ci_shared.identity_comparisons\n",
            ".pre-commit-config.yaml": "      entry: python -m py_ci_shared.mypy_gate --min-files 10\n",
            ".github/workflows/ci.yml": "      - run: python -m py_ci_shared.ci_workflow_gate .github\n      - run: py-ci-shared run tool_versions\n",
            "pyproject.toml": """
            [tool.py_ci_shared]
            enable = ["private_imports"]
            [tool.py_ci_shared.gates.rounds]
            module = "audit_round_format"
            [tool.py_ci_shared.gates.off]
            module = "naive_utcnow_never"
            enabled = false
            """,
            "audits/x.py": "import py_ci_shared.baseline_trend\n",
            "README.md": "python -m py_ci_shared.vulture_warn\n",
            "scripts/x.py": "import py_ci_shared.no_such_module\n",
        },
    )
    used, unknown = find_module_usage(root)
    assert sorted(used) == [
        "audit_round_format",
        "ci_workflow_gate",
        "function_length",
        "identity_comparisons",
        "mypy_gate",
        "naive_utcnow",
        "private_imports",
        "tool_versions",
    ]
    assert used["mypy_gate"] == [".pre-commit-config.yaml"]
    assert unknown == {"no_such_module": ["scripts/x.py"]}


def test_markdown_shape_and_exit_codes(tmp_path, capsys):
    good = _repo(tmp_path, {"requirements.txt": f"py-ci-shared @ {URL}@{SHA}\n", "tests/test_x.py": "import py_ci_shared.naive_utcnow\n"}, "good")
    bad = _repo(
        tmp_path, {"requirements.txt": "py-ci-shared\n", "tests/test_y.py": "import pytest\npytest.importorskip('py_ci_shared.function_length')\n"}, "bad"
    )
    reports = [scan_repo(good, local_copies=False), scan_repo(bad, local_copies=False)]
    md = render_markdown(reports, modules=["naive_utcnow", "function_length", "mypy_gate"])
    for heading in ("## Summary", "## Pins", "## Modules", "## Findings"):
        assert heading in md
    assert "| module | good | bad | n |" in md
    assert "| `naive_utcnow` | X | . | 1 |" in md
    assert "| `function_length` | . | X | 1 |" in md
    assert "| `mypy_gate` | . | . | 0 |" in md
    assert "| **modules used** | 1 | 1 | |" in md
    assert "- bad: `tests/test_y.py:2` importorskip:" in md
    assert "- bad: `requirements.txt:1` moving-pin:" in md
    out = tmp_path / "out.md"
    assert main([str(good), "--no-local-copies"]) == 0
    assert main([str(good), str(bad), "--no-local-copies", "--output", str(out)]) == 1
    assert main([str(good), str(bad), "--no-local-copies", "--advisory"]) == 0
    assert out.read_text(encoding="utf-8").startswith("# py-ci-shared adoption matrix")
    assert main([str(tmp_path / "missing")]) == 2
    capsys.readouterr()


def test_repos_file_and_consumer_is_not_written(tmp_path):
    root = _repo(tmp_path, {"requirements.txt": f"py-ci-shared @ {URL}@{SHA}\n"})
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    listing = tmp_path / "consumers.toml"
    listing.write_text('repos = ["repo"]\n[[repo]]\npath = "repo"\nname = "alias"\n', encoding="utf-8")
    assert load_repo_list(listing) == [("repo", root.resolve()), ("alias", root.resolve())]
    assert main(["--repos-file", str(listing), "--advisory"]) == 0
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before
