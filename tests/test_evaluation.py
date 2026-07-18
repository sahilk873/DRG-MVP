import unittest

from revenue_integrity.evaluation import LabeledOpportunity, evaluate_opportunities
from revenue_integrity.investigation import ConfidenceDimensions, OpportunityCategory, OpportunityHypothesis


class EvaluationTests(unittest.TestCase):
    def test_metrics_measure_precision_and_recall_without_dollar_bias(self):
        labels = [LabeledOpportunity("enc-1", OpportunityCategory.MISSED_DIAGNOSIS, "L89.154")]
        predictions = [OpportunityHypothesis(
            "opp-1", OpportunityCategory.MISSED_DIAGNOSIS, "enc-1", "supported", ("ev-1",),
            candidate_codes=("L89.154",), confidence=ConfidenceDimensions(.9, .9, .9),
        ), OpportunityHypothesis(
            "opp-2", OpportunityCategory.MISSED_CHARGE, "enc-2", "false positive", ("ev-2",),
            confidence=ConfidenceDimensions(.9, .9, .9),
        )]
        metrics = evaluate_opportunities(labels, predictions)
        self.assertEqual(metrics.true_positives, 1)
        self.assertEqual(metrics.false_positives, 1)
        self.assertEqual(metrics.false_negatives, 0)
        self.assertAlmostEqual(metrics.precision, 0.5)
