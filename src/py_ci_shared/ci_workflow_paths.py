"""Shared check: a CI workflow does not name paths that do not exist, and declares its permissions.

Three rules over ``.github/workflows/*.yml``:

1. **Every path a step names must exist.** ``working-directory:`` values and the script path in a
   ``run:`` line of the shape ``(sh|bash|python|python3|node) <path>`` are resolved against the repo
   root (script paths only inside ``run:`` content, never in a ``name:`` or description). A
   ``working-directory`` applies to its own step, or as ``defaults.run.working-directory`` to its own
   job or the whole workflow, never to a later step or job. A step pointing at a directory that was deleted or renamed does not fail loudly: with
   ``working-directory`` GitHub errors, but a ``run: python tool/gone.py`` inside a ``for`` loop or
   behind a ``[ -f ... ]`` guard silently does nothing and the job stays green. Found for real on
   2026-09-02 (glossum P04-1 / flutter_app_core C03-1): a ``core-tests`` job kept running
   ``working-directory: packages/flutter_app_core`` months after the package moved to its own
   repository, so the step that was supposed to be the core's CI coverage had been dead the whole
   time while reading as a passing job.

2. **A workflow declares a top-level ``permissions:``** (a block, ``read-all``, ``{}``: any value). Without one, the ``GITHUB_TOKEN`` gets the
   repository's default, which on many accounts is still read/write on every scope; a compromised
   action in any step can then push. Found as glossum P03-17.

3. **Third-party actions are pinned to a commit SHA.** ``uses: owner/action@v4`` follows a mutable
   tag: whoever controls the tag controls what runs in CI, with the token from rule 2. First-party
   actions (``first_party_owners``) are exempt -- the same carve-out ``git_dependency_pins`` makes
   for this account's own git dependencies -- and so is any ``uses:`` naming a local path
   (``./.github/actions/x``) or a reusable workflow in the same repository. A ``docker://`` image pinned
   by ``@sha256:<digest>`` is pinned.

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
from typing import Optional

from ._core import read_source

_WORKING_DIR_RE = re.compile(r"""^\s*(?:-\s*)?working-directory:\s*["']?([^"'#\n]+?)["']?\s*(?:#.*)?$""")
# `run: python tool/x.py`, `run: sh tool/x.sh`, `- run: node e2e/x.js` -- the interpreter makes the
# next token a path with no ambiguity, unlike a bare command name.
_RUN_SCRIPT_RE = re.compile(r"(?:^|[\s;&|])(?:sh|bash|python3?|node|npx\s+tsx|deno\s+run)\s+([\w./-]+\.(?:sh|py|js|mjs|ts|dart))(?![\w./-])")
_USES_RE = re.compile(r"^\s*-?\s*uses:\s*[\"']?([^\"'#\s]+)")
_PERMISSIONS_RE = re.compile(r"^permissions\s*:")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DOCKER_DIGEST_RE = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")
# Placeholders that are not real paths: `${{ ... }}` expressions and shell variables.
_EXPRESSION_RE = re.compile(r"\$\{\{|\$[A-Za-z_{]")
# `cd e2e && node x.js`, `(cd tool && python y.py)` - the directory a command runs in.
_CD_RE = re.compile(r"(?:^|[\s(&;])cd\s+([\w./-]+)")
_RUN_KEY_RE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:\s*(?P<value>.*)$")
_JOBS_RE = re.compile(r"^jobs\s*:\s*$")


def _workflow_files(workflows_dir: Path) -> list[Path]:
    return sorted(p for p in list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml")) if p.is_file())


def _strip_comment(line: str) -> str:
    """*line* without a trailing YAML comment: a ``#`` at the start or after whitespace, outside quotes."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and (i == 0 or line[i - 1] in " \t:-[{,"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _scopes(lines: list[str]) -> "tuple[list[Optional[int]], list[Optional[int]]]":
    """Per line: the index of the job it sits in (or None) and of the step (list item) it sits in (or None)."""
    job_of: list[Optional[int]] = []
    step_of: list[Optional[int]] = []
    in_jobs = False
    job_indent: Optional[int] = None
    job_no: Optional[int] = None
    jobs_seen = -1
    item_indent: Optional[int] = None
    step_no: Optional[int] = None
    steps_seen = -1
    for line in lines:
        if not line.strip():
            job_of.append(job_no)
            step_of.append(step_no)
            continue
        indent = _indent(line)
        content = line.strip()
        if indent == 0:
            in_jobs = bool(_JOBS_RE.match(content))
            job_indent = job_no = item_indent = step_no = None
        elif in_jobs:
            if job_indent is None:
                job_indent = indent
            if indent <= job_indent:
                jobs_seen += 1
                job_no = jobs_seen
                item_indent = step_no = None
        if item_indent is not None and indent <= item_indent and not (indent == item_indent and content.startswith("-")):
            item_indent = step_no = None
        if content.startswith("- ") or content == "-":
            if item_indent is None or indent <= item_indent:
                item_indent = indent
                steps_seen += 1
                step_no = steps_seen
        job_of.append(job_no)
        step_of.append(step_no)
    return job_of, step_of


def _run_content(lines: list[str]) -> "list[Optional[str]]":
    """Per line: the shell text it contributes to a ``run:`` (inline value or block-scalar body), else None."""
    out: list[Optional[str]] = [None] * len(lines)
    block_indent: Optional[int] = None
    for i, line in enumerate(lines):
        if block_indent is not None:
            if not line.strip() or _indent(line) > block_indent:
                out[i] = line
                continue
            block_indent = None
        m = _RUN_KEY_RE.match(line)
        if not m:
            continue
        value = m.group("value").strip()
        if value[:1] in ("|", ">"):
            block_indent = _indent(line)
        else:
            out[i] = value
    return out


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
        return [f"{workflows_dir}: no workflow files found - this check examined nothing, which reads as a pass."]

    problems: list[str] = []
    for path in files:
        raw_lines = read_source(path).splitlines()
        # A block-scalar `run: |` body is shell, not YAML: a `#` there is a shell comment and is stripped the same way.
        lines = [_strip_comment(line) for line in raw_lines]
        rel = path.relative_to(repo_root).as_posix() if repo_root in path.parents else path.name
        job_of, step_of = _scopes(lines)
        run_text = _run_content(lines)

        # The working-directory each scope declares: a step's own, a job's `defaults.run`, the workflow's `defaults.run`.
        step_wd: dict[int, str] = {}
        job_wd: dict[int, str] = {}
        top_wd: Optional[str] = None
        for i, line in enumerate(lines):
            if run_text[i] is not None and not _RUN_KEY_RE.match(line):
                continue  # a line inside a run block is shell, never a key
            m = _WORKING_DIR_RE.match(line)
            if not m:
                continue
            value = m.group(1).strip()
            if not _EXPRESSION_RE.search(value) and not (repo_root / value).exists():
                problems.append(f"{rel}:{i + 1}: working-directory `{value}` does not exist. The step it " f"scopes cannot be doing what its name claims.")
            usable = value if not _EXPRESSION_RE.search(value) else ""
            if step_of[i] is not None:
                step_wd[step_of[i]] = usable  # type: ignore[index]
            elif job_of[i] is not None:
                job_wd[job_of[i]] = usable  # type: ignore[index]
            else:
                top_wd = usable

        for i, text in enumerate(run_text):
            if text is None:
                continue
            step, job = step_of[i], job_of[i]
            if step is not None and step in step_wd:
                current_working_dir: Optional[str] = step_wd[step]
            elif job is not None and job in job_wd:
                current_working_dir = job_wd[job]
            else:
                current_working_dir = top_wd
            for script in _RUN_SCRIPT_RE.findall(text):
                if _EXPRESSION_RE.search(script) or script.startswith("-"):
                    continue
                # A step may cd first (`(cd e2e && node probe.js)`) or carry a working-directory,
                # so the path is relative to THAT, not to the repository root. Resolving only
                # against the root reported healthy steps as broken.
                bases = [repo_root]
                bases.extend(repo_root / cd_dir for cd_dir in _CD_RE.findall(text))
                if current_working_dir:
                    bases.append(repo_root / current_working_dir)
                if any((base / script).exists() for base in bases):
                    continue
                problems.append(f"{rel}:{i + 1}: runs `{script}`, which does not exist in the repository. A " f"guard that is not there is not coverage.")

        if require_permissions and not any(_PERMISSIONS_RE.match(line_) for line_ in lines):
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
                if ref.startswith("docker://"):
                    if _DOCKER_DIGEST_RE.match(ref):
                        continue
                    problems.append(f"{rel}:{i}: `uses: {ref}` names a docker image by a mutable tag. Pin it by digest " f"(`docker://image@sha256:<digest>`).")
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
