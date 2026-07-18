import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import unittest

from revenue_integrity.automation import build_automation_plan
from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import EncounterCase
from revenue_integrity.review_packet import build_review_packet
from revenue_integrity.routing import SQLiteRoutingOutbox


ROOT = Path(__file__).parents[1]


class RoutingOutboxTests(unittest.TestCase):
    def setUp(self):
        payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text())
        rules = json.loads((ROOT / "rules/wound_care_v1.json").read_text())
        case = EncounterCase.from_dict(payload)
        finding = RuleEngine(rules, DeterministicDemoGrouper()).evaluate(case)[0]
        # A supported, low-risk operational exception that does not alter DRG.
        self.finding = replace(
            finding, current_drg="SAME", simulated_drg="SAME",
            estimated_impact_cents=100_000,
        )
        packet = build_review_packet(
            case=case, case_payload=payload, rule_package=rules, findings=[self.finding],
            tenant_id="tenant-a", workspace_id="revenue",
        )
        self.plan = build_automation_plan(
            [self.finding], tenant_id="tenant-a", workspace_id="revenue",
            case_id=case.case_id, encounter_id=case.encounter_id,
            packet_id=packet["packet_id"], packet_hash=packet["provenance"]["packet_hash"],
        )
        self.temporary = tempfile.TemporaryDirectory()
        self.outbox = SQLiteRoutingOutbox(Path(self.temporary.name) / "routes.db")
        self.clock = lambda: datetime(2026, 7, 17, 15, tzinfo=UTC)

    def tearDown(self):
        self.temporary.cleanup()

    def test_auto_routes_are_durable_idempotent_and_tenant_scoped(self):
        first = self.outbox.enqueue_plan(self.plan, clock=self.clock)
        retried = self.outbox.enqueue_plan(self.plan, clock=self.clock)
        self.assertEqual(first, retried)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].action, "route_to_coding")
        self.assertNotIn("rationale", first[0].to_dict())
        self.assertEqual(self.outbox.list_pending("tenant-b", "revenue"), ())
        self.outbox.mark_delivered("tenant-a", "revenue", first[0].route_id)
        self.assertEqual(self.outbox.list_pending("tenant-a", "revenue"), ())

    def test_new_packet_for_same_finding_gets_a_distinct_route(self):
        first = self.outbox.enqueue_plan(self.plan, clock=self.clock)
        second_plan = build_automation_plan(
            [self.finding], tenant_id="tenant-a", workspace_id="revenue",
            case_id=self.plan["packet"]["case_id"], encounter_id=self.plan["packet"]["encounter_id"],
            packet_id="packet-00000000000000000000", packet_hash="0" * 64,
        )
        second = self.outbox.enqueue_plan(second_plan, clock=self.clock)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0].automation_id, second[0].automation_id)
        self.assertEqual(len(self.outbox.list_pending("tenant-a", "revenue")), 2)

    def test_tampered_plan_fails_before_enqueue(self):
        tampered = json.loads(json.dumps(self.plan))
        tampered["findings"][0]["queue"] = "compliance"
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.outbox.enqueue_plan(tampered, clock=self.clock)


if __name__ == "__main__":
    unittest.main()
