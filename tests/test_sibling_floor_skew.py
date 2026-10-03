"""Tests for py_ci_shared.sibling_floor_skew. Real files on disk; versions come from tags, a real local git clone, or
an injected resolver (never the network)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from py_ci_shared import sibling_floor_skew as sfs
from py_ci_shared._core import Baseline, EmptyScanError, UnparsedFilesError

BOM = b"\xef\xbb\xbf"
SHA = "8ffd7e6cdcfd104e428253bf67ae173798943cde"


def _corpus(root: Path, files: "dict[str, str]", *, bom: bool = False) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    return root


def _pyproject(floor: str = "pyutilz>=1.1") -> str:
    return f'[project]\nname = "x"\nversion = "0.1"\ndependencies = [\n    "{floor}",\n]\n'


# llm_bench 57e79a7, cut down: pyproject says >=1.1 and ci.yml still checks out pyutilz 1.0.0.
SEED_CI = f"""jobs:
  test:
    steps:
      - run: git clone https://github.com/fingoldo/pyutilz.git pyutilz
      - run: git -C pyutilz checkout {SHA}
"""


def _versions(table: "dict[str, str]"):
    return lambda sibling, owner, ref: table.get(ref)


def test_the_seed_is_reported_with_the_floor_and_the_resolved_version(tmp_path):
    root = _corpus(tmp_path, {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": SEED_CI})
    found = sfs.find_sibling_floor_skew(root, resolver=_versions({SHA: "1.0.0"}))
    assert [(f.path, f.line, f.rule) for f in found] == [(".github/workflows/ci.yml", 5, sfs.RULE)]
    assert f"pyutilz@{SHA}" in found[0].message and "= 1.0.0" in found[0].message and "pyutilz>=1.1 (pyproject.toml:5)" in found[0].message


def test_negative_control_a_revision_at_the_floor_passes(tmp_path):
    root = _corpus(tmp_path, {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": SEED_CI})
    assert sfs.find_sibling_floor_skew(root, resolver=_versions({SHA: "1.1.0"})) == []


def test_an_unresolvable_ref_is_a_finding_not_a_pass(tmp_path):
    root = _corpus(tmp_path, {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": SEED_CI})
    found = sfs.find_sibling_floor_skew(root, resolver=_versions({}))
    assert [f.rule for f in found] == [sfs.RULE_UNRESOLVED]
    assert "cannot be resolved" in found[0].message


@pytest.mark.parametrize(
    ("line", "shape"),
    [
        ("      - run: pip install git+https://github.com/fingoldo/pyutilz.git@v1.0.0", "git-url"),
        ("      - uses: fingoldo/pyutilz/.github/actions/setup@v1.0.0", "uses"),
        ("      - run: git clone --branch v1.0.0 https://github.com/fingoldo/pyutilz.git", "git-clone"),
        ("      - run: git -C ../pyutilz checkout --quiet v1.0.0", "git-checkout"),
    ],
)
def test_every_install_shape_resolves_a_release_tag_without_git(tmp_path, line, shape):
    root = _corpus(tmp_path, {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n" + line + "\n"})
    sites = sfs.find_install_sites(root)
    assert [(s.sibling, s.ref, s.shape) for s in sites] == [("pyutilz", "v1.0.0", shape)]
    found = sfs.find_sibling_floor_skew(root, network=False)
    assert [f.rule for f in found] == [sfs.RULE] and "= 1.0.0" in found[0].message


def test_actions_checkout_with_repository_and_ref(tmp_path):
    wf = "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n        with:\n          repository: fingoldo/pyutilz\n          ref: v1.0.0\n"
    root = _corpus(tmp_path, {"pyproject.toml": _pyproject(), ".github/workflows/ci.yaml": wf})
    assert [(s.ref, s.shape, s.line) for s in sfs.find_install_sites(root)] == [("v1.0.0", "actions-checkout", 6)]


def test_uv_sources_and_a_direct_reference_are_install_sites(tmp_path):
    text = (
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["pyutilz>=1.1", "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@v1.17.0"]\n'
        '[project.optional-dependencies]\nci = ["py_ci_shared>=1.18"]\n'
        '[tool.uv.sources]\npyutilz = { git = "https://github.com/fingoldo/pyutilz", rev = "v1.0.5" }\n'
    )
    root = _corpus(tmp_path, {"pyproject.toml": text})
    found = sfs.find_sibling_floor_skew(root, network=False)
    assert sorted((f.rule, f.message.split(" ", 2)[1]) for f in found) == [(sfs.RULE, "py-ci-shared@v1.17.0"), (sfs.RULE, "pyutilz@v1.0.5")]


def test_expression_refs_and_comments_are_not_sites(tmp_path):
    wf = "jobs:\n  t:\n    steps:\n      # - run: git -C pyutilz checkout v0.1.0\n      - run: git -C pyutilz checkout ${{ inputs.ref }}\n"
    root = _corpus(tmp_path, {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": wf})
    assert sfs.find_install_sites(root) == []


def test_floors_come_from_every_table_and_the_highest_wins(tmp_path):
    text = (
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["pyutilz>=1.0; python_version >= \'3.9\'"]\n'
        '[project.optional-dependencies]\nfull = ["pyutilz[llm]~=1.2"]\n[dependency-groups]\ndev = ["pyutilz==1.3.1", {include-group = "full"}]\n'
    )
    root = _corpus(tmp_path, {"pyproject.toml": text, ".github/workflows/ci.yml": "steps:\n  - run: git -C pyutilz checkout v1.2.9\n"})
    assert sorted(f.version for f in sfs.find_floors(root / "pyproject.toml")) == ["1.0", "1.2", "1.3.1"]
    found = sfs.find_sibling_floor_skew(root, network=False)
    assert len(found) == 1 and "pyutilz>=1.3.1" in found[0].message


def test_no_floor_means_nothing_to_check(tmp_path):
    root = _corpus(tmp_path, {"pyproject.toml": _pyproject("requests>=2"), ".github/workflows/ci.yml": SEED_CI})
    assert sfs.find_sibling_floor_skew(root, resolver=_versions({})) == []


def test_a_bom_prefixed_corpus_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": SEED_CI})
    bom = _corpus(tmp_path / "bom", {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": SEED_CI}, bom=True)
    resolver = _versions({SHA: "1.0.0"})
    assert sfs.find_sibling_floor_skew(bom, resolver=resolver) == sfs.find_sibling_floor_skew(plain, resolver=resolver)


def test_an_unparsable_pyproject_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"pyproject.toml": "[project\nname = ", ".github/workflows/ci.yml": SEED_CI})
    with pytest.raises(UnparsedFilesError, match=r"pyproject\.toml"):
        sfs.find_sibling_floor_skew(root, resolver=_versions({}))
    assert sfs.find_sibling_floor_skew(root, resolver=_versions({}), allow_unparsed=True) == []


def test_no_pyproject_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        sfs.find_sibling_floor_skew(tmp_path)


def test_version_at_reads_a_local_clone(tmp_path):
    clone = tmp_path / "pyutilz"
    clone.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    (clone / "pyproject.toml").write_text('[project]\nname = "pyutilz"\nversion = "1.0.0"\n', encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "v"]):
        subprocess.run(["git", "-C", str(clone), *args], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    assert sfs.version_at("pyutilz", "fingoldo", sha, resolve_in={"pyutilz": clone}, network=False) == "1.0.0"
    assert sfs.version_at("py_utilz", "fingoldo", "v2.0.1", network=False) == "2.0.1"
    assert sfs.version_at("pyutilz", "fingoldo", "deadbeef", resolve_in={"pyutilz": clone}, network=False) is None


def test_assert_and_baseline(tmp_path):
    root = _corpus(tmp_path / "repo", {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": SEED_CI})
    old, new = _versions({SHA: "1.0.0"}), _versions({SHA: "1.4.0"})
    with pytest.raises(AssertionError, match="below the pyproject floor"):
        sfs.assert_sibling_floor_skew(root, resolver=old)
    sfs.assert_sibling_floor_skew(root, resolver=new)
    baseline = tmp_path / "baseline.json"
    Baseline(baseline).save(Baseline.count(sfs.find_sibling_floor_skew(root, resolver=old)))
    sfs.assert_sibling_floor_skew(root, resolver=old, baseline_path=baseline)  # accepted
    with pytest.raises(pytest.fail.Exception, match="no longer found"):
        sfs.assert_sibling_floor_skew(root, resolver=new, baseline_path=baseline)  # fixed: the entry is stale


def test_cli_exit_codes(tmp_path, capsys):
    root = _corpus(tmp_path, {"pyproject.toml": _pyproject(), ".github/workflows/ci.yml": "steps:\n  - run: git -C pyutilz checkout v1.0.0\n"})
    assert sfs.main([str(root), "--no-network"]) == 1
    assert "[sibling-floor-skew]" in capsys.readouterr().out
    assert sfs.main([str(tmp_path / "missing"), "--no-network"]) == 2
    assert sfs.main([str(root), "--resolve-in", "nopath"]) == 2
