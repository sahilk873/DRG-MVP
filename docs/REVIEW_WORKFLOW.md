# Governed review workflow

The review workflow is the boundary between an engine finding and a human operational decision. It does not mutate a claim.

Every review packet v3 carries a `tenant_id`, `workspace_id`, and full-packet hash. Every automation plan names that exact packet and policy digest. A reviewer identity carries the same scope plus one or more roles. `ReviewWorkflowService` rejects cross-tenant access, tampered packets/plans, deferred or non-human findings, actions outside the finding contract, actions outside the reviewer's role, unknown findings, missing reasons, and packets that do not explicitly forbid claim mutation.

Accepted decisions reference the packet hash, automation-plan hash, policy hash, and a unique idempotency key, then append to a tenant-scoped SHA-256 chain. A finding permits one terminal decision; reversals require a future explicit governed workflow. Structured reason codes support offline quality evaluation without silently changing policy. `SQLiteDecisionRepository` is a durable reference implementation with transactional optimistic concurrency. A production deployment should place the same service contract behind authenticated APIs and a managed relational store with encryption, backups, retention policy, and organization-specific access controls.

Decision schema v2 intentionally cannot append to the reference v1 SQLite table: v1 rows do not contain the packet, plan, policy, or idempotency provenance required by v2. The repository fails fast with an explicit archive/export-and-reinitialize message instead of silently fabricating or breaking the audit chain. A production migration must preserve v1 as immutable legacy history and start a separately versioned v2 chain under an approved migration record.

The browser demo injects `BrowserDemoWorkflowGateway`. It applies the same tenant/action/role checks and persists synthetic decisions in browser storage so the pitch demonstrates the workflow. It is deliberately not presented as the production audit store.

Supported roles are coder, CDI specialist, charge reviewer, compliance reviewer, administrator, and read-only. Supported actions are routing to those governed queues or dismissal with a required reason. There is intentionally no “change claim” action.
