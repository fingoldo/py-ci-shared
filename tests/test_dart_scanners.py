"""Unit tests for the shared Dart source scanners. In-memory sources with a fake reader, same
no-mocking-of-logic convention as this package's other tests (the reader is a dict lookup, not a
mock of the rule under test).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.dart_scanners import (
    scan_hardcoded_ui_strings,
    scan_non_directional_layout,
    scan_painter_animation,
    scan_parse_serialize_catch,
    scan_provider_state_hygiene,
    scan_repaint_isolation,
    scan_tappable_semantics,
)


def _reader(sources: dict):
    return list(sources), (lambda rel: sources[rel])


class TestPainterAnimation:
    def test_should_repaint_true_is_flagged(self):
        files, read = _reader({"lib/p.dart": "bool shouldRepaint(covariant X old) => true;"})
        assert len(scan_painter_animation(files, read)) == 1

    def test_discriminating_should_repaint_passes(self):
        files, read = _reader({"lib/p.dart": "bool shouldRepaint(covariant X old) => old.value != value;"})
        assert scan_painter_animation(files, read) == {}

    def test_paint_allocated_in_a_loop_is_flagged(self):
        src = (
            "void paint(Canvas canvas, Size size) {\n"
            "  for (var i = 0; i < 10; i++) {\n"
            "    canvas.drawCircle(Offset.zero, 2, Paint()..color = c);\n"
            "  }\n"
            "}\n"
        )
        files, read = _reader({"lib/p.dart": src})
        problems = scan_painter_animation(files, read)
        assert len(problems) == 1
        assert "inside a loop" in list(problems.values())[0]

    def test_paint_hoisted_out_of_the_loop_passes(self):
        src = (
            "void paint(Canvas canvas, Size size) {\n"
            "  final p = Paint();\n"
            "  for (var i = 0; i < 10; i++) {\n"
            "    canvas.drawCircle(Offset.zero, 2, p);\n"
            "  }\n"
            "}\n"
        )
        files, read = _reader({"lib/p.dart": src})
        assert scan_painter_animation(files, read) == {}

    def test_platform_dispatcher_motion_source_is_flagged(self):
        files, read = _reader({"lib/a.dart": "final r = WidgetsBinding.instance.platformDispatcher.accessibilityFeatures.disableAnimations;"})
        assert len(scan_painter_animation(files, read)) == 1

    def test_activate_intent_without_button_variant_is_flagged(self):
        files, read = _reader({"lib/a.dart": "CallbackAction<ActivateIntent>(onInvoke: (_) => x());"})
        problems = scan_painter_animation(files, read)
        assert len(problems) == 1
        assert "ButtonActivateIntent" in list(problems.values())[0]

    def test_both_intents_handled_passes(self):
        src = "ActivateIntent: a, ButtonActivateIntent: b,"
        files, read = _reader({"lib/a.dart": src})
        assert scan_painter_animation(files, read) == {}

    def test_comment_mentioning_should_repaint_true_is_ignored(self):
        files, read = _reader({"lib/p.dart": "// bool shouldRepaint(X old) => true;\n"})
        assert scan_painter_animation(files, read) == {}


_WIDGET = "Widget build(BuildContext context) { return x; }"


class TestRepaintIsolation:
    def test_repeating_animation_without_boundary_is_flagged(self):
        files, read = _reader({"lib/a.dart": f"_controller.repeat();\n{_WIDGET}"})
        assert len(scan_repaint_isolation(files, read)) == 1

    def test_repeating_animation_with_boundary_passes(self):
        files, read = _reader({"lib/a.dart": f"_controller.repeat();\nRepaintBoundary(child: x);\n{_WIDGET}"})
        assert scan_repaint_isolation(files, read) == {}

    def test_utility_that_repeats_a_callers_controller_is_not_flagged(self):
        # No build() and no painter: there is no subtree here to isolate, so demanding a
        # RepaintBoundary would be asking for a widget the file does not have.
        files, read = _reader({"lib/reduced_motion.dart": "static void syncRepeat(BuildContext c, AnimationController x) { x.repeat(); }"})
        assert scan_repaint_isolation(files, read) == {}

    def test_animated_builder_rebuilding_an_image_without_child_is_flagged(self):
        src = "RepaintBoundary(child: AnimatedBuilder(animation: a, builder: (c, _) => Image.asset('x.png')));"
        files, read = _reader({"lib/a.dart": src})
        problems = scan_repaint_isolation(files, read)
        assert len(problems) == 1
        assert "child:" in list(problems.values())[0]

    def test_animated_builder_with_child_slot_passes(self):
        src = "RepaintBoundary(child: AnimatedBuilder(animation: a, child: Image.asset('x.png'), builder: (c, ch) => ch!));"
        files, read = _reader({"lib/a.dart": src})
        assert scan_repaint_isolation(files, read) == {}


class TestHardcodedStrings:
    def test_enum_display_string_is_flagged(self):
        files, read = _reader({"lib/e.dart": "enum Design { aiSlop('AI Slop', 'Excessive gradients'), wave('Wave', 'Curves') }"})
        problems = scan_hardcoded_ui_strings(files, read)
        assert len(problems) == 1
        assert "AI Slop" in list(problems.values())[0]

    def test_bare_enum_passes(self):
        files, read = _reader({"lib/e.dart": "enum Design { aiSlop, wave }"})
        assert scan_hardcoded_ui_strings(files, read) == {}

    def test_literal_text_widget_is_flagged(self):
        files, read = _reader({"lib/a.dart": "const Text('Cancel this order'),"})
        assert len(scan_hardcoded_ui_strings(files, read)) == 1

    def test_literal_in_a_log_call_is_ignored(self):
        files, read = _reader({"lib/a.dart": "AppLog.log('Started the sync engine', tag: 'x');"})
        assert scan_hardcoded_ui_strings(files, read) == {}

    def test_capitalised_fallback_is_flagged(self):
        files, read = _reader({"lib/a.dart": "final name = profile?.displayName ?? 'User';"})
        problems = scan_hardcoded_ui_strings(files, read)
        assert len(problems) == 1
        assert "User" in list(problems.values())[0]

    def test_raw_colour_outside_a_palette_is_flagged(self):
        files, read = _reader({"lib/login_dialog.dart": "color: const Color(0xFF1877F2),"})
        assert len(scan_hardcoded_ui_strings(files, read)) == 1

    def test_raw_colour_inside_a_palette_passes(self):
        files, read = _reader({"lib/theme/glossum_palette.dart": "primary: const Color(0xFF4C6A8F),"})
        assert scan_hardcoded_ui_strings(files, read) == {}


class TestTappableSemantics:
    def test_unlabelled_spinner_is_flagged(self):
        files, read = _reader({"lib/a.dart": "const CircularProgressIndicator(strokeWidth: 2)"})
        assert len(scan_tappable_semantics(files, read)) == 1

    def test_labelled_spinner_passes(self):
        files, read = _reader({"lib/a.dart": "CircularProgressIndicator(semanticsLabel: l10n.loading)"})
        assert scan_tappable_semantics(files, read) == {}

    def test_tappable_without_target_or_semantics_is_flagged_twice(self):
        files, read = _reader({"lib/a.dart": "GestureDetector(onTap: () => x(), child: const Icon(Icons.add))"})
        problems = scan_tappable_semantics(files, read)
        assert len(problems) == 2

    def test_wrapped_tappable_passes(self):
        src = "Semantics(button: true, label: 'Add', child: GestureDetector(onTap: () => x(), child: MinTapTargetBox(child: const Icon(Icons.add))))"
        files, read = _reader({"lib/a.dart": src})
        assert scan_tappable_semantics(files, read) == {}

    def test_double_tap_is_flagged(self):
        src = "Semantics(button: true, child: GestureDetector(onTap: a, onDoubleTap: b, child: MinTapTargetBox(child: x)))"
        files, read = _reader({"lib/a.dart": src})
        problems = scan_tappable_semantics(files, read)
        assert any("onDoubleTap" in v for v in problems.values())

    def test_named_context_parameter_is_flagged(self):
        files, read = _reader({"lib/a.dart": "Widget _buildCard({required BuildContext context, required String title}) { return x; }"})
        problems = scan_tappable_semantics(files, read)
        assert any("NAMED parameter" in v for v in problems.values())

    def test_positional_context_passes(self):
        files, read = _reader({"lib/a.dart": "Widget _buildCard(BuildContext context, {required String title}) { return x; }"})
        assert scan_tappable_semantics(files, read) == {}


class TestNonDirectionalLayout:
    def test_alignment_center_left_is_flagged(self):
        files, read = _reader({"lib/a.dart": "alignment: Alignment.centerLeft,"})
        assert len(scan_non_directional_layout(files, read)) == 1

    def test_directional_form_passes(self):
        files, read = _reader({"lib/a.dart": "alignment: AlignmentDirectional.centerStart,"})
        assert scan_non_directional_layout(files, read) == {}

    def test_gradient_begin_and_end_are_not_layout_sides(self):
        # A corner-to-corner sweep looks the same mirrored and has no Directional form; these
        # accounted for twenty-two of twenty-six findings on one renderer.
        src = "LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: c)"
        files, read = _reader({"lib/a.dart": src})
        assert scan_non_directional_layout(files, read) == {}

    def test_widget_alignment_is_still_flagged(self):
        files, read = _reader({"lib/a.dart": "Align(alignment: Alignment.centerLeft, child: c)"})
        assert len(scan_non_directional_layout(files, read)) == 1

    def test_painter_file_is_skipped(self):
        files, read = _reader({"lib/widgets/painters/wave_painter.dart": "Positioned(left: 8,"})
        assert scan_non_directional_layout(files, read) == {}


class TestParseSerializeCatch:
    def test_iso_without_utc_is_flagged(self):
        files, read = _reader({"lib/m.dart": "'date': takenAt.toIso8601String(),"})
        assert len(scan_parse_serialize_catch(files, read)) == 1

    def test_utc_iso_passes(self):
        files, read = _reader({"lib/m.dart": "'date': takenAt.toUtc().toIso8601String(),"})
        assert scan_parse_serialize_catch(files, read) == {}

    def test_now_fallback_is_flagged(self):
        files, read = _reader({"lib/m.dart": "createdAt: parse(json['c']) ?? DateTime.now(),"})
        assert any("?? DateTime.now()" in v for v in scan_parse_serialize_catch(files, read).values())

    def test_json_decode_outside_try_is_flagged(self):
        files, read = _reader({"lib/s.dart": "final data = jsonDecode(raw);"})
        assert any("jsonDecode" in v for v in scan_parse_serialize_catch(files, read).values())

    def test_json_decode_inside_try_passes(self):
        files, read = _reader({"lib/s.dart": "try {\n  final data = jsonDecode(raw);\n} catch (e) { log(e); }"})
        assert scan_parse_serialize_catch(files, read) == {}

    def test_enum_orelse_default_is_flagged(self):
        files, read = _reader({"lib/u.dart": "values.firstWhere((v) => v.name == s, orElse: () => Theme.muted);"})
        assert any("non-nullable default" in v for v in scan_parse_serialize_catch(files, read).values())

    def test_socket_exception_without_client_exception_is_flagged(self):
        files, read = _reader({"lib/r.dart": "try { x(); } on SocketException { return null; }"})
        assert any("SocketException" in v for v in scan_parse_serialize_catch(files, read).values())

    def test_socket_and_client_exception_together_pass(self):
        files, read = _reader({"lib/r.dart": "try { x(); } on SocketException { a(); } on ClientException { b(); }"})
        assert scan_parse_serialize_catch(files, read) == {}


class TestProviderStateHygiene:
    def test_datetime_now_in_a_provider_is_flagged(self):
        files, read = _reader({"lib/providers/user_provider.dart": "final today = DateTime.now();"})
        assert len(scan_provider_state_hygiene(files, read)) == 1

    def test_provider_using_the_clock_seam_passes(self):
        files, read = _reader({"lib/providers/user_provider.dart": "final today = ref.watch(nowProvider);"})
        assert scan_provider_state_hygiene(files, read) == {}

    def test_repo_without_a_clock_seam_is_not_nagged(self):
        files, read = _reader({"lib/providers/user_provider.dart": "final today = DateTime.now();"})
        assert scan_provider_state_hygiene(files, read, repo_has_clock_seam=False) == {}

    def test_unawaited_prefs_write_is_flagged(self):
        files, read = _reader({"lib/a.dart": "prefs.setString('k', v);"})
        assert any("neither awaited" in v for v in scan_provider_state_hygiene(files, read).values())

    def test_awaited_prefs_write_passes(self):
        files, read = _reader({"lib/a.dart": "await prefs.setString('k', v);"})
        assert scan_provider_state_hygiene(files, read) == {}

    def test_tostring_with_display_name_is_flagged(self):
        files, read = _reader({"lib/m.dart": "String toString() => 'Profile(name: $displayName)';"})
        assert any("toString()" in v for v in scan_provider_state_hygiene(files, read).values())

    def test_pii_free_tostring_passes(self):
        files, read = _reader({"lib/m.dart": "String toString() => 'Profile(id: $id, settings: ${s.length})';"})
        assert scan_provider_state_hygiene(files, read) == {}


class TestRefinements:
    """Each of these was a false positive the first run produced against a real package."""

    def test_currency_code_fallback_is_not_ui_text(self):
        files, read = _reader({"lib/a.dart": "final currency = plan.currency ?? 'USD';"})
        assert scan_hardcoded_ui_strings(files, read) == {}

    def test_log_message_inside_an_enum_method_is_not_a_display_string(self):
        src = (
            "enum SubscriptionStatus {\n"
            "  active,\n"
            "  expired;\n"
            "  static SubscriptionStatus parse(String raw) {\n"
            "    SharedLog.log('Unknown subscription status \"$raw\" - treated as not entitled');\n"
            "    return expired;\n"
            "  }\n"
            "}\n"
        )
        files, read = _reader({"lib/e.dart": src})
        assert scan_hardcoded_ui_strings(files, read) == {}

    def test_style_definition_file_may_hold_raw_colours(self):
        files, read = _reader({"lib/theming/styles/retro_style.dart": "shadow: const Color(0x40000000),"})
        assert scan_hardcoded_ui_strings(files, read) == {}

    def test_spinner_inside_a_labelled_semantics_is_not_unlabelled(self):
        src = "Semantics(liveRegion: true, label: l10n.loading, child: const Center(child: CircularProgressIndicator()))"
        files, read = _reader({"lib/a.dart": src})
        assert scan_tappable_semantics(files, read) == {}

    def test_the_min_tap_target_helper_is_not_asked_to_wrap_itself(self):
        src = "class MinTapTargetBox extends StatelessWidget { Widget build(c) => GestureDetector(onTap: onTap, child: box); }"
        files, read = _reader({"lib/widgets/min_tap_target_box.dart": src})
        assert scan_tappable_semantics(files, read) == {}

    def test_injectable_clock_default_is_the_seam_not_the_bug(self):
        files, read = _reader({"lib/r.dart": "final at = now ?? DateTime.now();"})
        assert scan_parse_serialize_catch(files, read) == {}

    def test_parsed_timestamp_fallback_is_still_flagged(self):
        files, read = _reader({"lib/m.dart": "createdAt: DateTime.tryParse(json['created_at']) ?? DateTime.now(),"})
        assert any("?? DateTime.now()" in v for v in scan_parse_serialize_catch(files, read).values())

    def test_list_first_fallback_is_not_an_enum_default(self):
        files, read = _reader({"lib/r.dart": "subs.firstWhere((s) => s.isActive, orElse: () => subs.first);"})
        assert scan_parse_serialize_catch(files, read) == {}

    def test_returned_preferences_write_is_awaited_by_its_caller(self):
        files, read = _reader({"lib/e.dart": "Future<void> mark(String k) => _prefs.setBool(k, true);"})
        assert scan_provider_state_hygiene(files, read) == {}

    def test_explicit_min_tap_target_constraint_counts_as_wrapping(self):
        src = (
            "GestureDetector(onTap: x, child: Semantics(button: true, child: ConstrainedBox("
            "constraints: const BoxConstraints(minHeight: AppConstants.minTapTarget), child: c)))"
        )
        files, read = _reader({"lib/a.dart": src})
        assert scan_tappable_semantics(files, read) == {}

    def test_radio_role_counts_as_an_announced_role(self):
        src = (
            "Semantics(inMutuallyExclusiveGroup: true, checked: sel, label: o.label, child: "
            "GestureDetector(onTap: x, child: ConstrainedBox(constraints: BoxConstraints("
            "minHeight: AppConstants.minTapTarget), child: c)))"
        )
        files, read = _reader({"lib/a.dart": src})
        assert scan_tappable_semantics(files, read) == {}

    def test_inkwell_is_not_asked_for_button_semantics(self):
        src = "InkWell(onTap: x, child: ConstrainedBox(constraints: BoxConstraints(minHeight: AppConstants.minTapTarget), child: c))"
        files, read = _reader({"lib/a.dart": src})
        assert scan_tappable_semantics(files, read) == {}

    def test_bare_gesture_detector_still_needs_a_role(self):
        src = "GestureDetector(onTap: x, child: MinTapTargetBox(child: c))"
        files, read = _reader({"lib/a.dart": src})
        problems = scan_tappable_semantics(files, read)
        assert any("announced role" in v for v in problems.values())
