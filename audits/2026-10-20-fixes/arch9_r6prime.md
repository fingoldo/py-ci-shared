# arch9 R6': a string a production module spells out is a data name

Branch `fix/phantom-scanner-fp`, scanner `src/py_ci_shared/phantom_code_references.py`.

## Rule change

Before: `_data_name_index` marked a string constant as a data name only at a definition site (dict key, subscript-store key,
string assigned to a name, table literal). A comparison, call argument, column read or other use did not count.

After (R6'): a string is a data name when a NON-TEST production `.py` module uses it as a string literal in any role.
Docstrings are excluded. Test modules (`test_*.py`, `*_test.py`, or a `tests` path part) never contribute.
The table-dot-column word index is unchanged (all constants, tests included), as in the prototype.

Code: `_definition_site_ids` and `_strings_defined_by` are removed (no caller left). `_is_test_file` and
`_docstring_ids` are new helpers; `_data_name_index` is split so each function stays small.

## Step 1: the 91 newly suppressed findings

Computed as `comm` of the current (HEAD 2a5815b) consumer list and the prototype list on `wt_round` (autopsia origin/master):
91 findings, covering 50 distinct names. For each name a production (non-test, non-docstring) string-literal site was found
by AST walk. All 50 have one, so every one is a REAL TERM: the repo spells the value out in production code, and the comment
names something the repo handles. None is a PHANTOM under the R6' definition (a name the repo does not define in any role).

| Name | Findings | Comment site(s) (consumer) | Production literal | Verdict |
|---|---|---|---|---|
| AbstractText | 1 | autopsia/kb/pubmed_bulk/index.py:446 | autopsia/kb/pubmed_bulk/index.py:451 | REAL TERM |
| Anemia | 3 | autopsia/kb/famenet.py:125; tests/test_golden_set_diagnostic_accuracy.py:543,544 | autopsia/kb/ddxplus.py:155 | REAL TERM |
| BLA | 1 | autopsia/kb/openfda_bulk_labels.py:369 | autopsia/kb/openfda_bulk_labels.py:442 | REAL TERM |
| Condition | 4 | api_noema_workflow.py:537; ingest/fhir_import.py:244,253; tests/test_api_fhir_import.py:152 | autopsia/ingest/fhir_import.py:284 | REAL TERM (FHIR resource type) |
| DeleteCitation | 1 | autopsia/kb/pubmed_bulk/index.py:611 | autopsia/kb/pubmed_bulk/index.py:566 | REAL TERM (MEDLINE XML element) |
| Dementia | 1 | tests/test_api_clinical.py:231 | autopsia/kb/famenet_leak_floor.py:105 | REAL TERM (condition in KB) |
| Frequency | 1 | autopsia/vocab/local_install.py:451 | autopsia/vocab/local_install.py:459 | REAL TERM (XML element) |
| Gender | 2 | bench/ru_gender_agreement_audit.py:10,104 | bench/ru_gender_agreement_audit.py:133 | REAL TERM (feature key) |
| Global | 1 | bench/gbd_results_bulk_fetch.py:30 | autopsia/kb/gbd_prevalence.py:310 | REAL TERM (GBD location value) |
| HeadingMappedTo | 2 | autopsia/vocab/structure.py:16,724 | autopsia/vocab/local_install.py:431 | REAL TERM (MeSH XML element) |
| High | 1 | bench/structured_prior_sources.py:135 | bench/structured_prior_sources.py:292 | REAL TERM (CSV column) |
| IndexingSourceName | 1 | autopsia/ingest/integrity.py:228 | autopsia/ingest/integrity.py:388 | REAL TERM (NLM field) |
| Label | 1 | autopsia/kb/pubmed_bulk/index.py:447 | autopsia/kb/pubmed_bulk/index.py:452 | REAL TERM (XML attribute) |
| Low | 1 | bench/structured_prior_sources.py:135 | bench/structured_prior_sources.py:292 | REAL TERM (CSV column) |
| Medication | 3 | ingest/fhir_import.py:27,157,178 | autopsia/ingest/fhir_import.py:168 | REAL TERM (FHIR resource type) |
| MedicationRequest | 2 | ingest/fhir_import.py:159,244 | autopsia/ingest/fhir_import.py:288 | REAL TERM (FHIR resource type) |
| MedicationStatement | 4 | api_noema_workflow.py:537; ingest/fhir_import.py:159,244,253 | autopsia/ingest/fhir_import.py:288 | REAL TERM (FHIR resource type) |
| MedlineCitation | 1 | autopsia/kb/pubmed_bulk/index.py:470 | autopsia/kb/pubmed_bulk/index.py:473 | REAL TERM (XML element) |
| MedlineTA | 1 | autopsia/kb/pubmed_bulk/index.py:22 | autopsia/kb/pubmed_bulk/index_helpers.py:19 | REAL TERM (XML element) |
| MultiLogloss | 1 | bench/ddxplus_discriminative.py:112 | bench/ddxplus_discriminative.py:120 | REAL TERM (CatBoost loss name) |
| NDA | 1 | autopsia/kb/openfda_bulk_labels.py:369 | autopsia/kb/openfda_bulk_labels.py:442 | REAL TERM (FDA submission type) |
| NFKD | 1 | autopsia/kb/i18n_shared.py:49 | bench/track_b_bycatch_row_checks_cells.py:121 | REAL TERM (unicodedata form) |
| NOT | 1 | autopsia/kb/cant_miss_severity.py:206 | autopsia/kb/cant_miss_severity.py:232 | REAL TERM (qualifier value) |
| National | 1 | autopsia/kb/hcup_stt.py:118 | autopsia/kb/hcup_stt.py:117 | REAL TERM (sheet name) |
| NlmUniqueID | 1 | autopsia/kb/pubmed_bulk/index.py:21 | autopsia/kb/pubmed_bulk/index_helpers.py:20 | REAL TERM (XML element) |
| NumericValue | 3 | bench/structured_prior_sources.py:21,103,109 | bench/structured_prior_sources.py:287 | REAL TERM (CSV column) |
| OFFICIAL | 1 | autopsia/vocab/babelon.py:56 | autopsia/vocab/babelon.py:73 | REAL TERM (status value) |
| OriginalPaperDOI | 1 | autopsia/ingest/integrity_sources.py:243 | autopsia/ingest/integrity_sources.py:272 | REAL TERM (CSV column) |
| Other | 3 | autopsia/kb/famenet.py:19,20; tests/test_noema_kb.py:752 | bench/harvest_famenet.py:70 | REAL TERM (FaMe-Net bucket label) |
| P3841 | 3 | autopsia/vocab/wikidata_hpo_labels.py:1,10,55 | bench/ru_label_source_guards.py:140 | REAL TERM (Wikidata property) |
| P5270 | 5 | wikidata_symptoms.py:5,42; wikidata_mondo_labels.py:1,83; wikidata_mondo_p31.py:9 | bench/ru_label_source_guards.py:140 | REAL TERM (Wikidata property) |
| Patient | 4 | ingest/fhir_import.py:219,255,265; tests/test_fhir_import.py:158 | autopsia/ingest/fhir_import.py:298 | REAL TERM (FHIR resource type) |
| PharmacologicalAction | 1 | autopsia/vocab/structure.py:734 | autopsia/vocab/local_install.py:436 | REAL TERM (MeSH XML element) |
| Prevalence | 1 | tests/test_noema_data_quality_overlays.py:39 | bench/gbd_results_bulk_fetch.py:68 | REAL TERM (GBD measure key) |
| PrevalenceClass | 2 | autopsia/kb/mints/mondo_0005035_ganglioneuroblastoma.py:19; mondo_0016227_episodic_ataxia.py:18 | autopsia/kb/prevalence.py:112 | REAL TERM (Orphanet field) |
| PreviousIndexing | 1 | autopsia/vocab/local_install.py:454 | autopsia/vocab/local_install.py:458 | REAL TERM (XML element) |
| RATE | 1 | bench/cms_pfs_fetch.py:19 | bench/cms_clfs_fetch.py:177 | REAL TERM (CSV column) |
| REPORTED | 2 | bench/structured_prior_allowlist.py:31,60 | bench/structured_prior_allowlist.py:69 | REAL TERM (Literal kind value) |
| Reason | 1 | autopsia/ingest/integrity.py:38 | autopsia/ingest/integrity_sources.py:287 | REAL TERM (Retraction Watch column) |
| RegistryNumber | 1 | autopsia/vocab/local_install.py:441 | autopsia/vocab/local_install.py:456 | REAL TERM (XML element) |
| RetractionNature | 2 | autopsia/ingest/integrity_sources.py:226,241 | autopsia/ingest/integrity_sources.py:275 | REAL TERM (CSV column) |
| Suffocation | 1 | autopsia/vocab/expand.py:4 | bench/grade_from_textbook.py:515 | REAL TERM (MeSH term) |
| TOTAL | 2 | bench/cms_pfs_fetch.py:179; tests/test_cms_pfs_fetch.py:36 | bench/differential_query_timing.py:100 | REAL TERM (CSV column) |
| TREATS | 2 | evidence_core/beta.py:152,182 | autopsia/graph_stats.py:60 | REAL TERM (edge relation) |
| __ | 1 | bench/lay_synonym_audit_full.py:72 | bench/annotator_ceiling.py:194 | REAL TERM (path-swap token) |
| _after_edge_trim | 1 | bench/track_b_bycatch_prompt.py:589 | bench/track_b_bycatch_grounding.py:276 | REAL TERM (tier suffix) |
| _ru | 12 | autopsia/api_risk_factors.py:155,178; reason/chains.py:553; reason/clinical_caveats.py:87,432; and 7 more | autopsia/reason/caveat_templates.py:706 | REAL TERM (suffix convention) |
| _study | 2 | autopsia/kb/track_b_llm_sourced.py:417; bench/narrow_population_prior_audit.py:120 | autopsia/kb/track_b_llm_sourced.py:426 | REAL TERM (suffix stripped) |

Total: 50 names, 91 findings, 0 phantoms. The generic words the owner flagged (Other, NOT, TOTAL, High, Low, REPORTED,
Dementia, Global) each have a production literal: "Other" in `bench/harvest_famenet.py:70` is the FaMe-Net bucket the
same comment family describes; the famenet.py comment says the source has no separate `Other` row, a claim about
data the scanner cannot judge, not a name the repo fails to define.

## Step 2: exception list

Empty. No name in the 91 is a phantom, so no `_KNOWN_NOT_DATA` entry is added. The module carries no exception list.
Consequence for the owner's test (d): there is no exception-list word to test, so that test is not written.

## Step 2 (tests): `tests/test_phantom_code_references.py`

- (a) `test_a_string_a_production_module_uses_in_any_role_is_a_data_name`: comparison, call argument, dict key, subscript key,
  table row in production files suppress; a never-spelled name is still reported.
- (b) `test_a_string_only_a_test_module_uses_is_still_a_phantom`: a dict key in `tests/test_fixture.py` does not suppress a
  production comment naming it.
- (c) `test_known_genuine_phantoms_stay_reported_next_to_longer_spelled_out_strings`: `LBD`, `_drop_age_excluded_candidates`,
  `CantMissCandidate.severity` stay reported; a longer literal (`LBD_pipeline`) does not suppress `LBD` (whole-string match).
- The old test `test_a_string_used_only_as_a_comparison_or_call_argument_does_not_suppress` (added by 2a5815b) encoded the
  narrowed rule this change reverses; it was replaced by (a).

Revert check (scanner temporarily restored to HEAD 2a5815b):
- (a) fails: `UseOnlyHere` and `UseOnlyTwo` reported.
- (b) fails: `OnlyInTestHere` suppressed by the test-module dict key.
- (c) passes on both versions. It is a guard for names that must stay reported, and the old rule already reported them.

## Step 3: results

- `python -m pytest tests/test_phantom_code_references.py -q -p no:anyio --no-cov`: 42 passed.
- `ruff check` on both touched files: 2 findings, both C901 complexity (`python_declarations` 32 > 10,
  `find_phantom_code_references` 16 > 10). Both are present at HEAD 2a5815b, not introduced here. Not fixed (out of scope).
- `mypy src/py_ci_shared/phantom_code_references.py`: 1 error at `src/py_ci_shared/_core/node_index.py:54`, a file this change
  does not touch (pre-existing).
- `black --check` on both touched files: clean after `black` (only the new lines were reformatted).
- Consumer recount: `PYTHONPATH=<wt_pyci>/src python scratchpad/arch9_count.py wt_round out.txt`: **COUNT 423**
  (`scratchpad/r6p/arch9_r6prime.txt`). Identical to the prototype's 423 (`arch9_var.txt`): no difference in either direction.

Count arithmetic: HEAD 493 unique findings (494 lines) - 91 suppressed + 21 newly reported = 423.

Known genuine phantoms: all still reported (`LBD`, `PMC13319664`, `_drop_age_excluded_candidates`, `TestStep.as_dict`,
`CantMissCandidate.severity`, `already_reviewed()`, `verify_merge_result()`); 10 lines in the output.

## 21 newly reported findings (test-file-only spellings)

Test modules no longer contribute, so these names are now reported. The rule says so and test (b) asserts it.
Names: `Location` (cochrane/wiley_tdm.py:20,70), `SAB` (curated_causal_chains.py:12; umls_source_metadata.py:32,47),
`ICD10` (mondo_icd10.py:15), `Malaria` (phmrc_child_verbal_autopsy.py:11), `SRL` (umls_source_metadata.py:17,20,33),
`Exposure` (bench/candidate_probe.py:963), `D2` (tests/test_clinical_boundary_rescue.py:128), `F2`
(tests/test_clinical_conformal.py:93), `COMMON`/`RARE` (tests/test_defaulted_prior_ids.py:20-23), `Polyphagia`
(tests/test_golden_set_diagnostic_accuracy.py:1036), `Proteinuria` (:290), `AUTOPSIA` (tests/test_meta/test_shared_library_boundary.py:87).
Owner decision point: if any of these is a real term, the fix is a production spelling or a per-word exception, not a test-file
exception. None was checked against its comment in this pass.

## Files

- `src/py_ci_shared/phantom_code_references.py`
- `tests/test_phantom_code_references.py`
- `audits/2026-10-20-fixes/arch9_r6prime.md` (this file)

Not pushed.
