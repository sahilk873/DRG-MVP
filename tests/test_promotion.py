import unittest

from revenue_integrity.promotion import PatternProposal


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.proposal = PatternProposal(
            "proposal-1", "wound-stage-omission", ("case-1", "case-2"),
            {"rule_id": "approved-rule", "when": {"concept": "stage-4"}}, "1.0.0", .98, .8,
        )

    def test_promotion_requires_reviewer_and_threshold(self):
        with self.assertRaises(ValueError):
            self.proposal.approve("reviewer", minimum_precision=.99)
        approved = self.proposal.approve("reviewer")
        self.assertTrue(approved.is_approved)
        self.assertEqual(approved.approved_by, "reviewer")

    def test_unapproved_proposal_is_not_production_ready(self):
        self.assertFalse(self.proposal.is_approved)
