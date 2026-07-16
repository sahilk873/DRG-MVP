import json
from pathlib import Path
import unittest

from revenue_integrity.models import EncounterCase


ROOT = Path(__file__).parents[1]


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
