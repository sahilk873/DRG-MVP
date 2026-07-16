# Contributing

## Development checks

Install Python 3.11+ and Node.js 22+, then run:

```bash
python -m pip install -e .
cd agent && npm ci && cd ..
make verify
```

Run `make demo` to inspect the deterministic review packet. No model credential is needed for tests or the deterministic demo.

## Change requirements

- Never commit PHI, credentials, licensed terminology files or customer data.
- Keep agent output schema-constrained and evidence-grounded.
- Do not allow generated code to enter the rule-execution path.
- Add positive, negative, contradictory and malformed-input tests for rule changes.
- Version ontology definitions and test class inheritance, relation domains/ranges, evidence requirements and semantic digest compatibility.
- Give every executable rule an ontology subject scope and retain subject/assertion/evidence lineage in findings.
- Preserve source-artifact bytes and update their governed checksum only as a deliberate reviewed change.
- Keep clinical decision-support sources and rules separate from revenue-integrity packages.
- Update schema versions for breaking contract changes.
- Treat changes to rules, code mappings, grouping, pricing or review policy as governed changes requiring domain approval.
- Preserve deterministic IDs, integer-cent monetary calculations and complete version provenance.
