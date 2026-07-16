# Five-pass architecture review

This record captures five successive design reviews of the ontology-driven revenue-integrity foundation. Each pass inspected the result of the prior pass, recorded the highest-leverage remaining weakness, implemented the correction, and reran the verification suite.

## Review principles

1. The language model extracts evidence; it does not decide coding, billing, DRG, payment, or clinical action.
2. Patient instances, ontology definitions, rule packages and grouping logic are separate versioned artifacts.
3. Every consequential result must be reproducible from immutable inputs and traceable to exact evidence.
4. Unknown, incompatible, oversized or unapproved inputs fail closed.
5. Specialty expansion occurs through injected definitions and packages, not engine branches.

## Pass 1 — domain boundaries and modularity

**Finding.** Python and TypeScript validators were data-driven, but the Mastra merge path still assumed fixed patient, encounter and claim IDs. A new specialty or encounter topology would therefore require code edits.

**Change.** Added a `structural_graph` template to the ontology definition. Root entities, structural relations, reserved IDs, prompt contracts and agent-fragment merging are now derived from the selected definition. Custom ontology tests replace root IDs without changing orchestration code.

**Result.** Ontology structure is an injected domain artifact. The Mastra layer has no wound-specific root constants.

## Pass 2 — ontology evolution and drift protection

**Finding.** Ontology ID and version alone could not detect an edited definition that accidentally retained its old version. A graph or rule package could silently bind to changed semantics.

**Change.** Added a canonical semantic SHA-256 digest over ontology classes, hierarchy, labels, relations, value sets and structural templates. Python and TypeScript compute the same digest. Patient graphs and rule packages carry it, and both runtimes reject mismatches.

**Result.** Version labels remain human-readable governance identifiers; the digest is the machine-enforced identity of the actual semantic contract.

## Pass 3 — deterministic targeting and explainability

**Finding.** Rules matched assertion fields but did not have to declare compatible ontology subject types. A free-form assertion with coincidentally matching attributes could trigger a rule on the wrong kind of entity.

**Change.** Every rule now declares `applies_to.subject_types` and whether subclasses are included. The engine validates scope classes before evaluation and uses ontology inheritance during matching. Findings retain the ordered matched `subject_ids` as well as assertion and evidence IDs.

**Result.** Rule targeting is typed, deterministic and inspectable. Reviewers can navigate directly from a finding to the affected patient-graph objects and source excerpts.

## Pass 4 — agent trust boundary and operability

**Finding.** Schema validity did not bound document or graph size. A malformed integration or model response could create excessive prompt, memory or validation work.

**Change.** Added an independent `ExtractionPolicy` in TypeScript for source-document, evidence, entity, relation and assertion budgets. Limits are checked before the prompt and after extraction and are included in the agent contract. Added configurable Python `CaseValidationLimits` so deterministic evaluation independently enforces evidence and graph budgets.

**Result.** Defaults are safe for an MVP and tunable for deployment profiles. Scaling limits does not require weakening lineage, ontology or rule validation.

## Pass 5 — source governance and release readiness

**Finding.** Published schemas were not compiled in tests, and the source-bundle schema contained an unresolved relative claim reference. The source workbook checksum and non-executable governance flags were documented but not mechanically enforced.

**Change.** Extracted a reusable claim JSON Schema, added strict Draft 2020-12 compilation tests for all canonical artifacts, and made AJV a direct pinned development dependency. Added tests for the workbook SHA-256, XLSX structure, macro/external-link absence, source authority flags and ontology source-manifest resolution. The raw workbook is committed byte-for-byte with its governed manifest.

**Result.** CI verifies interoperability contracts and knowledge provenance, not only implementation-specific parsers.

## Consolidated improvement backlog

The five passes establish an extensible foundation, not a production authorization. The next material capabilities should be delivered behind the existing interfaces:

- licensed MS-DRG grouper/pricer integration with effective-date and facility context;
- FHIR/HL7, UB-04/837I and charge-master adapters that preserve source identities;
- terminology service adapters for licensed ICD-10-CM/PCS, SNOMED CT and local mappings;
- separate, governed rule packages for CDI, coding, charge capture and compliance domains;
- retrieval/chunking for large longitudinal records instead of simply increasing prompt limits;
- evaluation sets covering negation, conflict, temporal change, duplicate documentation, false positives and silent omissions;
- reviewer workflow with role-based approval, reason capture, audit export and no automatic claim mutation;
- security, privacy, retention, tenant isolation and observability controls described in `SECURITY.md`.

## Verification baseline

At the end of this review, the required gate is `make verify`, followed by `npm run build` in `agent/` and a deterministic CLI demo. CI repeats Python tests, TypeScript tests, type checking, schema compilation and the Mastra production build.
