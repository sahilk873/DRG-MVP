import json
import unittest
from dataclasses import replace
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
from revenue_integrity.financial import ClaimLine, Denial, FinancialSnapshot
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


class FinancialLineageValidatorTests(unittest.TestCase):
    def setUp(self):
        payload = json.loads((Path(__file__).parents[1] / "examples/case_pressure_injury.json").read_text())
        base = EncounterCase.from_dict(payload)
        charge_lines = (
            ClaimLine("line-1", "97597", "CPT", 1, 10000),
            ClaimLine("line-2", "97598", "CPT", 1, 5000),
        )
        financial = FinancialSnapshot(
            schema_version="1.0.0",
            payer_id="PAYER-DEMO",
            claim_id="CLM-DEMO-001",
            claim_lines=charge_lines,
            denials=(Denial("denial-1", ("line-2",), "CO-50", "open", 5000),),
        )
        claim = replace(base.claim, charge_lines=charge_lines)
        self.case = replace(base, claim=claim, financial=financial)
        self.packet = InvestigationPacket("packet-fin", self.case)

    def test_unknown_claim_line_id_is_rejected(self):
        item = OpportunityHypothesis(
            "opp-line-unknown", OpportunityCategory.MISSED_CHARGE, self.case.encounter_id,
            "Charge cites a line that does not exist", (self.case.evidence[0].evidence_id,),
            claim_line_ids=("line-does-not-exist",), confidence=ConfidenceDimensions(.9, .8, .7),
        )
        valid, errors = BasicHypothesisValidator().validate(self.packet, item)
        self.assertFalse(valid)
        self.assertTrue(any("unknown claim_line_id" in e for e in errors), errors)
        self.assertEqual(validate_hypotheses(self.packet, [item]), [])

    def test_denial_risk_without_backing_denied_line_is_rejected(self):
        item = OpportunityHypothesis(
            "opp-denial-nobacking", OpportunityCategory.DENIAL_RISK, self.case.encounter_id,
            "Denial risk but cites a non-denied line", (self.case.evidence[0].evidence_id,),
            claim_line_ids=("line-1",), confidence=ConfidenceDimensions(.9, .8, .7),
        )
        valid, errors = BasicHypothesisValidator().validate(self.packet, item)
        self.assertFalse(valid)
        self.assertTrue(any("denied line" in e for e in errors), errors)

    def test_denial_risk_without_financial_snapshot_is_rejected(self):
        packet = InvestigationPacket("packet-nofin", replace(self.case, financial=None))
        item = OpportunityHypothesis(
            "opp-denial-nofin", OpportunityCategory.DENIAL_RISK, self.case.encounter_id,
            "Denial risk with no financial snapshot", (self.case.evidence[0].evidence_id,),
            claim_line_ids=("line-2",), confidence=ConfidenceDimensions(.9, .8, .7),
        )
        valid, errors = BasicHypothesisValidator().validate(packet, item)
        self.assertFalse(valid)
        self.assertTrue(any("financial snapshot" in e for e in errors), errors)

    def test_wellformed_denial_risk_passes(self):
        item = OpportunityHypothesis(
            "opp-denial-ok", OpportunityCategory.DENIAL_RISK, self.case.encounter_id,
            "Denial risk citing a genuinely denied line", (self.case.evidence[0].evidence_id,),
            claim_line_ids=("line-2",), confidence=ConfidenceDimensions(.9, .8, .7),
        )
        valid, errors = BasicHypothesisValidator().validate(self.packet, item)
        self.assertTrue(valid, errors)
        self.assertEqual(validate_hypotheses(self.packet, [item]), [item])
