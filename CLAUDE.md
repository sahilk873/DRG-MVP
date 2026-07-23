# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Encounter Revenue Integrity** — an evidence-grounded, **deterministic** reference
implementation that reconstructs a clinical encounter once and reasons about it through
**two governed lenses on one spine** (`episode → ontology → detect → validate → route → close`):

- **`revenue_integrity`** — compares documentation against coding and billing and routes
  consequential exceptions to reviewers as *candidate* claim corrections.
- **`clinical_care_gap`** — identifies gaps in guideline-expected care over a longitudinal
  episode and routes them to a clinician. It is structurally walled off from claim mutation
  (empty `proposed_change`, human review required) and detected with deterministic
  temporal/co-occurrence operators. Never assigns a DRG, computes reimbursement, or bypasses review.

Both lenses are peer rule domains keyed by a `rule_domain` field, share the authoritative
wound-care ontology v3 (`1.3.0-draft`), and run through the same engine, review packet,
automation policy, and audit chain.

Three cooperating pieces, each with its own toolchain:

- `src/revenue_integrity/` — the deterministic Python engine (models, rules, grouper
  boundary, audit). Reproducible and model-independent. Python 3.11+.
- `agent/` — a Mastra/TypeScript service that does **semantic extraction only**. Node 22+.
- `demo/` — a React (Vite) interactive pitch app that renders engine output. Node 22+.

Everything shipped (rules, codes, prices, grouper) is **synthetic** — not for production
billing or clinical use.

## Commands

```sh
# One-time setup (each subproject has its own deps)
python -m venv .venv && source .venv/bin/activate && python -m pip install -e .
cd agent && npm ci && cd ..
cd demo && npm ci && cd ..

make verify          # FULL GATE: test + typecheck (agent) + demo-ui-check
make test            # PYTHONPATH=src python -m unittest discover -s tests -v
make typecheck       # cd agent && npm run check  (agent tests + tsc --noEmit)
make demo            # deterministic CLI demo (no model credential needed)
make demo-packet-check   # prove demo fixture still matches the engine (run after engine changes)
make bulk-demo       # exercise deterministic bulk ingestion end-to-end
```

Run a single Python test:
```sh
PYTHONPATH=src python -m unittest tests.test_engine.<Class>.<test>
```

Per-subproject checks (what CI runs — see `.github/workflows/ci.yml`):
- Python: `python -m compileall -q src`, `unittest discover -s tests`, `generate_demo_packet.py --check`, plus two `revenue-integrity` CLI smoke runs.
- `agent/`: `npm run check` (= `npm run test` via `node --test` + `npm run typecheck`) then `npm run build`.
- `demo/`: `npm run test` (vitest) + `npm run typecheck` + `npm run build`.

Console scripts (after `pip install -e .`): `revenue-integrity` (CLI in `cli.py`),
`revenue-integrity-ingest` (bulk profiler/adapter runner in `ingestion/cli.py`).

## The core invariant (read before changing anything)

**No language-model output may execute code, create or change a claim, assign a DRG,
compute reimbursement, or bypass review.** The agent never receives claim/charge/DRG/
payment fields as generation targets. It emits only evidence excerpts and
schema-constrained hypotheses; deterministic Python then verifies exact grounding,
merges immutable source fields, evaluates governed rules, and calls the grouper boundary.

This is enforced structurally, not by convention:
- The agent is provider-agnostic via a `provider/model` string (`MODEL_ID` env var). It
  does **not** import any provider SDK. Changing the model changes nothing downstream.
- `rules.py` evaluates a **declarative** JSON DSL (`SUPPORTED_OPERATORS`,
  `SUPPORTED_CHANGE_KEYS`) — there is no eval/exec path for generated code.
- The grouper/pricer is a `Protocol` boundary (`grouper.py`); the shipped
  `DeterministicDemoGrouper` is a fake (`version = "demo-...-not-for-billing"`).
- Rule packages fail closed unless `status` ∈ {`approved`, `approved-for-demo`}.

`docs/ARCHITECTURE.md` has the authoritative layer-by-layer "may use an LLM / produces
authoritative result" table. Read it before touching the trust boundary.

## Two separate trust boundaries

1. **Adapter factory (bulk onboarding)** — `ingestion/`. The Mastra *adapter-designer*
   agent sees only a **bounded profile** and proposes a draft declarative mapping DSL.
   The deterministic runtime (`ingestion/adapter.py`, `readers.py`, `transforms.py`)
   sees the full dataset and accepts only an approved, fingerprint-compatible adapter.
   Adapter discovery and adapter execution never share code paths. See
   `docs/ADAPTER_FACTORY.md`.

2. **Evidence extraction** — the Mastra *encounter-extractor* agent receives encounter
   timing, an ontology contract, and source documents, but **not** the claim. It returns
   evidence excerpts + a patient-specific ontology fragment + clinical assertions. The
   orchestrator then validates every excerpt is a literal source substring, checks
   ontology types/relations/lineage, attaches structural + provenance nodes outside the
   model, merges immutable claim fields, and runs an independent Python validator
   (`investigation.py`). Anything malformed fails closed.

## Python module map (`src/revenue_integrity/`)

- `models.py` — `EncounterCase`, `Claim`, `Finding`, assertion/documentation status enums; `RuleDomain` (`revenue_integrity` | `clinical_care_gap`), `GapDomain`, `GapStatus`, `ClinicalUrgency`; `SUPPORTED_SCHEMA_VERSION` (currently `2.0.0`), extraction-policy fields; dated episode/`SizeMeasurement` support.
- `engine.py` — `RuleEngine`; evaluates a validated `RulePackage` (of either domain) against a case. `ENGINE_VERSION`.
- `rules.py` — the declarative condition/change DSL (`Condition`, `ProposedChange`, `RuleScope`). Enforces the claim-mutation wall (clinical rules → empty `proposed_change` + `requires_human_review`) and defines the deterministic temporal/co-occurrence operators (`elapsed_days_gte`/`_lte`, `absent_within_days`, `pct_change_gte`/`_lte`, bounded `co_occurs`).
- `narrative.py` — deterministic clinician/reviewer-legible finding narratives; a finding carrying `gap_domain` renders the walled-off gap narrative, a `revenue_integrity` finding renders byte-identically to before.
- `ontology.py` — data-driven ontology definitions with inheritance + relation domain/range validation and versioned semantic digests. `AUTHORITATIVE_WOUND_CARE_ONTOLOGY` / `load_authoritative_wound_care_ontology()` point at v3. Builtins under `data/` (v1/v2/v3 retained for lineage).
- `grouper.py` — `Grouper` protocol + demo grouper. Integer-cent math only.
- `investigation.py` — typed hypothesis→finding trust boundary; lineage checks. Model-framework-independent.
- `orchestration.py` — governed agent-to-review handoff (`run_investigation`).
- `automation.py` — deterministic exception policy: revenue tiers (suppress / enrich / auto-route / quick-confirm / focused-review / escalate) plus a separate `CARE_GAP` lane (urgency→tier map, `route_to_care_team`) and a hash-covered `gap_worklist` metric. `AUTOMATION_SCHEMA_VERSION` `1.2.0`.
- `routing.py`, `workflow.py`, `promotion.py` — routing outbox (with a dedicated `care_gap_alert` lane), reviewer workflow, hypothesis promotion. `workflow.py` also holds the gap-closure lifecycle (`GapClosureAction` close/exception/withdraw, `CARE_GAP_COORDINATOR` role, hash-chained `GapClosureRecord`, `GAP_CLOSURE_SCHEMA_VERSION` `1.0.0`).
- `review_packet.py` — versioned reviewer-UI handoff (`REVIEW_PACKET_SCHEMA_VERSION` `3.5.0`; optional `clinical_care_gap` finding fields); sets `claim_mutation_allowed=false`.
- `audit.py` — canonical JSON, sha256, hash-chained audit records.
- `security.py`, `integrations.py`, `financial.py`, `evaluation.py`, `agents.py` — model/data-plane policy, capability descriptors, monetary sim, eval harness, agent contracts.
- `ingestion/` — bulk profiling (`profiling.py`), readers (CSV/JSON/JSONL/XLSX), declarative adapters, provenance.

Shipped rule packages under `rules/`: `wound_care_v1.json`, `wound_care_v2.json` (revenue_integrity) and `wound_care_gaps_v1.json` (clinical_care_gap; 46 rules, flagship `CG-INF-002`, fires on `examples/case_diabetic_foot_ulcer_episode.json`). Demo care-gap surfaces live in `demo/src/views/CareGaps.tsx` and `demo/src/views/EpisodeDrilldown.tsx`.

Agent code lives in `agent/src/agents/`: `encounter-extractor.ts`, `adapter-designer.ts`,
`billing-reconciler.ts`, `investigation-critic.ts`. Entrypoint `agent/src/extract.ts`;
Zod schemas in `schema.ts`/`onboarding/schema.ts`.

## Governance rules for changes (from CONTRIBUTING.md)

These are project-specific and easy to violate — enforce them:

- **Version everything governed**: bump ontology / adapter / rule / schema versions on any
  breaking contract, ontology-binding, or transformation change; require new approval.
- **Test matrix per change type**: rule changes need positive + negative + contradictory +
  malformed-input tests; reader/adapter changes need malformed-file, schema-drift,
  unlinked-row, unmapped-value, and resource-budget tests.
- The review packet is a **versioned public contract** — update its schema, Python tests,
  browser validation, generated fixture, and docs **together**, then run
  `make demo-packet-check` so the demo cannot drift from the engine.
- Preserve deterministic IDs, integer-cent money, and complete version provenance.
- Never commit PHI, credentials, or licensed terminology/customer data. Keep clinical
  decision-support sources separate from revenue-integrity packages.
- Every executable rule must carry an ontology subject scope and retain
  subject/assertion/evidence lineage in findings.

## Running the agent / bulk pipeline manually

```sh
# Extraction (needs a model + API key; provider-agnostic via MODEL_ID)
cd agent && cp .env.example .env
MODEL_ID=anthropic/<model> npm run extract -- \
  ../examples/source_bundle_pressure_injury.json ../output/encounter-case.json \
  ../src/revenue_integrity/data/wound_care_ontology_v1.json

# Bulk ingestion (deterministic; no model needed)
revenue-integrity-ingest profile examples/bulk/clinic_alpha --output output/profile.json
revenue-integrity-ingest run examples/bulk/clinic_alpha \
  examples/adapters/clinic_alpha_wound_care_v1.json \
  --output-directory output/source-bundles --report output/run.json
```

Docs worth reading: `docs/ARCHITECTURE.md` (trust boundary), `docs/ADAPTER_FACTORY.md`,
`docs/ONTOLOGY.md` (domain-extension contract), `docs/REVIEW_PACKET.md`,
`docs/AUTOMATION.md`, `docs/REVIEW_WORKFLOW.md`, `docs/ITERATIVE_REVIEW.md`.
