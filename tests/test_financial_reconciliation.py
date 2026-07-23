import unittest

from revenue_integrity.financial import Denial, FinancialSnapshot, Remittance
from revenue_integrity.financial_reconciliation import (
    CATEGORY_DENIED,
    CATEGORY_OVERPAID,
    CATEGORY_PAID_AS_EXPECTED,
    CATEGORY_UNDERPAID,
    RECONCILIATION_SCHEMA_VERSION,
    reconcile_financials,
)
from revenue_integrity.grouper import GroupingResult


def _grouping(estimated_cents: int) -> GroupingResult:
    return GroupingResult(drg="DRG-1", estimated_payment_cents=estimated_cents, grouper_version="demo-v1")


def _snapshot(*, remittances=(), denials=(), claim_lines=None) -> FinancialSnapshot:
    return FinancialSnapshot(
        "1.0.0", "payer-1", "claim-1",
        claim_lines=tuple(claim_lines or ()),
        denials=tuple(denials),
        remittances=tuple(remittances),
    )


class FinancialReconciliationTests(unittest.TestCase):
    def test_underpaid(self):
        snapshot = _snapshot(remittances=(Remittance("r1", 6000, 0, "posted"),))
        result = reconcile_financials(_grouping(10000), snapshot)
        self.assertEqual(result.estimated_cents, 10000)
        self.assertEqual(result.realized_cents, 6000)
        self.assertEqual(result.denied_cents, 0)
        self.assertEqual(result.variance_cents, -4000)
        self.assertEqual(result.category, CATEGORY_UNDERPAID)

    def test_overpaid(self):
        snapshot = _snapshot(remittances=(Remittance("r1", 12000, 0, "posted"),))
        result = reconcile_financials(_grouping(10000), snapshot)
        self.assertEqual(result.realized_cents, 12000)
        self.assertEqual(result.variance_cents, 2000)
        self.assertEqual(result.category, CATEGORY_OVERPAID)

    def test_paid_as_expected(self):
        snapshot = _snapshot(remittances=(Remittance("r1", 10000, 0, "posted"),))
        result = reconcile_financials(_grouping(10000), snapshot)
        self.assertEqual(result.variance_cents, 0)
        self.assertEqual(result.category, CATEGORY_PAID_AS_EXPECTED)

    def test_denied(self):
        from revenue_integrity.financial import ClaimLine

        snapshot = FinancialSnapshot(
            "1.0.0", "payer-1", "claim-1",
            claim_lines=(ClaimLine("line-1", "97597", "CPT", 1, 10000),),
            denials=(Denial("d1", ("line-1",), "CO-50", "open", 10000),),
            remittances=(),
        )
        result = reconcile_financials(_grouping(10000), snapshot)
        self.assertEqual(result.realized_cents, 0)
        self.assertEqual(result.denied_cents, 10000)
        self.assertEqual(result.variance_cents, -10000)
        self.assertEqual(result.category, CATEGORY_DENIED)

    def test_absent_financial_zeroed(self):
        result = reconcile_financials(_grouping(10000), None)
        self.assertEqual(result.estimated_cents, 10000)
        self.assertEqual(result.realized_cents, 0)
        self.assertEqual(result.denied_cents, 0)
        self.assertEqual(result.variance_cents, -10000)
        self.assertEqual(result.category, CATEGORY_UNDERPAID)

    def test_to_dict_shape(self):
        result = reconcile_financials(_grouping(0), None)
        payload = result.to_dict()
        self.assertEqual(payload["schema_version"], RECONCILIATION_SCHEMA_VERSION)
        self.assertEqual(
            set(payload),
            {"schema_version", "estimated_cents", "realized_cents", "denied_cents", "variance_cents", "category"},
        )
        self.assertEqual(payload["category"], CATEGORY_PAID_AS_EXPECTED)


if __name__ == "__main__":
    unittest.main()
