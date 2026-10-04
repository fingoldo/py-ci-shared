"""constant_relations: the dashboard letter timeout against two live_config defaults, reproduced as fixture packages."""

from __future__ import annotations

import ast
import sys
import uuid
from pathlib import Path

import pytest

from py_ci_shared import constant_relations as cr

pytest.importorskip("pydantic")

_MODELS = """
from pydantic import BaseModel, Field

class LLMConfig(BaseModel):
    call_timeout_sec: float = Field({llm}, ge=1, le=3600)

class EvaluatorConfig(BaseModel):
    judge_call_timeout_sec: float = Field(300.0, ge=0, le=3600)

class AppConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    evaluator: EvaluatorConfig = Field(default_factory=EvaluatorConfig)
"""

_TOML_TEXT = '_DEFAULT_TOML = """\\n[llm]\\ncall_timeout_sec = {llm}\\n"""\n'


def _packages(tmp_path: Path, *, letter: float, llm: float) -> tuple[str, str]:
    """A `dash_*.data` module and a `cfg_*.models` module with unique names, so variants never share sys.modules."""
    tag = uuid.uuid4().hex[:8]
    dash, cfg = f"dash_{tag}", f"cfg_{tag}"
    (tmp_path / dash).mkdir()
    (tmp_path / dash / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / dash / "data.py").write_text(f"_LETTER_TIMEOUT_S = {letter}\n", encoding="utf-8")
    (tmp_path / cfg).mkdir()
    (tmp_path / cfg / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / cfg / "models.py").write_text(_MODELS.format(llm=llm), encoding="utf-8")
    (tmp_path / cfg / "defaults_toml.py").write_text(_TOML_TEXT.format(llm=llm), encoding="utf-8")
    (tmp_path / "config.toml").write_text(f"[llm]\ncall_timeout_sec = {llm}\n", encoding="utf-8")
    return dash, cfg


def _registry(tmp_path: Path, dash: str, cfg: str) -> Path:
    reg = tmp_path / "relations.toml"
    reg.write_text(
        "[names]\n"
        'llm_live = "toml:config.toml#llm.call_timeout_sec"\n'
        f'llm_template = "toml-attr:{cfg}.defaults_toml._DEFAULT_TOML#llm.call_timeout_sec"\n'
        "[[relation]]\n"
        f'check = "{dash}.data._LETTER_TIMEOUT_S >= {cfg}.models.AppConfig.llm.call_timeout_sec'
        f' + {cfg}.models.AppConfig.evaluator.judge_call_timeout_sec"\n'
        'reason = "best-of-N waits for the slowest variant, then the judge"\n'
        "[[relation]]\n"
        f'check = "{dash}.data._LETTER_TIMEOUT_S >= llm_live + {cfg}.models.AppConfig.evaluator.judge_call_timeout_sec"\n'
        'reason = "the live config.toml value, not only the compiled default"\n'
        "[[relation]]\n"
        f'check = "llm_template == {cfg}.models.AppConfig.llm.call_timeout_sec"\n'
        'reason = "the generated template matches the model default"\n',
        encoding="utf-8",
    )
    return reg


def test_the_raised_llm_timeout_is_reported_with_both_sides(tmp_path):
    """The real defect: llm 300 -> 900 while the dashboard still waited 900 s."""
    dash, cfg = _packages(tmp_path, letter=900.0, llm=900.0)
    problems = cr.find_constant_relation_problems(_registry(tmp_path, dash, cfg), sys_path=[tmp_path])
    violated = [p for p in problems if p.startswith("VIOLATED")]
    assert len(violated) == 2, problems
    assert "= 900" in violated[0] and "= 1200" in violated[0] and "judge" in violated[0], violated[0]
    assert "llm_live=900" in violated[1]
    with pytest.raises(pytest.fail.Exception, match="2 declared constant relation"):
        cr.assert_constant_relations(_registry(tmp_path, dash, cfg), sys_path=[tmp_path])


def test_the_fixed_wait_passes(tmp_path):
    dash, cfg = _packages(tmp_path, letter=1200.0, llm=900.0)
    assert cr.find_constant_relation_problems(_registry(tmp_path, dash, cfg), sys_path=[tmp_path]) == []
    assert str(tmp_path) not in sys.path


def test_teeth_the_comparison_is_what_reports_it(tmp_path, monkeypatch):
    dash, cfg = _packages(tmp_path, letter=900.0, llm=900.0)
    monkeypatch.setitem(cr._COMPARE, ast.GtE, (">=", lambda a, b: True))
    assert cr._COMPARE[ast.GtE][1](0, 1) is True  # the substitution took
    assert cr.find_constant_relation_problems(_registry(tmp_path, dash, cfg), sys_path=[tmp_path]) == []


def test_a_renamed_constant_is_a_finding_not_a_dropped_relation(tmp_path):
    dash, cfg = _packages(tmp_path, letter=1200.0, llm=900.0)
    rel = cr.Relation(f"{dash}.data._LETTER_TIMEOUT_SEC >= {cfg}.models.AppConfig.llm.call_timeout_sec", "renamed")
    (problem,) = cr.find_constant_relation_problems([rel], sys_path=[tmp_path])
    assert problem.startswith("UNRESOLVED") and "_LETTER_TIMEOUT_SEC" in problem and "renamed or removed" in problem
    rel = cr.Relation("nosuchpkg_zz.X >= 1", "gone")
    assert "no importable module prefix" in cr.find_constant_relation_problems([rel])[0]


def test_a_module_that_fails_on_import_is_named(tmp_path):
    tag = uuid.uuid4().hex[:8]
    (tmp_path / f"broken_{tag}.py").write_text("import nosuchdep_zz\nX = 1\n", encoding="utf-8")
    (problem,) = cr.find_constant_relation_problems([cr.Relation(f"broken_{tag}.X >= 0", "r")], sys_path=[tmp_path])
    assert "importing broken_" in problem and "nosuchdep_zz" in problem


@pytest.mark.parametrize("check", ["__import__('os').getcwd() > 1", "len('ab') > 1", "1 if 2 else 3 > 0", "2 ** 3 > 1", "1 + 2", "a.b[0] > 1"])
def test_anything_outside_the_grammar_is_refused_never_run(check):
    (problem,) = cr.find_constant_relation_problems([cr.Relation(check, "r")])
    assert problem.startswith("UNRESOLVED"), problem


def test_non_numbers_division_by_zero_and_an_empty_registry(tmp_path):
    tag = uuid.uuid4().hex[:8]
    (tmp_path / f"vals_{tag}.py").write_text("S = 'x'\nB = True\nZ = 0\n", encoding="utf-8")
    rels = [cr.Relation(f"vals_{tag}.S > 0", "r"), cr.Relation(f"vals_{tag}.B > 0", "r"), cr.Relation(f"1 / vals_{tag}.Z > 0", "r")]
    problems = cr.find_constant_relation_problems(rels, sys_path=[tmp_path])
    assert "not a number" in problems[0] and "bool" in problems[1] and "division by zero" in problems[2]
    empty = tmp_path / "empty.toml"
    empty.write_text("", encoding="utf-8")
    assert "checks nothing" in cr.find_constant_relation_problems(empty)[0]


def test_registry_entries_need_a_reason_and_bad_toml_is_reported(tmp_path):
    reg = tmp_path / "r.toml"
    reg.write_text('[[relation]]\ncheck = "1 < 2"\n', encoding="utf-8")
    assert "no `reason`" in cr.find_constant_relation_problems(reg)[0]
    reg.write_text("[[relation\n", encoding="utf-8")
    assert "unreadable relation registry" in cr.find_constant_relation_problems(reg)[0]


def test_dataclass_defaults_and_chained_comparisons(tmp_path):
    tag = uuid.uuid4().hex[:8]
    (tmp_path / f"dc_{tag}.py").write_text(
        "from dataclasses import dataclass, field\n@dataclass\nclass C:\n    a: int = 5\n    b: list = field(default_factory=lambda: [1])\n",
        encoding="utf-8",
    )
    ok = cr.Relation(f"0 < dc_{tag}.C.a <= 5", "r")
    bad = cr.Relation(f"0 < dc_{tag}.C.a < 5", "r")
    problems = cr.find_constant_relation_problems([ok, bad], sys_path=[tmp_path])
    assert len(problems) == 1 and "< 5" in problems[0], problems
