"""Shared check: every git-URL dependency in pyproject.toml is pinned to a
full commit SHA, not floating on a branch/tag.

Generalizes a 2026-07-21 audit finding (S-04): a ``py-ci-shared`` git
dependency in one consuming repo floated on the default branch with no
commit pin, unlike a sibling ``pyutilz`` git dependency in the same file
which WAS pinned -- and the adjacent comment incorrectly claimed it
mirrored that pattern. A floating git dependency means every fresh
``pip install`` (a new dev machine, a CI runner, a Docker rebuild) can
silently pull in a DIFFERENT commit than the one actually developed/tested
against -- the exact reproducibility guarantee a version pin exists to
provide, undone by one un-pinned line.

Usage (in a consuming repo's test suite)::

    from pathlib import Path
    from py_ci_shared.git_dependency_pins import assert_all_git_dependencies_pinned

    def test_all_git_dependencies_pinned():
        assert_all_git_dependencies_pinned(Path(__file__).resolve().parents[2] / "pyproject.toml")

Two further checks live here too. :func:`assert_pins_agree` fails unless every pin of one dependency across a
set of files (requirements, pyproject, uv.lock, CI workflows) names the same commit, and
:func:`assert_installed_includes_pin` fails when the installed copy -- an editable checkout, or a git install
recorded in ``direct_url.json`` -- does not include the pinned commit.

Deliberately dependency-light: ``tomllib``/``pytest`` are imported lazily,
matching this package's other modules.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

# A git URL dependency, PEP 508 direct-reference form: `name @ git+URL[@ref]`. Captures the
# WHOLE URL blob (ref, if any, still embedded) -- ref extraction is done separately in
# _extract_ref, since a regex alone can't reliably tell an auth '@' (`git+https://user@host/...`)
# apart from a ref '@' (`git+https://host/...@ref`) when only ONE '@' is present in the blob.
_GIT_DEP_RE = re.compile(r"^\s*[\"']?[\w.-]+\s*@\s*(git\+[^\s\"'#]+)", re.MULTILINE)
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _extract_ref(git_url: str) -> str | None:
    """Return the ref suffix of a ``git+URL[@ref]`` string, or ``None`` if no
    ref is present at all (the fully-unpinned, floats-on-whatever-HEAD-is case).

    An auth ``@`` (``git+https://user:pass@host/path``) always sits between
    ``://`` and the FIRST ``/`` that follows it (host/port can't contain a
    ``/``); any ``@`` after that first post-``://`` ``/`` is a ref separator,
    not auth. This correctly handles the no-ref case (whether or not auth is
    present) instead of assuming "one ``@`` present" always means "a ref is
    present" -- a bare ``git+https://host/path`` and a bare
    ``git+https://user@host/path`` (auth, no ref) both correctly resolve to
    "no ref", where a naive single-``@``-means-ref-separator regex would
    misparse the latter's auth ``@`` as if it introduced a ref.
    """
    scheme_end = git_url.find("://")
    if scheme_end == -1:
        # No scheme (e.g. a bare `git+ssh` shorthand this project doesn't use) --
        # fall back to "last '@', if any, is the ref separator".
        if "@" not in git_url:
            return None
        return git_url.rsplit("@", 1)[1]
    host_start = scheme_end + 3
    path_start = git_url.find("/", host_start)
    if path_start == -1:
        path_start = len(git_url)
    after_host = git_url[path_start:]
    if "@" not in after_host:
        return None
    return after_host.rsplit("@", 1)[1]


def find_unpinned_git_dependencies(
    pyproject_path: Path,
    *,
    allow_unpinned_url_prefixes: Sequence[str] = (),
) -> list[str]:
    """Return every git-dependency line in ``pyproject_path`` whose ref is
    NOT a full 40-hex-character commit SHA (a branch name, a tag, a
    short/abbreviated SHA, or NO REF AT ALL all count as unpinned -- only
    the full SHA guarantees the exact commit, since a tag can be moved, a
    short SHA can become ambiguous as the repo grows, and no ref at all
    floats on whatever the default branch's HEAD happens to be at install
    time).

    Args:
        pyproject_path: The ``pyproject.toml`` to scan.
        allow_unpinned_url_prefixes: Git-URL prefixes exempt from the pin
            requirement -- intended for FIRST-PARTY dependencies (an upstream
            the same owner controls), where the supply-chain threat the SHA
            pin defends against does not apply: whoever could move the
            upstream ref could equally push to this repo directly, so the
            pin buys ~nothing while costing a hand-maintained SHA bump in
            every satellite on each upstream release. Only exempt a URL when
            a committed lockfile (``uv.lock``/``poetry.lock``) still pins the
            resolved commit -- the lock, not the pyproject line, is then what
            provides reproducibility. Leave empty (the default) for
            third-party git dependencies, where the SHA pin is the only
            defence and remains mandatory.

    Returns:
        The raw ref string found for each violation (e.g. ``"master"``,
        ``"v1.2.0"``, or the literal string ``"<no ref>"`` when none is
        present at all), not line numbers -- pyproject.toml dependency
        arrays are typically short enough that this is enough to locate
        the line.
    """
    text = pyproject_path.read_text(encoding="utf-8")
    violations = []
    for m in _GIT_DEP_RE.finditer(text):
        git_url = m.group(1)
        if any(git_url.startswith(prefix) for prefix in allow_unpinned_url_prefixes):
            continue
        ref = _extract_ref(git_url)
        if ref is None:
            violations.append("<no ref>")
        elif not _FULL_SHA_RE.match(ref):
            violations.append(ref)
    return violations


def assert_all_git_dependencies_pinned(
    pyproject_path: Path,
    *,
    allow_unpinned_url_prefixes: Sequence[str] = (),
) -> None:
    """Fail if any git-URL dependency in ``pyproject_path`` isn't pinned to
    a full 40-hex-character commit SHA. Call this directly as the body of
    a ``test_*`` function -- no baseline/refresh mechanism, unlike the
    code-audit/LOC-budget helpers in this package, since an unpinned
    THIRD-PARTY git dependency is unconditionally wrong (there's no
    legitimate "grandfathered" case for a reproducibility guarantee).

    Args:
        pyproject_path: The ``pyproject.toml`` to scan.
        allow_unpinned_url_prefixes: First-party git-URL prefixes exempt from
            the pin requirement -- see
            :func:`find_unpinned_git_dependencies` for when that is
            defensible and what has to hold instead (a committed lockfile).
    """
    import pytest

    violations = find_unpinned_git_dependencies(pyproject_path, allow_unpinned_url_prefixes=allow_unpinned_url_prefixes)
    if violations:
        pytest.fail(
            f"{len(violations)} git-URL dependenc{'y is' if len(violations) == 1 else 'ies are'} "
            f"not pinned to a full commit SHA in {pyproject_path} -- a fresh install can silently "
            f"resolve to a different commit than the one actually developed/tested against:\n  " + "\n  ".join(violations)
        )


# ---------------------------------------------------------------------------------------------------------------------
# Pins that agree, and an installed copy that includes the pin.
#
# A full SHA on every line (above) is necessary and not sufficient. Two further failures were measured on 2026-09-11:
# mlframe carried 16 pyutilz pins across 9 workflow files naming two different commits, and realtime_applications'
# requirements.txt and pyproject.toml named two different commits for one project -- a bump that misses a line leaves
# CI testing a different commit from the one it claims. And the editable pyutilz checkout on the development machine
# was 8 commits behind master, so baselines refreshed that day were built with scanners no pin used.
# ---------------------------------------------------------------------------------------------------------------------

_SHA40 = r"([0-9a-f]{40})"


def _pin_patterns(name: str) -> list[re.Pattern[str]]:
    n = re.escape(name)
    return [
        re.compile(rf"{n}\.git@{_SHA40}"),
        re.compile(rf"{n}\.git#{_SHA40}"),
        re.compile(rf"{n}-ref:\s*['\"]?{_SHA40}"),
        re.compile(rf"git\s+-C\s+{n}\s+checkout\s+{_SHA40}"),
    ]


def pinned_shas(files: Sequence[Path], name: str, *, root: Path | None = None) -> dict[str, list[str]]:
    """``{sha: ["file:line", ...]}`` for every pin of *name* in *files*: ``<name>.git@<sha>``, ``<name>.git#<sha>``
    (a uv.lock source), ``<name>-ref: <sha>`` (an action input) and ``git -C <name> checkout <sha>``."""
    found: dict[str, list[str]] = {}
    patterns = _pin_patterns(name)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix() if root is not None and path.is_relative_to(root) else path.as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in patterns:
                for sha in pattern.findall(line):
                    found.setdefault(sha, []).append(f"{rel}:{lineno}")
    return found


def assert_pins_agree(files: Sequence[Path], name: str, *, root: Path | None = None, min_pins: int = 1) -> str:
    """Fail unless every pin of *name* in *files* names one commit, and at least *min_pins* were found; return it."""
    import pytest

    found = pinned_shas(list(files), name, root=root)
    total = sum(len(v) for v in found.values())
    if total < min_pins:
        pytest.fail(f"found {total} pin(s) of {name}, expected at least {min_pins} -- Check the file list and the pin spellings")
    if len(found) > 1:
        detail = "\n".join(f"  {sha[:12]}: {', '.join(where)}" for sha, where in sorted(found.items(), key=lambda kv: -len(kv[1])))
        pytest.fail(f"{name} is pinned to {len(found)} different commits; Update every pin to one commit:\n{detail}")
    return next(iter(found))


def _git(args: list[str], cwd: Path):
    import subprocess

    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


def _checkout_root(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _direct_url_commit(dist: str) -> str | None:
    import json
    from importlib import metadata

    try:
        raw = metadata.distribution(dist).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    commit = json.loads(raw).get("vcs_info", {}).get("commit_id")
    return commit if isinstance(commit, str) else None


def installed_pin_problem(package: str, pinned_sha: str, *, dist: str | None = None) -> tuple[str | None, str | None]:
    """``(problem, skip_reason)`` for the installed *package* against *pinned_sha*.

    An editable checkout must contain the pinned commit as an ancestor of its HEAD. A git install records the commit
    it came from in ``direct_url.json``, which must equal the pin. A copy installed from a plain directory, as CI does
    after cloning at the pin, records neither: that is a reason to skip, not a problem.
    """
    import importlib

    module = importlib.import_module(package)
    location = Path(module.__file__ or "").resolve()
    root = _checkout_root(location.parent)
    if root is not None:
        head = _git(["rev-parse", "HEAD"], root).stdout.strip()
        if _git(["cat-file", "-e", f"{pinned_sha}^{{commit}}"], root).returncode != 0:
            return f"{package} is an editable checkout at {root} ({head[:12]}) without the pinned commit {pinned_sha[:12]}; Fetch and update it", None
        if _git(["merge-base", "--is-ancestor", pinned_sha, "HEAD"], root).returncode != 0:
            return f"{package} at {root} is at {head[:12]}, which does not include the pinned commit {pinned_sha[:12]}; Update the checkout to the pin or past it", None
        return None, None
    commit = _direct_url_commit(dist or package)
    if commit is None:
        return None, f"{package} is installed from a plain directory ({location.parent}); it records no commit to compare with the pin"
    if commit != pinned_sha:
        return f"{package} was installed from commit {commit[:12]}, but the pin is {pinned_sha[:12]}; Reinstall it at the pin", None
    return None, None


def assert_installed_includes_pin(package: str, pinned_sha: str, *, dist: str | None = None) -> None:
    """Fail when the installed *package* does not include *pinned_sha*; skip, saying why, when it cannot tell."""
    import pytest

    problem, skip = installed_pin_problem(package, pinned_sha, dist=dist)
    if problem:
        pytest.fail(problem)
    if skip:
        pytest.skip(skip)
