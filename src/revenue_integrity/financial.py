"""Versioned normalized financial context for reconciliation workflows."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _cents(value: Any, name: str, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer number of cents")
    return value


@dataclass(frozen=True, slots=True)
class ClaimLine:
    line_id: str
    code: str
    code_system: str
    units: int
    charged_amount_cents: int
    allowed_amount_cents: int | None = None
    status: str = "submitted"

    def __post_init__(self) -> None:
        _text(self.line_id, "line_id")
        _text(self.code, "code")
        _text(self.code_system, "code_system")
        _text(self.status, "status")
        if isinstance(self.units, bool) or not isinstance(self.units, int) or self.units <= 0:
            raise ValueError("units must be a positive integer")
        _cents(self.charged_amount_cents, "charged_amount_cents")
        _cents(self.allowed_amount_cents, "allowed_amount_cents", allow_none=True)


@dataclass(frozen=True, slots=True)
class Denial:
    denial_id: str
    line_ids: tuple[str, ...]
    reason_code: str
    status: str
    amount_cents: int | None = None

    def __post_init__(self) -> None:
        _text(self.denial_id, "denial_id")
        _text(self.reason_code, "reason_code")
        _text(self.status, "status")
        if not self.line_ids or len(set(self.line_ids)) != len(self.line_ids):
            raise ValueError("denial line_ids must be unique and non-empty")
        for line_id in self.line_ids:
            _text(line_id, "denial.line_ids")
        _cents(self.amount_cents, "amount_cents", allow_none=True)


@dataclass(frozen=True, slots=True)
class Remittance:
    remittance_id: str
    paid_amount_cents: int
    adjustment_amount_cents: int
    status: str
    denial_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.remittance_id, "remittance_id")
        _text(self.status, "status")
        _cents(self.paid_amount_cents, "paid_amount_cents")
        _cents(self.adjustment_amount_cents, "adjustment_amount_cents")
        if len(set(self.denial_ids)) != len(self.denial_ids):
            raise ValueError("remittance denial_ids must be unique")


@dataclass(frozen=True, slots=True)
class FinancialSnapshot:
    """Canonical claim-line, denial, and remittance view for one encounter."""

    schema_version: str
    payer_id: str
    claim_id: str
    claim_lines: tuple[ClaimLine, ...] = ()
    denials: tuple[Denial, ...] = ()
    remittances: tuple[Remittance, ...] = ()
    contract_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.schema_version, "schema_version")
        _text(self.payer_id, "payer_id")
        _text(self.claim_id, "claim_id")
        line_ids = [line.line_id for line in self.claim_lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("claim line IDs must be unique")
        known = set(line_ids)
        for denial in self.denials:
            if set(denial.line_ids) - known:
                raise ValueError("denial references an unknown claim line")

    @property
    def denied_amount_cents(self) -> int:
        return sum(item.amount_cents or 0 for item in self.denials)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "payer_id": self.payer_id,
            "claim_id": self.claim_id,
            "claim_lines": [item.__dict__ if hasattr(item, "__dict__") else {
                "line_id": item.line_id, "code": item.code, "code_system": item.code_system,
                "units": item.units, "charged_amount_cents": item.charged_amount_cents,
                "allowed_amount_cents": item.allowed_amount_cents, "status": item.status,
            } for item in self.claim_lines],
            "denials": [{"denial_id": item.denial_id, "line_ids": list(item.line_ids), "reason_code": item.reason_code, "status": item.status, "amount_cents": item.amount_cents} for item in self.denials],
            "remittances": [{"remittance_id": item.remittance_id, "paid_amount_cents": item.paid_amount_cents, "adjustment_amount_cents": item.adjustment_amount_cents, "status": item.status, "denial_ids": list(item.denial_ids)} for item in self.remittances],
            "contract_context": dict(self.contract_context),
        }
