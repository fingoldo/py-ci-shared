"""_best_pair_ratio returns exactly the brute-force maximum of SequenceMatcher.ratio over the pairs (the bound-based skip never changes it)."""

from __future__ import annotations

import difflib
import random

from py_ci_shared.drifted_duplicate_functions import _best_pair_ratio


def _brute(dumps):
    return max(difflib.SequenceMatcher(None, dumps[i], dumps[j]).ratio() for i in range(len(dumps)) for j in range(i + 1, len(dumps)))


def test_matches_the_brute_force_maximum_on_random_bodies():
    rng = random.Random(3)
    for _ in range(200):
        n = rng.randint(2, 7)
        base = "".join(rng.choice("abcdef()=,\n ") for _ in range(rng.randint(5, 120)))
        dumps = []
        for _ in range(n):
            s = list(base)
            for _ in range(rng.randint(0, 15)):
                if s:
                    s[rng.randrange(len(s))] = rng.choice("xyz()")
            dumps.append("".join(s))
        assert abs(_best_pair_ratio(dumps) - _brute(dumps)) < 1e-12


def test_identical_pair_is_one_and_a_single_body_is_zero():
    assert _best_pair_ratio(["def f(): return 1", "def f(): return 1"]) == 1.0
    assert _best_pair_ratio(["only one"]) == 0.0
