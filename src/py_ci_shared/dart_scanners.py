"""Shared structural scanners over Dart/Flutter source.

Regex rules, one per finding class from the 2026-09-02 glossum / flutter_app_core audit round.
They live here rather than in either repo because both Flutter repos -- and the next one -- have
the same widgets, the same painters and the same provider library, so the rule text should exist
once. Each scanner takes the caller's own file list and reader, so this module never guesses a
project layout, and returns ``{key: description}`` in the shape the repos' existing
``tool/meta/baseline.py`` ratchet already consumes.

Every rule here is regex over source, which means every rule here is approximate. That is why they
are baselined rather than enforced outright: the ratchet's job is to stop a NEW instance appearing
without anyone deciding it is right, not to prove the existing ones are wrong.

Rules and the findings that motivated them (P = glossum, C = flutter_app_core):

* :func:`scan_painter_animation` - ``shouldRepaint => true`` (P02-6), ``Paint()``/``Path()`` built
  inside a loop inside ``paint()`` (P09-13, C04-17), the platform-dispatcher reduced-motion source
  instead of ``MediaQuery`` (C04-5, C04-16), and ``ActivateIntent`` handled without
  ``ButtonActivateIntent`` -- which is why Enter did nothing on the web build (C04-1).
* :func:`scan_repaint_isolation` - a repeating animation with no ``RepaintBoundary`` anywhere in
  the file (P02-2, P02-3, P09-7, C04-4, C04-15).
* :func:`scan_hardcoded_ui_strings` - user-visible text baked into Dart: enum constructor
  arguments (P05-12, P07-18, C02-4), literal ``Text``/``tooltip``/``label`` (P06-9, P08-8, P08-15,
  C02-10), capitalised ``??`` fallbacks (P05-19, P08-14), and raw ``Color(0x`` outside a palette
  (P05-15).
* :func:`scan_tappable_semantics` - unlabelled spinners (P05-14, C04-20), a tappable with no
  minimum tap target (P05-5, P05-6; a verified ``target_wrappers`` widget or a reasoned
  ``// tap-target: inline-text`` marker on a text-only tappable also satisfies it) or no button semantics (P05-11), ``onTap`` paired with
  ``onDoubleTap`` (P05-2), a named ``BuildContext`` parameter (P05-10), and a tooltip duplicating a
  semantics label without ``excludeFromSemantics`` (C04-13).
* :func:`scan_non_directional_layout` - ``Alignment.centerLeft`` and friends, which do not flip in
  a right-to-left locale (P08-17, P09-11).
* :func:`scan_parse_serialize_catch` - ``toIso8601String`` without ``toUtc`` (P01-17, C01-14),
  ``jsonDecode`` outside a ``try`` (P01-10), an enum-from-string helper with a non-nullable default
  (P01-8, C01-13), a fire-and-forget ``.then`` with no error path (P02-1), and ``SocketException``
  caught without ``ClientException`` on a codebase that also runs on the web (C01-2).
* :func:`scan_provider_state_hygiene` - ``DateTime.now()`` inside a provider that has a clock seam
  (P01-12, C01-5), an unawaited preferences write (C01-17), and a ``toString()`` that interpolates
  a personal field (C05-10).

Eight more that both Flutter repos carried as local copies: :func:`scan_file_size`, :func:`scan_empty_catch`,
:func:`scan_source_text_assertions` and :func:`scan_tests_without_assertions` (pass the test files),
:func:`scan_import_cycles` (``import`` and ``export``), :func:`scan_timed_dismissal`,
:func:`scan_double_error_reports` and :func:`scan_unused_test_seams`. :func:`dart_files_under`,
:func:`dart_reader` and :func:`package_name` are the file listing, reader and pubspec lookup those copies shared.

Usage (from a repo's own ``tool/meta/scanners.py``)::

    from py_ci_shared.dart_scanners import scan_painter_animation
    SCANS["painter-animation"] = lambda: scan_painter_animation(list(dart_files()), read)
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path

from ._core import SourceError, read_source
from ._core.source import PathLike

Reader = Callable[[str], str]

from ._dart_source import (  # noqa: F401  # the scanners' shared helpers
    _balanced_body,
    _enclosing_calls,
    _enclosing_semantics_args,
    _key,
    _line_of,
    _mask_strings,
    _rekey,
    _strip_comments,
    _top_level_args,
)

logger = logging.getLogger(__name__)


def _method_bodies(source: str, name_pattern: str) -> list[tuple[int, str]]:
    """Return ``(start_index, body)`` for every method whose signature matches ``name_pattern``."""
    out: list[tuple[int, str]] = []
    for m in re.finditer(name_pattern, source):
        brace = source.find("{", m.end())
        if brace == -1:
            continue
        out.append((m.start(), _balanced_body(source, brace)))
    return out


# --------------------------------------------------------------------------------------------
# M23 - painter and animation micro-rules

_SHOULD_REPAINT_TRUE = re.compile(r"bool\s+shouldRepaint\([^)]*\)\s*(?:=>\s*true\s*;|\{\s*return\s+true\s*;\s*\})")
_PAINT_SIGNATURE = r"void\s+paint\s*\(\s*Canvas\s+\w+\s*,\s*Size\s+\w+\s*\)\s*"
_LOOP_HEAD = re.compile(r"\b(?:for|while)\s*\(")
_ALLOC_IN_LOOP = re.compile(r"\b(?:Paint|Path)\s*\(\s*\)")
_PLATFORM_DISPATCHER_MOTION = re.compile(r"platformDispatcher\s*\.\s*accessibilityFeatures\s*\.\s*disableAnimations")
_ACTIVATE_INTENT = re.compile(r"\bActivateIntent\b")
_BUTTON_ACTIVATE_INTENT = re.compile(r"\bButtonActivateIntent\b")


def scan_painter_animation(files: Iterable[str], read: Reader) -> dict:
    """Painter/animation rules: see the module docstring."""
    found: dict[str, str] = {}
    for rel in files:
        source = read(rel)
        clean = _strip_comments(source)

        for m in _SHOULD_REPAINT_TRUE.finditer(clean):
            found[_key(found, rel)] = (
                f"shouldRepaint always returns true (line {_line_of(clean, m.start())}) - the "
                f"painter repaints on every frame of every rebuild, whatever changed"
            )

        for start, body in _method_bodies(clean, _PAINT_SIGNATURE):
            for loop in _LOOP_HEAD.finditer(body):
                brace = body.find("{", loop.end())
                if brace == -1:
                    continue
                loop_body = _balanced_body(body, brace)
                if _ALLOC_IN_LOOP.search(loop_body):
                    found[_key(found, rel)] = (
                        f"Paint()/Path() allocated inside a loop inside paint() (line " f"{_line_of(clean, start)}) - one allocation per iteration per frame"
                    )
                    break

        for m in _PLATFORM_DISPATCHER_MOTION.finditer(clean):
            found[_key(found, rel)] = (
                f"reduced motion read from the platform dispatcher (line "
                f"{_line_of(clean, m.start())}) - build() reads MediaQuery, so the two can "
                f"disagree: a ticker driving nothing, or a frozen sweep"
            )

        if (activate := _ACTIVATE_INTENT.search(clean)) and not _BUTTON_ACTIVATE_INTENT.search(clean):
            found[_key(found, rel)] = (
                f"ActivateIntent handled without ButtonActivateIntent (line "
                f"{_line_of(clean, activate.start())}) - on the web Enter maps to ButtonActivateIntent, "
                f"so the key does nothing there"
            )
    return _rekey(found)


# --------------------------------------------------------------------------------------------
# M7 - repaint isolation

_REPEATING_ANIMATION = re.compile(r"\.repeat\s*\(|AnimatedBuilder\s*\(\s*animation:|super\(repaint:")
# A file with no widget and no painter has no subtree to isolate: a utility that calls
# `controller.repeat()` on a caller's controller is not the thing that repaints.
_PAINTS_SOMETHING = re.compile(r"Widget\s+build\s*\(\s*BuildContext|extends\s+CustomPainter|extends\s+\w*Painter\b")
_REPAINT_BOUNDARY = re.compile(r"\bRepaintBoundary\s*\(")
_ANIMATED_BUILDER = re.compile(r"AnimatedBuilder\s*\(")
_EXPENSIVE_CHILD = re.compile(r"\bImage\.asset\s*\(|\bImage\.network\s*\(")


def scan_repaint_isolation(files: Iterable[str], read: Reader) -> dict:
    """A repeating animation in a file with no RepaintBoundary anywhere."""
    found: dict[str, str] = {}
    for rel in files:
        source = read(rel)
        clean = _strip_comments(source)
        m = _REPEATING_ANIMATION.search(clean)
        if m and _PAINTS_SOMETHING.search(clean) and not _REPAINT_BOUNDARY.search(clean):
            found[_key(found, rel)] = (
                f"repeating animation (line {_line_of(clean, m.start())}) with no RepaintBoundary "
                f"in the file - everything painted beneath it repaints every frame"
            )
        for ab in _ANIMATED_BUILDER.finditer(clean):
            body = _balanced_body(clean, clean.find("(", ab.end() - 1), "(", ")")
            if _EXPENSIVE_CHILD.search(body) and "child:" not in body:
                found[_key(found, rel)] = (
                    f"AnimatedBuilder rebuilding an image with no `child:` slot (line "
                    f"{_line_of(clean, ab.start())}) - the static subtree is rebuilt every frame"
                )
    return _rekey(found)


# --------------------------------------------------------------------------------------------
# M8 - hardcoded user-visible strings

_ENUM_HEAD = re.compile(r"\benum\s+(\w+)\s*\{")
_ENUM_STRING_ARG = re.compile(r"""\(\s*['"]([A-Z][^'"]{2,})['"]""")
# An all-caps token is a code (currency, locale, HTTP verb), not a sentence anyone reads.
_CODE_LIKE_RE = re.compile(r"^[A-Z0-9_]+$")
_LITERAL_TEXT = re.compile(
    r"""(?:\bText\s*\(\s*|\btooltip:\s*|\blabel:\s*|\bhintText:\s*|\bsemanticLabel:\s*|\btitle:\s*Text\s*\(\s*)['"]([A-Z][^'"]{3,})['"]"""
)
_CAPITALISED_FALLBACK = re.compile(r"""\?\?\s*['"]([A-Z][A-Za-z ]{2,})['"]""")
_RAW_COLOR = re.compile(r"\bColor\(0x[0-9A-Fa-f]{6,8}\)")
_LOG_CALL = re.compile(r"\b(?:AppLog|SharedLog|debugPrint|print)\s*[.(]")


def _is_non_ui_literal(clean: str, masked: str, m: re.Match, non_ui_label_calls: Sequence[str]) -> bool:
    """A literal on a log-call line, or a ``label:`` argument of a call in *non_ui_label_calls* (a log label, never rendered)."""
    line_start = clean.rfind("\n", 0, m.start()) + 1
    if _LOG_CALL.search(clean[line_start : clean.find("\n", m.start())]):
        return True
    if not m.group(0).startswith("label:"):
        return False
    innermost = next((name for name, _ in _enclosing_calls(masked, m.start())), None)
    return innermost in non_ui_label_calls


def scan_hardcoded_ui_strings(
    files: Iterable[str],
    read: Reader,
    *,
    palette_markers: Sequence[str] = (
        "palette",
        "theme_colors",
        "colors.dart",
        "painter",
        "/styles/",
        "_style.dart",
        "high_contrast",
        "min_tap_target_box",
    ),
    non_ui_label_calls: Sequence[str] = ("retryTransient",),
) -> dict:
    """User-visible text or colour baked into Dart instead of coming from l10n / the palette.

    A ``label:`` argument whose innermost enclosing call is named in *non_ui_label_calls* is a log or diagnostic
    label (``retryTransient(..., label: 'GET $path')`` names the request in its retry log line), never rendered.
    """
    found: dict[str, str] = {}
    for rel in files:
        source = read(rel)
        clean = _strip_comments(source)
        masked = _mask_strings(clean)
        lower = rel.lower()

        for em in _ENUM_HEAD.finditer(clean):
            body = _balanced_body(clean, clean.find("{", em.end() - 1))
            # Only the constant list, which ends at the first `;`. A Dart enum may carry methods,
            # and a log message inside one of them is not a display string.
            constants = body.split(";", 1)[0]
            for sm in _ENUM_STRING_ARG.finditer(constants):
                found[_key(found, rel)] = (
                    f"enum {em.group(1)} carries the display string {sm.group(1)!r} - an enum is " f"an identity, and a name in it can never be translated"
                )
                break

        for m in _LITERAL_TEXT.finditer(clean):
            if _is_non_ui_literal(clean, masked, m, non_ui_label_calls):
                continue
            found[_key(found, rel)] = (
                f"user-visible literal {m.group(1)[:40]!r} (line {_line_of(clean, m.start())}) - " f"every other string on this screen comes from l10n"
            )

        for m in _CAPITALISED_FALLBACK.finditer(clean):
            if _CODE_LIKE_RE.match(m.group(1)):
                continue
            found[_key(found, rel)] = (
                f"capitalised fallback ?? {m.group(1)!r} (line {_line_of(clean, m.start())}) - a " f"default that renders in English whatever the locale"
            )

        if not any(marker in lower for marker in palette_markers):
            for m in _RAW_COLOR.finditer(clean):
                found[_key(found, rel)] = (
                    f"raw {m.group(0)} (line {_line_of(clean, m.start())}) outside a palette - it " f"cannot follow the theme and no contrast check can see it"
                )
    return _rekey(found)


# --------------------------------------------------------------------------------------------
# M11 - tappable and semantics hygiene

_SPINNER = re.compile(r"CircularProgressIndicator\s*\(")
_TAPPABLE = re.compile(r"\b(?:InkWell|GestureDetector)\s*\(")
_ON_TAP = re.compile(r"\bonTap:\s*(?!null)")
_ON_DOUBLE_TAP = re.compile(r"\bonDoubleTap:\s*(?!null)")
# Either the shared helper, or an explicit constraint naming the same constant: a widget that
# writes `ConstrainedBox(minHeight: AppConstants.minTapTarget)` has done the thing the helper
# exists to do, and demanding the helper by name would be a style rule wearing an a11y label.
_MIN_TAP_TARGET = re.compile(r"\bMinTapTargetBox\s*\(|\bminTapTarget\b")
# Any announced ROLE, not just `button: true`: a segmented control is correctly announced with
# inMutuallyExclusiveGroup/checked, and a like button with toggled. Demanding `button: true`
# everywhere would push widgets towards the WRONG role.
# Matched against the top-level arguments of a Semantics that ENCLOSES the widget (bracket nesting, not a window).
_SEMANTICS_ROLE_ARG = re.compile(r"\b(?:button:\s*true|inMutuallyExclusiveGroup:|checked:|selected:|toggled:)")
_SEMANTICS_LABEL_ARG = re.compile(r"\blabel:")
_NAMED_CONTEXT_PARAM = re.compile(r"\{[^}]*?required\s+BuildContext\s+context\b", re.DOTALL)
_BUILD_HELPER = re.compile(r"\bWidget\s+_build\w*\s*\(")
_TOOLTIP_MESSAGE = re.compile(r"Tooltip\s*\(\s*message:\s*([^,\n]+)")
# The widget that PROVIDES the minimum tap target cannot be asked to wrap itself in one.
_TAP_TARGET_HELPER_FILES = ("min_tap_target_box",)
# Either spelling closes the double announcement: `excludeFromSemantics` on the tooltip drops the
# tooltip's own node, and `excludeSemantics` on the enclosing Semantics drops every descendant's.
_EXCLUDE_FROM_SEMANTICS = re.compile(r"exclude(?:FromSemantics|Semantics):\s*true")


# A project widget that guarantees a smaller-than-48 but still WCAG 2.5.8 AA target (24x24) counts as wrapping only
# when the caller names it AND its own definition, found in the scanned files, constrains BOTH minWidth and minHeight
# to at least 24. Naming a widget that does not is a finding, so a configured name cannot exempt an ordinary button.
_WCAG_AA_MIN_TARGET = 24
_MIN_DIMENSION = re.compile(r"\bmin(Width|Height):\s*(?:(\d+(?:\.\d+)?)\b|[\w.]*minTapTarget\b)")
# The inline-text exemption: WCAG 2.5.8 exempts a target that sits in a sentence of text. It is a comment directly
# above the tappable with a reason, and it holds only when the tappable's child is text and nothing else.
_INLINE_TEXT_MARKER = re.compile(r"^\s*//\s*tap-target:\s*inline-text\b\s*-?\s*(.*)$")
_INLINE_REASON_MIN_CHARS = 10
_TEXT_ONLY_CALLEES = frozenset(
    {"Semantics", "MergeSemantics", "ExcludeSemantics", "Text", "RichText", "TextSpan", "TextStyle", "TextDecoration", "DefaultTextStyle", "Theme"}
)
_TEXT_CALLEES = frozenset({"Text", "RichText", "TextSpan"})
_CALLEE = re.compile(r"\b([A-Z]\w*)(?:\.\w+)?\s*\(")


def _definition_body(clean: str, name: str) -> str | None:
    """The body of ``class <name>`` or of a function ``Widget <name>(...)`` in *clean*, or None."""
    m = re.search(r"\bclass\s+" + re.escape(name) + r"\b[^{]*\{", clean)
    if m:
        return _balanced_body(clean, m.end() - 1)
    m = re.search(r"\bWidget\s+" + re.escape(name) + r"\s*\(", clean)
    if m is None:
        return None
    after_params = m.end() + len(_balanced_body(clean, m.end() - 1, "(", ")")) + 1
    tail = clean[after_params:]
    if tail.lstrip().startswith("=>"):
        end = tail.find(";")
        return tail if end == -1 else tail[:end]
    brace = tail.find("{")
    return _balanced_body(tail, brace) if brace != -1 else None


def _guarantees_aa_target(body: str) -> bool:
    """Whether *body* constrains both minWidth and minHeight to at least the WCAG 2.5.8 AA 24px."""
    ok = {"Width": False, "Height": False}
    for m in _MIN_DIMENSION.finditer(body):
        if m.group(2) is None or float(m.group(2)) >= _WCAG_AA_MIN_TARGET:
            ok[m.group(1)] = True
    return ok["Width"] and ok["Height"]


def _verified_target_wrappers(cleaned: dict[str, str], target_wrappers: Sequence[str], found: dict[str, str]) -> frozenset[str]:
    """The configured wrappers whose definitions guarantee a 24x24 target; a finding for each one that does not."""
    verified = set()
    for name in target_wrappers:
        bodies = [b for b in (_definition_body(clean, name) for clean in cleaned.values()) if b is not None]
        if bodies and all(_guarantees_aa_target(b) for b in bodies):
            verified.add(name)
            continue
        why = "is defined in none of the scanned files" if not bodies else "does not constrain both minWidth and minHeight to at least 24"
        found[f"target_wrappers:{name}#0"] = f"configured tap-target wrapper {name} {why} - it cannot stand in for MinTapTargetBox"
    return frozenset(verified)


def _inline_text_exemption(source: str, clean: str, start: int) -> str | None:
    """None when no inline-text marker sits directly above the tappable at *start*, ``"ok"`` when a valid one does,
    and the reason it is invalid otherwise."""
    lines = source.split("\n")
    i = clean.count("\n", 0, start) - 1
    marker = None
    while i >= 0 and lines[i].lstrip().startswith("//"):
        marker = _INLINE_TEXT_MARKER.match(lines[i])
        if marker:
            break
        i -= 1
    if marker is None:
        return None
    if len(marker.group(1).strip()) < _INLINE_REASON_MIN_CHARS:
        return "the inline-text marker gives no reason"
    args = _balanced_body(_mask_strings(clean), clean.find("(", start), "(", ")")
    child = re.search(r"\bchild:", args)
    widgets = {m.group(1) for m in _CALLEE.finditer(args[child.end() :])} if child else set()
    others = sorted(widgets - _TEXT_ONLY_CALLEES)
    if others or not widgets & _TEXT_CALLEES:
        return "its child is not text alone (" + (", ".join(others) or "no Text") + ")"
    return "ok"


def _report_small_target(found: dict[str, str], rel: str, source: str, clean: str, start: int) -> None:
    line = _line_of(clean, start)
    verdict = _inline_text_exemption(source, clean, start)
    if verdict == "ok":
        return
    if verdict is not None:
        found[_key(found, rel)] = f"invalid inline-text tap-target exemption (line {line}) - {verdict}"
        return
    found[_key(found, rel)] = f"tappable with no MinTapTargetBox (line {line}) - the visible target can be smaller than the 48x48 minimum"


def _check_spinner_labels(found: dict[str, str], rel: str, clean: str, masked: str) -> None:
    """CircularProgressIndicator with no semanticsLabel of its own or on an enclosing Semantics."""
    for m in _SPINNER.finditer(clean):
        body = _balanced_body(clean, clean.find("(", m.end() - 1), "(", ")")
        # A Semantics(label:) wrapper already names it; adding semanticsLabel as well would
        # announce the same thing twice, which is the defect the tooltip rule below catches.
        # The wrapper is found by bracket nesting, not a character window: a label far above
        # on the spinner's own ancestor counts, one on a sibling above it does not.
        labelled_ancestor = any(_SEMANTICS_LABEL_ARG.search(args) for args in _enclosing_semantics_args(masked, m.start()))
        if "semanticsLabel" not in body and not labelled_ancestor:
            found[_key(found, rel)] = (
                f"CircularProgressIndicator with no semanticsLabel (line "
                f"{_line_of(clean, m.start())}) - a screen reader announces nothing while the "
                f"user waits"
            )


def _check_tappables(found: dict[str, str], rel: str, source: str, clean: str, masked: str, wrappers: set[str]) -> None:
    """Tap-target size, announced role and onTap/onDoubleTap rules for every tappable widget in one file."""
    defines_tap_helper = any(name in rel for name in _TAP_TARGET_HELPER_FILES)
    for m in _TAPPABLE.finditer(clean):
        body = _balanced_body(clean, clean.find("(", m.end() - 1), "(", ")")
        if not _ON_TAP.search(body) or defines_tap_helper:
            continue
        window_start = max(0, m.start() - 600)
        before = clean[window_start : m.start()]
        wrapped = any(name in wrappers for name, _ in _enclosing_calls(masked, m.start()))
        if not wrapped and not _MIN_TAP_TARGET.search(body) and not _MIN_TAP_TARGET.search(before):
            _report_small_target(found, rel, source, clean, m.start())
        _check_tap_semantics(found, rel, clean, masked, m.start(), body)


def _check_tap_semantics(found: dict[str, str], rel: str, clean: str, masked: str, start: int, body: str) -> None:
    """GestureDetector announced role, and onTap together with onDoubleTap, for the tappable at *start*."""
    # InkWell announces itself as a button; GestureDetector does not, which is the
    # whole difference this rule is about.
    is_gesture_detector = clean[start:].startswith("GestureDetector")
    announced = any(_SEMANTICS_ROLE_ARG.search(args) for args in _enclosing_semantics_args(masked, start))
    if is_gesture_detector and not announced and "Semantics(" not in body:
        found[_key(found, rel)] = (
            f"GestureDetector with no announced role (line {_line_of(clean, start)}) - "
            f"unlike InkWell it announces nothing, so a screen reader reads the contents "
            f"and not that they are actionable"
        )
    if _ON_DOUBLE_TAP.search(body):
        found[_key(found, rel)] = (
            f"onTap and onDoubleTap on one gesture detector (line "
            f"{_line_of(clean, start)}) - the single tap is delayed waiting for a "
            f"second, and the double tap is undiscoverable"
        )


def _check_build_helper_context(found: dict[str, str], rel: str, clean: str) -> None:
    """A ``_build`` helper whose BuildContext is a NAMED parameter."""
    for m in _BUILD_HELPER.finditer(clean):
        # The parameter list, not "up to the next brace": a named-parameter group opens with
        # `{` immediately after `(`, so slicing to the first brace cut the very text this rule
        # is about.
        params = _balanced_body(clean, m.end() - 1, "(", ")")
        if _NAMED_CONTEXT_PARAM.search(params):
            found[_key(found, rel)] = (
                f"_build helper takes BuildContext as a NAMED parameter (line "
                f"{_line_of(clean, m.start())}) - the convention everywhere else is first "
                f"positional"
            )


def _check_tooltip_duplicates(found: dict[str, str], rel: str, clean: str) -> None:
    """A tooltip message equal to the neighbouring semantics label without excludeFromSemantics."""
    for m in _TOOLTIP_MESSAGE.finditer(clean):
        window = clean[max(0, m.start() - 300) : m.start() + 300]
        label_match = re.search(r"\blabel:\s*([^,\n]+)", window)
        if label_match and label_match.group(1).strip() == m.group(1).strip() and not _EXCLUDE_FROM_SEMANTICS.search(window):
            found[_key(found, rel)] = (
                f"tooltip duplicates the semantics label (line {_line_of(clean, m.start())}) " f"with no excludeFromSemantics - the name is announced twice"
            )


def scan_tappable_semantics(files: Iterable[str], read: Reader, *, target_wrappers: Sequence[str] = ()) -> dict:
    """Interactive-affordance rules: see the module docstring.

    *target_wrappers* names project widgets (or ``Widget name(...)`` helpers) that guarantee a compact WCAG 2.5.8 AA
    target. Each counts as wrapping a tappable only if its definition in *files* sets both ``minWidth`` and
    ``minHeight`` to at least 24; a name whose definition does not, or is missing, is itself a finding.

    A tappable whose child is text alone, inline in a sentence, may instead carry ``// tap-target: inline-text -
    <reason>`` on the comment lines directly above it (WCAG 2.5.8's inline exemption). A marker with no reason, or on
    a tappable whose child holds any widget other than text and semantics, is a finding.
    """
    found: dict[str, str] = {}
    files = list(files)
    sources = {rel: read(rel) for rel in files}
    cleaned = {rel: _strip_comments(src) for rel, src in sources.items()}
    wrappers = {"MinTapTargetBox"} | _verified_target_wrappers(cleaned, target_wrappers, found)
    for rel in files:
        source = sources[rel]
        clean = cleaned[rel]
        masked = _mask_strings(clean)
        _check_spinner_labels(found, rel, clean, masked)
        _check_tappables(found, rel, source, clean, masked, wrappers)
        _check_build_helper_context(found, rel, clean)
        _check_tooltip_duplicates(found, rel, clean)
    return _rekey(found)


# --------------------------------------------------------------------------------------------
# M20 - non-directional layout

_NON_DIRECTIONAL = re.compile(
    r"\bAlignment\.(?:centerLeft|centerRight|topLeft|topRight|bottomLeft|bottomRight)\b"
    r"|\bEdgeInsets\.only\(\s*(?:left|right):"
    r"|\bPositioned\(\s*(?:left|right):"
    r"|\bTextAlign\.(?:left|right)\b"
)
# A gradient's begin/end is a direction of shading, not a side of the layout: a corner-to-corner
# sweep looks the same mirrored, and there is no Directional form to switch to. Matching these
# produced twenty-two findings on one renderer and buried the four real ones.
_GRADIENT_CONTEXT_RE = re.compile(r"\b(?:begin|end|transform|focal|center):\s*$")


def scan_non_directional_layout(files: Iterable[str], read: Reader, *, skip_markers: Sequence[str] = ("painter", "paint_utils")) -> dict:
    """Left/right layout constants, which do not flip in a right-to-left locale."""
    found: dict[str, str] = {}
    for rel in files:
        if any(marker in rel.lower() for marker in skip_markers):
            continue
        clean = _strip_comments(read(rel))
        for m in _NON_DIRECTIONAL.finditer(clean):
            preceding = clean[max(0, m.start() - 40) : m.start()]
            if _GRADIENT_CONTEXT_RE.search(preceding):
                continue
            found[_key(found, rel)] = (
                f"{' '.join(m.group(0).split())} (line {_line_of(clean, m.start())}) - a "
                f"physical side, so the "
                f"layout does not mirror in a right-to-left locale. Use the Directional form."
            )
    return _rekey(found)


# --------------------------------------------------------------------------------------------
# M13 - parse / serialise / catch hygiene

_ISO_WITHOUT_UTC = re.compile(r"(?<!toUtc\(\))\.toIso8601String\(\)")
_TO_UTC_ISO = re.compile(r"\.toUtc\(\)\s*\.toIso8601String\(\)")
_JSON_DECODE = re.compile(r"\bjsonDecode\s*\(")
_TRY_BLOCK_RE = re.compile(r"\btry\s*\{")
# `orElse: () => Type.value` is an enum default; `orElse: () => list.first` is a list fallback,
# which is ordinary and correct. The capitalised head is what distinguishes them.
_ENUM_DEFAULT = re.compile(r"orElse:\s*\(\)\s*=>\s*[A-Z]\w*\.\w+")
_FIRE_AND_FORGET_THEN = re.compile(r"^\s*[\w.]+\([^;]*\)\s*\.then\s*\(", re.MULTILINE)
_SOCKET_EXCEPTION = re.compile(r"\bon\s+SocketException\b")
_CLIENT_EXCEPTION = re.compile(r"\bClientException\b")
# `now ?? DateTime.now()` on an injectable parameter is the clock SEAM - the pattern this rule
# exists to encourage. Only a fallback for a value that was being PARSED is a defect, because
# there the missing timestamp becomes indistinguishable from a real one.
_NOW_FALLBACK = re.compile(r"""(?P<lhs>[\w.\[\]'"()]+)\s*\?\?\s*DateTime\.now\(\)""")
_PARSED_LHS_RE = re.compile(r"json\[|\bparse|\bmap\[|\brow\[|tryParse|\['\w+'\]")


def _check_iso_without_utc(found: dict[str, str], rel: str, clean: str) -> None:
    """toIso8601String() with no toUtc() just before it."""
    for m in _ISO_WITHOUT_UTC.finditer(clean):
        window = clean[max(0, m.start() - 30) : m.end()]
        if _TO_UTC_ISO.search(window):
            continue
        found[_key(found, rel)] = (
            f"toIso8601String() with no toUtc() (line {_line_of(clean, m.start())}) - the "
            f"stamp carries the writer's local offset and every reader in another zone is off"
        )


def _check_now_fallback(found: dict[str, str], rel: str, clean: str) -> None:
    """``?? DateTime.now()`` as the fallback of a value that was being parsed."""
    for m in _NOW_FALLBACK.finditer(clean):
        if not _PARSED_LHS_RE.search(m.group("lhs")):
            continue
        found[_key(found, rel)] = (
            f"?? DateTime.now() (line {_line_of(clean, m.start())}) - a missing timestamp " f"becomes 'now', which is indistinguishable from a real one"
        )


def _check_json_decode(found: dict[str, str], rel: str, clean: str) -> None:
    """jsonDecode with no ``try`` in the 600 characters before it."""
    for m in _JSON_DECODE.finditer(clean):
        window = clean[max(0, m.start() - 600) : m.start()]
        if not _TRY_BLOCK_RE.search(window):
            found[_key(found, rel)] = f"jsonDecode outside a try (line {_line_of(clean, m.start())}) - one corrupt " f"record throws past the caller"


def _check_enum_default(found: dict[str, str], rel: str, clean: str) -> None:
    """Enum lookup falling back to a non-nullable default."""
    for m in _ENUM_DEFAULT.finditer(clean):
        found[_key(found, rel)] = (
            f"enum lookup with a non-nullable default (line {_line_of(clean, m.start())}) - an "
            f"unknown stored value silently becomes a real choice the user never made"
        )


def _check_fire_and_forget(found: dict[str, str], rel: str, clean: str) -> None:
    """An unawaited ``.then()`` statement with neither onError nor catchError."""
    for m in _FIRE_AND_FORGET_THEN.finditer(clean):
        statement = clean[m.start() : clean.find(";", m.start())]
        if "onError" not in statement and "catchError" not in statement:
            found[_key(found, rel)] = (
                f"unawaited .then() with no error path (line {_line_of(clean, m.start())}) - "
                f"a rejection is an unhandled async error and the UI waits forever"
            )


def _check_socket_exception(found: dict[str, str], rel: str, clean: str) -> None:
    """SocketException caught with no ClientException anywhere in the file."""
    if (socket_hit := _SOCKET_EXCEPTION.search(clean)) and not _CLIENT_EXCEPTION.search(clean):
        found[_key(found, rel)] = (
            f"SocketException caught without ClientException (line "
            f"{_line_of(clean, socket_hit.start())}) - the web build never throws SocketException, so "
            f"this branch is dead there"
        )


def scan_parse_serialize_catch(files: Iterable[str], read: Reader) -> dict:
    """Serialisation and error-path rules: see the module docstring."""
    found: dict[str, str] = {}
    for rel in files:
        clean = _strip_comments(read(rel))
        _check_iso_without_utc(found, rel, clean)
        _check_now_fallback(found, rel, clean)
        _check_json_decode(found, rel, clean)
        _check_enum_default(found, rel, clean)
        _check_fire_and_forget(found, rel, clean)
        _check_socket_exception(found, rel, clean)
    return _rekey(found)


# --------------------------------------------------------------------------------------------
# M12 - provider and state hygiene

_DATETIME_NOW = re.compile(r"\bDateTime\.now\(\)")
# A write that is returned (`=> _prefs.setBool(...)`, `return prefs.setString(...)`) hands the
# future to the caller, which is as awaited as awaiting it here.
_PREFS_WRITE = re.compile(r"(?<![\w.])((?:await\s+|unawaited\(\s*|=>\s*|return\s+)?)(?:\w+(?:\([^()]*\))?\.)*\w*[Pp]refs\.set\w+\(")
_TOSTRING_PII = re.compile(r"String\s+toString\(\)[^;{]*(?:=>|\{)[^;}]*\$\{?(?:\w+\.)?(displayName|email|avatarUrl|fullName)")


def scan_provider_state_hygiene(
    files: Iterable[str],
    read: Reader,
    *,
    clock_seam: str = "nowProvider",
    repo_has_clock_seam: bool = True,
) -> dict:
    """Provider/state rules: see the module docstring."""
    found: dict[str, str] = {}
    for rel in files:
        clean = _strip_comments(read(rel))

        if repo_has_clock_seam and rel.endswith("_provider.dart") and clock_seam not in clean:
            for m in _DATETIME_NOW.finditer(clean):
                found[_key(found, rel)] = (
                    f"DateTime.now() in a provider (line {_line_of(clean, m.start())}) while the "
                    f"repo has a {clock_seam} seam - the value cannot be pinned in a test, so no "
                    f"date-boundary behaviour is provable"
                )

        for m in _PREFS_WRITE.finditer(clean):
            if m.group(1).strip():
                continue
            found[_key(found, rel)] = (
                f"preferences write neither awaited nor unawaited() (line "
                f"{_line_of(clean, m.start())}) - a failure is invisible and the value can be lost "
                f"on a kill"
            )

        for m in _TOSTRING_PII.finditer(clean):
            found[_key(found, rel)] = (
                f"toString() interpolates {m.group(1)} (line {_line_of(clean, m.start())}) - "
                f"personal data lands in every log line and crash report that prints this object"
            )
    return _rekey(found)


# --------------------------------------------------------------------------------------------
# Repository plumbing: file listing, reader and package name, so a caller need not copy them.

GENERATED_MARKERS = ("// GENERATED CODE - DO NOT MODIFY BY HAND", "// dart format off")


def package_name(repo_root: PathLike) -> str:
    """The Dart package name from ``pubspec.yaml``, read BOM-safe; raises when there is none to read.

    A scan that recognises first-party ``package:`` imports sees none at all without it, and reports a clean graph.
    """
    for line in read_source(Path(repo_root) / "pubspec.yaml").splitlines():
        match = re.match(r"\s*name:\s*([A-Za-z_][A-Za-z0-9_]*)", line)
        if match:
            return match.group(1)
    raise RuntimeError(f"no `name:` in {Path(repo_root) / 'pubspec.yaml'} - every first-party import would be invisible")


def dart_files_under(repo_root: PathLike, root: str, *, generated_prefixes: Sequence[str] = ("lib/src/l10n/generated/",)) -> list[str]:
    """Every hand-written ``.dart`` file under *root*, repo-relative POSIX paths, in a stable order.

    Skips ``.g.dart``/``.freezed.dart``, files whose first 400 characters carry a :data:`GENERATED_MARKERS` header,
    and paths starting with one of *generated_prefixes*. A file that cannot be decoded is still listed, so the
    reader from :func:`dart_reader` raises on it by name instead of the file silently leaving the corpus.
    """
    base = Path(repo_root) / root
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            if not name.endswith(".dart") or name.endswith((".g.dart", ".freezed.dart")):
                continue
            full = Path(dirpath) / name
            try:
                head = read_source(full)[:400]
            except SourceError as exc:
                logger.warning(
                    "dart_scanners.py line 654: substituted a fallback after %s: %s", type(exc).__name__, exc
                )  # audit-exempt: unthrottled_hot_loop_log -- one warning per unreadable file, and the loop runs over the files, so the count is bounded by the corpus
                head = ""
            if any(marker in head for marker in GENERATED_MARKERS):
                continue
            rel = full.relative_to(repo_root).as_posix()
            if not rel.startswith(tuple(generated_prefixes)):
                out.append(rel)
    return out


def dart_reader(repo_root: PathLike) -> Reader:
    """A :data:`Reader` over *repo_root*: BOM stripped, an undecodable file raises ``SourceReadError`` naming it."""
    return lambda rel: read_source(Path(repo_root) / rel)


def _context(source: str, index: int) -> str:
    """The nearest non-blank line above *index*, trimmed: what a finding's key hashes besides the matched text."""
    for line in reversed(source[:index].rsplit("\n", 12)[:-1]):
        if line.strip():
            return " ".join(line.split())[:60]
    return ""


# --------------------------------------------------------------------------------------------
# File size, silent catches, source-reading tests, timers, double reports, seams, import cycles


def scan_file_size(files: Iterable[str], read: Reader, *, max_lines: int = 1000) -> dict:
    """Files over *max_lines* lines (newlines + 1), keyed by path: one finding per file, so the path is stable."""
    found: dict[str, str] = {}
    for rel in files:
        lines = read(rel).count("\n") + 1
        if lines > max_lines:
            found[rel] = f"{lines} lines"
    return found


# `catch (_) {}` and `catch (_, _) { // comment only }`: a catch that does nothing. One that logs, returns a
# fallback or rethrows is fine. Only `_` as the bound name, which is how this code base spells "ignored".
_EMPTY_CATCH = re.compile(r"catch\s*\(\s*_\s*(?:,\s*_\s*)?\)\s*\{(?P<body>(?:[^{}/]|/(?!/)|//[^\n]*(?=\n|$))*?)\}", re.DOTALL)


def scan_empty_catch(files: Iterable[str], read: Reader) -> dict:
    """Catch blocks whose body is empty or comments only: each needs a decision, so a new one must not appear unseen."""
    found: dict[str, str] = {}
    for rel in files:
        source = read(rel)
        for m in _EMPTY_CATCH.finditer(source):
            if re.sub(r"//[^\n]*", "", m.group("body")).strip():
                continue
            found[_key(found, rel)] = f"catch with an empty body (line {_line_of(source, m.start())}) after `{_context(source, m.start())}`"
    return _rekey(found)


_SOURCE_TEXT_ASSERTION = re.compile(r"(File\(|Directory\()[^\n]*\.(readAsString|readAsLines)")


def scan_source_text_assertions(files: Iterable[str], read: Reader) -> dict:
    """Tests (pass the TEST files) that read source text: they check a line exists, not that the behaviour holds."""
    found: dict[str, str] = {}
    for rel in files:
        source = read(rel)
        for m in _SOURCE_TEXT_ASSERTION.finditer(source):
            found[_key(found, rel)] = f"test reads source on it (line {_line_of(source, m.start())}): `{' '.join(m.group(0).split())[:80]}`"
    return _rekey(found)


# Both directives, either quote, leading whitespace: a cycle through a barrel `export` is a cycle too.
_DIRECTIVE = re.compile(r"""^\s*(?:import|export)\s+['"]([^'"]+)['"]""", re.MULTILINE)


def scan_import_cycles(files: Iterable[str], read: Reader, *, package: str, lib_root: str = "lib") -> dict:
    """Import/export cycles among *files*: one finding per cycle, keyed on its entry file.

    ``package:<package>/x`` resolves to ``<lib_root>/x``, a relative URI against the importer; ``dart:`` and other
    packages are external. Dart tolerates cycles, which is why nothing else notices the one that turns extracting a
    package from a move into a rewrite.
    """
    return _find_cycles(_import_graph(files, read, f"package:{package}/", lib_root))


def _import_graph(files: Iterable[str], read: Reader, prefix: str, lib_root: str) -> dict[str, set[str]]:
    """``file -> files it imports or exports`` among the project's own relative and ``package:`` URIs."""
    graph: dict[str, set[str]] = {}
    for rel in files:
        targets: set[str] = set()
        for m in _DIRECTIVE.finditer(_strip_comments(read(rel))):
            uri = m.group(1)
            if uri.startswith(prefix):
                targets.add(f"{lib_root}/{uri[len(prefix):]}")
            elif not uri.startswith(("dart:", "package:")):
                targets.add(posixpath.normpath(posixpath.join(posixpath.dirname(rel), uri)))
        graph[rel] = targets
    return graph


def _find_cycles(graph: dict[str, set[str]]) -> dict[str, str]:
    """One finding per cycle of *graph*, keyed on the file where the depth-first walk first re-enters it."""
    found: dict[str, str] = {}
    state: dict[str, str] = {}

    def visit(node: str, stack: list[str]) -> None:
        if state.get(node) == "done":
            return
        if state.get(node) == "open":
            found[node] = "import cycle: " + " -> ".join([*stack[stack.index(node) :], node])
            return
        state[node] = "open"
        for target in sorted(graph.get(node, ())):
            if target in graph:
                visit(target, [*stack, node])
        state[node] = "done"

    for module in sorted(graph):
        visit(module, [])
    return found


# expect[A-Z]... covers shared matcher helpers (expectMinTapTargets and friends) that hold the expect() calls.
_ASSERTION = re.compile(r"\b(expect|expectLater|expect[A-Z]\w*|verify|verifyNever|verifyInOrder|fail)\s*\(")


def _test_blocks(source: str) -> Iterator[tuple[str, str, int]]:
    """``(name, body, line)`` for each ``test(``/``testWidgets(`` call, paren-matched from its opening paren.

    Calls and parens are found in the string-masked source: a fixture test file held in a triple-quoted string is not
    a test of this file, and a ``)`` inside a string must not close the call. *body* is the masked text, so an
    ``expect`` that is only string content is not an assertion; the name is read from the original at the same offsets.
    """
    masked = _mask_strings(source)
    for match in re.finditer(r"\b(testWidgets|test)\(", masked):
        start = match.end() - 1
        body = "(" + _balanced_body(masked, start, "(", ")") + ")"
        name_match = re.search(r"['\"](.+?)['\"]", source[start : start + len(body)], re.DOTALL)
        yield (name_match.group(1)[:70] if name_match else "?"), body, _line_of(source, start)


def scan_tests_without_assertions(files: Iterable[str], read: Reader, *, assertion: re.Pattern[str] = _ASSERTION) -> dict:
    """Tests (pass the TEST files) with no assertion: a smoke test counted as coverage of what its name claims.

    Keyed ``rel::<test name>``; a second test of the same name in one file gets ``#n`` instead of replacing the
    first. A commented-out ``expect`` is not an assertion. ``expect(call, returnsNormally)`` is the explicit form.
    """
    found: dict[str, str] = {}
    for rel in files:
        for name, body, line in _test_blocks(_strip_comments(read(rel))):
            if assertion.search(body):
                continue
            key, n = f"{rel}::{name}", 1
            while key in found:
                key, n = f"{rel}::{name}#{n}", n + 1
            found[key] = f"test contains no assertion (line {line})"
    return found


_TIMED_DISMISS = re.compile(r"Future\.delayed\([^;]{0,400}?Navigator\.of\([^;]{0,200}?\)\.pop\(", re.DOTALL)


def scan_timed_dismissal(files: Iterable[str], read: Reader) -> dict:
    """UI that pops itself on a timer: content on a time limit the user cannot pause or extend (WCAG 2.2.1)."""
    found: dict[str, str] = {}
    for rel in files:
        clean = _strip_comments(read(rel))
        for m in _TIMED_DISMISS.finditer(clean):
            found[_key(found, rel)] = f"UI dismissed by an uncontrollable timer (line {_line_of(clean, m.start())}): `{' '.join(m.group(0).split())[:100]}`"
    return _rekey(found)


_CATCH_HEAD = re.compile(r"catch\s*\([^)]*\)\s*\{")


def scan_double_error_reports(
    files: Iterable[str],
    read: Reader,
    *,
    logger_call: str = "AppLog.error",
    recorder_call: str = "ErrorService.recordError",
) -> dict:
    """A catch body calling both *logger_call* and *recorder_call*, when the logger already forwards to the recorder.

    One failure then files two crash-reporter issues, one of them without the stack trace or the reason.
    """
    found: dict[str, str] = {}
    for rel in files:
        clean = _strip_comments(read(rel))
        for m in _CATCH_HEAD.finditer(clean):
            body = _balanced_body(clean, m.end() - 1)
            if logger_call in body and recorder_call in body:
                found[_key(found, rel)] = (
                    f"one failure reported twice, {logger_call} and {recorder_call} in one catch "
                    f"(line {_line_of(clean, m.start())}) after `{_context(clean, m.start())}`"
                )
    return _rekey(found)


_TEST_SEAM = re.compile(r"@visibleForTesting\s*\n\s*[^\n]*?\b(\w+)\s*[;(=]")
_NOT_A_MEMBER_NAME = frozenset({"final", "const", "static", "void", "int", "bool", "String"})


def scan_unused_test_seams(files: Iterable[str], read: Reader, *, test_files: Iterable[str]) -> dict:
    """``@visibleForTesting`` members in *files* whose name no file in *test_files* mentions; keyed ``rel::name``.

    A seam exists to make something checkable; one no test uses bought nothing and is maintained as public API.
    """
    corpus = "\n".join(read(rel) for rel in test_files)
    found: dict[str, str] = {}
    for rel in files:
        for m in _TEST_SEAM.finditer(read(rel)):
            name = m.group(1)
            if name in _NOT_A_MEMBER_NAME or re.search(r"\b" + re.escape(name) + r"\b", corpus):
                continue
            found[f"{rel}::{name}"] = "@visibleForTesting member used by no test"
    return found


SCANNERS: dict[str, Callable[..., dict]] = {
    "painter-animation": scan_painter_animation,
    "repaint-isolation": scan_repaint_isolation,
    "hardcoded-ui-strings": scan_hardcoded_ui_strings,
    "tappable-semantics": scan_tappable_semantics,
    "non-directional-layout": scan_non_directional_layout,
    "parse-serialize-catch": scan_parse_serialize_catch,
    "provider-state-hygiene": scan_provider_state_hygiene,
    "file-size": scan_file_size,
    "empty-catch": scan_empty_catch,
    "source-text-assertions": scan_source_text_assertions,
    "import-cycles": scan_import_cycles,
    "tests-without-assertions": scan_tests_without_assertions,
    "timed-dismissal": scan_timed_dismissal,
    "double-error-reports": scan_double_error_reports,
    "unused-test-seams": scan_unused_test_seams,
}
