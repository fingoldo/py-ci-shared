"""``bump_pin`` rewrites only the py-ci-shared pin lines of one checkout, dry run by default (audit 47-T8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared import bump_pin

WORKFLOW = (
    "jobs:\n"
    "  lint:\n"
    "    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@v1.23.9  # v1.23.9\n"
    "    with:\n"
    "      py-ci-shared-ref: v1.23.9\n"
    "    steps:\n"
    "      - uses: actions/checkout@v7  # v7\n"
)


def _repo(tmp_path: Path, files: dict) -> Path:
    root = tmp_path / "consumer"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def test_rewrite_moves_the_uses_pin_the_input_and_the_comment_and_nothing_else():
    out = bump_pin.rewrite(WORKFLOW, "v1.23.11")
    assert "ruff-blocking.yml@v1.23.11  # v1.23.11" in out
    assert "py-ci-shared-ref: v1.23.11" in out
    assert "actions/checkout@v7  # v7" in out


def test_a_sha_ref_replaces_the_version_comment_with_the_sha():
    out = bump_pin.rewrite(WORKFLOW, "5264e5d0a1b2c3d4e5f60718293a4b5c6d7e8f90")
    assert "ruff-blocking.yml@5264e5d0a1b2c3d4e5f60718293a4b5c6d7e8f90  # 5264e5d0a1b2c3d4e5f60718293a4b5c6d7e8f90" in out
    assert "# v1.23.9" not in out


def test_a_git_url_pin_in_a_requirements_file_is_moved_and_a_dot_git_suffix_is_kept():
    text = "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@v1.23.9\n"
    assert bump_pin.rewrite(text, "v1.23.11") == "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@v1.23.11\n"


def test_crlf_line_endings_survive_a_rewrite():
    text = "    uses: fingoldo/py-ci-shared/.github/workflows/x.yml@v1.23.9\r\n"
    assert bump_pin.rewrite(text, "v1.23.11") == "    uses: fingoldo/py-ci-shared/.github/workflows/x.yml@v1.23.11\r\n"


def test_a_dry_run_prints_the_diff_and_writes_nothing(tmp_path, capsys):
    root = _repo(tmp_path, {".github/workflows/ci.yml": WORKFLOW})
    before = (root / ".github/workflows/ci.yml").read_bytes()
    assert bump_pin.main(["v1.23.11", str(root)]) == 0
    assert (root / ".github/workflows/ci.yml").read_bytes() == before
    shown = capsys.readouterr().out
    assert "-    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@v1.23.9" in shown
    assert "+    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@v1.23.11" in shown


def test_write_rewrites_the_pin_files_of_the_given_checkout(tmp_path):
    root = _repo(
        tmp_path,
        {
            ".github/workflows/ci.yml": WORKFLOW,
            ".pre-commit-config.yaml": "repos:\n  - repo: https://github.com/fingoldo/py-ci-shared\n    rev: v1.23.9  # v1.23.9\n",
            "pyproject.toml": "[project]\nname = 'x'\n",
            "src/app.py": "PIN = 'fingoldo/py-ci-shared@v1.23.9'\n",
        },
    )
    assert bump_pin.main(["v1.23.11", str(root), "--write"]) == 0
    assert "@v1.23.11" in (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    # only pin-bearing files are touched: a Python source line that mentions the pin is left alone
    assert "fingoldo/py-ci-shared@v1.23.9" in (root / "src/app.py").read_text(encoding="utf-8")


def test_nothing_outside_the_given_checkout_is_written(tmp_path):
    outside = tmp_path / "other" / ".github" / "workflows" / "ci.yml"
    outside.parent.mkdir(parents=True)
    outside.write_text(WORKFLOW, encoding="utf-8")
    root = _repo(tmp_path, {".github/workflows/ci.yml": WORKFLOW})
    assert bump_pin.main(["v1.23.11", str(root), "--write"]) == 0
    assert outside.read_text(encoding="utf-8") == WORKFLOW


def test_a_symlink_out_of_the_checkout_is_not_followed(tmp_path):
    root = _repo(tmp_path, {"pyproject.toml": "[project]\nname = 'x'\n"})
    outside = tmp_path / "outside.toml"
    outside.write_text("dep = 'fingoldo/py-ci-shared@v1.23.9'\n", encoding="utf-8")
    link = root / "requirements-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need a privilege on this platform")
    assert bump_pin.main(["v1.23.11", str(root), "--write"]) == 0
    assert outside.read_text(encoding="utf-8") == "dep = 'fingoldo/py-ci-shared@v1.23.9'\n"


@pytest.mark.parametrize("ref", ["", "v1.23.11; rm -rf /", "$(id)", "a b"])
def test_a_ref_with_shell_or_space_characters_is_refused(tmp_path, ref):
    root = _repo(tmp_path, {".github/workflows/ci.yml": WORKFLOW})
    assert bump_pin.main([ref, str(root), "--write"]) == 2
    assert (root / ".github/workflows/ci.yml").read_text(encoding="utf-8") == WORKFLOW


def test_a_missing_checkout_is_a_usage_error(tmp_path):
    assert bump_pin.main(["v1.23.11", str(tmp_path / "nope")]) == 2
