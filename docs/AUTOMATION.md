# Deterministic exception automation

The automation layer minimizes human work after deterministic evaluation. It consumes typed `Finding` objects and the exact full-packet hash; it never consumes raw provider files, invokes a model, edits a rule, changes a code, or mutates/submits a claim.

## Dispositions

Each finding receives one policy-versioned disposition. Contradictions and compliance-sensitive conditions take precedence over every other tier; insufficient evidence is never auto-routable even if a proposed change is present:

- `suppressed`: only a no-opportunity or an exact semantic duplicate. A duplicate retains its primary finding ID.
- `needs_enrichment`: evidence is insufficient, so the item returns to data/evidence acquisition rather than disappearing.
- `auto_routed`: a supported, high-confidence, bounded-impact operational item can be placed in a governed queue without a person.
- `quick_confirm`: a high-confidence material DRG hypothesis receives a prepared action and a short confirmation.
- `focused_review`: lower confidence or documentation clarification needs targeted expertise.
- `escalated`: contradictory evidence, compliance sensitivity, negative impact, or unavailable impact bypasses ordinary review budgets.

Unknown financial impact is never represented as zero. Review budgets defer supported work but never suppress it. All ordering, fingerprints, IDs, policy digests, metrics, and plan hashes are deterministic.

## Execution and feedback

`SQLiteRoutingOutbox` durably and idempotently enqueues only `auto_routed` items. Tasks contain scoped identifiers, hashes, queue, and action—not clinical excerpts or a claim payload. A deployment-specific adapter can deliver pending tasks and mark them delivered. Delivery is operational routing, not coding approval or claim submission.

The included SHA-256 values provide deterministic content addressing and corruption detection; they are not signatures and do not authenticate an untrusted caller. A production API must resolve packet and plan digests from an authenticated immutable artifact store or verify an institution-managed signature/MAC before invoking the workflow or outbox.

Human decisions are restricted to `review_now_finding_ids` and the per-finding allowed actions. Each decision binds the exact packet, automation plan, policy, actor scope, structured reason code, and idempotency key. `summarize_decision_feedback` produces offline acceptance and dismissal labels. Feedback must pass a governed validation/change process before it changes thresholds, rules, prompts, or ontology definitions.

## Required production controls

- Calibrate thresholds by specialty, payer, facility, action type, and prospective/retrospective workflow.
- Validate on representative positives, negatives, contradictions, missing data, and distribution shifts.
- Monitor no-touch rate alongside precision, false-negative sampling, override rate, unresolved enrichment, deferred age, and downstream reversals.
- Use a managed transactional database, authenticated delivery workers, least-privilege queue adapters, encryption, retention, and alerting.
- Keep automatic claim mutation and submission outside this contract unless separately authorized, clinically validated, and audited.
