"""Deterministic reviewer-productivity rollup over completed review records.

Pure aggregation of a set of :class:`~revenue_integrity.workflow.ReviewDecision`
records (the terminal, hash-chained outcomes of the reviewer workflow) into a
stable, sorted report: tallies by disposition (the taken :class:`ReviewAction`)
and outcome (:class:`DecisionReasonCode`), per-reviewer totals, a
confirmed-vs-overturned split, and an optional integer-cent realized impact.

It never consumes model output, never mutates a claim, and computes nothing
authoritative — it only counts governed labels that already exist. Realized
impact is only rolled up where a caller supplies an integer-cent value per
finding (from a deterministic upstream source); decisions themselves carry no
dollars, so absent an entry the finding contributes zero and is reported as
lacking impact data. Output is a frozen dataclass with ``to_dict``.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .workflow import DecisionReasonCode, ReviewAction, ReviewDecision

#: Rollup schema version (independent of packet/automation/decision schemas).
REVIEWER_PRODUCTIVITY_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ReviewerTotals:
    """Per-reviewer tally, keyed by ``actor_id`` and sorted stably by caller."""

    actor_id: str
    total: int
    confirmed: int
    overturned: int
    realized_impact_cents: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "total": self.total,
            "confirmed": self.confirmed,
            "overturned": self.overturned,
            "realized_impact_cents": self.realized_impact_cents,
        }


@dataclass(frozen=True, slots=True)
class ReviewerProductivityRollup:
    """Deterministic rollup of completed review records.

    ``confirmed`` counts decisions whose reason code is ``evidence_confirmed``
    (a routing decision that upheld the finding); ``overturned`` counts
    ``dismiss_with_reason`` decisions (the finding was set aside). These two are
    mutually exclusive and, by the workflow's action/reason-code invariant,
    together cover every decision.

    ``realized_impact_cents`` sums the supplied per-finding integer-cent impact
    across every decision that has a value; ``findings_with_impact`` /
    ``findings_without_impact`` record how many decisions did and did not have an
    impact figure available, so the total is never silently under-reported.
    """

    schema_version: str
    total_decisions: int
    confirmed: int
    overturned: int
    realized_impact_cents: int
    findings_with_impact: int
    findings_without_impact: int
    by_disposition: dict[str, int]
    by_outcome: dict[str, int]
    per_reviewer: tuple[ReviewerTotals, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "total_decisions": self.total_decisions,
            "confirmed": self.confirmed,
            "overturned": self.overturned,
            "realized_impact_cents": self.realized_impact_cents,
            "findings_with_impact": self.findings_with_impact,
            "findings_without_impact": self.findings_without_impact,
            "by_disposition": dict(self.by_disposition),
            "by_outcome": dict(self.by_outcome),
            "per_reviewer": [reviewer.to_dict() for reviewer in self.per_reviewer],
        }


def _validate_impact(realized_impact_cents: Mapping[str, int] | None) -> Mapping[str, int]:
    if realized_impact_cents is None:
        return {}
    validated: dict[str, int] = {}
    for finding_id, cents in realized_impact_cents.items():
        if not isinstance(finding_id, str) or not finding_id.strip():
            raise ValueError("realized_impact_cents keys must be non-empty finding ids")
        # Integer-cent only; reject bool (a bool is an int subclass) and floats.
        if isinstance(cents, bool) or not isinstance(cents, int):
            raise ValueError("realized_impact_cents values must be integer cents")
        validated[finding_id] = cents
    return validated


def roll_up_reviewer_productivity(
    decisions: Sequence[ReviewDecision],
    *,
    realized_impact_cents: Mapping[str, int] | None = None,
) -> ReviewerProductivityRollup:
    """Aggregate completed review records into a deterministic rollup.

    ``decisions`` is any set of terminal :class:`ReviewDecision` records (for
    example the full history for a packet). ``realized_impact_cents`` optionally
    maps a ``finding_id`` to a deterministic integer-cent realized impact; a
    decision whose finding is absent from the mapping contributes zero and is
    counted under ``findings_without_impact``.

    The result is a pure function of the inputs: dispositions and outcomes cover
    every supported enum value (zero-filled), per-reviewer rows are sorted by
    ``actor_id``, and an empty input yields a well-defined zero rollup.
    """
    impact = _validate_impact(realized_impact_cents)

    disposition_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    reviewer_total: Counter[str] = Counter()
    reviewer_confirmed: Counter[str] = Counter()
    reviewer_overturned: Counter[str] = Counter()
    reviewer_impact: Counter[str] = Counter()

    total_confirmed = 0
    total_overturned = 0
    total_impact = 0
    with_impact = 0
    without_impact = 0

    for decision in decisions:
        disposition_counts[decision.action.value] += 1
        outcome_counts[decision.reason_code.value] += 1
        reviewer_total[decision.actor_id] += 1

        is_confirmed = decision.reason_code is DecisionReasonCode.EVIDENCE_CONFIRMED
        if is_confirmed:
            total_confirmed += 1
            reviewer_confirmed[decision.actor_id] += 1
        if decision.action is ReviewAction.DISMISS_WITH_REASON:
            total_overturned += 1
            reviewer_overturned[decision.actor_id] += 1

        if decision.finding_id in impact:
            cents = impact[decision.finding_id]
            total_impact += cents
            reviewer_impact[decision.actor_id] += cents
            with_impact += 1
        else:
            without_impact += 1

    # Zero-fill every supported enum value for a stable, fully-specified shape.
    by_disposition = {action.value: disposition_counts.get(action.value, 0) for action in ReviewAction}
    by_outcome = {reason.value: outcome_counts.get(reason.value, 0) for reason in DecisionReasonCode}

    per_reviewer = tuple(
        ReviewerTotals(
            actor_id=actor_id,
            total=reviewer_total[actor_id],
            confirmed=reviewer_confirmed[actor_id],
            overturned=reviewer_overturned[actor_id],
            realized_impact_cents=reviewer_impact[actor_id],
        )
        for actor_id in sorted(reviewer_total)
    )

    return ReviewerProductivityRollup(
        schema_version=REVIEWER_PRODUCTIVITY_SCHEMA_VERSION,
        total_decisions=len(decisions),
        confirmed=total_confirmed,
        overturned=total_overturned,
        realized_impact_cents=total_impact,
        findings_with_impact=with_impact,
        findings_without_impact=without_impact,
        by_disposition=by_disposition,
        by_outcome=by_outcome,
        per_reviewer=per_reviewer,
    )
