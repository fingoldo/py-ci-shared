"""Shared check: every declared test partition is actually selected by some runner.

``ci_test_dir_reachability`` answers this for pytest directories. The same failure has three other
shapes, all found for real in the 2026-09-02 glossum audit round, and all of them read as coverage:

* **Tag partitions** (``dart_test.yaml`` tags, pytest markers). A tag every runner passes
  ``--exclude-tags`` for and no runner ever passes ``--tags`` for is a suite that never runs
  anywhere. glossum P04-13 / flutter_app_core C03-16: a ``benchmark`` tag excluded by the hook and
  by CI, included by nothing, so the benchmark suite had not executed in months.
* **Named projects** (Playwright ``projects[].name``, tox envs). glossum P04-10: four of the seven
  Playwright projects were never named by any ``--project=`` in any workflow, so the mobile and
  webkit viewports were declared and never exercised.
* **Standalone scripts** next to the suites (``e2e/*.mjs``, ``tool/*.py`` diagnostics) referenced by
  no workflow, hook or document. glossum P04-17: nine such scripts, several written for an incident
  and never run again, with nothing telling the next reader they exist or how to invoke them.

Plus the blunt one: a permanent top-level ``test.skip(``/``describe.skip(`` in a spec file.

Each rule is a set difference over regex hits, with an allowlist that requires a reason. Deliberately
dependency-free and language-agnostic: the "declared here, selected nowhere" shape is identical for
Dart tags, pytest markers, Playwright projects and tox environments.

Usage::

    from py_ci_shared.test_partition_reachability import assert_partitions_reachable

    def test_every_declared_partition_runs_somewhere():
        assert_partitions_reachable(
            runner_texts=[*(REPO / ".github" / "workflows").glob("*.yml"), REPO / ".githooks" / "pre-push"],
            declared_tags=REPO / "dart_test.yaml",
            playwright_config=REPO / "e2e" / "playwright.config.ts",
            script_dirs=[REPO / "e2e"],
            script_index=REPO / "e2e" / "SCRIPTS.md",
        )
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

_YAML_TAG_RE = re.compile(r"^  (?P<tag>[\w-]+):\s*(?:#.*)?$")
_TAGS_HEADER_RE = re.compile(r"^tags:\s*(?:#.*)?$")
# `name:` sits inside an object literal on the same line as the brace in most configs, so this
# is deliberately not anchored to the line start.
_PW_PROJECT_NAME_RE = re.compile(r"\bname:\s*['\"]([^'\"]+)['\"]")
_INCLUDE_TAG_RE = re.compile(r"--tags[=\s]+([\w,-]+)")
_EXCLUDE_TAG_RE = re.compile(r"--exclude-tags[=\s]+([\w,-]+)")
_PROJECT_SELECT_RE = re.compile(r"--project[=\s]+['\"]?([\w-]+)")
_SKIP_RE = re.compile(r"^\s*(?:test|describe|it)\.skip\(")
_SCRIPT_SUFFIXES = (".mjs", ".js", ".ts", ".py", ".sh")


def _read(paths: Iterable[Path]) -> str:
    out: list[str] = []
    for p in paths:
        if p.is_file():
            out.append(p.read_text(encoding="utf-8", errors="replace"))
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix in {".yml", ".yaml", ".sh", ".md", ""}:
                    out.append(child.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


def find_unreachable_tags(declared_tags_file: Path, runner_text: str) -> list[str]:
    """Return every tag declared in a ``dart_test.yaml``-shaped ``tags:`` map that no runner
    selects, and that at least one runner excludes."""
    if not declared_tags_file.is_file():
        return []
    lines = declared_tags_file.read_text(encoding="utf-8", errors="replace").splitlines()
    declared: list[str] = []
    in_tags = False
    for line in lines:
        if _TAGS_HEADER_RE.match(line):
            in_tags = True
            continue
        if in_tags:
            if line and not line[:1].isspace():
                in_tags = False
                continue
            m = _YAML_TAG_RE.match(line)
            if m:
                declared.append(m.group("tag"))
    included = {t for group in _INCLUDE_TAG_RE.findall(runner_text) for t in group.split(",")}
    excluded = {t for group in _EXCLUDE_TAG_RE.findall(runner_text) for t in group.split(",")}
    return [t for t in declared if t not in included and t in excluded]


def find_unselected_projects(playwright_config: Path, runner_text: str) -> list[str]:
    """Return every Playwright project name that no runner selects with ``--project``.

    A config whose projects are all selected implicitly (no ``--project`` anywhere) is fine: that
    means every project runs. The failure this catches is a runner that names *some* projects, so
    the unnamed ones silently never run.
    """
    if not playwright_config.is_file():
        return []
    names = _PW_PROJECT_NAME_RE.findall(playwright_config.read_text(encoding="utf-8", errors="replace"))
    selected = set(_PROJECT_SELECT_RE.findall(runner_text))
    if not selected:
        return []
    return [n for n in names if n not in selected]


def find_unreferenced_scripts(
    script_dirs: Sequence[Path],
    reference_text: str,
    *,
    skip_dir_names: Sequence[str] = ("tests", "node_modules", "test-results", "playwright-report"),
) -> list[str]:
    """Return every standalone script under ``script_dirs`` whose file name appears in no
    workflow, hook or index document."""
    out: list[str] = []
    for d in script_dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*")):
            if not path.is_file() or path.suffix not in _SCRIPT_SUFFIXES:
                continue
            if any(part in skip_dir_names for part in path.parts):
                continue
            if path.name not in reference_text:
                out.append(path.relative_to(d.parent).as_posix())
    return out


def find_permanent_skips(spec_dirs: Sequence[Path]) -> list[str]:
    """Return every top-level ``test.skip(`` / ``describe.skip(`` in a spec file."""
    out: list[str] = []
    for d in spec_dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.spec.*")):
            for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if _SKIP_RE.match(line):
                    out.append(f"{path.name}:{i}: {line.strip()[:80]}")
    return out


def assert_partitions_reachable(
    *,
    runner_texts: Sequence[Path],
    declared_tags: "Path | None" = None,
    playwright_config: "Path | None" = None,
    script_dirs: Sequence[Path] = (),
    script_index: "Path | None" = None,
    spec_dirs: Sequence[Path] = (),
    allowed: "Mapping[str, str] | None" = None,
) -> None:
    """Fail when a declared tag, project, script or skipped spec is never selected or referenced.

    ``allowed`` maps a tag / project / script name to the REASON it is deliberately unreachable.
    """
    import pytest

    allowed = dict(allowed or {})
    runner_text = _read(runner_texts)
    reference_text = runner_text + ("\n" + script_index.read_text(encoding="utf-8", errors="replace") if script_index and script_index.is_file() else "")

    problems: list[str] = []
    if declared_tags is not None:
        for tag in find_unreachable_tags(declared_tags, runner_text):
            if tag in allowed:
                continue
            problems.append(f"test tag {tag!r} is excluded by a runner and selected by none - the suite it " f"labels runs nowhere.")
    if playwright_config is not None:
        for name in find_unselected_projects(playwright_config, runner_text):
            if name in allowed:
                continue
            problems.append(
                f"Playwright project {name!r} is declared but no runner passes --project for it, "
                f"while other projects are named explicitly - this viewport/engine never runs."
            )
    for script in find_unreferenced_scripts(list(script_dirs), reference_text):
        if Path(script).name in allowed:
            continue
        problems.append(
            f"{script} is referenced by no workflow, hook or index document - nobody knows it "
            f"exists or how to invoke it. Add it to the script index, or delete it."
        )
    for skip in find_permanent_skips(list(spec_dirs)):
        problems.append(f"{skip} - a permanently skipped spec reads as a passing suite.")

    if problems:
        pytest.fail(f"{len(problems)} unreachable test partition(s):\n  " + "\n  ".join(problems))
