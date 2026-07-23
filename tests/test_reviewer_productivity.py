import unittest
from datetime import UTC, datetime

from revenue_integrity.audit import canonical_hash
from revenue_integrity.reviewer_productivity import (
    REVIEWER_PRODUCTIVITY_SCHEMA_VERSION,
    roll_up_reviewer_productivity,
)
from revenue_integrity.workflow import (
    DECISION_SCHEMA_VERSION,
    DecisionReasonCode,
    ReviewAction,
    ReviewDecision,
    ReviewerRole,
)


def _decision(
    *,
    actor_id: str,
    finding_id: str,
    action: ReviewAction,
    reason_code: DecisionReasonCode,
    previous_decision_hash: str | None = None,
) -> ReviewDecision:
    """Construct a valid, hash-consistent ReviewDecision for rollup tests."""
    body = {
        "decision_schema_version": DECISION_SCHEMA_VERSION,
        "tenant_id": "tenant-a",
        "workspace_id": "revenue",
        "packet_id": "packet-1",
        "finding_id": finding_id,
        "action": action.value,
        "reason_code": reason_code.value,
        "reason": "governed reason",
        "actor_id": actor_id,
        "actor_roles": [ReviewerRole.CODER.value],
        "decided_at": datetime(2026, 7, 17, 13, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        "packet_record_hash": "a" * 64,
        "packet_hash": "b" * 64,
        "automation_plan_hash": "c" * 64,
        "automation_policy_hash": "d" * 64,
        "idempotency_key": f"key-{finding_id}-{actor_id}",
        "previous_decision_hash": previous_decision_hash,
    }
    digest = canonical_hash(body)
    return ReviewDecision(
        decision_id=f"decision-{digest[:20]}",
        tenant_id="tenant-a",
        workspace_id="revenue",
        packet_id="packet-1",
        finding_id=finding_id,
        action=action,
        reason_code=reason_code,
        reason="governed reason",
        actor_id=actor_id,
        actor_roles=(ReviewerRole.CODER,),
        decided_at=body["decided_at"],
        packet_record_hash="a" * 64,
        packet_hash="b" * 64,
        automation_plan_hash="c" * 64,
        automation_policy_hash="d" * 64,
        idempotency_key=body["idempotency_key"],
        previous_decision_hash=previous_decision_hash,
        decision_hash=digest,
    )


class ReviewerProductivityRollupTests(unittest.TestCase):
    def test_empty_set_yields_well_defined_zero_rollup(self):
        rollup = roll_up_reviewer_productivity([])
        self.assertEqual(rollup.total_decisions, 0)
        self.assertEqual(rollup.confirmed, 0)
        self.assertEqual(rollup.overturned, 0)
        self.assertEqual(rollup.realized_impact_cents, 0)
        self.assertEqual(rollup.findings_with_impact, 0)
        self.assertEqual(rollup.findings_without_impact, 0)
        self.assertEqual(rollup.per_reviewer, ())
        # Every supported enum value is zero-filled for a fully-specified shape.
        self.assertEqual(set(rollup.by_disposition), {a.value for a in ReviewAction})
        self.assertEqual(set(rollup.by_outcome), {r.value for r in DecisionReasonCode})
        self.assertTrue(all(count == 0 for count in rollup.by_disposition.values()))
        self.assertTrue(all(count == 0 for count in rollup.by_outcome.values()))
        payload = rollup.to_dict()
        self.assertEqual(payload["schema_version"], REVIEWER_PRODUCTIVITY_SCHEMA_VERSION)

    def test_mixed_records_roll_up_to_expected_tallies(self):
        decisions = [
            _decision(
                actor_id="coder-1", finding_id="f1",
                action=ReviewAction.ROUTE_TO_CODING,
                reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            ),
            _decision(
                actor_id="coder-1", finding_id="f2",
                action=ReviewAction.DISMISS_WITH_REASON,
                reason_code=DecisionReasonCode.DUPLICATE,
            ),
            _decision(
                actor_id="coder-2", finding_id="f3",
                action=ReviewAction.ROUTE_TO_CDI,
                reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            ),
            _decision(
                actor_id="coder-2", finding_id="f4",
                action=ReviewAction.DISMISS_WITH_REASON,
                reason_code=DecisionReasonCode.ALREADY_CORRECTED,
            ),
        ]
        # Impact available for two of the four findings.
        rollup = roll_up_reviewer_productivity(
            decisions, realized_impact_cents={"f1": 12_000, "f4": -3_000}
        )

        self.assertEqual(rollup.total_decisions, 4)
        self.assertEqual(rollup.confirmed, 2)
        self.assertEqual(rollup.overturned, 2)
        self.assertEqual(rollup.realized_impact_cents, 9_000)
        self.assertEqual(rollup.findings_with_impact, 2)
        self.assertEqual(rollup.findings_without_impact, 2)

        self.assertEqual(rollup.by_disposition[ReviewAction.ROUTE_TO_CODING.value], 1)
        self.assertEqual(rollup.by_disposition[ReviewAction.ROUTE_TO_CDI.value], 1)
        self.assertEqual(rollup.by_disposition[ReviewAction.DISMISS_WITH_REASON.value], 2)
        self.assertEqual(rollup.by_disposition[ReviewAction.ROUTE_TO_COMPLIANCE.value], 0)

        self.assertEqual(rollup.by_outcome[DecisionReasonCode.EVIDENCE_CONFIRMED.value], 2)
        self.assertEqual(rollup.by_outcome[DecisionReasonCode.DUPLICATE.value], 1)
        self.assertEqual(rollup.by_outcome[DecisionReasonCode.ALREADY_CORRECTED.value], 1)

        # Per-reviewer rows are sorted by actor_id and carry correct splits.
        self.assertEqual([r.actor_id for r in rollup.per_reviewer], ["coder-1", "coder-2"])
        coder_1, coder_2 = rollup.per_reviewer
        self.assertEqual((coder_1.total, coder_1.confirmed, coder_1.overturned), (2, 1, 1))
        self.assertEqual(coder_1.realized_impact_cents, 12_000)
        self.assertEqual((coder_2.total, coder_2.confirmed, coder_2.overturned), (2, 1, 1))
        self.assertEqual(coder_2.realized_impact_cents, -3_000)

    def test_rollup_is_a_pure_function_and_order_independent(self):
        a = _decision(
            actor_id="coder-1", finding_id="f1",
            action=ReviewAction.ROUTE_TO_CODING,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
        )
        b = _decision(
            actor_id="coder-2", finding_id="f2",
            action=ReviewAction.DISMISS_WITH_REASON,
            reason_code=DecisionReasonCode.OTHER_GOVERNED,
        )
        self.assertEqual(
            roll_up_reviewer_productivity([a, b]).to_dict(),
            roll_up_reviewer_productivity([b, a]).to_dict(),
        )

    def test_rejects_non_integer_impact_values(self):
        decisions = [
            _decision(
                actor_id="coder-1", finding_id="f1",
                action=ReviewAction.ROUTE_TO_CODING,
                reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
            )
        ]
        with self.assertRaisesRegex(ValueError, "integer cents"):
            roll_up_reviewer_productivity(decisions, realized_impact_cents={"f1": 120.5})
        with self.assertRaisesRegex(ValueError, "integer cents"):
            roll_up_reviewer_productivity(decisions, realized_impact_cents={"f1": True})
        with self.assertRaisesRegex(ValueError, "finding ids"):
            roll_up_reviewer_productivity(decisions, realized_impact_cents={" ": 100})


if __name__ == "__main__":
    unittest.main()
