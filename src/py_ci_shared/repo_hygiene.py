"""Shared check: the repository tracks nothing it generates, and carries the files its gates need.

Small, boring rules that each cost one line to satisfy and are invisible until they bite:

1. **No generated artefacts tracked.** ``__pycache__/``, ``*.pyc``, ``.dart_tool/``, ``node_modules/``
   and coverage output in ``git ls-files`` mean every contributor's byte-compiled cache lands in
   diffs and merge conflicts. Found on 2026-09-02 as flutter_app_core C03-18 (two ``__pycache__``
   files committed alongside the tooling that generates them).

2. **Required files exist.** A repo-specific list: a linter config (``analysis_options.yaml`` for
   Dart, ``pyproject.toml`` for Python), a non-empty ``.github/workflows/``, a ``.gitignore``.
   flutter_app_core C03-2 was exactly this: the package had no ``analysis_options.yaml``, so
   ``flutter analyze`` ran with the SDK defaults and the lint set the consuming app enforced was
   simply absent from the package everything else depends on.

3. **A numeric CI gate cannot pass on an empty value.** ``if (( $(echo "$COV < 80" | bc) ))``
   evaluates to *pass* when ``$COV`` is empty because the parse failed -- the gate reports success
   precisely when its input broke. Require an explicit emptiness check (``[ -n "$VAR" ]``,
   ``: "${VAR:?}"``) in any block that compares a shell variable numerically. Found as glossum
   P04-5, where a coverage percentage that failed to parse read as "coverage fine".

4. **No NUL byte in a text file.** ``grep`` classifies such a file as binary and prints
   ``Binary file ... matches`` instead of the matches, so every text search over that tree skips it
   and says nothing. Found 2026-09-09 as production_scrapers CQ-35: an audit file had held one for
   eight days, hiding seven findings of a closed round from every search. The byte arrived inside a
   sentence *about* NUL stripping -- the four characters spelling the escape written as the byte --
   and the same slip was made twice that week, the other time in a code comment about NUL handling.
   The shape is easy to produce precisely when the subject makes it plausible.

   Scoped to text suffixes on purpose. In a ``.py`` file a NUL fails loudly (``SyntaxError: source
   code string cannot contain null bytes``, at import), so the interpreter already catches it; the
   rule earns its place on ``.md``/``.sql``/``.toml``/``.txt``, where it is silent.

5. **No UTF-8 BOM.** Measured rather than assumed: ``json.loads`` raises ``Unexpected UTF-8 BOM``
   and ``tomllib.loads`` raises ``Invalid statement (at line 1, column 1)``, so a BOM is a HARD
   parse failure in the two formats a repo keeps its baselines and config in. Python source
   tolerates it (the interpreter accepts ``utf-8-sig``), and in Markdown it is quieter but not
   harmless: the first line stops starting with what it appears to start with, so
   ``line.startswith("#")`` is False, ``grep '^# '`` misses the title, and a shebang check on a
   BOM'd script fails. Found 2026-09-09 as dashboard's ``audits/2026-09-03/03_performance.md``:
   one file in 1,700, three bytes, and ``grep '^# '`` went from finding one heading to two.

6. **No ``.py`` or ``.sql`` inside an ``audits/`` folder.** An audit folder holds findings; the
   runbook goes in ``sql/``, the probe in ``probes/``, the script in ``scripts/``, and the finding
   links to it. Found 2026-09-11 in social: a deliberate 09-04 ``DROP INDEX`` runbook sat in
   ``audits/2026-09-01-full-audit/``, its conclusion never reached the finding, and a later
   ``CREATE`` runbook in ``sql/`` had the operator rebuild the index it had dropped. Eight such
   files across three projects; the owner's rule since. Opt out with
   ``audit_dirs_hold_no_scripts=False`` only for a repo whose ``audits`` is a code package.

Deliberately dependency-free (``git ls-files`` via subprocess, regex over workflow text), and
language-agnostic: rules 1, 3, 4, 5 and 6 fire on any repo, rule 2 takes the caller's own list.

Usage::

    from py_ci_shared.repo_hygiene import assert_repo_hygiene

    def test_repo_hygiene():
        assert_repo_hygiene(
            REPO,
            required_files=("analysis_options.yaml", ".gitignore"),
            workflows_dir=REPO / ".github" / "workflows",
        )
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from ._core import DEFAULT_EXCLUDE, iter_files

#: Generated-artefact patterns, matched on path COMPONENTS, never as substrings (see :func:`matches_generated_pattern`).
_DEFAULT_GENERATED_PATTERNS: tuple[str, ...] = (
    "__pycache__/",
    ".pyc",
    ".dart_tool/",
    "node_modules/",
    ".pytest_cache/",
    "/build/",
    ".coverage",
    ".coverage.*",
    "coverage/lcov.info",
)
_RUN_LINE_RE = re.compile(r"^\s*(?:-\s*)?run:\s*(?P<first>.*)$")
# `$X`, `${X}` and `${X%\%}`-style expansions (a modifier after the name still expands X).
_VAR = r"""\$\{{?(?P<{g}>\w+)(?:[%#:/^,][^}}]*)?\}}?"""
_CMP = r"(?:<=|>=|<|>)"
_TEST_OP = r"-(?:lt|gt|le|ge|eq|ne)"
# A numeric comparison of a shell variable: `$X < 80` inside bc, `[ "$X" -lt 80 ]`, `test $X -lt 80`, `(( X < 80 ))`,
# with the variable on either side.
_NUMERIC_COMPARE_RE = re.compile(
    rf"""(?:{_VAR.format(g="bc")}[\"']?\s*{_CMP}\s*[\d.]+)"""
    rf"""|(?:[\d.]+\s*{_CMP}\s*[\"']?{_VAR.format(g="bc2")})"""
    rf"""|(?:(?:\[\[?|\btest)\s*[\"']?{_VAR.format(g="test")}[\"']?\s+{_TEST_OP}\s+)"""
    rf"""|(?:(?:\[\[?|\btest)\s*[\"']?[\d.]+[\"']?\s+{_TEST_OP}\s+[\"']?{_VAR.format(g="test2")})"""
    rf"""|(?:\(\(\s*\$?\{{?(?P<arith>\w+)\}}?\s*{_CMP}\s*[\d.]+)"""
)
# What proves VAR non-empty before it is compared: `[ -n "$VAR" ]`, `[[ -z $VAR ]]`, `test -n "$VAR"`, `: "${VAR:?}"`,
# `${VAR:-<non-empty default>}`, `[ "$VAR" = "" ]`. An EMPTY default (`${VAR:-}`) proves nothing.
_EMPTINESS_GUARD_TMPL = (
    r"""(?:\[\[?|\btest)\s+-[nz]\s+[\"']?\$\{{?{var}\}}?[\"']?"""
    r"""|\$\{{{var}:\?"""
    r"""|\$\{{{var}:-[^}}\s\"']"""
    r"""|(?:\[\[?|\btest)\s+[\"']?\$\{{?{var}\}}?[\"']?\s*==?\s*[\"']{{2}}"""
)


def tracked_files(repo_root: Path) -> list[str]:
    """Tracked paths as git stores them: ``-z`` so a non-ASCII name is not C-quoted (``"audits/\320\277..."``)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - environment
        raise RuntimeError(f"git ls-files failed in {repo_root}: {exc}") from exc
    return [rel for rel in out.decode("utf-8", errors="surrogateescape").split("\0") if rel]


_tracked_files = tracked_files


def matches_generated_pattern(rel: str, pattern: str) -> bool:
    """Does repo-relative *rel* match a generated-artefact *pattern*? Matching is by path component:

    * ``name/`` -- a directory component equal to ``name`` at any depth; ``/name/`` -- only at the repo root;
    * ``a/b`` -- the path ends with the components ``a/b`` (``/a/b`` anchors it at the root);
    * no ``/`` -- the file name: a glob when it has ``*?[``, otherwise equal to it or ending with it (``.pyc``).

    So ``.coverage`` matches ``.coverage`` but not ``.coveragerc``, and ``/build/`` does not match a package that
    happens to be named ``build`` below the root.
    """
    parts = rel.replace("\\", "/").split("/")
    if "/" not in pattern:
        name = parts[-1]
        if any(c in pattern for c in "*?["):
            return fnmatch.fnmatchcase(name, pattern)
        return name == pattern or name.endswith(pattern)
    anchored = pattern.startswith("/")
    is_dir = pattern.endswith("/")
    wanted = [p for p in pattern.strip("/").split("/") if p]
    if not wanted:
        return False
    haystack = parts[:-1] if is_dir else parts
    n = len(wanted)
    if anchored:
        return haystack[:n] == wanted
    if is_dir:
        return any(haystack[i : i + n] == wanted for i in range(len(haystack) - n + 1))
    return haystack[-n:] == wanted


def find_tracked_generated_files(
    repo_root: Path,
    patterns: Sequence[str] = _DEFAULT_GENERATED_PATTERNS,
) -> list[str]:
    """Return every tracked path matching one of ``patterns`` (a generated artefact); see :func:`matches_generated_pattern`."""
    hits: list[str] = []
    for rel in _tracked_files(repo_root):
        for pattern in patterns:
            if matches_generated_pattern(rel, pattern):
                hits.append(f"{rel} (matches {pattern!r})")
                break
    return hits


def find_missing_required_files(repo_root: Path, required_files: Iterable[str]) -> list[str]:
    """Return every entry of ``required_files`` that does not exist under ``repo_root``."""
    return [name for name in required_files if not (repo_root / name).exists()]


#: Suffixes something reads as TEXT. A list rather than "anything not known-binary": a new binary
#: fixture must not start failing this, and missing a text suffix costs coverage, not a false alarm.
DEFAULT_TEXT_SUFFIXES: "tuple[str, ...]" = (".py", ".md", ".sql", ".toml", ".yaml", ".yml", ".json", ".txt", ".ini", ".cfg", ".rst")


#: Directories no rule here should descend into. Shared by rules 4 and 5.
_DEFAULT_SKIP_DIRS: "tuple[str, ...]" = tuple(sorted(DEFAULT_EXCLUDE | {"logs", "checkpoints"}))


def _candidate_files(repo_root: Path, skip: "set[str]") -> "list[str]":
    """Repo-relative POSIX paths to consider: git's view when *repo_root* is a checkout, else a pruned walk.

    Git's view is the tracked files plus untracked ones that are NOT ignored, so a new file is checked before it is
    committed while ignored data, caches and outputs -- often ten times the repository -- are never read. Outside a
    checkout the walk prunes skipped directories instead of descending into them and filtering afterwards.
    """
    return [path.relative_to(repo_root).as_posix() for path in iter_files(repo_root, ("*",), exclude=skip)]


def _walk_text_files(repo_root: Path, text_suffixes: Sequence[str], skip_dirs: Iterable[str]) -> "list[Path]":
    """Every existing file under *repo_root* with a text suffix, outside the skipped directories."""
    skip = set(skip_dirs)
    suffixes = {suffix.lower() for suffix in text_suffixes}
    paths: "list[Path]" = []
    for rel in _candidate_files(repo_root, skip):
        parts = rel.split("/")
        if Path(parts[-1]).suffix.lower() not in suffixes or skip.intersection(parts[:-1]):
            continue
        path = repo_root / rel
        if path.is_file():  # a tracked file deleted from the working tree is still listed by git
            paths.append(path)
    return paths


def _read_bytes_or_none(path: Path) -> "bytes | None":
    """The file's bytes, or None when it cannot be read -- a vanished or unreadable file is not a finding."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _text_file_bytes(repo_root: Path, text_suffixes: Sequence[str], skip_dirs: Iterable[str]) -> "list[tuple[Path, bytes]]":
    """``(path, content)`` for every text file, each read once, so the NUL and BOM rules share one pass."""
    out: "list[tuple[Path, bytes]]" = []
    for path in _walk_text_files(repo_root, text_suffixes, skip_dirs):
        data = _read_bytes_or_none(path)
        if data is not None:
            out.append((path, data))
    return out


def _nul_problems(repo_root: Path, blobs: "list[tuple[Path, bytes]]") -> list[str]:
    return [
        f"{path.relative_to(repo_root).as_posix()} (x{blob.count(bytes([0]))}, first at byte {blob.index(bytes([0]))})"
        for path, blob in blobs
        if bytes([0]) in blob
    ]


def _bom_problems(repo_root: Path, blobs: "list[tuple[Path, bytes]]") -> list[str]:
    return [path.relative_to(repo_root).as_posix() for path, blob in blobs if blob.startswith(b"\xef\xbb\xbf")]


def find_text_files_with_nul_bytes(
    repo_root: Path,
    *,
    text_suffixes: Sequence[str] = DEFAULT_TEXT_SUFFIXES,
    skip_dirs: Iterable[str] = _DEFAULT_SKIP_DIRS,
) -> list[str]:
    """`path (xN, first at byte B)` for every text file holding a NUL byte."""
    return _nul_problems(repo_root, _text_file_bytes(repo_root, text_suffixes, skip_dirs))


def find_text_files_with_a_bom(
    repo_root: Path,
    *,
    text_suffixes: Sequence[str] = DEFAULT_TEXT_SUFFIXES,
    skip_dirs: Iterable[str] = _DEFAULT_SKIP_DIRS,
) -> list[str]:
    """Text files beginning with a UTF-8 BOM.

    Shares `DEFAULT_TEXT_SUFFIXES` with the NUL rule deliberately: both are "this file is not the
    plain text every tool assumes", and a repo that disagrees about which files are text would get
    two different answers from one setting.
    """
    return _bom_problems(repo_root, _text_file_bytes(repo_root, text_suffixes, skip_dirs))


#: Suffixes of code that must not live in an audit folder (rule 6).
AUDIT_FORBIDDEN_SUFFIXES: "tuple[str, ...]" = (".py", ".sql")


def find_scripts_in_audit_folders(repo_root: Path, suffixes: Sequence[str] = AUDIT_FORBIDDEN_SUFFIXES) -> list[str]:
    """Every TRACKED file with a script suffix under a directory named ``audits`` at any depth.

    Tracked, not walked: an untracked scratch probe on one machine is nobody else's problem, and a
    monorepo keeps several projects' ``audits/`` folders at different depths.
    """
    wanted = {s.lower() for s in suffixes}
    return [rel for rel in _tracked_files(repo_root) if "audits" in rel.replace("\\", "/").split("/")[:-1] and Path(rel).suffix.lower() in wanted]


def find_unguarded_numeric_gates(workflows_dir: Path) -> list[str]:
    """Return one problem string per workflow ``run:`` block that compares a shell variable
    numerically without first proving the variable is non-empty."""
    problems: list[str] = []
    if not workflows_dir.is_dir():
        return problems
    for path in sorted(workflows_dir.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            m = _NUMERIC_COMPARE_RE.search(line)
            if not m:
                continue
            var = m.group("bc") or m.group("bc2") or m.group("test") or m.group("test2") or m.group("arith")
            if not var or var.isdigit():
                continue
            # Look at the whole run block this line belongs to: the guard is usually a few lines up.
            start = i - 1
            while start > 0 and not _RUN_LINE_RE.match(lines[start - 1]):
                start -= 1
            block = "\n".join(lines[max(start - 1, 0) : i])
            if re.search(_EMPTINESS_GUARD_TMPL.format(var=re.escape(var)), block):
                continue
            problems.append(
                f"{path.name}:{i}: `{line.strip()[:90]}` compares ${var} numerically without "
                f"proving it is non-empty first. When the value fails to parse the comparison is "
                f"skipped or false, so the gate reports success exactly when its input broke. Add "
                f'`[ -n "${var}" ] || exit 1` (or `: "${{{var}:?}}"`) before the comparison.'
            )
    return problems


def assert_repo_hygiene(
    repo_root: Path,
    *,
    required_files: Iterable[str] = (),
    generated_patterns: Sequence[str] = _DEFAULT_GENERATED_PATTERNS,
    workflows_dir: "Path | None" = None,
    text_suffixes: Sequence[str] = DEFAULT_TEXT_SUFFIXES,
    audit_dirs_hold_no_scripts: bool = True,
) -> None:
    """Fail on tracked generated files, missing required files, a script in an audits/ folder, or an
    unguarded numeric CI gate."""
    import pytest

    problems: list[str] = []
    tracked = find_tracked_generated_files(repo_root, generated_patterns)
    if tracked:
        problems.append(
            f"{len(tracked)} generated file(s) are tracked by git - they land in every diff and conflict on every merge:\n    " + "\n    ".join(tracked[:20])
        )
    blobs = _text_file_bytes(repo_root, text_suffixes, _DEFAULT_SKIP_DIRS)
    nul_files = _nul_problems(repo_root, blobs)
    if nul_files:
        problems.append(
            f"{len(nul_files)} text file(s) contain a NUL byte. grep reports these as BINARY and "
            "prints no matches, so every text search silently skips them. If you meant the escape, "
            "write the four characters:\n    " + "\n    ".join(nul_files[:20])
        )
    bom_files = _bom_problems(repo_root, blobs)
    if bom_files:
        problems.append(
            f"{len(bom_files)} text file(s) start with a UTF-8 BOM. `json.loads` and `tomllib.loads` "
            "REJECT it outright, and in Markdown the first line stops starting with what it looks "
            "like it starts with:\n    " + "\n    ".join(bom_files[:20])
        )
    missing = find_missing_required_files(repo_root, required_files)
    if missing:
        problems.append("required file(s) missing: " + ", ".join(missing))
    if audit_dirs_hold_no_scripts:
        in_audits = find_scripts_in_audit_folders(repo_root)
        if in_audits:
            problems.append(
                f"{len(in_audits)} script(s) are tracked inside an audits/ folder. Runbooks belong in sql/, probes in "
                "probes/, scripts in scripts/, linked from the finding; beside the audit nobody looking in sql/ "
                "sees what is pending or already decided:\n    " + "\n    ".join(in_audits[:20])
            )
    if workflows_dir is not None:
        if workflows_dir.is_dir() and not list(workflows_dir.glob("*.y*ml")):
            problems.append(f"{workflows_dir} has no workflow files - this repo has no CI at all.")
        problems.extend(find_unguarded_numeric_gates(workflows_dir))
    if problems:
        pytest.fail(f"{len(problems)} repo hygiene problem(s):\n  " + "\n  ".join(problems))
