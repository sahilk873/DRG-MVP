# Human review packet contract

The review packet is the stable boundary between encounter evaluation and any reviewer application. It prevents the UI, workflow system, or downstream integration from reconstructing financial logic independently.

## Contract contents

`schemas/review_packet.schema.json` defines version `3.5.0` with:

- tenant/workspace scope, encounter identity, and the immutable claim snapshot used for evaluation;
- the evidence (each item carrying a deterministic `source_locator` deep link, see below), assertions, and patient-specific ontology graph seen by the rules;
- deterministic findings with rule, grouper, DRG, impact, evidence lineage, a grouping `derivation` trace, line-level `charge_line_refs`, and a plain-language `narrative` (see below);
- a deterministic `impact_summary` ROI rollup (see below);
- a deterministic `denial_summary` payer-denial rollup (see below);
- explicit controls forbidding claim mutation and enumerating reviewer actions;
- hashes for the case, rule package, and audit record plus all executable component versions;
- a full-packet hash covering tenant scope, controls, findings, evidence, `impact_summary`, and provenance.

### Evidence `source_locator` deep link (added in 3.4.0)

Every surfaced evidence item carries a read-only `source_locator` so a reviewer or the demo can deep-link to
the exact source location. It is a **pure deterministic function of grounding the trust boundary already
validated** — the agent (or authored reader) guarantees every excerpt `text` is an exact, contiguous
substring of its source document. No language-model output participates and no new authoritative field is
created. Two mutually exclusive shapes, discriminated by `kind`:

- `clinical_note_excerpt` — for chart-note excerpts: `document_id` plus the excerpt span (`char_start`,
  `char_end`, `length`) and a content-addressing `excerpt_sha256`. The span is expressed relative to the
  surfaced excerpt window; a viewer content-addresses the excerpt inside `document_id` to place it.
- `structured_source_record` — for evidence originating from a deterministic ingestion adapter: the existing
  row-level address (`adapter_id`, `adapter_version`, `resource`, `path`, `row_number`, `source_record_id`,
  `field_names`, optional `sheet`), re-tagged with `kind` so the UI can render a source-row deep link.

`source_locator` is inside the packet hash, so any tampering breaks `verify_review_packet_hash`.

### `impact_summary` (added in 3.1.0)

A deterministic, integer-cent aggregation over the packet's findings so a reviewer or CFO can
reproduce the ROI figure directly from the packet instead of trusting a hand-typed number:

- `positive_opportunity_cents` — sum of estimated under-coded upside (`impact > 0`);
- `at_risk_cents` — magnitude of estimated downside exposure (`impact < 0`), as a non-negative number;
- `net_estimated_impact_cents` — signed sum of the two;
- `estimated_finding_count` / `unavailable_impact_count` / `not_applicable_impact_count`;
- `total_findings`, `findings_requiring_review`, and `findings_by_disposition`;
- `basis` — a `synthetic-demo-grouper-not-for-billing` marker.

No language-model output participates: it only rolls up numbers the deterministic engine already
produced. `impact_summary` is inside the packet hash, so any tampering breaks `verify_review_packet_hash`.

### Finding `derivation` trace (added in 3.2.0)

Each finding carries a `derivation` object with `current` and `simulated` step lists, each an ordered,
deterministic explanation of how the grouper produced a DRG and payment (`severity_resolution → tier_selection →
pricing`). This lets a reviewer see exactly *why* a DRG changed — the severity-driving diagnosis, the resolved
tier, and the integer-cent pricing — rather than trusting an opaque number. The trace is produced by the
deterministic grouper (never a model), flows through `Finding.to_dict()`, and is covered by the packet hash.

### Finding `charge_line_refs` and `narrative` (added in 3.3.0)

- `charge_line_refs` — an optional, read-only list of the claim charge-line IDs a finding is bound to
  (empty for findings not tied to specific charge lines, including every demo finding, which has no
  charge lines). It is never model-supplied; it only restates line-level lineage the deterministic engine
  already established, and is covered by the packet hash.
- `narrative` — a single deterministic, plain-language sentence summarizing the finding (rule, DRG delta,
  estimated impact, and required review routing). It is purely presentational: it restates existing
  deterministic finding fields and introduces **no** new authoritative field, no claim mutation, and no
  language-model output. `review_packet.py` renders it via `narrative.render_finding_narrative` and injects
  it onto each serialized finding before hashing, so it is covered by `verify_review_packet_hash`.

### `denial_summary` (added in 3.3.0)

A deterministic, integer-cent, packet-level rollup of payer-denial exposure derived purely from
`case.financial` (the immutable `FinancialSnapshot`). It zeros out cleanly (all-zero, empty line list)
when the case carries no financial context:

- `denied_amount_cents` — sum of denial amounts (`0` for denials without an amount);
- `denial_count` — number of denials;
- `at_risk_line_count` / `at_risk_line_ids` — count and sorted, de-duplicated list of charge-line IDs
  referenced by any denial;
- `currency` — a `USD` marker.

No language-model output participates. `denial_summary` is inside the packet hash, so any tampering breaks
`verify_review_packet_hash`.

### Optional `clinical_care_gap` finding fields (added in 3.5.0)

The wire format carries a second governed peer domain, `clinical_care_gap`, on the same spine. A finding
emitted from that domain adds a set of **optional** fields alongside the existing revenue-integrity finding
fields. Every field is emitted only when the finding is a clinical-care gap, so a `revenue_integrity` packet
carries **none** of these keys and its serialized shape is byte-for-byte unchanged aside from the version
string. The optional fields (matching `Finding.to_dict()` exactly, since the schema is `additionalProperties:
false`):

- `gap_domain` — `missing_action` | `delayed_action` | `incomplete_follow_through`;
- `expected_action` / `actual_action` — what the guideline expected vs. what was documented;
- `timing_window_days` — the expected timing window (number, ≥ 0);
- `alert_urgency` — `routine` | `same_day` | `urgent` | `emergent`;
- `recommended_action` / `clinical_impact` — analytics-only clinician-facing text;
- `exception_checks` — array of `{exception_type, evidence_id, status}` evidence-grounded exception records
  (`exception_type` ∈ `patient_refusal`, `contraindication`, `transfer`, `hospice`, `outside_care`,
  `documented_judgment`);
- `gap_status` — `open` | `routed` | `closed` | `exception` | `withdrawn` (defaults to `open`);
- `closed_at` — closure timestamp when the gap is closed;
- `barrier_code` — an optional coded reason the gap remains open.

A finding carrying `gap_domain` must require human review and carry **no** claim-mutating `proposed_change`
(the domain wall, enforced in `Finding.__post_init__` and re-asserted conditionally in the schema). Analytics
identify gaps; clinicians decide. When any clinical-care-gap finding is present, the packet's
`controls.permitted_actions` additionally offers `route_to_care_team` and `close_gap_with_evidence` — neither
mutates a claim. All gap fields are inside the packet hash.

The packet is a reviewer input, not an outbound claim transaction. The governed decision service enforces tenant and role policy, requires a reason, and appends the decision to a hash-linked repository. Deployment authentication and the production database remain infrastructure responsibilities. The reference UI implements the same gateway contract locally for synthetic demonstrations.

## Generate a packet

```bash
revenue-integrity \
  examples/case_pressure_injury.json \
  rules/wound_care_v1.json \
  --tenant-id tenant-demo-alpha --workspace-id workspace-revenue-integrity \
  --format review-packet \
  --environment synthetic \
  --output output/review-packet.json
```

Environment labels are metadata, not authorization. Production authorization must come from deployment policy and identity controls.

## Demo fixture integrity

The primary frontend case is generated from the Python engine:

```bash
make demo-packet
make demo-packet-check
```

CI runs the check form. Any rule, ontology, evidence, grouper, engine, or automation-policy change that alters the deterministic result must deliberately regenerate and review both demo fixtures. The browser validates both contracts with fail-closed Zod boundaries before rendering them.

## Versioning rules

- Additive optional fields may remain within a compatible contract version.
- Required-field, meaning, enum, or trust-boundary changes require a new schema version.
- Consumers must reject unknown major versions.
- Never remove provenance or relax `claim_mutation_allowed: false` in this contract.
- Claim submission, payer communication, and reviewer decisions belong in separate, explicitly authorized contracts.
