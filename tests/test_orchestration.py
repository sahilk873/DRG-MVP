import json
import unittest
from pathlib import Path

from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.investigation import ConfidenceDimensions, InvestigationPacket, OpportunityCategory, OpportunityHypothesis
from revenue_integrity.orchestration import run_investigation
from revenue_integrity.models import EncounterCase


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
