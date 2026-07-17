import json
from dataclasses import replace
from pathlib import Path
import unittest

from revenue_integrity.audit import canonical_hash
from revenue_integrity.automation import (
    AutomationPolicy, AutomationTier, build_automation_plan, verify_automation_plan_hash,
)
from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import Disposition, EncounterCase, ImpactStatus
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

    def plan(self, findings=None, policy=None):
        return build_automation_plan(findings or [self.finding], policy=policy, **self.scope)

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

    def test_nested_policy_digest_is_independently_verified(self):
        plan = self.plan()
        plan["policy"]["quick_confirm_confidence"] = 0.1
        plan["plan_hash"] = canonical_hash({key: value for key, value in plan.items() if key != "plan_hash"})
        self.assertFalse(verify_automation_plan_hash(plan))


if __name__ == "__main__":
    unittest.main()
