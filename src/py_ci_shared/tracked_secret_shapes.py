"""Tracked files holding a string of a secret shape the consumer configured, and never the matched text itself.

87 live Upwork bearer tokens (``oauth2v2_`` plus hex) sat committed in 30 tracked files (exploratory scripts, a browser test)
and went unnoticed: ``detect-secrets`` ships no plugin for that shape and its hooks covered three packages of a monorepo.
A secret scanner with a fixed shape list cannot know a vendor's token format, so the shapes are the CONSUMER's data:

    [tool.py_ci_shared.secret_shapes]
    upwork_bearer = "oauth2v2_[0-9a-f]{20,}"
    internal_key = { pattern = "int_key_[A-Za-z0-9]{32}", min_length = 40, note = "rotate in the vault, then purge" }

``name = regex`` or ``name = { pattern, min_length, note }``. The built-in set (:data:`BUILTIN_SHAPES`) is EMPTY on purpose: the
generic shapes (AWS, GitHub, Slack, private-key headers, JWT) are already ``detect-secrets`` plugins, and a built-in that
duplicates them only adds a second source of false positives. A shape is added here only when detect-secrets lacks it and it
is not vendor-specific.

What it reads: the INDEX blobs of ``git ls-files`` (what a commit would contain), not the work tree. A binary blob (a NUL in
the first 8000 bytes, git's own test) and a blob over ``max_bytes`` are skipped and COUNTED (:class:`ScanReport`); a blob that
cannot be read is an error unless ``allow_unparsed``. A line carrying ``allow_marker`` is exempt (a test fake must say so).
There is no baseline on purpose: a committed secret is a finding however old, and the fix is a rotation. This gate does not
rewrite history: a token already pushed stays in every clone and in the reflog until the credential is revoked.

Nothing the gate prints contains the matched text: a finding is ``path:line: [rule] <shape> shape matched (N chars)``. Config
errors (no shapes, an uncompilable regex, a regex that matches the empty string, an unknown key) raise ``ConfigError``: a gate
with nothing to look for must not pass.

Usage in a consumer's meta test::

    from py_ci_shared.tracked_secret_shapes import assert_no_tracked_secret_shapes

    def test_no_secret_shaped_string_is_tracked():
        assert_no_tracked_secret_shapes(REPO_ROOT, exclude_paths=["vendor/"], min_files=50)

and as a pre-commit hook (``language: system`` receives the staged file names; the shapes come from ``pyproject.toml``)::

    - id: tracked-secret-shapes
      name: tracked secret shapes
      entry: python -m py_ci_shared.tracked_secret_shapes --config pyproject.toml
      language: system
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._core import CorpusError, EmptyScanError, Finding
from ._core.config import ConfigError
from ._toml_compat import tomllib
from .committed_line_endings import _blobs, _cat_blobs  # one reader of index blobs; a second copy would drift

__all__ = [
    "ALLOW_MARKER",
    "BUILTIN_SHAPES",
    "RULE",
    "ScanReport",
    "Shape",
    "assert_no_tracked_secret_shapes",
    "compile_shapes",
    "find_tracked_secret_shapes",
    "load_shapes",
    "main",
    "scan_tracked_secret_shapes",
]

RULE = "tracked-secret-shape"
ALLOW_MARKER = "pragma: allowlist secret"
CONFIG_TABLE = ("tool", "py_ci_shared", "secret_shapes")
DEFAULT_MAX_BYTES = 1_000_000
_BINARY_PROBE = 8000
_SPEC_KEYS = frozenset({"pattern", "min_length", "note"})

#: Deliberately empty, see the module docstring.
BUILTIN_SHAPES: dict[str, str] = {}

PathLike = Union[str, Path]


@dataclass(frozen=True)
class Shape:
    """One compiled secret shape."""

    name: str
    regex: "re.Pattern[str]"
    min_length: int = 0
    note: str = ""


@dataclass
class ScanReport:
    """Findings plus what was NOT read, so a green result says how much it covered."""

    findings: list[Finding] = field(default_factory=list)
    scanned: int = 0
    skipped_binary: int = 0
    skipped_large: int = 0
    skipped_excluded: int = 0
    unreadable: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.scanned} file(s) scanned, {self.skipped_binary} binary, {self.skipped_large} over the size cap, "
            f"{self.skipped_excluded} excluded, {len(self.unreadable)} unreadable"
        )


def compile_shapes(shapes: Mapping[str, Any]) -> list[Shape]:
    """Validate and compile ``name -> regex | {pattern, min_length, note}``, merged over :data:`BUILTIN_SHAPES`."""
    merged: dict[str, Any] = {**BUILTIN_SHAPES, **dict(shapes)}
    if not merged:
        raise ConfigError("no secret shapes configured: add a [tool.py_ci_shared.secret_shapes] table (name = regex) or pass shapes=")
    out = []
    for name, spec in sorted(merged.items()):
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"secret shape name must be a non-empty string, got {name!r}")
        table = {"pattern": spec} if isinstance(spec, str) else spec
        if not isinstance(table, Mapping) or not isinstance(table.get("pattern"), str) or not table["pattern"]:
            raise ConfigError(f"secret shape {name!r}: expected a regex string or a table with a 'pattern' string")
        unknown = sorted(set(table) - _SPEC_KEYS)
        if unknown:
            raise ConfigError(f"secret shape {name!r}: unknown key(s) {unknown}; allowed: {sorted(_SPEC_KEYS)}")
        min_length = table.get("min_length", 0)
        if isinstance(min_length, bool) or not isinstance(min_length, int) or min_length < 0:
            raise ConfigError(f"secret shape {name!r}: min_length must be a non-negative integer")
        try:
            regex = re.compile(table["pattern"])
        except re.error as exc:
            raise ConfigError(f"secret shape {name!r}: the regex does not compile ({exc})") from exc
        if regex.match("") is not None:
            raise ConfigError(f"secret shape {name!r}: the regex matches the empty string, so it would flag every line")
        out.append(Shape(name, regex, min_length, str(table.get("note", ""))))
    return out


def load_shapes(config_path: PathLike) -> dict[str, Any]:
    """The ``[tool.py_ci_shared.secret_shapes]`` table of *config_path* (``ConfigError`` when the file or table is missing)."""
    path = Path(config_path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read the secret shape config {path}: {type(exc).__name__}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    node: Any = data
    for key in CONFIG_TABLE:
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, dict) or not node:
        raise ConfigError(f"{path} has no [{'.'.join(CONFIG_TABLE)}] table with at least one shape")
    return node


def _excluded(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, p) or path.startswith(p.rstrip("/") + "/") for p in patterns)


def _scan_text(path: str, text: str, shapes: Sequence[Shape], allow_marker: str) -> list[Finding]:
    out: list[Finding] = []
    for number, line in enumerate(text.split("\n"), 1):
        if allow_marker in line:
            continue
        for shape in shapes:
            for match in shape.regex.finditer(line):
                length = match.end() - match.start()
                if length < shape.min_length:
                    continue
                note = f"; {shape.note}" if shape.note else ""
                message = f"{shape.name} shape matched ({length} chars, column {match.start() + 1}); text withheld{note}"
                out.append(Finding(path, number, RULE, message, key=f"{RULE}::{shape.name}::{path}::{number}::{match.start()}"))
    return out


def _check_blob(path: str, data: Optional[bytes], shapes: Sequence[Shape], report: ScanReport, allow_marker: str, max_bytes: int) -> None:
    if data is None:
        report.unreadable.append(path)
    elif b"\0" in data[:_BINARY_PROBE]:
        report.skipped_binary += 1
    elif len(data) > max_bytes:
        report.skipped_large += 1
    else:
        report.scanned += 1
        report.findings.extend(_scan_text(path, data.decode("utf-8-sig", "replace"), shapes, allow_marker))


def scan_tracked_secret_shapes(
    repo_root: PathLike,
    *,
    shapes: Optional[Mapping[str, Any]] = None,
    config_path: Optional[PathLike] = None,
    exclude_paths: Iterable[str] = (),
    allow_marker: str = ALLOW_MARKER,
    max_bytes: int = DEFAULT_MAX_BYTES,
    min_files: int = 1,
    allow_unparsed: bool = False,
    files: Optional[Sequence[PathLike]] = None,
) -> ScanReport:
    """Scan the index blobs of the git repo at *repo_root* (or, with *files*, those work-tree files) for the shapes.

    *shapes* wins over the config table; without it the table of *config_path* (default ``<repo_root>/pyproject.toml``) is read.
    Raises ``ConfigError`` for bad or missing shapes, ``CorpusError`` outside git or for an unreadable blob (unless
    *allow_unparsed*), ``EmptyScanError`` when fewer than *min_files* files were scanned."""
    root = Path(repo_root)
    if not root.is_dir():
        raise CorpusError(f"repository root does not exist: {root}")
    if not allow_marker:
        raise ConfigError("allow_marker must be a non-empty string, or every line would be exempt")
    compiled = compile_shapes(shapes if shapes is not None else load_shapes(config_path or root / "pyproject.toml"))
    patterns = [p.replace("\\", "/") for p in exclude_paths]
    report = ScanReport()
    if files is None:
        everything = _blobs(root)
        blobs = {p: s for p, s in everything.items() if not _excluded(p, patterns)}
        report.skipped_excluded = len(everything) - len(blobs)
        contents = _cat_blobs(root, sorted(set(blobs.values())))
        for path in sorted(blobs):
            _check_blob(path, contents.get(blobs[path]), compiled, report, allow_marker, max_bytes)
    else:
        for item in sorted({Path(f) for f in files}):
            rel = item.as_posix()
            if _excluded(rel, patterns):
                report.skipped_excluded += 1
                continue
            try:
                data: Optional[bytes] = (item if item.is_absolute() else root / item).read_bytes()
            except OSError:
                data = None
            _check_blob(rel, data, compiled, report, allow_marker, max_bytes)
    if report.unreadable and not allow_unparsed:
        raise CorpusError(f"{len(report.unreadable)} tracked file(s) could not be read, so they were not checked: {sorted(report.unreadable)[:20]}")
    if report.scanned < min_files:
        raise EmptyScanError(f"only {report.scanned} file(s) scanned under {root} ({report.summary()}); expected at least {min_files}")
    report.findings.sort(key=lambda f: (f.path, f.line, f.key))
    return report


def find_tracked_secret_shapes(repo_root: PathLike, shapes: Mapping[str, Any], **kwargs: Any) -> list[Finding]:
    """The findings of :func:`scan_tracked_secret_shapes` for explicit *shapes* (a repo checkout alone cannot supply them)."""
    return scan_tracked_secret_shapes(repo_root, shapes=shapes, **kwargs).findings


def assert_no_tracked_secret_shapes(
    repo_root: PathLike,
    *,
    shapes: Optional[Mapping[str, Any]] = None,
    config_path: Optional[PathLike] = None,
    exclude_paths: Iterable[str] = (),
    allow_marker: str = ALLOW_MARKER,
    max_bytes: int = DEFAULT_MAX_BYTES,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Fail on any tracked secret-shaped string. No baseline exists: rotate the credential, then remove or mark the line."""
    report = scan_tracked_secret_shapes(
        repo_root,
        shapes=shapes,
        config_path=config_path,
        exclude_paths=exclude_paths,
        allow_marker=allow_marker,
        max_bytes=max_bytes,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
    )
    if report.findings:
        guidance = f"revoke the credential first (history is not rewritten), then delete the line or mark a test fake with '{allow_marker}'"
        raise AssertionError(
            f"{len(report.findings)} tracked secret-shaped string(s) ({report.summary()}); {guidance}:\n  " + "\n  ".join(f.render() for f in report.findings)
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Pre-commit entry. Exit 0 clean, 1 findings, 2 a config or read error. Prints ``path:line: [rule] message``, never the text."""
    parser = argparse.ArgumentParser(prog="tracked_secret_shapes", description="Tracked files holding a configured secret shape.")
    parser.add_argument("files", nargs="*", help="files to check (pre-commit passes the staged ones); none means the whole index")
    parser.add_argument("--config", default="pyproject.toml")
    parser.add_argument("--root", default=".")
    parser.add_argument("--exclude", action="append", default=[], help="glob or directory prefix to skip (repeatable)")
    parser.add_argument("--allow-marker", default=ALLOW_MARKER)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--min-files", type=int, default=None, help="default 1 for the whole index, 0 when files are given")
    parser.add_argument("--allow-unparsed", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = Path(args.config)
    try:
        report = scan_tracked_secret_shapes(
            root,
            config_path=config if config.is_absolute() else root / config,
            exclude_paths=args.exclude,
            allow_marker=args.allow_marker,
            max_bytes=args.max_bytes,
            min_files=args.min_files if args.min_files is not None else (0 if args.files else 1),
            allow_unparsed=args.allow_unparsed,
            files=args.files or None,
        )
    except (ConfigError, CorpusError, EmptyScanError) as exc:
        print(f"tracked_secret_shapes: {type(exc).__name__}: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    for finding in report.findings:
        print(finding.render())  # noqa: T201
    print(f"tracked_secret_shapes: {len(report.findings)} finding(s); {report.summary()}", file=sys.stderr)  # noqa: T201
    return 1 if report.findings else 0


if __name__ == "__main__":
    sys.exit(main())
