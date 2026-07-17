import json
from datetime import UTC, datetime
from pathlib import Path
import unittest

from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import EncounterCase
from revenue_integrity.review_packet import build_review_packet


ROOT = Path(__file__).parents[1]


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class ReviewPacketTests(unittest.TestCase):
    def setUp(self):
        self.case_payload = load("examples/case_pressure_injury.json")
        self.rules = load("rules/wound_care_v1.json")
        self.case = EncounterCase.from_dict(self.case_payload)
        self.findings = RuleEngine(self.rules, DeterministicDemoGrouper()).evaluate(self.case)

    def packet(self):
        return build_review_packet(
            case=self.case,
            case_payload=self.case_payload,
            rule_package=self.rules,
            findings=self.findings,
            environment="synthetic",
            clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        )

    def test_packet_is_a_self_contained_human_review_handoff(self):
        packet = self.packet()
        self.assertEqual(packet["review_packet_schema_version"], "1.0.0")
        self.assertEqual(packet["environment"], "synthetic")
        self.assertEqual(packet["case"]["encounter_id"], self.case.encounter_id)
        self.assertEqual(packet["evidence"][0]["evidence_id"], "EV-001")
        self.assertEqual(packet["findings"][0]["estimated_impact_cents"], 842000)
        self.assertFalse(packet["controls"]["claim_mutation_allowed"])
        self.assertTrue(packet["controls"]["human_review_required"])
        self.assertIn("route_to_coding", packet["controls"]["permitted_actions"])

    def test_packet_is_reproducible_with_a_fixed_clock(self):
        self.assertEqual(self.packet(), self.packet())

    def test_packet_rejects_invalid_environment_and_mismatched_payload(self):
        with self.assertRaisesRegex(ValueError, "unsupported review-packet environment"):
            build_review_packet(
                case=self.case,
                case_payload=self.case_payload,
                rule_package=self.rules,
                findings=self.findings,
                environment="customer-demo",
            )
        changed = dict(self.case_payload)
        changed["case_id"] = "different-case"
        with self.assertRaisesRegex(ValueError, "does not match validated case"):
            build_review_packet(
                case=self.case,
                case_payload=changed,
                rule_package=self.rules,
                findings=self.findings,
            )
