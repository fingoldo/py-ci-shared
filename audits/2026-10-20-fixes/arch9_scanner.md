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
| after (this branch, after R6 narrowing) | 494 |
| removed | 407 (all attributed, below) |
| added | 0 |

## False-positive classes fixed

| Rule | Class | Removed | Mechanism and unit test |
|---|---|---|---|
| R1 | Python protocol names (`__file__`, `__dict__`, `__main__`, `__pycache__`) | 26 | Fixed set built from `dir()` of object/type/module/function types plus three runtime names. Any other dunder still fires (`test_python_protocol_dunder_names_are_not_repo_names`). |
| R2 | Standard vocabulary codes (ICD-10 `E11`, `A15-A19`; UMLS `C0020538`; Cochrane `CD000097.pub2`) | 77 | Shape regex. Test `test_standard_vocabulary_codes_are_not_repo_names`. |
| R3 | OS errno constants (`ENOENT`) | 1 | `hasattr(errno, name)`. Test `test_os_error_constants_are_not_repo_names`. |
| R4 | External data file names with extensions not in the list (`.dct`, `.RRF`, `.rrf`, `.dta`, `.parquet`, ...) | 43 | Extended `_FILE_EXT_RE`. Test `test_external_data_file_names_...`. |
| R5 | One-character symbols and placeholders (`Z`, `B`, `T`, `X`) | 36 | Single-character bare names with no call. Test `test_a_one_character_symbol_is_not_a_repo_name`. |
| R6 | Name is a DEFINITION-SITE string constant in repo code: dict key, subscript-store key, value assigned to a name, or element of an assigned list/tuple/set/dict | 188 | Exact match against definition-site string constants only (`_definition_site_ids`); comparison, call-argument and return uses do not suppress. Test `test_a_name_the_repo_spells_out_as_a_string_is_a_data_name`. |
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

- R6 was narrowed after the coordinator's review (see the sample section). The first version suppressed any string use and hid
  real phantoms. The narrowed version drops 137 of those suppressions; on a 40-item sample 14 of the 19 re-reported items are
  real terms the code uses at a use site (a comparison, a column read), not phantoms. That is a known cost, not fixed here.
- R8 accepts a `table.column` pair if one SQL string contains both words, so a wrong column name in a comment is silent when the table and
  the column both occur in some string. Weaker than an exact schema check; noted for review.

## R6 sample: 40 removals checked against the code (stratified across 139 files)

Sample: 40 of the 325 R6 removals from the first version, drawn round-robin across files (`scratchpad/r6_sample.txt`).
"Now" is the outcome after narrowing to definition sites. Verdicts:
- **CORRECT**: the string is a definition site in production code (a key, a table, an assigned name): the name refers to a real constant.
- **WEAK**: the only definition site is in a test file.
- **REAL TERM**: a real term the code handles at a use site (comparison, column read, call argument). Not a phantom; narrowing re-reports it.
- **GENUINE PHANTOM**: names a concrete item that no production code defines, and only test fixtures or nothing spell it.
- **UNCERTAIN**: test-only evidence; the comment could go either way without the data.

| # | File:line | Name | Now | Verdict | Reason (evidence) |
|---|---|---|---|---|---|
| 1 | autopsia/api_models_clinical.py:632 | `Bundle` | reported | REAL TERM | FHIR R4 resource type; only test fixtures spell it. |
| 2 | autopsia/api_noema_observation_costs.py:61 | `_meta` | suppressed | CORRECT | Record key `_meta` used as a dict key. |
| 3 | autopsia/api_noema_workflow.py:537 | `Condition` | reported | REAL TERM | FHIR resource; compared at `ingest/fhir_import.py:284`. |
| 4 | autopsia/api_risk_factors.py:178 | `_ru` | reported | REAL TERM | Field-suffix convention; the literal builds names at `reason/caveat_templates.py:706`. |
| 5 | autopsia/graph_context.py:44 | `_kind_cache` | suppressed | CORRECT | Set through a `__dict__` subscript store, a real attribute. |
| 6 | autopsia/graph_types.py:684 | `_edge_position` | suppressed | CORRECT | Element of an assigned tuple at `graph_types.py:252`. |
| 7 | autopsia/ingest/canonicalize.py:64 | `LBD` | reported | GENUINE PHANTOM | Abbreviation claim; only test call arguments spell it; no production table defines it. |
| 8 | autopsia/ingest/fars.py:184 | `DTOBITO` | suppressed | CORRECT | DBF column key at `ingest/sim_brazil.py:139`. |
| 9 | autopsia/ingest/fetch.py:339 | `CASE` | suppressed | CORRECT | Section name in an assigned tuple at `fetch.py:38`. |
| 10 | autopsia/ingest/fhir_import.py:178 | `Medication` | reported | REAL TERM | FHIR resource; compared at `fhir_import.py:168`. |
| 11 | autopsia/ingest/integrity.py:38 | `Reason` | reported | REAL TERM | Retraction Watch CSV column, read at `integrity_sources.py:287`. |
| 12 | autopsia/ingest/integrity_sources.py:226 | `RetractionNature` | reported | REAL TERM | CSV column, read at `integrity_sources.py:275`. |
| 13 | autopsia/ingest/lexicon.py:609 | `Asphyxia` | suppressed | CORRECT | MeSH descriptor in a table literal (`lexicon.py:50`). |
| 14 | autopsia/ingest/pipeline.py:664 | `_meta` | suppressed | CORRECT | Record key. |
| 15 | autopsia/ingest/pubmed_meta.py:278 | `_meta` | suppressed | CORRECT | Record key. |
| 16 | autopsia/ingest/sim_brazil.py:484 | `NECROPSIA` | suppressed | CORRECT | DBF column key at `sim_brazil.py:140`. |
| 17 | autopsia/ingest/store.py:161 | `PMC13319664` | reported | GENUINE PHANTOM | A record id claimed to be on disk; only test fixtures spell it; no code defines it. |
| 18 | autopsia/ingest/to_graph_frequencies.py:334 | `_meta` | suppressed | CORRECT | Record key. |
| 19 | autopsia/kb/build_all_overlays.py:250 | `PYTHONHASHSEED` | suppressed | CORRECT | `os.environ` subscript key at `build_all_overlays.py:302`. |
| 20 | autopsia/kb/cant_miss_missing_causes.py:46 | `DOID` | suppressed | CORRECT | ID-space key at `vocab/local_install.py:492`. |
| 21 | autopsia/kb/cant_miss_severity.py:206 | `NOT` | reported | REAL TERM | Qualifier value compared at `cant_miss_severity.py:232`. |
| 22 | autopsia/kb/cochrane/wiley_tdm.py:70 | `Location` | suppressed | WEAK | HTTP header name; the only dict key is in a test fixture (`test_attack_surface_net.py:116`). |
| 23 | autopsia/kb/curated_causal_chains.py:12 | `SAB` | suppressed | WEAK | UMLS source column; the only definition is a test table (`test_umls_history.py:19`). |
| 24 | autopsia/kb/ddxplus.py:535 | `PATHOLOGY` | suppressed | WEAK | Only definition sites are test dict keys (`test_clinical_conformal.py:59`). |
| 25 | autopsia/kb/display_name.py:27 | `NAFLD1` | reported | REAL TERM | A shipped label; only the casing test spells it, at a use site. |
| 26 | autopsia/kb/famenet.py:19 | `Other` | reported | GENUINE PHANTOM | Names a row the comment says does not exist ("There is no separate `Other` row"); only test keyword args spell it. |
| 27 | autopsia/kb/hcup_stt.py:118 | `National` | reported | REAL TERM | Sheet tab name passed as an argument at `hcup_stt.py:117`. |
| 28 | autopsia/kb/i18n_shared.py:49 | `NFKD` | reported | REAL TERM | `unicodedata.normalize` form; a call argument at `i18n_shared.py:61`. |
| 29 | autopsia/kb/icd10_bridge.py:126 | `UBERON` | suppressed | CORRECT | Vocabulary key at `vocab/local_install.py:486`. |
| 30 | autopsia/kb/lay_synonym_generator.py:46 | `WARNING` | reported | UNCERTAIN | Status word; only test call arguments spell it. |
| 31 | autopsia/kb/mints/mondo_0005035_ganglioneuroblastoma.py:19 | `PrevalenceClass` | reported | REAL TERM | Prevalence class string passed at `kb/prevalence.py:112`. |
| 32 | autopsia/kb/mints/mondo_0016227_episodic_ataxia.py:18 | `PrevalenceClass` | reported | REAL TERM | Same as 31. |
| 33 | autopsia/kb/mints/mondo_0021783_streptococcal_pharyngitis.py:10 | `Hypertension` | suppressed | CORRECT | Dict value in the NHAMCS table at `kb/nhamcs.py:248`. |
| 34 | autopsia/kb/mondo_icd10.py:15 | `ICD10` | suppressed | WEAK | Xref prefix; the only table is a test (`test_ingest_table_merge_preservation.py:130`). |
| 35 | autopsia/kb/namcs.py:19 | `YEAR` | suppressed | CORRECT | Column in an assigned table at `ingest/fars.py:153`. |
| 36 | autopsia/kb/name_hygiene.py:20 | `P1PK` | reported | REAL TERM | Shipped label; the casing test spells it at a use site. |
| 37 | autopsia/kb/openfda_bulk_labels.py:369 | `NDA` | reported | REAL TERM | Submission-type value passed at `openfda_bulk_labels.py:442`. |
| 38 | autopsia/kb/phmrc_child_verbal_autopsy.py:11 | `Malaria` | suppressed | WEAK | Cause label; only test tables define it. |
| 39 | autopsia/kb/phmrc_forensic_drowning_crosswalk.py:1 | `Drowning` | suppressed | CORRECT | Assignment at `phmrc_forensic_drowning_crosswalk.py:46`. |
| 40 | autopsia/kb/phmrc_verbal_autopsy.py:94 | `Falls` | reported | UNCERTAIN | Cause label; only test tables spell it at use sites. |

### Tally

| Verdict | Count |
|---|---|
| CORRECT (definition site in production) | 16 |
| WEAK (definition only in a test) | 5 |
| REAL TERM (narrowing re-reports a real term) | 14 |
| GENUINE PHANTOM | 3 (rows 7, 17, 26: the names LBD, PMC13319664, Other) |
| UNCERTAIN | 2 (rows 30, 40: the names WARNING, Falls) |

Total 16 + 5 + 14 + 3 + 2 = 40. The UNCERTAIN rows are not counted as phantoms.

### Result

- Genuine phantoms found in the sample: **3 of 40** (7.5%). Extrapolated to the 325 first-version removals, roughly 24 were
  real phantoms hidden by the broad rule. Narrowing restores them.
- The narrowing re-reports 137 findings. In the sample, 14 of the 19 re-reported items are real terms, so most of the restored
  findings are false positives. That is a regression on the consumer tree, not a fix.
- Option for the coordinator: keep the narrow rule and accept the 137 restored findings as baseline debt, or add a use-site rule
  for comparisons and column reads, which would hide some genuine phantoms again. Not decided here.

## Full removed list, by rule (after R6 narrowing)

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
## R6 name is a definition-site string in repo code (188)
autopsia/api_noema_observation_costs.py:61: _meta
autopsia/graph_context.py:44: _kind_cache
autopsia/graph_types.py:684: _edge_position
autopsia/ingest/fars.py:14: ATESTANTE
autopsia/ingest/fars.py:14: COMUNSVOIM
autopsia/ingest/fars.py:181: YEAR
autopsia/ingest/fars.py:184: DTOBITO
autopsia/ingest/fars.py:24: ATESTANTE
autopsia/ingest/fars.py:24: NECROPSIA
autopsia/ingest/fetch.py:339: ABSTRACT
autopsia/ingest/fetch.py:339: CASE
autopsia/ingest/integrity.py:448: PMC13187864
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
autopsia/ingest/to_graph_frequencies.py:331: _meta
autopsia/ingest/to_graph_frequencies.py:334: _meta
autopsia/kb/build_all_overlays.py:250: PYTHONHASHSEED
autopsia/kb/build_all_overlays.py:259: PYTHONHASHSEED
autopsia/kb/build_all_overlays.py:61: PYTHONHASHSEED
autopsia/kb/cant_miss_missing_causes.py:46: DOID
autopsia/kb/cant_miss_severity.py:68: _meta
autopsia/kb/cochrane/wiley_tdm.py:20: Location
autopsia/kb/cochrane/wiley_tdm.py:70: Location
autopsia/kb/curated_causal_chains.py:12: SAB
autopsia/kb/ddxplus.py:535: PATHOLOGY
autopsia/kb/icd10_bridge.py:126: HP
autopsia/kb/icd10_bridge.py:126: UBERON
autopsia/kb/icd10_bridge.py:127: CHEBI
autopsia/kb/icd10_bridge.py:127: GO
autopsia/kb/mints/mondo_0021783_streptococcal_pharyngitis.py:10: Hypertension
autopsia/kb/mondo_icd10.py:15: ICD10
autopsia/kb/namcs.py:19: YEAR
autopsia/kb/phmrc_child_verbal_autopsy.py:11: Malaria
autopsia/kb/phmrc_forensic_drowning_crosswalk.py:13: Drowning
autopsia/kb/phmrc_forensic_drowning_crosswalk.py:1: Drowning
autopsia/kb/phmrc_verbal_autopsy.py:94: Drowning
autopsia/kb/symcat_overlay.py:296: PYTHONHASHSEED
autopsia/kb/symcat_overlay.py:300: PYTHONHASHSEED
autopsia/reason/clinical_kb.py:457: Headache
autopsia/reason/clinical_kb.py:458: Pain
autopsia/reason/clinical_kb_score_adjustments.py:209: Fatigue
autopsia/reason/clinical_kb_score_adjustments.py:209: Pain
autopsia/reason/clinical_kb_score_adjustments.py:249: Dyspnea
autopsia/reason/clinical_kb_subgraph.py:126: PYTHONHASHSEED
autopsia/reason/enumerate.py:343: PYTHONHASHSEED
autopsia/reason/patient_view.py:128: Pneumonia
autopsia/reason/renal_clearance.py:51: _meta
autopsia/reason/worksheet_cant_miss.py:171: PYTHONHASHSEED
autopsia/reason/worksheet_cant_miss.py:409: PYTHONHASHSEED
autopsia/report_clinical.py:357: Melena
autopsia/vocab/expand.py:5: Asphyxia
autopsia/vocab/expand.py:98: Asphyxia
autopsia/vocab/loinc_ru.py:10: DN
autopsia/vocab/loinc_ru.py:10: LC
autopsia/vocab/loinc_ru.py:20: LN
autopsia/vocab/loinc_ru.py:7: LN
autopsia/vocab/structure.py:566: UMLS
autopsia/vocab/structure.py:567: MESH
autopsia/vocab/structure.py:643: SCTID
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
bench/audit_benchmarks.py:219: Asphyxia
bench/author_gold.py:824: PMC13350245
bench/candidate_probe.py:963: Exposure
bench/conditioned_prior_audit.py:77: _given_
bench/ddxplus_discriminative.py:95: PATHOLOGY
bench/ddxplus_fork_measurement.py:194: PATHOLOGY
bench/eval_extraction.py:423: PMC13275216
bench/fix_gold.py:39: _delete_
bench/gbd_results_bulk_fetch.py:30: Rate
bench/pool_gold.py:28: PMC13275216
bench/structured_prior_sources.py:103: Value
bench/structured_prior_sources.py:110: Value
bench/structured_prior_sources.py:111: Value
bench/structured_prior_sources.py:135: Value
bench/structured_prior_sources.py:21: Value
bench/structured_prior_sources.py:321: HKG
bench/structured_prior_sources.py:321: MAC
bench/structured_prior_sources.py:321: PRI
bench/structured_prior_sources.py:91: Value
bench/tie_band_determinism_probe.py:17: PYTHONHASHSEED
bench/tie_band_determinism_probe.py:1: PYTHONHASHSEED
bench/tie_band_determinism_probe.py:5: PYTHONHASHSEED
bench/track_a_sourcing_pilot.py:943: CAUSES
bench/track_b_fetch_cache.py:92: _schema_version
bench/track_b_resolver_calibration.py:155: HP
bench/track_b_sourcing_pilot.py:318: BROAD
bench/track_b_sourcing_pilot.py:318: NARROW
tests/test_api_clinical.py:233: Depression
tests/test_api_risk_factors.py:63: Gibraltar
tests/test_api_risk_factors.py:63: Macao
tests/test_api_view_schemas.py:64: Pain
tests/test_cant_miss_questions_are_rendered.py:4: Melena
tests/test_clinical_boundary_rescue.py:128: D2
tests/test_clinical_conformal.py:93: F2
tests/test_ddxplus_concepts.py:255: Diplopia
tests/test_defaulted_prior_ids.py:20: COMMON
tests/test_defaulted_prior_ids.py:21: RARE
tests/test_defaulted_prior_ids.py:23: COMMON
tests/test_defaulted_prior_ids.py:23: RARE
tests/test_golden_set_diagnostic_accuracy.py:1036: Polyphagia
tests/test_golden_set_diagnostic_accuracy.py:1062: Hypertension
tests/test_golden_set_diagnostic_accuracy.py:1070: Hypertension
tests/test_golden_set_diagnostic_accuracy.py:1144: Dyspnea
tests/test_golden_set_diagnostic_accuracy.py:1180: Pain
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
tests/test_golden_set_diagnostic_accuracy.py:628: Petechiae
tests/test_golden_set_diagnostic_accuracy.py:951: Fatigue
tests/test_golden_set_diagnostic_accuracy.py:982: Dyspnea
tests/test_marginal_rate_noisy_or.py:132: Seizure
tests/test_marginal_rate_noisy_or.py:6: Fever
tests/test_meta/test_base_seed_matches_its_builder.py:40: _meta
tests/test_meta/test_shared_library_boundary.py:87: AUTOPSIA
tests/test_noema_scenarios_common.py:616: PYTHONHASHSEED
tests/test_noema_scenarios_common.py:726: Anxiety
tests/test_noema_scenarios_common.py:729: Anxiety
tests/test_noema_scenarios_common.py:849: Fatigue
tests/test_noema_scenarios_common.py:947: Hypertension
tests/test_noema_scenarios_common.py:952: Arrhythmia
tests/test_noema_scenarios_common.py:953: Arrhythmia
tests/test_noema_worksheet_voi_property.py:381: Fever
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
