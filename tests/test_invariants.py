"""Executable guards for the CORE INVARIANT.

docs/ARCHITECTURE.md: "No language-model output can directly execute code, recreate or
change a claim, assign a DRG, calculate reimbursement, or bypass required review."

That invariant is enforced today by guards scattered across investigation.py,
review_packet.py, rules.py and engine.py. This suite asserts them AS A SET, so a future
refactor that silently relaxes one turns a green build red. If a change here fails, it is
a governance decision — not a test to "fix".
"""
import json
import unittest
from pathlib import Path

from revenue_integrity.audit import canonical_hash
from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.investigation import (
    ConfidenceDimensions,
    InvestigationPacket,
    OpportunityCategory,
    OpportunityHypothesis,
    promote_hypotheses_to_findings,
)
from revenue_integrity.models import EncounterCase
from revenue_integrity.review_packet import build_review_packet
from revenue_integrity.rules import SUPPORTED_CHANGE_KEYS, SUPPORTED_OPERATORS

ROOT = Path(__file__).parents[1]


def _case():
    return EncounterCase.from_dict(json.loads((ROOT / "examples/case_pressure_injury.json").read_text()))


def _rules():
    return json.loads((ROOT / "rules/wound_care_v1.json").read_text())


class DslSurfaceIsLocked(unittest.TestCase):
    def test_supported_operators_membership_is_frozen(self):
        # Adding/removing an operator is a governed contract change: bump versions and
        # update this snapshot deliberately.
        self.assertEqual(
            set(SUPPORTED_OPERATORS),
            {"eq", "ne", "gte", "lte", "in", "contains", "not_contains", "exists",
             "between", "starts_with", "count_gte", "count_lte", "subsumed_by",
             # Phase 1 clinical_care_gap additions: native temporal + percentage-change
             # operators whose arithmetic is computed deterministically in the engine.
             "elapsed_days_gte", "elapsed_days_lte", "absent_within_days",
             "pct_change_gte", "pct_change_lte"},
        )

    def test_supported_change_keys_membership_is_frozen(self):
        self.assertEqual(
            set(SUPPORTED_CHANGE_KEYS),
            {"add_diagnoses", "remove_diagnoses", "add_procedures",
             "remove_procedures", "add_charges", "remove_charges"},
        )


class ClaimAffectingFindingsAlwaysRequireReview(unittest.TestCase):
    def test_engine_findings_with_a_change_require_human_review(self):
        findings = RuleEngine(_rules(), DeterministicDemoGrouper()).evaluate(_case())
        self.assertTrue(findings)
        for finding in findings:
            if finding.proposed_change:
                self.assertTrue(
                    finding.requires_human_review,
                    f"{finding.finding_id} proposes a claim change without requiring review",
                )

    def test_promoted_hypotheses_with_a_change_require_human_review(self):
        case = _case()
        packet = InvestigationPacket("packet-invariant", case)
        hypotheses = [
            OpportunityHypothesis(
                "opp-1", OpportunityCategory.MISSED_DIAGNOSIS, case.encounter_id,
                "Possible omitted diagnosis", (case.evidence[0].evidence_id,),
                candidate_codes=("L89.154",), confidence=ConfidenceDimensions(.9, .9, .8),
            ),
        ]
        findings = promote_hypotheses_to_findings(packet, hypotheses, DeterministicDemoGrouper())
        self.assertTrue(findings)
        for finding in findings:
            self.assertTrue(finding.requires_human_review)


class ModelPathNeverMutatesTheClaim(unittest.TestCase):
    def test_promotion_leaves_the_source_claim_canonically_identical(self):
        case = _case()
        packet = InvestigationPacket("packet-immutable", case)
        before = canonical_hash(case.claim.__dict__ if hasattr(case.claim, "__dict__") else str(case.claim))
        before_codes = (case.claim.diagnoses, case.claim.procedures, case.claim.charges, case.claim.drg)
        hypotheses = [
            OpportunityHypothesis(
                "opp-mut", OpportunityCategory.MISSED_DIAGNOSIS, case.encounter_id,
                "Possible omitted diagnosis", (case.evidence[0].evidence_id,),
                candidate_codes=("L89.999",), confidence=ConfidenceDimensions(.9, .9, .8),
            ),
        ]
        findings = promote_hypotheses_to_findings(packet, hypotheses, DeterministicDemoGrouper())
        self.assertTrue(findings)
        after_codes = (case.claim.diagnoses, case.claim.procedures, case.claim.charges, case.claim.drg)
        self.assertEqual(before_codes, after_codes, "the source claim must never be mutated by promotion")
        self.assertEqual(before, canonical_hash(case.claim.__dict__ if hasattr(case.claim, "__dict__") else str(case.claim)))
        # The candidate code the model proposed must NOT have leaked into the claim.
        self.assertNotIn("L89.999", case.claim.diagnoses)


class ReviewPacketForbidsClaimMutation(unittest.TestCase):
    def test_every_built_packet_blocks_claim_mutation(self):
        case = _case()
        payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text())
        findings = RuleEngine(_rules(), DeterministicDemoGrouper()).evaluate(case)
        packet = build_review_packet(
            case=case, case_payload=payload, rule_package=_rules(), findings=findings,
            tenant_id="tenant-inv", workspace_id="workspace-inv", environment="synthetic",
        )
        self.assertFalse(packet["controls"]["claim_mutation_allowed"])
        self.assertTrue(packet["controls"]["human_review_required"])


if __name__ == "__main__":
    unittest.main()
