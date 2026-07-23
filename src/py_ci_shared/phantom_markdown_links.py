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
from pathlib import Path
from collections.abc import Iterable

# [text](path/to/thing.ext) -- captures the link target. Scoped to a small,
# explicit extension allowlist (not "any non-space run") to avoid matching
# inline-code spans or other bracket-paren text that isn't really a link.
_MD_LINK_RE = re.compile(r"\]\(([\w./-]+\.(?:py|md|sql|yml|yaml|json|toml|cfg|ini|txt))\)")


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "git+", "mailto:"))


def find_phantom_markdown_links(md_files: Iterable[Path], repo_root: Path) -> list[str]:
    """Return ``"<rel_path>:<line>: dead markdown-link target '<target>'"``
    for every markdown-link target that resolves against neither
    ``repo_root`` nor the referencing file's own directory. External links
    (http(s)://, git+, mailto:) are never checked."""
    violations: list[str] = []
    for path in md_files:
        try:
            rel = str(path.relative_to(repo_root))
        except ValueError:
            rel = str(path)
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(source.splitlines(), start=1):
            for m in _MD_LINK_RE.finditer(line):
                target = m.group(1)
                if _is_external(target):
                    continue
                if (repo_root / target).exists() or (path.parent / target).exists():
                    continue
                violations.append(f"{rel}:{lineno}: dead markdown-link target {target!r}")
    return violations


def assert_no_phantom_markdown_links(md_files: Iterable[Path], repo_root: Path) -> None:
    """Fail if any markdown-link target in ``md_files`` doesn't resolve.
    Call this directly as the body of a ``test_*`` function -- no
    baseline/refresh mechanism, since a dead link is unconditionally
    wrong (there's no legitimate "grandfathered" dead link)."""
    import pytest

    violations = find_phantom_markdown_links(md_files, repo_root)
    if violations:
        msg = "\n  ".join(violations)
        pytest.fail(f"Dead markdown-link target(s):\n  {msg}")
