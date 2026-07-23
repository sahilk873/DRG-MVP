# 6 · Glossary

Every term, acronym, and named constant used across the guide, in one place.

## Core concepts

- **Spine** — the single deterministic pipeline both lenses share: *episode → ontology →
  detect → validate → route → close*.
- **Lens** — one of the two peer reasoning domains riding the spine, keyed by `rule_domain`:
  `revenue_integrity` or `clinical_care_gap`.
- **Core invariant** — no language-model output may execute code, create/change a claim,
  assign a DRG, compute reimbursement, or bypass review.
- **Claim-mutation wall** — the structural guarantee that a `clinical_care_gap` rule/finding
  cannot carry a claim change and a `revenue_integrity` rule cannot carry clinical action
  fields. Enforced at parse-, construction-, schema-, and workflow-time.
- **Evidence grounding** — the rule that every evidence excerpt must be a literal substring of
  a source document; the clinician "litmus test" made mechanical.
- **Trust boundary** — the line the language model may not cross: it emits only grounded
  evidence + hypotheses; deterministic Python does everything authoritative.

## Domain model

- **`EncounterCase`** — the reconstructed encounter/episode: claim + evidence + assertions
  (+ dated wound-assessment timeline for the clinical lens).
- **Assertion** — a typed clinical fact anchored to evidence and (often) a date.
- **Finding** — the engine's output for a matched rule; carries lineage, disposition,
  narrative, and (for gaps) the clinical output fields. Never both `gap_domain` and a
  `proposed_change`.
- **Derived longitudinal fact** — a provenance-tagged value the engine computes from the
  timeline (`size_trend_pct`, `days_since_baseline`, `reassessment_overdue`) so rules express
  temporal logic with ordinary operators.

## Rules & DSL

- **RulePackage** — a governed, versioned bundle of rules for one `rule_domain`; fails closed
  unless `status` ∈ {`approved`, `approved-for-demo`}.
- **`when` / `then`** — a rule's condition and action.
- **Combinators** — `all`, `any`, `not`, `co_occurs` (bounded, optional `window_days`).
- **Operators** — scalar (`eq`, `ne`, `gte`, `lte`, `in`, `between`, `contains`,
  `not_contains`, `starts_with`, `exists`, `count_gte`, `count_lte`, `subsumed_by`), temporal
  (`elapsed_days_gte`, `elapsed_days_lte`, `absent_within_days`), percentage-change
  (`pct_change_gte`, `pct_change_lte`).
- **`SUPPORTED_CHANGE_KEYS`** — the six claim-mutation keys a revenue rule may carry:
  `add/remove_diagnoses`, `add/remove_procedures`, `add/remove_charges`.

## Gap taxonomy & lifecycle

- **`GapDomain`** — `missing_action`, `delayed_action`, `incomplete_follow_through`.
- **`ClinicalUrgency`** — `routine`, `same_day`, `urgent`, `emergent`.
- **`ExceptionType`** — `patient_refusal`, `contraindication`, `transfer`, `hospice`,
  `outside_care`, `documented_judgment` (justified reasons a standard is legitimately bypassed).
- **`GapStatus`** — `open` → `routed` → `closed` / `exception` / `withdrawn`.
- **`CARE_GAP`** — the automation queue for gap findings.
- **`CARE_GAP_ALERT` / `CARE_GAP_ALERT_PROSPECTIVE`** — the routing outbox lanes for gaps.
- **`CARE_GAP_COORDINATOR`** — the only role authorized to close a gap.
- **gap_worklist** — the automation-plan metrics block: `open_high_risk_gaps`,
  `avg_expected_window_days`, `median_closure_days`, `top_alert_reason`, `top_barrier`,
  `total_gaps`, `is_estimate`.

## Versions & constants

- **`ENGINE_VERSION`** `0.7.0` · **`SUPPORTED_SCHEMA_VERSION`** (case) `2.0.0`
- **`REVIEW_PACKET_SCHEMA_VERSION`** `3.5.0` · **`AUTOMATION_SCHEMA_VERSION`** `1.3.0`
- **`GAP_CLOSURE_SCHEMA_VERSION`** `1.0.0`
- **Authoritative ontology** — `wound-care-encounter-ontology` `1.3.0-draft` (v1/v2 retained
  for lineage).
- **Gap rule package** — `wound-care-clinical-care-gap` `1.0.0-demo`, 46 rules.

## Acronyms

- **DFU** — diabetic foot ulcer (the worked example). **PI** — pressure injury.
- **DRG** — diagnosis-related group (the reimbursement grouping the revenue lens reasons about;
  computed only by the deterministic grouper boundary, never by a model).
- **CDI** — clinical documentation integrity. **SSI** — surgical site infection.
- **MASD / MARSI** — moisture-associated skin damage / medical-adhesive-related skin injury.
- **PAD / CLI** — peripheral arterial disease / critical limb ischemia.
- **PHI** — protected health information (never committed).

## Where to go deeper

[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) · [`docs/ONTOLOGY.md`](../ONTOLOGY.md) ·
[`docs/REVIEW_PACKET.md`](../REVIEW_PACKET.md) · [`docs/AUTOMATION.md`](../AUTOMATION.md) ·
[`docs/REVIEW_WORKFLOW.md`](../REVIEW_WORKFLOW.md) ·
[`docs/ADAPTER_FACTORY.md`](../ADAPTER_FACTORY.md) · [`docs/EVALUATION.md`](../EVALUATION.md)
