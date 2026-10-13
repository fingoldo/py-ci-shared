"""Cut a py-ci-shared release from this machine: everything ``self-ci.yml`` and ``release.yml`` check, run locally.

GitHub Actions cannot run on this account (every job fails to start on billing), so the tag-triggered ``release.yml`` and
the self-ci run it waits for never happen. This script is the release path instead. For ``X.Y.Z`` it:

1. refuses a dirty working tree, a ``HEAD`` that is not ``origin/master`` (``--ahead-ok``: a fast-forward of it, whose
   push is then printed first), and a ``vX.Y.Z`` tag that already exists elsewhere (locally or on the remote);
2. runs ``release_guard.py prepare X.Y.Z --check`` and the declared-version check of release.yml's ``verify`` job;
3. runs every blocking check of self-ci.yml and release.yml in throwaway virtual environments built from this tree:
   the whole test suite on every Python of self-ci's matrix (coverage, with its ``fail_under`` floor, on 3.11 as on
   self-ci's ubuntu 3.11 leg; the 3.11 environment is installed with release.yml's runtime constraints), the smoke
   import, ``py-ci-shared run-all``, ruff over the whole tree, ``configs/ruff-base.toml`` loading, mypy, the isolated
   mypy-beachhead run, black-filtered, and every pre-commit hook over all files (detect-secrets, actionlint, ...);
4. creates the annotated tag ``vX.Y.Z`` on ``HEAD``, then runs release.yml's tag checks (``version_tag_currency``,
   ``release_guard.py major-move``); a failure there deletes the tag again;
5. prints the push commands: the tag, ``v1`` moved to it with ``--force-with-lease``, and ``gh release create`` with
   notes taken from CHANGELOG.md. ``--push`` runs them after a typed confirmation. Only this script moves ``v1``.

Not reproducible here: self-ci's Linux and macOS legs (the suite runs on this machine's OS) and its
``force-remote-fetch`` integration jobs, which fetch py-ci-shared from GitHub at the commit and so need it pushed.

Stdlib only, like ``release_guard.py``. Exit 0 when the tag is ready (or pushed), 1 when a check refuses, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parents[2]
GUARD = ".github/scripts/release_guard.py"
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
#: self-ci.yml's matrix (``python:``); 3.11 is the leg that measures coverage and runs ruff and mypy.
PYTHONS = ("3.9", "3.10", "3.11", "3.12", "3.13")
COVERAGE_PYTHON = "3.11"
MYPY_VERSION = "2.1.0"
RUNTIME_CONSTRAINTS = ".github/constraints/runtime.txt"


@dataclass(frozen=True)
class Step:
    """One command the release runs; any non-zero exit refuses the release."""

    name: str
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)


Runner = Callable[[Step], int]


def _env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(extra)
    return env


def run_step(step: Step) -> int:
    """Run ``step`` in the repository root, output streamed to this terminal."""
    sys.stdout.write(f"\n=== {step.name}\n$ {' '.join(step.argv)}\n")
    sys.stdout.flush()
    return subprocess.run(list(step.argv), cwd=REPO, env=_env(step.env), check=False).returncode


def git(*args: str) -> str:
    """``git args`` in the repository root; stdout stripped, raises on failure."""
    out = subprocess.run(["git", *args], cwd=REPO, env=_env({}), capture_output=True, encoding="utf-8", check=True, timeout=300)
    return out.stdout.strip()


def git_ok(*args: str) -> Optional[str]:
    """``git args`` stdout, or ``None`` when git exits non-zero."""
    try:
        return git(*args)
    except subprocess.CalledProcessError:
        return None


@dataclass(frozen=True)
class RepoState:
    """What the preconditions look at, read once from git."""

    dirty: str
    head: str
    upstream: str
    head_descends_from_upstream: bool
    local_tag_commit: Optional[str]
    remote_tag_commit: Optional[str]
    #: What the remote's major tag (``refs/tags/v1``) holds now: the lease the ``v1`` push is made against.
    remote_major_ref: Optional[str] = None


def read_state(tag: str, remote: str, branch: str) -> RepoState:
    git("fetch", "--quiet", "--tags", remote, branch)
    head = git("rev-parse", "HEAD")
    upstream = git("rev-parse", f"{remote}/{branch}")
    major = tag.split(".", 1)[0]
    remote_line = git("ls-remote", "--tags", remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}", f"refs/tags/{major}")
    remote_refs = dict(reversed(line.split("\t", 1)) for line in remote_line.splitlines() if "\t" in line)
    return RepoState(
        dirty=git("status", "--porcelain", "--untracked-files=all"),
        head=head,
        upstream=upstream,
        head_descends_from_upstream=git_ok("merge-base", "--is-ancestor", upstream, head) is not None,
        local_tag_commit=git_ok("rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"),
        remote_tag_commit=remote_refs.get(f"refs/tags/{tag}^{{}}") or remote_refs.get(f"refs/tags/{tag}"),
        remote_major_ref=remote_refs.get(f"refs/tags/{major}"),
    )


def precondition_problems(state: RepoState, tag: str, *, ahead_ok: bool, remote: str = "origin", branch: str = "master") -> list[str]:
    """Why the release may not start from ``state``; empty when it may."""
    problems = []
    if state.dirty:
        problems.append(f"the working tree is not clean (commit or remove):\n{state.dirty}")
    if state.head != state.upstream:
        if not ahead_ok:
            problems.append(f"HEAD {state.head[:12]} is not {remote}/{branch} {state.upstream[:12]}; release the commit that is on {branch}")
        elif not state.head_descends_from_upstream:
            problems.append(f"HEAD {state.head[:12]} does not descend from {remote}/{branch} {state.upstream[:12]}, so pushing it is not a fast-forward")
    if state.remote_tag_commit is not None:
        problems.append(f"{tag} already exists on {remote} at {state.remote_tag_commit[:12]}: a pushed tag is never moved; release the next version")
    if state.local_tag_commit is not None and state.local_tag_commit != state.head:
        problems.append(f"{tag} already exists locally at {state.local_tag_commit[:12]}, not HEAD; delete it (git tag -d {tag}) if it was never pushed")
    return problems


def venv_python(venv: Path) -> str:
    return str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def check_steps(version: str, *, pythons: Sequence[str], venv_root: Path, workers: int) -> list[Step]:
    """Every check self-ci.yml and release.yml run before a tag may become ``v1``, in the order they run here."""
    tag = f"v{version}"
    steps = [
        Step("release_guard prepare --check (CLAUDE.md, before tagging)", (sys.executable, GUARD, "prepare", version, "--check")),
        Step(
            "pre-commit hooks over all files (commit-time hooks only see changed files)",
            (sys.executable, "-m", "pre_commit", "run", "--all-files", "--show-diff-on-failure"),
        ),
    ]
    xdist = ("-n", str(workers)) if workers > 1 else ()
    for py in pythons:
        venv = venv_root / f"py{py}"
        python = venv_python(venv)
        constraints = ("-c", RUNTIME_CONSTRAINTS) if py == COVERAGE_PYTHON else ()
        steps += [
            Step(f"[{py}] fresh virtual environment", ("uv", "venv", "--clear", "--python", py, str(venv))),
            Step(
                f"[{py}] install the package with [dev] (self-ci 'Install package')",
                ("uv", "pip", "install", "--python", python, *constraints, "-e", ".[dev]", "pytest-xdist"),
            ),
            Step(
                f"[{py}] smoke import and black_filtered_apply (self-ci)",
                (python, "-c", "import py_ci_shared; print('py_ci_shared', py_ci_shared.__version__)"),
            ),
            Step(
                f"[{py}] black_filtered_apply on one file (self-ci)",
                (python, "-m", "py_ci_shared.black_filtered_apply", "--config", "pyproject.toml", "src/py_ci_shared/__init__.py"),
                {"BLACK_CMD": "uvx black"},
            ),
        ]
        cov = ("--cov=py_ci_shared", "--cov-report=term") if py == COVERAGE_PYTHON else ()
        steps.append(
            Step(
                f"[{py}] the whole test suite (self-ci 'Run tests', release.yml 'Run the test suite')", (python, "-m", "pytest", "tests/", "-ra", *xdist, *cov)
            )
        )
        steps.append(Step(f"[{py}] py-ci-shared run-all (self-ci dogfood)", (python, "-m", "py_ci_shared.cli", "run-all")))
        if py == COVERAGE_PYTHON:
            steps += [
                Step(
                    "version the package declares equals the tag (release.yml verify)",
                    (python, "-c", f"import py_ci_shared, sys; v = py_ci_shared.__version__; print(v); sys.exit(0 if 'v' + v == {tag!r} else 1)"),
                ),
                Step("install mypy (self-ci)", ("uv", "pip", "install", "--python", python, f"mypy=={MYPY_VERSION}")),
                Step("mypy src/py_ci_shared (self-ci MyPy self-check)", (python, "-m", "mypy", "src/py_ci_shared")),
            ]
    steps += [
        Step(
            "ruff over the whole tree (self-ci Ruff self-check, integration-ruff-blocking)",
            ("uvx", f"ruff=={ruff_version()}", "check", ".", "--ignore", "C901"),
        ),
        Step(
            "configs/ruff-base.toml loads (self-ci)", ("uvx", f"ruff=={ruff_version()}", "check", "--config", "configs/ruff-base.toml", "--show-settings", ".")
        ),
        Step(
            "black-filtered --check (integration-black-filtered)",
            (sys.executable, "-m", "py_ci_shared.black_filtered_apply", "--config", "pyproject.toml", "--check", "src/py_ci_shared", "tests"),
            {"BLACK_CMD": f"uvx black=={black_version()}", "PYTHONPATH": str(REPO / "src")},
        ),
        Step("isolated mypy (integration-mypy-beachhead)", ("uvx", f"mypy=={MYPY_VERSION}", "--follow-imports=silent", "src/py_ci_shared/__init__.py")),
    ]
    return steps


def _tool_version(name: str) -> str:
    text = (REPO / "src/py_ci_shared/tool_versions.py").read_text(encoding="utf-8")
    m = re.search(rf'^{name} = "([0-9.]+)"', text, re.M)
    if not m:
        raise ValueError(f"{name} not found in src/py_ci_shared/tool_versions.py")
    return m.group(1)


def ruff_version() -> str:
    return _tool_version("RUFF_VERSION")


def black_version() -> str:
    return _tool_version("BLACK_VERSION")


def tag_steps(tag: str) -> list[Step]:
    """release.yml's checks that need the tag to exist."""
    src = {"PYTHONPATH": str(REPO / "src")}
    currency = (
        "from pathlib import Path; from py_ci_shared.version_tag_currency import find_version_tag_problems as f; "
        "p = f(Path('.'), 'pyproject.toml'); print(*p, sep='\\n'); raise SystemExit(1 if p else 0)"
    )
    return [
        Step("version_tag_currency (release.yml verify)", (sys.executable, "-c", currency), src),
        Step("the tag is the newest release of its major (release.yml major-move)", (sys.executable, GUARD, "major-move", tag)),
    ]


def release_notes(changelog: str, version: str) -> str:
    """The CHANGELOG section for ``version`` (``## X.Y.Z`` or ``## vX.Y.Z``), else the ``## Unreleased`` section."""
    sections: dict[str, str] = {}
    for m in re.finditer(r"^## (.+?)\s*$\n(.*?)(?=^## |\Z)", changelog, re.M | re.S):
        sections[m.group(1).strip()] = m.group(2).strip()
    for key in (version, f"v{version}", "Unreleased"):
        if sections.get(key):
            return sections[key] + "\n"
    raise ValueError(f"CHANGELOG.md has no non-empty '## {version}' or '## Unreleased' section")


def push_commands(tag: str, sha: str, *, remote: str, branch: str, push_branch: bool, old_major: Optional[str], notes_file: Path) -> list[list[str]]:
    """The commands that publish ``tag``: what release.yml's ``publish`` job did, plus the branch when HEAD is ahead of it."""
    major = tag.split(".", 1)[0]
    cmds = []
    if push_branch:
        cmds.append(["git", "push", remote, f"{sha}:refs/heads/{branch}"])
    cmds.append(["git", "push", remote, f"refs/tags/{tag}"])
    lease = f"--force-with-lease=refs/tags/{major}:{old_major}" if old_major else f"--force-with-lease=refs/tags/{major}:"
    cmds.append(["git", "push", lease, remote, f"{sha}:refs/tags/{major}"])
    cmds.append(["gh", "release", "create", tag, "--verify-tag", "--title", tag, "--notes-file", str(notes_file)])
    return cmds


def _quote(argv: Sequence[str]) -> str:
    return " ".join(f'"{a}"' if (" " in a or not a) else a for a in argv)


def release(
    version: str,
    *,
    remote: str = "origin",
    branch: str = "master",
    ahead_ok: bool = False,
    push: bool = False,
    pythons: Sequence[str] = PYTHONS,
    workers: int = 2,
    venv_root: Optional[Path] = None,
    runner: Runner = run_step,
    state_reader: Callable[[str, str, str], RepoState] = read_state,
    tagger: Optional[Callable[[str, str], None]] = None,
    untagger: Optional[Callable[[str], None]] = None,
    confirm: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """The whole release; returns the exit code. Every side effect is injectable so the decisions are testable."""
    if not VERSION.match(version):
        out(f"error: {version!r} is not X.Y.Z")
        return 2
    tag = f"v{version}"
    state = state_reader(tag, remote, branch)
    problems = precondition_problems(state, tag, ahead_ok=ahead_ok, remote=remote, branch=branch)
    if problems:
        for p in problems:
            out(f"refused: {p}")
        return 1
    try:
        notes_text = release_notes((REPO / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    except ValueError as exc:
        out(f"refused: {exc}")
        return 1
    root = venv_root or Path(tempfile.gettempdir()) / "py-ci-shared-release"
    for step in check_steps(version, pythons=pythons, venv_root=root, workers=workers):
        if runner(step) != 0:
            out(f"refused: '{step.name}' failed; nothing was tagged")
            return 1
    created = state.local_tag_commit is None
    if created:
        (tagger or _create_tag)(tag, state.head)
    for step in tag_steps(tag):
        if runner(step) != 0:
            if created:
                (untagger or _delete_tag)(tag)
            out(f"refused: '{step.name}' failed; the local tag {tag} was deleted")
            return 1
    notes = Path(tempfile.gettempdir()) / f"py-ci-shared-{tag}-notes.md"
    notes.write_text(notes_text, encoding="utf-8")
    cmds = push_commands(
        tag, state.head, remote=remote, branch=branch, push_branch=state.head != state.upstream, old_major=state.remote_major_ref, notes_file=notes
    )
    out(f"\n{tag} is tagged locally on {state.head[:12]} and passed every check. To publish it, run:\n")
    for c in cmds:
        out("  " + _quote(c))
    if not push:
        return 0
    if confirm(f"\nType {tag} to run these {len(cmds)} commands: ").strip() != tag:
        out("not pushed")
        return 1
    for c in cmds:
        if runner(Step(" ".join(c[:3]), tuple(c))) != 0:
            out(f"failed: {_quote(c)}; the steps before it are done, re-run the rest by hand")
            return 1
    return 0


def rollback_commands(target: str, target_sha: str, *, remote: str, old_major: Optional[str]) -> list[list[str]]:
    """Point the major tag back at an earlier release ``target`` (release.yml's emergency ``rollback`` job)."""
    major = target.split(".", 1)[0]
    lease = f"--force-with-lease=refs/tags/{major}:{old_major}" if old_major else f"--force-with-lease=refs/tags/{major}:"
    return [["git", "push", lease, remote, f"{target_sha}:refs/tags/{major}"]]


def rollback(
    target: str,
    *,
    remote: str = "origin",
    push: bool = False,
    runner: Runner = run_step,
    confirm: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """Check ``target`` is a release that shipped (``release_guard.py rollback``) and print, or run, the ``v1`` move."""
    git("fetch", "--quiet", "--tags", remote)
    if runner(Step("the target is a release that shipped (release_guard rollback)", (sys.executable, GUARD, "rollback", target))) != 0:
        return 1
    major = target.split(".", 1)[0]
    line = git_ok("ls-remote", "--tags", remote, f"refs/tags/{major}")
    cmds = rollback_commands(target, git("rev-parse", f"{target}^{{commit}}"), remote=remote, old_major=line.split("\t", 1)[0] if line else None)
    out(f"\nTo point {major} at {target}, run:\n")
    for c in cmds:
        out("  " + _quote(c))
    out("Then fix forward with a new, higher release. Consumers with a clone need `git fetch --tags --force`.")
    if not push:
        return 0
    if confirm(f"\nType {target} to run it: ").strip() != target:
        out("not pushed")
        return 1
    return 0 if all(runner(Step("rollback", tuple(c))) == 0 for c in cmds) else 1


def _create_tag(tag: str, sha: str) -> None:
    git("tag", "-a", tag, "-m", f"Release {tag}", sha)


def _delete_tag(tag: str) -> None:
    git("tag", "-d", tag)


def _default_workers() -> int:
    try:
        sys.path.insert(0, str(REPO / "src"))
        from py_ci_shared.worker_budget import available_workers

        return available_workers()
    except Exception:  # the budget helper is a convenience; two workers is what pre-push uses
        return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="release_local.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("version", nargs="?", help="X.Y.Z, the release to cut")
    parser.add_argument("--rollback-to", metavar="vX.Y.Z", help="emergency: point v1 back at this earlier release instead of releasing")
    parser.add_argument("--push", action="store_true", help="run the printed push commands after a typed confirmation")
    parser.add_argument("--ahead-ok", action="store_true", help="HEAD may be a fast-forward of origin/master; its push is printed first")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--pythons", default=",".join(PYTHONS), help=f"comma-separated; default self-ci's matrix {','.join(PYTHONS)}")
    parser.add_argument("--workers", type=int, help="pytest -n (default: py_ci_shared.worker_budget)")
    parser.add_argument("--venv-root", type=Path, help="where the per-Python environments go (default: the temp directory)")
    args = parser.parse_args(argv)
    if args.rollback_to:
        return rollback(args.rollback_to, remote=args.remote, push=args.push)
    if not args.version:
        parser.error("give the version X.Y.Z to release, or --rollback-to vX.Y.Z")
    pythons = tuple(p.strip() for p in args.pythons.split(",") if p.strip())
    if COVERAGE_PYTHON not in pythons:
        parser.error(f"--pythons must include {COVERAGE_PYTHON}: coverage, mypy and the release-path install run there")
    return release(
        args.version,
        remote=args.remote,
        branch=args.branch,
        ahead_ok=args.ahead_ok,
        push=args.push,
        pythons=pythons,
        workers=args.workers or _default_workers(),
        venv_root=args.venv_root,
    )


if __name__ == "__main__":
    sys.exit(main())
