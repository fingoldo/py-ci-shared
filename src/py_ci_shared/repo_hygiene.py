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

import re
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

_DEFAULT_GENERATED_PATTERNS: tuple[str, ...] = (
    "__pycache__/",
    ".pyc",
    ".dart_tool/",
    "node_modules/",
    ".pytest_cache/",
    "/build/",
    ".coverage",
    "coverage/lcov.info",
)
_RUN_LINE_RE = re.compile(r"^\s*(?:-\s*)?run:\s*(?P<first>.*)$")
# A numeric comparison of a shell variable: `$X < 80` inside bc, `[ "$X" -lt 80 ]`, `(( X < 80 ))`.
_NUMERIC_COMPARE_RE = re.compile(
    r"""(?:\$\{?(?P<bc>\w+)\}?\s*(?:<|>|<=|>=)\s*[\d.]+)"""
    r"""|(?:\[\s*[\"']?\$\{?(?P<test>\w+)\}?[\"']?\s+-(?:lt|gt|le|ge|eq|ne)\s+)"""
    r"""|(?:\(\(\s*\$?\{?(?P<arith>\w+)\}?\s*(?:<|>|<=|>=)\s*[\d.]+)"""
)
_EMPTINESS_GUARD_TMPL = (
    r"""\[\s*-n\s+[\"']?\$\{{?{var}\}}?[\"']?\s*\]"""
    r"""|\[\s*-z\s+[\"']?\$\{{?{var}\}}?[\"']?\s*\]"""
    r"""|:\s*[\"']?\$\{{{var}:[?-]"""
    r"""|\$\{{{var}:-"""
    r"""|if\s+\[\s*[\"']?\$\{{?{var}\}}?[\"']?\s*=\s*[\"']{{2}}"""
)


def _tracked_files(repo_root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - environment
        raise RuntimeError(f"git ls-files failed in {repo_root}: {exc}") from exc
    return [line for line in out.splitlines() if line]


def find_tracked_generated_files(
    repo_root: Path,
    patterns: Sequence[str] = _DEFAULT_GENERATED_PATTERNS,
) -> list[str]:
    """Return every tracked path containing one of ``patterns`` (a generated artefact)."""
    hits: list[str] = []
    for rel in _tracked_files(repo_root):
        normalized = "/" + rel.replace("\\", "/")
        for pattern in patterns:
            if pattern in normalized:
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
_DEFAULT_SKIP_DIRS: "tuple[str, ...]" = (
    ".git",
    "__pycache__",
    ".hypothesis",
    ".benchmarks",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    "build",
    "dist",
    "logs",
    "checkpoints",
)


def _walk_text_files(repo_root: Path, text_suffixes: Sequence[str], skip_dirs: Iterable[str]) -> "list[Path]":
    """Every file under *repo_root* with a text suffix, outside the skipped directories."""
    skip = set(skip_dirs)
    suffixes = {suffix.lower() for suffix in text_suffixes}
    return [
        path
        for path in sorted(repo_root.rglob("*"))
        if path.is_file() and path.suffix.lower() in suffixes and not (set(path.relative_to(repo_root).parts) & skip)
    ]


def find_text_files_with_nul_bytes(
    repo_root: Path,
    *,
    text_suffixes: Sequence[str] = DEFAULT_TEXT_SUFFIXES,
    skip_dirs: Iterable[str] = _DEFAULT_SKIP_DIRS,
) -> list[str]:
    """`path (xN, first at byte B)` for every text file holding a NUL byte."""
    out: list[str] = []
    for path in _walk_text_files(repo_root, text_suffixes, skip_dirs):
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in blob:
            out.append(f"{path.relative_to(repo_root).as_posix()} (x{blob.count(bytes([0]))}, first at byte {blob.index(bytes([0]))})")
    return out


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
    return [
        path.relative_to(repo_root).as_posix()
        for path in _walk_text_files(repo_root, text_suffixes, skip_dirs)
        if path.read_bytes().startswith(b"\xef\xbb\xbf")
    ]


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
            var = m.group("bc") or m.group("test") or m.group("arith")
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
    nul_files = find_text_files_with_nul_bytes(repo_root, text_suffixes=text_suffixes)
    if nul_files:
        problems.append(
            f"{len(nul_files)} text file(s) contain a NUL byte. grep reports these as BINARY and "
            "prints no matches, so every text search silently skips them. If you meant the escape, "
            "write the four characters:\n    " + "\n    ".join(nul_files[:20])
        )
    bom_files = find_text_files_with_a_bom(repo_root, text_suffixes=text_suffixes)
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
