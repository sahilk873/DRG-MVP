"""Deterministic backend handoff for clinical-financial investigations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .grouper import Grouper
from .investigation import InvestigationPacket, OpportunityHypothesis, promote_hypotheses_to_findings
from .models import Finding


@dataclass(frozen=True, slots=True)
class InvestigationRun:
    packet_id: str
    encounter_id: str
    findings: tuple[Finding, ...]
    rejected_hypothesis_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "encounter_id": self.encounter_id,
            "findings": [item.to_dict() for item in self.findings],
            "rejected_hypothesis_count": self.rejected_hypothesis_count,
        }


def run_investigation(
    packet: InvestigationPacket,
    hypotheses: Sequence[OpportunityHypothesis],
    grouper: Grouper,
) -> InvestigationRun:
    """Run the governed agent-to-review handoff without mutating source data."""
    findings = promote_hypotheses_to_findings(packet, hypotheses, grouper)
    return InvestigationRun(
        packet_id=packet.packet_id,
        encounter_id=packet.case.encounter_id,
        findings=tuple(findings),
        rejected_hypothesis_count=len(hypotheses) - len(findings),
    )
