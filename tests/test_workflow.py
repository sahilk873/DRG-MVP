import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
import unittest

from revenue_integrity.audit import canonical_hash
from revenue_integrity.engine import RuleEngine
from revenue_integrity.automation import build_automation_plan
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import EncounterCase
from revenue_integrity.review_packet import build_review_packet
from revenue_integrity.workflow import (
    DecisionReasonCode, ReviewAction, ReviewerIdentity, ReviewerRole, ReviewWorkflowService,
    SQLiteDecisionRepository, summarize_decision_feedback, verify_decision_chain,
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
        self.plan = build_automation_plan(
            findings, tenant_id="tenant-a", workspace_id="revenue", case_id=case.case_id,
            encounter_id=case.encounter_id, packet_id=self.packet["packet_id"],
            packet_hash=self.packet["provenance"]["packet_hash"],
        )
        self.actor = ReviewerIdentity("coder-1", "tenant-a", "revenue", (ReviewerRole.CODER,))
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = SQLiteDecisionRepository(Path(self.temporary.name) / "decisions.db")
        self.service = ReviewWorkflowService(self.repository, lambda: datetime(2026, 7, 17, 13, tzinfo=UTC))

    def tearDown(self):
        self.temporary.cleanup()

    def test_persists_tenant_scoped_hash_linked_decisions(self):
        finding_id = self.packet["findings"][0]["finding_id"]
        first = self.service.submit(
            packet=self.packet, automation_plan=self.plan, actor=self.actor,
            finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reason="Coder validation required", idempotency_key="submit-1",
        )
        retried = self.service.submit(
            packet=self.packet, automation_plan=self.plan, actor=self.actor,
            finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            reason="Coder validation required", idempotency_key="submit-1",
        )
        decisions = self.repository.list_for_packet("tenant-a", "revenue", self.packet["packet_id"])
        self.assertEqual(first, retried)
        self.assertEqual([first], list(decisions))
        self.assertTrue(verify_decision_chain(decisions))
        self.assertEqual(summarize_decision_feedback(decisions)["acceptance_rate"], 1.0)
        self.assertEqual(self.repository.list_for_packet("tenant-b", "revenue", self.packet["packet_id"]), ())

    def test_denies_cross_tenant_and_unauthorized_actions(self):
        finding_id = self.packet["findings"][0]["finding_id"]
        outsider = ReviewerIdentity("coder-2", "tenant-b", "revenue", (ReviewerRole.CODER,))
        with self.assertRaisesRegex(PermissionError, "tenant scope"):
            self.service.submit(packet=self.packet, automation_plan=self.plan, actor=outsider, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED, reason="review", idempotency_key="outside")
        reader = ReviewerIdentity("reader-1", "tenant-a", "revenue", (ReviewerRole.READ_ONLY,))
        with self.assertRaisesRegex(PermissionError, "roles"):
            self.service.submit(packet=self.packet, automation_plan=self.plan, actor=reader, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED, reason="review", idempotency_key="reader")

    def test_requires_a_reason_and_packet_finding(self):
        with self.assertRaisesRegex(ValueError, "finding"):
            self.service.submit(packet=self.packet, automation_plan=self.plan, actor=self.actor, finding_id="unknown", action=ReviewAction.ROUTE_TO_CODING, reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED, reason="review", idempotency_key="unknown")
        finding_id = self.packet["findings"][0]["finding_id"]
        with self.assertRaisesRegex(ValueError, "reason"):
            self.service.submit(packet=self.packet, automation_plan=self.plan, actor=self.actor, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED, reason=" ", idempotency_key="blank")

    def test_reason_codes_are_action_compatible(self):
        finding_id = self.packet["findings"][0]["finding_id"]
        with self.assertRaisesRegex(ValueError, "reason code"):
            self.service.submit(
                packet=self.packet, automation_plan=self.plan, actor=self.actor,
                finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING,
                reason_code=DecisionReasonCode.DUPLICATE, reason="duplicate", idempotency_key="bad-label",
            )

    def test_rejects_tampered_packet_plan_and_conflicting_terminal_decision(self):
        finding_id = self.packet["findings"][0]["finding_id"]
        tampered = json.loads(json.dumps(self.packet))
        tampered["controls"]["claim_mutation_allowed"] = True
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.service.submit(packet=tampered, automation_plan=self.plan, actor=self.actor, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED, reason="review", idempotency_key="tampered")
        self.service.submit(packet=self.packet, automation_plan=self.plan, actor=self.actor, finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING, reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED, reason="review", idempotency_key="terminal")
        with self.assertRaisesRegex(ValueError, "terminal decision"):
            self.service.submit(packet=self.packet, automation_plan=self.plan, actor=self.actor, finding_id=finding_id, action=ReviewAction.DISMISS_WITH_REASON, reason_code=DecisionReasonCode.OTHER_GOVERNED, reason="changed mind", idempotency_key="different")

    def test_plan_finding_must_match_exact_packet_finding(self):
        finding_id = self.packet["findings"][0]["finding_id"]
        fabricated = json.loads(json.dumps(self.plan))
        changed_finding = {**self.packet["findings"][0], "rationale": "different"}
        fabricated["findings"][0]["finding_hash"] = canonical_hash(changed_finding)
        fabricated["plan_hash"] = canonical_hash({
            key: value for key, value in fabricated.items() if key != "plan_hash"
        })
        with self.assertRaisesRegex(ValueError, "exact packet finding"):
            self.service.submit(
                packet=self.packet, automation_plan=fabricated, actor=self.actor,
                finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING,
                reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
                reason="review", idempotency_key="fabricated",
            )

    def test_legacy_database_fails_fast_with_safe_migration_message(self):
        legacy = Path(self.temporary.name) / "legacy.db"
        with sqlite3.connect(legacy) as connection:
            connection.execute("""CREATE TABLE review_decisions (
                sequence INTEGER PRIMARY KEY, decision_id TEXT, tenant_id TEXT,
                workspace_id TEXT, packet_id TEXT, payload TEXT, decision_hash TEXT)""")
        with self.assertRaisesRegex(RuntimeError, "schema v1"):
            SQLiteDecisionRepository(legacy)


if __name__ == "__main__":
    unittest.main()
