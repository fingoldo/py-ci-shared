"""Report a consumer workflow that pins a between-release SHA of the reusable workflows and omits ``py-ci-shared-ref`` (47-F10).

README says a caller pinned to a SHA between releases gets the previous release's configs unless it also passes
``py-ci-shared-ref: <the same SHA>``. A ``uses:`` pinned to a commit that is not a release tag, with no matching input in
the same workflow file, runs an older configuration than its ``# vX.Y.Z`` comment suggests. Report only: the check does
not fail a consumer until the consumers have moved to the input.

    python -m py_ci_shared.adoption_sha_ref --repos-file repos.toml --resolve-in ../py-ci-shared

Release-tag commits are exempt: a release's own default ref is the release, so a pin to it needs no input.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Optional

from ._core import read_source

__all__ = ["sha_pins_without_ref_input", "scan_repos", "main"]

_USES_SHA = re.compile(r"\buses:\s*['\"]?[\w.-]+/py-ci-shared/\.github/workflows/[\w.-]+\.ya?ml@([0-9a-f]{40})\b")
_REF_INPUT = re.compile(r"\bpy-ci-shared-ref:\s*['\"]?([0-9a-f]{40})\b")


def sha_pins_without_ref_input(workflow_text: str, is_release_sha: Callable[[str], bool]) -> list[tuple[int, str]]:
    """``(line, sha)`` for each ``uses:`` pinned to a non-release SHA that the same file does not pair with ``py-ci-shared-ref``."""
    given = set(_REF_INPUT.findall(workflow_text))
    out: list[tuple[int, str]] = []
    for number, line in enumerate(workflow_text.splitlines(), 1):
        for sha in _USES_SHA.findall(line):
            if is_release_sha(sha) or sha in given:
                continue
            out.append((number, sha))
    return out


def scan_repos(roots: Iterable[tuple[str, Path]], is_release_sha: Callable[[str], bool]) -> list[str]:
    """One finding line per flagged pin, as ``repo path:line: sha``."""
    findings: list[str] = []
    for name, root in roots:
        for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
            for line, sha in sha_pins_without_ref_input(read_source(wf), is_release_sha):
                findings.append(f"{name} {wf.relative_to(root).as_posix()}:{line}: {sha[:12]}")
    return findings


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Report consumer workflows that pin a between-release SHA without ``py-ci-shared-ref``; always 0 (report only, 47-F10)."""
    from .adoption_matrix import RefResolver, load_repo_list

    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.adoption_sha_ref", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repos-file", required=True, help="the repos file a checkout wrote")
    parser.add_argument("--resolve-in", required=True, help="a py-ci-shared checkout with its release tags")
    args = parser.parse_args(argv)
    resolver = RefResolver(args.resolve_in)
    release_shas = {sha for tag in resolver.releases() if (sha := resolver(tag))}
    findings = scan_repos(load_repo_list(Path(args.repos_file)), release_shas.__contains__)
    for finding in findings:
        sys.stdout.write(f"::warning title=uses-sha-without-ref-input::{finding}\n")
    sys.stdout.write(f"uses-sha-without-ref-input: {len(findings)} pin(s) to a between-release SHA without py-ci-shared-ref\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
