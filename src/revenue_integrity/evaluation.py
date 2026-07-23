"""Offline evaluation primitives for opportunity discovery quality.

These primitives measure how well deterministic discovery (the ``RuleEngine``) and the
agent hypothesis path recover a labeled gold set. Nothing here touches a claim, DRG, or
payment: it only scores predictions against human-provided labels, which is the honest
source for any precision/recall/F1 figure the product quotes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .audit import canonical_hash
from .models import Disposition, Finding
from .investigation import OpportunityCategory, OpportunityHypothesis

EVALUATION_SCHEMA_VERSION = "1.0.0"

#: Proposed-change collection -> the opportunity category it represents.
_CHANGE_KEY_TO_CATEGORY = {
    "add_diagnoses": OpportunityCategory.MISSED_DIAGNOSIS,
    "add_procedures": OpportunityCategory.MISSED_PROCEDURE,
    "add_charges": OpportunityCategory.MISSED_CHARGE,
}

#: Fallback mapping when a finding proposes no additive claim change.
_DISPOSITION_TO_CATEGORY = {
    Disposition.CODING_REVIEW: OpportunityCategory.CODING_SPECIFICITY,
    Disposition.CDI_QUERY: OpportunityCategory.DOCUMENTATION_GAP,
    Disposition.CHARGE_REVIEW: OpportunityCategory.MISSED_CHARGE,
    Disposition.COMPLIANCE_REVIEW: OpportunityCategory.UNSUPPORTED_BILLING,
    Disposition.INSUFFICIENT_EVIDENCE: OpportunityCategory.DOCUMENTATION_GAP,
    Disposition.NO_OPPORTUNITY: OpportunityCategory.DOCUMENTATION_GAP,
}


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


def finding_to_opportunity_key(
    finding: Finding, encounter_id: str
) -> tuple[str, OpportunityCategory, str]:
    """Map a deterministic engine ``Finding`` to a comparable opportunity key.

    Pure, table-driven lookup over the finding's proposed change and disposition — no
    floats, no model output. The key is the first added code (the reviewer-meaningful
    identity) or the rule ID when the finding proposes no additive change.
    """
    change = dict(finding.proposed_change)
    for change_key, category in _CHANGE_KEY_TO_CATEGORY.items():
        codes = change.get(change_key)
        if codes:
            return encounter_id, category, str(codes[0])
    return encounter_id, _DISPOSITION_TO_CATEGORY[finding.disposition], finding.rule_id


def predicted_keys_from_findings(
    encounter_id: str, findings: Iterable[Finding]
) -> set[tuple[str, OpportunityCategory, str]]:
    return {finding_to_opportunity_key(item, encounter_id) for item in findings}


def load_labeled_opportunities(items: Iterable[Mapping[str, Any]]) -> list[LabeledOpportunity]:
    """Normalize gold labels from either the canonical or the legacy fixture shape.

    Canonical: ``{encounter_id, category, key, valid}``.
    Legacy:    ``{case_id, category, key, label}`` where ``label`` starts with ``true``
    for a true positive. Both deserialize into identical ``LabeledOpportunity`` records.
    """
    result: list[LabeledOpportunity] = []
    for item in items:
        encounter_id = item.get("encounter_id") or item.get("case_id")
        if not isinstance(encounter_id, str) or not encounter_id.strip():
            raise ValueError("labeled opportunity requires a non-empty encounter_id or case_id")
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("labeled opportunity requires a non-empty key")
        category = OpportunityCategory(item["category"])
        if "valid" in item:
            valid = bool(item["valid"])
        elif "label" in item:
            valid = str(item["label"]).startswith("true")
        else:
            valid = True
        result.append(LabeledOpportunity(encounter_id, category, key, valid))
    return result


def evaluate_predictions(
    labels: Iterable[LabeledOpportunity],
    predicted: Iterable[tuple[str, OpportunityCategory, str]],
) -> EvaluationMetrics:
    """Score already-materialized (encounter_id, category, key) predictions against labels."""
    expected = {(item.encounter_id, item.category, item.key) for item in labels if item.valid}
    predicted_set = set(predicted)
    return EvaluationMetrics(
        true_positives=len(expected & predicted_set),
        false_positives=len(predicted_set - expected),
        false_negatives=len(expected - predicted_set),
    )


def build_evaluation_report(
    metrics: EvaluationMetrics,
    *,
    engine_version: str,
    case_count: int,
    label_count: int,
    thresholds: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, hash-signed accuracy report.

    The report is a measurement artifact, explicitly marked synthetic and not for billing.
    ``report_hash`` covers every field so a quoted precision figure is tamper-evident.
    """
    body: dict[str, Any] = {
        "eval_schema_version": EVALUATION_SCHEMA_VERSION,
        "basis": "synthetic-gold-set-not-for-billing",
        "engine_version": engine_version,
        "case_count": case_count,
        "label_count": label_count,
        "metrics": metrics.to_dict(),
    }
    if thresholds:
        parsed = {name: float(value) for name, value in sorted(thresholds.items())}
        body["thresholds"] = parsed
        body["passed"] = _meets_thresholds(metrics, parsed)
    body["report_hash"] = canonical_hash(body)
    return body


def _meets_thresholds(metrics: EvaluationMetrics, thresholds: Mapping[str, float]) -> bool:
    checks = {
        "min_precision": metrics.precision,
        "min_recall": metrics.recall,
        "min_f1": metrics.f1,
    }
    return all(
        actual >= thresholds[name]
        for name, actual in checks.items()
        if name in thresholds
    )


# --- Confidence calibration -------------------------------------------------
#
# A deterministic calibration report answers "when the system says it is 90%
# confident, is it actually confirmed ~90% of the time?" It reuses the same
# labeled-outcome discipline as the accuracy harness: every scored item carries a
# stated confidence and a realized outcome (confirmed vs overturned) supplied by a
# human, never by a model. All arithmetic is fixed-point over a 10,000 basis (four
# decimal places) — the same basis ``automation.py`` uses for ``confidence_weight``
# — so there are no floats in the report and money is never involved.

CALIBRATION_SCHEMA_VERSION = "1.0.0"

#: Fixed-point basis: a confidence of 1.0 is stored as 10_000 (four decimals).
CALIBRATION_BASIS = 10_000

#: Number of fixed-width confidence bins (deciles).
CALIBRATION_BIN_COUNT = 10


@dataclass(frozen=True, slots=True)
class LabeledConfidence:
    """A single scored item: a stated confidence and its realized outcome.

    ``confidence`` is a ratio in the closed interval [0, 1]. ``confirmed`` is the
    human-adjudicated outcome — ``True`` when the hypothesis/finding held up on
    review, ``False`` when it was overturned. No claim, DRG, or dollar is touched.
    """

    confidence: float
    confirmed: bool

    def __post_init__(self) -> None:
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("calibration confidence must be a number")
        if not 0 <= self.confidence <= 1:
            raise ValueError("calibration confidence must be between 0 and 1")


def _confidence_to_fixed(confidence: float) -> int:
    """Round a [0, 1] ratio to the fixed-point basis (banker-free, half-up)."""
    scaled = confidence * CALIBRATION_BASIS
    # Deterministic half-up rounding on a non-negative value; avoids float
    # round()'s banker's rounding so results are stable across platforms.
    return int(scaled + 0.5)


def _bin_index_for(fixed_confidence: int) -> int:
    """Map a fixed-point confidence to its decile bin index [0, BIN_COUNT-1]."""
    width = CALIBRATION_BASIS // CALIBRATION_BIN_COUNT
    index = fixed_confidence // width
    # A confidence of exactly 1.0 (10_000) lands in the top bin, not a phantom 11th.
    return min(index, CALIBRATION_BIN_COUNT - 1)


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """One decile of the calibration curve, entirely in fixed-point.

    ``bin_index`` 0 covers [0.0, 0.1), 9 covers [0.9, 1.0]. All *_fixed fields are
    integers over ``CALIBRATION_BASIS``. ``calibration_gap_fixed`` is
    ``predicted - observed`` (positive means overconfident). Empty bins report
    ``None`` for the derived rates so a caller never mistakes 0 for "0% confirm".
    """

    bin_index: int
    count: int
    confirmed_count: int
    lower_bound_fixed: int
    upper_bound_fixed: int
    mean_confidence_fixed: int | None
    observed_confirm_rate_fixed: int | None
    calibration_gap_fixed: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bin_index": self.bin_index,
            "count": self.count,
            "confirmed_count": self.confirmed_count,
            "lower_bound_fixed": self.lower_bound_fixed,
            "upper_bound_fixed": self.upper_bound_fixed,
            "mean_confidence_fixed": self.mean_confidence_fixed,
            "observed_confirm_rate_fixed": self.observed_confirm_rate_fixed,
            "calibration_gap_fixed": self.calibration_gap_fixed,
        }


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Deterministic, order-stable calibration curve over labeled confidences."""

    basis: int
    bin_count: int
    total: int
    confirmed_total: int
    bins: tuple[CalibrationBin, ...]
    #: Count-weighted mean absolute gap across non-empty bins, in fixed-point.
    expected_calibration_error_fixed: int
    #: Largest single-bin absolute gap across non-empty bins, in fixed-point.
    max_calibration_gap_fixed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "bin_count": self.bin_count,
            "total": self.total,
            "confirmed_total": self.confirmed_total,
            "expected_calibration_error_fixed": self.expected_calibration_error_fixed,
            "max_calibration_gap_fixed": self.max_calibration_gap_fixed,
            "bins": [item.to_dict() for item in self.bins],
        }


def compute_calibration(labels: Iterable[LabeledConfidence]) -> CalibrationReport:
    """Bucket labeled confidences into deciles and score calibration deterministically.

    Pure function: identical inputs (in any order) yield an identical report because
    each item is assigned to a bin by its confidence alone and bins are emitted in
    ascending index order. All rates and gaps are integer fixed-point over
    ``CALIBRATION_BASIS``; the empty set yields all-empty bins and zero aggregate
    error rather than a divide-by-zero.
    """
    width = CALIBRATION_BASIS // CALIBRATION_BIN_COUNT
    counts = [0] * CALIBRATION_BIN_COUNT
    confirmed = [0] * CALIBRATION_BIN_COUNT
    confidence_sum_fixed = [0] * CALIBRATION_BIN_COUNT

    for item in labels:
        fixed = _confidence_to_fixed(item.confidence)
        index = _bin_index_for(fixed)
        counts[index] += 1
        confidence_sum_fixed[index] += fixed
        if item.confirmed:
            confirmed[index] += 1

    bins: list[CalibrationBin] = []
    weighted_gap_sum = 0
    max_gap = 0
    for index in range(CALIBRATION_BIN_COUNT):
        count = counts[index]
        lower = index * width
        upper = CALIBRATION_BASIS if index == CALIBRATION_BIN_COUNT - 1 else (index + 1) * width
        if count:
            mean_conf = confidence_sum_fixed[index] // count
            observed = (confirmed[index] * CALIBRATION_BASIS) // count
            gap = mean_conf - observed
            abs_gap = abs(gap)
            weighted_gap_sum += abs_gap * count
            max_gap = max(max_gap, abs_gap)
        else:
            mean_conf = None
            observed = None
            gap = None
        bins.append(
            CalibrationBin(
                bin_index=index,
                count=count,
                confirmed_count=confirmed[index],
                lower_bound_fixed=lower,
                upper_bound_fixed=upper,
                mean_confidence_fixed=mean_conf,
                observed_confirm_rate_fixed=observed,
                calibration_gap_fixed=gap,
            )
        )

    total = sum(counts)
    ece = weighted_gap_sum // total if total else 0
    return CalibrationReport(
        basis=CALIBRATION_BASIS,
        bin_count=CALIBRATION_BIN_COUNT,
        total=total,
        confirmed_total=sum(confirmed),
        bins=tuple(bins),
        expected_calibration_error_fixed=ece,
        max_calibration_gap_fixed=max_gap,
    )


def load_labeled_confidences(items: Iterable[Mapping[str, Any]]) -> list[LabeledConfidence]:
    """Normalize calibration labels from a mapping shape.

    Each record needs a numeric ``confidence`` in [0, 1] and an ``outcome`` /
    ``confirmed`` flag. ``outcome`` accepts the reviewer-facing strings
    ``"confirmed"`` (true positive held up) and ``"overturned"`` (rejected on
    review); ``confirmed`` accepts a bare boolean. Malformed rows fail closed.
    """
    result: list[LabeledConfidence] = []
    for item in items:
        if "confidence" not in item:
            raise ValueError("calibration label requires a confidence")
        raw = item["confidence"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("calibration confidence must be a number")
        if "confirmed" in item:
            confirmed = bool(item["confirmed"])
        elif "outcome" in item:
            outcome = str(item["outcome"]).strip().lower()
            if outcome not in {"confirmed", "overturned"}:
                raise ValueError("calibration outcome must be 'confirmed' or 'overturned'")
            confirmed = outcome == "confirmed"
        else:
            raise ValueError("calibration label requires an outcome or confirmed flag")
        result.append(LabeledConfidence(float(raw), confirmed))
    return result


def build_calibration_report(
    report: CalibrationReport,
    *,
    engine_version: str,
) -> dict[str, Any]:
    """Wrap a ``CalibrationReport`` in a deterministic, hash-signed artifact.

    Like ``build_evaluation_report``, the result is explicitly synthetic and
    tamper-evident: ``report_hash`` covers every field. No dollars are present; all
    calibration figures are fixed-point over ``CALIBRATION_BASIS``.
    """
    body: dict[str, Any] = {
        "calibration_schema_version": CALIBRATION_SCHEMA_VERSION,
        "basis": "synthetic-gold-set-not-for-billing",
        "engine_version": engine_version,
        "calibration": report.to_dict(),
    }
    body["report_hash"] = canonical_hash(body)
    return body
