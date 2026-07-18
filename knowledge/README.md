# Knowledge-source governance

Files in this directory inform ontology and rule design; they are not executable policy.

## Source classes

- `sources/` preserves approved repository copies of source artifacts byte-for-byte.
- `*_manifest.json` records provenance, checksum, inventory, review status and authority boundaries.
- Ontology definitions may cite a `source_id`, but citation does not make the source executable.

The wound-care workbook is committed at `sources/wound_care_clinical_rules_raw.xlsx`. Its manifest checksum is verified in CI. The file must not be reformatted or normalized in place; a changed byte requires a deliberate manifest checksum update and review of downstream ontology or rule impact.

## Promotion path

A candidate source statement can influence executable revenue-integrity behavior only after it is normalized into a versioned ontology or rule package, reviewed by the appropriate clinical/coding owner, supplied with positive/negative/boundary/conflict tests, and approved under its own effective date. Raw clinical rules never authorize coding, claim mutation, treatment, or payment decisions.

Production customer data, licensed code sets, payer contracts and proprietary grouping logic do not belong in this directory.
