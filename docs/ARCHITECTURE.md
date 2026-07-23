# Architecture and trust boundaries

## One spine, two lenses

The platform is a single deterministic spine — **episode → ontology → detect → validate → route → close** — carrying two governed peer rule domains that never share a code path at the point of decision:

- **Revenue integrity** (`revenue_integrity`): compares documentation against coding and billing and proposes evidence-grounded candidate claim corrections for human review.
- **Clinical care gaps** (`clinical_care_gap`): identifies gaps in guideline-expected care over a longitudinal episode and routes them to a clinician. It never mutates a claim, assigns a DRG, computes reimbursement, or bypasses review.

A rule package declares its domain via a required `rule_domain` field (`revenue_integrity` | `clinical_care_gap`). The two domains reference the **same** authoritative wound-care ontology (v3, `1.3.0-draft`) and run through the same engine, review packet, automation policy, and audit chain — but they are walled off from one another at rule-parse time (see "The claim-mutation wall"). Adding the clinical lens added a peer domain, not an engine branch.

## System responsibility split

| Layer | Implementation | May use an LLM? | Produces an authoritative billing result? |
|---|---|---:|---:|
| Source discovery | Bounded profiler and Mastra adapter designer | Draft mapping only | No |
| Source ingestion | Approved declarative adapters and reader registry | No | No |
| Semantic extraction (revenue) | Mastra agents; documents only | Yes | No |
| Longitudinal / episode extraction (care gaps) | Mastra agents; timing + prior assessments as evidence only | Yes | No |
| Ontology validation | Versioned class/relation definitions | No | No |
| Evidence validation | Zod plus Python domain validation | No | No |
| Reconciliation (`revenue_integrity` rules) | Versioned declarative rules | No | Candidate only |
| Care-gap detection (`clinical_care_gap` rules) | Versioned declarative rules; deterministic temporal/co-occurrence operators | No | Analytics alert only |
| Grouping and pricing | Licensed grouper/pricer adapter | No | Simulation |
| Review-packet assembly | Versioned deterministic projection | No | Candidate / alert only |
| Exception automation | Versioned deterministic policy and routing outbox | No | Operational route only |
| Compliance criticism | Mastra agent plus deterministic checks | Yes | No |
| Final disposition (claim change) | Threshold policy and focused reviewer | No | Institution-defined |
| Gap closure | Authorized clinician (`care_gap_coordinator`); hash-chained closure record | No | Clinical, never a claim |

The model is provider-agnostic because Mastra receives the model as a `provider/model` string. The system is behaviorally model-aware: every production assertion must also record the model, prompt, extraction-policy and terminology versions in the audit store. Adapter discovery and execution are separate: the model sees only a bounded profile and proposes a draft DSL; the deterministic data plane sees the full bulk input and accepts only an approved, fingerprint-compatible adapter.

## The claim-mutation wall

The two rule domains are separated structurally in `rules.py`, not by convention:

- A `clinical_care_gap` rule must carry an **empty** `proposed_change` payload — a gap rule that tries to mutate a claim is rejected at parse time.
- A `clinical_care_gap` rule must set `requires_human_review = true`; a gap finding can never bypass review.
- A `revenue_integrity` rule carrying any clinical action field (`gap_domain`, `expected_action`, `timing_window_days`, …) is rejected.

So the clinical lens can surface an alert and route it to a clinician, but the structural boundary makes it impossible for a care-gap rule to assign a DRG, change a code, compute reimbursement, or auto-clear an item. Care-gap findings ride a dedicated `care_gap_alert` routing lane, never the revenue lanes, and are closed by an authorized clinician through a hash-chained closure record (see `docs/AUTOMATION.md`), never by a claim workflow.

## Deterministic (LLM-free) detection operators

Care-gap detection reasons about time and co-occurrence with declarative operators evaluated entirely by Python — no language-model output participates in whether a rule fires:

- `elapsed_days_gte` / `elapsed_days_lte` — days elapsed since a dated observation crosses a bound.
- `absent_within_days` — an expected action is missing within a window.
- `pct_change_gte` / `pct_change_lte` — a measured quantity (e.g. wound size) changed by at least/at most a percentage across dated assessments.
- `co_occurs` (with an optional bounded `window_days`) — two or more sub-conditions hold together within a bounded time window.

These live in the same `rules.py` declarative DSL as the revenue operators; there is no eval/exec path. Given the same episode they always yield the same findings.

## Longitudinal / episode extraction policy

For the clinical lens the extraction agent works over a longitudinal episode: multiple dated wound assessments and prior clinical observations. The agent receives encounter/episode timing and prior assessments **only as grounded evidence to normalize** — never the claim, DRG, payment fields, or any gap decision. It emits evidence excerpts, a patient-specific ontology fragment (including dated `SizeMeasurement` quantities), and clinical assertions; deterministic Python then verifies exact grounding, computes the temporal/co-occurrence facts, evaluates the governed `clinical_care_gap` rules, and decides whether a gap exists. Whether an action was "late" or "missing" is a deterministic computation over validated dated evidence, not a model judgement.

## Core invariant

No language-model output can directly execute code, recreate or change a claim, assign a DRG, calculate reimbursement, decide a care gap, or bypass required review. The agent never receives claim/DRG/payment fields or gap decisions as generation targets. It emits evidence excerpts and schema-constrained hypotheses; deterministic orchestration verifies exact grounding, merges immutable source fields, evaluates governed rules and calls the grouper boundary. This holds identically for both lenses: revenue-integrity rules can only propose a candidate claim change for review, and clinical-care-gap rules can only surface an analytics alert for a clinician.

## Evidence semantics

Every clinical assertion contains:

- a subject ID linking it to a typed ontology entity;
- normalized concept and attributes;
- present, absent, uncertain or historical status;
- explicit, inferred, conflicted or absent documentation status;
- extraction confidence;
- supporting evidence IDs;
- contradicting evidence IDs.

Evidence records preserve the source document, author role, time and minimal exact excerpt. The current boundary verifies each excerpt is a literal substring and source metadata is unchanged. In production, evidence IDs should point to access-controlled source objects rather than duplicating unrestricted PHI.

Deterministically projected evidence instead carries a source locator: adapter ID/version, resource, relative path, worksheet when applicable, row number, source-record ID and contributing field names. This lineage is matched against ingestion provenance. A model is explicitly forbidden from creating source locators.

Ontology relations carry the same assertion status, documentation status, confidence and evidence references. The definition validator enforces concrete classes, inheritance-aware relation domains and ranges, evidence requirements, unique IDs, and exact ontology ID/version/digest compatibility. Revenue rule packages declare the semantic ontology fingerprint and typed subject scopes against which they were reviewed.

Source and extraction volume are governed by a configurable Mastra `ExtractionPolicy`; Python independently enforces case-validation limits. The resolved extraction policy is recorded in provenance so an accepted graph can be reproduced and audited under the same operational boundary.

### Deterministic derived rule fields

The rule engine exposes a few read-only fields on each assertion payload that are computed by Python, never supplied by a model: `evidence_count`, `contradicting_evidence_count`, and `has_contradicting_evidence`. Declarative rules can reason about evidence strength and contradiction (for example, only proposing a claim change when `has_contradicting_evidence` is false) without any new schema field or model trust. They are derived at evaluation time from the already-validated evidence lineage.

### Ontology-subgraph retrieval (token efficiency)

The extraction agent does not need the full ontology contract inlined on every call. `selectOntologySubgraph` deterministically selects only the classes relevant to the document term set (plus their full ancestor chain, the relations whose endpoints are selected, and the value-sets those classes reference) and the extractor can send that scoped contract instead of all classes. This is a prompt hint only: `validateOntologyGraph` still validates the returned graph against the full definition, so retrieval can shrink what the model is shown but can never widen what the deterministic layer accepts. The default remains the full contract; document-scoped retrieval is opt-in.

### Data-driven DRG grouping

The demo grouper is defined by a governed, versioned `demo_grouping_v1.json` artifact (base rate + MCC/CC severity tiers + a diagnosis-severity table) rather than hardcoded branches. It remains a deterministic fake (its version contains `not-for-billing`), computes integer-cent payments, and emits an ordered derivation trace (severity resolution → tier selection → pricing) so a reviewer can see exactly why a DRG and payment were produced. A licensed grouper still plugs in through the same `Grouper` protocol.

## Human review policy

Human review is exception-based. The system should automatically clear encounters with no supported discrepancy and suppress disproven hypotheses. A reviewer receives a completed packet only when an unresolved action is material, ambiguous, institutionally controlled or compliance-sensitive.

Initial production policy should require approval for every claim-affecting change. Straight-through processing can be considered later only for narrowly scoped actions supported by prospective validation and institutional governance.

The deterministic engine emits a versioned review packet containing the exact evidence graph, immutable claim snapshot, findings, component versions and audit hashes used for evaluation. A separately hashed automation plan binds the exact packet and selects consolidation, enrichment, safe operational routing, or human exception review. Reviewer applications consume these contracts rather than reimplementing rule or payment logic. The packet explicitly sets `claim_mutation_allowed` to false; reviewer decisions and any later claim workflow require separate authenticated services and audit events. See [REVIEW_PACKET.md](REVIEW_PACKET.md) and [AUTOMATION.md](AUTOMATION.md).

## Planned Mastra workflow

The implementation contains an extraction agent and a separately bounded adapter-designer agent. Subsequent agents should be composed in a Mastra workflow with explicit inputs and outputs:

1. assemble the relevant source bundle;
2. extract assertions;
3. normalize terminology;
4. identify contradictions and missing documentation;
5. ask a compliance critic to challenge candidates;
6. pass only validated structured data to the deterministic engine;
7. optionally summarize the deterministic packet for readability without changing its evidence, controls, codes, DRGs, or financial fields.

Agent debate is supporting evidence, not consensus truth. Disagreement increases escalation priority rather than authorizing a code.
