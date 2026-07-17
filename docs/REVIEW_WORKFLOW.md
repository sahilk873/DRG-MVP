# Governed review workflow

The review workflow is the boundary between an engine finding and a human operational decision. It does not mutate a claim.

Every review packet v2 carries a `tenant_id` and `workspace_id`. A reviewer identity carries the same scope plus one or more roles. `ReviewWorkflowService` rejects cross-tenant access, actions outside the packet contract, actions outside the reviewer's role, unknown findings, missing reasons, and packets that do not explicitly forbid claim mutation.

Accepted decisions reference the exact packet audit hash and are appended to a tenant-scoped SHA-256 chain. `SQLiteDecisionRepository` is a durable reference implementation with transactional optimistic concurrency. A production deployment should place the same service contract behind authenticated APIs and a managed relational store with encryption, backups, retention policy, and organization-specific access controls.

The browser demo injects `BrowserDemoWorkflowGateway`. It applies the same tenant/action/role checks and persists synthetic decisions in browser storage so the pitch demonstrates the workflow. It is deliberately not presented as the production audit store.

Supported roles are coder, CDI specialist, charge reviewer, compliance reviewer, administrator, and read-only. Supported actions are routing to those governed queues or dismissal with a required reason. There is intentionally no “change claim” action.
