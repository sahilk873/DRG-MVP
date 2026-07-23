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

    def test_from_dict_round_trips(self):
        payload = {
            "schema_version": "1.0.0", "payer_id": "payer-1", "claim_id": "claim-1",
            "claim_lines": [
                {"line_id": "line-1", "code": "97597", "code_system": "CPT", "units": 1, "charged_amount_cents": 10000},
            ],
            "denials": [
                {"denial_id": "denial-1", "line_ids": ["line-1"], "reason_code": "CO-50", "status": "open", "amount_cents": 4000},
            ],
            "remittances": [
                {"remittance_id": "remit-1", "paid_amount_cents": 6000, "adjustment_amount_cents": 0, "status": "posted", "denial_ids": ["denial-1"]},
            ],
        }
        snapshot = FinancialSnapshot.from_dict(payload)
        self.assertEqual(snapshot.claim_id, "claim-1")
        self.assertEqual(snapshot.denied_amount_cents, 4000)
        self.assertEqual(snapshot.claim_lines[0].code, "97597")
        self.assertEqual(snapshot.remittances[0].denial_ids, ("denial-1",))

    def test_from_dict_rejects_unknown_field(self):
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            FinancialSnapshot.from_dict(
                {"schema_version": "1.0.0", "payer_id": "p", "claim_id": "c", "bogus": 1}
            )

    def test_from_dict_rejects_denial_referencing_unknown_line(self):
        with self.assertRaisesRegex(ValueError, "unknown claim line"):
            FinancialSnapshot.from_dict({
                "schema_version": "1.0.0", "payer_id": "p", "claim_id": "c",
                "claim_lines": [
                    {"line_id": "line-1", "code": "97597", "code_system": "CPT", "units": 1, "charged_amount_cents": 100},
                ],
                "denials": [{"denial_id": "d1", "line_ids": ["missing"], "reason_code": "CO-50", "status": "open"}],
            })
