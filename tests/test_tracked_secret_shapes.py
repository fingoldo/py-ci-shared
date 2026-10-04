"""Tests for py_ci_shared.tracked_secret_shapes, over real git repositories built in tmp_path.

Every token here is a fake built at run time from pieces, so no token-shaped text sits in this repository's files, and every
assertion about output checks that the matched text is NOT in it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from py_ci_shared import tracked_secret_shapes as tss
from py_ci_shared._core import CorpusError, EmptyScanError
from py_ci_shared._core.config import ConfigError

PREFIX = "oauth2v2_"
FAKE = PREFIX + "int_" + "0f1e2d3c4b5a6978" * 2  # a fake of the incident's shape, obviously not a credential  # pragma: allowlist secret
HEX_ONLY = PREFIX + "0f1e2d3c4b5a6978" * 2  # pragma: allowlist secret
SHAPES = {"upwork_bearer": PREFIX + "(int_)?[0-9a-f]{20,}", "internal": PREFIX + "int_[0-9a-f]{32}"}
ONE = {"upwork_bearer": PREFIX + "(int_)?[0-9a-f]{20,}"}
WIDE_CONFIG = ("[tool.py_ci_shared.secret_shapes]\nu = '" + PREFIX + "[0-9a-z_]{20,}'\n").encode()


def _git(repo: Path, *args: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _repo(tmp_path: Path, files: "dict[str, bytes]") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    for rel, data in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    _git(repo, "add", "-A")
    return repo


def _at(found):
    return [(f.path, f.line) for f in found]


def test_the_seeded_token_is_reported_at_path_line_with_shape_and_length_only(tmp_path):
    repo = _repo(tmp_path, {"scripts/a.py": f'x = 1\nTOKEN = "{HEX_ONLY}"\n'.encode()})
    found = tss.find_tracked_secret_shapes(repo, shapes=ONE)
    assert [(f.path, f.line, f.rule) for f in found] == [("scripts/a.py", 2, tss.RULE)]
    assert f"upwork_bearer shape matched ({len(HEX_ONLY)} chars, column 10)" in found[0].message
    assert HEX_ONLY not in found[0].message and PREFIX not in found[0].message


def test_the_assertion_text_and_the_cli_output_never_hold_the_matched_text(tmp_path, capsys):
    repo = _repo(tmp_path, {"a.js": f"const t = '{FAKE}';\n".encode(), "pyproject.toml": WIDE_CONFIG})
    with pytest.raises(AssertionError) as exc:
        tss.assert_no_tracked_secret_shapes(repo, shapes=SHAPES)
    assert "a.js:1" in str(exc.value) and "history is not rewritten" in str(exc.value)
    assert FAKE not in str(exc.value) and "0f1e2d3c" not in str(exc.value)
    assert tss.main(["--root", str(repo)]) == 1
    out = capsys.readouterr()
    assert "a.js:1" in out.out and "0f1e2d3c" not in out.out + out.err and PREFIX not in out.out + out.err


def test_negative_control_the_same_line_with_the_allow_marker_is_not_reported(tmp_path):
    repo = _repo(tmp_path, {"t.py": f'FAKE = "{FAKE}"  # {tss.ALLOW_MARKER}\n'.encode()})
    assert tss.find_tracked_secret_shapes(repo, shapes=ONE) == []


def test_the_marker_exempts_only_its_own_line(tmp_path):
    body = f"# {tss.ALLOW_MARKER}\nA = '{FAKE}'\nB = '{FAKE}'  # {tss.ALLOW_MARKER}\nC = '{FAKE}'\n"
    repo = _repo(tmp_path, {"t.py": body.encode()})
    assert _at(tss.find_tracked_secret_shapes(repo, shapes=ONE)) == [("t.py", 2), ("t.py", 4)]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = tss.find_tracked_secret_shapes(_repo(tmp_path / "p", {"a.txt": f"{HEX_ONLY}\n".encode()}), shapes=ONE)
    bom = tss.find_tracked_secret_shapes(_repo(tmp_path / "b", {"a.txt": b"\xef\xbb\xbf" + f"{HEX_ONLY}\n".encode()}), shapes=ONE)
    assert plain and bom == plain


def test_two_matches_on_a_line_and_two_shapes_are_each_a_finding(tmp_path):
    repo = _repo(tmp_path, {"a.txt": f"{FAKE} {FAKE}\n".encode()})
    found = tss.find_tracked_secret_shapes(repo, shapes=SHAPES)
    assert len(found) == 4 and {f.message.split()[0] for f in found} == {"upwork_bearer", "internal"}
    assert len({f.key for f in found}) == 4  # a baseline-style multiset would collapse equal keys, so they are distinct


def test_min_length_is_a_boundary_on_the_match_length(tmp_path):
    repo = _repo(tmp_path, {"a.txt": f"{HEX_ONLY}\n".encode()})
    n = len(HEX_ONLY)
    at = {"s": {"pattern": PREFIX + "[0-9a-f]+", "min_length": n}}
    above = {"s": {"pattern": PREFIX + "[0-9a-f]+", "min_length": n + 1}}
    assert len(tss.find_tracked_secret_shapes(repo, shapes=at)) == 1
    assert tss.find_tracked_secret_shapes(repo, shapes=above) == []


def test_the_note_is_carried_into_the_finding(tmp_path):
    repo = _repo(tmp_path, {"a.txt": f"{HEX_ONLY}\n".encode()})
    found = tss.find_tracked_secret_shapes(repo, shapes={"s": {"pattern": PREFIX + "[0-9a-f]+", "note": "rotate it in the vault"}})
    assert "rotate it in the vault" in found[0].message


def test_binary_and_oversized_blobs_are_skipped_and_counted(tmp_path):
    big = f"{FAKE}\n".encode() + b"x" * 100
    repo = _repo(tmp_path, {"img.bin": b"\x00" + FAKE.encode(), "big.txt": big, "ok.txt": b"fine\n", "hit.txt": f"{FAKE}\n".encode()})
    report = tss.scan_tracked_secret_shapes(repo, shapes=ONE, max_bytes=len(big) - 1)
    assert (report.skipped_binary, report.skipped_large, report.scanned) == (1, 1, 2)
    assert _at(report.findings) == [("hit.txt", 1)]
    assert "1 binary, 1 over the size cap" in report.summary()
    assert tss.scan_tracked_secret_shapes(repo, shapes=ONE, max_bytes=len(big)).skipped_large == 0  # exactly at the cap is read


def test_the_index_is_read_not_the_work_tree_and_untracked_files_are_ignored(tmp_path):
    repo = _repo(tmp_path, {"a.txt": f"{FAKE}\n".encode(), "ok.txt": b"fine\n"})
    (repo / "a.txt").write_text("cleaned after staging\n")  # the commit would still contain the token
    (repo / "untracked.txt").write_text(f"{FAKE}\n")
    assert _at(tss.find_tracked_secret_shapes(repo, shapes=ONE)) == [("a.txt", 1)]


def test_exclude_paths_take_globs_and_directory_prefixes_and_are_counted(tmp_path):
    files = {f"{d}/a.txt": f"{FAKE}\n".encode() for d in ("vendor", "keep", "docs")}
    files["keep/b.fixture"] = f"{FAKE}\n".encode()
    repo = _repo(tmp_path, files)
    report = tss.scan_tracked_secret_shapes(repo, shapes=ONE, exclude_paths=["vendor/", "docs", "*.fixture"])
    assert _at(report.findings) == [("keep/a.txt", 1)] and report.skipped_excluded == 3
    assert tss.scan_tracked_secret_shapes(repo, shapes=ONE, exclude_paths=["vend"]).skipped_excluded == 0  # a prefix is a directory


def test_shapes_come_from_the_pyproject_table_and_table_form_works(tmp_path):
    cfg = f'[tool.py_ci_shared.secret_shapes]\nplain = "{PREFIX}[0-9a-f]{{20,}}"\nrich = {{ pattern = "{PREFIX}int_[0-9a-f]+", min_length = 5 }}\n'
    repo = _repo(tmp_path, {"pyproject.toml": cfg.encode(), "a.txt": f"{FAKE}\n".encode()})
    assert [f.message.split()[0] for f in tss.scan_tracked_secret_shapes(repo).findings] == ["rich"]  # FAKE has "int_" before the hex, so plain misses
    explicit = tmp_path / "other.toml"
    explicit.write_text(cfg)
    assert [f.message.split()[0] for f in tss.scan_tracked_secret_shapes(repo, config_path=explicit).findings] == ["rich"]


def test_the_builtin_set_is_empty_so_no_shapes_is_a_config_error_not_a_pass(tmp_path):
    assert tss.BUILTIN_SHAPES == {}
    repo = _repo(tmp_path, {"a.txt": f"{FAKE}\n".encode()})
    with pytest.raises(ConfigError, match="no secret shapes"):
        tss.find_tracked_secret_shapes(repo, shapes={})
    with pytest.raises(ConfigError, match="cannot read"):
        tss.scan_tracked_secret_shapes(repo)  # no pyproject.toml tracked or present
    (repo / "pyproject.toml").write_text("[tool.other]\nx = 1\n")
    with pytest.raises(ConfigError, match="secret_shapes"):
        tss.scan_tracked_secret_shapes(repo)
    (repo / "pyproject.toml").write_text("[tool.py_ci_shared.secret_shapes\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        tss.scan_tracked_secret_shapes(repo)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("([", "does not compile"),
        ("a*", "empty string"),
        ({"pattern": "x", "min_len": 3}, "unknown key"),
        ({"pattern": "x", "min_length": -1}, "non-negative"),
        ({"pattern": "x", "min_length": True}, "non-negative"),
        ({"min_length": 3}, "'pattern' string"),
        (7, "'pattern' string"),
        ("", "'pattern' string"),
    ],
)
def test_a_bad_shape_is_a_config_error_naming_it(tmp_path, spec, message):
    repo = _repo(tmp_path, {"a.txt": b"x\n"})
    with pytest.raises(ConfigError, match=message) as exc:
        tss.find_tracked_secret_shapes(repo, shapes={"mine": spec})
    assert "'mine'" in str(exc.value)


def test_an_empty_marker_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="allow_marker"):
        tss.find_tracked_secret_shapes(_repo(tmp_path, {"a.txt": b"x\n"}), shapes=ONE, allow_marker="")


def test_the_floor_and_non_git_and_missing_roots_are_errors(tmp_path):
    with pytest.raises(EmptyScanError, match="at least 1"):
        tss.find_tracked_secret_shapes(_repo(tmp_path / "e", {}), shapes=ONE)
    only_binary = _repo(tmp_path / "b", {"x.bin": b"\x00\x01"})
    with pytest.raises(EmptyScanError, match="1 binary"):
        tss.find_tracked_secret_shapes(only_binary, shapes=ONE)
    two = _repo(tmp_path / "t", {"a.txt": b"x\n", "b.txt": b"y\n"})
    assert tss.find_tracked_secret_shapes(two, shapes=ONE, min_files=2) == []
    with pytest.raises(EmptyScanError):
        tss.find_tracked_secret_shapes(two, shapes=ONE, min_files=3)
    (tmp_path / "plain").mkdir()
    with pytest.raises(CorpusError):
        tss.find_tracked_secret_shapes(tmp_path / "plain", shapes=ONE)
    with pytest.raises(CorpusError):
        tss.find_tracked_secret_shapes(tmp_path / "missing", shapes=ONE)


def test_a_file_that_cannot_be_read_is_an_error_unless_allowed(tmp_path):
    repo = _repo(tmp_path, {"a.txt": b"x\n"})
    with pytest.raises(CorpusError, match=r"gone.txt"):
        tss.scan_tracked_secret_shapes(repo, shapes=ONE, files=["a.txt", "gone.txt"], min_files=0)
    report = tss.scan_tracked_secret_shapes(repo, shapes=ONE, files=["a.txt", "gone.txt"], min_files=0, allow_unparsed=True)
    assert report.unreadable == ["gone.txt"] and report.scanned == 1


def test_cli_files_mode_checks_only_the_named_files_and_exit_codes(tmp_path, capsys):
    repo = _repo(tmp_path, {"pyproject.toml": WIDE_CONFIG, "bad.py": f"{FAKE}\n".encode(), "good.py": b"x = 1\n"})
    assert tss.main(["--root", str(repo), "good.py"]) == 0
    assert tss.main(["--root", str(repo), "bad.py", "good.py"]) == 1
    out = capsys.readouterr().out
    assert out.count("bad.py:1") == 1 and "good.py" not in out
    assert tss.main(["--root", str(repo), "--exclude", "bad.py", "bad.py"]) == 0  # nothing left to scan is fine in files mode
    assert tss.main(["--root", str(repo), "--config", "missing.toml", "good.py"]) == 2
    assert "ConfigError" in capsys.readouterr().err


def test_cli_whole_index_mode_enforces_the_floor_and_a_clean_run_is_zero(tmp_path, capsys):
    repo = _repo(tmp_path, {"pyproject.toml": WIDE_CONFIG, "good.py": b"x = 1\n"})
    assert tss.main(["--root", str(repo)]) == 0
    assert tss.main(["--root", str(repo), "--min-files", "5"]) == 2
    assert "EmptyScanError" in capsys.readouterr().err


def test_the_incident_shape_matches_a_real_length_token_and_not_a_short_or_prefix_only_string(tmp_path):
    body = f"{PREFIX}\n{PREFIX}0f1e2d\n{PREFIX}{'0f' * 9}\n{HEX_ONLY}\n"
    repo = _repo(tmp_path, {"a.txt": body.encode()})
    assert _at(tss.find_tracked_secret_shapes(repo, shapes=ONE)) == [("a.txt", 4)]  # 18 hex digits is below the 20 floor
