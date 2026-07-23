# Notional test data

`examples/bulk/mercy_regional/` is a synthetic ("notional") hospital data export used to test the
onboarding + ingestion trust boundary end to end. It is **not** real data and contains **no PHI** —
every MRN, name, and figure is fabricated. It is shaped to resemble a real inpatient revenue-integrity
extract so the deterministic pipeline is exercised the way a real provider export would exercise it.

## What real revenue-integrity data looks like

A hospital coding / CDI / revenue-integrity workflow typically has these data domains, usually
delivered as flat extracts from an EHR data warehouse (Epic Clarity/Caboodle, Cerner) or as
standards-based feeds (HL7 v2, FHIR R4, X12 837I/835). The notional files map onto these:

| Domain | Real-world source / standard | Notional file | Key fields |
|---|---|---|---|
| Encounter / ADT | HL7 v2 ADT, FHIR `Encounter`, UB-04 FLs 12–17 | `encounters.csv` | MRN, encounter id, admit/discharge datetime, patient class, admit source, discharge disposition, payer |
| Diagnoses | ICD-10-CM, UB-04 FLs 66–75, with **POA indicator** (Y/N/U/W/1) | `diagnoses.csv` | `icd10_cm`, `dx_seq` (1 = principal), `poa_ind` |
| Procedures | ICD-10-PCS (inpatient), UB-04 FLs 74a–e | `procedures.csv` | `icd10_pcs`, procedure datetime |
| Charges (CDM) | UB-04 FLs 42–47: **revenue codes** + HCPCS/CPT, units, charges | `charges.csv` | `rev_code`, `hcpcs`, units, `charge_amt_cents`, service date |
| Claim | UB-04 / X12 837I: type of bill, **working vs final DRG**, total charges | `claims.csv` | `type_of_bill`, `working_drg`, `final_drg`, `expected_reimb_cents` |
| Clinical documentation | FHIR `DocumentReference`, dictated notes | `clinical_notes.csv` | doc type, author role, authored datetime, note text |
| Nursing skin/wound assessment | Flowsheet rows (structured) | `skin_assessments.csv` | wound id, PI stage, body site, **POA flag** |

Not modelled yet (see `READINESS.md`): X12 **835 remittance / denials** (CARC/RARC codes) and native
**FHIR/HL7/837** parsing — those require dedicated adapters and are the real-data-path readiness gap.

## Deliberately "messy" so it exercises the adapter factory

Unlike the tidy `clinic_alpha` example, this export uses realistic column names and encodings that force
the declarative adapter (`examples/adapters/mercy_regional_wound_care_v1.json`) to actually transform:

- **Local timestamps** (`2026-06-01 07:45`, no zone) → `datetime` op with `format` + `timezone`
  (`America/Chicago`) resolves them to zoned ISO-8601.
- **Enum encodings** (`Stage IV`, `Unstageable`, `Sacrum`, `Right Heel`, `Y`/`N`) → `map` and `boolean`
  ops normalize them into ontology value-set members; unmapped values **fail closed**.
- **Cents as strings** → `integer` op.
- Different resource/column names (`mrn`, `final_drg`, `rev_code`, `pi_stage`, `poa_flag`) → the adapter
  binds them to the canonical source-bundle contract without any code change.

## Scenarios covered (6 encounters)

| Encounter | Scenario | Expected engine behavior |
|---|---|---|
| ENC-MR-2001 | Hospital-acquired **Stage IV sacral** PI, POA=N, L89 omitted from claim | severity coding finding (DEMO-292 → DEMO-290) **and** POA compliance finding |
| ENC-MR-2002 | **Stage III sacral** PI, present on admission | severity coding finding; no POA flag |
| ENC-MR-2003 | **Stage II coccyx** PI | no severity finding (stage below the 3–4 band) |
| ENC-MR-2004 | Sepsis, **no wound** (clean skin survey) | no wound findings |
| ENC-MR-2005 | Hospital-acquired **Stage IV right heel** PI, POA=N | severity + POA findings |
| ENC-MR-2006 | **Unstageable sacral** PI, POA=N | POA finding; unstageable is outside the numeric severity band |

## Run it

```sh
# Profile the export (bounded, schema + content fingerprints)
revenue-integrity-ingest profile examples/bulk/mercy_regional --output output/mercy.profile.json

# Transform it through the approved adapter into evidence-grounded source bundles
revenue-integrity-ingest run examples/bulk/mercy_regional \
  examples/adapters/mercy_regional_wound_care_v1.json \
  --output-directory output/mercy-bundles --report output/mercy.run.json

# or:
make bulk-demo-mercy
```

`tests/test_ingestion.py::MercyRegionalNotionalDataTests` runs the full path in CI: profile → adapter →
six evidence-grounded bundles → governed rules → findings (including the hospital-acquired severity +
POA findings and the clean-encounter negative control).
