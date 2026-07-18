# Production integration boundaries

External dependencies are capabilities, not assumptions embedded in rules.

- Source adapters transform provider exports into source bundles while preserving deterministic row and file lineage.
- Mastra agents map bounded narrative evidence into schema-validated encounter fragments. They do not execute billing rules.
- A terminology service normalizes codes through a versioned implementation of `TerminologyService`. The default unavailable service fails closed.
- A licensed grouper and contract-aware pricer implement the existing `Grouper` boundary. The bundled deterministic grouper is marked `production_ready = false` and is only for synthetic demonstrations.
- The automation policy writes bounded operational routes to a tenant-scoped transactional outbox; delivery adapters own retries and acknowledgements.
- The reviewer workflow persists authorized decisions separately from claims and downstream billing systems.

`CapabilityRegistry(production=True)` refuses any component that is not explicitly marked production ready. Production adapters should expose an immutable component ID, version, capability kind, supported formats, and deployment approval status. Effective dates, payer contracts, terminology releases, and vendor response identifiers belong in each adapter's result provenance.

This keeps the ontology and rule engine stable as vendors, clinics, formats, code systems, and commercial groupers change. New capabilities are registered at composition time; clinical logic does not import vendor SDKs directly.
