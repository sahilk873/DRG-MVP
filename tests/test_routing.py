import gc
import json
import tempfile
import warnings
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import unittest

from revenue_integrity.automation import build_automation_plan
from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import (
    ClinicalUrgency, Disposition, EncounterCase, Finding, GapDomain, ImpactStatus,
    LifecycleState,
)
from revenue_integrity.review_packet import build_review_packet
from revenue_integrity.routing import (
    RoutingLane,
    SQLiteRoutingOutbox,
    care_gap_lane_for_lifecycle,
    route_lane_for_lifecycle,
)


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

    def test_retrospective_default_routes_exactly_as_today(self):
        # Default (no lifecycle_state) and an explicit retrospective request must both
        # produce the byte-identical historical payload — including route_id and no lane key.
        default = self.outbox.enqueue_plan(self.plan, clock=self.clock)
        explicit = self.outbox.enqueue_plan(
            self.plan, clock=self.clock, lifecycle_state=LifecycleState.RETROSPECTIVE
        )
        self.assertEqual(default, explicit)
        self.assertEqual(len(default), 1)
        task = default[0]
        self.assertEqual(task.lane, RoutingLane.RETROSPECTIVE_CORRECTION.value)
        self.assertNotIn("lane", task.to_dict())
        self.assertEqual(task.action, "route_to_coding")

    def test_prospective_encounter_routes_eligible_finding_to_prospective_query(self):
        prospective = self.outbox.enqueue_plan(
            self.plan, clock=self.clock, lifecycle_state=LifecycleState.PROSPECTIVE
        )
        self.assertEqual(len(prospective), 1)
        task = prospective[0]
        self.assertEqual(task.lane, RoutingLane.PROSPECTIVE_QUERY.value)
        self.assertEqual(task.to_dict()["lane"], RoutingLane.PROSPECTIVE_QUERY.value)
        # The governed queue action is unchanged — the lane never bypasses review.
        self.assertEqual(task.action, "route_to_coding")
        self.assertEqual(task.queue, "coding")
        # A prospective query gets a distinct, deterministic route_id from the retrospective
        # one (fresh outbox so the tenant-scoped uniqueness constraint does not dedupe them).
        other = SQLiteRoutingOutbox(Path(self.temporary.name) / "routes-retro.db")
        retrospective = other.enqueue_plan(self.plan, clock=self.clock)
        self.assertNotEqual(task.route_id, retrospective[0].route_id)

    def test_concurrent_encounter_also_uses_prospective_query_lane(self):
        concurrent = self.outbox.enqueue_plan(
            self.plan, clock=self.clock, lifecycle_state=LifecycleState.CONCURRENT
        )
        self.assertEqual(len(concurrent), 1)
        self.assertEqual(concurrent[0].lane, RoutingLane.PROSPECTIVE_QUERY.value)

    def test_outbox_lifecycle_leaves_no_unclosed_connections(self):
        # Construct + enqueue + list + mark-delivered must each close their sqlite
        # handle: escalating ResourceWarning to an error and forcing a GC sweep would
        # surface any connection that was opened but never closed.
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            outbox = SQLiteRoutingOutbox(Path(self.temporary.name) / "lifecycle.db")
            enqueued = outbox.enqueue_plan(self.plan, clock=self.clock)
            pending = outbox.list_pending("tenant-a", "revenue")
            outbox.mark_delivered("tenant-a", "revenue", enqueued[0].route_id)
            drained = outbox.list_pending("tenant-a", "revenue")
            del outbox
            gc.collect()
        self.assertEqual(len(enqueued), 1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(drained, ())


class RoutingLaneMappingTests(unittest.TestCase):
    def test_lane_mapping_is_deterministic_per_lifecycle_state(self):
        self.assertEqual(
            route_lane_for_lifecycle(LifecycleState.PROSPECTIVE), RoutingLane.PROSPECTIVE_QUERY
        )
        self.assertEqual(
            route_lane_for_lifecycle(LifecycleState.CONCURRENT), RoutingLane.PROSPECTIVE_QUERY
        )
        self.assertEqual(
            route_lane_for_lifecycle(LifecycleState.RETROSPECTIVE),
            RoutingLane.RETROSPECTIVE_CORRECTION,
        )
        self.assertEqual(
            route_lane_for_lifecycle(LifecycleState.POST_BILL),
            RoutingLane.RETROSPECTIVE_CORRECTION,
        )

    def test_care_gap_lane_mapping_is_deterministic_and_distinct(self):
        self.assertEqual(
            care_gap_lane_for_lifecycle(LifecycleState.PROSPECTIVE),
            RoutingLane.CARE_GAP_ALERT_PROSPECTIVE,
        )
        self.assertEqual(
            care_gap_lane_for_lifecycle(LifecycleState.CONCURRENT),
            RoutingLane.CARE_GAP_ALERT_PROSPECTIVE,
        )
        self.assertEqual(
            care_gap_lane_for_lifecycle(LifecycleState.RETROSPECTIVE),
            RoutingLane.CARE_GAP_ALERT,
        )


class CareGapRoutingTests(unittest.TestCase):
    """Auto-routed clinical care gaps ride a dedicated CARE_GAP_ALERT outbox lane."""

    def setUp(self):
        payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text())
        self.case = EncounterCase.from_dict(payload)
        # A routine gap with a recommended action -> auto_routed to the care_gap queue.
        self.gap = Finding(
            finding_id="gap-routine", rule_id="CG-DET-010",
            rule_package_id="wound-care-clinical-care-gap", rule_package_version="1.0.0-demo",
            title="Routine reassessment gap", disposition=Disposition.CDI_QUERY, confidence=0.9,
            proposed_change={}, subject_ids=("wound:1",), assertion_ids=("AS-001",),
            evidence_ids=("EV-001",), contradicting_evidence_ids=(), rationale="No reassessment.",
            requires_human_review=True, submitted_drg=None, current_drg="DEMO-292",
            simulated_drg="DEMO-292", estimated_impact_cents=None,
            impact_status=ImpactStatus.NOT_APPLICABLE, grouper_version="demo-0.2-not-for-billing",
            gap_domain=GapDomain.MISSING_ACTION, alert_urgency=ClinicalUrgency.ROUTINE,
            recommended_action="Schedule a wound reassessment.",
        )
        packet = build_review_packet(
            case=self.case, case_payload=payload, rule_package=json.loads(
                (ROOT / "rules/wound_care_gaps_v1.json").read_text()
            ), findings=[self.gap], tenant_id="tenant-a", workspace_id="clinical",
        )
        self.plan = build_automation_plan(
            [self.gap], tenant_id="tenant-a", workspace_id="clinical",
            case_id=self.case.case_id, encounter_id=self.case.encounter_id,
            packet_id=packet["packet_id"], packet_hash=packet["provenance"]["packet_hash"],
        )
        self.temporary = tempfile.TemporaryDirectory()
        self.outbox = SQLiteRoutingOutbox(Path(self.temporary.name) / "gap-routes.db")
        self.clock = lambda: datetime(2026, 7, 17, 15, tzinfo=UTC)

    def tearDown(self):
        self.temporary.cleanup()

    def test_auto_routed_gap_uses_care_gap_alert_lane(self):
        routed = self.outbox.enqueue_plan(self.plan, clock=self.clock)
        self.assertEqual(len(routed), 1)
        task = routed[0]
        self.assertEqual(task.queue, "care_gap")
        self.assertEqual(task.action, "route_to_care_team")
        self.assertEqual(task.lane, RoutingLane.CARE_GAP_ALERT.value)
        self.assertEqual(task.to_dict()["lane"], RoutingLane.CARE_GAP_ALERT.value)

    def test_prospective_gap_uses_prospective_care_gap_lane(self):
        routed = self.outbox.enqueue_plan(
            self.plan, clock=self.clock, lifecycle_state=LifecycleState.PROSPECTIVE
        )
        self.assertEqual(routed[0].lane, RoutingLane.CARE_GAP_ALERT_PROSPECTIVE.value)

    def test_gap_route_preserves_pending_delivered_semantics(self):
        routed = self.outbox.enqueue_plan(self.plan, clock=self.clock)
        self.assertEqual(len(self.outbox.list_pending("tenant-a", "clinical")), 1)
        self.outbox.mark_delivered("tenant-a", "clinical", routed[0].route_id)
        self.assertEqual(self.outbox.list_pending("tenant-a", "clinical"), ())
        # Idempotent re-enqueue does not duplicate the delivered task.
        again = self.outbox.enqueue_plan(self.plan, clock=self.clock)
        self.assertEqual(again, ())


if __name__ == "__main__":
    unittest.main()
