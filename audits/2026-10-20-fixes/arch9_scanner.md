# ARCH-9 follow-up: phantom-code-reference scanner false positives

Branch `fix/phantom-scanner-fp` (from origin/master `d122e8a`). Scanner: `src/py_ci_shared/phantom_code_references.py`.
Tests: `tests/test_phantom_code_references.py` (39 pass, one unit test per rule, each with a neighbouring real phantom that still fires).

## Setup notes

- `audits/2026-10-02-full-audit/TRACKER.md` and `DECISIONS.md` do not exist in py-ci-shared. The consumer's copies
  (`Redline/autopsia-wt-batch4`) are modified and uncommitted; they were not read or changed. The `ARCH-9` row in py-ci-shared's
  `audits/TRACKER.md` is about manifest hooks, an unrelated item.
- The consumer was scanned from a `git archive origin/master` export (`scratchpad/autopsia_master`), not the working tree.
- The consumer baseline (`tests/test_meta/_phantom_code_references_baseline.json`, 902 entries) is NOT rebuilt here: the brief keeps
  the pin update separate, and the consumer checkout was not touched. Rebuild after the pin moves.

## Before / after on the consumer tree (autopsia origin/master)

| | findings |
|---|---|
| before (origin/master scanner) | 901 |
| after (this branch) | 357 |
| removed | 544 (all attributed, below) |
| added | 0 |

## False-positive classes fixed

| Rule | Class | Removed | Mechanism and unit test |
|---|---|---|---|
| R1 | Python protocol names (`__file__`, `__dict__`, `__main__`, `__pycache__`) | 26 | Fixed set built from `dir()` of object/type/module/function types plus three runtime names. Any other dunder still fires (`test_python_protocol_dunder_names_are_not_repo_names`). |
| R2 | Standard vocabulary codes (ICD-10 `E11`, `A15-A19`; UMLS `C0020538`; Cochrane `CD000097.pub2`) | 77 | Shape regex. Test `test_standard_vocabulary_codes_are_not_repo_names`. |
| R3 | OS errno constants (`ENOENT`) | 1 | `hasattr(errno, name)`. Test `test_os_error_constants_are_not_repo_names`. |
| R4 | External data file names with extensions not in the list (`.dct`, `.RRF`, `.rrf`, `.dta`, `.parquet`, ...) | 43 | Extended `_FILE_EXT_RE`. Test `test_external_data_file_names_...`. |
| R5 | One-character symbols and placeholders (`Z`, `B`, `T`, `X`) | 36 | Single-character bare names with no call. Test `test_a_one_character_symbol_is_not_a_repo_name`. |
| R6 | Names the repo's code spells out as a string constant (data column, label, FHIR resource, key). Docstrings excluded. | 325 | Exact match against non-docstring string constants of the scanned files. Test `test_a_name_the_repo_spells_out_as_a_string_is_a_data_name`. |
| R7 | Bare `self.x` / `cls.x` attributes (`_last_request_at`, `_min_interval`) | 4 | `python_declarations` now also adds the bare attribute name. Test `test_a_bare_attribute_assigned_on_self_is_declared`. |
| R8 | `table.column` pairs both spelled in one repo SQL string | 32 | Both words in one non-docstring string constant. Test `test_table_dot_column_the_repo_builds_in_sql_is_not_a_phantom`. |
| R9 | `Head.member` where the commented file imports `Head` and no repo declaration owns it | 0 on this tree | Test `test_a_library_member_of_a_class_the_commented_file_imports_is_not_judged`. |

Structural: the new skip rules live in `_spelled_out_elsewhere` and `_outside_head_verdict` so the finder stays at the
ratchet complexity (16). `_PYTHON_PROTOCOL_NAMES` and `_STANDARD_CODE_RE` are module constants.

## Verified real (still reported, not changed)

Checked against the export. Each is a comment naming something the repo does not define:
- `_drop_age_excluded_candidates` (`autopsia/reason/clinical_caveats.py:582`): no `def` or assignment anywhere in the tree.
- `TestStep.as_dict` (`autopsia/api_models_views.py:197`), `CantMissCandidate.severity` (`api_models_views.py:93`): no class of these names.
- `already_reviewed()` (`bench/lay_synonym_import.py:155`), `verify_merge_result()` (`bench/merge_gz_jsonl_union.py:34`): no definition.

## Still reported, not fixed (scanner cannot decide mechanically)

- Library method and attribute calls with no imported head: `tobytes()`, `getresponse()`, `LOWER()` (SQLite), `StataReader.variable_labels()`
  where the commented file does not import `StataReader`. A safe rule would need a type index; a method-name allowlist would hide real calls.
- Stdlib class names cited bare (`TaskGroup`, `Element`, `CodeableConcept`): no stdlib class index.
- `_detailFrom` (client repo, cross-repo claim): unresolvable from this repo. Belongs in the consumer's `extra_known`, a consumer decision.
- Remaining 357 are mostly UPPER identifiers and class-shaped names with no string or SQL evidence in the repo, plus `_ru` / `_en` suffix twins.

## Risk to review

- R6 is the widest rule (325). A name is exempt if ANY non-docstring string constant equals it, so a comment naming a function that
  happens to be spelled as a string elsewhere is now silent. This is the price of the data-name class; the tests pin the
  docstring exclusion and a neighbouring phantom still fires.
- R8 accepts a `table.column` pair if one SQL string contains both words, so a wrong column name in a comment is silent when the table and
  the column both occur in some string. Weaker than an exact schema check; noted for review.

## Full removed list, by rule

## R1 Python protocol name (26)
autopsia/graph_context.py:72: __dict__
autopsia/graph_seed_cache.py:87: __dict__
autopsia/graph_serialisation.py:316: __dict__
autopsia/kb/ambulatory_presentation.py:64: __file__
autopsia/kb/cant_miss_severity.py:59: __file__
autopsia/kb/i18n_shared.py:8: __main__
autopsia/vocab/local_install.py:864: __dict__
bench/_do_dtype_probe.py:10: __pycache__
bench/_do_dtype_probe.py:17: __pycache__
bench/clinical_eval.py:40: __main__
bench/clinical_eval.py:41: __main__
bench/clinical_eval.py:469: __main__
bench/clinical_eval.py:470: __main__
bench/medspacy_context_worker.py:40: __file__
bench/track_b_sourcing_pilot.py:44: __main__
bench/track_c_sourcing_pilot.py:47: __main__
tests/conftest.py:107: __file__
tests/test_ambulatory_presentation.py:239: __defaults__
tests/test_graph_honesty.py:1214: __dict__
tests/test_graph_honesty.py:273: __dict__
tests/test_i18n_import_order.py:11: __main__
tests/test_i18n_import_order.py:53: __main__
tests/test_i18n_import_order.py:58: __main__
tests/test_meta/test_documented_commands_are_runnable.py:24: __main__
tests/test_meta/test_documented_commands_are_runnable.py:7: __main__
tests/test_meta/test_the_installed_package_is_this_checkout.py:23: __file__
## R2 standard vocabulary code (77)
autopsia/kb/cochrane/wiley_tdm.py:22: CD000056.pub2
autopsia/kb/cochrane/wiley_tdm.py:22: CD012570.pub2
autopsia/kb/cochrane/wiley_tdm.py:23: CD000097.pub2
autopsia/kb/ddxplus_prevalence_overlay.py:33: B20
autopsia/kb/famenet_leak_floor.py:265: X29
autopsia/kb/famenet_leak_floor.py:66: X29
autopsia/kb/famenet_leak_floor.py:67: U29
autopsia/kb/i18n_mkb10_join.py:201: Q96
autopsia/kb/icd10_bridge.py:12: A37
autopsia/kb/icd10_bridge.py:145: A15
autopsia/kb/icd10_bridge.py:14: J93
autopsia/kb/icd10_bridge.py:15: I20
autopsia/kb/icd10_bridge.py:175: J93
autopsia/kb/icd10_bridge.py:176: A15
autopsia/kb/icd10_bridge.py:176: I20
autopsia/kb/icd10_bridge.py:200: J18
autopsia/kb/icd10_bridge.py:201: J17
autopsia/kb/icd10_bridge.py:20: A15.something
autopsia/kb/icd10_bridge.py:20: A15
autopsia/kb/namcs.py:178: V10
autopsia/kb/presentation_prior_crosswalk.py:17: K35
autopsia/kb/presentation_prior_crosswalk.py:23: G43
autopsia/kb/presentation_prior_crosswalk.py:24: G43
autopsia/kb/presentation_prior_crosswalk.py:97: D64
autopsia/kb/presentation_prior_crosswalk.py:98: R09
autopsia/kb/presentation_prior_crosswalk.py:99: R47
autopsia/vocab/ambiguous_terms.py:36: C0544691
autopsia/vocab/ambiguous_terms.py:37: C0544691
autopsia/vocab/ambiguous_terms.py:37: C1823998
autopsia/vocab/ambiguous_terms.py:39: C1823998
autopsia/vocab/ccsr_icd10cm.py:39: C0178238
autopsia/vocab/icd10.py:161: T58
autopsia/vocab/mkb10_ru.py:21: A00
autopsia/vocab/mkb10_ru.py:61: A00
autopsia/vocab/mkb10_ru.py:61: T58
autopsia/vocab/structure_classify.py:355: F03
autopsia/vocab/umls_definitions.py:19: C0000139
autopsia/vocab/umls_definitions.py:19: C0000248
autopsia/vocab/umls_definitions.py:19: C0000257
autopsia/vocab/umls_definitions.py:32: C0269825
autopsia/vocab/umls_definitions.py:38: C0000139
autopsia/vocab/umls_definitions.py:44: C0020538
autopsia/vocab/wikidata_mondo_p31.py:1: P31
tests/test_age_restricted_disclosure.py:15: K55
tests/test_ambulatory_presentation.py:174: D64
tests/test_ambulatory_presentation.py:174: R09
tests/test_ambulatory_presentation.py:175: M79
tests/test_cauda_equina_red_flags.py:93: K55
tests/test_cauda_equina_red_flags.py:94: N15
tests/test_counted_rows_and_identity.py:348: F03
tests/test_icd10_bridge.py:196: J17
tests/test_icd10_bridge.py:197: J18
tests/test_icd10_bridge.py:198: J17
tests/test_icd10_bridge.py:48: A15
tests/test_icd10_bridge.py:57: I20
tests/test_icd10_bridge.py:99: A15
tests/test_icpc2_crosswalk.py:103: E11
tests/test_icpc2_crosswalk.py:93: I20
tests/test_noema_data_quality_overlays.py:187: E11
tests/test_noema_data_quality_overlays.py:192: K60
tests/test_noema_data_quality_overlays.py:193: K60
tests/test_noema_data_quality_overlays.py:194: E11
tests/test_noema_data_quality_overlays.py:529: K55
tests/test_noema_data_quality_overlays.py:530: N15
tests/test_noema_data_quality_overlays.py:77: G43
tests/test_presentation_prior_crosswalk.py:105: G43
tests/test_presentation_prior_crosswalk.py:116: K35
tests/test_presentation_prior_crosswalk.py:117: G43
tests/test_presentation_prior_crosswalk.py:169: K55
tests/test_presentation_prior_crosswalk.py:171: N15
tests/test_presentation_prior_crosswalk.py:172: L60
tests/test_presentation_prior_crosswalk.py:48: K60
tests/test_presentation_prior_crosswalk.py:50: E11
tests/test_presentation_prior_crosswalk.py:50: K60
tests/test_presentation_prior_crosswalk.py:51: K60
tests/test_presentation_prior_crosswalk.py:53: E11
tests/test_presentation_prior_crosswalk.py:55: K80
## R3 errno constant (1)
tests/conftest.py:131: ENOENT
## R4 data file name (extension) (43)
autopsia/kb/namcs.py:145: namdict2019.dct
autopsia/kb/namcs.py:148: namdict2019.dct
autopsia/kb/namcs.py:17: namdict2019.dct
autopsia/kb/namcs.py:191: namdict2015.dct
autopsia/kb/namcs.py:211: namdict2016.dct
autopsia/kb/namcs.py:212: namdict2016.dct
autopsia/kb/namcs.py:232: namdict2018.dct
autopsia/kb/namcs.py:46: namdict2019.dct
autopsia/kb/nhamcs.py:15: eddict2019.dct
autopsia/kb/nhamcs.py:15: eddict2020.dct
autopsia/kb/nhamcs.py:15: eddict2021.dct
autopsia/kb/nhamcs.py:181: eddict2019.dct
autopsia/kb/nhamcs.py:181: eddict2020.dct
autopsia/kb/nhamcs.py:181: eddict2021.dct
autopsia/kb/phmrc_child_verbal_autopsy.py:19: IHME_PHMRC_VA_DATA_NEONATE_Y2013M09D11_1.dta
autopsia/kb/round28_finding_causes.py:17: MRSTY.RRF
autopsia/vocab/ambiguous_terms.py:123: AMBIGLUI.RRF
autopsia/vocab/ambiguous_terms.py:123: AMBIGSUI.RRF
autopsia/vocab/ambiguous_terms.py:1: AMBIGLUI.RRF
autopsia/vocab/ambiguous_terms.py:1: AMBIGSUI.RRF
autopsia/vocab/ambiguous_terms.py:36: AMBIGLUI.RRF
autopsia/vocab/ambiguous_terms.py:7: AMBIGLUI.RRF
autopsia/vocab/ccsr_icd10cm.py:108: MRMAP.RRF
autopsia/vocab/ccsr_icd10cm.py:168: MRMAP.RRF
autopsia/vocab/ccsr_icd10cm.py:1: MRMAP.RRF
autopsia/vocab/ccsr_icd10cm.py:22: MRMAP.RRF
autopsia/vocab/ccsr_icd10cm.py:22: MRSMAP.RRF
autopsia/vocab/loinc.py:11: MRSAT.RRF
autopsia/vocab/loinc_attributes.py:1: MRSAT.RRF
autopsia/vocab/loinc_attributes.py:2: MRSAT.RRF
autopsia/vocab/loinc_attributes.py:4: MRSAT.RRF
autopsia/vocab/loinc_attributes.py:8: MRSAT.RRF
autopsia/vocab/structure_classify.py:38: MRSTY.RRF
autopsia/vocab/umls_definitions.py:1: MRDEF.RRF
autopsia/vocab/umls_icpc_codes.py:17: MRMAP.RRF
autopsia/vocab/umls_install.py:140: MRCUI.RRF
autopsia/vocab/umls_install.py:16: MRCUI.RRF
autopsia/vocab/umls_install.py:47: MRCUI.RRF
autopsia/vocab/umls_install.py:47: MRSTY.RRF
autopsia/vocab/umls_source_metadata.py:1: MRSAB.RRF
tests/test_ccsr_icd10cm.py:1: MRMAP.RRF
tests/test_ccsr_icd10cm.py:25: MRMAP.RRF
tests/test_ccsr_icd10cm.py:4: MRMAP.RRF
## R5 one-character symbol (36)
autopsia/kb/ddxplus_hpo_crosswalk.py:143: B
autopsia/kb/ddxplus_hpo_crosswalk.py:14: B
autopsia/kb/ddxplus_hpo_crosswalk.py:21: B
autopsia/kb/ddxplus_hpo_crosswalk.py:25: B
autopsia/reason/eliminate_candidates.py:541: Z
autopsia/reason/eliminate_model.py:78: T
autopsia/reason/eliminate_model.py:79: T
autopsia/reason/eliminate_model.py:84: T
autopsia/reason/vectorised.py:262: H
autopsia/vocab/structure_classify.py:347: D
autopsia/vocab/structure_classify.py:350: B
autopsia/vocab/structure_classify.py:351: E
autopsia/vocab/umls_definitions.py:10: E
autopsia/vocab/umls_definitions.py:10: O
autopsia/vocab/umls_definitions.py:10: Y
autopsia/vocab/umls_definitions.py:12: O
autopsia/vocab/umls_definitions.py:12: Y
bench/burden_ranked_candidates.py:306: Z
tests/test_clinical_boundary_rescue.py:101: D
tests/test_clinical_boundary_rescue.py:101: T
tests/test_clinical_boundary_rescue.py:128: T
tests/test_clinical_boundary_rescue.py:36: B
tests/test_clinical_boundary_rescue.py:92: D
tests/test_clinical_boundary_rescue.py:92: T
tests/test_clinical_boundary_rescue.py:93: D
tests/test_clinical_boundary_rescue.py:93: T
tests/test_clinical_boundary_rescue.py:94: D
tests/test_clinical_boundary_rescue.py:94: T
tests/test_clinical_boundary_rescue.py:95: D
tests/test_clinical_boundary_rescue.py:95: T
tests/test_clinical_boundary_rescue.py:96: D
tests/test_clinical_boundary_rescue.py:96: T
tests/test_counted_rows_and_identity.py:317: D
tests/test_counted_rows_and_identity.py:346: B
tests/test_counted_rows_and_identity.py:346: D
tests/test_counted_rows_and_identity.py:347: E
## R6 name spelled out as a string in repo code (325)
autopsia/api_models_clinical.py:632: Bundle
autopsia/api_noema_observation_costs.py:61: _meta
autopsia/api_noema_workflow.py:531: Bundle
autopsia/api_noema_workflow.py:537: Condition
autopsia/api_noema_workflow.py:537: MedicationStatement
autopsia/api_risk_factors.py:155: _ru
autopsia/api_risk_factors.py:178: _ru
autopsia/graph_context.py:44: _kind_cache
autopsia/graph_types.py:684: _edge_position
autopsia/ingest/canonicalize.py:64: LBD
autopsia/ingest/fars.py:14: ATESTANTE
autopsia/ingest/fars.py:14: COMUNSVOIM
autopsia/ingest/fars.py:181: YEAR
autopsia/ingest/fars.py:184: DTOBITO
autopsia/ingest/fars.py:24: ATESTANTE
autopsia/ingest/fars.py:24: NECROPSIA
autopsia/ingest/fetch.py:339: ABSTRACT
autopsia/ingest/fetch.py:339: CASE
autopsia/ingest/fhir_import.py:157: Medication
autopsia/ingest/fhir_import.py:159: MedicationRequest
autopsia/ingest/fhir_import.py:159: MedicationStatement
autopsia/ingest/fhir_import.py:178: Medication
autopsia/ingest/fhir_import.py:219: Patient
autopsia/ingest/fhir_import.py:242: Bundle
autopsia/ingest/fhir_import.py:244: Condition
autopsia/ingest/fhir_import.py:244: MedicationRequest
autopsia/ingest/fhir_import.py:244: MedicationStatement
autopsia/ingest/fhir_import.py:253: Condition
autopsia/ingest/fhir_import.py:253: MedicationStatement
autopsia/ingest/fhir_import.py:255: Patient
autopsia/ingest/fhir_import.py:265: Patient
autopsia/ingest/fhir_import.py:27: Medication
autopsia/ingest/integrity.py:228: IndexingSourceName
autopsia/ingest/integrity.py:38: Reason
autopsia/ingest/integrity.py:448: PMC13187864
autopsia/ingest/integrity_sources.py:226: RetractionNature
autopsia/ingest/integrity_sources.py:241: RetractionNature
autopsia/ingest/integrity_sources.py:243: OriginalPaperDOI
autopsia/ingest/lexicon.py:609: Asphyxia
autopsia/ingest/pipeline.py:664: _meta
autopsia/ingest/pubmed_meta.py:278: _meta
autopsia/ingest/sim_brazil.py:11: ATESTANTE
autopsia/ingest/sim_brazil.py:131: ATESTANTE
autopsia/ingest/sim_brazil.py:13: CIRCOBITO
autopsia/ingest/sim_brazil.py:13: NECROPSIA
autopsia/ingest/sim_brazil.py:21: COMUNSVOIM
autopsia/ingest/sim_brazil.py:28: CIRCOBITO
autopsia/ingest/sim_brazil.py:35: NECROPSIA
autopsia/ingest/sim_brazil.py:387: DTOBITO
autopsia/ingest/sim_brazil.py:389: CAUSABAS
autopsia/ingest/sim_brazil.py:432: CAUSABAS
autopsia/ingest/sim_brazil.py:475: NECROPSIA
autopsia/ingest/sim_brazil.py:484: ATESTANTE
autopsia/ingest/sim_brazil.py:484: CAUSABAS
autopsia/ingest/sim_brazil.py:484: DTOBITO
autopsia/ingest/sim_brazil.py:484: NECROPSIA
autopsia/ingest/sim_brazil.py:485: COMUNSVOIM
autopsia/ingest/store.py:161: PMC13319664
autopsia/ingest/to_graph_frequencies.py:331: _meta
autopsia/ingest/to_graph_frequencies.py:334: _meta
autopsia/kb/build_all_overlays.py:250: PYTHONHASHSEED
autopsia/kb/build_all_overlays.py:259: PYTHONHASHSEED
autopsia/kb/build_all_overlays.py:61: PYTHONHASHSEED
autopsia/kb/cant_miss_missing_causes.py:46: DOID
autopsia/kb/cant_miss_severity.py:206: NOT
autopsia/kb/cant_miss_severity.py:68: _meta
autopsia/kb/cochrane/wiley_tdm.py:20: Location
autopsia/kb/cochrane/wiley_tdm.py:70: Location
autopsia/kb/curated_causal_chains.py:12: SAB
autopsia/kb/ddxplus.py:535: PATHOLOGY
autopsia/kb/display_name.py:106: NAFLD1
autopsia/kb/display_name.py:106: P1PK
autopsia/kb/display_name.py:106: SHEP11
autopsia/kb/display_name.py:27: CADDS
autopsia/kb/display_name.py:27: ESTRIDOR
autopsia/kb/display_name.py:27: NAFLD1
autopsia/kb/display_name.py:27: PANDAS
autopsia/kb/display_name.py:5: P1PK
autopsia/kb/display_name.py:5: SHEP11
autopsia/kb/famenet.py:125: Anemia
autopsia/kb/famenet.py:19: Other
autopsia/kb/famenet.py:20: Other
autopsia/kb/hcup_stt.py:118: National
autopsia/kb/i18n_shared.py:49: NFKD
autopsia/kb/icd10_bridge.py:126: HP
autopsia/kb/icd10_bridge.py:126: UBERON
autopsia/kb/icd10_bridge.py:127: CHEBI
autopsia/kb/icd10_bridge.py:127: GO
autopsia/kb/lay_synonym_generator.py:46: WARNING
autopsia/kb/mints/mondo_0005035_ganglioneuroblastoma.py:19: PrevalenceClass
autopsia/kb/mints/mondo_0016227_episodic_ataxia.py:18: PrevalenceClass
autopsia/kb/mints/mondo_0021783_streptococcal_pharyngitis.py:10: Hypertension
autopsia/kb/mondo_icd10.py:15: ICD10
autopsia/kb/namcs.py:19: YEAR
autopsia/kb/name_hygiene.py:20: P1PK
autopsia/kb/name_hygiene.py:20: SHEP11
autopsia/kb/openfda_bulk_labels.py:369: BLA
autopsia/kb/openfda_bulk_labels.py:369: NDA
autopsia/kb/phmrc_child_verbal_autopsy.py:11: Malaria
autopsia/kb/phmrc_forensic_drowning_crosswalk.py:13: Drowning
autopsia/kb/phmrc_forensic_drowning_crosswalk.py:1: Drowning
autopsia/kb/phmrc_verbal_autopsy.py:94: Drowning
autopsia/kb/phmrc_verbal_autopsy.py:94: Falls
autopsia/kb/pubmed_bulk/index.py:21: NlmUniqueID
autopsia/kb/pubmed_bulk/index.py:22: MedlineTA
autopsia/kb/pubmed_bulk/index.py:27: Humans
autopsia/kb/pubmed_bulk/index.py:446: AbstractText
autopsia/kb/pubmed_bulk/index.py:447: Label
autopsia/kb/pubmed_bulk/index.py:470: MedlineCitation
autopsia/kb/pubmed_bulk/index.py:611: DeleteCitation
autopsia/kb/symcat_overlay.py:296: PYTHONHASHSEED
autopsia/kb/symcat_overlay.py:300: PYTHONHASHSEED
autopsia/kb/track_b_llm_sourced.py:417: _study
autopsia/kb/wikidata_symptoms.py:42: P5270
autopsia/kb/wikidata_symptoms.py:5: P5270
autopsia/reason/attribute.py:66: Hanging
autopsia/reason/chains.py:553: _ru
autopsia/reason/clinical_caveats.py:432: _ru
autopsia/reason/clinical_caveats.py:87: _ru
autopsia/reason/clinical_kb.py:457: Headache
autopsia/reason/clinical_kb.py:458: Pain
autopsia/reason/clinical_kb_score_adjustments.py:209: Fatigue
autopsia/reason/clinical_kb_score_adjustments.py:209: Pain
autopsia/reason/clinical_kb_score_adjustments.py:249: Dyspnea
autopsia/reason/clinical_kb_subgraph.py:126: PYTHONHASHSEED
autopsia/reason/clinical_support_views.py:831: _ru
autopsia/reason/enumerate.py:343: PYTHONHASHSEED
autopsia/reason/export.py:56: _ru
autopsia/reason/localize.py:606: _ru
autopsia/reason/patient_view.py:128: Pneumonia
autopsia/reason/renal_clearance.py:51: _meta
autopsia/reason/worksheet_cant_miss.py:171: PYTHONHASHSEED
autopsia/reason/worksheet_cant_miss.py:409: PYTHONHASHSEED
autopsia/report_clinical.py:357: Melena
autopsia/vocab/babelon.py:56: OFFICIAL
autopsia/vocab/ccsr_icd10cm.py:25: DIG001
autopsia/vocab/ccsr_icd10cm.py:39: DIG001
autopsia/vocab/expand.py:4: Suffocation
autopsia/vocab/expand.py:5: Asphyxia
autopsia/vocab/expand.py:98: Asphyxia
autopsia/vocab/local_install.py:441: RegistryNumber
autopsia/vocab/local_install.py:451: Frequency
autopsia/vocab/local_install.py:454: PreviousIndexing
autopsia/vocab/loinc_ru.py:10: DN
autopsia/vocab/loinc_ru.py:10: LC
autopsia/vocab/loinc_ru.py:20: LN
autopsia/vocab/loinc_ru.py:7: LN
autopsia/vocab/structure.py:16: HeadingMappedTo
autopsia/vocab/structure.py:566: UMLS
autopsia/vocab/structure.py:567: MESH
autopsia/vocab/structure.py:643: SCTID
autopsia/vocab/structure.py:724: HeadingMappedTo
autopsia/vocab/structure.py:734: PharmacologicalAction
autopsia/vocab/structure_umls_bridge.py:14: SCTID
autopsia/vocab/umls_definitions.py:26: HPO
autopsia/vocab/umls_definitions.py:26: MSH
autopsia/vocab/umls_definitions.py:26: NCI
autopsia/vocab/umls_definitions.py:37: NCI
autopsia/vocab/umls_definitions.py:37: PDQ
autopsia/vocab/umls_definitions.py:38: FMA
autopsia/vocab/umls_definitions.py:38: GO
autopsia/vocab/umls_definitions.py:38: UWDA
autopsia/vocab/umls_definitions.py:43: CHV
autopsia/vocab/umls_definitions.py:43: MEDLINEPLUS
autopsia/vocab/umls_icpc_codes.py:55: ICPC2EENG
autopsia/vocab/umls_rxcui_map.py:48: RXNORM
autopsia/vocab/umls_source_metadata.py:17: SRL
autopsia/vocab/umls_source_metadata.py:20: SRL
autopsia/vocab/umls_source_metadata.py:27: CPT
autopsia/vocab/umls_source_metadata.py:32: SAB
autopsia/vocab/umls_source_metadata.py:33: SRL
autopsia/vocab/umls_source_metadata.py:34: CPT
autopsia/vocab/umls_source_metadata.py:47: SAB
autopsia/vocab/wikidata_hpo_labels.py:10: P3841
autopsia/vocab/wikidata_hpo_labels.py:1: P3841
autopsia/vocab/wikidata_hpo_labels.py:55: P3841
autopsia/vocab/wikidata_mondo_labels.py:1: P5270
autopsia/vocab/wikidata_mondo_labels.py:83: P5270
autopsia/vocab/wikidata_mondo_p31.py:9: P5270
bench/audit_benchmarks.py:219: Asphyxia
bench/author_gold.py:824: PMC13350245
bench/candidate_probe.py:963: Exposure
bench/cms_pfs_fetch.py:179: TOTAL
bench/cms_pfs_fetch.py:19: RATE
bench/conditioned_prior_audit.py:77: _given_
bench/ddxplus_discriminative.py:112: MultiLogloss
bench/ddxplus_discriminative.py:95: PATHOLOGY
bench/ddxplus_fork_measurement.py:194: PATHOLOGY
bench/eval_extraction.py:423: PMC13275216
bench/fix_gold.py:39: _delete_
bench/gbd_results_bulk_fetch.py:30: Global
bench/gbd_results_bulk_fetch.py:30: Rate
bench/lay_synonym_audit_full.py:72: __
bench/narrow_population_prior_audit.py:120: _study
bench/pool_gold.py:28: PMC13275216
bench/ru_gender_agreement_audit.py:104: Gender
bench/ru_gender_agreement_audit.py:10: Gender
bench/structured_prior_allowlist.py:31: REPORTED
bench/structured_prior_allowlist.py:60: REPORTED
bench/structured_prior_sources.py:103: NumericValue
bench/structured_prior_sources.py:103: Value
bench/structured_prior_sources.py:109: NumericValue
bench/structured_prior_sources.py:110: Value
bench/structured_prior_sources.py:111: Value
bench/structured_prior_sources.py:135: High
bench/structured_prior_sources.py:135: Low
bench/structured_prior_sources.py:135: Value
bench/structured_prior_sources.py:21: NumericValue
bench/structured_prior_sources.py:21: Value
bench/structured_prior_sources.py:321: HKG
bench/structured_prior_sources.py:321: MAC
bench/structured_prior_sources.py:321: PRI
bench/structured_prior_sources.py:91: Value
bench/tie_band_determinism_probe.py:17: PYTHONHASHSEED
bench/tie_band_determinism_probe.py:1: PYTHONHASHSEED
bench/tie_band_determinism_probe.py:5: PYTHONHASHSEED
bench/track_a_sourcing_pilot.py:943: CAUSES
bench/track_b_bycatch_prompt.py:589: _after_edge_trim
bench/track_b_bycatch_sweep.py:168: PMC123
bench/track_b_bycatch_sweep.py:172: PMC1PMC2
bench/track_b_fetch_cache.py:92: _schema_version
bench/track_b_resolver_calibration.py:155: HP
bench/track_b_sourcing_pilot.py:318: BROAD
bench/track_b_sourcing_pilot.py:318: NARROW
evidence_core/beta.py:152: TREATS
evidence_core/beta.py:182: TREATS
tests/test_api_clinical.py:230: Chorea
tests/test_api_clinical.py:231: Dementia
tests/test_api_clinical.py:232: Chorea
tests/test_api_clinical.py:233: Depression
tests/test_api_fhir_import.py:152: Condition
tests/test_api_fhir_import.py:93: Encounter
tests/test_api_risk_factors.py:63: Gibraltar
tests/test_api_risk_factors.py:63: Macao
tests/test_api_view_schemas.py:64: Pain
tests/test_cant_miss_questions_are_rendered.py:4: Melena
tests/test_caveats_state_what_the_code_did.py:113: _ru
tests/test_caveats_state_what_the_code_did.py:241: _ru
tests/test_clinical_boundary_rescue.py:128: D2
tests/test_clinical_conformal.py:93: F2
tests/test_cms_pfs_fetch.py:36: TOTAL
tests/test_ddxplus_concepts.py:254: Dysarthria
tests/test_ddxplus_concepts.py:255: Diplopia
tests/test_defaulted_prior_ids.py:20: COMMON
tests/test_defaulted_prior_ids.py:21: RARE
tests/test_defaulted_prior_ids.py:23: COMMON
tests/test_defaulted_prior_ids.py:23: RARE
tests/test_diagnosis_code_widening.py:48: DIG001
tests/test_discard_preservation.py:238: PMC13319664
tests/test_fhir_import.py:158: Patient
tests/test_golden_set_diagnostic_accuracy.py:1036: Polyphagia
tests/test_golden_set_diagnostic_accuracy.py:1062: Hypertension
tests/test_golden_set_diagnostic_accuracy.py:1070: Hypertension
tests/test_golden_set_diagnostic_accuracy.py:1144: Dyspnea
tests/test_golden_set_diagnostic_accuracy.py:1180: Pain
tests/test_golden_set_diagnostic_accuracy.py:1181: Neoplasm
tests/test_golden_set_diagnostic_accuracy.py:1310: Pain
tests/test_golden_set_diagnostic_accuracy.py:1319: Pain
tests/test_golden_set_diagnostic_accuracy.py:1332: Pain
tests/test_golden_set_diagnostic_accuracy.py:200: Pruritus
tests/test_golden_set_diagnostic_accuracy.py:2075: Headache
tests/test_golden_set_diagnostic_accuracy.py:2248: PYTHONHASHSEED
tests/test_golden_set_diagnostic_accuracy.py:2359: Fever
tests/test_golden_set_diagnostic_accuracy.py:2410: Cough
tests/test_golden_set_diagnostic_accuracy.py:2410: Fatigue
tests/test_golden_set_diagnostic_accuracy.py:289: Arthritis
tests/test_golden_set_diagnostic_accuracy.py:290: Proteinuria
tests/test_golden_set_diagnostic_accuracy.py:340: Fever
tests/test_golden_set_diagnostic_accuracy.py:384: Hyperpigmentation
tests/test_golden_set_diagnostic_accuracy.py:543: Anemia
tests/test_golden_set_diagnostic_accuracy.py:544: Anemia
tests/test_golden_set_diagnostic_accuracy.py:628: Petechiae
tests/test_golden_set_diagnostic_accuracy.py:738: Hyperpigmentation
tests/test_golden_set_diagnostic_accuracy.py:738: Xanthelasma
tests/test_golden_set_diagnostic_accuracy.py:791: Cirrhosis
tests/test_golden_set_diagnostic_accuracy.py:951: Fatigue
tests/test_golden_set_diagnostic_accuracy.py:982: Dyspnea
tests/test_lay_synonym_score_candidates_language_routing.py:44: _translate_to_english
tests/test_marginal_rate_noisy_or.py:132: Seizure
tests/test_marginal_rate_noisy_or.py:133: Hypotonia
tests/test_marginal_rate_noisy_or.py:6: Fever
tests/test_meta/test_base_seed_matches_its_builder.py:40: _meta
tests/test_meta/test_display_name_casing.py:132: P1PK
tests/test_meta/test_display_name_casing.py:132: SHEP11
tests/test_meta/test_display_name_casing.py:15: P1PK
tests/test_meta/test_ru_companion_for_free_text_slots.py:59: _ru
tests/test_meta/test_ru_companion_for_free_text_slots.py:6: _ru
tests/test_meta/test_shared_library_boundary.py:87: AUTOPSIA
tests/test_noema_data_quality_hpoa_ddxplus.py:251: I219
tests/test_noema_data_quality_hpoa_ddxplus.py:251: J450
tests/test_noema_data_quality_overlays.py:39: Prevalence
tests/test_noema_kb.py:752: Other
tests/test_noema_label_casing_wiring.py:42: P1PK
tests/test_noema_label_casing_wiring.py:99: P1PK
tests/test_noema_scenarios_common.py:616: PYTHONHASHSEED
tests/test_noema_scenarios_common.py:726: Anxiety
tests/test_noema_scenarios_common.py:729: Anxiety
tests/test_noema_scenarios_common.py:849: Fatigue
tests/test_noema_scenarios_common.py:947: Hypertension
tests/test_noema_scenarios_common.py:952: Arrhythmia
tests/test_noema_scenarios_common.py:953: Arrhythmia
tests/test_noema_worksheet_voi_property.py:381: Fever
tests/test_open_items.py:78: LBD
tests/test_renal_clearance.py:83: _meta
tests/test_require_complete_clinical_overlay_build_fixture.py:189: PYTHONPATH
tests/test_residual.py:439: PYTHONHASHSEED
tests/test_screening_descendant_match.py:110: Arrhythmia
tests/test_screening_leak_noisy_or.py:58: Headache
tests/test_screening_leak_noisy_or.py:58: Pain
tests/test_screening_leak_noisy_or.py:9: Headache
tests/test_screening_leak_noisy_or.py:9: Pain
tests/test_sim_brazil.py:263: ATESTANTE
tests/test_structure_xrefs.py:4: SCTID
tests/test_structured_prior_sources.py:13: Value
tests/test_structured_prior_sources.py:35: Value
tests/test_structured_prior_sources.py:60: Value
tests/test_testsheet.py:301: _meta
tests/test_track_b_count_basis_invariants.py:451: Priapism
tests/test_track_b_fetch_cache.py:111: _schema_version
tests/test_track_b_id_mixup_guard.py:92: Anorexia
tests/test_track_b_id_mixup_guard.py:92: Priapism
tests/test_transitive_causal_chains.py:516: Orthopnea
tests/test_vocab.py:427: Asphyxia
tests/test_vocab.py:42: Asphyxia
tests/test_worksheet.py:757: PYTHONHASHSEED
## R7 bare self/cls attribute declared in repo (4)
autopsia/kb/cochrane/wiley_tdm.py:104: _min_interval
tests/test_cochrane_wiley_tdm.py:115: _last_request_at
tests/test_cochrane_wiley_tdm.py:117: _last_request_at
tests/test_cochrane_wiley_tdm.py:118: _last_request_at
## R8 table.column spelled in repo SQL string (32)
autopsia/ingest/extract_verification.py:180: treats_evidence.magnitude
autopsia/kb/bacterial_tracheitis.py:15: doid.obo
autopsia/kb/bacterial_tracheitis.py:18: doid.obo
autopsia/kb/cant_miss_missing_causes.py:26: doid.obo
autopsia/kb/cant_miss_missing_causes.py:36: doid.obo
autopsia/kb/cant_miss_missing_causes.py:502: doid.obo
autopsia/kb/cant_miss_missing_causes.py:8: doid.obo
autopsia/kb/hpo_annotation_gaps_msk_gu.py:88: doid.obo
autopsia/kb/mints/mondo_0001384_myopia.py:40: prior_population.source
autopsia/kb/mints/mondo_0003004_macular_degeneration.py:40: prior_population.source
autopsia/kb/mints/mondo_0004872_hemorrhoids.py:35: prior_population.source
autopsia/kb/mints/mondo_0005602_ovarian_teratoma.py:30: prior_population.source
autopsia/kb/mints/mondo_0007150_corneal_arcus.py:18: prior_population.source
autopsia/kb/mints/mondo_0007743_adhd.py:39: prior_population.source
autopsia/kb/mints/mondo_0011284_astigmatism.py:36: prior_population.source
autopsia/kb/mints/mondo_0011438_acne.py:29: prior_population.source
autopsia/kb/mints/mondo_0013600_insomnia.py:37: prior_population.source
autopsia/kb/mints/mondo_0017080_occipital_encephalocele.py:21: prior_population.source
autopsia/kb/mints/mondo_0700322_tinnitus.py:35: prior_population.source
autopsia/kb/nhamcs.py:211: ed2019.do
autopsia/kb/nhamcs.py:244: ed2019.do
autopsia/kb/openfda_bulk_labels.py:490: drugs.canonical
autopsia/kb/pubmed_bulk/index.py:127: fts_backfill_progress.last_rowid
autopsia/vocab/structure.py:916: concept_field.field
bench/candidate_probe.py:997: scispacy.linking
bench/cms_clfs_fetch.py:19: cms.gov
tests/hpo_annotation_gaps_mints/test_mints_02.py:186: prior_population.source
tests/hpo_annotation_gaps_mints/test_mints_05.py:385: prior_population.source
tests/hpo_annotation_gaps_mints/test_mints_21.py:263: prior_population.source
tests/test_cant_miss_severity.py:211: doid.obo
tests/test_dbc_crosscheck.py:9: DOAC2023.dbc
tests/test_renal_clearance.py:107: _meta.why
