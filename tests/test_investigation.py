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
)
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
