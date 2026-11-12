"""_best_pair_ratio: the brute-force maximum when it reaches the threshold, and a value below the threshold otherwise.

The bound-based skip must never change which groups are reported (a group needs its best pair at or above the threshold) nor
the value reported for them.
"""

from __future__ import annotations

import difflib
import random

from py_ci_shared.drifted_duplicate_functions import _best_pair_ratio


def _brute(dumps):
    return max(difflib.SequenceMatcher(None, dumps[i], dumps[j]).ratio() for i in range(len(dumps)) for j in range(i + 1, len(dumps)))


def test_matches_the_brute_force_maximum_wherever_it_reaches_the_threshold():
    rng = random.Random(3)
    for _ in range(300):
        n = rng.randint(2, 7)
        base = "".join(rng.choice("abcdef()=,\n ") for _ in range(rng.randint(5, 120)))
        dumps = []
        for _ in range(n):
            s = list(base)
            for _ in range(rng.randint(0, 15)):
                if s:
                    s[rng.randrange(len(s))] = rng.choice("xyz()")
            dumps.append("".join(s))
        brute = _brute(dumps)
        for threshold in (0.5, 0.9):
            got = _best_pair_ratio(dumps, threshold)
            if brute >= threshold:
                assert abs(got - brute) < 1e-12, (threshold, got, brute)
            else:
                assert got < threshold, (threshold, got, brute)


def test_identical_pair_is_one_and_a_single_body_is_below_any_threshold():
    assert _best_pair_ratio(["def f(): return 1", "def f(): return 1"], 0.9) == 1.0
    assert _best_pair_ratio(["only one"], 0.9) < 0.9
