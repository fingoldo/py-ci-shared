"""Shared check: a git hook fails loudly, stages nothing of its own, and runs what CI runs.

A pre-commit/pre-push hook is the only gate most changes ever meet on a developer machine, and it
fails in ways that look exactly like success. Four rules, all found for real in the 2026-09-02
glossum / flutter_app_core audit round:

1. **Silent skip.** ``[ -f tool/check-x.sh ] && sh tool/check-x.sh`` passes when the file is gone.
   A guard that was renamed, moved to another repo, or never committed then reads as coverage
   forever (glossum P04-8). Require an ``else`` branch that exits non-zero or prints ``WARNING`` /
   ``ERROR``.

2. **The hook stages files the author did not.** ``git add -u`` / ``git add -A`` inside a
   pre-commit hook sweeps unrelated working-tree edits into the commit under review, which is how a
   half-finished change ships attached to an unrelated one (glossum P04-6). A formatter hook may
   re-stage only the paths that were already staged.

3. **Exit codes read from text.** ``flutter analyze | grep "error -"`` (or any ``grep`` over a
   tool's human-readable output) decides the verdict from a message format the tool is free to
   change; the tool's own exit code is the verdict (glossum P04-7, core C03-21). Flagged as an
   advisory-strength rule because a few greps over output are legitimate (counting, extracting).

4. **Hook-vs-CI parity.** Every ``tool/check*`` script a hook runs should also run in CI, or be
   listed in a manual-only file with a reason -- otherwise the gate exists only on the machine of
   whoever wrote it, and a ``--no-verify`` or a CI-only contributor bypasses it silently (glossum
   P04-9, 36 guards; core C03-14 in the other direction).

Deliberately regex/line-based over the hook text, no shell parser, matching this package's other
scanners. Language-agnostic: hooks are shell whatever the project is written in.

Usage::

    from py_ci_shared.hook_hygiene import assert_hooks_are_honest

    def test_git_hooks_fail_loudly():
        assert_hooks_are_honest(
            REPO / ".githooks",
            workflows_dir=REPO / ".github" / "workflows",
            manual_only_file=REPO / "tool" / "manual-only-guards.txt",
        )
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

# `[ -f x ] && cmd`, `[ -x x ] && cmd`, `command -v x >/dev/null && cmd` on one line: the `&&` form
# has no else branch at all, so a missing file is indistinguishable from a passing check.
_INLINE_GUARD_RE = re.compile(r"^\s*(?:\[\s*-[fxe]\s+[^\]]+\]|command\s+-v\s+\S+[^&|]*)\s*&&\s*\S")
# `if [ -f x ]; then ... fi` -- acceptable only when the block has an else that fails or warns.
_IF_GUARD_RE = re.compile(r"^\s*if\s+(?:\[\s*-[fxe]\s+|command\s+-v\s+)")
_ELSE_RE = re.compile(r"^\s*else\b")
_FI_RE = re.compile(r"^\s*fi\b")
_LOUD_RE = re.compile(r"\bexit\s+[1-9]|\bWARNING\b|\bERROR\b|\bMISSING\b", re.IGNORECASE)
_GIT_ADD_ALL_RE = re.compile(r"^\s*git\s+add\s+(-u|-A|--all|--update)\b")
_GREP_VERDICT_RE = re.compile(r"""(?:analyze|lint|test|check)[^\n|]*\|\s*grep\s+[^|\n]*["'](?:error|warning)\b""")
_CHECK_SCRIPT_RE = re.compile(r"(tool/[\w.-]*check[\w.-]*\.(?:sh|py))")
# The path a `[ -f ... ]` / `if [ -f ... ]` conditional is testing for.
_GUARDED_PATH_RE = re.compile(r"\[\s*-[fxe]\s+([^\]]+?)\s*\]")
# A runner that ENUMERATES guards rather than listing them one by one:
# `for candidate in tool/check-*.sh`. Verified, not trusted - see the parity rule below.
_GUARD_GLOB_RE = re.compile(r"tool/check[-_]?\*|tool/check[\w-]*\*")


def _hook_files(hooks_dir: Path) -> list[Path]:
    if not hooks_dir.is_dir():
        return []
    return sorted(p for p in hooks_dir.iterdir() if p.is_file() and not p.name.endswith(".sample"))


def find_hook_hygiene_problems(
    hooks_dir: Path,
    *,
    repo_root: "Path | None" = None,
    workflows_dir: "Path | None" = None,
    manual_only_file: "Path | None" = None,
    runner_scripts: Sequence[str] = (),
    check_grep_verdicts: bool = True,
) -> list[str]:
    """Return one problem string per silent skip, self-staging hook, text-derived verdict, or
    hook-only guard. See the module docstring for what each rule is protecting against."""
    hooks = _hook_files(hooks_dir)
    if not hooks:
        return [f"{hooks_dir}: no hook files found - this check examined nothing, which reads as a " f"pass. Point it at the directory `core.hooksPath` names."]

    problems: list[str] = []
    hook_guards: set[str] = set()

    for path in hooks:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            guarded = _GUARDED_PATH_RE.search(line)
            guard_exists = False
            if repo_root and guarded:
                # Hooks write `"$PROJECT_ROOT/tool/check-x.sh"`; the variable is the repo root,
                # so strip it before resolving rather than reporting a healthy guard as missing.
                candidate = guarded.group(1).strip("\"'")
                candidate = re.sub(r"^\$\{?\w+\}?/", "", candidate)
                guard_exists = (repo_root / candidate).exists()
            if _INLINE_GUARD_RE.match(line) and "check" in line and not guard_exists:
                problems.append(
                    f"{path.name}:{i}: `{stripped[:90]}` - a `&&` guard with no else branch. When "
                    f"the file is missing the hook passes, so a deleted or renamed guard reads as "
                    f"coverage forever. Fail (or print a WARNING) in the missing case."
                )
            if _IF_GUARD_RE.match(line) and "check" in line and not guard_exists:
                block: list[str] = []
                for follow in lines[i - 1 :]:
                    block.append(follow)
                    if _FI_RE.match(follow) and len(block) > 1:
                        break
                joined = "\n".join(block)
                has_else = any(_ELSE_RE.match(b) for b in block)
                if not has_else or not _LOUD_RE.search(joined.split("else", 1)[-1]):
                    problems.append(
                        f"{path.name}:{i}: `if [ -f ... ]` around a guard with no else that fails "
                        f"or warns. A missing guard must be louder than a passing one."
                    )
            if _GIT_ADD_ALL_RE.match(line):
                problems.append(
                    f"{path.name}:{i}: `{stripped}` stages every modified file, sweeping unrelated "
                    f"working-tree edits into the commit under review. Re-stage only paths that "
                    f"were already staged (`git diff --cached --name-only`)."
                )
            if check_grep_verdicts and _GREP_VERDICT_RE.search(line):
                problems.append(
                    f"{path.name}:{i}: `{stripped[:90]}` derives the verdict from the tool's "
                    f"human-readable output. The exit code is the verdict; a message-format change "
                    f"silently turns this gate off."
                )
            hook_guards.update(_CHECK_SCRIPT_RE.findall(line))

    if workflows_dir is not None and workflows_dir.is_dir():
        ci_text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(workflows_dir.glob("*.y*ml")))
        ci_guards = set(_CHECK_SCRIPT_RE.findall(ci_text))
        manual: set[str] = set()
        if manual_only_file is not None and manual_only_file.is_file():
            for line in manual_only_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name = line.split("#", 1)[0].strip()
                if name:
                    manual.add(name if name.startswith("tool/") else f"tool/{name}")
        # A workflow may run every guard through ONE runner instead of naming each. That is the
        # better shape, and a text comparison cannot see it - so the claim is checked rather than
        # trusted: the named runner must exist AND discover guards by glob, or it is not standing
        # in for anything and the guards really are hook-only.
        runner_covers_everything = False
        for runner in runner_scripts:
            if runner not in ci_text:
                continue
            runner_path = (repo_root / runner) if repo_root else Path(runner)
            if not runner_path.is_file():
                problems.append(f"CI runs {runner}, which does not exist - the guards it was supposed to run " f"are running nowhere.")
                continue
            if _GUARD_GLOB_RE.search(runner_path.read_text(encoding="utf-8", errors="replace")):
                runner_covers_everything = True
            else:
                problems.append(
                    f"CI runs {runner}, but that script does not discover guards by glob, so it " f"cannot be standing in for the ones CI never names."
                )

        hook_only = (
            []
            if runner_covers_everything
            else sorted(g for g in hook_guards - ci_guards if g not in manual and Path(g).name not in {Path(m).name for m in manual})
        )
        if hook_only:
            problems.append(
                f"{len(hook_only)} guard(s) run on the hook but never in CI, and are not listed in "
                f"{manual_only_file.name if manual_only_file else 'a manual-only file'}: a gate "
                f"that lives only on one machine is bypassed by --no-verify and by every "
                f"contributor who pushes from another:\n    " + "\n    ".join(hook_only)
            )
    return problems


def assert_hooks_are_honest(
    hooks_dir: Path,
    *,
    repo_root: "Path | None" = None,
    workflows_dir: "Path | None" = None,
    manual_only_file: "Path | None" = None,
    runner_scripts: Sequence[str] = (),
    check_grep_verdicts: bool = True,
) -> None:
    """Fail on a silent skip, a self-staging hook, a text-derived verdict, or a hook-only guard."""
    import pytest

    problems = find_hook_hygiene_problems(
        hooks_dir,
        repo_root=repo_root,
        workflows_dir=workflows_dir,
        manual_only_file=manual_only_file,
        runner_scripts=runner_scripts,
        check_grep_verdicts=check_grep_verdicts,
    )
    if problems:
        pytest.fail(f"{len(problems)} hook hygiene problem(s):\n  " + "\n  ".join(problems))
