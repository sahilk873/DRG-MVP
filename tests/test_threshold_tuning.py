import unittest
from dataclasses import FrozenInstanceError

from revenue_integrity.automation import AutomationPolicy
from revenue_integrity.evaluation import CALIBRATION_BASIS, LabeledConfidence
from revenue_integrity.threshold_tuning import (
    THRESHOLD_TUNING_SCHEMA_VERSION,
    TUNABLE_THRESHOLDS,
    ThresholdTuningProposal,
    propose_threshold_adjustments,
)


def _labels(spec):
    """spec: iterable of (confidence, confirmed, count) -> flat LabeledConfidence list."""
    out = []
    for confidence, confirmed, count in spec:
        out.extend(LabeledConfidence(confidence, confirmed) for _ in range(count))
    return out


class ThresholdTuningTests(unittest.TestCase):
    def _by_name(self, proposals):
        return {item.threshold_name: item for item in proposals}

    def test_returns_one_proposal_per_tunable_threshold_in_stable_order(self):
        proposals = propose_threshold_adjustments([])
        self.assertEqual([p.threshold_name for p in proposals], list(TUNABLE_THRESHOLDS))

    def test_too_loose_threshold_yields_raise_proposal(self):
        # auto_route_confidence is 0.93. Findings at/above it confirm poorly (60%),
        # but at/above 0.97 they confirm perfectly -> propose raising to 0.97.
        labels = _labels([
            (0.93, False, 40),
            (0.94, False, 20),
            (0.97, True, 60),
            (0.99, True, 40),
        ])
        proposals = self._by_name(propose_threshold_adjustments(labels))
        auto = proposals["auto_route_confidence"]
        self.assertEqual(auto.direction, "raise")
        self.assertEqual(auto.required_status, "proposed")
        self.assertTrue(auto.is_actionable)
        self.assertAlmostEqual(auto.current_value, 0.93)
        self.assertGreater(auto.proposed_value, 0.93)
        # Lowest step-aligned cutoff whose automated band meets the 90% target is 0.95:
        # at 0.94 the band still includes the overturned 0.94 findings (~83% confirm),
        # but from 0.95 up only the fully-confirmed 0.97/0.99 findings remain.
        self.assertAlmostEqual(auto.proposed_value, 0.95)
        self.assertEqual(auto.supporting_metric["metric_name"], "automated_confirm_rate")
        self.assertEqual(auto.supporting_metric["basis"], CALIBRATION_BASIS)

    def test_too_tight_threshold_yields_lower_proposal(self):
        # quick_confirm_confidence is 0.95. The band just below [0.94, 0.95) confirms
        # 100% with plenty of samples -> propose lowering to 0.94.
        labels = _labels([
            (0.94, True, 50),
            (0.95, True, 40),
            (0.97, True, 30),
        ])
        proposals = self._by_name(propose_threshold_adjustments(labels))
        quick = proposals["quick_confirm_confidence"]
        self.assertEqual(quick.direction, "lower")
        self.assertEqual(quick.required_status, "proposed")
        self.assertAlmostEqual(quick.current_value, 0.95)
        self.assertAlmostEqual(quick.proposed_value, 0.94)
        self.assertEqual(quick.supporting_metric["metric_name"], "below_band_confirm_rate")

    def test_optimal_data_yields_noop_draft_proposal(self):
        # Automated band confirms exactly at target (90%), below band not extreme -> no-op.
        labels = _labels([
            (0.90, True, 30),
            (0.93, True, 18),
            (0.94, False, 2),
            (0.95, True, 27),
            (0.97, True, 23),
        ])
        proposals = propose_threshold_adjustments(labels)
        for proposal in proposals:
            self.assertEqual(proposal.direction, "noop")
            self.assertEqual(proposal.required_status, "draft")
            self.assertFalse(proposal.is_actionable)
            self.assertAlmostEqual(proposal.current_value, proposal.proposed_value)

    def test_thin_evidence_yields_noop(self):
        # Well below min_samples in every band -> fail closed to a no-op.
        labels = _labels([(0.93, False, 3), (0.95, False, 2)])
        proposals = propose_threshold_adjustments(labels)
        self.assertTrue(all(p.direction == "noop" for p in proposals))

    def test_empty_data_yields_noop(self):
        proposals = propose_threshold_adjustments([])
        self.assertTrue(all(p.direction == "noop" for p in proposals))

    def test_live_policy_thresholds_unchanged_after_proposing(self):
        policy = AutomationPolicy()
        before_quick = policy.quick_confirm_confidence
        before_auto = policy.auto_route_confidence
        before_digest = policy.digest
        labels = _labels([(0.93, False, 40), (0.99, True, 60)])
        propose_threshold_adjustments(labels, policy=policy)
        self.assertEqual(policy.quick_confirm_confidence, before_quick)
        self.assertEqual(policy.auto_route_confidence, before_auto)
        self.assertEqual(policy.digest, before_digest)
        # And the default-policy factory path is likewise untouched.
        self.assertEqual(AutomationPolicy().quick_confirm_confidence, before_quick)
        self.assertEqual(AutomationPolicy().auto_route_confidence, before_auto)

    def test_deterministic_and_order_independent(self):
        labels = _labels([(0.93, False, 20), (0.97, True, 60), (0.99, True, 40)])
        first = [p.to_dict() for p in propose_threshold_adjustments(labels)]
        second = [p.to_dict() for p in propose_threshold_adjustments(list(reversed(labels)))]
        self.assertEqual(first, second)

    def test_to_dict_carries_schema_version_and_is_json_safe(self):
        proposal = propose_threshold_adjustments(
            _labels([(0.93, False, 40), (0.99, True, 60)])
        )[1]
        payload = proposal.to_dict()
        self.assertEqual(
            payload["threshold_tuning_schema_version"], THRESHOLD_TUNING_SCHEMA_VERSION
        )
        self.assertIn("proposal_id", payload)
        self.assertIsInstance(payload["supporting_metric"], dict)

    def test_proposal_is_frozen(self):
        proposal = propose_threshold_adjustments([])[0]
        with self.assertRaises(FrozenInstanceError):
            proposal.current_value = 0.5  # type: ignore[misc]

    def test_invalid_construction_fails_closed(self):
        with self.assertRaises(ValueError):
            ThresholdTuningProposal(
                proposal_id="x", threshold_name="not_a_threshold", current_value=0.9,
                proposed_value=0.9, direction="noop", rationale="r",
                supporting_metric={}, required_status="draft",
            )
        with self.assertRaises(ValueError):
            ThresholdTuningProposal(
                proposal_id="x", threshold_name="auto_route_confidence", current_value=0.9,
                proposed_value=0.9, direction="noop", rationale="r",
                supporting_metric={}, required_status="proposed",
            )
        with self.assertRaises(ValueError):
            propose_threshold_adjustments([], target_reliability=1.5)
        with self.assertRaises(ValueError):
            propose_threshold_adjustments([], min_samples=0)


if __name__ == "__main__":
    unittest.main()
