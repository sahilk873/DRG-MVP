import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
import unittest

from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import EncounterCase
from revenue_integrity.review_packet import build_review_packet
from revenue_integrity.workflow import (
    ReviewAction, ReviewerIdentity, ReviewerRole, ReviewWorkflowService,
    SQLiteDecisionRepository, verify_decision_chain,
)

ROOT = Path(__file__).parents[1]


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        case_payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text())
        rules = json.loads((ROOT / "rules/wound_care_v1.json").read_text())
        case = EncounterCase.from_dict(case_payload)
        findings = RuleEngine(rules, DeterministicDemoGrouper()).evaluate(case)
        self.packet = build_review_packet(
            case=case, case_payload=case_payload, rule_package=rules, findings=findings,
            tenant_id="tenant-a", workspace_id="revenue", environment="synthetic",
            clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        )
        self.actor = ReviewerIdentity("coder-1", "tenant-a", "revenue", (ReviewerRole.CODER,))
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = SQLiteDecisionRepository(Path(self.temporary.name) / "decisions.db")
        self.service = ReviewWorkflowService(self.repository, lambda: datetime(2026, 7, 17, 13, tzinfo=UTC))

    def tearDown(self):
        self.temporary.cleanup()

    def test_persists_tenant_scoped_hash_linked_decisions(self):
        finding_id = self.packet["findings"][0]["finding_id"]
        first = self.service.submit(packet=self.packet, actor=self.actor, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason="Coder validation required")
        second = self.service.submit(packet=self.packet, actor=self.actor, finding_id=finding_id, action=ReviewAction.DISMISS_WITH_REASON, reason="Duplicate opportunity")
        decisions = self.repository.list_for_packet("tenant-a", "revenue", self.packet["packet_id"])
        self.assertEqual([first, second], list(decisions))
        self.assertEqual(second.previous_decision_hash, first.decision_hash)
        self.assertTrue(verify_decision_chain(decisions))
        self.assertEqual(self.repository.list_for_packet("tenant-b", "revenue", self.packet["packet_id"]), ())

    def test_denies_cross_tenant_and_unauthorized_actions(self):
        finding_id = self.packet["findings"][0]["finding_id"]
        outsider = ReviewerIdentity("coder-2", "tenant-b", "revenue", (ReviewerRole.CODER,))
        with self.assertRaisesRegex(PermissionError, "tenant scope"):
            self.service.submit(packet=self.packet, actor=outsider, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason="review")
        reader = ReviewerIdentity("reader-1", "tenant-a", "revenue", (ReviewerRole.READ_ONLY,))
        with self.assertRaisesRegex(PermissionError, "roles"):
            self.service.submit(packet=self.packet, actor=reader, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason="review")

    def test_requires_a_reason_and_packet_finding(self):
        with self.assertRaisesRegex(ValueError, "finding"):
            self.service.submit(packet=self.packet, actor=self.actor, finding_id="unknown", action=ReviewAction.ROUTE_TO_CODING, reason="review")
        finding_id = self.packet["findings"][0]["finding_id"]
        with self.assertRaisesRegex(ValueError, "reason"):
            self.service.submit(packet=self.packet, actor=self.actor, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason=" ")


if __name__ == "__main__":
    unittest.main()
