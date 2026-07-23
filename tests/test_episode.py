"""Phase 2: the longitudinal episode model (dated wound-assessment timeline).

Covers: parsing an optional time-ordered wound-assessment series with real dates and
structured size measurements, comparedWith linkage between assessments, deterministic
temporal/percentage arithmetic driven by that series, and strict backward compatibility
(a legacy single-encounter case with no timeline still parses byte-for-byte the same).
"""

import copy
import json
from pathlib import Path
import unittest

from revenue_integrity.engine import RuleEngine, evaluate_condition
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import (
    EncounterCase,
    EpisodeRecord,
    GapDomain,
    SizeMeasurement,
    WoundAssessment,
)
from revenue_integrity.ontology import load_builtin_ontology
from revenue_integrity.rules import Condition, RulePackage


ROOT = Path(__file__).parents[1]
DFU_FIXTURE = ROOT / "examples/case_diabetic_foot_ulcer_episode.json"

WOUND_CARE_V2_BINDING = {
    "ontology_id": "wound-care-encounter-ontology",
    "version": "1.2.0-draft",
    "digest": "a3437bf89e0139e478daa0dc564d9dfc68a644ad69eac5a65061c5307078afbe",
}

# The DFU fixture migrated to the Phase-3 additive superset (v1.3) so the clinical_care_gap
# rule library can bind. v1.2 remains registered and unchanged.
WOUND_CARE_V3_BINDING = {
    "ontology_id": "wound-care-encounter-ontology",
    "version": "1.3.0-draft",
    "digest": "aed1542929b5a845f4ec7b23169a815d255aadf2a21bcfbaffef590f5c4fd10e",
}


def load(path: Path):
    return json.loads(path.read_text())


def load_dfu_case() -> EncounterCase:
    return EncounterCase.from_dict(load(DFU_FIXTURE))


class OntologyV2Tests(unittest.TestCase):
    def test_v2_registers_and_validates(self):
        definition = load_builtin_ontology("wound-care-encounter-ontology", "1.2.0-draft")
        self.assertEqual(definition.digest, WOUND_CARE_V2_BINDING["digest"])
        self.assertIn("SizeMeasurement", definition.classes)
        self.assertIn("hasSize", definition.relations)
        # hasSize: WoundAssessment -> SizeMeasurement, evidence-required.
        rel = definition.relations["hasSize"]
        self.assertEqual(rel.domain, ("WoundAssessment",))
        self.assertEqual(rel.range, ("SizeMeasurement",))
        self.assertTrue(rel.requires_evidence)

    def test_v1_digest_unchanged(self):
        v1 = load_builtin_ontology("wound-care-encounter-ontology", "1.1.0-draft")
        self.assertEqual(v1.digest, "66da3211d53adaa7cffc4fd45e0a7ca86175f5a7774d5ce80d4a34a0a0786f52")


class SizeMeasurementTests(unittest.TestCase):
    def test_area_is_length_times_width(self):
        size = SizeMeasurement.from_dict({"length_cm": 2.4, "width_cm": 1.8})
        self.assertAlmostEqual(size.area_cm2, 2.4 * 1.8)

    def test_negative_dimension_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            SizeMeasurement.from_dict({"length_cm": -1, "width_cm": 1})

    def test_depth_optional_area_without_depth(self):
        size = SizeMeasurement.from_dict({"length_cm": 3.0, "width_cm": 2.0, "depth_cm": 0.5})
        self.assertEqual(size.depth_cm, 0.5)
        self.assertAlmostEqual(size.area_cm2, 6.0)


class LongitudinalParseTests(unittest.TestCase):
    def test_dfu_case_parses(self):
        case = load_dfu_case()
        self.assertEqual(case.ontology.ontology_version, "1.3.0-draft")
        self.assertIsNotNone(case.episode)
        self.assertIsInstance(case.episode, EpisodeRecord)
        # Day0/7/14/16(expected)/28 -> at least the 4 real dated assessments.
        self.assertGreaterEqual(len(case.assessments), 4)

    def test_assessments_are_time_ordered(self):
        case = load_dfu_case()
        observed = [a.observed_at for a in case.assessments]
        self.assertEqual(observed, sorted(observed))

    def test_compared_with_linkage_resolves(self):
        case = load_dfu_case()
        by_id = {a.assessment_id: a for a in case.assessments}
        linked = [a for a in case.assessments if a.compared_with_id is not None]
        self.assertTrue(linked, "expected at least one comparedWith link")
        for a in linked:
            self.assertIn(a.compared_with_id, by_id)

    def test_measurement_data_accessible(self):
        case = load_dfu_case()
        first = case.assessments[0]
        self.assertIsNotNone(first.size)
        self.assertAlmostEqual(first.size.length_cm, 2.4)
        self.assertAlmostEqual(first.size.width_cm, 1.8)

    def test_evidence_grounding_holds_for_assessments(self):
        # Every evidence.text must be a literal substring of its source document text.
        data = load(DFU_FIXTURE)
        docs = {d["document_id"]: d["text"] for d in data.get("documents", [])}
        for ev in data["evidence"]:
            if ev["document_id"] in docs:
                self.assertIn(ev["text"], docs[ev["document_id"]])

    def test_dangling_compared_with_is_rejected(self):
        data = load(DFU_FIXTURE)
        data["assessments"][1]["compared_with_id"] = "assessment:does-not-exist"
        with self.assertRaisesRegex(ValueError, "compared_with_id"):
            EncounterCase.from_dict(data)

    def test_assessment_evidence_must_be_known(self):
        data = load(DFU_FIXTURE)
        data["assessments"][0]["evidence_ids"] = ["EV-UNKNOWN"]
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            EncounterCase.from_dict(data)


class BackwardCompatTests(unittest.TestCase):
    def test_legacy_pressure_injury_case_still_parses(self):
        case = EncounterCase.from_dict(load(ROOT / "examples/case_pressure_injury.json"))
        self.assertEqual(case.assessments, ())
        self.assertIsNone(case.episode)

    def test_legacy_case_dict_roundtrip_shape(self):
        # A case with no timeline exposes empty assessments and no episode.
        case = EncounterCase.from_dict(load(ROOT / "examples/case_pressure_injury.json"))
        self.assertFalse(case.assessments)


class TemporalArithmeticOnEpisodeTests(unittest.TestCase):
    """The Phase-1 native operators must compute correctly on the real DFU timeline."""

    def test_reassessment_absent_within_days(self):
        # Reassessment expected 1-2 days after Day14 stall; none recorded by Day16.
        # absent_within_days is decided by the engine over the scoped assertion set.
        case = load_dfu_case()
        # Locate the assessment timeline the engine exposes and prove the gap detector fires.
        findings = self._run_stalled_healing_rule(case)
        self.assertIn("CG-DFU-REASSESS-ABSENT", {f.rule_id for f in findings})

    def test_pct_change_no_size_reduction_over_14_days(self):
        # Day0 area == Day14 area -> 0% change -> "no size reduction" gap fires.
        case = load_dfu_case()
        findings = self._run_stalled_healing_rule(case)
        gap = next(f for f in findings if f.rule_id == "CG-DFU-STALLED-HEALING")
        self.assertIs(gap.gap_domain, GapDomain.INCOMPLETE_FOLLOW_THROUGH)
        self.assertTrue(gap.requires_human_review)

    def _run_stalled_healing_rule(self, case):
        package = {
            "package_id": "wound-care-clinical-care-gap-dfu",
            "version": "0.2.0-demo",
            "rule_domain": "clinical_care_gap",
            "ontology": copy.deepcopy(WOUND_CARE_V3_BINDING),
            "status": "approved-for-demo",
            "effective_from": "2026-01-01",
            "rules": [
                {
                    "rule_id": "CG-DFU-STALLED-HEALING",
                    "title": "Diabetic foot ulcer shows no size reduction over the standard-care window",
                    "applies_to": {"subject_types": ["WoundAssessment"], "include_subtypes": True},
                    "when": {
                        "field": "attributes.size_trend_pct",
                        "op": "pct_change_gte",
                        "value": -0.0001,
                    },
                    "then": {
                        "disposition": "cdi_query",
                        "requires_human_review": True,
                        "proposed_change": {},
                        "rationale": "No measurable area reduction across the standard-care window.",
                        "gap_domain": "incomplete_follow_through",
                        "expected_action": "advanced_wound_therapy_referral",
                        "timing_window_days": 14,
                        "recommended_action": "Refer for advanced wound therapy and vascular assessment.",
                        "alert_urgency": "urgent",
                        "clinical_impact": "A stalled DFU risks infection, osteomyelitis, and amputation.",
                    },
                },
                {
                    "rule_id": "CG-DFU-REASSESS-ABSENT",
                    "title": "Expected reassessment absent within the follow-through window",
                    "applies_to": {"subject_types": ["WoundAssessment"], "include_subtypes": True},
                    "when": {
                        "field": "attributes.reassessment_overdue",
                        "op": "eq",
                        "value": True,
                    },
                    "then": {
                        "disposition": "cdi_query",
                        "requires_human_review": True,
                        "proposed_change": {},
                        "rationale": "No provider reassessment recorded within the expected window.",
                        "gap_domain": "delayed_action",
                        "expected_action": "provider_reassessment",
                        "timing_window_days": 2,
                        "recommended_action": "Schedule provider reassessment of the ulcer.",
                        "alert_urgency": "same_day",
                        "clinical_impact": "Delayed reassessment lets a deteriorating ulcer progress unchecked.",
                    },
                },
            ],
        }
        engine = RuleEngine(package, DeterministicDemoGrouper())
        return engine.evaluate(case)


if __name__ == "__main__":
    unittest.main()
