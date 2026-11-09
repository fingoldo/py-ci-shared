"""Release decisions for ``.github/workflows/release.yml``, as pure functions with a small command line.

* ``major-move TAG``: may pushing ``TAG`` move its major tag (``v1``)? Only when ``TAG`` is the highest release of that
  major. A back-port tag (``v1.17.1`` after ``v1.19.0``) must not move every ``@v1`` consumer backwards.
* ``rollback TAG``: ``TAG`` is an existing release, so the emergency ``workflow_dispatch`` may point its major at it.
* ``ci-green --repo R --sha S``: the ``self-ci.yml`` run for commit ``S`` succeeded; waits (``--wait``) while it is
  still running, fails when it failed or never ran.
* ``prepare VERSION [--write | --check]``: bring pyproject, ``__version__``, the README install tag, the reusable workflows' default ref and the
  ``since`` of every not-yet-published module to ``VERSION`` (dry run by default; ``--check`` exits 1 while anything still differs). Run it BEFORE tagging.
* ``version`` (used by ``tests/test_release_version.py``): the declared version is ahead of every release tag, or
  is exactly the release whose tagged commit is ``HEAD``.

Stdlib only: the workflow runs it before (and independently of) installing the package. Exit 0 on yes, 1 with a
``::error::`` line on no, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Callable, Optional

RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
GREEN = frozenset({"success"})


def release_key(tag: str) -> Optional[tuple[int, int, int]]:
    """``(major, minor, patch)`` of a ``vX.Y.Z`` tag, else ``None`` (``v1``, ``v1.2``, ``v1.2.3-rc1`` are not releases)."""
    m = RELEASE_TAG.match(tag.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def release_tags(tags: Iterable[str]) -> list[str]:
    """The ``vX.Y.Z`` tags among ``tags``, oldest version first (numeric, so ``v1.10.0`` sorts after ``v1.9.0``)."""
    keyed = {t.strip(): release_key(t) for t in tags}
    return sorted((t for t, k in keyed.items() if k is not None), key=lambda t: keyed[t] or (0, 0, 0))


def highest(tags: Iterable[str], major: Optional[int] = None) -> Optional[str]:
    """The highest release tag, optionally only among ``vMAJOR.*.*``."""
    found = [t for t in release_tags(tags) if major is None or (release_key(t) or (-1,))[0] == major]
    return found[-1] if found else None


def major_move_problem(tag: str, tags: Iterable[str]) -> Optional[str]:
    """Why pushing ``tag`` may not move its major tag, or ``None`` when it may."""
    key = release_key(tag)
    if key is None:
        return f"{tag} is not a vX.Y.Z release tag"
    tags = list(tags)
    if tag not in tags:
        return f"{tag} is not among the repository's tags"
    top = highest(tags, key[0])
    if top != tag:
        return (
            f"{tag} is not the newest v{key[0]} release ({top} is): moving v{key[0]} to it would move every @v{key[0]} "
            f"consumer backwards. A back-port is published without moving v{key[0]}; to roll v{key[0]} back on purpose "
            "use the release workflow's workflow_dispatch (see CLAUDE.md, Rolling back a release)"
        )
    return None


def rollback_problem(target: str, tags: Iterable[str], published: Optional[Iterable[str]] = None) -> Optional[str]:
    """Why the major tag may not be pointed at ``target`` by the emergency dispatch, or ``None``.

    ``published`` is the tag names that have a GitHub release (``None`` when the API could not be read, which skips the check): a tag whose Release
    run failed is an existing tag that never passed verify and must not become ``v1``."""
    if release_key(target) is None:
        return f"{target!r} is not a vX.Y.Z release tag"
    if target not in set(tags):
        return f"{target} is not an existing tag; roll back only to a release that already shipped"
    if published is not None and target not in set(published):
        newest = ", ".join(release_tags(published)[-5:][::-1]) or "none"
        return f"{target} has no GitHub release (its Release run failed or never ran); roll back to a published one, newest first: {newest}"
    return None


def version_problem(version: str, tags: Iterable[str], head: str, tag_commit: Optional[str]) -> Optional[str]:
    """Why the declared ``version`` is wrong for a tree at commit ``head``, or ``None``.

    ``tag_commit`` is the commit ``v{version}`` points at (``None`` when that tag does not exist). Between releases the
    version must be strictly ahead of every release tag; it may equal a tag only on that tag's own commit."""
    key = release_key(f"v{version}")
    if key is None:
        return f"{version!r} is not X.Y.Z"
    top = highest(tags)
    fix = "bump version in pyproject.toml and src/py_ci_shared/__init__.py to the next release"
    if top is not None and key < (release_key(top) or (0, 0, 0)):
        return f"the declared version {version} is behind the newest release {top}; {fix}"
    if tag_commit is not None and tag_commit != head:
        return f"v{version} already shipped at {tag_commit[:12]} and HEAD ({head[:12]}) is past it; {fix}"
    return None


def ci_verdict(runs: Sequence[dict]) -> tuple[str, str]:
    """``("green" | "pending" | "red", why)`` from the ``self-ci.yml`` runs of one commit (any event)."""
    if any(r.get("conclusion") in GREEN for r in runs):
        ok = next(r for r in runs if r.get("conclusion") in GREEN)
        return "green", f"self-ci succeeded: {ok.get('html_url', '')}"
    if any(r.get("status") != "completed" for r in runs):
        return "pending", "self-ci is still running for this commit"
    if not runs:
        return "red", "self-ci never ran for this commit on master (tag a commit that was pushed to master)"
    worst = ", ".join(f"{r.get('conclusion')} {r.get('html_url', '')}" for r in runs)
    hint = ""
    if all(r.get("conclusion") == "cancelled" for r in runs):
        hint = " (only cancelled runs: start a fresh one with `gh workflow run self-ci.yml --ref master`, workflow_dispatch, then re-run Release)"
    return "red", f"self-ci did not succeed for this commit: {worst}{hint}"


#: Every file that names the release, relative to the repository root.
PREP_FILES = (
    "pyproject.toml",
    "src/py_ci_shared/__init__.py",
    "README.md",
    "src/py_ci_shared/registry.toml",
    ".github/workflows/black-filtered.yml",
    ".github/workflows/ruff-blocking.yml",
    ".github/workflows/lint-advisory.yml",
    ".github/workflows/gates.yml",
)
#: The four reusable workflows whose ``py-ci-shared-ref`` default moves with the release (the tail of ``PREP_FILES``).
WORKFLOW_FILES = PREP_FILES[4:]
_PYPROJECT_VERSION = re.compile(r'^(version = ")[^"]+(")', re.M)
_MODULE_VERSION = re.compile(r'^(__version__ = ")[^"]+(")', re.M)
_README_TAG = re.compile(r"py-ci-shared\.git@(v\d+\.\d+\.\d+)")
_WORKFLOW_REF = re.compile(r'(py-ci-shared-ref:\s*\n(?:[^\n]*\n){0,8}?\s*default:\s*")v\d+\.\d+\.\d+(")')
_SINCE = re.compile(r'(^since = ")(\d+\.\d+\.\d+)(")', re.M)


def _sub_once(pattern: "re.Pattern[str]", text: str, version: str, what: str) -> str:
    """``pattern`` with its quoted value replaced by ``version``; it must match exactly once, or the file is not the shape this knows."""
    new, count = pattern.subn(lambda m: f"{m.group(1)}{version}{m.group(2)}", text)
    if count != 1:
        raise ValueError(f"{what}: expected one match, found {count}")
    return new


def _readme_edit(text: str, version: str) -> str:
    """Every install tag the README names, and the prose mentions of those same tags, moved to ``v{version}``."""
    olds = set(_README_TAG.findall(text))
    if not olds:
        raise ValueError("README.md: no `py-ci-shared.git@vX.Y.Z` install line")
    for old in sorted(olds, reverse=True):
        text = re.sub(re.escape(old) + r"(?![\d.]*\d)", f"v{version}", text)
    return text


def _registry_edit(text: str, version: str, published: tuple[int, int, int]) -> str:
    """The ``since`` of every module first shipped AFTER the newest published release becomes ``version``: a dead tag never shipped it."""

    def move(m: "re.Match[str]") -> str:
        key = release_key(f"v{m.group(2)}") or (0, 0, 0)
        return f"{m.group(1)}{version}{m.group(3)}" if key > published else m.group(0)

    return _SINCE.sub(move, text)


_CATALOGUE_ROW = re.compile(r"(^\| \[`[^`]+`\]\([^)]*\) \| [a-z]+ \| )(\d+\.\d+\.\d+)( \|)", re.M)


def _catalogue_edit(text: str, version: str, published: tuple[int, int, int]) -> str:
    """The README gate catalogue is the rendered registry, so its version column moves with ``since``."""

    def move(m: "re.Match[str]") -> str:
        key = release_key(f"v{m.group(2)}") or (0, 0, 0)
        return f"{m.group(1)}{version}{m.group(3)}" if key > published else m.group(0)

    return _CATALOGUE_ROW.sub(move, text)


def _workflow_edit(text: str, version: str, name: str) -> str:
    """The reusable workflow's ``py-ci-shared-ref`` default, moved to ``v{version}``."""
    new, count = _WORKFLOW_REF.subn(lambda m: f"{m.group(1)}v{version}{m.group(2)}", text)
    if count != 1:
        raise ValueError(f"{name}: expected one py-ci-shared-ref default, found {count}")
    return new


def release_edits(version: str, texts: dict[str, str], published_tag: str) -> dict[str, str]:
    """The new text of every file in ``PREP_FILES`` for a release ``version``, given the newest PUBLISHED release tag.

    Between a release and the next the version is bumped in five kinds of place that must agree before the tag is pushed: ``pyproject.toml``, the module's
    ``__version__``, the README install tag(s), the four reusable workflows' default ref, and the ``since`` of every module not yet in a published
    release. ``tests/test_release_version.py`` checks them, but only on a tagged commit's Release run, which is too late to fix without a new tag
    (v1.22.0 and v1.22.2 were both tagged with the README still on the previous release).
    """
    key, published = release_key(f"v{version}"), release_key(published_tag)
    if key is None or published is None:
        raise ValueError(f"version {version!r} and published tag {published_tag!r} must be X.Y.Z / vX.Y.Z")
    if key <= published:
        raise ValueError(f"v{version} is not ahead of the newest published release {published_tag}")
    out = {
        "pyproject.toml": _sub_once(_PYPROJECT_VERSION, texts["pyproject.toml"], version, "pyproject.toml version"),
        "src/py_ci_shared/__init__.py": _sub_once(_MODULE_VERSION, texts["src/py_ci_shared/__init__.py"], version, "__version__"),
        "README.md": _catalogue_edit(_readme_edit(texts["README.md"], version), version, published),
        "src/py_ci_shared/registry.toml": _registry_edit(texts["src/py_ci_shared/registry.toml"], version, published),
    }
    for name in WORKFLOW_FILES:
        out[name] = _workflow_edit(texts[name], version, name)
    return out


def _published_release(repo: str, token: str, tags: Sequence[str]) -> str:
    """The newest tag that has a GitHub release (a tag whose Release run failed is not one); the newest tag when the API cannot be read."""
    try:
        names = [r.get("tag_name", "") for r in _github(token)(f"repos/{repo}/releases?per_page=100")]
    except OSError:
        names = []
    return highest(names) or highest(tags) or "v0.0.0"


def run_prep(repo_dir: Path, version: str, published_tag: str, *, write: bool) -> list[str]:
    """The files that change (written when ``write``); a ValueError names a file that is not the shape the edit knows."""
    texts = {name: (repo_dir / name).read_bytes().decode("utf-8") for name in PREP_FILES}  # bytes: read_text would turn CRLF into LF and hide the line ending
    new = release_edits(version, {k: v.replace("\r\n", "\n") for k, v in texts.items()}, published_tag)
    changed = []
    for name, text in new.items():
        crlf = "\r\n" in texts[name]
        text = text.replace("\n", "\r\n") if crlf else text
        if text != texts[name]:
            changed.append(name)
            if write:
                (repo_dir / name).write_bytes(text.encode("utf-8"))
    return changed


def _git_tags(cwd: Path) -> list[str]:
    out = subprocess.run(["git", "tag", "--list", "v*"], cwd=cwd, capture_output=True, text=True, check=True, timeout=60)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the host that was asked.

    CPython copies the request headers, Authorization included, to the redirect target. A token must not leave
    api.github.com because a proxy fault or an API change pointed the request somewhere else, so a redirect to another host is refused
    (it surfaces as an HTTPError for the original request).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _github(token: str) -> Callable[[str], Any]:
    def get(path: str) -> Any:
        req = urllib.request.Request(f"https://api.github.com/{path}")
        req.add_header("Accept", "application/vnd.github+json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.build_opener(_SameHostRedirects).open(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return get


class TransientApiError(Exception):
    """A GitHub API call that failed for a reason a retry may cure: a 5xx answer or a network error."""


_API_ATTEMPTS = 3
_API_RETRY_SECONDS = 10.0


def _api_with_retries(api: Callable[[str], Any], path: str, sleep: Callable[[float], None]) -> Any:
    last: Optional[BaseException] = None
    for attempt in range(_API_ATTEMPTS):
        try:
            return api(path)
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        if attempt + 1 < _API_ATTEMPTS:
            sleep(_API_RETRY_SECONDS)
    raise TransientApiError(str(last)) from last


def wait_for_ci(
    api: Callable[[str], Any],
    repo: str,
    sha: str,
    *,
    workflow: str = "self-ci.yml",
    branch: str = "master",
    wait: float = 0.0,
    poll: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, str]:
    """Poll ``workflow``'s runs for ``sha`` until green or red, or ``wait`` seconds pass (then the verdict is pending).

    A transient API failure (a 5xx or a network error) is retried, and a poll still failing after the retries returns
    pending with the reason, so one 502 cannot abort a release verification (46-F15). A client error still raises."""
    end = clock() + wait
    path = f"repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&branch={branch}&per_page=50"
    while True:
        try:
            runs = _api_with_retries(api, path, sleep).get("workflow_runs", [])
        except TransientApiError as exc:
            return ("pending", f"GitHub API unreachable ({exc}): re-run this job")
        verdict = ci_verdict(runs)
        if verdict[0] != "pending" or clock() >= end:
            return verdict
        sleep(poll)


def green_run_id(runs: Sequence[dict]) -> Optional[int]:
    """The id of the first successful run among ``runs`` (what ``ci_verdict`` called green), or ``None``."""
    return next((r.get("id") for r in runs if r.get("conclusion") in GREEN), None)


def failed_job_names(jobs: Sequence[dict]) -> list[str]:
    """Names of the jobs that failed inside a run that is green overall: a ``continue-on-error`` preview leg reports exactly like that."""
    return [str(j.get("name")) for j in jobs if j.get("conclusion") == "failure"]


FULL_SHA = re.compile(r"[0-9a-f]{40}")


def sha_problem(sha: str) -> Optional[str]:
    """Why ``sha`` cannot be used to look up runs (GitHub matches ``head_sha`` on all 40 characters), or ``None``."""
    if FULL_SHA.fullmatch(sha):
        return None
    return f"--sha {sha!r} is not a full commit SHA; pass the full 40-character lowercase SHA (git rev-parse <rev>), a short one finds no run"


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")


def _release_names(args: argparse.Namespace) -> Optional[list[str]]:
    """Tag names with a GitHub release, or ``None`` when ``--repo`` was not given or the API cannot be read."""
    if not getattr(args, "repo", None):
        return None
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    try:
        return [r.get("tag_name", "") for r in _github(token)(f"repos/{args.repo}/releases?per_page=100")]
    except OSError:
        return None


def _warn_failed_legs(api: Callable[[str], Any], args: argparse.Namespace) -> None:
    """Annotate failed jobs of the green run: a non-blocking preview leg that is red must not pass unseen."""
    try:
        runs = api(f"repos/{args.repo}/actions/workflows/{args.workflow}/runs?head_sha={args.sha}&branch=master&per_page=50").get("workflow_runs", [])
        run_id = green_run_id(runs)
        jobs = api(f"repos/{args.repo}/actions/runs/{run_id}/jobs?per_page=100").get("jobs", []) if run_id else []
    except OSError:
        return
    for name in failed_job_names(jobs):
        _say(f"::warning title=failed leg in a green run::{name}")


def _main_prepare(args: argparse.Namespace) -> int:
    repo_dir = Path(args.repo_dir)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    published = args.published or _published_release(args.repo, token, _git_tags(repo_dir))
    try:
        changed = run_prep(repo_dir, args.version, published, write=args.write)
    except ValueError as exc:
        _say(f"::error::{exc}")
        return 2
    verb = "wrote" if args.write else "would change"
    _say(f"release {args.version} (newest published: {published}): " + (f"{verb} {', '.join(changed)}" if changed else "nothing differs, ready to tag"))
    return 1 if (args.check and changed) else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="release_guard.py", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("major-move", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("tag")
        p.add_argument("--repo-dir", default=".")
        p.add_argument("--repo", default=None, help="owner/name: with it, rollback also requires the tag to have a GitHub release")
    ci = sub.add_parser("ci-green")
    ci.add_argument("--repo", required=True)
    ci.add_argument("--sha", required=True)
    ci.add_argument("--workflow", default="self-ci.yml")
    ci.add_argument("--wait", type=float, default=0.0, help="seconds to keep polling while the run is in progress")
    ci.add_argument("--poll", type=float, default=30.0)
    prep = sub.add_parser("prepare")
    prep.add_argument("version", help="X.Y.Z, the release about to be tagged")
    prep.add_argument("--repo-dir", default=".")
    prep.add_argument("--repo", default="fingoldo/py-ci-shared")
    prep.add_argument("--published", help="newest PUBLISHED release tag (default: the newest GitHub release)")
    mode = prep.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write the edits")
    mode.add_argument("--check", action="store_true", help="exit 1 while any file still differs")
    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        return _main_prepare(args)
    if args.cmd == "ci-green":
        bad = sha_problem(args.sha)
        if bad:
            _say(f"::error::{bad}")
            return 2
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        state, why = wait_for_ci(_github(token), args.repo, args.sha, workflow=args.workflow, wait=args.wait, poll=args.poll)
        _say(why if state == "green" else f"::error::{why}")
        if state == "green":
            _warn_failed_legs(_github(token), args)
        return 0 if state == "green" else 1
    tags = _git_tags(Path(args.repo_dir))
    problem = major_move_problem(args.tag, tags) if args.cmd == "major-move" else rollback_problem(args.tag, tags, _release_names(args))
    if problem:
        _say(f"::error::{problem}")
        return 1
    _say(f"{args.tag}: ok to move v{(release_key(args.tag) or (0,))[0]} to it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
