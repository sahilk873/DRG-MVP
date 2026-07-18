"""Governed path from recurring discoveries to versioned deterministic assets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class PatternProposal:
    proposal_id: str
    pattern_key: str
    evidence_case_ids: tuple[str, ...]
    proposed_rule: Mapping[str, Any]
    ontology_version: str
    evaluation_precision: float
    evaluation_recall: float
    approved_by: str | None = None

    def __post_init__(self) -> None:
        for name in ("proposal_id", "pattern_key", "ontology_version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be non-empty")
        if not self.evidence_case_ids:
            raise ValueError("pattern proposal requires evidence cases")
        for metric in (self.evaluation_precision, self.evaluation_recall):
            if isinstance(metric, bool) or not 0 <= metric <= 1:
                raise ValueError("evaluation metrics must be between 0 and 1")

    def approve(self, reviewer_id: str, *, minimum_precision: float = 0.95) -> "PatternProposal":
        if not reviewer_id.strip():
            raise ValueError("reviewer_id must be non-empty")
        if self.evaluation_precision < minimum_precision:
            raise ValueError("pattern does not meet the promotion precision threshold")
        if not self.proposed_rule:
            raise ValueError("pattern proposal has no deterministic rule payload")
        return PatternProposal(
            proposal_id=self.proposal_id, pattern_key=self.pattern_key,
            evidence_case_ids=self.evidence_case_ids, proposed_rule=self.proposed_rule,
            ontology_version=self.ontology_version, evaluation_precision=self.evaluation_precision,
            evaluation_recall=self.evaluation_recall, approved_by=reviewer_id,
        )

    @property
    def is_approved(self) -> bool:
        return self.approved_by is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id, "pattern_key": self.pattern_key,
            "evidence_case_ids": list(self.evidence_case_ids), "proposed_rule": dict(self.proposed_rule),
            "ontology_version": self.ontology_version, "evaluation_precision": self.evaluation_precision,
            "evaluation_recall": self.evaluation_recall, "approved_by": self.approved_by,
        }
