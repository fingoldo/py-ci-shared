"""comment_tokens keeps a corpus larger than 512 files cached between gates (audit 2026-11-16 48-T5)."""

from __future__ import annotations

from py_ci_shared._core.source import comment_tokens


def test_second_pass_over_a_large_corpus_is_served_from_cache() -> None:
    sources = [f"x_{i} = {i}  # note {i}\n" for i in range(600)]
    comment_tokens.cache_clear()
    for text in sources:
        comment_tokens(text)
    before = comment_tokens.cache_info().hits
    for text in sources:
        comment_tokens(text)
    assert comment_tokens.cache_info().hits - before == len(sources)
