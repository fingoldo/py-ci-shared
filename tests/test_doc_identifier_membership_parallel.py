"""The process-pool membership test answers exactly what the serial substring test answers."""

from __future__ import annotations

from py_ci_shared.doc_identifier_parity import _PARALLEL_MIN_TOKENS, _membership, _words


def test_parallel_membership_matches_serial_substring_answers():
    """A batch above the parallel threshold, mixing present, absent, mid-word and non-ASCII tokens, gets the serial answers."""
    corpus = "\n".join(f"line {i} uses name_{i} and alpha-{i}.beta, caf\u00e9-{i}" for i in range(4000))
    tokens = [f"name_{i}" for i in range(0, 3000, 7)] + [f"absent_{i}_zz" for i in range(200)] + ["ame_1", "alpha-3.beta", "caf\u00e9-9", "nope\u00e9"]
    assert len(set(tokens)) > _PARALLEL_MIN_TOKENS
    serial = {token: token in corpus for token in tokens}
    assert _membership(tokens, corpus, _words(corpus)) == serial
