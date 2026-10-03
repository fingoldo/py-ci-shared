"""`_core.git`: the one git runner (UTF-8 output, a timeout, hook-safe environment) and the meta-tests that keep it one."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import git as core_git
from py_ci_shared._core.git import REPO_LOCATION_VARS, TIMEOUT_ENV_VAR, GitError, git_env, git_output, git_text, run_git

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "py_ci_shared"


def _init(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "seed"], check=True)
    return root


@needs_git
def test_output_is_bytes_and_git_text_decodes_utf8_whatever_the_locale(tmp_path):
    repo = _init(tmp_path / "r")
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@e", "-c", "user.name=Ёжиков", "commit", "-q", "--allow-empty", "-m", "数据"], check=True)
    proc = run_git(repo, "log", "-1", "--format=%an %s")
    assert isinstance(proc.stdout, bytes)
    assert git_text(proc.stdout).strip() == "Ёжиков 数据"


@needs_git
def test_a_hooks_git_dir_does_not_redirect_the_call(tmp_path, monkeypatch):
    """Inside a pre-commit hook GIT_DIR/GIT_INDEX_FILE name the committing repository and override -C."""
    mine, other = _init(tmp_path / "mine"), _init(tmp_path / "other")
    subprocess.run(["git", "-C", str(other), "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "2"], check=True)
    want = git_output(mine, "rev-parse", "HEAD").strip()
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    assert git_output(mine, "rev-parse", "HEAD").strip() == want
    # The opt-out keeps them, for a tool that asks about the repository the user is in.
    kept = git_text(run_git(mine, "rev-parse", "HEAD", inherit_location=True).stdout).strip()
    assert kept != want


def test_git_env_drops_location_variables_and_keeps_configuration():
    base = {"GIT_DIR": "x", "GIT_INDEX_FILE": "y", "GIT_WORK_TREE": "z", "GIT_CONFIG_COUNT": "1", "GIT_TERMINAL_PROMPT": "0", "PATH": "p"}
    env = git_env({"GIT_CONFIG_KEY_0": "a.b"}, base=base)
    assert env == {"GIT_CONFIG_COUNT": "1", "GIT_TERMINAL_PROMPT": "0", "PATH": "p", "GIT_CONFIG_KEY_0": "a.b"}
    assert {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"} <= REPO_LOCATION_VARS


@needs_git
def test_check_raises_with_gits_own_message(tmp_path):
    with pytest.raises(GitError, match="not a git repository") as info:
        run_git(tmp_path, "rev-parse", "HEAD", check=True)
    assert info.value.returncode not in (None, 0) and not info.value.timed_out


def test_a_missing_git_binary_is_a_typed_error(monkeypatch, tmp_path):
    def missing(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("git")

    monkeypatch.setattr(core_git.subprocess, "run", missing)
    with pytest.raises(GitError, match="git is not available") as info:
        run_git(tmp_path, "status")
    assert info.value.returncode is None and not info.value.timed_out


def test_every_call_has_a_timeout_and_the_env_var_sets_it(monkeypatch, tmp_path):
    seen: list = []

    def fake(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(core_git.subprocess, "run", fake)
    monkeypatch.delenv(TIMEOUT_ENV_VAR, raising=False)
    with pytest.raises(GitError, match="timed out") as info:
        run_git(tmp_path, "status")
    assert info.value.timed_out and seen == [core_git.DEFAULT_TIMEOUT_S]
    monkeypatch.setenv(TIMEOUT_ENV_VAR, "7.5")
    with pytest.raises(GitError):
        run_git(tmp_path, "status")
    assert seen[-1] == 7.5
    monkeypatch.setenv(TIMEOUT_ENV_VAR, "soon")
    with pytest.raises(GitError, match=TIMEOUT_ENV_VAR):
        run_git(tmp_path, "status")


# --------------------------------------------------------------------------------------------- one runner, enforced

_SUBPROCESS_CALLS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
#: Files allowed to start git themselves, with the reason.
_OWN_GIT = {
    "_core/git.py": "the runner itself",
    "safe_precommit.py": "runs inside pre-commit through pre-commit's own cmd_output, on the repository being committed",
}


def _is_git_argv(node: ast.AST) -> bool:
    return isinstance(node, (ast.List, ast.Tuple)) and bool(node.elts) and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "git"


def _git_spawns(tree: ast.AST) -> list[int]:
    """Lines that spawn git directly: a subprocess call whose argv literal starts with "git", or an argv variable
    (``cmd = ["git", ...]``) built for one."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _SUBPROCESS_CALLS:
            if node.args and _is_git_argv(node.args[0]):
                out.append(node.lineno)
        elif isinstance(node, ast.Assign) and _is_git_argv(node.value):
            out.append(node.lineno)
    return out


def _sources() -> list[tuple[str, ast.AST]]:
    return [(p.relative_to(PACKAGE).as_posix(), ast.parse(p.read_text(encoding="utf-8-sig"))) for p in sorted(PACKAGE.rglob("*.py"))]


def test_no_module_spawns_git_outside_the_core_runner():
    found = [f"{rel}:{line}" for rel, tree in _sources() if rel not in _OWN_GIT for line in _git_spawns(tree)]
    assert not found, "run git through py_ci_shared._core.git.run_git (timeout, UTF-8, hook-safe env):\n  " + "\n  ".join(found)


def test_the_git_spawn_detector_has_teeth():
    tree = ast.parse("import subprocess\nsubprocess.run(['git', 'status'])\ncmd = ['git', 'clone']\nsubprocess.run(['ls'])\n")
    assert sorted(_git_spawns(tree)) == [2, 3]


def _text_without_encoding(tree: ast.AST) -> list[int]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            kws = {k.arg: k.value for k in node.keywords if k.arg}
            for name in ("text", "universal_newlines"):
                value = kws.get(name)
                if isinstance(value, ast.Constant) and value.value is True and "encoding" not in kws:
                    out.append(node.lineno)
    return out


def test_no_text_mode_subprocess_decodes_with_the_locale_codec():
    """``text=True`` alone decodes with the locale codec: on cp1251 a UTF-8 byte 0x98 killed the reader thread."""
    found = [f"{rel}:{line}" for rel, tree in _sources() for line in _text_without_encoding(tree)]
    assert not found, 'pass encoding="utf-8", errors="replace" with text=True:\n  ' + "\n  ".join(found)


def test_the_text_mode_detector_has_teeth():
    tree = ast.parse("run(x, text=True)\nrun(x, text=True, encoding='utf-8')\nrun(x, universal_newlines=True)\n")
    assert _text_without_encoding(tree) == [1, 3]


@needs_git
def test_a_clone_error_survives_a_cp1251_console(tmp_path):
    """K-13: `git clone` into an existing non-ASCII directory. With locale decoding on cp1251 the UTF-8 byte 0x98 of
    'И' crashed the reader thread and the result was a bare 'git clone exited 128'."""
    src = _init(tmp_path / "src")
    dest = tmp_path / "dИr" / "x"
    dest.mkdir(parents=True)
    (dest / "f").write_text("x", encoding="utf-8")
    script = textwrap.dedent(f"""
        from pathlib import Path
        from py_ci_shared._consumers import Consumer, _git_clone
        class Local(Consumer):
            @property
            def url(self):
                return {("file://" + src.as_posix())!r}
        print(ascii(_git_clone(Local("x", "o/x", "master", False), Path({str(dest)!r}), None)))
        """)
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1251", "PYTHONPATH": str(SRC)}
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, env=env, timeout=120)
    out = proc.stdout.decode("ascii", "replace")
    assert proc.returncode == 0, out + proc.stderr.decode("utf-8", "replace")
    assert "already exists" in out and "exited 128" not in out, out
