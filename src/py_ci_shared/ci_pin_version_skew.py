"""The py-ci-shared version a consumer's workflows pin differs from the one installed where its hooks run.

A consumer's CI installs py-ci-shared at a commit SHA with a trailing version comment
(``pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@<sha>"  # v1.18.0``, or
``uses: fingoldo/py-ci-shared/.github/workflows/x.yml@<sha>  # v1.18.0``), while the developer's pre-commit hooks run
against an editable install that moves with the library. A gate widened in between passes or fails differently on the
two sides: green locally and red in CI, or the reverse, found only at push (production_scrapers, dashboard and
realtime_applications, 2026-10-04: pinned v1.18.0 against a local 1.21.x).

Offline, so only the comment is compared: that a SHA is the commit its tag names cannot be checked without the
repository (``git_dependency_pins.assert_installed_includes_pin`` compares the commit, which an editable checkout
that is merely AHEAD of the pin always includes, so it cannot see this skew). Rules:

- ``ci-pin-no-version``: a pin to a SHA carries no ``# vX.Y.Z`` comment, so nothing says which release it is.
- ``ci-pin-installed-older``: the installed version is older than a pin. CI runs newer gates than the hooks do.
- ``ci-pin-installed-newer``: the installed version is newer than a pin. ``tolerate_ahead=True`` accepts it for a
  consumer that develops against the newer library on purpose; installed older is never tolerated.
- ``ci-pin-mixed``: the workflows pin different versions, so two jobs of one run apply different gates.
- ``ci-pin-lags-release``: with ``known_releases`` (the library's release tags), a pin more than ``max_lag`` releases
  behind the newest, the tolerance ``configs/consumers.toml`` gives ``consumer-pins.yml`` (2).
- ``ci-pin-installed-unknown``: the installed version is missing or not a version number.

The reusable workflows' ``py-ci-shared-ref: <sha>  # vX.Y.Z`` input counts as a pin too.

A ref that is a moving tag or branch (``@v1``) names no version and is not a pin here; that is the library's
documented ``@v1`` consumption. A release-tag ref (``@v1.18.0``) is a pin whose version is the tag.

Usage in a consumer's meta test::

    from pathlib import Path
    from py_ci_shared.ci_pin_version_skew import assert_installed_matches_ci_pin

    def test_installed_py_ci_shared_matches_ci_pin():
        assert_installed_matches_ci_pin(Path(__file__).resolve().parents[1])
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import EmptyScanError, Finding, SourceReadError, UnparsedFilesError, read_source, relative_posix

__all__ = ["RULE", "assert_installed_matches_ci_pin", "find_pin_skew_findings", "installed_py_ci_shared_version"]

RULE = "ci-pin-version-skew"
DEFAULT_REPO = "fingoldo/py-ci-shared"
#: The lag the shared consumer-pins workflow tolerates (``--allow-behind 2``).
DEFAULT_MAX_LAG = 2

_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_RELEASE = re.compile(r"^v?(\d+\.\d+\.\d+)$")
_COMMENT_VERSION = re.compile(r"#\s*v?(\d+\.\d+\.\d+)\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)*")


def _pin_pattern(repo: str) -> "re.Pattern[str]":
    name = re.escape(repo.rsplit("/", 1)[-1])
    # `owner/repo...@ref` (pip URL, uses:) or the reusable-workflow input `<repo>-ref: <sha>`
    return re.compile(re.escape(repo) + r"[\w./-]*@([A-Za-z0-9._/-]+)|(?<![\w-])" + name + r"-ref:\s*([A-Za-z0-9._/-]+)")


def _vtuple(version: str) -> Optional[tuple[int, ...]]:
    match = _NUMBER.match(version.strip().lstrip("vV"))
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def installed_py_ci_shared_version() -> Optional[str]:
    """The ``__version__`` of the py_ci_shared that is imported (what the local hooks run), or None."""
    import py_ci_shared

    version = getattr(py_ci_shared, "__version__", None)
    return version if isinstance(version, str) else None


class _Pin:
    __slots__ = ("line", "path", "ref", "version")

    def __init__(self, path: str, line: int, version: Optional[str], ref: str) -> None:
        self.path, self.line, self.version, self.ref = path, line, version, ref


def _workflow_files(workflows_dir: Path) -> list[Path]:
    return sorted(p for p in list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml")) if p.is_file())


def _pins_in(text: str, rel: str, pattern: "re.Pattern[str]") -> list[_Pin]:
    pins = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        match = pattern.search(line)
        if match is None:
            continue
        ref = match.group(1) or match.group(2)
        release = _RELEASE.match(ref)
        if release:
            pins.append(_Pin(rel, number, release.group(1), ref))
        elif _SHA.match(ref):
            comment = _COMMENT_VERSION.search(line[match.end() :])
            pins.append(_Pin(rel, number, comment.group(1) if comment else None, ref))
    return pins


def _format(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


def _read_pins(base: Path, files: list[Path], pattern: "re.Pattern[str]", allow_unparsed: bool) -> list[_Pin]:
    pins: list[_Pin] = []
    unread: list[str] = []
    for path in files:
        try:
            text = read_source(path)
        except SourceReadError as exc:
            unread.append(f"{relative_posix(path, base)}: {exc}")
            continue
        pins.extend(_pins_in(text, relative_posix(path, base), pattern))
    if unread and not allow_unparsed:
        raise UnparsedFilesError("ci_pin_version_skew cannot read:\n  " + "\n  ".join(unread))
    return pins


def _no_version(pin: _Pin) -> Finding:
    return Finding(
        pin.path,
        pin.line,
        "ci-pin-no-version",
        f"the pin {pin.ref[:12]} carries no '# vX.Y.Z' comment, so neither a reader nor this check knows which release CI runs; "
        "Append the release the SHA belongs to as a trailing '# vX.Y.Z' comment",
    )


_Pinned = list[tuple[_Pin, tuple[int, ...]]]


def _installed_findings(pinned: "_Pinned", installed_text: Optional[str], tolerate_ahead: bool) -> list[Finding]:
    if not pinned:
        return []
    installed = _vtuple(installed_text) if installed_text else None
    if installed is None:
        first = pinned[0][0]
        return [
            Finding(
                first.path,
                first.line,
                "ci-pin-installed-unknown",
                f"the installed py-ci-shared version is {installed_text!r}, not a version number, so it cannot be compared with the pin; "
                "Install py-ci-shared (pip install -e <checkout>) so that py_ci_shared.__version__ is set",
            )
        ]
    out = []
    for pin, vt in pinned:
        if vt > installed:
            out.append(
                Finding(
                    pin.path,
                    pin.line,
                    "ci-pin-installed-older",
                    f"py-ci-shared {_format(installed)} is installed but CI pins {_format(vt)}, so CI runs newer gates than your hooks and a push "
                    f"can go red after a green local run; Upgrade the local install to {_format(vt)} or newer (git pull the checkout behind the editable install)",
                )
            )
        elif vt < installed and not tolerate_ahead:
            out.append(
                Finding(
                    pin.path,
                    pin.line,
                    "ci-pin-installed-newer",
                    f"py-ci-shared {_format(installed)} is installed but CI pins {_format(vt)}, so a gate widened since then passes or fails "
                    f"differently locally than in CI; Bump the pin to {_format(installed)}, or pass tolerate_ahead=True to say that developing "
                    "ahead of CI is deliberate",
                )
            )
    return out


def _mixed_findings(pinned: "_Pinned") -> list[Finding]:
    distinct = sorted({vt for _, vt in pinned})
    if len(distinct) < 2:
        return []
    first = pinned[0][0]
    where = "; ".join(f"{pin.path}:{pin.line} = {_format(vt)}" for pin, vt in pinned)
    message = (
        f"the workflows pin {len(distinct)} different py-ci-shared versions ({where}), so two jobs of one run apply different gates; "
        f"Move every pin to one version (the newest, {_format(distinct[-1])})"
    )
    return [Finding(first.path, first.line, "ci-pin-mixed", message)]


def _lag_findings(pinned: "_Pinned", known_releases: Iterable[str], max_lag: int) -> list[Finding]:
    releases = sorted({vt for vt in (_vtuple(r) for r in known_releases) if vt is not None})
    if not releases:
        return []
    found = []
    for pin, vt in pinned:
        behind = sum(1 for r in releases if r > vt)
        if behind > max_lag:
            message = (
                f"the pin {_format(vt)} is {behind} releases behind {_format(releases[-1])} (tolerated: {max_lag}); "
                "Bump the pin to a recent release and update its '# vX.Y.Z' comment"
            )
            found.append(Finding(pin.path, pin.line, "ci-pin-lags-release", message))
    return found


def find_pin_skew_findings(
    root: Union[str, Path],
    *,
    workflows_dir: str = ".github/workflows",
    installed_version: Optional[str] = None,
    tolerate_ahead: bool = False,
    known_releases: Iterable[str] = (),
    max_lag: int = DEFAULT_MAX_LAG,
    repo: str = DEFAULT_REPO,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> list[Finding]:
    """Every skew between the py-ci-shared pins of ``root/<workflows_dir>`` and the installed version, sorted by path and line.

    *installed_version* defaults to the imported ``py_ci_shared.__version__``. Raises ``EmptyScanError`` when fewer than
    *min_files* workflow files exist and ``UnparsedFilesError`` for one that cannot be read (unless *allow_unparsed*):
    a gate that cannot read the workflows must not pass them.
    """
    base = Path(root)
    files = _workflow_files(base / workflows_dir)
    if len(files) < min_files:
        raise EmptyScanError(f"ci_pin_version_skew: {len(files)} workflow file(s) under {base / workflows_dir}, expected at least {min_files}")
    pins = _read_pins(base, files, _pin_pattern(repo), allow_unparsed)
    pinned = [(pin, vt) for pin in pins if pin.version is not None for vt in [_vtuple(pin.version)] if vt is not None]
    installed_text = installed_version if installed_version is not None else installed_py_ci_shared_version()
    out = [_no_version(pin) for pin in pins if pin.version is None]
    out.extend(_installed_findings(pinned, installed_text, tolerate_ahead))
    out.extend(_mixed_findings(pinned))
    out.extend(_lag_findings(pinned, known_releases, max_lag))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_installed_matches_ci_pin(
    root: Union[str, Path],
    *,
    workflows_dir: str = ".github/workflows",
    installed_version: Optional[str] = None,
    tolerate_ahead: bool = False,
    known_releases: Iterable[str] = (),
    max_lag: int = DEFAULT_MAX_LAG,
    repo: str = DEFAULT_REPO,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Fail when the installed py-ci-shared and the version the workflows pin disagree (see the module docstring)."""
    found = find_pin_skew_findings(
        root,
        workflows_dir=workflows_dir,
        installed_version=installed_version,
        tolerate_ahead=tolerate_ahead,
        known_releases=known_releases,
        max_lag=max_lag,
        repo=repo,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
    )
    if found:
        raise AssertionError(f"{len(found)} ci-pin-version-skew finding(s):\n  " + "\n  ".join(f.render() for f in found))
