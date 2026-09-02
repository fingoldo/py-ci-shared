"""Shared check: a CI workflow does not name paths that do not exist, and declares its permissions.

Three rules over ``.github/workflows/*.yml``:

1. **Every path a step names must exist.** ``working-directory:`` values and the script path in a
   ``run:`` line of the shape ``(sh|bash|python|python3|node) <path>`` are resolved against the repo
   root. A step pointing at a directory that was deleted or renamed does not fail loudly: with
   ``working-directory`` GitHub errors, but a ``run: python tool/gone.py`` inside a ``for`` loop or
   behind a ``[ -f ... ]`` guard silently does nothing and the job stays green. Found for real on
   2026-09-02 (glossum P04-1 / flutter_app_core C03-1): a ``core-tests`` job kept running
   ``working-directory: packages/flutter_app_core`` months after the package moved to its own
   repository, so the step that was supposed to be the core's CI coverage had been dead the whole
   time while reading as a passing job.

2. **A workflow declares a top-level ``permissions:``.** Without one, the ``GITHUB_TOKEN`` gets the
   repository's default, which on many accounts is still read/write on every scope; a compromised
   action in any step can then push. Found as glossum P03-17.

3. **Third-party actions are pinned to a commit SHA.** ``uses: owner/action@v4`` follows a mutable
   tag: whoever controls the tag controls what runs in CI, with the token from rule 2. First-party
   actions (``first_party_owners``) are exempt -- the same carve-out ``git_dependency_pins`` makes
   for this account's own git dependencies -- and so is any ``uses:`` naming a local path
   (``./.github/actions/x``) or a reusable workflow in the same repository.

Rules 2 and 3 are opt-in via ``require_permissions`` / ``require_sha_pins`` because adopting them is
a real diff in every consuming repo; rule 1 has no false positives worth a flag.

Deliberately regex/line-based, no YAML parser and no new dependency, matching this package's other
workflow scanners. Language-agnostic: the same rule fires on a Dart, TypeScript or Python repo.

Usage::

    from py_ci_shared.ci_workflow_paths import assert_workflow_paths_exist

    def test_ci_workflows_name_real_paths():
        assert_workflow_paths_exist(REPO / ".github" / "workflows", REPO)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_WORKING_DIR_RE = re.compile(r"""^\s*(?:-\s*)?working-directory:\s*["']?([^"'#\n]+?)["']?\s*(?:#.*)?$""")
# `run: python tool/x.py`, `run: sh tool/x.sh`, `- run: node e2e/x.js` -- the interpreter makes the
# next token a path with no ambiguity, unlike a bare command name.
_RUN_SCRIPT_RE = re.compile(r"(?:^|[\s;&|])(?:sh|bash|python3?|node|npx\s+tsx|deno\s+run)\s+([\w./-]+\.(?:sh|py|js|mjs|ts|dart))(?![\w./-])")
_USES_RE = re.compile(r"^\s*-?\s*uses:\s*[\"']?([^\"'#\s]+)")
_PERMISSIONS_RE = re.compile(r"^permissions:\s*(?:#.*)?$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Placeholders that are not real paths: `${{ ... }}` expressions and shell variables.
_EXPRESSION_RE = re.compile(r"\$\{\{|\$[A-Za-z_{]")


def _workflow_files(workflows_dir: Path) -> list[Path]:
    return sorted(p for p in list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml")) if p.is_file())


def find_missing_workflow_paths(
    workflows_dir: Path,
    repo_root: Path,
    *,
    require_permissions: bool = False,
    require_sha_pins: bool = False,
    first_party_owners: Iterable[str] = (),
) -> list[str]:
    """Return one problem string per workflow path that does not exist (plus, when enabled, per
    missing top-level ``permissions:`` and per unpinned third-party action)."""
    first_party = {o.lower() for o in first_party_owners}
    files = _workflow_files(workflows_dir)
    if not files:
        return [f"{workflows_dir}: no workflow files found - this check examined nothing, which reads " f"as a pass."]

    problems: list[str] = []
    for path in files:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        rel = path.relative_to(repo_root) if repo_root in path.parents else path.name

        for i, line in enumerate(lines, start=1):
            if line.lstrip().startswith("#"):
                continue
            m = _WORKING_DIR_RE.match(line)
            if m:
                value = m.group(1).strip()
                if not _EXPRESSION_RE.search(value) and not (repo_root / value).exists():
                    problems.append(f"{rel}:{i}: working-directory `{value}` does not exist. The step it " f"scopes cannot be doing what its name claims.")
            for script in _RUN_SCRIPT_RE.findall(line):
                if _EXPRESSION_RE.search(script) or script.startswith("-"):
                    continue
                if not (repo_root / script).exists():
                    problems.append(f"{rel}:{i}: runs `{script}`, which does not exist in the repository. A " f"guard that is not there is not coverage.")

        if require_permissions and not any(_PERMISSIONS_RE.match(l) for l in lines):
            problems.append(
                f"{rel}: no top-level `permissions:` block. The GITHUB_TOKEN then gets the "
                f"repository default, which is read/write on every scope on many accounts. Declare "
                f"the least privilege the workflow actually needs (usually `contents: read`)."
            )

        if require_sha_pins:
            for i, line in enumerate(lines, start=1):
                m = _USES_RE.match(line)
                if not m:
                    continue
                ref = m.group(1)
                if ref.startswith("./") or ref.startswith(".github/"):
                    continue
                owner = ref.split("/", 1)[0].lower()
                if owner in first_party:
                    continue
                _, _, version = ref.partition("@")
                if not _SHA_RE.match(version):
                    problems.append(
                        f"{rel}:{i}: `uses: {ref}` follows a mutable tag. Whoever controls that "
                        f"tag controls what runs in CI with this workflow's token. Pin the full "
                        f"40-character commit SHA (keep the tag in a trailing comment)."
                    )
    return problems


def assert_workflow_paths_exist(
    workflows_dir: Path,
    repo_root: Path,
    *,
    require_permissions: bool = False,
    require_sha_pins: bool = False,
    first_party_owners: Iterable[str] = (),
) -> None:
    """Fail when a workflow names a path that does not exist (see
    :func:`find_missing_workflow_paths`)."""
    import pytest

    problems = find_missing_workflow_paths(
        workflows_dir,
        repo_root,
        require_permissions=require_permissions,
        require_sha_pins=require_sha_pins,
        first_party_owners=first_party_owners,
    )
    if problems:
        pytest.fail(f"{len(problems)} CI workflow problem(s):\n  " + "\n  ".join(problems))
