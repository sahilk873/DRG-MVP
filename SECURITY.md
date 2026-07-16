# Security policy

This prototype must not receive protected health information, production credentials, payer contracts, or live claims.

Report vulnerabilities privately to the repository maintainers. Do not include real patient data in an issue, pull request, test fixture, screenshot, log, or proof of concept.

## Production security prerequisites

Before handling regulated data, complete a formal threat model and security review covering:

- HIPAA agreements and institution-approved data flows;
- encryption in transit and at rest with managed key rotation;
- tenant isolation, least-privilege RBAC, break-glass access and periodic access review;
- secret management with no credentials in model prompts or repositories;
- immutable access, extraction, rule-execution and reviewer-decision logs;
- retention, legal-hold and deletion controls;
- prompt-injection, data-exfiltration and model-provider risk controls;
- software composition analysis, signed builds and deployment provenance;
- incident response, disaster recovery and tested rollback procedures.

The demo grouper and sample clinical rules are explicitly non-production artifacts.

## Bulk onboarding boundary

- Mount provider input read-only and write outputs to a separate location.
- Reject symbolic links, traversal, unsafe or over-expanded workbook archives, unknown resources and configured file/row/byte budget violations.
- Run profiling and adapter execution without outbound network access.
- Send only policy-bounded, deidentified profiles to the adapter-design model; never give it shell or filesystem tools.
- Treat filenames, headers, worksheet names, cell values and document contents as untrusted data.
- Require explicit human promotion from `draft` to an approved adapter state.
- Pin adapter and ontology versions and fail on schema or semantic-digest drift.
- Store source objects, profiles, adapters, run manifests and review decisions in tenant-isolated audit storage.
- Replace the reference in-memory readers with hardened streaming services before processing production-scale exports.
