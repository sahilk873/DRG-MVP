"""Deterministic realized-vs-estimated dollar reconciliation.

Pure function of the immutable :class:`FinancialSnapshot` plus a grouper
:class:`GroupingResult`. It never consumes model output and never mutates a
claim; it only aggregates integer-cent facts (estimated payment, actually-paid
remittances, denied amounts) into a signed variance and a sign-aware category.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .financial import FinancialSnapshot
from .grouper import GroupingResult

#: Reconciliation report schema version (independent of packet/automation schemas).
RECONCILIATION_SCHEMA_VERSION = "1.0.0"

#: Sign-aware variance categories.
CATEGORY_PAID_AS_EXPECTED = "paid_as_expected"
CATEGORY_UNDERPAID = "underpaid"
CATEGORY_OVERPAID = "overpaid"
CATEGORY_DENIED = "denied"


@dataclass(frozen=True, slots=True)
class FinancialReconciliation:
    """Signed reconciliation of realized dollars against the estimated payment."""

    schema_version: str
    estimated_cents: int
    realized_cents: int
    denied_cents: int
    variance_cents: int
    category: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "estimated_cents": self.estimated_cents,
            "realized_cents": self.realized_cents,
            "denied_cents": self.denied_cents,
            "variance_cents": self.variance_cents,
            "category": self.category,
        }


def _categorize(*, realized_cents: int, denied_cents: int, variance_cents: int) -> str:
    """Deterministically classify the variance with denial precedence."""
    if realized_cents == 0 and denied_cents > 0:
        return CATEGORY_DENIED
    if variance_cents < 0:
        return CATEGORY_UNDERPAID
    if variance_cents > 0:
        return CATEGORY_OVERPAID
    return CATEGORY_PAID_AS_EXPECTED


def reconcile_financials(
    grouping: GroupingResult,
    financial: FinancialSnapshot | None,
) -> FinancialReconciliation:
    """Reconcile realized dollars against the grouper's estimated payment.

    When ``financial`` is absent, realized and denied amounts are zero and the
    variance is reported against the estimate (a well-defined empty
    reconciliation). All arithmetic is integer-cent.
    """
    estimated_cents = grouping.estimated_payment_cents
    if financial is None:
        realized_cents = 0
        denied_cents = 0
    else:
        realized_cents = sum(item.paid_amount_cents for item in financial.remittances)
        denied_cents = financial.denied_amount_cents
    variance_cents = realized_cents - estimated_cents
    category = _categorize(
        realized_cents=realized_cents,
        denied_cents=denied_cents,
        variance_cents=variance_cents,
    )
    return FinancialReconciliation(
        schema_version=RECONCILIATION_SCHEMA_VERSION,
        estimated_cents=estimated_cents,
        realized_cents=realized_cents,
        denied_cents=denied_cents,
        variance_cents=variance_cents,
        category=category,
    )
