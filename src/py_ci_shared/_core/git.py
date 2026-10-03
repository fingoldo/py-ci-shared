"""The one ``git`` runner every module uses: one timeout, one decoding, one environment policy.

Before this module eight private wrappers ran git with eight policies (audit 2026-10-03 K-13, K-14): some decoded
output with the locale codec (a cp1251 console then lost git's own error text to a ``UnicodeDecodeError`` in the
reader thread), some had no timeout, and only one dropped the repository-locating ``GIT_*`` variables that git
exports to its hooks, so the other seven answered for the COMMITTING repository when they ran inside a pre-commit
hook. Rules here:

* Output is captured as bytes; :func:`git_text` decodes it as UTF-8 with ``errors="replace"`` (git writes paths and
  messages as UTF-8 bytes whatever the console codepage). A caller that needs the exact bytes of a path reads
  ``stdout`` itself (``surrogateescape``).
* Every call has a timeout: ``timeout`` when given, else ``PY_CI_SHARED_GIT_TIMEOUT_S``, else
  :data:`DEFAULT_TIMEOUT_S`. ``timeout=None`` means "no limit" and is only for a caller that bounds the call itself.
* The variables that NAME a repository, index or object store (:data:`REPO_LOCATION_VARS`, the location half of
  ``git rev-parse --local-env-vars``) are removed, so ``-C <repo>`` always talks to *repo*. Configuration passed
  through the environment (``GIT_CONFIG_*``, ``GIT_TERMINAL_PROMPT``, ``GIT_SSH_COMMAND``...) is kept.
* A missing ``git`` binary or a timeout raises :class:`GitError`; a non-zero exit is returned (``check=False``) or
  raised (``check=True``). Nothing is turned into an empty answer here: a caller that wants "empty on failure"
  says so where it calls.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from typing import Optional, Union

from .errors import CoreError

__all__ = ["DEFAULT_TIMEOUT_S", "REPO_LOCATION_VARS", "TIMEOUT_ENV_VAR", "GitError", "git_env", "git_output", "git_text", "run_git"]

PathLike = Union[str, "os.PathLike[str]"]

DEFAULT_TIMEOUT_S = 120.0
TIMEOUT_ENV_VAR = "PY_CI_SHARED_GIT_TIMEOUT_S"

#: Variables that tell git WHICH repository, work tree, index or object store to use. A hook inherits them from the
#: ``git commit`` that runs it, and they override ``-C``: inside a hook ``git -C other rev-parse HEAD`` answered with the
#: committing repository's HEAD.
REPO_LOCATION_VARS = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_INTERNAL_SUPER_PREFIX",
        "GIT_NAMESPACE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


class GitError(CoreError):
    """git could not be run, ran past its timeout, or (with ``check=True``) exited non-zero.

    ``returncode`` is None when git never produced one (missing binary, timeout); ``timed_out`` tells the two apart."""

    def __init__(self, message: str, *, args: Sequence[str] = (), returncode: Optional[int] = None, stderr: str = "", timed_out: bool = False) -> None:
        super().__init__(message)
        self.git_args = tuple(args)
        self.returncode = returncode
        self.stderr = stderr
        self.timed_out = timed_out


def git_env(extra: Optional[Mapping[str, str]] = None, *, base: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """*base* (default ``os.environ``) without :data:`REPO_LOCATION_VARS`, then *extra* on top."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k.upper() not in REPO_LOCATION_VARS}
    if extra:
        env.update(extra)
    return env


def git_text(data: Optional[bytes]) -> str:
    """Bytes git printed, as text: UTF-8, undecodable bytes replaced (never raises, never locale-dependent)."""
    return (data or b"").decode("utf-8", "replace")


def _timeout(timeout: Union[float, str, None]) -> Optional[float]:
    if timeout != "default":
        return None if timeout is None else float(timeout)
    raw = os.environ.get(TIMEOUT_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        raise GitError(f"{TIMEOUT_ENV_VAR}={raw!r} is not a number of seconds") from None
    if value <= 0:
        raise GitError(f"{TIMEOUT_ENV_VAR}={raw!r} must be positive")
    return value


def run_git(
    repo: Optional[PathLike],
    *args: str,
    timeout: Union[float, str, None] = "default",
    check: bool = False,
    stdin: Optional[bytes] = None,
    env: Optional[Mapping[str, str]] = None,
    inherit_location: bool = False,
) -> "subprocess.CompletedProcess[bytes]":
    """``git -C <repo> <args>`` (no ``-C`` when *repo* is None), output as bytes; see the module doc for the policy.

    *env* holds variables added after the location variables are removed (``GIT_TERMINAL_PROMPT=0``, a
    ``GIT_CONFIG_*`` triple). Raises :class:`GitError` when git is missing or times out, and with *check* when it
    exits non-zero (the message carries git's stderr). ``inherit_location=True`` keeps :data:`REPO_LOCATION_VARS`: only
    for a tool that asks about "the repository the user is in" (``install_safe_hook``), never for a gate."""
    argv = ["git", *(["-C", str(repo)] if repo is not None else []), *args]
    limit = _timeout(timeout)
    try:
        proc = subprocess.run(
            argv, capture_output=True, input=stdin, check=False, timeout=limit, env=dict(os.environ, **(env or {})) if inherit_location else git_env(env)
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)} timed out after {limit:g}s in {repo} (raise {TIMEOUT_ENV_VAR})", args=args, timed_out=True) from None
    except OSError as exc:
        raise GitError(f"git is not available to run `git {' '.join(args)}` in {repo}: {exc}", args=args) from exc
    if check and proc.returncode != 0:
        detail = git_text(proc.stderr).strip()[:400]
        raise GitError(f"git {' '.join(args)} failed in {repo} (exit {proc.returncode}): {detail}", args=args, returncode=proc.returncode, stderr=detail)
    return proc


def git_output(repo: Optional[PathLike], *args: str, timeout: Union[float, str, None] = "default") -> str:
    """stdout of a git command that must succeed, as text; raises :class:`GitError` otherwise."""
    return git_text(run_git(repo, *args, timeout=timeout, check=True).stdout)
