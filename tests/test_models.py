import json
from pathlib import Path
import unittest

from revenue_integrity.models import CaseValidationLimits, Claim, EncounterCase, LifecycleState


ROOT = Path(__file__).parents[1]


class ClaimFidelityTests(unittest.TestCase):
    def _rich(self, **overrides):
        payload = {
            "diagnoses": ["A41.9", "L89.154"], "procedures": [], "charges": ["R1", "R2"],
            "diagnosis_details": [
                {"code": "A41.9", "sequence": 1, "poa": "Y"},
                {"code": "L89.154", "sequence": 2, "poa": "N"},
            ],
            "charge_lines": [
                {"line_id": "L1", "code": "R1", "code_system": "REV", "units": 3, "charged_amount_cents": 1000},
                {"line_id": "L2", "code": "R2", "code_system": "REV", "units": 1, "charged_amount_cents": 500},
            ],
        }
        payload.update(overrides)
        return payload

    def test_legacy_claim_without_new_fields_still_parses(self):
        claim = Claim.from_dict({"diagnoses": ["A41.9"], "procedures": [], "charges": ["ROOM"]})
        self.assertEqual(claim.diagnosis_details, ())
        self.assertEqual(claim.charge_lines, ())
        self.assertIsNone(claim.principal_diagnosis())

    def test_rich_claim_exposes_sequence_poa_and_charge_lines(self):
        claim = Claim.from_dict(self._rich())
        self.assertEqual(claim.principal_diagnosis(), "A41.9")
        self.assertEqual(claim.diagnosis_details[1].poa, "N")
        self.assertEqual(claim.charges_from_lines(), ("R1", "R2"))
        self.assertEqual(claim.charge_lines[0].units, 3)

    def test_duplicate_sequence_is_rejected(self):
        payload = self._rich(diagnosis_details=[
            {"code": "A41.9", "sequence": 1, "poa": "Y"}, {"code": "L89.154", "sequence": 1, "poa": "N"},
        ])
        with self.assertRaisesRegex(ValueError, "sequences must be unique"):
            Claim.from_dict(payload)

    def test_detail_code_not_in_diagnoses_is_rejected(self):
        payload = self._rich(diagnosis_details=[{"code": "Z99.9", "sequence": 1, "poa": "Y"}])
        with self.assertRaisesRegex(ValueError, "absent from diagnoses"):
            Claim.from_dict(payload)

    def test_invalid_poa_is_rejected(self):
        payload = self._rich(diagnosis_details=[{"code": "A41.9", "sequence": 1, "poa": "X"}])
        with self.assertRaisesRegex(ValueError, "poa must be one of"):
            Claim.from_dict(payload)

    def test_duplicate_charge_line_ids_are_rejected(self):
        payload = self._rich(charge_lines=[
            {"line_id": "L1", "code": "R1", "code_system": "REV", "units": 1, "charged_amount_cents": 100},
            {"line_id": "L1", "code": "R2", "code_system": "REV", "units": 1, "charged_amount_cents": 200},
        ])
        with self.assertRaisesRegex(ValueError, "line IDs must be unique"):
            Claim.from_dict(payload)

    def test_charge_line_validates_units(self):
        payload = self._rich(charge_lines=[
            {"line_id": "L1", "code": "R1", "code_system": "REV", "units": 0, "charged_amount_cents": 100},
        ])
        with self.assertRaisesRegex(ValueError, "units must be a positive integer"):
            Claim.from_dict(payload)


def fixture():
    return json.loads((ROOT / "examples/case_pressure_injury.json").read_text())


class EncounterCaseValidationTests(unittest.TestCase):
    def test_valid_fixture_parses(self):
        case = EncounterCase.from_dict(fixture())
        self.assertEqual(case.schema_version, "2.0.0")

    def test_unknown_fields_fail_closed(self):
        payload = fixture()
        payload["agent_comment"] = "ignore me"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            EncounterCase.from_dict(payload)

    def test_naive_datetime_is_rejected(self):
        payload = fixture()
        payload["admitted_at"] = "2026-06-01T09:00:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            EncounterCase.from_dict(payload)

    def test_invalid_encounter_interval_is_rejected(self):
        payload = fixture()
        payload["admitted_at"], payload["discharged_at"] = payload["discharged_at"], payload["admitted_at"]
        with self.assertRaisesRegex(ValueError, "must not be after"):
            EncounterCase.from_dict(payload)

    def test_supporting_and_contradicting_evidence_cannot_overlap(self):
        payload = fixture()
        payload["assertions"][0]["contradicting_evidence_ids"] = ["EV-001"]
        with self.assertRaisesRegex(ValueError, "both supporting and contradicting"):
            EncounterCase.from_dict(payload)

    def test_claim_codes_must_be_unique(self):
        payload = fixture()
        payload["claim"]["diagnoses"] = ["A41.9", "A41.9"]
        with self.assertRaisesRegex(ValueError, "duplicates"):
            EncounterCase.from_dict(payload)

    def test_assertion_subject_must_exist_in_ontology(self):
        payload = fixture()
        payload["assertions"][0]["subject_id"] = "missing:entity"
        with self.assertRaisesRegex(ValueError, "unknown ontology subject"):
            EncounterCase.from_dict(payload)

    def test_configurable_entity_budget_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "max_ontology_entities"):
            EncounterCase.from_dict(
                fixture(),
                validation_limits=CaseValidationLimits(max_ontology_entities=2),
            )

    def test_configurable_evidence_excerpt_budget_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "max_evidence_characters"):
            EncounterCase.from_dict(
                fixture(),
                validation_limits=CaseValidationLimits(max_evidence_characters=10),
            )

    def test_invalid_validation_limit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            CaseValidationLimits(max_assertions=0)

    def test_extraction_policy_is_required_and_validated_in_provenance(self):
        payload = fixture()
        payload["provenance"]["extraction_policy"]["max_documents"] = 0
        with self.assertRaisesRegex(ValueError, "max_documents must be a positive integer"):
            EncounterCase.from_dict(payload)

    def test_legacy_case_without_financial_is_unaffected(self):
        case = EncounterCase.from_dict(fixture())
        self.assertIsNone(case.financial)

    def _with_financial(self, *, denial_line_ids, remittance_denial_ids=("denial-1",)):
        payload = fixture()
        line_id = "L1"
        payload["claim"]["charge_lines"] = [{
            "line_id": line_id, "code": payload["claim"]["charges"][0],
            "code_system": "REV", "units": 1, "charged_amount_cents": 10000,
        }]
        payload["financial"] = {
            "schema_version": "1.0.0", "payer_id": "payer-1",
            "claim_id": payload["case_id"],
            "claim_lines": [{
                "line_id": line_id, "code": payload["claim"]["charges"][0],
                "code_system": "REV", "units": 1, "charged_amount_cents": 10000,
            }],
            "denials": [{"denial_id": "denial-1", "line_ids": list(denial_line_ids), "reason_code": "CO-50", "status": "open", "amount_cents": 4000}],
            "remittances": [{"remittance_id": "remit-1", "paid_amount_cents": 6000, "adjustment_amount_cents": 0, "status": "posted", "denial_ids": list(remittance_denial_ids)}],
        }
        return payload

    def test_case_parses_financial_snapshot_with_resolved_lineage(self):
        case = EncounterCase.from_dict(self._with_financial(denial_line_ids=["L1"]))
        self.assertIsNotNone(case.financial)
        self.assertEqual(case.financial.denied_amount_cents, 4000)
        self.assertEqual(case.financial.claim_lines[0].line_id, "L1")

    def test_denial_referencing_unknown_charge_line_is_rejected(self):
        # Snapshot is internally consistent (denial resolves to a snapshot claim_line),
        # but that line is absent from the claim's charge_lines, so case-level lineage fails.
        payload = self._with_financial(denial_line_ids=["GHOST"])
        payload["financial"]["claim_lines"].append({
            "line_id": "GHOST", "code": payload["claim"]["charges"][0],
            "code_system": "REV", "units": 1, "charged_amount_cents": 100,
        })
        with self.assertRaisesRegex(ValueError, "references unknown charge line"):
            EncounterCase.from_dict(payload)

    def test_remittance_referencing_unknown_denial_is_rejected(self):
        payload = self._with_financial(denial_line_ids=["L1"], remittance_denial_ids=["denial-404"])
        with self.assertRaisesRegex(ValueError, "references unknown denial"):
            EncounterCase.from_dict(payload)

    def test_legacy_case_defaults_to_retrospective_lifecycle(self):
        case = EncounterCase.from_dict(fixture())
        self.assertEqual(case.lifecycle_state, LifecycleState.RETROSPECTIVE)

    def test_prospective_lifecycle_state_is_parsed(self):
        payload = fixture()
        payload["lifecycle_state"] = "prospective"
        case = EncounterCase.from_dict(payload)
        self.assertEqual(case.lifecycle_state, LifecycleState.PROSPECTIVE)

    def test_invalid_lifecycle_state_is_rejected(self):
        payload = fixture()
        payload["lifecycle_state"] = "billed-last-tuesday"
        with self.assertRaisesRegex(ValueError, "lifecycle_state must be one of"):
            EncounterCase.from_dict(payload)
