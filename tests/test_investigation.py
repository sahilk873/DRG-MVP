import json
import unittest
from pathlib import Path

from revenue_integrity.investigation import (
    BasicHypothesisValidator,
    ConfidenceDimensions,
    InvestigationPacket,
    OpportunityCategory,
    OpportunityHypothesis,
    validate_hypotheses,
    promote_hypotheses_to_findings,
)
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import EncounterCase


class InvestigationContractTests(unittest.TestCase):
    def setUp(self):
        payload = json.loads((Path(__file__).parents[1] / "examples/case_pressure_injury.json").read_text())
        self.case = EncounterCase.from_dict(payload)
        self.packet = InvestigationPacket("packet-1", self.case, financial={"claim_status": "submitted"})

    def test_hypothesis_is_lineage_validated(self):
        item = OpportunityHypothesis(
            "opp-1", OpportunityCategory.MISSED_DIAGNOSIS, self.case.encounter_id,
            "A documented diagnosis is absent from the claim", (self.case.evidence[0].evidence_id,),
            assertion_ids=(self.case.assertions[0].assertion_id,),
            confidence=ConfidenceDimensions(.9, .8, .7),
        )
        self.assertEqual(validate_hypotheses(self.packet, [item]), [item])

    def test_unknown_evidence_is_rejected(self):
        item = OpportunityHypothesis(
            "opp-2", OpportunityCategory.MISSED_CHARGE, self.case.encounter_id,
            "A charge may be missing", ("unknown",), confidence=ConfidenceDimensions(.9, .8, .7),
        )
        self.assertEqual(validate_hypotheses(self.packet, [item]), [])

    def test_conflicting_evidence_fails_closed(self):
        with self.assertRaises(ValueError):
            OpportunityHypothesis(
                "opp-3", OpportunityCategory.DOCUMENTATION_GAP, self.case.encounter_id,
                "Conflicting documentation", ("EV-001",), ("EV-001",), confidence=ConfidenceDimensions(.1, .1, .1),
            )

    def test_wrong_encounter_is_rejected(self):
        item = OpportunityHypothesis(
            "opp-4", OpportunityCategory.DRG_DISCREPANCY, "other", "wrong encounter", (self.case.evidence[0].evidence_id,),
            confidence=ConfidenceDimensions(1, 1, 1),
        )
        self.assertEqual(BasicHypothesisValidator().validate(self.packet, item)[0], False)

    def test_agent_hypothesis_promotes_to_simulated_finding(self):
        item = OpportunityHypothesis(
            "opp-5", OpportunityCategory.MISSED_DIAGNOSIS, self.case.encounter_id,
            "The documented stage is absent from the claim", (self.case.evidence[0].evidence_id,),
            assertion_ids=(self.case.assertions[0].assertion_id,), candidate_codes=("L89.154",),
            confidence=ConfidenceDimensions(.95, .9, .8),
        )
        findings = promote_hypotheses_to_findings(self.packet, [item], DeterministicDemoGrouper())
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].proposed_change, {"add_diagnoses": ["L89.154"]})
        self.assertEqual(findings[0].current_drg, "DEMO-292")
        self.assertEqual(findings[0].simulated_drg, "DEMO-290")
        self.assertEqual(findings[0].estimated_impact_cents, 842000)
        self.assertTrue(findings[0].requires_human_review)

    def test_agent_hypothesis_never_mutates_original_claim(self):
        item = OpportunityHypothesis(
            "opp-6", OpportunityCategory.MISSED_DIAGNOSIS, self.case.encounter_id,
            "Possible missing diagnosis", (self.case.evidence[0].evidence_id,), candidate_codes=("L89.154",),
        )
        promote_hypotheses_to_findings(self.packet, [item], DeterministicDemoGrouper())
        self.assertNotIn("L89.154", self.case.claim.diagnoses)
