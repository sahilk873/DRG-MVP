import unittest
from pathlib import Path

from revenue_integrity.evaluation import EvaluationMetrics, build_evaluation_report
from revenue_integrity.promotion import PatternProposal
from revenue_integrity.promotion_backtest import (
    BacktestGateError,
    promote_with_backtest,
)
from revenue_integrity.runtime import KnowledgeStore, admit_rule_package
from revenue_integrity.runtime.knowledge import EXEMPLAR_KINDS

MANIFEST = Path(__file__).resolve().parents[1] / "examples" / "evaluation" / "gold_manifest.json"


def _report(*, precision=1.0, recall=1.0, tp=3, fp=0, fn=0, thresholds=None):
    return build_evaluation_report(
        EvaluationMetrics(true_positives=tp, false_positives=fp, false_negatives=fn),
        engine_version="test-engine",
        case_count=2,
        label_count=tp + fn,
        thresholds=thresholds,
    )


class PromotionBacktestTests(unittest.TestCase):
    def setUp(self):
        self.proposal = PatternProposal(
            "proposal-1", "wound-stage-omission", ("case-1", "case-2"),
            {"rule_id": "approved-rule", "when": {"concept": "stage-4"}}, "1.0.0", .98, .8,
        )

    def test_promotion_succeeds_with_real_signed_backtest(self):
        promoted = promote_with_backtest(self.proposal, "reviewer", MANIFEST)
        self.assertTrue(promoted.proposal.is_approved)
        self.assertEqual(promoted.proposal.approved_by, "reviewer")
        # report_hash + metrics recorded into provenance
        self.assertTrue(promoted.backtest.report_hash)
        self.assertEqual(promoted.backtest.eval_schema_version, "1.0.0")
        self.assertEqual(promoted.backtest.precision, 1.0)
        payload = promoted.to_dict()
        self.assertEqual(payload["backtest"]["report_hash"], promoted.backtest.report_hash)
        self.assertEqual(payload["proposal"]["approved_by"], "reviewer")

    def test_promotion_records_hash_from_injected_report(self):
        report = _report(thresholds={"min_precision": 0.9, "min_recall": 0.9})

        promoted = promote_with_backtest(
            self.proposal, "reviewer", "fake.json",
            evaluate=lambda path, allow_unapproved=False: report,
        )
        self.assertEqual(promoted.backtest.report_hash, report["report_hash"])
        self.assertEqual(promoted.backtest.engine_version, "test-engine")

    def test_promotion_refused_when_thresholds_not_met(self):
        # precision 0.6 < min_precision 0.95 -> passed False
        report = _report(tp=3, fp=2, fn=0, thresholds={"min_precision": 0.95})
        self.assertFalse(report["passed"])
        with self.assertRaises(BacktestGateError):
            promote_with_backtest(
                self.proposal, "reviewer", "fake.json",
                evaluate=lambda path, allow_unapproved=False: report,
            )

    def test_promotion_refused_when_manifest_has_no_thresholds(self):
        report = _report(thresholds=None)  # no 'passed' key
        with self.assertRaises(BacktestGateError):
            promote_with_backtest(
                self.proposal, "reviewer", "fake.json",
                evaluate=lambda path, allow_unapproved=False: report,
            )

    def test_promotion_refused_on_regression_against_baseline(self):
        baseline = _report(precision=1.0, recall=1.0, tp=3, fp=0, fn=0,
                           thresholds={"min_precision": 0.9})
        # current recall drops (fn=1) -> regression even though it still passes threshold
        current = _report(tp=2, fp=0, fn=1, thresholds={"min_precision": 0.9})
        self.assertTrue(current["passed"])
        with self.assertRaises(BacktestGateError):
            promote_with_backtest(
                self.proposal, "reviewer", "fake.json",
                baseline_report=baseline,
                evaluate=lambda path, allow_unapproved=False: current,
            )

    def test_promotion_succeeds_when_no_regression(self):
        baseline = _report(tp=2, fp=0, fn=1, thresholds={"min_precision": 0.9})
        current = _report(tp=3, fp=0, fn=0, thresholds={"min_precision": 0.9})
        promoted = promote_with_backtest(
            self.proposal, "reviewer", "fake.json",
            baseline_report=baseline,
            evaluate=lambda path, allow_unapproved=False: current,
        )
        self.assertTrue(promoted.proposal.is_approved)
        self.assertEqual(promoted.backtest.baseline_report_hash, baseline["report_hash"])

    def test_promotion_refused_on_unsigned_report(self):
        report = _report(thresholds={"min_precision": 0.9})
        del report["report_hash"]
        with self.assertRaises(BacktestGateError):
            promote_with_backtest(
                self.proposal, "reviewer", "fake.json",
                evaluate=lambda path, allow_unapproved=False: report,
            )

    def test_precision_floor_still_enforced_after_backtest_passes(self):
        # backtest passes, but proposal precision 0.98 < requested floor 0.99
        report = _report(thresholds={"min_precision": 0.9})
        with self.assertRaises(ValueError):
            promote_with_backtest(
                self.proposal, "reviewer", "fake.json",
                minimum_precision=0.99,
                evaluate=lambda path, allow_unapproved=False: report,
            )


class RulePackageExemplarTests(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore()
        self.proposal = PatternProposal(
            "proposal-rp", "wound-stage-omission", ("case-1", "case-2"),
            {"rule_id": "approved-rule", "when": {"concept": "stage-4"}}, "1.0.0", .98, .8,
        )

    def test_rule_package_is_a_known_exemplar_kind(self):
        self.assertIn("rule_package", EXEMPLAR_KINDS)

    def test_passing_rule_package_is_admitted_with_report_hash(self):
        exemplar, promoted = admit_rule_package(
            self.store, self.proposal, "reviewer", MANIFEST,
        )
        self.assertEqual(exemplar.kind, "rule_package")
        self.assertEqual(exemplar.label, "approved")
        self.assertTrue(exemplar.payload["report_hash"])
        self.assertEqual(exemplar.payload["report_hash"], promoted.backtest.report_hash)
        self.assertEqual(exemplar.provenance["report_hash"], promoted.backtest.report_hash)
        self.assertTrue(promoted.proposal.is_approved)
        # exemplar was actually recorded into the append-only store
        self.assertEqual(len(self.store.exemplars("rule_package")), 1)
        self.assertTrue(self.store.verify_chain())

    def test_regressing_rule_package_is_rejected_and_not_recorded(self):
        baseline = _report(precision=1.0, recall=1.0, tp=3, fp=0, fn=0,
                           thresholds={"min_precision": 0.9})
        current = _report(tp=2, fp=0, fn=1, thresholds={"min_precision": 0.9})
        with self.assertRaises(BacktestGateError):
            admit_rule_package(
                self.store, self.proposal, "reviewer", "fake.json",
                baseline_report=baseline,
                promote=lambda proposal, reviewer, manifest, **kw: promote_with_backtest(
                    proposal, reviewer, manifest,
                    evaluate=lambda path, allow_unapproved=False: current, **kw,
                ),
            )
        # nothing was written to the store on a failed gate
        self.assertEqual(len(self.store), 0)


if __name__ == "__main__":
    unittest.main()
