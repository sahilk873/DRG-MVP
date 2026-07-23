import json
from dataclasses import replace
from pathlib import Path
import unittest

from revenue_integrity.audit import canonical_hash
from revenue_integrity.automation import (
    AUTOMATION_SCHEMA_VERSION, AutomationPolicy, AutomationQueue, AutomationReason,
    AutomationTier, build_automation_plan, verify_automation_plan_hash,
)
from revenue_integrity.engine import RuleEngine
from revenue_integrity.financial import ClaimLine, Denial, FinancialSnapshot
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import (
    ClinicalUrgency, Disposition, EncounterCase, ExceptionType, Finding, GapDomain,
    GapStatus, ImpactStatus,
)
from revenue_integrity.review_packet import build_review_packet

ROOT = Path(__file__).parents[1]


class AutomationPlanTests(unittest.TestCase):
    def setUp(self):
        self.case_payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text())
        self.rules = json.loads((ROOT / "rules/wound_care_v1.json").read_text())
        self.case = EncounterCase.from_dict(self.case_payload)
        self.finding = RuleEngine(self.rules, DeterministicDemoGrouper()).evaluate(self.case)[0]
        packet = build_review_packet(
            case=self.case, case_payload=self.case_payload, rule_package=self.rules,
            findings=[self.finding], tenant_id="tenant-a", workspace_id="revenue",
        )
        self.scope = {
            "tenant_id": "tenant-a", "workspace_id": "revenue",
            "case_id": self.case.case_id, "encounter_id": self.case.encounter_id,
            "packet_id": packet["packet_id"], "packet_hash": packet["provenance"]["packet_hash"],
        }

    def plan(self, findings=None, policy=None, case=None):
        return build_automation_plan(findings or [self.finding], policy=policy, case=case, **self.scope)

    def _case_with_denied_line(self, line_id):
        snapshot = FinancialSnapshot(
            schema_version="1.0.0", payer_id="payer-demo", claim_id="claim-demo",
            claim_lines=(ClaimLine(line_id, "99213", "CPT", 1, 12_000),),
            denials=(Denial("denial-1", (line_id,), "CO-50", "open", 45_000),),
        )
        return replace(self.case, financial=snapshot)

    def test_supported_drg_change_becomes_thirty_second_confirmation(self):
        plan = self.plan()
        item = plan["findings"][0]
        self.assertEqual(item["tier"], AutomationTier.QUICK_CONFIRM.value)
        self.assertEqual(item["recommended_action"], "route_to_coding")
        self.assertEqual(item["estimated_review_seconds"], 30)
        self.assertTrue(verify_automation_plan_hash(plan))

    def test_contradiction_and_unknown_impact_force_mandatory_escalation(self):
        contradiction = replace(self.finding, contradicting_evidence_ids=("EV-X",))
        unknown = replace(
            self.finding, finding_id="finding-unknown", estimated_impact_cents=None,
            impact_status=ImpactStatus.UNAVAILABLE,
        )
        plan = self.plan([contradiction, unknown], AutomationPolicy(max_review_cases=1, max_review_seconds=30))
        tiers = {item["finding_id"]: item["tier"] for item in plan["findings"]}
        self.assertEqual(tiers[contradiction.finding_id], "escalated")
        self.assertEqual(tiers[unknown.finding_id], "escalated")
        self.assertEqual(set(plan["review_now_finding_ids"]), {contradiction.finding_id, unknown.finding_id})

    def test_contradiction_cannot_be_hidden_by_no_opportunity_disposition(self):
        contradiction = replace(
            self.finding, disposition=Disposition.NO_OPPORTUNITY,
            proposed_change={}, current_drg="SAME", simulated_drg="SAME",
            contradicting_evidence_ids=("EV-X",),
        )
        item = self.plan([contradiction])["findings"][0]
        self.assertEqual(item["tier"], "escalated")
        self.assertIn("contradictory_evidence", item["reason_codes"])

    def test_insufficient_evidence_is_enriched_not_silently_suppressed(self):
        finding = replace(
            self.finding, disposition=Disposition.INSUFFICIENT_EVIDENCE,
            proposed_change={}, current_drg="SAME", simulated_drg="SAME",
            estimated_impact_cents=None, impact_status=ImpactStatus.UNAVAILABLE,
        )
        item = self.plan([finding])["findings"][0]
        self.assertEqual(item["tier"], "needs_enrichment")
        self.assertEqual(item["allowed_actions"], [])

        with_change = replace(
            finding, finding_id="finding-insufficient-with-change",
            proposed_change={"add_diagnoses": ["L89.154"]},
        )
        changed_item = self.plan([with_change])["findings"][0]
        self.assertEqual(changed_item["tier"], "needs_enrichment")
        self.assertEqual(changed_item["allowed_actions"], [])

    def test_exact_duplicates_consolidate_stably_without_double_counting(self):
        duplicate = replace(self.finding, finding_id="finding-duplicate")
        forward = self.plan([self.finding, duplicate])
        reverse = self.plan([duplicate, self.finding])
        self.assertEqual(forward, reverse)
        self.assertEqual(forward["metrics"]["consolidated_findings"], 1)
        self.assertEqual(forward["metrics"]["suppressed"], 1)
        self.assertEqual(len(forward["review_now_finding_ids"]), 1)

    def test_duplicate_ids_and_materially_different_estimates_do_not_hide_work(self):
        with self.assertRaisesRegex(ValueError, "unique finding_id"):
            self.plan([self.finding, self.finding])
        different = replace(
            self.finding, finding_id="finding-different-impact",
            estimated_impact_cents=self.finding.estimated_impact_cents + 1,
        )
        plan = self.plan([self.finding, different])
        self.assertEqual(plan["metrics"]["suppressed"], 0)
        self.assertEqual(len(plan["review_now_finding_ids"]), 2)

    def test_budget_defers_but_never_suppresses_supported_work(self):
        second = replace(
            self.finding, finding_id="finding-second",
            proposed_change={"add_diagnoses": ["L89.153"]},
        )
        plan = self.plan(
            [self.finding, second],
            AutomationPolicy(max_review_cases=1, max_review_seconds=30),
        )
        self.assertEqual(len(plan["review_now_finding_ids"]), 1)
        self.assertEqual(len(plan["deferred_finding_ids"]), 1)
        deferred = next(item for item in plan["findings"] if item["finding_id"] in plan["deferred_finding_ids"])
        self.assertEqual(deferred["tier"], "quick_confirm")

    def test_automation_never_mutates_the_claim_snapshot(self):
        before = canonical_hash(self.case_payload["claim"])
        self.plan()
        self.assertEqual(canonical_hash(self.case_payload["claim"]), before)

    def test_scope_and_policy_changes_change_plan_hash(self):
        first = self.plan()
        changed_scope = {**self.scope, "tenant_id": "tenant-b"}
        second = build_automation_plan([self.finding], **changed_scope)
        changed_policy = self.plan(policy=AutomationPolicy(version="1.0.1"))
        self.assertNotEqual(first["plan_hash"], second["plan_hash"])
        self.assertNotEqual(first["plan_hash"], changed_policy["plan_hash"])

    def test_priority_components_sum_to_score_and_impact_is_uncapped(self):
        # $2,000,000.00 impact => impact_weight 2,000,000 (no 10,000 ceiling).
        big = replace(self.finding, estimated_impact_cents=200_000_000)
        item = self.plan([big])["findings"][0]
        components = item["priority_components"]
        self.assertEqual(item["priority_score"], sum(components.values()))
        self.assertEqual(components["impact_weight"], 2_000_000)
        self.assertEqual(set(components), {"tier_weight", "confidence_weight", "impact_weight", "urgency_weight"})

    def test_large_dollar_finding_outranks_small_dollar_at_equal_tier(self):
        big = replace(self.finding, finding_id="finding-big", estimated_impact_cents=200_000_000)
        small = replace(self.finding, finding_id="finding-small", estimated_impact_cents=5_000_000)
        plan = self.plan([big, small])
        scores = {item["finding_id"]: item["priority_score"] for item in plan["findings"]}
        self.assertGreater(scores["finding-big"], scores["finding-small"])
        # The larger recovery must be reviewed first, not buried by a saturated weight.
        self.assertEqual(plan["review_now_finding_ids"][0], "finding-big")

    def test_reviewer_effort_rollup_is_deterministic_and_hash_covered(self):
        duplicate = replace(self.finding, finding_id="finding-duplicate")
        plan = self.plan([self.finding, duplicate])
        effort = plan["metrics"]["reviewer_effort"]
        self.assertTrue(effort["is_estimate"])
        self.assertEqual(effort["consolidated_duplicate_count"], 1)
        self.assertEqual(effort["no_touch_finding_count"], 1)
        self.assertEqual(effort["no_touch_rate"], 0.5)
        self.assertEqual(effort["seconds_avoided_estimate"], 180)
        # reviewer_effort lives inside plan_body, so tampering breaks the plan hash.
        plan["metrics"]["reviewer_effort"]["no_touch_rate"] = 0.99
        self.assertFalse(verify_automation_plan_hash(plan))

    def test_nested_policy_digest_is_independently_verified(self):
        plan = self.plan()
        plan["policy"]["quick_confirm_confidence"] = 0.1
        plan["plan_hash"] = canonical_hash({key: value for key, value in plan.items() if key != "plan_hash"})
        self.assertFalse(verify_automation_plan_hash(plan))

    def test_schema_version_is_bumped(self):
        self.assertEqual(AUTOMATION_SCHEMA_VERSION, "1.3.0")
        self.assertEqual(self.plan()["automation_schema_version"], "1.3.0")

    def test_urgency_weight_is_populated_deterministically(self):
        # quick_confirm (rank 3 -> 3000) + $8,420 impact (8 steps of $1,000 -> 800) + no denial.
        item = self.plan()["findings"][0]
        self.assertEqual(item["tier"], AutomationTier.QUICK_CONFIRM.value)
        self.assertEqual(item["priority_components"]["urgency_weight"], 3_800)
        self.assertEqual(item["priority_score"], sum(item["priority_components"].values()))

    def test_urgency_weight_is_zero_for_suppressed_and_duplicate(self):
        no_op = replace(
            self.finding, disposition=Disposition.NO_OPPORTUNITY,
            proposed_change={}, current_drg="SAME", simulated_drg="SAME",
        )
        suppressed = self.plan([no_op])["findings"][0]
        self.assertEqual(suppressed["tier"], "suppressed")
        self.assertEqual(suppressed["priority_components"]["urgency_weight"], 0)

        duplicate = replace(self.finding, finding_id="finding-duplicate")
        plan = self.plan([self.finding, duplicate])
        dup_item = next(item for item in plan["findings"] if item["duplicate_of"])
        self.assertEqual(dup_item["priority_components"]["urgency_weight"], 0)

    def test_unknown_impact_uses_neutral_urgency_floor_not_zero(self):
        unknown = replace(
            self.finding, estimated_impact_cents=None, impact_status=ImpactStatus.UNAVAILABLE,
        )
        item = self.plan([unknown])["findings"][0]
        # escalated rank 4 -> 4000, unknown-impact floor 500, no denial.
        self.assertEqual(item["tier"], "escalated")
        self.assertEqual(item["priority_components"]["urgency_weight"], 4_500)

    def test_denial_exposure_raises_urgency_and_adds_signal(self):
        line_id = "charge-line-denied"
        bound = replace(self.finding, charge_line_refs=(line_id,))
        case = self._case_with_denied_line(line_id)
        with_denial = self.plan([bound], case=case)["findings"][0]
        without_denial = self.plan([bound])["findings"][0]
        # +10,000 denial bump, and the governed routing signal is attached.
        self.assertEqual(
            with_denial["priority_components"]["urgency_weight"]
            - without_denial["priority_components"]["urgency_weight"],
            10_000,
        )
        self.assertIn(AutomationReason.DENIAL_EXPOSURE.value, with_denial["reason_codes"])
        self.assertNotIn(AutomationReason.DENIAL_EXPOSURE.value, without_denial["reason_codes"])
        self.assertGreater(with_denial["priority_score"], without_denial["priority_score"])

    def test_denial_exposure_requires_line_intersection(self):
        # A denial on an unrelated line must not flag a finding bound to a different line.
        bound_elsewhere = replace(self.finding, charge_line_refs=("charge-line-other",))
        case = self._case_with_denied_line("charge-line-denied")
        item = self.plan([bound_elsewhere], case=case)["findings"][0]
        self.assertNotIn(AutomationReason.DENIAL_EXPOSURE.value, item["reason_codes"])
        self.assertEqual(item["priority_components"]["urgency_weight"], 3_800)

    def test_denial_exposure_never_suppresses_review_required_work(self):
        line_id = "charge-line-denied"
        # A no-opportunity finding stays suppressed even when its line is denied — denial
        # exposure may raise urgency but must never bypass or resurrect suppression logic.
        no_op = replace(
            self.finding, disposition=Disposition.NO_OPPORTUNITY,
            proposed_change={}, current_drg="SAME", simulated_drg="SAME",
            charge_line_refs=(line_id,),
        )
        case = self._case_with_denied_line(line_id)
        item = self.plan([no_op], case=case)["findings"][0]
        self.assertEqual(item["tier"], "suppressed")
        self.assertNotIn(AutomationReason.DENIAL_EXPOSURE.value, item["reason_codes"])
        self.assertEqual(item["priority_components"]["urgency_weight"], 0)

    def test_denial_exposure_changes_plan_hash(self):
        line_id = "charge-line-denied"
        bound = replace(self.finding, charge_line_refs=(line_id,))
        without = self.plan([bound])
        with_denial = self.plan([bound], case=self._case_with_denied_line(line_id))
        self.assertNotEqual(without["plan_hash"], with_denial["plan_hash"])
        self.assertTrue(verify_automation_plan_hash(with_denial))


class CareGapAutomationTests(unittest.TestCase):
    """CARE_GAP lane tiering: analytics identify gaps, clinicians decide.

    A clinical_care_gap finding never mutates a claim (empty proposed_change) and always
    requires human review unless a confirmed, undisputed exception has resolved it. Revenue
    tiering is exercised by :class:`AutomationPlanTests`; these tests only cover the gap lane.
    """

    def setUp(self):
        self.case_payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text())
        self.case = EncounterCase.from_dict(self.case_payload)
        self.scope = {
            "tenant_id": "tenant-a", "workspace_id": "clinical",
            "case_id": self.case.case_id, "encounter_id": self.case.encounter_id,
            "packet_id": "packet-" + "0" * 20, "packet_hash": "a" * 64,
        }

    def _gap_finding(self, **overrides):
        base = dict(
            finding_id="gap-1", rule_id="CG-DET-003", rule_package_id="wound-care-clinical-care-gap",
            rule_package_version="1.0.0-demo", title="Stalled pressure injury",
            disposition=Disposition.CDI_QUERY, confidence=0.9, proposed_change={},
            subject_ids=("wound:1",), assertion_ids=("AS-001",), evidence_ids=("EV-001",),
            contradicting_evidence_ids=(), rationale="No reassessment recorded.",
            requires_human_review=True, submitted_drg=None, current_drg="DEMO-292",
            simulated_drg="DEMO-292", estimated_impact_cents=None,
            impact_status=ImpactStatus.NOT_APPLICABLE, grouper_version="demo-0.2-not-for-billing",
            gap_domain=GapDomain.DELAYED_ACTION, expected_action="reassessment",
            actual_action="none", timing_window_days=3,
            alert_urgency=ClinicalUrgency.URGENT, recommended_action="Reassess the wound today.",
            clinical_impact="Delay risks deterioration.",
        )
        base.update(overrides)
        return Finding(**base)

    def plan(self, findings, policy=None):
        return build_automation_plan(findings, policy=policy, case=self.case, **self.scope)

    def test_emergent_and_urgent_gaps_escalate_to_care_gap_queue(self):
        for urgency in (ClinicalUrgency.EMERGENT, ClinicalUrgency.URGENT):
            with self.subTest(urgency=urgency):
                finding = self._gap_finding(finding_id=f"gap-{urgency.value}", alert_urgency=urgency)
                item = self.plan([finding])["findings"][0]
                self.assertEqual(item["tier"], AutomationTier.ESCALATED.value)
                self.assertEqual(item["queue"], AutomationQueue.CARE_GAP.value)
                self.assertEqual(item["recommended_action"], "route_to_care_team")
                self.assertIn(AutomationReason.EMERGENT_CARE_GAP.value, item["reason_codes"])

    def test_same_day_gap_gets_focused_review(self):
        finding = self._gap_finding(alert_urgency=ClinicalUrgency.SAME_DAY)
        item = self.plan([finding])["findings"][0]
        self.assertEqual(item["tier"], AutomationTier.FOCUSED_REVIEW.value)
        self.assertEqual(item["queue"], AutomationQueue.CARE_GAP.value)
        self.assertIn(AutomationReason.SAME_DAY_CARE_GAP.value, item["reason_codes"])

    def test_routine_gap_auto_routes_to_care_team(self):
        finding = self._gap_finding(alert_urgency=ClinicalUrgency.ROUTINE)
        plan = self.plan([finding])
        item = plan["findings"][0]
        self.assertEqual(item["tier"], AutomationTier.AUTO_ROUTED.value)
        self.assertEqual(item["queue"], AutomationQueue.CARE_GAP.value)
        self.assertEqual(item["recommended_action"], "route_to_care_team")
        self.assertIn(AutomationReason.ROUTINE_CARE_GAP.value, item["reason_codes"])
        # Auto-routed gaps consume no reviewer budget and are not queued for a person.
        self.assertNotIn(item["finding_id"], plan["review_now_finding_ids"])

    def test_routine_gap_without_action_is_held_for_enrichment(self):
        finding = self._gap_finding(alert_urgency=ClinicalUrgency.ROUTINE, recommended_action=None)
        item = self.plan([finding])["findings"][0]
        self.assertEqual(item["tier"], AutomationTier.NEEDS_ENRICHMENT.value)
        self.assertEqual(item["queue"], AutomationQueue.NONE.value)
        self.assertIn(AutomationReason.GAP_NEEDS_ACTION.value, item["reason_codes"])

    def test_confirmed_undisputed_exception_is_suppressed_not_reviewed(self):
        # A confirmed, undisputed exception on an otherwise-urgent gap must NOT reach
        # focused review or escalation — it downgrades to a suppressed exception.
        finding = self._gap_finding(
            alert_urgency=ClinicalUrgency.URGENT,
            exception_checks=(
                {"exception_type": ExceptionType.HOSPICE, "evidence_id": "EV-001", "status": "confirmed"},
            ),
            gap_status=GapStatus.EXCEPTION,
        )
        plan = self.plan([finding])
        item = plan["findings"][0]
        self.assertEqual(item["tier"], AutomationTier.SUPPRESSED.value)
        self.assertEqual(item["queue"], AutomationQueue.NONE.value)
        self.assertIsNone(item["recommended_action"])
        self.assertEqual(item["allowed_actions"], [])
        self.assertIn(AutomationReason.GAP_EXCEPTION_CONFIRMED.value, item["reason_codes"])
        self.assertNotIn(item["finding_id"], plan["review_now_finding_ids"])

    def test_disputed_exception_keeps_gap_live(self):
        # A single disputed check overrides a confirmed one; the gap still routes to review.
        finding = self._gap_finding(
            alert_urgency=ClinicalUrgency.URGENT,
            exception_checks=(
                {"exception_type": ExceptionType.HOSPICE, "evidence_id": "EV-001", "status": "confirmed"},
                {"exception_type": ExceptionType.PATIENT_REFUSAL, "evidence_id": "EV-001", "status": "disputed"},
            ),
        )
        item = self.plan([finding])["findings"][0]
        self.assertEqual(item["tier"], AutomationTier.ESCALATED.value)
        self.assertEqual(item["queue"], AutomationQueue.CARE_GAP.value)

    def test_gap_finding_never_touches_revenue_queues(self):
        item = self.plan([self._gap_finding()])["findings"][0]
        self.assertNotIn(
            item["queue"],
            {q.value for q in (AutomationQueue.CODING, AutomationQueue.CDI,
                               AutomationQueue.CHARGE, AutomationQueue.COMPLIANCE)},
        )

    def test_gap_worklist_metrics_are_deterministic_and_hash_covered(self):
        findings = [
            self._gap_finding(finding_id="gap-open-urgent", alert_urgency=ClinicalUrgency.URGENT,
                              timing_window_days=4, barrier_code="BARRIER-STAFFING"),
            self._gap_finding(finding_id="gap-open-sameday", alert_urgency=ClinicalUrgency.SAME_DAY,
                              timing_window_days=2, barrier_code="BARRIER-STAFFING"),
            self._gap_finding(
                finding_id="gap-closed", alert_urgency=ClinicalUrgency.ROUTINE, timing_window_days=6,
                exception_checks=(
                    {"exception_type": ExceptionType.HOSPICE, "evidence_id": "EV-001", "status": "confirmed"},
                ),
                gap_status=GapStatus.CLOSED, closed_at="2026-07-20T00:00:00Z",
                barrier_code="BARRIER-ACCESS",
            ),
        ]
        first = self.plan(findings)["metrics"]["gap_worklist"]
        # Reordering the input must not change the deterministic rollup.
        second = self.plan(list(reversed(findings)))["metrics"]["gap_worklist"]
        self.assertEqual(first, second)
        self.assertEqual(first["total_gaps"], 3)
        self.assertEqual(first["open_high_risk_gaps"], 2)  # urgent + same_day, both open
        self.assertEqual(first["gaps_closed_pct"], round(1 / 3, 4))
        # Metric renamed: it aggregates the rule-CONFIGURED expected window, not observed lateness.
        self.assertEqual(first["avg_expected_window_days"], round((4 + 2 + 6) / 3, 2))
        self.assertNotIn("avg_delay_days", first)
        self.assertEqual(first["median_closure_days"], 6)  # only the closed gap contributes
        self.assertEqual(first["top_barrier"], "BARRIER-STAFFING")  # appears twice
        # urgent/same_day/routine each appear once -> 3-way tie broken alphabetically.
        self.assertEqual(first["top_alert_reason"], "routine")
        self.assertTrue(first["is_estimate"])
        # The section lives in plan_body, so tampering breaks the plan hash.
        plan = self.plan(findings)
        plan["metrics"]["gap_worklist"]["open_high_risk_gaps"] = 99
        self.assertFalse(verify_automation_plan_hash(plan))

    def test_gap_worklist_preserves_fractional_timing_windows(self):
        # Fractional windows (e.g. 0.5d) must NOT be truncated to 0 by int() — a
        # same-day/twice-daily window is a legitimate fractional day.
        findings = [
            self._gap_finding(finding_id="gap-half", alert_urgency=ClinicalUrgency.SAME_DAY,
                              timing_window_days=0.5),
            self._gap_finding(finding_id="gap-1p5", alert_urgency=ClinicalUrgency.SAME_DAY,
                              timing_window_days=1.5),
            self._gap_finding(
                finding_id="gap-closed-half", alert_urgency=ClinicalUrgency.ROUTINE,
                timing_window_days=0.5,
                exception_checks=(
                    {"exception_type": ExceptionType.HOSPICE, "evidence_id": "EV-001", "status": "confirmed"},
                ),
                gap_status=GapStatus.CLOSED, closed_at="2026-07-20T00:00:00Z",
            ),
        ]
        worklist = self.plan(findings)["metrics"]["gap_worklist"]
        # (0.5 + 1.5 + 0.5) / 3 = 0.8333 — would be 0.67 if 0.5 were truncated to 0.
        self.assertEqual(worklist["avg_expected_window_days"], round((0.5 + 1.5 + 0.5) / 3, 2))
        # Only the closed 0.5d gap contributes; must survive as 0.5, not truncate to 0.
        self.assertEqual(worklist["median_closure_days"], 0.5)
        # Determinism under reordering with floats.
        self.assertEqual(worklist, self.plan(list(reversed(findings)))["metrics"]["gap_worklist"])
        self.assertTrue(verify_automation_plan_hash(self.plan(findings)))

    def test_gap_worklist_is_empty_but_present_on_revenue_only_plans(self):
        # A revenue_integrity-only plan still carries a stable, empty gap worklist.
        finding = RuleEngine(
            json.loads((ROOT / "rules/wound_care_v1.json").read_text()), DeterministicDemoGrouper()
        ).evaluate(self.case)[0]
        worklist = self.plan([finding])["metrics"]["gap_worklist"]
        self.assertEqual(worklist["total_gaps"], 0)
        self.assertEqual(worklist["open_high_risk_gaps"], 0)
        self.assertEqual(worklist["gaps_closed_pct"], 0.0)
        self.assertEqual(worklist["avg_expected_window_days"], 0.0)
        self.assertEqual(worklist["median_closure_days"], 0)
        self.assertIsNone(worklist["top_alert_reason"])
        self.assertIsNone(worklist["top_barrier"])

    def test_gap_plan_hash_verifies(self):
        plan = self.plan([self._gap_finding()])
        self.assertTrue(verify_automation_plan_hash(plan))


if __name__ == "__main__":
    unittest.main()
