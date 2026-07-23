import json
import unittest
from pathlib import Path

from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.investigation import ConfidenceDimensions, InvestigationPacket, OpportunityCategory, OpportunityHypothesis
from revenue_integrity.orchestration import run_investigation
from revenue_integrity.models import Disposition, EncounterCase


class InvestigationOrchestrationTests(unittest.TestCase):
    def test_run_returns_reviewable_findings_and_rejection_count(self):
        payload = json.loads((Path(__file__).parents[1] / "examples/case_pressure_injury.json").read_text())
        case = EncounterCase.from_dict(payload)
        packet = InvestigationPacket("packet-orchestration", case)
        hypothesis = OpportunityHypothesis(
            "opp-orchestration", OpportunityCategory.MISSED_DIAGNOSIS, case.encounter_id,
            "Possible omitted diagnosis", (case.evidence[0].evidence_id,), candidate_codes=("L89.154",),
            confidence=ConfidenceDimensions(.9, .9, .8),
        )
        rejected = OpportunityHypothesis(
            "opp-rejected", OpportunityCategory.MISSED_CHARGE, "wrong-encounter", "Invalid", ("missing",),
        )
        result = run_investigation(packet, [hypothesis, rejected], DeterministicDemoGrouper())
        self.assertEqual(result.encounter_id, case.encounter_id)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.rejected_hypothesis_count, 1)


class AgentDisagreementTests(unittest.TestCase):
    def _case(self) -> EncounterCase:
        payload = json.loads(
            (Path(__file__).parents[1] / "examples/case_pressure_injury_v2.json").read_text()
        )
        return EncounterCase.from_dict(payload)

    def test_conflicting_validated_outputs_emit_single_escalation_finding(self):
        case = self._case()
        packet = InvestigationPacket("packet-disagreement", case)
        # Extractor asserts an opportunity grounded in EV-001 and treats EV-002 as
        # contradicting; the critic reaches the opposite position on EV-002.
        extractor = OpportunityHypothesis(
            "opp-extractor", OpportunityCategory.MISSED_DIAGNOSIS, case.encounter_id,
            "Possible omitted diagnosis", ("EV-001",),
            contradicting_evidence_ids=("EV-002",),
            assertion_ids=("AS-001",), candidate_codes=("L89.154",),
            confidence=ConfidenceDimensions(.9, .9, .8),
            provenance={"agent_id": "encounter-extractor"},
        )
        critic = OpportunityHypothesis(
            "opp-critic", OpportunityCategory.DOCUMENTATION_GAP, case.encounter_id,
            "Documentation supports the opposite reading", ("EV-002",),
            assertion_ids=("AS-001",),
            confidence=ConfidenceDimensions(.8, .8, .8),
            provenance={"agent_id": "investigation-critic"},
        )
        result = run_investigation(packet, [extractor, critic], DeterministicDemoGrouper())
        disagreements = [f for f in result.findings if f.rule_id == "AGENT_DISAGREEMENT"]
        self.assertEqual(len(disagreements), 1)
        finding = disagreements[0]
        # Routed to human escalation: compliance disposition + review required.
        self.assertEqual(finding.disposition, Disposition.COMPLIANCE_REVIEW)
        self.assertTrue(finding.requires_human_review)
        # Conflicting agent identities + evidence carried in metadata.
        self.assertEqual(
            finding.derivation["conflicting_agents"],
            ["encounter-extractor", "investigation-critic"],
        )
        self.assertEqual(finding.derivation["conflicting_evidence_ids"], ["EV-002"])
        # Subject/assertion/evidence lineage retained.
        self.assertIn("AS-001", finding.assertion_ids)
        self.assertIn("wound:1", finding.subject_ids)
        self.assertIn("EV-002", finding.evidence_ids)
        # Never mutates the claim.
        self.assertEqual(finding.proposed_change, {})

    def test_disagreement_finding_escalates_in_automation_plan(self):
        from revenue_integrity.audit import canonical_hash
        from revenue_integrity.automation import AutomationTier, build_automation_plan

        case = self._case()
        packet = InvestigationPacket("packet-disagreement", case)
        extractor = OpportunityHypothesis(
            "opp-extractor", OpportunityCategory.MISSED_DIAGNOSIS, case.encounter_id,
            "Possible omitted diagnosis", ("EV-001",),
            contradicting_evidence_ids=("EV-002",), assertion_ids=("AS-001",),
            candidate_codes=("L89.154",), confidence=ConfidenceDimensions(.9, .9, .8),
            provenance={"agent_id": "encounter-extractor"},
        )
        critic = OpportunityHypothesis(
            "opp-critic", OpportunityCategory.DOCUMENTATION_GAP, case.encounter_id,
            "Opposite reading", ("EV-002",), assertion_ids=("AS-001",),
            confidence=ConfidenceDimensions(.8, .8, .8),
            provenance={"agent_id": "investigation-critic"},
        )
        result = run_investigation(packet, [extractor, critic], DeterministicDemoGrouper())
        disagreement = next(f for f in result.findings if f.rule_id == "AGENT_DISAGREEMENT")
        plan = build_automation_plan(
            [disagreement],
            tenant_id="t", workspace_id="w", case_id=case.case_id,
            encounter_id=case.encounter_id, packet_id=packet.packet_id,
            packet_hash=canonical_hash({"p": packet.packet_id}), case=case,
        )
        entry = next(f for f in plan["findings"] if f["finding_id"] == disagreement.finding_id)
        self.assertEqual(entry["tier"], AutomationTier.ESCALATED.value)
        self.assertIn(disagreement.finding_id, plan["review_now_finding_ids"])

    def test_agreeing_validated_outputs_emit_no_disagreement_finding(self):
        case = self._case()
        packet = InvestigationPacket("packet-agree", case)
        one = OpportunityHypothesis(
            "opp-one", OpportunityCategory.MISSED_DIAGNOSIS, case.encounter_id,
            "Possible omitted diagnosis", ("EV-001",), assertion_ids=("AS-001",),
            candidate_codes=("L89.154",), confidence=ConfidenceDimensions(.9, .9, .8),
            provenance={"agent_id": "encounter-extractor"},
        )
        two = OpportunityHypothesis(
            "opp-two", OpportunityCategory.DOCUMENTATION_GAP, case.encounter_id,
            "Consistent supporting reading", ("EV-002",), assertion_ids=("AS-001",),
            confidence=ConfidenceDimensions(.8, .8, .8),
            provenance={"agent_id": "investigation-critic"},
        )
        result = run_investigation(packet, [one, two], DeterministicDemoGrouper())
        disagreements = [f for f in result.findings if f.rule_id == "AGENT_DISAGREEMENT"]
        self.assertEqual(len(disagreements), 0)
