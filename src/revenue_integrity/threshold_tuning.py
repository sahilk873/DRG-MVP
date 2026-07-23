"""Governed, proposal-only tuning of automation confidence thresholds.

This module answers a narrow governance question: *given labeled outcome data, does the
evidence suggest an automation confidence cutoff in :mod:`~revenue_integrity.automation`
is set too loosely or too tightly?* It NEVER mutates the live thresholds, the
:class:`~revenue_integrity.automation.AutomationPolicy`, or ``AUTOMATION_SCHEMA_VERSION``.
It only emits governed :class:`ThresholdTuningProposal` objects a human must approve —
exactly mirroring the verify-then-promote philosophy elsewhere in the platform: the
system may SUGGEST a change, but a person governs it. Applying an approved proposal is
deliberately out of scope here.

All arithmetic is deterministic fixed-point over the same 10,000 basis the automation
priority math and the calibration harness use (four decimal places). No floats enter a
proposal payload, no dollar is touched, and identical inputs always yield an identical,
order-independent set of proposals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .automation import AutomationPolicy
from .evaluation import CALIBRATION_BASIS, LabeledConfidence

THRESHOLD_TUNING_SCHEMA_VERSION = "1.0.0"

#: The :class:`AutomationPolicy` confidence cutoffs this module may propose adjusting.
#: Each is a ratio in [0, 1]; only these two fields are in scope. Integer-cent and
#: count budgets (``auto_route_max_impact_cents`` etc.) are deliberately excluded —
#: outcome-labeled confidence data does not speak to them.
TUNABLE_THRESHOLDS: tuple[str, ...] = ("quick_confirm_confidence", "auto_route_confidence")

#: Target reliability for automated findings, in fixed-point (0.90 = 9_000). If the
#: findings a cutoff would automate confirm at least this often, the cutoff is "loose
#: enough is fine"; below it the cutoff is too loose and we propose RAISING it.
_DEFAULT_TARGET_RELIABILITY_FIXED = 9_000

#: A cutoff is "too tight" only when the band immediately below it is confirmed at least
#: this reliably (fixed-point) AND has enough samples — i.e. we are sending demonstrably
#: reliable findings to a person for no benefit. We then propose LOWERING the cutoff.
_DEFAULT_TIGHTEN_RELIABILITY_FIXED = 9_800

#: Minimum labeled samples in a band before its confirm rate is trusted for a proposal.
#: Below this the evidence is too thin and no proposal is produced (fail closed / no-op).
_DEFAULT_MIN_SAMPLES = 20

#: Granularity of candidate threshold values, in fixed-point (0.01 = 100). Proposed
#: values are always a multiple of this step so they stay human-legible.
_THRESHOLD_STEP_FIXED = 100


def _to_fixed(ratio: float) -> int:
    """Deterministic half-up round of a [0, 1] ratio to the fixed-point basis."""
    return int(ratio * CALIBRATION_BASIS + 0.5)


def _from_fixed(fixed: int) -> float:
    return fixed / CALIBRATION_BASIS


@dataclass(frozen=True, slots=True)
class ThresholdTuningProposal:
    """A governed, human-approval-required suggestion to move one automation cutoff.

    A proposal is inert: it records ``current_value`` (the live threshold, unchanged),
    a ``proposed_value``, a ``rationale`` string, the ``supporting_metric`` that drove
    it (name + observed/target values in fixed-point + sample size), a ``direction``
    (``raise``/``lower``/``noop``), and a ``required_status`` a reviewer must satisfy
    (``proposed`` for an actionable suggestion, ``draft`` for a no-op). Nothing here can
    change a claim, a DRG, a payment, or the live policy.
    """

    proposal_id: str
    threshold_name: str
    current_value: float
    proposed_value: float
    direction: str
    rationale: str
    supporting_metric: dict[str, Any]
    required_status: str = "proposed"

    def __post_init__(self) -> None:
        if self.threshold_name not in TUNABLE_THRESHOLDS:
            raise ValueError(f"threshold_name must be one of {TUNABLE_THRESHOLDS}")
        for name in ("current_value", "proposed_value"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a ratio between 0 and 1")
        if self.direction not in ("raise", "lower", "noop"):
            raise ValueError("direction must be 'raise', 'lower', or 'noop'")
        if self.required_status not in ("draft", "proposed"):
            raise ValueError("required_status must be 'draft' or 'proposed'")
        if self.direction == "noop" and self.required_status != "draft":
            raise ValueError("a no-op proposal must have required_status 'draft'")
        if self.direction != "noop" and self.required_status != "proposed":
            raise ValueError("an actionable proposal must have required_status 'proposed'")
        if not self.rationale.strip():
            raise ValueError("proposal requires a rationale")

    @property
    def is_actionable(self) -> bool:
        """True when the proposal actually asks a human to move a threshold."""
        return self.direction != "noop"

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold_tuning_schema_version": THRESHOLD_TUNING_SCHEMA_VERSION,
            "proposal_id": self.proposal_id,
            "threshold_name": self.threshold_name,
            "current_value": self.current_value,
            "proposed_value": self.proposed_value,
            "direction": self.direction,
            "rationale": self.rationale,
            "supporting_metric": dict(self.supporting_metric),
            "required_status": self.required_status,
        }


def _confirm_rate_fixed(labels: Sequence[LabeledConfidence], predicate) -> tuple[int, int | None]:
    """(sample_count, confirm_rate_fixed) over labels satisfying ``predicate``.

    Confirm rate is ``None`` when no label matches, so an empty band is never mistaken
    for a 0% confirm rate.
    """
    matched = [item for item in labels if predicate(_to_fixed(item.confidence))]
    if not matched:
        return 0, None
    confirmed = sum(1 for item in matched if item.confirmed)
    return len(matched), (confirmed * CALIBRATION_BASIS) // len(matched)


def _propose_for_threshold(
    threshold_name: str,
    labels: Sequence[LabeledConfidence],
    current_fixed: int,
    target_reliability_fixed: int,
    tighten_reliability_fixed: int,
    min_samples: int,
) -> ThresholdTuningProposal:
    """Deterministically decide raise / lower / no-op for a single cutoff.

    Loose case: findings the cutoff automates (confidence >= threshold) confirm below the
    target. Propose the lowest step-aligned value >= the current cutoff whose automated
    band meets the target (search upward). Tight case: the band just below the cutoff
    ``[current - step, current)`` confirms at least ``tighten_reliability`` reliably with
    enough samples, so lowering the cutoff by one step would safely automate more work.
    Otherwise: no-op (draft).
    """
    automated_count, automated_rate = _confirm_rate_fixed(
        labels, lambda fixed: fixed >= current_fixed
    )

    # --- Loose: automated population is unreliable -> raise the cutoff. ---
    if (
        automated_rate is not None
        and automated_count >= min_samples
        and automated_rate < target_reliability_fixed
    ):
        candidate = current_fixed + _THRESHOLD_STEP_FIXED
        while candidate <= CALIBRATION_BASIS:
            count, rate = _confirm_rate_fixed(labels, lambda fixed: fixed >= candidate)
            if count >= min_samples and rate is not None and rate >= target_reliability_fixed:
                return ThresholdTuningProposal(
                    proposal_id=_proposal_id(threshold_name, current_fixed, candidate),
                    threshold_name=threshold_name,
                    current_value=_from_fixed(current_fixed),
                    proposed_value=_from_fixed(candidate),
                    direction="raise",
                    rationale=(
                        f"Findings at or above the current {threshold_name} cutoff "
                        f"({_from_fixed(current_fixed):.2f}) were confirmed on review only "
                        f"{automated_rate / 100:.1f}% of the time (n={automated_count}), below the "
                        f"{target_reliability_fixed / 100:.1f}% target. Raising the cutoff to "
                        f"{_from_fixed(candidate):.2f} restores the target confirm rate. "
                        "Proposal only — a human must approve; live thresholds are unchanged."
                    ),
                    supporting_metric=_metric(
                        threshold_name, "automated_confirm_rate",
                        automated_rate, target_reliability_fixed, automated_count,
                    ),
                )
            candidate += _THRESHOLD_STEP_FIXED

    # --- Tight: the band just below the cutoff is demonstrably reliable -> lower. ---
    lower_fixed = current_fixed - _THRESHOLD_STEP_FIXED
    if lower_fixed >= 0:
        band_count, band_rate = _confirm_rate_fixed(
            labels, lambda fixed: lower_fixed <= fixed < current_fixed
        )
        if (
            band_rate is not None
            and band_count >= min_samples
            and band_rate >= tighten_reliability_fixed
        ):
            return ThresholdTuningProposal(
                proposal_id=_proposal_id(threshold_name, current_fixed, lower_fixed),
                threshold_name=threshold_name,
                current_value=_from_fixed(current_fixed),
                proposed_value=_from_fixed(lower_fixed),
                direction="lower",
                rationale=(
                    f"Findings in the band just below the current {threshold_name} cutoff "
                    f"[{_from_fixed(lower_fixed):.2f}, {_from_fixed(current_fixed):.2f}) were confirmed "
                    f"{band_rate / 100:.1f}% of the time (n={band_count}), at or above the "
                    f"{tighten_reliability_fixed / 100:.1f}% tighten threshold. Lowering the cutoff to "
                    f"{_from_fixed(lower_fixed):.2f} would safely automate more reliable findings. "
                    "Proposal only — a human must approve; live thresholds are unchanged."
                ),
                supporting_metric=_metric(
                    threshold_name, "below_band_confirm_rate",
                    band_rate, tighten_reliability_fixed, band_count,
                ),
            )

    # --- No actionable evidence: emit an inert no-op proposal. ---
    return ThresholdTuningProposal(
        proposal_id=_proposal_id(threshold_name, current_fixed, current_fixed),
        threshold_name=threshold_name,
        current_value=_from_fixed(current_fixed),
        proposed_value=_from_fixed(current_fixed),
        direction="noop",
        rationale=(
            f"Outcome data does not justify moving the {threshold_name} cutoff "
            f"({_from_fixed(current_fixed):.2f}); it is within tolerance or the evidence is too thin. "
            "No change proposed."
        ),
        supporting_metric=_metric(
            threshold_name, "automated_confirm_rate",
            automated_rate if automated_rate is not None else 0,
            target_reliability_fixed, automated_count,
        ),
        required_status="draft",
    )


def _metric(
    threshold_name: str, metric_name: str, observed_fixed: int, target_fixed: int, sample_size: int,
) -> dict[str, Any]:
    return {
        "threshold_name": threshold_name,
        "metric_name": metric_name,
        "observed_fixed": observed_fixed,
        "target_fixed": target_fixed,
        "basis": CALIBRATION_BASIS,
        "sample_size": sample_size,
    }


def _proposal_id(threshold_name: str, current_fixed: int, proposed_fixed: int) -> str:
    return f"threshold-{threshold_name}-{current_fixed:05d}-to-{proposed_fixed:05d}"


def propose_threshold_adjustments(
    labels: Iterable[LabeledConfidence],
    *,
    policy: AutomationPolicy | None = None,
    target_reliability: float = 0.90,
    tighten_reliability: float = 0.98,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
) -> list[ThresholdTuningProposal]:
    """Deterministically propose (never apply) automation-threshold adjustments.

    Given human-labeled confidence outcomes (:class:`LabeledConfidence`; a stated
    confidence + a realized confirmed/overturned outcome), evaluate each tunable
    :class:`AutomationPolicy` confidence cutoff and return one governed
    :class:`ThresholdTuningProposal` per cutoff, in stable ``TUNABLE_THRESHOLDS`` order.

    A cutoff whose automated population confirms below ``target_reliability`` yields a
    ``raise`` proposal; a cutoff with a demonstrably reliable band just beneath it yields
    a ``lower`` proposal; otherwise a ``noop`` proposal (``required_status='draft'``) is
    returned so the audit trail records that the data was examined. The live ``policy`` is
    read only — it is never mutated, and neither is ``AUTOMATION_SCHEMA_VERSION``.
    """
    active_policy = policy or AutomationPolicy()
    for name, value in (
        ("target_reliability", target_reliability), ("tighten_reliability", tighten_reliability),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be a ratio between 0 and 1")
    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples <= 0:
        raise ValueError("min_samples must be a positive integer")
    materialized = list(labels)
    target_fixed = _to_fixed(target_reliability)
    tighten_fixed = _to_fixed(tighten_reliability)
    return [
        _propose_for_threshold(
            name, materialized, _to_fixed(getattr(active_policy, name)),
            target_fixed, tighten_fixed, min_samples,
        )
        for name in TUNABLE_THRESHOLDS
    ]
