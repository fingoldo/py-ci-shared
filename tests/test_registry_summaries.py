"""43-F8: a registry ``summary`` is the README catalogue row; it must be well formed and describe what the module docstring says."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from py_ci_shared import registry

PACKAGE = Path(registry.__file__).resolve().parent
STOPWORDS = frozenset(
    "a an the of to in is are and or for on by with that this it as be at from its not no any every each one all when which than then if so can".split()
)
MIN_OVERLAP = 0.5
_ENTRY_PREFIX = re.compile(r"^(?:[a-z_]+\(|(?:assert|find|scan)_\w+:)")
_DANGLING = re.compile(r"(?:\b(?:and|or|of|the|a|to|with|by|for|in|that)|[:,;(])$")


def _docstring(name: str) -> str:
    path = PACKAGE / f"{name}.py"
    if not path.exists():
        path = PACKAGE / name / "__init__.py"
    return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8-sig"))) or ""


def _tokens(text: str) -> frozenset[str]:
    words = (w for w in re.findall(r"[a-z][a-z0-9]{2,}", text.lower()) if w not in STOPWORDS)
    return frozenset(re.sub(r"(?:ing|ed|es|s)$", "", w) for w in words)


def overlap(summary: str, docstring: str) -> float:
    """Shared stems over the smaller of the two stem sets, the docstring side being its first paragraph."""
    first = docstring.split("\n\n", 1)[0]
    a, b = _tokens(summary), _tokens(first)
    return len(a & b) / max(1, min(len(a), len(b)))


def problems(summary: str, docstring: str) -> list[str]:
    out = []
    if len(re.findall(r"`+", summary)) % 2:
        out.append("unclosed code span")
    if _DANGLING.search(summary):
        out.append("ends mid-sentence")
    if summary.endswith("."):
        out.append("ends with a period")
    if not (summary[:1].isupper() or summary.startswith("``")):
        out.append("does not start with a capital letter")
    if _ENTRY_PREFIX.match(summary):
        out.append("starts with an entry-function name instead of a sentence")
    if docstring and overlap(summary, docstring) < MIN_OVERLAP:
        out.append(f"shares too few words with the docstring's first paragraph ({overlap(summary, docstring):.2f} < {MIN_OVERLAP})")
    return out


@pytest.mark.parametrize("spec", registry.GATES, ids=lambda s: s.name)
def test_every_registry_summary_is_well_formed_and_matches_its_docstring(spec):
    assert problems(spec.summary, _docstring(spec.name)) == []


@pytest.mark.parametrize(
    ("summary", "docstring", "expected"),
    [
        ("Every ``continue-on-error", "Shared check: every ``continue-on-error: true`` in a CI workflow file is advisory.", "unclosed code span"),
        ("Persist the directory so ruff finds it (see README's \"Using the", "Persist the directory so ruff finds it.", "ends mid-sentence"),
        (
            "assert_key_sensitive: every input changes a cache key",
            "Every input that changes a memoised result changes its cache key.",
            "starts with an entry-function name",
        ),
        (
            "known_gap(reason, closed): xfail while a limit is open",
            "known_gap(reason, closed): record an open limit as an expected failure.",
            "starts with an entry-function name",
        ),
        ("a workflow must not cancel the default branch", "A workflow must not cancel the default branch.", "capital letter"),
        ("Tracks the weather in Lisbon", "Every ratchet baseline holds sorted unique keys.", "shares too few words"),
    ],
)
def test_the_checks_have_teeth(summary, docstring, expected):
    assert any(expected in p for p in problems(summary, docstring)), problems(summary, docstring)


def test_a_faithful_summary_passes():
    assert (
        problems(
            "Every input that changes a memoised result must change its cache key",
            "Every input that changes a memoised result must change its cache key, and nothing else may.",
        )
        == []
    )
