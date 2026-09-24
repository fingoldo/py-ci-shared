"""Shared check: every value a `.env.example` documents can actually be loaded by the settings class.

A name-parity check (is each documented variable a real field?) passes while the documented VALUE cannot be
loaded. glossum 2026-09-01 audit 12-H1: `.env.example` showed `CORS_ORIGINS=http://localhost:3000,http://
localhost:8080`, pydantic-settings JSON-decoded the list field before its comma-splitting validator could run, and
anyone copying the example got a SettingsError at startup. The name check was green the whole time. The fix that
had been cited as precedent for the same shape on another field was dead too, and only running it showed that.

This puts each documented value into a cleared environment, one variable at a time, and constructs the settings
class. Any exception is a failure: a documented value that cannot be loaded is a bug whichever field it is.

Three traps, each a parameter or a rule here, because each made a first version report the wrong thing:
* **A complete baseline environment.** A settings class with required fields refuses to build from one variable
  alone, which reads as every line failing. The caller passes ``base_env``: the minimum that builds.
* **Inline comments.** ``LOG_FORMAT=text          # "text" or "json"`` is dotenv's comment syntax; the value is
  ``text``. A ``#`` inside a value is kept unless whitespace precedes it, as dotenv does.
* **The shell's own environment.** A variable set on the developer's machine would mask the documented one, or
  make a missing required field look present. The environment is cleared for each trial and restored after.

Commented-out assignments (``# NAME=VALUE``) are checked too: that is how an example documents an optional
setting, and 12-H1's line was one. Placeholder values (``...``, ``<...>``, ``your-...``, ``changeme``, empty) are
skipped: they fail format validators for reasons that are not drift.

Usage::

    from py_ci_shared.env_example_round_trip import assert_env_example_loads

    def test_every_documented_value_loads():
        assert_env_example_loads(Settings, REPO / ".env.example", base_env={"DATABASE_URL": "postgresql://x"})
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ._core import read_source

#: ``NAME=VALUE``, ``# NAME=VALUE``, ``export NAME=VALUE`` and ``NAME = VALUE`` (dotenv accepts all four); NAME in the
#: shell's own shape.
_ASSIGNMENT = re.compile(r"^\s*(?:#\s*)?(?:export\s+)?(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>.*)$")
#: A dotenv inline comment: whitespace, then ``#`` to the end of the line.
_INLINE_COMMENT = re.compile(r"\s+#.*$")
DEFAULT_PLACEHOLDER = re.compile(r"^$|\.\.\.|^<.*>$|<[^>]+>|^your[-_]|changeme", re.IGNORECASE)


def documented_values(env_path: Path) -> list[tuple[int, str, str]]:
    """``(line number, NAME, value)`` for every assignment, commented out or not, inline comment stripped.

    A quoted value is read up to its closing quote first, so ``X="a #b"  # note`` is ``a #b``; an unquoted value loses
    an inline comment (whitespace, then ``#``). The file is decoded BOM-safe, so the first assignment is not lost.
    """
    out = []
    for lineno, line in enumerate(read_source(env_path).splitlines(), start=1):
        m = _ASSIGNMENT.match(line)
        if not m:
            continue
        out.append((lineno, m.group("name"), _value(m.group("value"))))
    return out


def _value(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        i = 1
        while i < len(raw):
            if raw[i] == "\\" and quote == '"':
                i += 2
                continue
            if raw[i] == quote:
                return raw[1:i]
            i += 1
    return _INLINE_COMMENT.sub("", raw).strip()


def env_names(settings_cls: type) -> dict[str, str]:
    """``{ENV_NAME: field}``: the prefix rule, or the validation alias where one is set, upper-cased. An
    ``AliasChoices`` contributes every choice, and an ``AliasPath`` its first element (the variable it reads)."""
    prefix = str(getattr(settings_cls, "model_config", {}).get("env_prefix", "") or "")
    names = {}
    for field, info in settings_cls.model_fields.items():  # type: ignore[attr-defined]
        aliases = _alias_names(getattr(info, "validation_alias", None))
        for name in aliases or [prefix + field]:
            names[name.upper()] = field
    return names


def _alias_names(alias: object) -> list[str]:
    """The environment names a pydantic validation alias reads: a str, ``AliasChoices(...).choices``, ``AliasPath``."""
    if alias is None:
        return []
    if isinstance(alias, str):
        return [alias]
    choices = getattr(alias, "choices", None)
    if isinstance(choices, (list, tuple)):
        return [n for choice in choices for n in _alias_names(choice)]
    path = getattr(alias, "path", None)
    if isinstance(path, (list, tuple)) and path and isinstance(path[0], str):
        return [path[0]]
    return []


@contextmanager
def _only_env(values: Mapping[str, str]) -> Iterator[None]:
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def unloadable_values(
    settings_cls: type,
    env_path: Path,
    *,
    base_env: "Mapping[str, str] | None" = None,
    placeholder: "re.Pattern[str]" = DEFAULT_PLACEHOLDER,
    skip: Iterable[str] = (),
) -> "tuple[list[str], int]":
    """(one problem per documented value the class cannot load, how many values were tried)."""
    base = dict(base_env or {})
    fields = env_names(settings_cls)
    skipped = set(skip)
    with _only_env(base):
        try:
            settings_cls(_env_file=None)
        except Exception as exc:  # the report IS the exception, whatever its type
            return [f"base_env alone does not build {settings_cls.__name__}: {type(exc).__name__}: {exc}"], 0
    problems, tried = [], 0
    for lineno, name, value in documented_values(env_path):
        if name not in fields or name in skipped or placeholder.search(value):
            continue
        tried += 1
        with _only_env({**base, name: value}):
            try:
                settings_cls(_env_file=None)
            except Exception as exc:  # any exception is the finding
                first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
                problems.append(f"{env_path.name}:{lineno}: {name}={value!r} does not load: {type(exc).__name__}: {first}")
    return problems, tried


def assert_env_example_loads(
    settings_cls: type,
    env_path: Path,
    *,
    base_env: "Mapping[str, str] | None" = None,
    placeholder: "re.Pattern[str]" = DEFAULT_PLACEHOLDER,
    skip: Iterable[str] = (),
    min_values: int = 1,
) -> None:
    """Fail on any documented value that cannot be loaded, and when fewer than *min_values* were tried."""
    import pytest

    problems, tried = unloadable_values(settings_cls, env_path, base_env=base_env, placeholder=placeholder, skip=skip)
    if problems:
        pytest.fail(f"{len(problems)} value(s) documented in {env_path.name} cannot be loaded:\n  " + "\n  ".join(problems))
    if tried < min_values:
        pytest.fail(f"only {tried} documented value(s) were tried from {env_path.name}; expected at least {min_values} -- the pattern or the field map broke")
