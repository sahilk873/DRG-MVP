# Deterministic exception automation

The automation layer minimizes human work after deterministic evaluation. It consumes typed `Finding` objects and the exact full-packet hash; it never consumes raw provider files, invokes a model, edits a rule, changes a code, or mutates/submits a claim. `AUTOMATION_SCHEMA_VERSION` is `1.3.0`, which adds a dedicated clinical-care-gap lane and a coordinator worklist rollup (below) without changing revenue routing.

## Dispositions

Each finding receives one policy-versioned disposition. Contradictions and compliance-sensitive conditions take precedence over every other tier; insufficient evidence is never auto-routable even if a proposed change is present:

- `suppressed`: only a no-opportunity or an exact semantic duplicate. A duplicate retains its primary finding ID.
- `needs_enrichment`: evidence is insufficient, so the item returns to data/evidence acquisition rather than disappearing.
- `auto_routed`: a supported, high-confidence, bounded-impact operational item can be placed in a governed queue without a person.
- `quick_confirm`: a high-confidence material DRG hypothesis receives a prepared action and a short confirmation.
- `focused_review`: lower confidence or documentation clarification needs targeted expertise.
- `escalated`: contradictory evidence, compliance sensitivity, negative impact, or unavailable impact bypasses ordinary review budgets.

Unknown financial impact is never represented as zero. Review budgets defer supported work but never suppress it. All ordering, fingerprints, IDs, policy digests, metrics, and plan hashes are deterministic.

## Transparent priority score

Each finding's `priority_score` is the sum of an itemized, integer `priority_components` breakdown:

- `tier_weight` — the disposition tier's base weight;
- `confidence_weight` — `int(confidence * 10000)`;
- `impact_weight` — dollars of absolute impact (`abs(cents) // 100`), **uncapped** so a six-figure recovery outranks a routine one instead of saturating at a shared ceiling; unknown impact uses a neutral floor;
- `urgency_weight` — a deterministic integer urgency signal (formula below).

The breakdown is emitted so reviewers can see *why* an item ranks where it does. Sorting remains `(-priority_score, finding_id)`.

### `urgency_weight` formula

`urgency_weight` is pure integer arithmetic over the deterministic disposition and the immutable financial snapshot — no language-model output participates:

```
urgency_weight = tier_rank * 1000
               + min(abs(impact_cents) // 100_000, 25) * 100    # bounded $1,000-per-step ramp
               + 10_000  if the finding is bound to a denied / at-risk charge line
```

- `tier_rank` — disposition severity: `escalated` 4, `quick_confirm`/`focused_review` 3, `auto_routed`/`needs_enrichment` 1, `suppressed` 0.
- Impact ramp — magnitude nudges urgency without swamping the (uncapped) `impact_weight`; it caps at 25 steps (2,500). Unknown impact contributes a neutral `500`, never `0`.
- Denial-exposure bump — a fixed `10_000` (see below).

Suppressed and exact-duplicate items always carry `urgency_weight = 0`.

## Denial exposure

When the case carries a `FinancialSnapshot`, the charge lines a payer has **denied or placed at risk** (`denials[].line_ids`) are read (never mutated). A finding bound to one of those lines (via its read-only `charge_line_refs`) earns:

- a `denial_exposure` reason code — a governed high-urgency routing signal; and
- a `+10,000` `urgency_weight` bump, raising it in the review queue.

Denial exposure only **raises** urgency. It can never move a review-required finding to `suppressed` or bypass a person: the deterministic tier classification (including no-opportunity suppression and exact-duplicate consolidation) runs first, and the denial signal is layered on afterward only for findings that still route to a human. The core invariant holds — no financial signal authorizes a claim change.

## Reviewer-effort rollup

`metrics.reviewer_effort` is a deterministic, clearly-labelled productivity estimate covered by the plan hash:

- `estimated_review_seconds` — projected human time for the `review_now` queue;
- `seconds_avoided_estimate` — counterfactual human time saved by suppressed + auto-routed items;
- `no_touch_rate` — share of input findings the policy cleared without a person (suppressed + auto-routed + needs-enrichment);
- `no_touch_finding_count`, `consolidated_duplicate_count`, and an explicit `is_estimate: true` flag.

These are operational estimates only. They never authorize a claim change.

## Execution and feedback

`SQLiteRoutingOutbox` durably and idempotently enqueues only `auto_routed` items. Tasks contain scoped identifiers, hashes, queue, and action—not clinical excerpts or a claim payload. A deployment-specific adapter can deliver pending tasks and mark them delivered. Delivery is operational routing, not coding approval or claim submission.

The included SHA-256 values provide deterministic content addressing and corruption detection; they are not signatures and do not authenticate an untrusted caller. A production API must resolve packet and plan digests from an authenticated immutable artifact store or verify an institution-managed signature/MAC before invoking the workflow or outbox.

Human decisions are restricted to `review_now_finding_ids` and the per-finding allowed actions. Each decision binds the exact packet, automation plan, policy, actor scope, structured reason code, and idempotency key. `summarize_decision_feedback` produces offline acceptance and dismissal labels. Feedback must pass a governed validation/change process before it changes thresholds, rules, prompts, or ontology definitions.

## Clinical care gaps (`clinical_care_gap` lane)

The same automation policy also tiers findings from the second governed peer domain, but on a fully separate track. A `clinical_care_gap` finding is an analytics alert; it can never mutate a claim, assign a DRG, or bypass review, and it rides the dedicated `care_gap` queue (`AutomationQueue.CARE_GAP`) rather than any revenue queue. When no gap findings are present the whole gap track is a byte-identical no-op, so revenue-only plans are unchanged.

### Deterministic urgency → tier

Gap tiering is driven by the finding's `alert_urgency`, independent of the revenue tiering:

| `alert_urgency` | Tier | Reason code |
|---|---|---|
| `emergent` / `urgent` | `escalated` | `emergent_care_gap` |
| `same_day` | `focused_review` | `same_day_care_gap` |
| `routine` | `auto_routed` (to the `care_gap` alert lane) | `routine_care_gap` |

A routine gap that lacks a recommended action is held for enrichment (`gap_needs_action`) instead of being auto-routed. A gap whose documented exception is confirmed and undisputed is downgraded to a suppressed exception (`gap_exception_confirmed`) rather than routed to a person. The single governed clinician-facing route action is `route_to_care_team`.

### `metrics.gap_worklist` rollup

`metrics.gap_worklist` is a deterministic coordinator rollup derived by Python from the already-validated gap findings and their tiering; it is placed inside `plan_body` so it is covered by `plan_hash` (tampering breaks plan integrity), and carries an explicit `is_estimate: true`:

- `total_gaps`, `open_high_risk_gaps` (open, high-urgency, still routed to a person);
- `avg_expected_window_days` — the mean of the rule-**configured** `timing_window_days` across gaps that carry one. This is the intended action window, **not** an observed expected→actual lateness (no observed-delay datum exists at plan-build time). Fractional windows (e.g. `0.5`) are preserved, not truncated;
- `gaps_closed_pct` and `median_closure_days` (the configured window on closed/exception gaps, fractional-safe);
- `top_alert_reason` and `top_barrier` (deterministic mode, ties broken by sort order).

These are operational estimates only; they never authorize a claim change or close a gap.

### Gap closure lifecycle

A surfaced gap moves through a `GapStatus` lifecycle: `open` → `routed` → one of `closed` | `exception` | `withdrawn`. The terminal decision is made by an authorized clinician — a coordinator with the `care_gap_coordinator` role (or `admin` break-glass), never by an automated policy. The three coordinator actions are `close`, `exception`, and `withdraw`.

Each decision is written as an immutable `GapClosureRecord` (`GAP_CLOSURE_SCHEMA_VERSION` `1.0.0`) hash-chained per `(tenant, workspace, packet)`, exactly like the revenue decision chain: it binds the finding, the packet and automation-plan hashes, the actor and roles, a structured reason, an optional `barrier_code`, and an idempotency key. None of these actions touch a claim, a DRG, or a payment; they only fold the coordinator's decision into a `GapStatus` change on the gap finding. See [the governed review workflow](REVIEW_WORKFLOW.md).

## Required production controls

- Calibrate thresholds by specialty, payer, facility, action type, and prospective/retrospective workflow.
- Validate on representative positives, negatives, contradictions, missing data, and distribution shifts.
- Monitor no-touch rate alongside precision, false-negative sampling, override rate, unresolved enrichment, deferred age, and downstream reversals.
- Use a managed transactional database, authenticated delivery workers, least-privilege queue adapters, encryption, retention, and alerting.
- Keep automatic claim mutation and submission outside this contract unless separately authorized, clinically validated, and audited.
