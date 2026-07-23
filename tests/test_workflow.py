import gc
import json
import sqlite3
import tempfile
import warnings
from datetime import UTC, datetime
from pathlib import Path
import unittest

from revenue_integrity.audit import canonical_hash
from revenue_integrity.engine import RuleEngine
from revenue_integrity.automation import build_automation_plan
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import (
    ClinicalUrgency, Disposition, EncounterCase, Finding, GapDomain, GapStatus, ImpactStatus,
)
from revenue_integrity.review_packet import build_review_packet
from revenue_integrity.workflow import (
    DecisionReasonCode, GapClosureAction, GapClosureService, ReviewAction, ReviewerIdentity,
    ReviewerRole, ReviewWorkflowService, SQLiteDecisionRepository, SQLiteGapClosureRepository,
    summarize_decision_feedback, verify_decision_chain, verify_gap_closure_chain,
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
        connection = sqlite3.connect(legacy)
        try:
            connection.execute("""CREATE TABLE review_decisions (
                sequence INTEGER PRIMARY KEY, decision_id TEXT, tenant_id TEXT,
                workspace_id TEXT, packet_id TEXT, payload TEXT, decision_hash TEXT)""")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(RuntimeError, "schema v1"):
            SQLiteDecisionRepository(legacy)

    def test_repository_lifecycle_leaves_no_unclosed_connections(self):
        finding_id = self.packet["findings"][0]["finding_id"]
        # Exercise construct + write + read paths, then prove none of them leaked a
        # sqlite connection: escalating ResourceWarning to an error and forcing a GC
        # sweep would surface any handle that was opened but never closed.
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            repository = SQLiteDecisionRepository(Path(self.temporary.name) / "lifecycle.db")
            service = ReviewWorkflowService(
                repository, lambda: datetime(2026, 7, 17, 13, tzinfo=UTC)
            )
            decision = service.submit(
                packet=self.packet, automation_plan=self.plan, actor=self.actor,
                finding_id=finding_id, action=ReviewAction.ROUTE_TO_CODING,
                reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
                reason="lifecycle check", idempotency_key="lifecycle-1",
            )
            listed = repository.list_for_packet("tenant-a", "revenue", self.packet["packet_id"])
            found = repository.find_by_idempotency("tenant-a", "revenue", "lifecycle-1")
            del repository, service
            gc.collect()
        self.assertEqual([decision], list(listed))
        self.assertEqual(found, decision)

    def test_revenue_submit_refuses_a_clinical_care_gap_finding(self):
        # GOVERNANCE SEGREGATION: a clinical_care_gap finding is decided only via the
        # GapClosureService by the CARE_GAP_COORDINATOR role, and its hash-chained
        # GapClosureRecord lifecycle. An escalated/focused gap carries "dismiss_with_reason"
        # in its automation allowed_actions, so a reviewer who happens to hold revenue
        # dismiss authority (e.g. CODER) could otherwise dispose of a clinical gap through
        # the revenue decision path, bypassing the coordinator role and the gap lifecycle.
        # ReviewWorkflowService.submit must reject any finding whose gap_domain is set and
        # write nothing.
        gap = Finding(
            finding_id="gap-rev-1", rule_id="CG-CMP-05",
            rule_package_id="wound-care-clinical-care-gap", rule_package_version="1.0.0-demo",
            title="Stalled pressure injury", disposition=Disposition.CDI_QUERY, confidence=0.9,
            proposed_change={}, subject_ids=("wound:1",), assertion_ids=("AS-001",),
            evidence_ids=("EV-001",), contradicting_evidence_ids=(), rationale="No reassessment.",
            requires_human_review=True, submitted_drg=None, current_drg="DEMO-292",
            simulated_drg="DEMO-292", estimated_impact_cents=None,
            impact_status=ImpactStatus.NOT_APPLICABLE, grouper_version="demo-0.2-not-for-billing",
            gap_domain=GapDomain.DELAYED_ACTION, alert_urgency=ClinicalUrgency.URGENT,
            recommended_action="Reassess the wound today.",
        )
        case_payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text())
        case = EncounterCase.from_dict(case_payload)
        packet = build_review_packet(
            case=case, case_payload=case_payload,
            rule_package=json.loads((ROOT / "rules/wound_care_gaps_v1.json").read_text()),
            findings=[gap], tenant_id="tenant-a", workspace_id="revenue",
            environment="synthetic", clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        )
        plan = build_automation_plan(
            [gap], tenant_id="tenant-a", workspace_id="revenue", case_id=case.case_id,
            encounter_id=case.encounter_id, packet_id=packet["packet_id"],
            packet_hash=packet["provenance"]["packet_hash"],
        )
        with self.assertRaisesRegex(PermissionError, "clinical_care_gap"):
            self.service.submit(
                packet=packet, automation_plan=plan, actor=self.actor,
                finding_id="gap-rev-1", action=ReviewAction.DISMISS_WITH_REASON,
                reason_code=DecisionReasonCode.OTHER_GOVERNED,
                reason="attempting revenue-path disposal of a gap", idempotency_key="gap-via-rev",
            )
        # Nothing was persisted on the revenue decision chain.
        self.assertEqual(
            self.repository.list_for_packet("tenant-a", "revenue", packet["packet_id"]), ()
        )


class GapClosureWorkflowTests(unittest.TestCase):
    """Close / exception / withdraw transitions for clinical care gaps.

    THE WALL: a gap closure targets only a clinical_care_gap finding, never carries a claim
    mutation, and must be recorded by an authorized clinical role. The history is
    hash-chained and tamper-evident, exactly like the revenue decision chain.
    """

    def setUp(self):
        self.case_payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text())
        self.case = EncounterCase.from_dict(self.case_payload)
        self.gap = Finding(
            finding_id="gap-close-1", rule_id="CG-CMP-05",
            rule_package_id="wound-care-clinical-care-gap", rule_package_version="1.0.0-demo",
            title="Stalled pressure injury", disposition=Disposition.CDI_QUERY, confidence=0.9,
            proposed_change={}, subject_ids=("wound:1",), assertion_ids=("AS-001",),
            evidence_ids=("EV-001",), contradicting_evidence_ids=(), rationale="No reassessment.",
            requires_human_review=True, submitted_drg=None, current_drg="DEMO-292",
            simulated_drg="DEMO-292", estimated_impact_cents=None,
            impact_status=ImpactStatus.NOT_APPLICABLE, grouper_version="demo-0.2-not-for-billing",
            gap_domain=GapDomain.DELAYED_ACTION, alert_urgency=ClinicalUrgency.URGENT,
            recommended_action="Reassess the wound today.",
        )
        self.packet = build_review_packet(
            case=self.case, case_payload=self.case_payload,
            rule_package=json.loads((ROOT / "rules/wound_care_gaps_v1.json").read_text()),
            findings=[self.gap], tenant_id="tenant-a", workspace_id="clinical",
            environment="synthetic", clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        )
        self.plan = build_automation_plan(
            [self.gap], tenant_id="tenant-a", workspace_id="clinical", case_id=self.case.case_id,
            encounter_id=self.case.encounter_id, packet_id=self.packet["packet_id"],
            packet_hash=self.packet["provenance"]["packet_hash"],
        )
        self.coordinator = ReviewerIdentity(
            "coord-1", "tenant-a", "clinical", (ReviewerRole.CARE_GAP_COORDINATOR,)
        )
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = SQLiteGapClosureRepository(Path(self.temporary.name) / "gap-closures.db")
        self.service = GapClosureService(
            self.repository, lambda: datetime(2026, 7, 17, 13, tzinfo=UTC)
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _submit(self, action, *, actor=None, idempotency_key="k1", barrier_code=None, claim_mutation=None, reason="clinician decision"):
        return self.service.submit(
            packet=self.packet, automation_plan=self.plan, actor=actor or self.coordinator,
            finding_id="gap-close-1", action=action, reason=reason,
            idempotency_key=idempotency_key, barrier_code=barrier_code, claim_mutation=claim_mutation,
        )

    def test_close_folds_into_closed_status_with_timestamp_and_barrier(self):
        record = self._submit(GapClosureAction.CLOSE, barrier_code="BARRIER-STAFFING")
        self.assertEqual(record.gap_status, GapStatus.CLOSED)
        self.assertEqual(record.action, GapClosureAction.CLOSE)
        self.assertEqual(record.barrier_code, "BARRIER-STAFFING")
        self.assertEqual(record.closed_at, "2026-07-17T13:00:00Z")
        chain = self.repository.list_for_packet("tenant-a", "clinical", self.packet["packet_id"])
        self.assertEqual([record], list(chain))
        self.assertTrue(verify_gap_closure_chain(chain))

    def test_exception_transition_stamps_closed_at(self):
        record = self._submit(GapClosureAction.EXCEPTION)
        self.assertEqual(record.gap_status, GapStatus.EXCEPTION)
        self.assertEqual(record.closed_at, "2026-07-17T13:00:00Z")

    def test_withdraw_transition_has_no_closure_timestamp(self):
        record = self._submit(GapClosureAction.WITHDRAW)
        self.assertEqual(record.gap_status, GapStatus.WITHDRAWN)
        self.assertIsNone(record.closed_at)

    def test_closure_is_idempotent_and_hash_reproducible(self):
        first = self._submit(GapClosureAction.CLOSE, idempotency_key="same")
        retried = self._submit(GapClosureAction.CLOSE, idempotency_key="same")
        self.assertEqual(first, retried)
        # A fresh service over a fresh DB re-derives a byte-identical record hash.
        other_dir = tempfile.TemporaryDirectory()
        self.addCleanup(other_dir.cleanup)
        fresh = GapClosureService(
            SQLiteGapClosureRepository(Path(other_dir.name) / "c.db"),
            lambda: datetime(2026, 7, 17, 13, tzinfo=UTC),
        )
        replayed = fresh.submit(
            packet=self.packet, automation_plan=self.plan, actor=self.coordinator,
            finding_id="gap-close-1", action=GapClosureAction.CLOSE, reason="clinician decision",
            idempotency_key="same",
        )
        self.assertEqual(first.record_hash, replayed.record_hash)

    def test_second_terminal_decision_on_same_gap_is_rejected(self):
        self._submit(GapClosureAction.CLOSE, idempotency_key="first")
        with self.assertRaisesRegex(ValueError, "terminal decision"):
            self._submit(GapClosureAction.WITHDRAW, idempotency_key="second")

    # ---- negative tests: the wall ----

    def test_gap_closure_carrying_a_claim_mutation_is_rejected(self):
        with self.assertRaisesRegex(PermissionError, "must not carry a claim mutation"):
            self._submit(
                GapClosureAction.CLOSE,
                claim_mutation={"add_diagnoses": ["L89.154"]},
            )
        # Nothing was persisted.
        self.assertEqual(
            self.repository.list_for_packet("tenant-a", "clinical", self.packet["packet_id"]), ()
        )

    def test_unauthorized_role_cannot_close_a_gap(self):
        coder = ReviewerIdentity("coder-1", "tenant-a", "clinical", (ReviewerRole.CODER,))
        with self.assertRaisesRegex(PermissionError, "roles do not permit"):
            self._submit(GapClosureAction.CLOSE, actor=coder)
        reader = ReviewerIdentity("reader-1", "tenant-a", "clinical", (ReviewerRole.READ_ONLY,))
        with self.assertRaisesRegex(PermissionError, "roles do not permit"):
            self._submit(GapClosureAction.CLOSE, actor=reader)

    def test_cross_tenant_gap_closure_is_rejected(self):
        outsider = ReviewerIdentity(
            "coord-2", "tenant-b", "clinical", (ReviewerRole.CARE_GAP_COORDINATOR,)
        )
        with self.assertRaisesRegex(PermissionError, "tenant scope"):
            self._submit(GapClosureAction.CLOSE, actor=outsider)

    def test_gap_closure_cannot_target_a_revenue_finding(self):
        revenue_finding = RuleEngine(
            json.loads((ROOT / "rules/wound_care_v1.json").read_text()), DeterministicDemoGrouper()
        ).evaluate(self.case)[0]
        packet = build_review_packet(
            case=self.case, case_payload=self.case_payload,
            rule_package=json.loads((ROOT / "rules/wound_care_v1.json").read_text()),
            findings=[revenue_finding], tenant_id="tenant-a", workspace_id="revenue",
            environment="synthetic",
        )
        plan = build_automation_plan(
            [revenue_finding], tenant_id="tenant-a", workspace_id="revenue",
            case_id=self.case.case_id, encounter_id=self.case.encounter_id,
            packet_id=packet["packet_id"], packet_hash=packet["provenance"]["packet_hash"],
        )
        coordinator = ReviewerIdentity(
            "coord-1", "tenant-a", "revenue", (ReviewerRole.CARE_GAP_COORDINATOR,)
        )
        with self.assertRaisesRegex(ValueError, "clinical_care_gap finding"):
            self.service.submit(
                packet=packet, automation_plan=plan, actor=coordinator,
                finding_id=revenue_finding.finding_id, action=GapClosureAction.CLOSE,
                reason="attempt", idempotency_key="rev",
            )

    def test_tampered_packet_fails_before_persistence(self):
        tampered = json.loads(json.dumps(self.packet))
        tampered["controls"]["claim_mutation_allowed"] = True
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.service.submit(
                packet=tampered, automation_plan=self.plan, actor=self.coordinator,
                finding_id="gap-close-1", action=GapClosureAction.CLOSE, reason="x",
                idempotency_key="tampered",
            )


if __name__ == "__main__":
    unittest.main()
