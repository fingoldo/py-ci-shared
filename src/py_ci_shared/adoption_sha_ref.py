"""Report a consumer workflow that pins a between-release SHA of the reusable workflows and omits ``py-ci-shared-ref`` (47-F10).

README says a caller pinned to a SHA between releases gets the previous release's configs unless it also passes
``py-ci-shared-ref: <the same SHA>``. A ``uses:`` pinned to a commit that is not a release tag, with no matching input in
the same workflow file, runs an older configuration than its ``# vX.Y.Z`` comment suggests. Report only: the check does
not fail a consumer until the consumers have moved to the input.

    python -m py_ci_shared.adoption_sha_ref REPO [REPO ...] [--repos-file repos.toml] [--resolve-in ../py-ci-shared]

Release-tag commits are exempt: a release's own default ref is the release, so a pin to it needs no input. A pinned workflow
that does not declare the input at that SHA is not reported either (47-T5): passing it would break the caller.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Optional

from ._core import read_source

__all__ = ["sha_pins_without_ref_input", "scan_repos", "declares_ref_input", "main"]

_USES_SHA = re.compile(r"\buses:\s*['\"]?[\w.-]+/py-ci-shared/\.github/workflows/([\w.-]+\.ya?ml)@([0-9a-f]{40})\b")
_REF_INPUT = re.compile(r"\bpy-ci-shared-ref:\s*['\"]?([0-9a-f]{40})\b")
_DECLARES_REF_INPUT = re.compile(r"^\s+py-ci-shared-ref:\s*$", re.M)


def declares_ref_input(workflow_text: Optional[str]) -> bool:
    """Whether a reusable workflow declares the ``py-ci-shared-ref`` input (a bare key under ``inputs:``); True when its text is unknown."""
    return workflow_text is None or _DECLARES_REF_INPUT.search(workflow_text) is not None


def sha_pins_without_ref_input(
    workflow_text: str,
    is_release_sha: Callable[[str], bool],
    declares_input: Callable[[str, str], bool] = lambda name, sha: True,
) -> list[tuple[int, str]]:
    """``(line, sha)`` for each ``uses:`` pinned to a non-release SHA that the same file does not pair with ``py-ci-shared-ref``.

    *declares_input(workflow file name, sha)* says whether the pinned workflow has the input at that SHA; a pin whose workflow does not is skipped.
    """
    given = set(_REF_INPUT.findall(workflow_text))
    out: list[tuple[int, str]] = []
    for number, line in enumerate(workflow_text.splitlines(), 1):
        for name, sha in _USES_SHA.findall(line):
            if is_release_sha(sha) or sha in given or not declares_input(name, sha):
                continue
            out.append((number, sha))
    return out


def scan_repos(
    roots: Iterable[tuple[str, Path]],
    is_release_sha: Callable[[str], bool],
    declares_input: Callable[[str, str], bool] = lambda name, sha: True,
) -> list[str]:
    """One finding line per flagged pin, as ``repo path:line: sha``."""
    findings: list[str] = []
    for name, root in roots:
        for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
            for line, sha in sha_pins_without_ref_input(read_source(wf), is_release_sha, declares_input):
                findings.append(f"{name} {wf.relative_to(root).as_posix()}:{line}: {sha[:12]}")
    return findings


def _package_checkout() -> Optional[Path]:
    """The py-ci-shared git checkout this package is imported from, when it is one (a site-packages install is not)."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Report consumer workflows that pin a between-release SHA without ``py-ci-shared-ref``; always 0 (report only, 47-F10)."""
    from .adoption_matrix import RefResolver, _unique_names, load_repo_list

    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.adoption_sha_ref", description=__doc__.split("\n", 1)[0])
    parser.add_argument("repos", nargs="*", help="consumer repo roots")
    parser.add_argument("--repos-file", help="TOML file listing repos (`repos = [...]` or `[[repo]]` with path/name)")
    parser.add_argument("--resolve-in", help="a py-ci-shared checkout with its release tags (default: the checkout this package runs from)")
    args = parser.parse_args(argv)
    roots: list[tuple[str, Path]] = [(Path(r).resolve().name, Path(r).resolve()) for r in args.repos]
    if args.repos_file:
        roots += load_repo_list(Path(args.repos_file))
    if not roots:
        parser.error("give repo roots or --repos-file")
    checkout = Path(args.resolve_in) if args.resolve_in else _package_checkout()
    if checkout is None:
        parser.error("--resolve-in is needed: this package is not running from a py-ci-shared git checkout")
    resolver = RefResolver(checkout)
    release_shas = {sha for tag in resolver.releases() if (sha := resolver(tag))}

    def declares(name: str, sha: str) -> bool:
        return declares_ref_input(resolver.file_text(sha, f".github/workflows/{name}"))

    findings = scan_repos(_unique_names(roots), release_shas.__contains__, declares)
    for finding in findings:
        sys.stdout.write(f"::warning title=uses-sha-without-ref-input::{finding}\n")
    sys.stdout.write(f"uses-sha-without-ref-input: {len(findings)} pin(s) to a between-release SHA without py-ci-shared-ref\n")
    return 0


if __name__ == "__main__":
    from ._core.console import safe_stdio

    safe_stdio()
    raise SystemExit(main())
