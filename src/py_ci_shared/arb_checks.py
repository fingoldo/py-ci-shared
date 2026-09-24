"""Shared checks over Flutter ``.arb`` localization catalogues.

Pure JSON rules with no app knowledge, so every product that ships ARBs gets the same gate:

1. **Key parity.** A key present in one locale and absent from another falls back to the template
   locale at runtime -- English text inside a Russian sentence, or a crash in a generator that
   assumes parity.
2. **Counted values use ICU plural.** A value whose key reads as a count and that contains words
   outside its placeholders needs plural branches; ``'{count} days'`` is wrong in English for 1 and
   wrong in Russian for almost everything.
3. **Every plural covers its locale's categories, and every branch keeps the placeholder.** This is
   the rule that a template-locale-only check cannot express: English needs ``one``/``other``,
   Russian needs ``one``/``few``/``many``/``other``. glossum P08-1 (2026-09-02): the Russian
   plurals were written with English's two branches, so every count from 2 to 4 rendered the
   ``other`` form. A branch that drops ``{count}`` renders a sentence with no number in it at all
   (the literal ``1``/``один`` spelled out in a ``one`` branch is accepted).
4. **A ``{count}`` placeholder outside a plural.** glossum P08-12: a key not named ``*Count`` still
   pluralizes, so the name-based rule 2 misses it. Values whose remaining text is only separators or
   a ratio (``{index} of {count}``) are accepted -- they are displays, not sentences.
5. **Register consistency (advisory).** Mixing formal and informal address (``tu``/``vous``,
   ``ты``/``вы``) inside one product reads as two different applications. glossum P08-4.
6. **Dead keys.** A key with no call site is a translation someone paid for and nobody renders. The
   caller supplies the source text and the call patterns, because how a key is read is
   framework-specific (``l10n.key``, ``AppLocalizations.of(context)!.key``).

Deliberately dependency-free (``json`` + ``re``); the caller passes paths, so the module never
guesses a project layout.

Usage::

    from py_ci_shared.arb_checks import assert_arb_catalogues_are_sound

    assert_arb_catalogues_are_sound(
        {"en": L10N / "app_en.arb", "fr": L10N / "app_fr.arb", "ru": L10N / "app_ru.arb"},
        template_locale="en",
        source_text=all_dart_source,
    )
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from ._core import read_source

# CLDR plural categories that a locale must cover for a count to render correctly. Only the locales
# a caller is likely to ship are listed; an unlisted locale falls back to {"other"}, which every
# language needs.
PLURAL_CATEGORIES: Mapping[str, frozenset[str]] = {
    "en": frozenset({"one", "other"}),
    "de": frozenset({"one", "other"}),
    "es": frozenset({"one", "other"}),
    "it": frozenset({"one", "other"}),
    "nl": frozenset({"one", "other"}),
    "pt": frozenset({"one", "other"}),
    "fr": frozenset({"one", "other"}),
    "ru": frozenset({"one", "few", "many", "other"}),
    "uk": frozenset({"one", "few", "many", "other"}),
    "pl": frozenset({"one", "few", "many", "other"}),
    "cs": frozenset({"one", "few", "other"}),
    "ar": frozenset({"zero", "one", "two", "few", "many", "other"}),
    "ja": frozenset({"other"}),
    "zh": frozenset({"other"}),
    "ko": frozenset({"other"}),
    "tr": frozenset({"one", "other"}),
}

_COUNT_LIKE_KEY_RE = re.compile(r"(count|days|results|items|total)$", re.IGNORECASE)
_PLURAL_HEAD_RE = re.compile(r"\{\s*\w+\s*,\s*plural\s*,")
_SELECTOR_RE = re.compile(r"\s*(offset:\s*\d+\s*)?(=\d+|zero|one|two|few|many|other)\s*\{")

# Kept for callers that imported it. Coverage is now decided by FINITE_CATEGORY_VALUES below.
_EXACT_COVERS = {"=0": "zero", "=1": "one", "=2": "two"}

# An exact selector covers a category only when that category is a FINITE set of integers that the exact
# selectors present enumerate completely: ICU checks `=N` first, so `=1{..}` renders 1 correctly, but in ru/uk
# `one` also holds 21, 31, ... which then fall through to `other`, and in fr/pt `one` holds 0 as well as 1.
# Categories not listed here for a locale are treated as infinite (never covered by exact selectors).
FINITE_CATEGORY_VALUES: Mapping[str, Mapping[str, frozenset[int]]] = {
    "en": {"one": frozenset({1})},
    "de": {"one": frozenset({1})},
    "es": {"one": frozenset({1})},
    "it": {"one": frozenset({1})},
    "nl": {"one": frozenset({1})},
    "tr": {"one": frozenset({1})},
    "cs": {"one": frozenset({1})},
    "pl": {"one": frozenset({1})},
    "fr": {"one": frozenset({0, 1})},
    "pt": {"one": frozenset({0, 1})},
    "ar": {"zero": frozenset({0}), "one": frozenset({1}), "two": frozenset({2})},
}
_PLURAL_ARG_RE = re.compile(r",\s*plural\s*,")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)[^}]*\}")
_WORDS_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
# The text a placeholder is followed by, when it starts with a word, makes the placeholder a
# counted noun phrase ("{count} days"). A placeholder at the end of a clause, or followed by
# punctuation or a separator ("{index} of {count}", "{current} / {total}"), is a ratio or a
# position display: those are not pluralized in any language, and flagging them is noise.
_FOLLOWED_BY_WORD_RE = re.compile(r"^\s+[^\W\d_]{2,}", re.UNICODE)
_INFORMAL_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "fr": (r"\btu\b", r"\bton\b", r"\bta\b", r"\btes\b", r"\btoi\b"),
    "ru": (r"\bты\b", r"\bтебя\b", r"\bтебе\b", r"\bтвой\b", r"\bтвоя\b", r"\bтвои\b"),
}


class TemplateLocaleError(KeyError):
    """The template locale is not one of the catalogues passed in."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


def _messages(path: Path) -> dict[str, str]:
    data = json.loads(read_source(path))
    return {k: v for k, v in data.items() if not k.startswith("@") and isinstance(v, str)}


def _template(catalogues: Mapping[str, Path], template_locale: str) -> Path:
    if template_locale not in catalogues:
        raise TemplateLocaleError(
            f"template locale {template_locale!r} is not among the catalogues {sorted(catalogues)}; " "pass template_locale= matching one of those keys"
        )
    return catalogues[template_locale]


def find_key_parity_problems(catalogues: Mapping[str, Path], template_locale: str) -> list[str]:
    """Return one problem string per locale whose key set differs from the template's."""
    base = set(_messages(_template(catalogues, template_locale)))
    out: list[str] = []
    for loc, path in catalogues.items():
        if loc == template_locale:
            continue
        keys = set(_messages(path))
        missing = sorted(base - keys)
        extra = sorted(keys - base)
        if missing:
            out.append(f"{path.name}: missing {len(missing)} key(s) present in {template_locale}: {missing[:10]}")
        if extra:
            out.append(f"{path.name}: has {len(extra)} key(s) absent from {template_locale}: {extra[:10]}")
    return out


def _plural_branches(value: str) -> dict[str, str]:
    """Return ``{category: branch_text}`` for an ICU plural value.

    Brace-aware rather than a flat regex: a branch body legitimately contains its own placeholder
    (``one{{count} день}``), so ``[^{}]*`` matches nothing and every branch of every correctly
    written plural disappears -- which would make this checker report a missing category on exactly
    the strings that are right.

    Only the selectors at the top level of each plural are heads: a body such as ``other{someone {name}}``
    contains ``one {`` as text, which is not a branch.
    """
    branches: dict[str, str] = {}
    pos = 0
    while True:
        head = _PLURAL_HEAD_RE.search(value, pos)
        if head is None:
            return branches
        i = head.end()
        while True:
            m = _SELECTOR_RE.match(value, i)
            if m is None:
                break
            end = _close(value, m.end())
            branches.setdefault(m.group(2), value[m.end() : end - 1])
            i = end
        pos = max(i, head.end())


def _close(value: str, start: int) -> int:
    """Index just past the ``}`` that closes the brace opened right before *start*."""
    depth = 1
    i = start
    while i < len(value) and depth:
        if value[i] == "{":
            depth += 1
        elif value[i] == "}":
            depth -= 1
        i += 1
    return i


def _is_plural(value: str) -> bool:
    return bool(_PLURAL_ARG_RE.search(value))


def _covered(locale: str, branches: Iterable[str]) -> set[str]:
    names = set(branches)
    exact = {int(b[1:]) for b in names if b.startswith("=")}
    finite = FINITE_CATEGORY_VALUES.get(locale, {})
    return names | {cat for cat, values in finite.items() if values <= exact}


def _needs_plural(key: str, value: str) -> bool:
    """True when ``value`` reads as a counted noun phrase with no ICU plural.

    The test is what FOLLOWS the count placeholder: ``{count} days`` is a noun phrase that
    pluralizes, while ``{index} of {count}`` and ``{current} / {total}`` are ratio displays that do
    not pluralize in any language.
    """
    if _is_plural(value) or "{" not in value:
        return False
    count_like_key = bool(_COUNT_LIKE_KEY_RE.search(key))
    for m in _PLACEHOLDER_RE.finditer(value):
        is_count = m.group(1).lower() == "count"
        if not is_count and not count_like_key:
            continue
        if _FOLLOWED_BY_WORD_RE.match(value[m.end() :]):
            return True
    return False


def find_plural_problems(catalogues: Mapping[str, Path]) -> list[str]:
    """Return one problem string per counted value with no ICU plural, per plural missing a
    category its locale needs, and per branch that dropped the placeholder."""
    out: list[str] = []
    for loc, path in catalogues.items():
        lang = loc.split("_")[0].split("-")[0].lower()
        required = PLURAL_CATEGORIES.get(lang, frozenset({"other"}))
        for key, value in _messages(path).items():
            if _needs_plural(key, value):
                out.append(
                    f"{path.name}: '{key}' reads as a counted phrase ({value!r}) with no ICU " f"plural. It is wrong for at least one count in this locale."
                )
                continue
            if not _is_plural(value):
                continue
            branches = _plural_branches(value)
            covered = _covered(lang, branches)
            missing = sorted(required - covered)
            if missing:
                out.append(
                    f"{path.name}: '{key}' has plural branches {sorted(branches)} but {loc} needs "
                    f"{sorted(required)} - counts falling in {missing} render the wrong form."
                )
            for name, text in branches.items():
                if "{" in text or "#" in text or not text.strip():
                    continue
                # A `one` branch may spell the number out ("1 blank", "один"); any other branch
                # with no placeholder renders a sentence with no number in it.
                if name not in {"one", "=0", "=1", "=2"} and not re.search(r"\d", text):
                    out.append(f"{path.name}: '{key}' branch '{name}' has no placeholder and no digit " f"({text!r}) - this count renders without its number.")
    return out


def find_register_problems(catalogues: Mapping[str, Path]) -> list[str]:
    """Advisory: return keys whose value uses informal address in a locale that has a formal form."""
    out: list[str] = []
    for loc, path in catalogues.items():
        patterns = _INFORMAL_PATTERNS.get(loc.split("_")[0].lower())
        if not patterns:
            continue
        for key, value in _messages(path).items():
            for pattern in patterns:
                if re.search(pattern, value, re.IGNORECASE):
                    out.append(f"{path.name}: '{key}' uses informal address ({value[:60]!r})")
                    break
    return out


def find_dead_keys(
    catalogues: Mapping[str, Path],
    template_locale: str,
    source_text: str,
    *,
    call_patterns: Sequence[str] = (
        # `\s*` around the dot: dart format wraps a long chain as `l10n\n    .key`; `[!?]?` accepts `of(context)?.key`.
        r"l10n\s*\.\s*([A-Za-z]\w*)",
        r"AppLocalizations\.of\([^)]*\)[!?]?\s*\.\s*([A-Za-z]\w*)",
        r"localizations\s*\.\s*([A-Za-z]\w*)",
    ),
    allowed: Iterable[str] = (),
) -> list[str]:
    """Return every template-locale key with no call site in ``source_text``.

    ``allowed`` lists keys read by something other than a Dart call site (a code generator, a route
    snapshot tool); each entry must be justified where the caller declares it.
    """
    called: set[str] = set()
    for pattern in call_patterns:
        called.update(re.findall(pattern, source_text))
    allow = set(allowed)
    return [key for key in sorted(_messages(_template(catalogues, template_locale))) if key not in called and key not in allow]


def assert_arb_catalogues_are_sound(
    catalogues: Mapping[str, Path],
    *,
    template_locale: str = "en",
    source_text: "str | None" = None,
    allowed_dead_keys: Iterable[str] = (),
    register_advisory: bool = True,
) -> None:
    """Fail on key parity, plural or dead-key problems; print register findings as advisories."""
    import pytest

    problems: list[str] = []
    problems.extend(find_key_parity_problems(catalogues, template_locale))
    problems.extend(find_plural_problems(catalogues))
    if source_text is not None:
        dead = find_dead_keys(catalogues, template_locale, source_text, allowed=allowed_dead_keys)
        if dead:
            problems.append(
                f"{len(dead)} key(s) defined and never rendered - a translation nobody sees, " f"kept current by every future translator: {dead[:15]}"
            )
    if register_advisory:
        for line in find_register_problems(catalogues):
            print(f"ADVISORY register: {line}")
    if problems:
        pytest.fail(f"{len(problems)} ARB problem(s):\n  " + "\n  ".join(problems))
