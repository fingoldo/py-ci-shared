"""The consumer repo list (``configs/consumers.toml``) and a read-only shallow checkout of it for scheduled jobs.

Usage::

    python -m py_ci_shared._consumers checkout configs/consumers.toml --dest "$RUNNER_TEMP/consumers" \\
        --repos-file "$RUNNER_TEMP/consumers/repos.toml" [--corpus-only]

The token comes from ``CONSUMER_READ_TOKEN``. A private repo without a token is skipped with a GitHub ``::warning::``
annotation (the job stays green but says so loudly); a clone that should work (a public repo, or any repo while a
token is set) and fails is an error, exit 1. The written repos file is the ``[[repo]]`` form
:func:`py_ci_shared.adoption_matrix.load_repo_list` reads.
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ._core import CoreError, read_source
from ._toml_compat import tomllib

__all__ = ["TOKEN_ENV", "CheckoutResult", "Consumer", "checkout", "load_consumers", "main", "write_repos_file"]

TOKEN_ENV = "CONSUMER_READ_TOKEN"


@dataclass(frozen=True)
class Consumer:
    """One consumer repo: ``repo`` is ``owner/name`` on GitHub, ``branch`` the one its CI runs on."""

    name: str
    repo: str
    branch: str
    private: bool = True
    corpus: bool = False

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}.git"


@dataclass
class CheckoutResult:
    """What :func:`checkout` did: repos on disk, repos skipped for want of a token, clones that failed."""

    cloned: list[tuple[Consumer, Path]]
    skipped: list[Consumer]
    failed: list[tuple[Consumer, str]]


def load_consumers(path: Path) -> list[Consumer]:
    """The ``[[consumer]]`` tables of *path*; a table without ``name``, ``repo`` or ``branch`` raises ``CoreError``."""
    data = tomllib.loads(read_source(Path(path)))
    out: list[Consumer] = []
    for i, table in enumerate(data.get("consumer", []) or []):
        missing = [k for k in ("name", "repo", "branch") if not table.get(k)]
        if missing:
            raise CoreError(f"{path}: consumer #{i + 1} lacks {missing}")
        if str(table["repo"]).count("/") != 1:
            raise CoreError(f"{path}: consumer {table['name']!r}: repo must be owner/name, got {table['repo']!r}")
        out.append(
            Consumer(
                name=str(table["name"]),
                repo=str(table["repo"]),
                branch=str(table["branch"]),
                private=bool(table.get("private", True)),
                corpus=bool(table.get("corpus", False)),
            )
        )
    names = [c.name for c in out]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise CoreError(f"{path}: duplicate consumer names {dupes}")
    return out


def _git_clone(consumer: Consumer, dest: Path, token: Optional[str]) -> Optional[str]:
    """Clone *consumer* shallowly into *dest*; the error text, or None on success. The token never reaches argv."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    cmd = ["git"]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        n = int(env.get("GIT_CONFIG_COUNT", "0") or 0)  # append to, never replace, config passed the same way
        env.update({"GIT_CONFIG_COUNT": str(n + 1), f"GIT_CONFIG_KEY_{n}": "http.https://github.com/.extraheader"})
        env[f"GIT_CONFIG_VALUE_{n}"] = f"AUTHORIZATION: basic {basic}"
    cmd += ["clone", "--quiet", "--depth", "1", "--single-branch", "--branch", consumer.branch, consumer.url, str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return str(exc)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or f"git clone exited {proc.returncode}"
    return None


def checkout(
    consumers: Sequence[Consumer],
    dest: Path,
    *,
    token: Optional[str],
    clone: Optional[Callable[[Consumer, Path, Optional[str]], Optional[str]]] = None,
) -> CheckoutResult:
    """Clone every consumer under ``dest/<name>``; skip private ones when *token* is empty (see the module doc)."""
    run = clone if clone is not None else _git_clone
    result = CheckoutResult([], [], [])
    dest.mkdir(parents=True, exist_ok=True)
    for consumer in consumers:
        if consumer.private and not token:
            result.skipped.append(consumer)
            continue
        target = dest / consumer.name
        error = run(consumer, target, token or None)
        if error is None:
            result.cloned.append((consumer, target))
        else:
            result.failed.append((consumer, error))
    return result


def _toml_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_repos_file(path: Path, cloned: Sequence[tuple[Consumer, Path]]) -> None:
    """Write the ``[[repo]]`` list (absolute paths) that ``adoption_matrix --repos-file`` and ``corpus_drift`` read."""
    lines = []
    for consumer, root in cloned:
        lines += ["[[repo]]", f"name = {_toml_str(consumer.name)}", f"path = {_toml_str(root.resolve().as_posix())}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("\n".join(lines).encode("utf-8"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``checkout``: clone the listed consumers; 1 when a clone that should work failed or nothing was cloned."""
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared._consumers", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("checkout", help="shallow-clone the consumers of a consumers.toml")
    p.add_argument("config", help="consumers.toml")
    p.add_argument("--dest", required=True, help="directory the repos are cloned into")
    p.add_argument("--repos-file", required=True, help="write the [[repo]] list of the cloned repos here")
    p.add_argument("--corpus-only", action="store_true", help="only the consumers marked corpus = true")
    args = parser.parse_args(argv)
    consumers = load_consumers(Path(args.config))
    if args.corpus_only:
        consumers = [c for c in consumers if c.corpus]
    token = os.environ.get(TOKEN_ENV, "").strip() or None
    result = checkout(consumers, Path(args.dest), token=token)
    for consumer in result.skipped:
        sys.stdout.write(f"::warning title=consumer skipped::{consumer.repo} is private and {TOKEN_ENV} is not set; it was NOT checked\n")
    for consumer, error in result.failed:
        sys.stdout.write(f"::error title=consumer checkout failed::{consumer.repo}@{consumer.branch}: {error.splitlines()[0] if error else ''}\n")
    write_repos_file(Path(args.repos_file), result.cloned)
    sys.stdout.write(f"cloned {len(result.cloned)}, skipped {len(result.skipped)} (no token), failed {len(result.failed)}\n")
    if result.failed:
        return 1
    if not result.cloned:
        sys.stderr.write("_consumers: nothing was cloned, so nothing would be checked\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
