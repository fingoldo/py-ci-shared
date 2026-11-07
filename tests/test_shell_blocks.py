"""_shell_blocks: block membership of shell statements and the run: scripts of workflow files (audit 41-F12)."""

from __future__ import annotations

from py_ci_shared._shell_blocks import TOP_LEVEL, parse_shell, workflow_run_scripts


def _blocks(script: str) -> "dict[str, int]":
    return {s.text: s.block for s in parse_shell(script.splitlines())}


def test_if_then_else_bodies_are_distinct_and_the_condition_belongs_to_the_outer_block() -> None:
    b = _blocks("if probe; then\n  a\nelse\n  b\nfi\nc\n")
    assert b["probe"] == b["c"] == TOP_LEVEL
    assert len({b["a"], b["b"], TOP_LEVEL}) == 3


def test_one_line_forms_and_case_clauses() -> None:
    b = _blocks('if x; then a; b; fi; c\ncase "$v" in\n  p|q) d; e ;;\n  *) f ;;\nesac\ng\n')
    assert b["a"] == b["b"] != b["c"] == TOP_LEVEL
    assert b["d"] == b["e"] != b["f"]
    assert b["g"] == TOP_LEVEL


def test_groups_functions_and_subshells() -> None:
    stmts = {s.text: s for s in parse_shell("f() {\n  a\n}\nx || { b; c; }\n(d)\ne\n".splitlines())}
    assert stmts["a"].in_function and not stmts["b"].in_function
    assert stmts["b"].block == stmts["c"].block != TOP_LEVEL and stmts["c"].conditional is False
    assert stmts["d"].block != TOP_LEVEL and stmts["e"].block == TOP_LEVEL


def test_conditional_flag_follows_and_or() -> None:
    s = {x.text: x for x in parse_shell(["a && b; c || d"])}
    assert [s[k].conditional for k in "abcd"] == [False, True, False, True]


def test_heredocs_quotes_continuations_and_comments() -> None:
    texts = [s.text for s in parse_shell(["cat <<-'EOF'", "\tfi", "\tEOF", "echo 'a; b' \\", "  c  # note; hidden", 'echo "x', 'y"; last'])]
    assert texts == ["cat <<-'EOF'", "echo 'a; b' c", 'echo "x\ny"', "last"]


def test_substitutions_do_not_split() -> None:
    assert [s.text for s in parse_shell(["x=$(a; b) && y=`c; d`; (( n++ )); echo $((1+2)) 2>&1"])] == [
        "x=$(a; b)",
        "y=`c; d`",
        "(( n++ ))",
        "echo $((1+2)) 2>&1",
    ]


def test_run_scripts_of_a_workflow() -> None:
    text = (
        "steps:\n  - name: a\n    run: |\n      one\n\n      two\n    env:\n      K: v\n"
        "  - run: inline cmd\n  - run: 'quoted'\n    shell: python\n  - uses: x\n"
    )
    got = workflow_run_scripts(text)
    assert [(s.first_line, s.lines, s.shell) for s in got] == [
        (3, ["one", "", "two"], "bash"),
        (8, ["inline cmd"], "bash"),
        (9, ["quoted"], "python"),
    ]


def test_a_shell_key_of_the_next_step_is_not_this_steps_shell() -> None:
    text = "steps:\n  - name: a\n    run: one\n  - shell: python\n    run: two\n"
    assert [s.shell for s in workflow_run_scripts(text)] == ["bash", "python"]


def test_a_last_case_clause_without_double_semicolon_does_not_swallow_the_rest() -> None:
    b = _blocks('case "$v" in\n  a) one\n  ;;\n  b) two\nesac\nafter\n')
    assert b["after"] == TOP_LEVEL and b["two"] != TOP_LEVEL
    b = _blocks('case "$v" in a) x ;; b) y ;; esac; z\n')
    assert b["z"] == TOP_LEVEL
