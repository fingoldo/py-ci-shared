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
  minimum tap target (P05-5, P05-6) or no button semantics (P05-11), ``onTap`` paired with
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

Usage (from a repo's own ``tool/meta/scanners.py``)::

    from py_ci_shared.dart_scanners import scan_painter_animation
    SCANS["painter-animation"] = lambda: scan_painter_animation(list(dart_files()), read)
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

Reader = Callable[[str], str]

# --------------------------------------------------------------------------------------------
# helpers


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _key(found: dict, rel: str) -> str:
    """Ordinal-within-file key, so an edit above a violation does not renumber every entry."""
    ordinal = sum(1 for k in found if k.startswith(rel + "#"))
    return f"{rel}#{ordinal}"


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def _balanced_body(source: str, open_index: int, opener: str = "{", closer: str = "}") -> str:
    """Return the text between ``open_index`` (at an opener) and its matching closer."""
    depth = 0
    i = open_index
    while i < len(source):
        if source[i] == opener:
            depth += 1
        elif source[i] == closer:
            depth -= 1
            if depth == 0:
                return source[open_index + 1 : i]
        i += 1
    return source[open_index + 1 :]


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

        if _ACTIVATE_INTENT.search(clean) and not _BUTTON_ACTIVATE_INTENT.search(clean):
            m = _ACTIVATE_INTENT.search(clean)
            found[_key(found, rel)] = (
                f"ActivateIntent handled without ButtonActivateIntent (line "
                f"{_line_of(clean, m.start())}) - on the web Enter maps to ButtonActivateIntent, "
                f"so the key does nothing there"
            )
    return found


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
    return found


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
) -> dict:
    """User-visible text or colour baked into Dart instead of coming from l10n / the palette."""
    found: dict[str, str] = {}
    for rel in files:
        source = read(rel)
        clean = _strip_comments(source)
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
            line_start = clean.rfind("\n", 0, m.start()) + 1
            line = clean[line_start : clean.find("\n", m.start())]
            if _LOG_CALL.search(line):
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
    return found


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
_SEMANTICS_BUTTON = re.compile(
    r"Semantics\s*\(\s*(?:[^()]*?)(?:button:\s*true|inMutuallyExclusiveGroup:|checked:|selected:|toggled:)",
    re.DOTALL,
)
_NAMED_CONTEXT_PARAM = re.compile(r"\{[^}]*?required\s+BuildContext\s+context\b", re.DOTALL)
_BUILD_HELPER = re.compile(r"\bWidget\s+_build\w*\s*\(")
_TOOLTIP_MESSAGE = re.compile(r"Tooltip\s*\(\s*message:\s*([^,\n]+)")
_SEMANTICS_LABEL_RE = re.compile(r"Semantics\s*\(\s*(?:[^()]*?)label:", re.DOTALL)
# The widget that PROVIDES the minimum tap target cannot be asked to wrap itself in one.
_TAP_TARGET_HELPER_FILES = ("min_tap_target_box",)
_EXCLUDE_FROM_SEMANTICS = re.compile(r"excludeFromSemantics:\s*true")


def scan_tappable_semantics(files: Iterable[str], read: Reader) -> dict:
    """Interactive-affordance rules: see the module docstring."""
    found: dict[str, str] = {}
    for rel in files:
        source = read(rel)
        clean = _strip_comments(source)

        for m in _SPINNER.finditer(clean):
            body = _balanced_body(clean, clean.find("(", m.end() - 1), "(", ")")
            # A Semantics(label:) wrapper already names it; adding semanticsLabel as well would
            # announce the same thing twice, which is the defect the tooltip rule below catches.
            labelled_ancestor = _SEMANTICS_LABEL_RE.search(clean[max(0, m.start() - 400) : m.start()])
            if "semanticsLabel" not in body and not labelled_ancestor:
                found[_key(found, rel)] = (
                    f"CircularProgressIndicator with no semanticsLabel (line "
                    f"{_line_of(clean, m.start())}) - a screen reader announces nothing while the "
                    f"user waits"
                )

        defines_tap_helper = any(name in rel for name in _TAP_TARGET_HELPER_FILES)
        for m in _TAPPABLE.finditer(clean):
            body = _balanced_body(clean, clean.find("(", m.end() - 1), "(", ")")
            if not _ON_TAP.search(body) or defines_tap_helper:
                continue
            window_start = max(0, m.start() - 600)
            before = clean[window_start : m.start()]
            if not _MIN_TAP_TARGET.search(body) and not _MIN_TAP_TARGET.search(before):
                found[_key(found, rel)] = (
                    f"tappable with no MinTapTargetBox (line {_line_of(clean, m.start())}) - the " f"visible target can be smaller than the 48x48 minimum"
                )
            # InkWell announces itself as a button; GestureDetector does not, which is the
            # whole difference this rule is about.
            is_gesture_detector = clean[m.start() :].startswith("GestureDetector")
            if is_gesture_detector and not _SEMANTICS_BUTTON.search(before) and "Semantics(" not in body:
                found[_key(found, rel)] = (
                    f"GestureDetector with no announced role (line {_line_of(clean, m.start())}) - "
                    f"unlike InkWell it announces nothing, so a screen reader reads the contents "
                    f"and not that they are actionable"
                )
            if _ON_DOUBLE_TAP.search(body):
                found[_key(found, rel)] = (
                    f"onTap and onDoubleTap on one gesture detector (line "
                    f"{_line_of(clean, m.start())}) - the single tap is delayed waiting for a "
                    f"second, and the double tap is undiscoverable"
                )

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

        for m in _TOOLTIP_MESSAGE.finditer(clean):
            window = clean[max(0, m.start() - 300) : m.start() + 300]
            label_match = re.search(r"\blabel:\s*([^,\n]+)", window)
            if label_match and label_match.group(1).strip() == m.group(1).strip() and not _EXCLUDE_FROM_SEMANTICS.search(window):
                found[_key(found, rel)] = (
                    f"tooltip duplicates the semantics label (line {_line_of(clean, m.start())}) " f"with no excludeFromSemantics - the name is announced twice"
                )
    return found


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
    return found


# --------------------------------------------------------------------------------------------
# M13 - parse / serialise / catch hygiene

_ISO_WITHOUT_UTC = re.compile(r"(?<!toUtc\(\))\.toIso8601String\(\)")
_TO_UTC_ISO = re.compile(r"\.toUtc\(\)\s*\.toIso8601String\(\)")
_JSON_DECODE = re.compile(r"\bjsonDecode\s*\(")
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


def scan_parse_serialize_catch(files: Iterable[str], read: Reader) -> dict:
    """Serialisation and error-path rules: see the module docstring."""
    found: dict[str, str] = {}
    for rel in files:
        clean = _strip_comments(read(rel))

        for m in _ISO_WITHOUT_UTC.finditer(clean):
            window = clean[max(0, m.start() - 30) : m.end()]
            if _TO_UTC_ISO.search(window):
                continue
            found[_key(found, rel)] = (
                f"toIso8601String() with no toUtc() (line {_line_of(clean, m.start())}) - the "
                f"stamp carries the writer's local offset and every reader in another zone is off"
            )

        for m in _NOW_FALLBACK.finditer(clean):
            if not _PARSED_LHS_RE.search(m.group("lhs")):
                continue
            found[_key(found, rel)] = (
                f"?? DateTime.now() (line {_line_of(clean, m.start())}) - a missing timestamp " f"becomes 'now', which is indistinguishable from a real one"
            )

        for m in _JSON_DECODE.finditer(clean):
            window = clean[max(0, m.start() - 600) : m.start()]
            if "try" not in window:
                found[_key(found, rel)] = f"jsonDecode outside a try (line {_line_of(clean, m.start())}) - one corrupt " f"record throws past the caller"

        for m in _ENUM_DEFAULT.finditer(clean):
            found[_key(found, rel)] = (
                f"enum lookup with a non-nullable default (line {_line_of(clean, m.start())}) - an "
                f"unknown stored value silently becomes a real choice the user never made"
            )

        for m in _FIRE_AND_FORGET_THEN.finditer(clean):
            statement = clean[m.start() : clean.find(";", m.start())]
            if "onError" not in statement and "catchError" not in statement:
                found[_key(found, rel)] = (
                    f"unawaited .then() with no error path (line {_line_of(clean, m.start())}) - "
                    f"a rejection is an unhandled async error and the UI waits forever"
                )

        if _SOCKET_EXCEPTION.search(clean) and not _CLIENT_EXCEPTION.search(clean):
            m = _SOCKET_EXCEPTION.search(clean)
            found[_key(found, rel)] = (
                f"SocketException caught without ClientException (line "
                f"{_line_of(clean, m.start())}) - the web build never throws SocketException, so "
                f"this branch is dead there"
            )
    return found


# --------------------------------------------------------------------------------------------
# M12 - provider and state hygiene

_DATETIME_NOW = re.compile(r"\bDateTime\.now\(\)")
# A write that is returned (`=> _prefs.setBool(...)`, `return prefs.setString(...)`) hands the
# future to the caller, which is as awaited as awaiting it here.
_PREFS_WRITE = re.compile(r"(?<![\w.])((?:await\s+|unawaited\(\s*|=>\s*|return\s+)?)\w*[Pp]refs\.set\w+\(")
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
    return found


SCANNERS: dict[str, Callable[..., dict]] = {
    "painter-animation": scan_painter_animation,
    "repaint-isolation": scan_repaint_isolation,
    "hardcoded-ui-strings": scan_hardcoded_ui_strings,
    "tappable-semantics": scan_tappable_semantics,
    "non-directional-layout": scan_non_directional_layout,
    "parse-serialize-catch": scan_parse_serialize_catch,
    "provider-state-hygiene": scan_provider_state_hygiene,
}
