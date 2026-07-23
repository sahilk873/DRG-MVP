# Extensible encounter ontology

The ontology is a versioned type system for patient-specific encounter graphs. It is not a list of billing conclusions and it is not executable clinical guidance. Wound care is the first domain package, not a special case in the engine.

## Layered model

| Layer | Responsibility | Change cadence |
|---|---|---|
| Core encounter model | Patient, encounter, evidence, claim, terminology mappings | Slow and governed |
| Domain ontology definition | Specialty classes, inheritance, relations, value sets | Versioned by domain owners |
| Patient instance graph | Entities and relations extracted for one encounter | Per encounter |
| Materialized assertions | Evidence-linked facts optimized for deterministic rules | Per encounter |
| Revenue rule package | Claim comparisons and candidate corrections | Effective-dated and approved |
| Clinical rule package | Alerts or treatment recommendations | Separate runtime and clinical governance |

The Python and TypeScript validators read structural templates, classes and relation definitions from data. They do not contain wound-type, wound-relation or fixed root-ID branches. A new domain supplies a new definition and passes it to the same validators. The packaged registry is only a convenient bootstrap for built-in definitions.

## Source-derived design

The wound-care workbook contributed 46 candidate clinical rules, 55 condition fields, and 19 core relationships. Those relationships are represented in `wound_care_ontology_v1.json`. The clinical rules are recorded as a non-executable source manifest because recommendations such as antibiotics, biopsy, debridement, vascular escalation, or offloading require clinical ownership, authorization, contraindication logic, and prospective validation. They are not coding authority.

The disease-treatment ontology paper contributed the reusable concepts of disease, treatment, condition, effect, evidence, quantity, time, and modality. This implementation adapts that literature model to a patient encounter by adding exact evidence lineage, assertion and documentation status, confidence, encounter linkage, financial artifacts, and governed version compatibility.

Terminology mappings are carried on instances as `{system, code, display}`. Internal class identifiers remain stable even when a customer uses SNOMED CT, ICD-10-CM, LOINC, RxNorm, local vocabularies, or a licensed terminology service. Production mappings must be licensed and validated; the bundled example deliberately uses a local demonstration namespace.

## Wound-care ontology lineage (v1 → v2 → v3)

The wound-care ontology has evolved as a strictly **additive** chain. Each version is a superset of the one before, so a newer version can validate everything an older one could. Every version is retained on disk with its own frozen digest and approval state; nothing is deleted, and no digest is ever recomputed for an existing version. Each shipped case, rule package, and adapter pins the exact ontology `id`, `version`, and `digest` it was reviewed against, and continues to validate against that pinned version unchanged.

| Version | File | Adds | Digest |
|---|---|---|---|
| `1.1.0-draft` (v1) | `data/wound_care_ontology_v1.json` | Base wound-care classes, relations, value sets, and terminology mappings | Frozen |
| `1.2.0-draft` (v2) | `data/wound_care_ontology_v2.json` | `SizeMeasurement` quantity + `hasSize` relation for longitudinal, dated wound-assessment measurements | Frozen |
| `1.3.0-draft` (v3) | `data/wound_care_ontology_v3.json` | Clinical finding / perfusion / systemic-marker classes and value sets, healing-trend and exudate-type value sets, and structural wiring for the clinical alert / recommended-action / urgency / contraindication relations so the `clinical_care_gap` rule library can bind | Frozen |

**v3 (`1.3.0-draft`) is the single authoritative wound-care ontology.** New wound-care artifacts should bind against v3, which is the version both governed peer domains share: `revenue_integrity` rules (claim comparisons and candidate corrections) and `clinical_care_gap` rules (analytics that identify gaps in care but never mutate a claim, assign a DRG, compute reimbursement, or bypass review). The two domains are structurally walled off from each other at rule-parse time; v3 simply gives both a common, evidence-grounded type system to reference.

The authoritative version is wired in `ontology.py` as `AUTHORITATIVE_WOUND_CARE_ONTOLOGY` with the `load_authoritative_wound_care_ontology()` helper. This pointer is lineage/wiring only — it does not change any ontology digest and does not widen what the deterministic validators accept. Because v1 and v2 remain registered and unchanged, promoting v3 to authoritative is backward-compatible: legacy artifacts keep validating against their pinned versions with byte-identical results.

## Agent and deterministic boundary

Mastra may extract entities, relationships, modalities, quantities, contradictions, and materialized assertions. It may also suggest terminology candidates. The orchestrator then verifies exact excerpts, source metadata, references, types, relation domain and range, evidence requirements, ontology ID/version/digest, configured resource budgets, and immutable claim separation.

Only deterministic components may execute approved rules, compare claims, call a grouper or pricer, calculate deltas, or produce a governed disposition. Agent output cannot introduce code, alter a claim, assign a DRG, authorize treatment, or mark its own rule package approved.

The intended automation path is:

1. ingest and normalize source records;
2. retrieve the smallest relevant document set;
3. extract the patient graph and evidence;
4. normalize terminology and surface conflicts;
5. validate the graph against its ontology definition;
6. materialize rule facts and execute approved packages;
7. reproduce grouping and simulate candidate changes;
8. suppress cleared or immaterial cases;
9. assemble a focused review packet with evidence and contradictions.

Humans should review the small set of claim-affecting, ambiguous, compliance-sensitive, or clinically controlled exceptions. Automation should clear supported no-op cases, but straight-through claim changes require separate institutional approval and prospective evidence.

## Adding a specialty or function

1. Create a new JSON definition conforming to `schemas/ontology_definition.schema.json`. Extend stable core classes instead of renaming them.
2. Add domain classes, parent relationships, relation domains/ranges, evidence requirements, and value sets. Avoid embedding payer policy or workflow state in clinical classes.
3. Map external terminologies on instances or in a separately licensed terminology package.
4. Configure the Mastra extractor with the definition. The same structured-output and semantic validators apply.
5. Create an effective-dated revenue-integrity rule package that declares the exact ontology ID, version and semantic digest. Give each rule an ontology `applies_to` scope; clinical decision-support rules belong in a separate package and runtime.
6. Add positive, negative, boundary, negation, temporality, contradiction, and malformed-graph tests.
7. Evaluate ontology coverage using a development corpus and an untouched holdout corpus. Add concepts only when the holdout reveals a real coverage gap, then repeat until additions stabilize.
8. Publish a new ontology version for breaking class or relation changes and update the generated digest binding. Digest mismatch fails closed even if a version bump is accidentally omitted; it does not replace version governance.

Rule-facing assertions keep the deterministic engine simple, but each assertion must reference its graph subject. Future graph-pattern evaluators can consume the same instance graph without changing extraction or evidence contracts.
