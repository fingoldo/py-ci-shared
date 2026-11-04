"""34-F1, 34-F2, 34-F12: adoption is read from enable lists, pre-commit hook ids and listed gate names, and ranked per repo."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.adoption_matrix import ranked_gaps, render_markdown, scan_repo

MODULES = ["repo_hygiene", "import_cycles", "loc_budget", "stale_comment_age"]


def _repo(tmp_path: Path, name: str, files: "dict[str, str]") -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return root


def _python(files: "dict[str, str]") -> "dict[str, str]":
    """*files* plus enough plain modules for the repo to count as a Python project."""
    return {**files, **{f"pkg/m{i}.py": "x = 1\n" for i in range(25)}}


def _scan(root: Path):
    return scan_repo(root, modules=MODULES, local_copies=False)


def test_enable_list_precommit_hook_id_and_parametrized_list_all_count(tmp_path):
    enabled = _repo(tmp_path, "enabled", {"pyproject.toml": '[tool.py_ci_shared]\nenable = ["repo_hygiene"]\n'})
    hooked = _repo(
        tmp_path,
        "hooked",
        {
            "pyproject.toml": "[project]\nname='x'\n",
            ".pre-commit-config.yaml": "repos:\n  - repo: https://github.com/o/py-ci-shared\n    rev: v1.2.3\n    hooks:\n      - id: import-cycles\n",
        },
    )
    listed = _repo(
        tmp_path,
        "listed",
        {"pyproject.toml": "[project]\nname='x'\n", "tests/test_meta.py": 'import pytest\nGATES = ["loc_budget", "stale_comment_age"]  # py_ci_shared gates\n'},
    )
    assert "repo_hygiene" in _scan(enabled).modules
    assert "import_cycles" in _scan(hooked).modules, "a pre-commit hook id naming a module counts"
    assert {"loc_budget", "stale_comment_age"} <= set(_scan(listed).modules), "a list of gate names in a .py file counts"


def test_one_lone_string_or_a_foreign_hook_id_is_not_adoption(tmp_path):
    root = _repo(
        tmp_path,
        "lone",
        {
            "pyproject.toml": "[project]\nname='x'\n",
            "tests/test_a.py": 'NAME = ["repo_hygiene"]  # py_ci_shared mentioned here\n',
            ".pre-commit-config.yaml": "repos:\n  - repo: https://example.com/other\n    rev: v1\n    hooks:\n      - id: import-cycles\n",
        },
    )
    assert _scan(root).modules == {}


def test_ranked_gaps_lists_missing_repos_and_unadopted_modules(tmp_path):
    a = _repo(tmp_path, "a", _python({"pyproject.toml": '[tool.py_ci_shared]\nenable = ["repo_hygiene", "import_cycles"]\n'}))
    b = _repo(tmp_path, "b", _python({"pyproject.toml": '[tool.py_ci_shared]\nenable = ["repo_hygiene"]\n'}))
    docs = _repo(tmp_path, "docs", {"README.md": "no python here\n", "pyproject.toml": "[project]\nname = 'docs'\n", "tools/one.py": "x = 1\n"})
    reports = [_scan(a), _scan(b), _scan(docs)]
    gaps, unadopted = ranked_gaps(reports, MODULES)
    assert [(g.module, g.adopters, g.missing) for g in gaps] == [("import_cycles", ("a",), ("b",))]
    assert unadopted == ["loc_budget", "stale_comment_age"]
    text = render_markdown(reports, modules=MODULES)
    assert "## Ranked gaps" in text and "| `import_cycles` | 1/2 | b |" in text
    assert "## Enabled nowhere (2)" in text
    assert "## Python consumers without a `[tool.py_ci_shared]` gate runner" in text and "None." in text.split("gate runner")[1].split("##")[0]
    assert "## Reusable workflows a Python consumer does not call" in text
