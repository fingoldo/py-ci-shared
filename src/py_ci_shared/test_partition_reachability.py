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

from ._core import DEFAULT_EXCLUDE, SourceError, iter_files, read_source

# `name:` sits inside an object literal on the same line as the brace in most configs, so this
# is deliberately not anchored to the line start.
_PW_PROJECT_NAME_RE = re.compile(r"\bname:\s*['\"]([^'\"]+)['\"]")
_FLAG_VALUE = r"(?:=|\s+)(?:\"([^\"]*)\"|'([^']*)'|([^\s'\"]+))"
_INCLUDE_TAG_RE = re.compile(r"(?<![\w-])--tags" + _FLAG_VALUE)
_EXCLUDE_TAG_RE = re.compile(r"(?<![\w-])--exclude-tags" + _FLAG_VALUE)
# `-t` / `-x` are dart test's short spellings; they are read only on a `dart test` / `flutter test` line, since other
# tools use the same letters for unrelated things (`docker build -t`).
_SHORT_INCLUDE_RE = re.compile(r"(?<![\w-])-t" + _FLAG_VALUE)
_SHORT_EXCLUDE_RE = re.compile(r"(?<![\w-])-x" + _FLAG_VALUE)
_DART_TEST_LINE_RE = re.compile(r"\b(?:dart|flutter)\s+test\b")
_PROJECT_SELECT_RE = re.compile(r"(?<![\w-])--project" + _FLAG_VALUE)
_PLAYWRIGHT_TEST_RE = re.compile(r"\bplaywright\s+test\b")
# A disabled test or suite: `test.skip('name', ...)`, `test.describe.skip(...)`, `test.fixme('name', ...)`, `xit(...)`,
# `xdescribe(...)`, `xtest(...)`. `test.skip(cond, 'reason')` inside a test is a platform guard - Chromium-only CDP,
# a browser without service workers - and flagging those buries the real ones, so the first argument must be a title.
_SKIP_RE = re.compile(r"^\s*(?:(?:test|describe|it)(?:\.describe)?(?:\.serial|\.parallel)?\.(?:skip|fixme)|xit|xdescribe|xtest|xcontext)\(\s*['\"`]")
_SCRIPT_SUFFIXES = (".mjs", ".js", ".ts", ".py", ".sh")


def _flag_values(pattern: "re.Pattern[str]", text: str) -> set[str]:
    """Every tag named by *pattern*'s flag, split on anything that is not part of a tag name (``a,b``, ``a || b``)."""
    out: set[str] = set()
    for groups in pattern.findall(text):
        value = next((g for g in groups if g), "")
        out.update(t for t in re.split(r"[^\w-]+", value) if t)
    return out


def _logical_lines(text: str) -> list[str]:
    """Lines with shell continuations joined and ``#`` comment lines dropped: a flag on a continuation line belongs
    to the command above it, and a commented-out command runs nothing."""
    joined = re.sub(r"\\[ \t]*\r?\n", " ", text)
    return [line for line in joined.splitlines() if not line.lstrip().startswith("#")]


def _read(paths: Iterable[Path]) -> str:
    out: list[str] = []
    for p in paths:
        if p.is_file():
            out.append(read_source(p))
        elif p.is_dir():
            out.extend(read_source(child) for child in iter_files(p, ("*",)) if child.suffix in {".yml", ".yaml", ".sh", ".md", ""})
        else:
            raise FileNotFoundError(f"runner path does not exist: {p}")
    return "\n".join(out)


def declared_tags(declared_tags_file: Path) -> list[str]:
    """Tag names under the top-level ``tags:`` map of a ``dart_test.yaml``, at any indentation (parsed as YAML)."""
    import yaml

    data = yaml.safe_load(read_source(declared_tags_file)) or {}
    tags = data.get("tags") if isinstance(data, dict) else None
    if tags is None:
        return []
    if isinstance(tags, dict):
        return [str(t) for t in tags]
    if isinstance(tags, list):
        return [str(t) for t in tags]
    raise ValueError(f"{declared_tags_file}: `tags` is neither a map nor a list")


def find_unreachable_tags(declared_tags_file: Path, runner_text: str) -> list[str]:
    """Return every tag declared in a ``dart_test.yaml``-shaped ``tags:`` map that no runner
    selects, and that at least one runner excludes. A missing file raises ``FileNotFoundError``."""
    if not declared_tags_file.is_file():
        raise FileNotFoundError(f"declared tags file does not exist: {declared_tags_file}")
    declared = declared_tags(declared_tags_file)
    lines = _logical_lines(runner_text)
    text = "\n".join(lines)
    dart_lines = "\n".join(line for line in lines if _DART_TEST_LINE_RE.search(line))
    included = _flag_values(_INCLUDE_TAG_RE, text) | _flag_values(_SHORT_INCLUDE_RE, dart_lines)
    excluded = _flag_values(_EXCLUDE_TAG_RE, text) | _flag_values(_SHORT_EXCLUDE_RE, dart_lines)
    return [t for t in declared if t not in included and t in excluded]


def find_unselected_projects(playwright_config: Path, runner_text: str) -> list[str]:
    """Return every Playwright project name that no runner selects with ``--project``.

    A config whose projects are all selected implicitly (no ``--project`` anywhere) is fine: that
    means every project runs. The failure this catches is a runner that names *some* projects, so
    the unnamed ones silently never run. A ``playwright test`` invocation with no ``--project`` (continuations
    joined, comments ignored) runs them all. A missing config raises ``FileNotFoundError``.
    """
    if not playwright_config.is_file():
        raise FileNotFoundError(f"playwright config does not exist: {playwright_config}")
    names = _PW_PROJECT_NAME_RE.findall(read_source(playwright_config))
    lines = _logical_lines(runner_text)
    selected = _flag_values_exact(_PROJECT_SELECT_RE, "\n".join(lines))
    if not selected or any(_PLAYWRIGHT_TEST_RE.search(line) and not _PROJECT_SELECT_RE.search(line) for line in lines):
        return []
    return [n for n in names if n not in selected]


def _flag_values_exact(pattern: "re.Pattern[str]", text: str) -> set[str]:
    return {next((g for g in groups if g), "") for groups in pattern.findall(text)} - {""}


def _referenced(name: str, reference_text: str) -> bool:
    return re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])", reference_text) is not None


def find_unreferenced_scripts(
    script_dirs: Sequence[Path],
    reference_text: str,
    *,
    skip_dir_names: Sequence[str] = ("tests", "node_modules", "test-results", "playwright-report"),
    skip_name_patterns: Sequence[str] = (".config.", ".test.", ".spec."),
) -> list[str]:
    """Return every standalone script under ``script_dirs`` whose file name appears, as a whole name, in no
    workflow, hook or index document (``prerun.py`` does not reference ``run.py``). *skip_dir_names* are matched
    below each script dir only, never against the directories above it. A missing dir raises ``FileNotFoundError``."""
    out: list[str] = []
    for d in script_dirs:
        if not d.is_dir():
            raise FileNotFoundError(f"script dir does not exist: {d}")
        for path in iter_files(d, ("*",), exclude=DEFAULT_EXCLUDE):
            if path.suffix not in _SCRIPT_SUFFIXES:
                continue
            if any(part in skip_dir_names for part in path.relative_to(d).parts[:-1]):
                continue
            # A config or a test file is not a standalone script somebody has to remember to run.
            if any(marker in path.name for marker in skip_name_patterns):
                continue
            if not _referenced(path.name, reference_text):
                out.append(path.relative_to(d.parent).as_posix())
    return out


def find_permanent_skips(spec_dirs: Sequence[Path]) -> list[str]:
    """Return every disabled test or suite in a spec file: ``test.skip('title'``, ``test.describe.skip(``,
    ``test.fixme('title'``, ``xit(``, ``xdescribe(``, ``xtest(``. A missing dir raises ``FileNotFoundError``."""
    out: list[str] = []
    for d in spec_dirs:
        if not d.is_dir():
            raise FileNotFoundError(f"spec dir does not exist: {d}")
        for path in iter_files(d, ("*.spec.*",)):
            for i, line in enumerate(read_source(path).splitlines(), start=1):
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

    ``allowed`` maps a tag / project / script name to the REASON it is deliberately unreachable; an empty reason,
    and an entry that no longer excuses anything, fail too. Every path given must exist: a typo'd path is a
    finding, never a clean pass.
    """
    import pytest

    allowed = dict(allowed or {})
    problems: list[str] = []
    missing = [
        str(p)
        for p in [*runner_texts, *(x for x in (declared_tags, playwright_config, script_index) if x is not None), *script_dirs, *spec_dirs]
        if not p.exists()
    ]
    if missing:
        pytest.fail("path(s) given to assert_partitions_reachable do not exist, so nothing was checked against them:\n  " + "\n  ".join(missing))
    try:
        runner_text = _read(runner_texts)
        reference_text = runner_text + ("\n" + read_source(script_index) if script_index is not None else "")
    except SourceError as exc:
        pytest.fail(f"cannot read a runner or index file: {exc}")
    used: set[str] = set()

    def excused(name: str) -> bool:
        if name in allowed:
            used.add(name)
            return True
        return False

    if declared_tags is not None:
        for tag in find_unreachable_tags(declared_tags, runner_text):
            if excused(tag):
                continue
            problems.append(f"test tag {tag!r} is excluded by a runner and selected by none - the suite it " f"labels runs nowhere.")
    if playwright_config is not None:
        for name in find_unselected_projects(playwright_config, runner_text):
            if excused(name):
                continue
            problems.append(
                f"Playwright project {name!r} is declared but no runner passes --project for it, "
                f"while other projects are named explicitly - this viewport/engine never runs."
            )
    for script in find_unreferenced_scripts(list(script_dirs), reference_text):
        if excused(Path(script).name):
            continue
        problems.append(
            f"{script} is referenced by no workflow, hook or index document - nobody knows it "
            f"exists or how to invoke it. Add it to the script index, or delete it."
        )
    problems.extend(f"{skip} - a permanently skipped spec reads as a passing suite." for skip in find_permanent_skips(list(spec_dirs)))
    problems.extend(
        f"allowed entry {name!r} has no reason; say why it is deliberately unreachable"
        for name, reason in sorted(allowed.items())
        if not str(reason or "").strip()
    )
    problems.extend(f"allowed entry {name!r} no longer excuses anything - remove it" for name in sorted(set(allowed) - used))

    if problems:
        pytest.fail(f"{len(problems)} unreachable test partition(s):\n  " + "\n  ".join(problems))
