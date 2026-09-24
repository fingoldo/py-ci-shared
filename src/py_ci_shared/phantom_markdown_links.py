"""Shared check: every markdown-link target in a repo's .md files resolves
to a real file.

Generalizes a 2026-07-22 audit finding (llm_bench): README.md linked to
``docs/architecture.md`` before that file existed -- a dead link a reader
hits immediately, and nothing caught it going stale either way (the doc
existing then later being renamed/removed is the same failure shape).

Deliberately scoped to markdown-LINK syntax (``[text](path)``) only, not
bare filename mentions in prose (e.g. "see round_runner.py") -- bare
mentions are extremely common as plain module-naming in comments/
docstrings/READMEs and produce a very high false-positive rate (test
fixture literals, illustrative examples) without a much more
sophisticated context model. An explicit markdown link is a low-noise,
unambiguous claim that its target resolves.

Usage (in a consuming repo's test suite)::

    from pathlib import Path
    from py_ci_shared.phantom_markdown_links import assert_no_phantom_markdown_links

    def test_no_phantom_markdown_links():
        assert_no_phantom_markdown_links(md_files=Path(__file__).resolve().parents[2].glob("*.md"),
                                          repo_root=Path(__file__).resolve().parents[2])

Deliberately dependency-light: ``pytest`` is imported lazily, matching
this package's other modules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote

from ._core import SourceError, read_source, relative_posix

# An inline link or image: [text](target) / ![alt](target), the target optionally in <...> and optionally followed
# by a "title". Text may hold one level of nested brackets (a badge image inside a link).
_MD_LINK_RE = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*(<[^>\n]*>|[^\s()]+(?:\([^\s()]*\)[^\s()]*)*)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
# A reference definition: [id]: target "title"
_MD_REF_DEF_RE = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:\s*(<[^>\n]*>|\S+)")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _is_external(target: str) -> bool:
    return bool(_SCHEME_RE.match(target)) or target.startswith("//")


def _link_targets(source: str) -> "list[tuple[int, str]]":
    """``(line, target)`` for every inline link, image and reference definition outside fenced code and code spans."""
    out: list[tuple[int, str]] = []
    fence: "str | None" = None
    for lineno, line in enumerate(source.splitlines(), start=1):
        opener = _FENCE_RE.match(line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence) and not line.strip()[len(opener.group(1)) :].strip():
                fence = None
            continue
        if opener:
            fence = opener.group(1)
            continue
        text = _CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)
        out.extend((lineno, m.group(1)) for m in _MD_LINK_RE.finditer(text))
        ref = _MD_REF_DEF_RE.match(text)
        if ref:
            out.append((lineno, ref.group(1)))
    return out


def _local_path(target: str) -> "str | None":
    """The file part of a relative target (``<>`` removed, ``#fragment``/``?query`` dropped, ``%20`` decoded), or
    ``None`` for an external link or a same-page anchor."""
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target or target.startswith("#") or _is_external(target):
        return None
    target = re.split(r"[#?]", target, maxsplit=1)[0]
    return unquote(target) if target else None


def tracked_markdown_files(repo_root: Path) -> list[Path]:
    """Every ``.md`` file git tracks under ``repo_root``: the repository's own prose, and nothing else.

    A ``rglob("*.md")`` also walks whatever sits in the checkout untracked, and a local virtualenv
    is full of third-party markdown whose relative links point at files its packaging never shipped:
    glossum's check failed on ``.venv/Lib/site-packages/nltk-*.dist-info/licenses/README.md`` the
    first time a ``.venv`` appeared beside it. An untracked file is not something a reader of the
    repository can follow a link from, so it is not the check's subject.
    """
    from .repo_hygiene import tracked_files

    return [repo_root / rel for rel in tracked_files(repo_root) if rel.endswith(".md")]


def find_phantom_markdown_links(md_files: Iterable[Path], repo_root: Path) -> list[str]:
    """Return ``"<rel_path>:<line>: dead markdown-link target '<target>'"`` for every link target that does not
    resolve the way a markdown renderer resolves it: relative to the referencing file's own directory, or, for a
    target starting with ``/``, relative to ``repo_root``.

    Covered: inline links and images (any extension, directories too), ``<...>`` targets, titles, reference-style
    definitions; a ``#fragment`` or ``?query`` is dropped before the file is checked. Skipped: external links (any
    ``scheme:``), same-page anchors, and anything inside fenced code blocks or code spans. An unreadable file is
    reported, not skipped.
    """
    violations: list[str] = []
    for path in md_files:
        rel = relative_posix(path, repo_root)
        try:
            source = read_source(path)
        except SourceError as exc:
            violations.append(f"{rel}:{exc.line or 1}: {exc.kind}: {exc.message}")
            continue
        for lineno, raw in _link_targets(source):
            local = _local_path(raw)
            if local is None:
                continue
            resolved = repo_root / local.lstrip("/") if local.startswith("/") else path.parent / local
            if resolved.exists():
                continue
            violations.append(f"{rel}:{lineno}: dead markdown-link target {raw.strip('<>')!r}")
    return violations


def assert_no_phantom_markdown_links(md_files: Iterable[Path], repo_root: Path, *, min_files: int = 1) -> None:
    """Fail if any markdown-link target in ``md_files`` doesn't resolve, or if fewer than ``min_files`` files could
    be read (a scan that lost its subject).
    Call this directly as the body of a ``test_*`` function -- no
    baseline/refresh mechanism, since a dead link is unconditionally
    wrong (there's no legitimate "grandfathered" dead link)."""
    import pytest

    md_files = list(md_files)
    violations = find_phantom_markdown_links(md_files, repo_root)
    unreadable = {v.split(":", 1)[0] for v in violations if ": unreadable: " in v}
    read = len({relative_posix(p, repo_root) for p in md_files} - unreadable)
    if read < min_files:
        violations.append(f"only {read} markdown file(s) read; expected at least {min_files}. The scan lost its subject.")
    if violations:
        msg = "\n  ".join(violations)
        pytest.fail(f"Dead markdown-link target(s):\n  {msg}")
