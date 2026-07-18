# Architecture and trust boundaries

## System responsibility split

| Layer | Implementation | May use an LLM? | Produces an authoritative billing result? |
|---|---|---:|---:|
| Source discovery | Bounded profiler and Mastra adapter designer | Draft mapping only | No |
| Source ingestion | Approved declarative adapters and reader registry | No | No |
| Semantic extraction | Mastra agents; documents only | Yes | No |
| Ontology validation | Versioned class/relation definitions | No | No |
| Evidence validation | Zod plus Python domain validation | No | No |
| Reconciliation | Versioned declarative rules | No | Candidate only |
| Grouping and pricing | Licensed grouper/pricer adapter | No | Simulation |
| Review-packet assembly | Versioned deterministic projection | No | Candidate only |
| Exception automation | Versioned deterministic policy and routing outbox | No | Operational route only |
| Compliance criticism | Mastra agent plus deterministic checks | Yes | No |
| Final disposition | Threshold policy and focused reviewer | No | Institution-defined |

The model is provider-agnostic because Mastra receives the model as a `provider/model` string. The system is behaviorally model-aware: every production assertion must also record the model, prompt, extraction-policy and terminology versions in the audit store. Adapter discovery and execution are separate: the model sees only a bounded profile and proposes a draft DSL; the deterministic data plane sees the full bulk input and accepts only an approved, fingerprint-compatible adapter.

## Core invariant

No language-model output can directly execute code, recreate or change a claim, assign a DRG, calculate reimbursement, or bypass required review. The agent never receives claim fields as generation targets. It emits evidence excerpts and schema-constrained hypotheses; deterministic orchestration verifies exact grounding, merges immutable source fields, evaluates governed rules and calls the grouper boundary.

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
