# 1 · Purpose & vision

## The problem

Two costly problems hide in the same clinical record, and both are usually attacked with
shallow, alert-happy tooling:

1. **Revenue integrity** — the claim (diagnoses, procedures, charges, DRG) drifts from what
   the documentation actually supports. Under-coding leaves money on the table; unsupported
   billing is a compliance risk.
2. **Gaps in care** — guideline-expected care (a reassessment, a lab follow-up, a referral,
   an escalation) was **missing, delayed, or incompletely followed through** across a
   patient's episode, and nobody closed the loop.

The naive version of either is a keyword-matching alert generator that buries clinicians in
false positives. This project is the opposite: **prove the finding with grounded evidence, or
don't surface it.**

## The narrative: "Clinical Gaps-in-Care Intelligence"

The guiding standard (from the product methodology deck):

> **Surface a gap only when the longitudinal record proves that an expected, clinically
> appropriate action was missing, delayed, or incomplete — after ruling out justified
> exceptions (refusal, contraindication, transfer, hospice, outside care, documented
> clinical judgment).**

The litmus test for any alert: *can a clinician see the trigger, see the supporting
timeline, and agree the expected action did not occur?* That is exactly the
**evidence-grounding** discipline the revenue lens already uses, applied to a **temporal,
clinical** domain.

## Why two lenses on one spine

Rather than a second product, the clinical care-gap capability is a **peer lens** that
reuses every safety mechanism the revenue engine already has:

- literal-substring **evidence grounding**,
- a governed, **versioned ontology** with digests,
- a **declarative rule DSL** with no code-eval path,
- **fail-closed** status gating,
- a **hash-chained audit** trail,
- **human-in-the-loop** review packets,
- automation **tiering** and a routing **outbox**.

One spine means one place to reason about correctness, and no drift between two parallel
pipelines. The lenses are distinguished by a single `rule_domain` field
(`revenue_integrity` | `clinical_care_gap`).

## The trust boundary (why it's non-negotiable)

A care-gap rule recommends **clinical actions** ("order a wound culture", "vascular
referral") and consumes multi-encounter PHI. That is a materially different risk surface
from a claim-mutation rule. So the design draws a hard line:

- **The core invariant** — no language-model output may execute code, create/change a claim,
  assign a DRG, compute reimbursement, or bypass review. See
  [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) for the authoritative layer-by-layer table.
- **The claim-mutation wall** — a `clinical_care_gap` rule is *structurally* barred from
  carrying a claim change; a `revenue_integrity` rule is barred from carrying clinical
  action fields. This is enforced at four independent layers (see
  [technical implementation](03-technical-implementation.md#the-claim-mutation-wall)).
- **Analytics identify gaps; clinicians decide.** Care-gap findings always require human
  review and can only be closed through a dedicated, role-gated, audited lifecycle.

## Who this is for

- **Reviewers / coders / CDI / clinicians** consume the review packet and worklists.
- **Rule authors / clinical governance** write and approve governed rule packages.
- **Engineers** extend the engine, ontology, agent, and demo.

## What "done" looks like for an alert

Every surfaced item carries: the **rule**, the **grounded evidence**, the **expected
action**, the **timing window**, the **exceptions checked**, and the **resolution status**.
If any of those can't be produced from the record, the item fails closed rather than
guessing.

Next: [How it works, end-to-end →](02-how-it-works.md)
