# Human review packet contract

The review packet is the stable boundary between encounter evaluation and any reviewer application. It prevents the UI, workflow system, or downstream integration from reconstructing financial logic independently.

## Contract contents

`schemas/review_packet.schema.json` defines version `1.0.0` with:

- encounter identity and the immutable claim snapshot used for evaluation;
- the evidence, assertions, and patient-specific ontology graph seen by the rules;
- deterministic findings with rule, grouper, DRG, impact, and evidence lineage;
- explicit controls forbidding claim mutation and enumerating reviewer actions;
- hashes for the case, rule package, and audit record plus all executable component versions.

The packet is a reviewer input, not an outbound claim transaction. A separate decision service must authenticate the reviewer, enforce tenant and role policy, record a reason, and append the decision to immutable audit storage. The reference UI only models those interactions locally.

## Generate a packet

```bash
revenue-integrity \
  examples/case_pressure_injury.json \
  rules/wound_care_v1.json \
  --format review-packet \
  --environment synthetic \
  --output output/review-packet.json
```

Environment labels are metadata, not authorization. Production authorization must come from deployment policy and identity controls.

## Demo fixture integrity

The primary frontend case is generated from the Python engine:

```bash
make demo-packet
make demo-packet-check
```

CI runs the check form. Any rule, ontology, evidence, grouper, or engine change that alters the deterministic result must deliberately regenerate and review `demo/src/fixtures/review-packet.json`. The browser validates the packet again with a fail-closed Zod boundary before rendering it.

## Versioning rules

- Additive optional fields may remain within a compatible contract version.
- Required-field, meaning, enum, or trust-boundary changes require a new schema version.
- Consumers must reject unknown major versions.
- Never remove provenance or relax `claim_mutation_allowed: false` in this contract.
- Claim submission, payer communication, and reviewer decisions belong in separate, explicitly authorized contracts.
