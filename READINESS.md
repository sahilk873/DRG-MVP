# MVP readiness

This document states, unambiguously, what "the MVP is built and ready to go" means for this
project and what it does **not** yet mean. Everything shipped here is **synthetic** and **not for
production billing or clinical use**.

## What "ready to go" means here — and is DONE

The **demonstrable reference MVP** is complete, runnable, and verified:

- **Runs end to end today.** `cd demo && npm run dev` launches the pitch app; the deterministic
  Python engine, the eval harness, and the bulk-ingestion CLI all run from the command line.
- **Core proposition is real, not a stub.** DRG grouping is a data-driven, versioned deterministic
  grouper (`data/demo_grouping_v1.json`) with base rate + MCC/CC severity tiers and a hash-covered
  derivation trace. POA and DRG severity tier are first-class ontology concepts. Two governed rule
  packages evaluate off the ontology, including the expressive operators (`between`, `starts_with`,
  `count_gte`, derived contradiction fields).
- **Trust boundary is enforced structurally and provably.** No model output can execute code, create
  or change a claim, assign a DRG, compute reimbursement, or bypass review; an executable invariant
  test suite locks this in, and the review packet sets `claim_mutation_allowed = false`.
- **Reviewer explainability.** Every finding carries evidence lineage, a governed rule trace, and a
  step-by-step grouper derivation, surfaced in the demo (lineage rail, "why the DRG changed" panel,
  always-visible trust strip) across two packet-backed cases.
- **Measurement.** A signed precision/recall/F1 backtest (`make eval`) runs over a labelled gold set
  covering both rule packages.
- **Realistic notional onboarding.** A synthetic, EHR-warehouse-shaped provider export
  (`examples/bulk/mercy_regional/`, 6 encounters) is profiled, mapped through an approved declarative
  adapter, and flowed all the way to governed findings — proving the ingestion trust boundary on
  realistic data. See `docs/NOTIONAL_DATA.md`.
- **Verified.** Full gate green: 161 Python tests, 38 agent tests, 11 demo tests, both demo fixtures
  fresh, eval thresholds enforced, demo type-checks and builds. Live smoke-tested in a browser.

Run the full gate:

```sh
PYTHONPATH=src python -m unittest discover -s tests   # (use python 3.11+)
PYTHONPATH=src python scripts/generate_demo_packet.py --check
PYTHONPATH=src python -m revenue_integrity.eval_cli examples/evaluation/gold_manifest.json --enforce
cd agent && npm run check
cd demo && npm run test && npm run typecheck && npm run build
```

## What is NOT yet done — and why it needs external inputs

The following gates are required before a **production pilot with a real client**, and each is blocked
on an input that cannot be produced from inside this repository. They are intentionally out of scope
for the synthetic reference MVP:

| Gate | Blocked on |
|---|---|
| Real DRG assignment/pricing | A **licensed grouper/pricer** (e.g. MS-DRG / APR-DRG) behind the existing `Grouper` protocol. The shipped grouper is a deliberate fake (`version` contains `not-for-billing`). |
| Real data path | **FHIR/HL7 and 837 claim adapters** and **X12 835 remittance/denial (CARC/RARC)** parsing, plus a **de-identified client export** to onboard. (Tabular CSV/JSON/JSONL/XLSX exports already work end to end — see `docs/NOTIONAL_DATA.md`.) |
| "What we would have caught" backtest on client data | A **BAA + de-identified historical encounters** from a design partner. The mechanism exists (ingestion + eval); it needs their data. |
| Institution-approved content | **Clinically validated rules, licensed terminology, and payer contracts**, distributed separately. |
| Production operations | Auth, real multi-tenancy, a managed transactional DB, deployment, monitoring, and the security controls in `SECURITY.md`. |

## Bottom line

The **reference/demo MVP is built and ready to demo today.** It is **not** a production-billing system,
and by design cannot become one without the licensed grouper, real data adapters, institution-approved
content, and productionization above. See `docs/ARCHITECTURE.md` for the trust boundary and
`docs/ITERATIVE_REVIEW.md` for the remaining production roadmap.
