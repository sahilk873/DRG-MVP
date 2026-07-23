# 4 · The clinical_care_gap domain

This is the deep-dive on the new lens: its taxonomy, the DSL and operators it needs, the
shipped rule library, the closure lifecycle, and a step-by-step guide to authoring a rule.

## The three gap domains (`GapDomain`)

Every care-gap rule classifies the gap it detects:

| `gap_domain` | Meaning | Example |
|--------------|---------|---------|
| `missing_action` | An expected assessment/test/treatment/referral/escalation **never occurred** | Offloading device never evaluated for a neuropathic DFU |
| `delayed_action` | Appropriate care occurred but **outside the clinically-defined window** | Reassessment expected within 1–2 days of stalled healing, not done |
| `incomplete_follow_through` | An order was placed but result/completion/closure **can't be established** | Referral placed, no completed visit or closure status |

Shipped distribution in `rules/wound_care_gaps_v1.json`: **missing_action 33 · delayed_action
7 · incomplete_follow_through 6** (46 rules total).

## Clinical output fields

A gap `Finding` carries (all optional on the shared `Finding`, populated only for gaps):
`gap_domain`, `expected_action`, `actual_action`, `timing_window_days`, `alert_urgency`
(`ClinicalUrgency`: routine / same_day / urgent / emergent), `recommended_action`,
`clinical_impact`, `exception_checks[]` (`{exception_type, evidence_id, status}`),
`gap_status` (`GapStatus`), `closed_at`, `barrier_code`. It **never** carries a
`proposed_change` — that's the wall.

## Temporal & co-occurrence

The methodology's rules are inherently about **time** and **co-occurring facts**. Two
capabilities make that expressible deterministically:

- **Derived longitudinal facts** — the engine pre-computes `size_trend_pct`,
  `days_since_baseline`, `reassessment_overdue` (etc.) from the dated assessment timeline, so
  rules read them as ordinary `attributes.*`.
- **Native operators** — `elapsed_days_gte/lte`, `pct_change_gte/lte`, and the assertion-set-
  aware `absent_within_days` (true when **no** assertion in scope has an observation within N
  days of the reference date). Plus the bounded `co_occurs` combinator for "fact A and fact B
  together, optionally within `window_days`".

> **Author note:** `absent_within_days` is *assertion-set-aware*. As a bare leaf with no
> assertion-set context it fails safe (never fires); use it where the engine passes the scoped
> assertion set. It is coherent under `not` (meaning "present within window").

## Anatomy of a rule — the flagship CG-INF-002

```jsonc
{
  "rule_id": "CG-INF-002",
  "title": "Chronic wound with no size reduction after two weeks of standard care needs clinician reassessment",
  "applies_to": { "subject_types": ["WoundAssessment"], "include_subtypes": true },
  "when": {
    "all": [
      { "field": "attributes.standard_care_documented", "op": "eq",            "value": true    },
      { "field": "attributes.days_since_baseline",       "op": "gte",           "value": 14      },
      { "field": "attributes.size_trend_pct",            "op": "pct_change_gte","value": -0.0001 },  // no net reduction
      { "field": "attributes.reassessment_overdue",      "op": "eq",            "value": true    }
    ]
  },
  "then": {
    "disposition": "cdi_query",
    "requires_human_review": true,
    "proposed_change": {},                 // ← empty: the wall
    "gap_domain": "delayed_action",
    "alert_urgency": "urgent",
    "recommended_action": "Reassess the wound and evaluate for chronic wound infection or advanced therapy.",
    "timing_window_days": 14,
    "rationale": "…analytics identify this care gap; a clinician decides."
  }
}
```

## The rule library (`rules/wound_care_gaps_v1.json`)

`package_id: wound-care-clinical-care-gap`, `version: 1.0.0-demo`,
`rule_domain: clinical_care_gap`, `status: approved-for-demo`, bound to ontology
`wound-care-encounter-ontology` `1.3.0-draft`. 46 rules grouped by clinical theme, id scheme
`CG-<GROUP>-NNN`:

| Group | Prefix | Coverage |
|-------|--------|----------|
| Infection | `CG-INF` | purulent/erythema/warmth, chronic-wound stall (flagship), SSI |
| Pressure injury | `CG-PI` | stages 1–4, sacrum/heel recurrence |
| Diabetic foot ulcer | `CG-DFU` | neuropathic, probe-to-bone, DFI, callus, glucose |
| Venous / arterial | `CG-VEN`, `CG-ART` | insufficiency, maceration, ischemia, perfusion, mixed |
| Tissue / debridement | `CG-TIS` | eschar, slough, granulation, epithelializing |
| Periwound / moisture | `CG-PW` | MASD, MARSI, cellulitis, breakdown |
| Tunneling / undermining | `CG-TUN` | dead space, stalled edge, abscess, deterioration |
| Deterioration / escalation | `CG-DET` | ≥20% growth, systemic infection, immunocompromised |
| Advanced composite | `CG-CMP-01..10` | PAD, chronic non-healing, dehiscence, sepsis, osteomyelitis, CLI, biofilm, unstageable |

## Exceptions & the closure lifecycle

- **Exceptions** (`ExceptionType`): patient_refusal, contraindication, transfer, hospice,
  outside_care, documented_judgment. A **confirmed, undisputed** exception downgrades a gap
  (it should not reach focused review as an active alert).
- **Routing**: gap findings go to the `CARE_GAP` automation queue with an urgency→tier map;
  auto-routed gaps land in the `CARE_GAP_ALERT` outbox lane (`routing.py`).
- **Closure** (`workflow.py`): `GapStatus` transitions open → routed → closed / exception /
  withdrawn, driven only by the `CARE_GAP_COORDINATOR` role via `GapClosureService`, producing
  a hash-chained `GapClosureRecord`. The revenue path refuses gap findings entirely.

## Worklist metrics (`gap_worklist` in the automation plan)

`open_high_risk_gaps`, `avg_expected_window_days`, `median_closure_days`, `top_alert_reason`,
`top_barrier`, `total_gaps`, `is_estimate`. These are deterministic and hash-covered.

> **Honesty note:** `avg_expected_window_days` is the rule-**configured** action window, not an
> observed expected→actual delay (the model has no actual-action timestamp at plan-build time).
> It was named this way on purpose so a dashboard cannot misread it as measured lateness.

## Authoring a new gap rule — checklist

1. **Pick the subject scope** — an `applies_to.subject_types` that exists in the authoritative
   ontology (don't add a redundant `concept ==` gate; the scope enforces the type).
2. **Write the `when`** using scalar + derived-fact + temporal/co-occurrence operators.
3. **Classify `gap_domain`** and set `alert_urgency`, `recommended_action`, and
   `timing_window_days` where a window is implied.
4. **Leave `proposed_change` empty** and `requires_human_review: true` — the wall (and the
   schema) will reject anything else.
5. **Add tests** — positive, negative, and (if there's a plausible justified exception) a
   suppression test. Run the suite + `demo-packet-check`.
6. **Version & approve** — bump the ontology/rule versions on any contract change; the package
   must be `approved` or `approved-for-demo` to load (fail-closed).

Next: [Quickstart & verification →](05-quickstart.md)
