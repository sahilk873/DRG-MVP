"""Clinical-financial investigation contracts and deterministic validation.

LLM agents produce hypotheses; this module provides the typed trust boundary that
checks lineage and converts safe hypotheses into reviewable findings.  It is
deliberately independent of any model provider or workflow framework.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence

from .models import EncounterCase


class OpportunityCategory(StrEnum):
    MISSED_DIAGNOSIS = "missed_diagnosis"
    MISSED_PROCEDURE = "missed_procedure"
    MISSED_CHARGE = "missed_charge"
    CODING_SPECIFICITY = "coding_specificity"
    DRG_DISCREPANCY = "drg_discrepancy"
    DOCUMENTATION_GAP = "documentation_gap"
    DENIAL_RISK = "denial_risk"
    PAYMENT_VARIANCE = "payment_variance"
    UNSUPPORTED_BILLING = "unsupported_billing"


@dataclass(frozen=True, slots=True)
class InvestigationPacket:
    """Minimum-necessary, scoped view supplied to reconciliation agents."""

    packet_id: str
    case: EncounterCase
    financial: Mapping[str, Any] = field(default_factory=dict)
    payer_context: Mapping[str, Any] = field(default_factory=dict)
    policy_context: Mapping[str, Any] = field(default_factory=dict)
    data_quality: Mapping[str, Any] = field(default_factory=dict)
    allowed_data_views: tuple[str, ...] = ("clinical", "financial")

    def __post_init__(self) -> None:
        if not self.packet_id.strip():
            raise ValueError("packet_id must not be empty")
        if not self.allowed_data_views:
            raise ValueError("allowed_data_views must not be empty")
        if any(not item.strip() for item in self.allowed_data_views):
            raise ValueError("allowed_data_views entries must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "case_id": self.case.case_id,
            "encounter_id": self.case.encounter_id,
            "clinical": {
                "evidence": [e.evidence_id for e in self.case.evidence],
                "assertions": [a.assertion_id for a in self.case.assertions],
                "ontology": self.case.ontology.ontology_id,
            },
            "financial": dict(self.financial),
            "payer_context": dict(self.payer_context),
            "policy_context": dict(self.policy_context),
            "data_quality": dict(self.data_quality),
            "allowed_data_views": list(self.allowed_data_views),
        }


@dataclass(frozen=True, slots=True)
class ConfidenceDimensions:
    evidence: float
    semantic: float
    financial: float

    def __post_init__(self) -> None:
        if any(isinstance(v, bool) or not 0 <= v <= 1 for v in (self.evidence, self.semantic, self.financial)):
            raise ValueError("confidence dimensions must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class OpportunityHypothesis:
    hypothesis_id: str
    category: OpportunityCategory
    encounter_id: str
    hypothesis: str
    evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...] = ()
    assertion_ids: tuple[str, ...] = ()
    claim_line_ids: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    candidate_codes: tuple[str, ...] = ()
    candidate_drgs: tuple[str, ...] = ()
    required_validations: tuple[str, ...] = ()
    recommended_action: str = ""
    confidence: ConfidenceDimensions = field(default_factory=lambda: ConfidenceDimensions(0, 0, 0))
    materiality_cents: int | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.hypothesis_id.strip() or not self.encounter_id.strip() or not self.hypothesis.strip():
            raise ValueError("hypothesis_id, encounter_id and hypothesis are required")
        if not self.evidence_ids and self.category is not OpportunityCategory.PAYMENT_VARIANCE:
            raise ValueError("hypothesis must cite supporting evidence")
        if set(self.evidence_ids) & set(self.contradicting_evidence_ids):
            raise ValueError("evidence cannot be both supporting and contradicting")
        if self.materiality_cents is not None and self.materiality_cents < 0:
            raise ValueError("materiality_cents must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id, "category": self.category.value,
            "encounter_id": self.encounter_id, "hypothesis": self.hypothesis,
            "evidence_ids": list(self.evidence_ids),
            "contradicting_evidence_ids": list(self.contradicting_evidence_ids),
            "assertion_ids": list(self.assertion_ids), "claim_line_ids": list(self.claim_line_ids),
            "missing_information": list(self.missing_information), "candidate_codes": list(self.candidate_codes),
            "candidate_drgs": list(self.candidate_drgs), "required_validations": list(self.required_validations),
            "recommended_action": self.recommended_action,
            "confidence": {"evidence": self.confidence.evidence, "semantic": self.confidence.semantic, "financial": self.confidence.financial},
            "materiality_cents": self.materiality_cents, "provenance": dict(self.provenance),
        }


class HypothesisValidator(Protocol):
    def validate(self, packet: InvestigationPacket, hypothesis: OpportunityHypothesis) -> tuple[bool, tuple[str, ...]]: ...


class BasicHypothesisValidator:
    """Provider-independent safety checks; payer/grouper validators plug in here."""

    def validate(self, packet: InvestigationPacket, hypothesis: OpportunityHypothesis) -> tuple[bool, tuple[str, ...]]:
        if hypothesis.encounter_id != packet.case.encounter_id:
            return False, ("encounter_id does not match packet",)
        evidence = {item.evidence_id for item in packet.case.evidence}
        assertions = {item.assertion_id for item in packet.case.assertions}
        errors: list[str] = []
        errors.extend(f"unknown evidence: {item}" for item in set(hypothesis.evidence_ids + hypothesis.contradicting_evidence_ids) - evidence)
        errors.extend(f"unknown assertion: {item}" for item in set(hypothesis.assertion_ids) - assertions)
        if hypothesis.category is OpportunityCategory.DRG_DISCREPANCY and not packet.case.claim.drg:
            errors.append("DRG discrepancy requires a submitted DRG")
        return not errors, tuple(errors)


def validate_hypotheses(packet: InvestigationPacket, hypotheses: Sequence[OpportunityHypothesis], validator: HypothesisValidator | None = None) -> list[OpportunityHypothesis]:
    """Return only hypotheses safe to enter the governed review workflow."""
    checker = validator or BasicHypothesisValidator()
    accepted: list[OpportunityHypothesis] = []
    for item in hypotheses:
        valid, _errors = checker.validate(packet, item)
        if valid:
            accepted.append(item)
    return accepted
