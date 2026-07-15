from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .models import Claim, EncounterCase


@dataclass(frozen=True, slots=True)
class GroupingResult:
    drg: str
    estimated_payment_cents: int
    grouper_version: str

    def __post_init__(self) -> None:
        if not self.drg or not self.grouper_version:
            raise ValueError("grouping result requires a DRG and grouper version")
        if isinstance(self.estimated_payment_cents, bool) or self.estimated_payment_cents < 0:
            raise ValueError("estimated payment must be a non-negative integer number of cents")


@runtime_checkable
class Grouper(Protocol):
    """Boundary for a licensed, versioned DRG grouper and contract-aware pricer."""

    def group(self, case: EncounterCase, claim: Claim) -> GroupingResult: ...


class DeterministicDemoGrouper:
    """Fake integration adapter. Never use its values for real coding or billing."""

    version = "demo-0.2-not-for-billing"

    def group(self, case: EncounterCase, claim: Claim) -> GroupingResult:
        has_pressure_injury = any(code.startswith("L89") for code in claim.diagnoses)
        has_mcc = "L89.154" in claim.diagnoses
        if has_mcc:
            return GroupingResult("DEMO-290", 1_842_000, self.version)
        if has_pressure_injury:
            return GroupingResult("DEMO-291", 1_280_000, self.version)
        return GroupingResult("DEMO-292", 1_000_000, self.version)

