import shutil
import tempfile
import unittest
from pathlib import Path

from revenue_integrity.ingestion.adapter import load_adapter, run_adapter
from revenue_integrity.ingestion.models import DegradationPolicy
from revenue_integrity.ontology import load_builtin_ontology

ROOT = Path(__file__).parents[1]
CLINIC = ROOT / "examples/bulk/clinic_alpha"
ADAPTER_PATH = ROOT / "examples/adapters/clinic_alpha_wound_care_v1.json"

# Same columns as clinic_alpha (schema fingerprint unchanged) but seeded with each recoverable fault.
ENCOUNTERS = (
    "case_id,patient_id,encounter_id,admitted_at,discharged_at,facility\n"
    "CASE-ALPHA-001,PAT-ALPHA-001,ENC-ALPHA-001,2026-06-01T08:00:00Z,2026-06-05T16:00:00Z,Alpha Medical Center\n"
    "CASE-ALPHA-001B,PAT-ALPHA-001,ENC-ALPHA-001,2026-06-01T08:00:00Z,2026-06-05T16:00:00Z,Alpha Medical Center\n"  # duplicate
    "CASE-ALPHA-002,PAT-ALPHA-002,ENC-ALPHA-002,2026-06-02T08:00:00Z,2026-06-06T16:00:00Z,Alpha Medical Center\n"  # multi-claim
    "CASE-ALPHA-003,PAT-ALPHA-003,ENC-ALPHA-003,2026-06-03T08:00:00Z,2026-06-07T16:00:00Z,Alpha Medical Center\n"  # no claim
    "CASE-ALPHA-004,PAT-ALPHA-004,ENC-ALPHA-004,2026-06-04T08:00:00Z,2026-06-08T16:00:00Z,Alpha Medical Center\n"  # clean
)
CLAIMS = (
    "encounter_id,submitted_drg,allowed_amount_cents\n"
    "ENC-ALPHA-001,871,1200000\n"
    "ENC-ALPHA-002,872,1100000\n"
    "ENC-ALPHA-002,873,1150000\n"  # second claim for 002
    "ENC-ALPHA-004,864,1000000\n"
    "ENC-ALPHA-999,900,999999\n"  # orphan: no such encounter
)


class QuarantinePlaneTests(unittest.TestCase):
    def setUp(self):
        self.adapter = load_adapter(ADAPTER_PATH)
        self.ontology = load_builtin_ontology(self.adapter.ontology.ontology_id, self.adapter.ontology.version)

    def _bulk(self, temp: str) -> Path:
        bulk = Path(temp) / "bulk"
        shutil.copytree(CLINIC, bulk)
        (bulk / "encounters.csv").write_text(ENCOUNTERS)
        (bulk / "claims.csv").write_text(CLAIMS)
        return bulk

    def test_default_mode_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                run_adapter(self._bulk(temp), self.adapter, self.ontology)

    def test_quarantine_mode_keeps_clean_encounters_and_records_faults(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_adapter(
                self._bulk(temp), self.adapter, self.ontology,
                degradation=DegradationPolicy(mode="quarantine"),
            )
        # Only the two clean encounters (001, 004) survive to output.
        self.assertEqual(result.report.output_cases, 2)
        self.assertEqual({b["encounter_id"] for b in result.source_bundles}, {"ENC-ALPHA-001", "ENC-ALPHA-004"})
        reasons = {record["reason"] for record in result.report.quarantined}
        self.assertEqual(reasons, {"duplicate_encounter", "multiple_claims", "no_claim", "unknown_encounter"})
        # The report serializes the quarantine detail for operators.
        self.assertEqual(result.report.to_dict()["quarantined_count"], len(result.report.quarantined))

    def test_circuit_breaker_aborts_when_too_much_is_wrong(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "circuit breaker"):
                run_adapter(
                    self._bulk(temp), self.adapter, self.ontology,
                    degradation=DegradationPolicy(mode="quarantine", max_quarantined=1),
                )

    def test_policy_validates_mode(self):
        with self.assertRaisesRegex(ValueError, "degradation mode"):
            DegradationPolicy(mode="skip-everything")


if __name__ == "__main__":
    unittest.main()
