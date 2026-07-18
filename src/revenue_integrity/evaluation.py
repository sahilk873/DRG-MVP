"""Offline evaluation primitives for opportunity discovery quality."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .investigation import OpportunityCategory, OpportunityHypothesis


@dataclass(frozen=True, slots=True)
class LabeledOpportunity:
    encounter_id: str
    category: OpportunityCategory
    key: str
    valid: bool = True


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else 1.0

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    def to_dict(self) -> dict[str, float | int]:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


def evaluate_opportunities(
    labels: Iterable[LabeledOpportunity],
    predictions: Sequence[OpportunityHypothesis],
) -> EvaluationMetrics:
    expected = {(item.encounter_id, item.category, item.key) for item in labels if item.valid}
    predicted = {(item.encounter_id, item.category, _prediction_key(item)) for item in predictions}
    return EvaluationMetrics(
        true_positives=len(expected & predicted),
        false_positives=len(predicted - expected),
        false_negatives=len(expected - predicted),
    )


def _prediction_key(item: OpportunityHypothesis) -> str:
    return item.candidate_codes[0] if item.candidate_codes else item.hypothesis_id
