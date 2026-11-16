"""doc_identifier_parity answers a known whole word without searching the corpus (audit 2026-11-16 48-T4)."""

from __future__ import annotations

from py_ci_shared.doc_identifier_parity import _in_corpus, _words

CORPUS = "def alpha_beta():\n    return --dry-run\n"


class CountingText(str):
    """A corpus string that counts its substring searches."""

    searches = 0

    def __contains__(self, item: object) -> bool:
        CountingText.searches += 1
        return super().__contains__(item)  # type: ignore[operator]


def test_whole_word_token_is_answered_from_the_word_set() -> None:
    CountingText.searches = 0
    corpus = CountingText(CORPUS)
    words = _words(corpus)
    assert _in_corpus("alpha_beta", corpus, words, {}) is True
    assert CountingText.searches == 0


def test_absent_token_still_searches_the_corpus() -> None:
    CountingText.searches = 0
    corpus = CountingText(CORPUS)
    words = _words(corpus)
    assert _in_corpus("gamma_delta", corpus, words, {}) is False
    assert CountingText.searches == 1


def test_substring_that_is_not_a_word_still_matches() -> None:
    corpus = CORPUS
    words = _words(corpus)
    assert _in_corpus("lpha_bet", corpus, words, {}) is True
    assert _in_corpus("--dry", corpus, words, {}) is True
