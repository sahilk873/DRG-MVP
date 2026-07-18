import unittest

from revenue_integrity.financial import ClaimLine, Denial, FinancialSnapshot, Remittance


class FinancialSnapshotTests(unittest.TestCase):
    def test_snapshot_validates_lineage_and_denied_amount(self):
        snapshot = FinancialSnapshot(
            "1.0.0", "payer-1", "claim-1",
            claim_lines=(ClaimLine("line-1", "97597", "CPT", 1, 10000),),
            denials=(Denial("denial-1", ("line-1",), "CO-50", "open", 4000),),
            remittances=(Remittance("remit-1", 6000, 0, "posted"),),
        )
        self.assertEqual(snapshot.denied_amount_cents, 4000)
        self.assertEqual(snapshot.to_dict()["claim_lines"][0]["code"], "97597")

    def test_denial_cannot_reference_unknown_line(self):
        with self.assertRaisesRegex(ValueError, "unknown claim line"):
            FinancialSnapshot(
                "1.0.0", "payer-1", "claim-1",
                claim_lines=(ClaimLine("line-1", "97597", "CPT", 1, 10000),),
                denials=(Denial("denial-1", ("missing",), "CO-50", "open"),),
            )

    def test_claim_line_rejects_invalid_units(self):
        with self.assertRaises(ValueError):
            ClaimLine("line-1", "97597", "CPT", 0, 10000)
