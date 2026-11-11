"""Dated lag waivers for ``adoption_matrix`` (``--waivers FILE``); split out of it to keep it under the size budget."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Union

from ._core import CoreError
from ._toml_compat import tomllib
from .version_tag_currency import is_release_tag

__all__ = ["Waiver", "load_waivers", "waived_allowance"]

PathLike = Union[str, Path]


@dataclass(frozen=True)
class Waiver:
    """An accepted lag for one consumer: it may pin as far back as ``oldest`` until the day ``until`` (inclusive)."""

    name: str
    oldest: str
    until: date
    reason: str


def load_waivers(path: PathLike) -> dict[str, Waiver]:
    """The ``[[waiver]]`` tables of ``path`` by consumer name; a missing key, a bad date or a non-release ``oldest`` is a ``CoreError``."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, Waiver] = {}
    for row in data.get("waiver", []):
        try:
            waiver = Waiver(str(row["name"]), str(row["oldest"]), date.fromisoformat(str(row["until"])), str(row["reason"]))
        except (KeyError, ValueError) as exc:
            raise CoreError(f"{path}: a [[waiver]] needs name, oldest (vX.Y.Z), until (YYYY-MM-DD) and reason: {exc!r}") from exc
        if not is_release_tag(waiver.oldest) or not waiver.reason.strip():
            raise CoreError(f"{path}: waiver {waiver.name!r}: oldest must be a vX.Y.Z tag and reason must be given")
        if waiver.name in out:  # 47-F15: a second row with the same name would replace the first and silently drop its dates and reason
            raise CoreError(f"{path}: waiver {waiver.name!r} is listed twice; keep one row per consumer")
        out[waiver.name] = waiver
    return out


def waived_allowance(waiver: Optional[Waiver], releases: Sequence[str], base: int, today: date) -> tuple[int, str]:
    """``(allow_behind, note)`` for one consumer: ``base``, widened to the releases after ``waiver.oldest`` while the waiver has not expired."""
    if waiver is None:
        return base, ""
    if today > waiver.until:
        return base, f"waiver for {waiver.name} expired on {waiver.until}: bump the pin or renew the waiver ({waiver.reason})"
    if waiver.oldest not in releases:
        return base, f"waiver for {waiver.name} names {waiver.oldest}, which is not a release tag here; it grants nothing"
    after = len(releases) - 1 - list(releases).index(waiver.oldest)
    return max(base, after), f"waived until {waiver.until} down to {waiver.oldest} ({waiver.reason})"


def preflight_problem(has_resolver: bool, releases: Sequence[str], waivers: Mapping[str, Waiver], scanned: Sequence[str], advisory: bool) -> Optional[str]:
    """Why the run must fail before any repo is read, or ``None``. A resolver without release tags cannot run the stale-pin check (47-F14);
    a waiver for no scanned consumer applies to nothing (47-F15). ``advisory`` reports only, so it never returns a problem."""
    if advisory:
        return None
    if has_resolver and not releases:
        return ("--resolve-in has no vX.Y.Z release tags (a shallow clone, or the tags were not fetched), so the stale-pin check did not run: "
                "run git fetch --tags there, or pass --advisory")
    unused = sorted(set(waivers) - set(scanned))
    if unused:
        return f"waiver(s) for no scanned consumer, so they apply to nothing: {', '.join(unused)}"
    return None
