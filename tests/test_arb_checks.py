"""Unit tests for the ARB catalogue checks. Real scratch .arb files, same no-mocking convention as
this package's other tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.arb_checks import (
    assert_arb_catalogues_are_sound,
    find_dead_keys,
    find_key_parity_problems,
    find_plural_problems,
    find_register_problems,
)


def _catalogues(tmp_path: Path, **locales: dict) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for loc, data in locales.items():
        p = tmp_path / f"app_{loc}.arb"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out[loc] = p
    return out


class TestKeyParity:
    def test_missing_key_is_flagged(self, tmp_path):
        c = _catalogues(tmp_path, en={"a": "A", "b": "B"}, ru={"a": "А"})
        problems = find_key_parity_problems(c, "en")
        assert len(problems) == 1
        assert "missing 1 key" in problems[0]

    def test_extra_key_is_flagged(self, tmp_path):
        c = _catalogues(tmp_path, en={"a": "A"}, ru={"a": "А", "z": "Я"})
        problems = find_key_parity_problems(c, "en")
        assert len(problems) == 1
        assert "absent from en" in problems[0]

    def test_matching_sets_pass(self, tmp_path):
        c = _catalogues(tmp_path, en={"a": "A"}, ru={"a": "А"})
        assert find_key_parity_problems(c, "en") == []


class TestPlurals:
    def test_russian_plural_with_english_branches_is_flagged(self, tmp_path):
        c = _catalogues(
            tmp_path,
            ru={"streakDays": "{count, plural, one{{count} день} other{{count} дней}}"},
        )
        problems = find_plural_problems(c)
        assert len(problems) == 1
        assert "few" in problems[0] and "many" in problems[0]

    def test_russian_plural_with_all_categories_passes(self, tmp_path):
        c = _catalogues(
            tmp_path,
            ru={"streakDays": "{count, plural, one{{count} день} few{{count} дня} " "many{{count} дней} other{{count} дня}}"},
        )
        assert find_plural_problems(c) == []

    def test_counted_value_without_plural_is_flagged(self, tmp_path):
        c = _catalogues(tmp_path, en={"streakDays": "{count} days"})
        problems = find_plural_problems(c)
        assert len(problems) == 1
        assert "no ICU plural" in problems[0]

    def test_ratio_display_is_not_treated_as_a_plural(self, tmp_path):
        c = _catalogues(tmp_path, en={"imageObjectPosition": "{name}, object {index} of {count}"})
        assert find_plural_problems(c) == []

    def test_progress_ratio_is_not_flagged(self, tmp_path):
        c = _catalogues(tmp_path, en={"progressCount": "{current} / {total}"})
        assert find_plural_problems(c) == []

    def test_branch_that_drops_the_placeholder_is_flagged(self, tmp_path):
        c = _catalogues(
            tmp_path,
            en={"itemsCount": "{count, plural, one{one item} other{many items}}"},
        )
        problems = find_plural_problems(c)
        assert len(problems) == 1
        assert "renders without its number" in problems[0]

    def test_one_branch_may_spell_the_number_out(self, tmp_path):
        c = _catalogues(
            tmp_path,
            en={"blanksCount": "{count, plural, one{1 blank is empty} other{{count} blanks are empty}}"},
        )
        assert find_plural_problems(c) == []


class TestRegisterAndDeadKeys:
    def test_informal_french_is_reported(self, tmp_path):
        c = _catalogues(tmp_path, fr={"greeting": "Bonjour, entre ton mot de passe"})
        problems = find_register_problems(c)
        assert len(problems) == 1
        assert "informal" in problems[0]

    def test_formal_french_passes(self, tmp_path):
        c = _catalogues(tmp_path, fr={"greeting": "Bonjour, entrez votre mot de passe"})
        assert find_register_problems(c) == []

    def test_dead_key_is_found(self, tmp_path):
        c = _catalogues(tmp_path, en={"used": "U", "dead": "D"})
        assert find_dead_keys(c, "en", "Text(l10n.used)") == ["dead"]

    def test_inline_call_form_counts_as_a_use(self, tmp_path):
        c = _catalogues(tmp_path, en={"used": "U"})
        assert find_dead_keys(c, "en", "AppLocalizations.of(context)!.used") == []

    def test_allowed_key_is_not_dead(self, tmp_path):
        c = _catalogues(tmp_path, en={"generatorOnly": "G"})
        assert find_dead_keys(c, "en", "", allowed=["generatorOnly"]) == []


class TestAssert:
    def test_assert_passes(self, tmp_path):
        c = _catalogues(tmp_path, en={"hello": "Hello"}, ru={"hello": "Привет"})
        assert_arb_catalogues_are_sound(c, template_locale="en", source_text="l10n.hello")

    def test_assert_fails_on_a_dead_key(self, tmp_path):
        c = _catalogues(tmp_path, en={"hello": "Hello"})
        with pytest.raises(pytest.fail.Exception, match="never rendered"):
            assert_arb_catalogues_are_sound(c, template_locale="en", source_text="")


def test_an_exact_selector_covers_its_category(tmp_path):
    """`=1` is checked before the categories, so a plural written with it renders correctly for 1.

    Reading only the category names reported `one` missing on every such string - 18 of 24 findings on
    the first repository this ran against, all of them correct ICU.
    """
    en = tmp_path / "app_en.arb"
    en.write_text(
        json.dumps(
            {
                "rows": "{count, plural, =1{1 row could not be read} other{{count} rows could not be read}}",
                "days": "{days, plural, =1{1 day} other{{days} days}}",
            }
        ),
        encoding="utf-8",
    )
    assert find_plural_problems({"en": en}) == []


def test_a_plural_with_neither_the_category_nor_an_exact_selector_still_fails(tmp_path):
    en = tmp_path / "app_en.arb"
    en.write_text(
        json.dumps({"rows": "{count, plural, other{{count} rows could not be read}}"}),
        encoding="utf-8",
    )
    problems = find_plural_problems({"en": en})
    assert len(problems) == 1
    assert "needs ['one', 'other']" in problems[0]


def test_a_russian_plural_needs_its_own_categories_even_with_an_exact_one(tmp_path):
    ru = tmp_path / "app_ru.arb"
    ru.write_text(
        json.dumps({"rows": "{count, plural, =1{1 строка} other{{count} строк}}"}),
        encoding="utf-8",
    )
    problems = find_plural_problems({"ru": ru})
    assert len(problems) == 1
    assert "few" in problems[0] and "many" in problems[0]

