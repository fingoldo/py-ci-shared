"""Unit tests for the import-layering check. Real scratch source trees, same no-mocking convention
as this package's other tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.import_layering import LayerRule, assert_layering, find_layering_violations

_CORE_RULE = LayerRule(
    "lib/core/**",
    ["lib/providers/**", "lib/screens/**"],
    reason="core/ is the product-neutral layer other apps consume",
)


def _tree(tmp_path: Path, **files: str) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


class TestFindLayeringViolations:
    def test_relative_import_across_the_boundary_is_flagged(self, tmp_path):
        root = _tree(
            tmp_path,
            **{
                "lib__core__landing__renderer.dart": "import '../../providers/theme.dart';\n",
                "lib__providers__theme.dart": "// provider\n",
            },
        )
        problems = find_layering_violations(root, [_CORE_RULE])
        assert len(problems) == 1
        assert "lib/providers/theme.dart" in problems[0]
        assert "product-neutral" in problems[0]

    def test_package_import_across_the_boundary_is_flagged(self, tmp_path):
        root = _tree(
            tmp_path,
            **{
                "lib__core__landing__renderer.dart": "import 'package:glossum/screens/x.dart';\n",
                "lib__screens__x.dart": "// screen\n",
            },
        )
        problems = find_layering_violations(root, [_CORE_RULE], package_roots={"glossum": "lib"})
        assert len(problems) == 1
        assert "lib/screens/x.dart" in problems[0]

    def test_import_inside_the_layer_passes(self, tmp_path):
        root = _tree(
            tmp_path,
            **{
                "lib__core__landing__renderer.dart": "import '../theme/palette.dart';\n",
                "lib__core__theme__palette.dart": "// palette\n",
            },
        )
        assert find_layering_violations(root, [_CORE_RULE]) == []

    def test_external_package_import_is_ignored(self, tmp_path):
        root = _tree(
            tmp_path,
            **{"lib__core__landing__renderer.dart": "import 'package:flutter/material.dart';\nimport 'dart:math';\n"},
        )
        assert find_layering_violations(root, [_CORE_RULE]) == []

    def test_allow_files_exempts_a_specific_file(self, tmp_path):
        rule = LayerRule("lib/core/**", ["lib/providers/**"], allow_files=("lib/core/landing/renderer.dart",))
        root = _tree(
            tmp_path,
            **{
                "lib__core__landing__renderer.dart": "import '../../providers/theme.dart';\n",
                "lib__providers__theme.dart": "// provider\n",
            },
        )
        assert find_layering_violations(root, [rule]) == []

    def test_advisory_rule_is_prefixed_not_dropped(self, tmp_path):
        rule = LayerRule("lib/core/**", ["lib/providers/**"], advisory=True)
        root = _tree(
            tmp_path,
            **{
                "lib__core__landing__renderer.dart": "import '../../providers/theme.dart';\n",
                "lib__providers__theme.dart": "// provider\n",
            },
        )
        problems = find_layering_violations(root, [rule])
        assert len(problems) == 1
        assert problems[0].startswith("ADVISORY ")

    def test_python_and_typescript_import_shapes_are_understood(self, tmp_path):
        rule = LayerRule("core/**", ["app/**"])
        root = _tree(
            tmp_path,
            **{
                "core__a.ts": "import { x } from '../app/b';\n",
                "app__b.ts": "export const x = 1;\n",
            },
        )
        problems = find_layering_violations(root, [rule])
        assert len(problems) == 1

    def test_no_matching_file_reports_examining_nothing(self, tmp_path):
        root = _tree(tmp_path, **{"other__x.dart": "// nothing\n"})
        problems = find_layering_violations(root, [_CORE_RULE])
        assert len(problems) == 1
        assert "examined nothing" in problems[0]


class TestAssert:
    def test_assert_passes(self, tmp_path):
        root = _tree(
            tmp_path,
            **{
                "lib__core__a.dart": "import 'package:flutter/material.dart';\n",
                "lib__providers__theme.dart": "// provider\n",
            },
        )
        assert_layering(root, [_CORE_RULE])

    def test_assert_fails_naming_the_importer(self, tmp_path):
        root = _tree(
            tmp_path,
            **{
                "lib__core__a.dart": "import '../providers/theme.dart';\n",
                "lib__providers__theme.dart": "// provider\n",
            },
        )
        with pytest.raises(pytest.fail.Exception, match=r"lib/core/a\.dart"):
            assert_layering(root, [_CORE_RULE])


class TestAuditRegressions:
    _PY_RULE = LayerRule("pkg/core/**", ["pkg/providers/**"])

    @pytest.mark.parametrize(
        "source",
        ["from ..providers import p\n", "from ..providers.p import thing\n", "from pkg.providers import p\n", "import pkg.providers.p as p\n"],
    )
    def test_python_imports_across_the_boundary_are_flagged(self, tmp_path, source):
        root = _tree(tmp_path, **{"pkg__core__a.py": source, "pkg__providers__p.py": "thing = 1\n", "pkg__providers____init__.py": "", "pkg____init__.py": ""})
        problems = find_layering_violations(root, [self._PY_RULE])
        assert len(problems) == 1 and "pkg/core/a.py:1: imports pkg/providers/" in problems[0]

    @pytest.mark.parametrize("source", ["from . import b\n", "import os\nfrom pathlib import Path\n", "from pkg.core import b\n"])
    def test_python_imports_inside_the_layer_or_external_pass(self, tmp_path, source):
        root = _tree(tmp_path, **{"pkg__core__a.py": source, "pkg__core__b.py": "", "pkg____init__.py": "", "pkg__core____init__.py": ""})
        assert find_layering_violations(root, [self._PY_RULE]) == []

    def test_an_unparsable_python_file_is_reported(self, tmp_path):
        root = _tree(tmp_path, **{"pkg__core__a.py": "from .. import (\n"})
        problems = find_layering_violations(root, [self._PY_RULE])
        assert len(problems) == 1 and "cannot be parsed" in problems[0]

    @pytest.mark.parametrize(
        "source",
        ["// see from '../../providers/x'\n", "/* import '../providers/theme.dart';\n*/\n", "const url = 'http://x'; // import '../providers/theme.dart';\n"],
    )
    def test_imports_named_in_comments_are_not_imports(self, tmp_path, source):
        root = _tree(tmp_path, **{"lib__core__a.dart": source, "lib__providers__theme.dart": "// provider\n"})
        assert find_layering_violations(root, [_CORE_RULE]) == []

    def test_a_real_import_after_a_block_comment_keeps_its_line(self, tmp_path):
        root = _tree(tmp_path, **{"lib__core__a.dart": "/*\n  header\n*/\nimport '../providers/theme.dart';\n", "lib__providers__theme.dart": ""})
        (problem,) = find_layering_violations(root, [_CORE_RULE])
        assert problem.startswith("lib/core/a.dart:4:")

    def test_excluded_directories_are_not_walked(self, tmp_path):
        root = _tree(
            tmp_path,
            **{
                "lib__core__a.dart": "import 'package:flutter/material.dart';\n",
                "node_modules__lib__core__b.ts": "import '../../providers/x';\n",
                ".venv__lib__core__c.py": "from ..providers import x\n",
            },
        )
        assert find_layering_violations(root, [LayerRule("**/lib/core/**", ["**/providers/**"]), _CORE_RULE]) == []
