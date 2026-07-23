import json
import unittest
from pathlib import Path

from revenue_integrity.engine import ENGINE_VERSION, RuleEngine
from revenue_integrity.eval_cli import main as eval_main
from revenue_integrity.evaluation import (
    CALIBRATION_BASIS,
    LabeledConfidence,
    LabeledOpportunity,
    build_calibration_report,
    build_evaluation_report,
    compute_calibration,
    evaluate_opportunities,
    evaluate_predictions,
    finding_to_opportunity_key,
    load_labeled_confidences,
    load_labeled_opportunities,
    predicted_keys_from_findings,
)
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.investigation import ConfidenceDimensions, OpportunityCategory, OpportunityHypothesis
from revenue_integrity.models import Disposition, EncounterCase, Finding, ImpactStatus

ROOT = Path(__file__).parents[1]


def _finding(disposition, change, rule_id="R1"):
    status = ImpactStatus.ESTIMATED
    return Finding(
        finding_id="f", rule_id=rule_id, rule_package_id="P", rule_package_version="1",
        title="t", disposition=disposition, confidence=0.9, proposed_change=change,
        subject_ids=(), assertion_ids=(), evidence_ids=(), contradicting_evidence_ids=(),
        rationale="r", requires_human_review=True, submitted_drg="A", current_drg="A",
        simulated_drg="B", estimated_impact_cents=100, impact_status=status, grouper_version="demo",
    )


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

    def test_finding_to_opportunity_key_maps_change_then_disposition(self):
        self.assertEqual(
            finding_to_opportunity_key(_finding(Disposition.CODING_REVIEW, {"add_diagnoses": ["L89.154"]}), "enc-1"),
            ("enc-1", OpportunityCategory.MISSED_DIAGNOSIS, "L89.154"),
        )
        self.assertEqual(
            finding_to_opportunity_key(_finding(Disposition.CHARGE_REVIEW, {"add_charges": ["SUP-1"]}), "enc-1"),
            ("enc-1", OpportunityCategory.MISSED_CHARGE, "SUP-1"),
        )
        # No additive change -> fall back to disposition + rule id.
        self.assertEqual(
            finding_to_opportunity_key(_finding(Disposition.CDI_QUERY, {}, rule_id="CDI-9"), "enc-1"),
            ("enc-1", OpportunityCategory.DOCUMENTATION_GAP, "CDI-9"),
        )

    def test_loader_normalizes_legacy_and_canonical_shapes(self):
        legacy = json.loads((ROOT / "examples/evaluation/investigation_cases.json").read_text())
        labels = load_labeled_opportunities(legacy["cases"])
        self.assertEqual(len(labels), 5)
        by_id = {item.encounter_id: item for item in labels}
        self.assertTrue(by_id["eval-missed-diagnosis"].valid)
        self.assertFalse(by_id["eval-drug-no-opportunity"].valid)
        # A canonical record deserializes identically to its legacy twin.
        canonical = load_labeled_opportunities([
            {"encounter_id": "eval-missed-diagnosis", "category": "missed_diagnosis", "key": "L89.154", "valid": True},
        ])
        self.assertEqual(canonical[0], by_id["eval-missed-diagnosis"])

    def test_loader_rejects_malformed_labels(self):
        with self.assertRaises(ValueError):
            load_labeled_opportunities([{"category": "missed_diagnosis", "key": "x"}])  # no encounter/case id
        with self.assertRaises(ValueError):
            load_labeled_opportunities([{"encounter_id": "e", "category": "missed_diagnosis", "key": ""}])

    def test_evaluate_predictions_over_real_engine_findings(self):
        case = EncounterCase.from_dict(json.loads((ROOT / "examples/case_pressure_injury.json").read_text()))
        findings = RuleEngine(
            json.loads((ROOT / "rules/wound_care_v1.json").read_text()), DeterministicDemoGrouper()
        ).evaluate(case)
        predicted = predicted_keys_from_findings(case.encounter_id, findings)
        labels = load_labeled_opportunities([
            {"encounter_id": case.encounter_id, "category": "missed_diagnosis", "key": "L89.154", "valid": True},
        ])
        metrics = evaluate_predictions(labels, predicted)
        self.assertEqual((metrics.true_positives, metrics.false_positives, metrics.false_negatives), (1, 0, 0))
        self.assertEqual(metrics.precision, 1.0)
        self.assertEqual(metrics.recall, 1.0)

    def test_hac_sequencing_gold_case_emits_expected_system_findings(self):
        case = EncounterCase.from_dict(
            json.loads((ROOT / "examples/case_pressure_injury_hac_sequencing.json").read_text())
        )
        findings = RuleEngine(
            json.loads((ROOT / "rules/wound_care_v2.json").read_text()), DeterministicDemoGrouper()
        ).evaluate(case)
        rule_ids = {finding.rule_id for finding in findings}
        # The submitted MCC DRG (DEMO-290) does not survive the HAC POA exclusion of the
        # not-present-on-admission pressure injury, so both the reproduction and sequencing
        # system checks flag it, and the compliance rule flags the hospital-acquired injury.
        self.assertIn("SYSTEM-DRG-SEQUENCING", rule_ids)
        self.assertIn("SYSTEM-DRG-REPRODUCTION", rule_ids)
        self.assertIn("WC-PI-POA-002", rule_ids)
        sequencing = next(f for f in findings if f.rule_id == "SYSTEM-DRG-SEQUENCING")
        self.assertEqual(sequencing.submitted_drg, "DEMO-290")
        self.assertEqual(sequencing.simulated_drg, "DEMO-292")
        predicted = predicted_keys_from_findings(case.encounter_id, findings)
        labels = load_labeled_opportunities([
            {"encounter_id": case.encounter_id, "category": "coding_specificity", "key": "SYSTEM-DRG-REPRODUCTION", "valid": True},
            {"encounter_id": case.encounter_id, "category": "coding_specificity", "key": "SYSTEM-DRG-SEQUENCING", "valid": True},
            {"encounter_id": case.encounter_id, "category": "unsupported_billing", "key": "WC-PI-POA-002", "valid": True},
        ])
        metrics = evaluate_predictions(labels, predicted)
        self.assertEqual(
            (metrics.true_positives, metrics.false_positives, metrics.false_negatives), (3, 0, 0)
        )

    def test_report_is_deterministic_signed_and_threshold_aware(self):
        labels = [LabeledOpportunity("e", OpportunityCategory.MISSED_DIAGNOSIS, "L89.154")]
        predicted = [("e", OpportunityCategory.MISSED_DIAGNOSIS, "L89.154")]
        metrics = evaluate_predictions(labels, predicted)
        report_a = build_evaluation_report(metrics, engine_version=ENGINE_VERSION, case_count=1, label_count=1,
                                           thresholds={"min_precision": 0.9})
        report_b = build_evaluation_report(metrics, engine_version=ENGINE_VERSION, case_count=1, label_count=1,
                                           thresholds={"min_precision": 0.9})
        self.assertEqual(report_a, report_b)  # byte-stable
        self.assertTrue(report_a["passed"])
        self.assertIn("not-for-billing", report_a["basis"])
        failing = build_evaluation_report(
            evaluate_predictions(labels, []), engine_version=ENGINE_VERSION, case_count=1, label_count=1,
            thresholds={"min_recall": 0.9},
        )
        self.assertFalse(failing["passed"])

    def test_empty_labels_and_predictions_do_not_divide_by_zero(self):
        metrics = evaluate_predictions([], [])
        self.assertEqual(metrics.precision, 1.0)
        self.assertEqual(metrics.recall, 1.0)
        self.assertEqual(metrics.f1, 1.0)


class CalibrationTests(unittest.TestCase):
    def test_well_calibrated_set_shows_near_zero_gap(self):
        # In the 0.9 bin, 9 of 10 confirm -> observed 0.9 matches predicted 0.9.
        labels = [LabeledConfidence(0.9, confirmed=(i < 9)) for i in range(10)]
        report = compute_calibration(labels)
        top = report.bins[9]
        self.assertEqual(top.count, 10)
        self.assertEqual(top.confirmed_count, 9)
        self.assertEqual(top.mean_confidence_fixed, 9_000)
        self.assertEqual(top.observed_confirm_rate_fixed, 9_000)
        self.assertEqual(top.calibration_gap_fixed, 0)
        self.assertEqual(report.expected_calibration_error_fixed, 0)
        self.assertEqual(report.max_calibration_gap_fixed, 0)

    def test_miscalibrated_high_confidence_low_confirm_shows_gap_in_right_bin(self):
        # 10 items stated at 0.9 confidence but only 1 confirms -> observed 0.1.
        labels = [LabeledConfidence(0.9, confirmed=(i == 0)) for i in range(10)]
        report = compute_calibration(labels)
        top = report.bins[9]
        self.assertEqual(top.mean_confidence_fixed, 9_000)
        self.assertEqual(top.observed_confirm_rate_fixed, 1_000)
        # Overconfident: predicted 0.9 - observed 0.1 = +0.8 in fixed-point.
        self.assertEqual(top.calibration_gap_fixed, 8_000)
        self.assertEqual(report.max_calibration_gap_fixed, 8_000)
        self.assertEqual(report.expected_calibration_error_fixed, 8_000)
        # Lower bins that received no items stay empty (None), not a false 0%.
        self.assertIsNone(report.bins[0].observed_confirm_rate_fixed)
        self.assertIsNone(report.bins[0].calibration_gap_fixed)
        self.assertEqual(report.bins[0].count, 0)

    def test_binning_places_confidences_in_expected_deciles(self):
        labels = [
            LabeledConfidence(0.0, confirmed=False),   # bin 0
            LabeledConfidence(0.05, confirmed=True),   # bin 0
            LabeledConfidence(0.5, confirmed=True),    # bin 5
            LabeledConfidence(1.0, confirmed=True),    # bin 9 (boundary lands in top)
        ]
        report = compute_calibration(labels)
        self.assertEqual(report.total, 4)
        self.assertEqual(report.bins[0].count, 2)
        self.assertEqual(report.bins[5].count, 1)
        self.assertEqual(report.bins[9].count, 1)
        self.assertEqual(report.bins[9].mean_confidence_fixed, CALIBRATION_BASIS)

    def test_empty_set_is_well_defined(self):
        report = compute_calibration([])
        self.assertEqual(report.total, 0)
        self.assertEqual(report.confirmed_total, 0)
        self.assertEqual(report.expected_calibration_error_fixed, 0)
        self.assertEqual(report.max_calibration_gap_fixed, 0)
        self.assertEqual(len(report.bins), 10)
        for b in report.bins:
            self.assertEqual(b.count, 0)
            self.assertIsNone(b.observed_confirm_rate_fixed)
            self.assertIsNone(b.calibration_gap_fixed)

    def test_report_is_order_stable(self):
        forward = [LabeledConfidence(c, confirmed=True) for c in (0.1, 0.5, 0.9)]
        reversed_ = list(reversed(forward))
        self.assertEqual(compute_calibration(forward), compute_calibration(reversed_))

    def test_loader_normalizes_outcome_and_confirmed_shapes(self):
        labels = load_labeled_confidences([
            {"confidence": 0.9, "outcome": "confirmed"},
            {"confidence": 0.2, "outcome": "overturned"},
            {"confidence": 0.5, "confirmed": True},
        ])
        self.assertEqual([l.confirmed for l in labels], [True, False, True])

    def test_loader_rejects_malformed_calibration_labels(self):
        with self.assertRaises(ValueError):
            load_labeled_confidences([{"outcome": "confirmed"}])  # no confidence
        with self.assertRaises(ValueError):
            load_labeled_confidences([{"confidence": 1.5, "outcome": "confirmed"}])  # out of range
        with self.assertRaises(ValueError):
            load_labeled_confidences([{"confidence": 0.5, "outcome": "maybe"}])  # bad outcome
        with self.assertRaises(ValueError):
            load_labeled_confidences([{"confidence": 0.5}])  # no outcome/confirmed

    def test_labeled_confidence_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            LabeledConfidence(1.1, confirmed=True)
        with self.assertRaises(ValueError):
            LabeledConfidence(True, confirmed=True)  # bool is not a valid confidence

    def test_build_calibration_report_is_deterministic_and_signed(self):
        labels = [LabeledConfidence(0.9, confirmed=(i < 9)) for i in range(10)]
        report = compute_calibration(labels)
        a = build_calibration_report(report, engine_version=ENGINE_VERSION)
        b = build_calibration_report(report, engine_version=ENGINE_VERSION)
        self.assertEqual(a, b)
        self.assertEqual(len(a["report_hash"]), 64)
        self.assertIn("not-for-billing", a["basis"])


class EvalCliTests(unittest.TestCase):
    def test_gold_manifest_passes_thresholds(self):
        code = eval_main([str(ROOT / "examples/evaluation/gold_manifest.json"), "--enforce"])
        self.assertEqual(code, 0)

    def test_writes_signed_report_to_output(self):
        out = ROOT / "output" / "eval-report-test.json"
        if out.exists():
            out.unlink()
        try:
            code = eval_main([str(ROOT / "examples/evaluation/gold_manifest.json"), "--output", str(out)])
            self.assertEqual(code, 0)
            report = json.loads(out.read_text())
            self.assertEqual(report["metrics"]["precision"], 1.0)
            self.assertEqual(len(report["report_hash"]), 64)
        finally:
            if out.exists():
                out.unlink()

    def test_missing_manifest_fails_closed(self):
        self.assertEqual(eval_main([str(ROOT / "examples/evaluation/does-not-exist.json")]), 2)


if __name__ == "__main__":
    unittest.main()
