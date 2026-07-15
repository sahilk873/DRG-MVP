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

