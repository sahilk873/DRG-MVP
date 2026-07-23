# Onboarding Guide — start here

> A fast, self-contained tour of **Encounter Revenue Integrity**: what it is, why it
> exists, how it works end-to-end, and where every moving part lives in the code.
> Read top-to-bottom in ~30 minutes, or jump to the section you need.

Everything shipped here (rules, codes, prices, grouper, patient data) is **synthetic**.
This is a reference implementation, **not for production billing or clinical use**.

---

## 60-second summary

The system reconstructs a clinical encounter **once** and reasons about it through **two
governed lenses that ride one shared spine**:

```
             ┌─────────────────────────────────────────────────────────────┐
             │   ONE SPINE:  episode → ontology → detect → validate →        │
             │               route → close   (deterministic Python)         │
             └─────────────────────────────────────────────────────────────┘
                    ▲                                        ▲
     revenue_integrity lens                        clinical_care_gap lens
   "was the claim coded/billed         "was guideline-expected care missing,
    to match the documentation?"        delayed, or incomplete over the episode?"
   → candidate claim corrections       → clinician-routed care-gap alerts
                                          (structurally walled off from claims)
```

The hard guarantee (the **core invariant**): *no language-model output may execute code,
create or change a claim, assign a DRG, compute reimbursement, or bypass review.* Models
propose only grounded evidence and hypotheses; deterministic Python verifies, evaluates
rules, and routes to humans. The `clinical_care_gap` lens is additionally **walled off
from claim mutation** — a care-gap rule literally cannot change a claim.

---

## The reading map

| # | Read this | You'll learn |
|---|-----------|--------------|
| 1 | [Purpose & vision](01-purpose-and-vision.md) | The problem, the "gaps-in-care" narrative, why two lenses, why the trust boundary |
| 2 | [How it works (end-to-end)](02-how-it-works.md) | The six-stage spine, traced through the Diabetic Foot Ulcer worked example |
| 3 | [Technical implementation](03-technical-implementation.md) | Architecture, module map, versions, the wall's enforcement points, data flow |
| 4 | [The clinical_care_gap domain](04-clinical-care-gap-domain.md) | The rule DSL, gap taxonomy, temporal operators, the 46-rule library, closure lifecycle — and how to author a rule |
| 5 | [Quickstart & verification](05-quickstart.md) | Set up, run the tests/demo/CLI, the five gates, regenerate fixtures |
| 6 | [Glossary](06-glossary.md) | Every term and acronym in one place |

## How this relates to the existing reference docs

This guide is the **fast on-ramp**. The authoritative, deeper references already in `docs/`
are linked throughout:

- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — the authoritative trust-boundary table
- [`docs/ONTOLOGY.md`](../ONTOLOGY.md) — the ontology contract & versioned digests
- [`docs/REVIEW_PACKET.md`](../REVIEW_PACKET.md) — the versioned reviewer-UI contract
- [`docs/AUTOMATION.md`](../AUTOMATION.md) — the exception/tiering policy & worklist metrics
- [`docs/REVIEW_WORKFLOW.md`](../REVIEW_WORKFLOW.md) — reviewer decisions & closure
- [`docs/ADAPTER_FACTORY.md`](../ADAPTER_FACTORY.md) — the bulk-ingestion trust boundary
- [`docs/EVALUATION.md`](../EVALUATION.md) — the precision/recall eval harness

## The three cooperating pieces

| Piece | Path | Role | Toolchain |
|-------|------|------|-----------|
| Deterministic engine | `src/revenue_integrity/` | Models, rules, grouper boundary, audit. Reproducible, model-independent. | Python 3.11+ |
| Extraction agent | `agent/` | **Semantic extraction only** — grounded evidence + hypotheses. | Node 22+ (Mastra/TS) |
| Demo app | `demo/` | Renders engine output as an interactive pitch. | Node 22+ (React/Vite) |
