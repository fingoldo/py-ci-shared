"""Tests for the field/text agreement record checker, ported from pyutilz when it moved into py_ci_shared.code_audit."""

from __future__ import annotations

from py_ci_shared.code_audit.field_text_agreement import (
    AGREE,
    CONTRADICT,
    KIND_OPPOSED,
    KIND_UNFILLED,
    UNCHECKABLE,
    FieldTextRule,
    check_record,
    check_records,
    cues_in_text,
)


def _temporal_rule() -> FieldTextRule:
    """A representative FieldTextRule: the forensic temporal_class pair, cues and anti-cues included."""
    return FieldTextRule(
        name="temporal_class",
        field="temporal_class",
        text_fields=("subject", "object"),
        cues={"antemortem": ("antemortem", "vital", "vitality"), "postmortem": ("postmortem", "putrefaction")},
        anti_cues={"antemortem": ("vital organs",)},
        neutral_values=frozenset({"na", ""}),
        partitions=(frozenset({"antemortem", "perimortem", "agonal"}), frozenset({"postmortem", "artifact"})),
    )


def test_field_text_agreement_flags_a_field_its_own_text_contradicts():
    v = check_record(_temporal_rule(), {"subject": "haemorrhage", "object": "vital hanging", "temporal_class": "postmortem"})
    assert v.outcome == CONTRADICT and v.kind == KIND_OPPOSED and v.supported == "antemortem"


def test_field_text_agreement_reads_a_neutral_field_as_unfilled_not_as_agreement():
    v = check_record(_temporal_rule(), {"subject": "x", "object": "ante-mortem hanging", "temporal_class": "na"})
    assert v.outcome == CONTRADICT and v.kind == KIND_UNFILLED


def test_field_text_agreement_hyphenation_does_not_hide_a_cue():
    rule = _temporal_rule()
    assert cues_in_text(rule, "ante-mortem hanging") == cues_in_text(rule, "antemortem hanging")


def test_field_text_agreement_anti_cue_cancels_only_the_homograph():
    rule = _temporal_rule()
    assert check_record(rule, {"subject": "injury to vital organs", "object": "y", "temporal_class": "perimortem"}).outcome == UNCHECKABLE
    # ...and the same word still fires where it is a real vitality claim.
    assert check_record(rule, {"subject": "vital reaction", "object": "y", "temporal_class": "antemortem"}).outcome == AGREE


def test_field_text_agreement_compatible_partition_members_agree():
    rule = _temporal_rule()
    assert check_record(rule, {"subject": "vital reaction", "object": "y", "temporal_class": "perimortem"}).outcome == AGREE
    assert check_record(rule, {"subject": "putrefaction", "object": "y", "temporal_class": "artifact"}).outcome == AGREE
    assert check_record(rule, {"subject": "putrefaction", "object": "y", "temporal_class": "antemortem"}).outcome == CONTRADICT


def test_field_text_agreement_publishes_coverage_and_an_empty_vocabulary_is_uncheckable():
    rows = [{"subject": "a", "object": "b", "temporal_class": "na"}, {"subject": "putrefaction", "object": "b", "temporal_class": "postmortem"}]
    rep = check_records(_temporal_rule(), rows)
    assert (rep.agree, rep.contradict, rep.uncheckable) == (1, 0, 1)
    assert rep.coverage == 0.5 and rep.as_dict()["coverage"] == 0.5
    blank = check_records(FieldTextRule(name="manner", field="manner", text_fields=("object",)), rows)
    assert blank.uncheckable == 2 and blank.agree == 0 and blank.has_vocabulary is False


def test_field_text_agreement_resolver_overrides_the_cue_table():
    rule = FieldTextRule(
        name="modality",
        field="modality",
        text_fields=("quote",),
        neutral_values=frozenset({"unstated", ""}),
        resolver=lambda rec: ("may", "possibly") if "possibly" in str(rec.get("quote", "")) else ("", ""),
    )
    assert check_record(rule, {"quote": "possibly fatal", "modality": "usual"}).outcome == CONTRADICT
    assert check_record(rule, {"quote": "fatal", "modality": "usual"}).outcome == "uncheckable"


def test_field_text_agreement_renders_a_finding():
    from py_ci_shared.code_audit._base import Finding

    v = check_record(_temporal_rule(), {"subject": "x", "object": "vital hanging", "temporal_class": "postmortem"})
    f = v.as_finding(file="bench/gold/PMC13161347.json", line=1)
    assert isinstance(f, Finding) and f.check == "field_text_temporal_class" and "postmortem" in f.detail
