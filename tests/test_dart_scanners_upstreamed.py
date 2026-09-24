"""Tests for the Dart scanners both Flutter repos used to carry as local copies, and their file-listing plumbing.

In-memory sources behind a dict reader, as in test_dart_scanners.py; the plumbing tests use a real tmp_path tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import SourceReadError
from py_ci_shared.dart_scanners import (
    SCANNERS,
    dart_files_under,
    dart_reader,
    package_name,
    scan_double_error_reports,
    scan_empty_catch,
    scan_file_size,
    scan_import_cycles,
    scan_source_text_assertions,
    scan_tests_without_assertions,
    scan_timed_dismissal,
    scan_unused_test_seams,
)


def _reader(sources: dict):
    return list(sources), (lambda rel: sources[rel])


class TestFileSize:
    def test_file_over_the_limit_is_flagged_by_path(self):
        files, read = _reader({"lib/big.dart": "x\n" * 1000})
        assert scan_file_size(files, read) == {"lib/big.dart": "1001 lines"}

    def test_file_at_the_limit_passes(self):
        files, read = _reader({"lib/ok.dart": "x\n" * 999 + "x"})
        assert scan_file_size(files, read) == {}

    def test_limit_is_a_parameter(self):
        files, read = _reader({"lib/a.dart": "a\nb\nc"})
        assert list(scan_file_size(files, read, max_lines=2)) == ["lib/a.dart"]


class TestEmptyCatch:
    def test_empty_body_is_flagged(self):
        files, read = _reader({"lib/a.dart": "void f() {\n  try {\n    g();\n  } catch (_) {}\n}\n"})
        found = scan_empty_catch(files, read)
        assert len(found) == 1
        assert "(line 4)" in next(iter(found.values()))

    def test_comment_only_body_with_two_bindings_is_flagged(self):
        files, read = _reader({"lib/a.dart": "try { g(); } catch (_, _) {\n  // cache write must not break the response\n}\n"})
        assert len(scan_empty_catch(files, read)) == 1

    def test_body_that_does_something_passes(self):
        files, read = _reader({"lib/a.dart": "try { g(); } catch (_) { return null; }\n"})
        assert scan_empty_catch(files, read) == {}

    def test_key_survives_an_edit_above_the_finding(self):
        body = "void f() {\n  try {\n    g();\n  } catch (_) {}\n}\n"
        files, read = _reader({"lib/a.dart": body})
        before = set(scan_empty_catch(files, read))
        files, read = _reader({"lib/a.dart": "// new header\n\n" + body})
        assert set(scan_empty_catch(files, read)) == before


class TestSourceTextAssertions:
    def test_reading_source_in_a_test_is_flagged(self):
        files, read = _reader({"test/a_test.dart": "final s = File('lib/a.dart').readAsStringSync();\n"})
        assert len(scan_source_text_assertions(files, read)) == 1

    def test_directory_listing_read_is_flagged(self):
        files, read = _reader({"test/a_test.dart": "Directory('lib').listSync().map((f) => f.readAsLines());\n"})
        assert len(scan_source_text_assertions(files, read)) == 1

    def test_file_without_a_text_read_passes(self):
        files, read = _reader({"test/a_test.dart": "final exists = File('fixture.png').existsSync();\n"})
        assert scan_source_text_assertions(files, read) == {}


class TestImportCycles:
    def test_package_import_cycle_is_found(self):
        files, read = _reader(
            {
                "lib/a.dart": "import 'package:app/b.dart';\n",
                "lib/b.dart": "import 'package:app/a.dart';\n",
            }
        )
        assert scan_import_cycles(files, read, package="app") == {"lib/a.dart": "import cycle: lib/a.dart -> lib/b.dart -> lib/a.dart"}

    def test_cycle_through_a_barrel_export_with_double_quotes_is_found(self):
        files, read = _reader(
            {
                "lib/src/barrel.dart": '  export "widgets/w.dart";\n',
                "lib/src/widgets/w.dart": "import '../barrel.dart';\n",
            }
        )
        assert list(scan_import_cycles(files, read, package="app")) == ["lib/src/barrel.dart"]

    def test_acyclic_graph_and_external_imports_pass(self):
        files, read = _reader(
            {
                "lib/a.dart": "import 'dart:async';\nimport 'package:other/a.dart';\nimport 'b.dart';\n",
                "lib/b.dart": "import 'package:flutter/material.dart';\n",
            }
        )
        assert scan_import_cycles(files, read, package="app") == {}

    def test_commented_out_import_is_not_an_edge(self):
        files, read = _reader(
            {
                "lib/a.dart": "import 'b.dart';\n",
                "lib/b.dart": "/*\nimport 'a.dart';\n*/\n",
            }
        )
        assert scan_import_cycles(files, read, package="app") == {}


class TestTestsWithoutAssertions:
    def test_test_with_no_assertion_is_flagged_by_name(self):
        files, read = _reader({"test/a_test.dart": "void main() {\n  test('does work', () {\n    run();\n  });\n}\n"})
        assert scan_tests_without_assertions(files, read) == {"test/a_test.dart::does work": "test contains no assertion (line 2)"}

    def test_widget_test_whose_only_expect_is_commented_out_is_flagged(self):
        files, read = _reader(
            {"test/a_test.dart": "testWidgets('renders', (t) async {\n  await t.pumpWidget(w);\n  // expect(find.text('x'), findsOneWidget);\n});\n"}
        )
        assert len(scan_tests_without_assertions(files, read)) == 1

    @pytest.mark.parametrize("assertion", ["expect(x, 1);", "expectMinTapTargets(t);", "verify(m.call());", "fail('no');"])
    def test_any_assertion_form_passes(self, assertion):
        files, read = _reader({"test/a_test.dart": f"test('ok', () {{\n  {assertion}\n}});\n"})
        assert scan_tests_without_assertions(files, read) == {}

    def test_two_tests_of_one_name_are_both_reported(self):
        files, read = _reader({"test/a_test.dart": "test('same', () { a(); });\ntest('same', () { b(); });\n"})
        assert set(scan_tests_without_assertions(files, read)) == {"test/a_test.dart::same", "test/a_test.dart::same#1"}


class TestTimedDismissal:
    def test_timer_that_pops_the_route_is_flagged(self):
        src = "Future.delayed(const Duration(seconds: 3), () {\n  Navigator.of(context).pop();\n});\n"
        files, read = _reader({"lib/a.dart": src})
        assert len(scan_timed_dismissal(files, read)) == 1

    def test_pop_from_a_button_passes(self):
        src = "Future.delayed(const Duration(seconds: 3), refresh);\nonPressed: () => Navigator.of(context).pop();\n"
        files, read = _reader({"lib/a.dart": src})
        assert scan_timed_dismissal(files, read) == {}

    def test_commented_out_timer_passes(self):
        files, read = _reader({"lib/a.dart": "// Future.delayed(d, () { Navigator.of(context).pop(); });\n"})
        assert scan_timed_dismissal(files, read) == {}


class TestDoubleErrorReports:
    def test_both_reporters_in_one_catch_is_flagged(self):
        src = "try { g(); } catch (e, st) {\n  AppLog.error('x', e, st);\n  ErrorService.recordError(e, st);\n}\n"
        files, read = _reader({"lib/a.dart": src})
        assert len(scan_double_error_reports(files, read)) == 1

    def test_one_reporter_passes_and_a_comment_does_not_count(self):
        src = "try { g(); } catch (e, st) {\n  AppLog.error('x', e, st);\n  // not ErrorService.recordError, it double-reports\n}\n"
        files, read = _reader({"lib/a.dart": src})
        assert scan_double_error_reports(files, read) == {}

    def test_reporter_names_are_parameters(self):
        src = "try { g(); } catch (e) { Log.e(e); Crash.report(e); }\n"
        files, read = _reader({"lib/a.dart": src})
        assert scan_double_error_reports(files, read) == {}
        assert len(scan_double_error_reports(files, read, logger_call="Log.e", recorder_call="Crash.report")) == 1


class TestUnusedTestSeams:
    def test_seam_no_test_mentions_is_flagged(self):
        _, read = _reader({"lib/a.dart": "@visibleForTesting\nstatic void resetCache() {}\n", "test/a_test.dart": "void main() {}\n"})
        assert scan_unused_test_seams(["lib/a.dart"], read, test_files=["test/a_test.dart"]) == {
            "lib/a.dart::resetCache": "@visibleForTesting member used by no test"
        }

    def test_seam_a_test_uses_passes(self):
        _, read = _reader({"lib/a.dart": "@visibleForTesting\nint counter = 0;\n", "test/a_test.dart": "expect(counter, 0);\n"})
        assert scan_unused_test_seams(["lib/a.dart"], read, test_files=["test/a_test.dart"]) == {}

    def test_name_used_only_as_a_substring_is_still_unused(self):
        _, read = _reader({"lib/a.dart": "@visibleForTesting\nvoid seed() {}\n", "test/a_test.dart": "reseeded();\n"})
        assert list(scan_unused_test_seams(["lib/a.dart"], read, test_files=["test/a_test.dart"])) == ["lib/a.dart::seed"]


class TestPlumbing:
    def _tree(self, root: Path) -> None:
        (root / "lib" / "src" / "l10n" / "generated").mkdir(parents=True)
        (root / "lib" / "b").mkdir()
        (root / "pubspec.yaml").write_bytes(b"\xef\xbb\xbfname: my_app\nversion: 1.0.0\n")
        (root / "lib" / "a.dart").write_bytes(b"\xef\xbb\xbfvoid a() {}\n")
        (root / "lib" / "b" / "c.dart").write_bytes(b"void c() {}\n")
        (root / "lib" / "m.g.dart").write_bytes(b"part of 'm.dart';\n")
        (root / "lib" / "gen.dart").write_bytes(b"// GENERATED CODE - DO NOT MODIFY BY HAND\n")
        (root / "lib" / "src" / "l10n" / "generated" / "x.dart").write_bytes(b"class X {}\n")

    def test_listing_skips_generated_files_and_reader_strips_the_bom(self, tmp_path):
        self._tree(tmp_path)
        files = dart_files_under(tmp_path, "lib")
        assert files == ["lib/a.dart", "lib/b/c.dart"]
        assert dart_reader(tmp_path)("lib/a.dart") == "void a() {}\n"

    def test_generated_prefixes_are_a_parameter(self, tmp_path):
        self._tree(tmp_path)
        assert "lib/src/l10n/generated/x.dart" in dart_files_under(tmp_path, "lib", generated_prefixes=())

    def test_undecodable_file_stays_listed_and_the_reader_names_it(self, tmp_path):
        self._tree(tmp_path)
        (tmp_path / "lib" / "bad.dart").write_bytes(b"void \xff() {}\n")
        assert "lib/bad.dart" in dart_files_under(tmp_path, "lib")
        with pytest.raises(SourceReadError, match=r"bad.dart"):
            dart_reader(tmp_path)("lib/bad.dart")

    def test_package_name_reads_past_a_bom(self, tmp_path):
        self._tree(tmp_path)
        assert package_name(tmp_path) == "my_app"

    def test_pubspec_without_a_name_raises(self, tmp_path):
        (tmp_path / "pubspec.yaml").write_bytes(b"version: 1.0.0\n")
        with pytest.raises(RuntimeError, match="name"):
            package_name(tmp_path)


def test_every_upstreamed_scan_is_registered():
    upstreamed = {
        "file-size",
        "empty-catch",
        "source-text-assertions",
        "import-cycles",
        "tests-without-assertions",
        "timed-dismissal",
        "double-error-reports",
        "unused-test-seams",
    }
    assert upstreamed <= set(SCANNERS)
