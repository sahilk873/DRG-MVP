# Encounter Revenue Integrity

An evidence-grounded reference implementation for reconstructing an inpatient encounter, comparing documentation with coding and billing, and routing only consequential exceptions to human reviewers. [Mastra](https://mastra.ai/) provides provider-agnostic semantic extraction; the revenue-integrity engine remains deterministic and model-independent.

> **Not for production billing or clinical use.** The included rules, codes, prices and demo grouper are synthetic integration artifacts that have not been clinically or operationally validated.

## Architecture

Clinical records benefit from semantic extraction. Claim grouping, rule evaluation, payment simulation and audit records must remain reproducible. The agent therefore produces **schema-constrained evidence and hypotheses, never executable rules or authoritative financial fields**.

```mermaid
flowchart TD
    A["Source documents"] --> B["Mastra extraction agent"]
    B --> C["Grounding and schema gate"]
    H["Immutable claim data"] --> C
    C --> D["Versioned rule engine"]
    D --> E["Licensed grouper boundary"]
    E --> F["Focused review packet"]
```

The case model separates what happened clinically, what was explicitly documented, what was coded and charged, what was submitted and paid, and what evidence supports or contradicts a proposed change.

## Current capabilities

- Canonical source-bundle and encounter-case JSON Schemas
- Mastra model routing through a configurable `provider/model` ID
- Claims, charges, DRGs and payment fields excluded from model generation
- Exact-excerpt grounding against immutable source documents
- Supporting and contradicting evidence lineage
- Strict, declarative JSON rules with no generated-code execution path
- Fail-closed package approval and action validation
- Replaceable licensed DRG grouper/pricer interface
- Integer-cent payment simulation
- Deterministic finding IDs and hash-chained audit records
- Atomic CLI output, CI, dependency monitoring and cross-language tests

## Repository layout

```text
agent/          Provider-agnostic Mastra extraction service
schemas/        Source and encounter interoperability contracts
rules/          Versioned declarative rule packages
examples/       Deidentified synthetic fixtures
src/            Deterministic models, rules, grouper boundary and audit code
tests/          Correctness, malformed-input and safety tests
docs/           Architecture and trust-boundary decisions
```

## Quick start

Python 3.11+ and Node.js 22+ are required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .

cd agent
npm ci
cd ..

make verify
make demo
```

The deterministic demo creates a review finding, supporting evidence, a proposed code change, demo regrouping and payment delta. It never modifies or submits a claim.

## Run the Mastra extraction layer

```bash
cd agent
cp .env.example .env

# Select any Mastra-supported provider/model and set that provider's API key.
MODEL_ID=anthropic/<model> npm run extract -- \
  ../examples/source_bundle_pressure_injury.json \
  ../output/encounter-case.json
```

The application does not import a provider SDK. Changing `MODEL_ID` changes the extraction model without changing the ontology or deterministic engine.

## Trust boundary

The agent receives encounter timing and source documents, but not the claim, charges, existing DRG or payment. It returns only evidence excerpts and clinical assertions. The orchestrator then:

1. validates the source bundle;
2. verifies every excerpt is an exact source substring;
3. verifies the document ID, author role and timestamp are unchanged;
4. validates evidence lineage and contradictions;
5. attaches model and schema provenance outside the model;
6. merges immutable encounter and claim fields;
7. passes the completed case through an independent Python validator.

Invalid confidence values, unknown fields, duplicate IDs, naive timestamps, dangling citations, overlapping supporting/contradicting evidence and schema-version mismatches all fail closed. Any proposed claim change requires human review in this version.

## Extension path

Additional Mastra agents can handle retrieval, terminology normalization, conflict detection, coding/CDI hypothesis generation, charge reconciliation, compliance criticism and reviewer-packet drafting. Each agent must use an explicit contract. Agent consensus is not authoritative evidence.

Before real use, the project still requires licensed terminology and grouping components, FHIR/HL7 and claim adapters, institution-approved rules, representative positive and negative validation data, model/retrieval evaluations, a reviewer application and the security controls in [SECURITY.md](SECURITY.md).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design decisions and [CONTRIBUTING.md](CONTRIBUTING.md) for change requirements.

## License

Apache-2.0. Clinical rules, licensed terminologies, customer data and payer contracts must be distributed separately under their applicable terms.

