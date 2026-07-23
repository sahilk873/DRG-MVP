# 3 · Technical implementation

This is the engineer's map: architecture, the Python module layout, the versioned
contracts, where the wall is enforced, and how data flows through the spine.

## Layered architecture

```
 agent/ (Node/TS, Mastra)          src/revenue_integrity/ (Python 3.11+)              demo/ (React/Vite)
 ─────────────────────────         ────────────────────────────────────────          ──────────────────
 semantic extraction ONLY   ──▶    orchestration → investigation (validate)   ──▶     renders the review
 grounded evidence +               → engine (detect) → automation (route)              packet + automation
 hypotheses; provider-              → routing (outbox) → workflow (close)               plan (read-only)
 agnostic (MODEL_ID)               → review_packet (versioned handoff)
                                    → audit (hash chain)     [all deterministic]
```

The agent is provider-agnostic via a `provider/model` string (`MODEL_ID`); it imports **no**
provider SDK, so swapping the model changes nothing downstream. The authoritative
layer-by-layer "may use an LLM / produces authoritative result" table lives in
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md).

## Python module map (`src/revenue_integrity/`)

| Module | Responsibility |
|--------|----------------|
| `models.py` | `EncounterCase`, `Claim`, `Finding`; enums `RuleDomain`, `GapDomain`, `GapStatus`, `ClinicalUrgency`, `ExceptionType`; dated wound-assessment / `SizeMeasurement` support; the construction-time half of the wall (`Finding.__post_init__`). |
| `rules.py` | The declarative DSL: `Condition` (`all`/`any`/`not`/`co_occurs`), `ProposedChange`, `RuleAction`, `RuleScope`. Operator sets, `SUPPORTED_CHANGE_KEYS`, and the parse-time half of the wall (`RuleAction.from_dict`). |
| `engine.py` | `RuleEngine` — evaluates a validated `RulePackage` of either domain; derives longitudinal facts; evaluates temporal/co-occurrence operators. `ENGINE_VERSION` 0.7.0. |
| `narrative.py` | Deterministic clinician/reviewer-legible narratives; a gap finding renders the walled-off gap narrative, a revenue finding renders byte-identically to before. |
| `ontology.py` | Data-driven ontology (inheritance + relation domain/range validation, versioned semantic digests). `load_authoritative_wound_care_ontology()` → v3 (`1.3.0-draft`); v1/v2 retained for lineage. |
| `investigation.py` | Typed hypothesis→finding trust boundary; lineage checks; framework-independent. |
| `orchestration.py` | Governed agent-to-review handoff (`run_investigation`). |
| `automation.py` | Exception/tiering policy; the `CARE_GAP` queue + urgency→tier map; the hash-covered `gap_worklist` metrics. `AUTOMATION_SCHEMA_VERSION` 1.3.0. |
| `routing.py` | Routing outbox with the dedicated `CARE_GAP_ALERT` / `CARE_GAP_ALERT_PROSPECTIVE` lanes. |
| `workflow.py` | Reviewer decisions + the gap-closure lifecycle (`GapClosureAction`, `GapClosureService`, `CARE_GAP_COORDINATOR`, hash-chained `GapClosureRecord`, `GAP_CLOSURE_SCHEMA_VERSION` 1.0.0). |
| `review_packet.py` | Versioned reviewer-UI handoff. `REVIEW_PACKET_SCHEMA_VERSION` 3.5.0; optional clinical-gap finding fields; `claim_mutation_allowed=false`. |
| `grouper.py` | `Grouper` protocol + `DeterministicDemoGrouper` (a fake, `version="demo-...-not-for-billing"`). Integer-cent math only. |
| `audit.py` | Canonical JSON, sha256, hash-chained audit records. |
| `ingestion/` | Bulk profiling + declarative adapters (the second trust boundary — see [`docs/ADAPTER_FACTORY.md`](../ADAPTER_FACTORY.md)). |

## Versioned contracts (bump on any breaking change)

| Constant | Value | Where |
|----------|-------|-------|
| `ENGINE_VERSION` | `0.7.0` | `engine.py` |
| `SUPPORTED_SCHEMA_VERSION` (case) | `2.0.0` | `models.py` |
| `REVIEW_PACKET_SCHEMA_VERSION` | `3.5.0` | `review_packet.py` + `schemas/review_packet.schema.json` + demo Zod |
| `AUTOMATION_SCHEMA_VERSION` | `1.3.0` | `automation.py` + `schemas/automation_plan.schema.json` + demo Zod |
| `GAP_CLOSURE_SCHEMA_VERSION` | `1.0.0` | `workflow.py` |
| Authoritative ontology | `wound-care-encounter-ontology` `1.3.0-draft` | `ontology.py` / `data/` |

JSON Schemas live in `schemas/`; they are **public contracts** — change the schema, the
Python, the demo Zod, the generated fixture, and the docs **together**, then run
`make demo-packet-check` so the demo cannot drift from the engine.

## The claim-mutation wall

The wall is enforced at **four independent layers**, so no single forgotten check can breach
it (see finding-#9 hardening for the schema layer):

1. **Parse-time** (`rules.py` → `RuleAction.from_dict`): a `clinical_care_gap` rule with any
   non-empty `proposed_change` is rejected; a `revenue_integrity` rule carrying gap fields is
   rejected.
2. **Construction-time** (`models.py` → `Finding.__post_init__`): a `Finding` object cannot
   simultaneously hold a `gap_domain` and a non-empty `proposed_change`, and gap findings must
   set `requires_human_review=true`.
3. **Schema-time** (`schemas/rule_package.schema.json` + `review_packet.schema.json`):
   `if/then` constraints reject cross-domain artifacts for schema-only tooling.
4. **Workflow-time** (`workflow.py`): `ReviewWorkflowService.submit` refuses gap findings;
   `GapClosureService.submit` refuses non-gap findings and any gap action carrying a claim
   mutation. Both paths are role-gated and hash-chained.

## Deterministic longitudinal facts

Temporal reasoning is not baked into rule operators as ad-hoc arithmetic; the engine
computes provenance-tagged **derived facts** from the assessment timeline and exposes them as
`attributes.*` that ordinary operators read:

- `size_trend_pct` — % change vs the `compared_with_id` prior assessment.
- `days_since_baseline` — elapsed days from the first sized assessment.
- `reassessment_overdue` — whether an expected reassessment has lapsed.

Because the math is deterministic Python (not an LLM and not an opaque operator), it is unit-
tested independently and stays legible in the audit trail.

## Operators (`rules.py`)

- **Scalar:** `eq`, `ne`, `gte`, `lte`, `in`, `between`, `contains`, `not_contains`,
  `starts_with`, `exists`, `count_gte`, `count_lte`, `subsumed_by`.
- **Temporal:** `elapsed_days_gte`, `elapsed_days_lte`, `absent_within_days` (assertion-set-
  aware — see [domain deep-dive](04-clinical-care-gap-domain.md#temporal--co-occurrence)).
- **Percentage-change:** `pct_change_gte`, `pct_change_lte`.
- **Combinator:** `co_occurs` (bounded; two-or-more sub-conditions must be satisfied across
  the assertion set, optionally within a `window_days`).

## Test & verification surface

527 Python unit tests + the agent (`node --test`) and demo (vitest) suites. The full
**five-gate** proof is in [Quickstart](05-quickstart.md). The engine is also exercised by a
precision/recall eval harness — see [`docs/EVALUATION.md`](../EVALUATION.md).

Next: [The clinical_care_gap domain →](04-clinical-care-gap-domain.md)
