# 2 · How it works — end-to-end

The whole system is one deterministic pipeline — the **spine** — with two lenses reading
the same reconstructed episode. This page walks the spine stage by stage, then traces the
**Diabetic Foot Ulcer (DFU)** worked example through it.

```
 (1) EPISODE        (2) ONTOLOGY +      (3) DETECT        (4) VALIDATE        (5) ROUTE        (6) CLOSE
     build              extraction          rules             exceptions          to team          & track
 ─────────────      ───────────────     ───────────      ───────────────     ──────────       ────────────
 reconstruct a      typed clinical      declarative       confirm the gap     automation       gap lifecycle
 time-ordered       facts anchored      If→Then rules     is real + screen    tiers + a        (open→routed→
 record of the      to dates, from      over the          justified           routing          closed/exception/
 encounter/episode  grounded evidence   timeline          exceptions          outbox           withdrawn)
```

Stages 1–6 are **deterministic Python**. The only place a language model participates is the
grounded-evidence extraction feeding stage 2 — and it never sees the claim, never emits
authoritative fields, and never makes the gap decision.

---

## Stage 1 — Build the episode

An `EncounterCase` (`models.py`) carries the immutable source facts: the claim, evidence
items (each with `document_id`, `author_role`, `recorded_at`, and literal `text`), and
**assertions** (typed clinical facts). For the clinical lens the case also carries a
**longitudinal series of dated wound assessments** with measurements, linked by
`compared_with_id`, so the engine can reason about trends over time.

## Stage 2 — Ontology + extraction

The extraction agent (`agent/`) reads encounter timing, an ontology contract, and source
documents — **but not the claim**. It returns evidence excerpts + a patient-specific
ontology fragment + clinical assertions. The orchestrator then, **outside the model**:
verifies every excerpt is a literal source substring, checks ontology types/relations/
lineage against the authoritative ontology (`wound-care-encounter-ontology` v1.3.0-draft),
merges immutable claim fields, and runs an independent Python validator
(`investigation.py`). Anything malformed fails closed. See [`docs/ONTOLOGY.md`](../ONTOLOGY.md).

## Stage 3 — Detect (the rule engine)

`RuleEngine` (`engine.py`) evaluates a validated `RulePackage` against the case. Rules are a
**declarative JSON DSL** (`rules.py`): a `when` condition (`all` / `any` / `not` /
`co_occurs` combinators over leaf operators) and a `then` action. Crucially, the engine
derives **deterministic longitudinal facts** from the assessment timeline (e.g.
`size_trend_pct`, `days_since_baseline`, `reassessment_overdue`) so rules can express
"no size reduction in two weeks" or "grew ≥20%" using ordinary operators — the temporal math
lives in one testable, non-LLM place. See [the domain deep-dive](04-clinical-care-gap-domain.md).

## Stage 4 — Validate

Structural/provenance validation (`investigation.py`, `review_packet.py`) confirms every
finding's lineage and grounding. For care gaps this is where **justified exceptions**
(`ExceptionType`: patient_refusal, contraindication, transfer, hospice, outside_care,
documented_judgment) are represented — a confirmed, undisputed exception downgrades the gap
rather than routing it as an active alert.

## Stage 5 — Route

`automation.py` tiers each finding (suppress / enrich / auto-route / quick-confirm /
focused-review / escalate) and picks a queue. Care-gap findings route to the **`CARE_GAP`**
queue with a clinical **urgency→tier** mapping; `routing.py` writes auto-routed gaps to a
dedicated **`CARE_GAP_ALERT`** outbox lane (prospective/retrospective), separate from the
revenue queues. See [`docs/AUTOMATION.md`](../AUTOMATION.md).

## Stage 6 — Close & track

`workflow.py` records hash-chained decisions. Care gaps have their own **closure lifecycle**
(`GapStatus`: open → routed → closed / exception / withdrawn), driven only by an authorized
**`CARE_GAP_COORDINATOR`** role through `GapClosureService`, producing a reproducible
`GapClosureRecord`. The revenue decision path (`ReviewWorkflowService.submit`) **refuses**
care-gap findings, so a gap can never be disposed of outside its governed lane. See
[`docs/REVIEW_WORKFLOW.md`](../REVIEW_WORKFLOW.md).

Everything a reviewer sees is assembled into a **versioned review packet**
(`REVIEW_PACKET_SCHEMA_VERSION` 3.5.0) with `claim_mutation_allowed=false`, plus an
**automation plan** (`AUTOMATION_SCHEMA_VERSION` 1.3.0) carrying worklist metrics.

---

## Worked example: Diabetic Foot Ulcer with stalled healing

Fixture: `examples/case_diabetic_foot_ulcer_episode.json`. The episode timeline:

| Day | Event | Grounded evidence excerpt |
|-----|-------|---------------------------|
| 0  | Initial eval, DFU 2.4 × 1.8 cm | *"Ulcer remains 2.4 x 1.8 cm"* (baseline) |
| 7  | Standard care (dressing + offloading) | *"Offloading boot provided"* |
| 14 | **No size reduction**, still 2.4 × 1.8 cm | *"No provider reassessment noted"* |
| 16 | Reassessment expected within 1–2 days — none | — |
| 28 | Worsened wound, ED visit + antibiotics | *"Purulent drainage, warm periwound"* |

**What the engine produces** (run it yourself — see [Quickstart](05-quickstart.md)):

- **CG-INF-002** *(delayed_action, urgency: urgent)* — the flagship stall rule. Fires because
  `standard_care_documented` AND `days_since_baseline ≥ 14` AND no size reduction
  (`size_trend_pct`) AND `reassessment_overdue`. Recommended action: reassess / evaluate for
  chronic wound infection.
- **CG-DET-001** *(deterioration)* — fires on the day-28 rapid growth. *(This is the fix from
  finding #1 — the ≥20% growth rule no longer goes blind after day 14.)*
- **CG-DFU-001** *(missing_action, routine)* — offloading device evaluation.
- **CG-DFU-002** *(missing_action, same_day)* — osteomyelitis imaging/referral workup.

Each finding carries the rule, the grounded excerpt(s), the expected action, the timing
window, exceptions checked, and its `gap_status` — the "done" contract from
[Purpose](01-purpose-and-vision.md#what-done-looks-like-for-an-alert). None of them can
touch the claim.

**The two dashboards the demo renders from this** (see `demo/src/views/`):
- **Care Gap Dashboard** — open high-risk gaps, avg expected window, top alert reason.
- **Closure Performance** — gaps closed %, median window, top barrier.
- **Episode Drilldown** — the Day 0→28 timeline + Evidence → Expected → Actual → Impact →
  Next-step chain, each row backed by a grounded excerpt.

Next: [Technical implementation →](03-technical-implementation.md)
