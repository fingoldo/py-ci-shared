"""The epsilon-padded-power-denominator check must fire on the real shapes and stay quiet on the safe ones."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.epsilon_padded_denominators import (
    assert_no_epsilon_padded_power_denominators,
    find_epsilon_padded_power_denominators,
)


def _module(tmp_path: Path, body_lines, name: str = "m.py") -> Path:
    """Write one module from a list of source lines and return the directory holding it."""
    newline = chr(10)  # written this way so the fixture source carries no escape sequences of its own
    (tmp_path / name).write_bytes((newline.join(body_lines) + newline).encode("utf-8"))
    return tmp_path


def _fn(line: str):
    """A one-expression function body, so every fixture differs only in the expression under test."""
    return ["def f():", "    " + line, "    return 0"]


FLAGGED = [
    ("knn_density", "local_density = float(k) / (dist_to_kth**d + 1e-12)"),
    ("idw_weights", "weights = 1.0 / (compact_dist**power + 1e-12)"),
    ("epsilon_first", "w = 1.0 / (1e-12 + dist**power)"),
    ("written_out_square", "z = num / (sigma * sigma + 1e-12)"),
    ("np_power", "z = num / (np.power(d, k) + 1e-9)"),
    ("np_square", "z = num / (np.square(sigma) + 1e-12)"),
    ("cubed_std", "skew = m3 / (std**3 + 1e-12)"),
]


@pytest.mark.parametrize("label,line", FLAGGED, ids=[label for label, _ in FLAGGED])
def test_a_padded_power_denominator_is_reported(tmp_path: Path, label: str, line: str):
    """Each of these is a shape that shipped, or the same shape written another way."""
    root = _module(tmp_path, _fn(line))
    found = find_epsilon_padded_power_denominators([root])
    assert len(found) == 1, f"{label}: expected one finding, got {[str(x) for x in found]}"
    assert found[0].lineno == 2


NOT_FLAGGED = [
    ("relative_error", "rel = abs(a - b) / (abs(a) + 1e-12)"),
    ("plain_denominator", "z = num / (den + 1e-12)"),
    ("clamped", "z = num / np.maximum(d**k, np.finfo(np.float64).tiny)"),
    ("no_epsilon", "z = num / d**k"),
    ("large_addend", "z = num / (d**k + 1.0)"),
    ("addend_is_a_name", "z = num / (d**k + eps)"),
    ("multiplied_not_added", "z = num / (d**k * 1e-12)"),
    ("different_operands", "z = num / (sigma * mu + 1e-12)"),
]


@pytest.mark.parametrize("label,line", NOT_FLAGGED, ids=[label for label, _ in NOT_FLAGGED])
def test_a_safe_form_is_not_reported(tmp_path: Path, label: str, line: str):
    """A check that has to be triaged is a check that gets baselined, so the safe forms must stay silent.

    `plain_denominator` and `relative_error` matter most: the general 'epsilon added to a denominator' rule
    matched 109 sites on a ~3500-module repository, nearly all of them legitimate, against 2 for this one.
    """
    root = _module(tmp_path, _fn(line))
    assert find_epsilon_padded_power_denominators([root]) == [], f"{label} should not be reported"


def test_the_addend_being_a_name_is_out_of_scope_deliberately(tmp_path: Path):
    """A named epsilon cannot be judged by parsing, so it is left unreported rather than guessed at.

    Pinned so that widening the rule to names has to change a test that says so.
    """
    root = _module(tmp_path, ["EPS = 1e-12", "", "", "def f():", "    return num / (d**k + EPS)"])
    assert find_epsilon_padded_power_denominators([root]) == []


def test_the_assertion_names_every_site_and_suggests_the_clamp(tmp_path: Path):
    """The message has to carry the fix; a bare count sends the reader back to the source to guess."""
    root = _module(tmp_path, _fn("return k / (r**d + 1e-12)"))
    with pytest.raises(AssertionError) as exc:
        assert_no_epsilon_padded_power_denominators([root])
    message = str(exc.value)
    assert "m.py:2" in message
    assert "np.maximum" in message


def test_an_allowed_site_is_not_reported(tmp_path: Path):
    """A judged-safe site can be listed, keyed by path and line."""
    root = _module(tmp_path, _fn("return k / (r**d + 1e-12)"))
    assert_no_epsilon_padded_power_denominators([root], allow=[f"{(root / 'm.py').as_posix()}:2"])


def test_excluded_paths_are_skipped(tmp_path: Path):
    """Frozen baselines and benchmark copies keep the shape they were frozen with."""
    bench = tmp_path / "_benchmarks"
    bench.mkdir()
    _module(bench, _fn("return k / (r**d + 1e-12)"), name="b.py")
    assert find_epsilon_padded_power_denominators([tmp_path]) != []
    assert find_epsilon_padded_power_denominators([tmp_path], exclude=("_benchmarks",)) == []


def test_a_file_that_does_not_parse_is_skipped_rather_than_raising(tmp_path: Path):
    """A scanner that dies on one unparseable file takes the whole gate down with it."""
    _module(tmp_path, ["def f(:"], name="broken.py")
    _module(tmp_path, _fn("return k / (r**d + 1e-12)"), name="good.py")
    found = find_epsilon_padded_power_denominators([tmp_path])
    assert [f.path.name for f in found] == ["good.py"]


MORE_FLAGGED = [
    ("builtin_pow", "z = k / (pow(r, d) + 1e-12)"),
    ("math_pow", "z = k / (math.pow(r, d) + 1e-12)"),
    ("augmented_divide", "z /= r**d + 1e-12"),
    ("np_divide", "z = np.divide(k, r**d + 1e-12)"),
    ("three_terms", "z = k / (r**d + offset + 1e-12)"),
    ("three_terms_eps_middle", "z = k / (offset + 1e-12 + r**d)"),
]


@pytest.mark.parametrize("label,line", MORE_FLAGGED, ids=[label for label, _ in MORE_FLAGGED])
def test_the_other_spellings_of_a_padded_power_are_reported(tmp_path: Path, label: str, line: str):
    assert len(find_epsilon_padded_power_denominators([_module(tmp_path, _fn(line))])) == 1


@pytest.mark.parametrize(
    "line",
    ["z = np.divide(k, r + 1e-12)", "z /= r + 1e-12", "z = k / (pow(r, d) + offset)", "z = k / (a + b + 1e-12)", "z = np.divide(r**d + 1e-12, k)"],
)
def test_their_safe_counterparts_are_not(tmp_path: Path, line: str):
    assert find_epsilon_padded_power_denominators([_module(tmp_path, _fn(line))]) == []


def test_a_bom_file_is_scanned(tmp_path: Path):
    (tmp_path / "m.py").write_bytes(b"\xef\xbb\xbfy = k / (r**d + 1e-12)\n")
    assert len(find_epsilon_padded_power_denominators([tmp_path])) == 1


def test_zero_files_and_unparsable_files_fail_the_assertion(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AssertionError, match="parsed"):
        assert_no_epsilon_padded_power_denominators([empty])
    _module(tmp_path, _fn("return 1"), name="ok.py")
    assert_no_epsilon_padded_power_denominators([tmp_path])
    _module(tmp_path, ["def f(:"], name="broken.py")
    with pytest.raises(AssertionError, match=r"broken.py"):
        assert_no_epsilon_padded_power_denominators([tmp_path])


def test_a_missing_root_raises(tmp_path: Path):
    from py_ci_shared._core import CorpusError

    with pytest.raises(CorpusError, match="does not exist"):
        assert_no_epsilon_padded_power_denominators([tmp_path / "typo"])
