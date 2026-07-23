import json
import unittest
from pathlib import Path

from dataclasses import replace

from revenue_integrity.grouper import (
    DeterministicDemoGrouper,
    GroupingDefinition,
    GroupingRegistry,
    GroupingResult,
    GroupingSelector,
    load_grouping_definition,
)
from revenue_integrity.models import Claim, DiagnosisDetail, EncounterCase

ROOT = Path(__file__).parents[1]


def _case():
    return EncounterCase.from_dict(json.loads((ROOT / "examples/case_pressure_injury.json").read_text()))


def _claim(diagnoses):
    return Claim(diagnoses=tuple(diagnoses), procedures=(), charges=())


class DataDrivenGrouperTests(unittest.TestCase):
    def setUp(self):
        self.case = _case()
        self.grouper = DeterministicDemoGrouper()

    def test_severity_tiers_drive_the_drg_and_payment(self):
        # MCC-severity code -> DEMO-290; other L89 -> CC DEMO-291; neither -> none DEMO-292.
        mcc = self.grouper.group(self.case, _claim(["L89.154"]))
        cc = self.grouper.group(self.case, _claim(["L89.100"]))
        none = self.grouper.group(self.case, _claim(["E11.9"]))
        self.assertEqual((mcc.drg, mcc.estimated_payment_cents), ("DEMO-290", 1_842_000))
        self.assertEqual((cc.drg, cc.estimated_payment_cents), ("DEMO-291", 1_280_000))
        self.assertEqual((none.drg, none.estimated_payment_cents), ("DEMO-292", 1_000_000))

    def test_most_severe_diagnosis_wins_regardless_of_order(self):
        forward = self.grouper.group(self.case, _claim(["L89.100", "L89.154"]))
        reverse = self.grouper.group(self.case, _claim(["L89.154", "L89.100"]))
        self.assertEqual(forward.drg, "DEMO-290")
        self.assertEqual(forward, reverse)

    def test_version_and_not_for_billing_marker_preserved(self):
        self.assertEqual(self.grouper.version, "demo-0.2-not-for-billing")
        self.assertIn("not-for-billing", self.grouper.version)

    def test_result_carries_an_ordered_derivation_trace(self):
        result = self.grouper.group(self.case, _claim(["L89.154"]))
        steps = [step.step for step in result.derivation]
        self.assertEqual(steps, ["severity_resolution", "tier_selection", "pricing"])
        self.assertEqual(result.derivation[0].value, "mcc")
        self.assertEqual(result.derivation[0].detail, "L89.154")
        self.assertEqual(result.derivation[1].value, "DEMO-290")

    def test_determinism(self):
        a = self.grouper.group(self.case, self.case.claim)
        b = self.grouper.group(self.case, self.case.claim)
        self.assertEqual(a, b)

    def test_grouping_result_still_rejects_bad_cents(self):
        with self.assertRaises(ValueError):
            GroupingResult("DEMO-290", -1, "demo-0.2-not-for-billing")
        with self.assertRaises(ValueError):
            GroupingResult("DEMO-290", True, "demo-0.2-not-for-billing")


class HacPoaAwareGrouperTests(unittest.TestCase):
    def setUp(self):
        self.case = _case()
        self.grouper = DeterministicDemoGrouper()

    def _detailed_claim(self, details):
        codes = [d["code"] for d in details]
        return Claim(
            diagnoses=tuple(codes),
            procedures=(),
            charges=(),
            diagnosis_details=tuple(
                DiagnosisDetail(code=d["code"], sequence=d["sequence"], poa=d["poa"]) for d in details
            ),
        )

    def test_legacy_no_details_is_byte_identical(self):
        # No diagnosis_details -> exactly the same result and derivation as the legacy path.
        legacy = self.grouper.group(self.case, _claim(["L89.154"]))
        self.assertEqual(legacy.drg, "DEMO-290")
        self.assertEqual(legacy.estimated_payment_cents, 1_842_000)
        steps = [s.step for s in legacy.derivation]
        self.assertEqual(steps, ["severity_resolution", "tier_selection", "pricing"])
        # No poa_exclusion step ever appears without diagnosis_details.
        self.assertNotIn("poa_exclusion", steps)

    def test_hac_code_not_present_on_admission_is_excluded_from_severity(self):
        # L89.154 is an MCC-severity HAC. With poa='N' it must NOT drive severity;
        # the remaining L89.100 only reaches CC, so the DRG/tier is lower.
        result = self._detailed_claim(
            [
                {"code": "L89.154", "sequence": 1, "poa": "N"},
                {"code": "L89.100", "sequence": 2, "poa": "Y"},
            ]
        )
        grouped = self.grouper.group(self.case, result)
        self.assertEqual(grouped.drg, "DEMO-291")
        self.assertEqual(grouped.estimated_payment_cents, 1_280_000)
        steps = [s.step for s in grouped.derivation]
        self.assertEqual(steps[0], "poa_exclusion")
        self.assertEqual(grouped.derivation[0].value, "L89.154")
        self.assertEqual([s.value for s in grouped.derivation if s.step == "severity_resolution"], ["cc"])

    def test_same_hac_code_present_on_admission_still_counts(self):
        # Same code, poa='Y' -> still drives MCC severity; no exclusion step emitted.
        grouped = self.grouper.group(
            self.case,
            self._detailed_claim(
                [
                    {"code": "L89.154", "sequence": 1, "poa": "Y"},
                    {"code": "L89.100", "sequence": 2, "poa": "Y"},
                ]
            ),
        )
        self.assertEqual(grouped.drg, "DEMO-290")
        self.assertEqual(grouped.estimated_payment_cents, 1_842_000)
        steps = [s.step for s in grouped.derivation]
        self.assertNotIn("poa_exclusion", steps)

    def test_non_hac_code_not_on_admission_is_not_excluded(self):
        # L89.100 is not a HAC code; poa='N' must NOT exclude it.
        grouped = self.grouper.group(
            self.case,
            self._detailed_claim([{"code": "L89.100", "sequence": 1, "poa": "N"}]),
        )
        self.assertEqual(grouped.drg, "DEMO-291")
        self.assertNotIn("poa_exclusion", [s.step for s in grouped.derivation])


class GroupingDefinitionValidationTests(unittest.TestCase):
    def _valid(self):
        return json.loads((ROOT / "src/revenue_integrity/data/demo_grouping_v1.json").read_text())

    def test_shipped_definition_loads(self):
        definition = load_grouping_definition()
        self.assertEqual(definition.grouper_id, "deterministic-demo-grouper")
        self.assertIn("not-for-billing", definition.version)

    def test_version_must_be_marked_not_for_billing(self):
        payload = self._valid()
        payload["version"] = "prod-1.0"
        with self.assertRaisesRegex(ValueError, "not-for-billing"):
            GroupingDefinition.from_dict(payload)

    def test_non_executable_status_fails_closed(self):
        payload = self._valid()
        payload["status"] = "draft"
        with self.assertRaisesRegex(ValueError, "not executable"):
            GroupingDefinition.from_dict(payload)

    def test_missing_none_tier_is_rejected(self):
        payload = self._valid()
        payload["tiers"] = [t for t in payload["tiers"] if t["severity"] != "none"]
        with self.assertRaisesRegex(ValueError, "none"):
            GroupingDefinition.from_dict(payload)

    def test_duplicate_drg_is_rejected(self):
        payload = self._valid()
        payload["tiers"][1]["drg"] = payload["tiers"][0]["drg"]
        with self.assertRaisesRegex(ValueError, "unique"):
            GroupingDefinition.from_dict(payload)

    def test_non_positive_weight_is_rejected(self):
        payload = self._valid()
        payload["tiers"][0]["relative_weight_micros"] = 0
        with self.assertRaisesRegex(ValueError, "relative_weight_micros"):
            GroupingDefinition.from_dict(payload)

    def test_unknown_severity_value_is_rejected(self):
        payload = self._valid()
        payload["diagnosis_severity"]["codes"]["X99"] = "critical"
        with self.assertRaisesRegex(ValueError, "unknown severity"):
            GroupingDefinition.from_dict(payload)

    def test_hac_codes_must_be_an_array(self):
        payload = self._valid()
        payload["hac_codes"] = "L89.154"
        with self.assertRaisesRegex(ValueError, "hac_codes must be an array"):
            GroupingDefinition.from_dict(payload)

    def test_hac_codes_reject_empty_strings(self):
        payload = self._valid()
        payload["hac_codes"] = ["  "]
        with self.assertRaisesRegex(ValueError, "hac_codes must be non-empty"):
            GroupingDefinition.from_dict(payload)

    def test_shipped_definition_carries_hac_codes(self):
        definition = load_grouping_definition()
        self.assertIn("L89.154", definition.hac_codes)


class SpecialtyAgnosticRegistryTests(unittest.TestCase):
    """The grouper is a registry of governed definitions selected deterministically per case."""

    def setUp(self):
        self.case = _case()  # ontology_id == "wound-care-encounter-ontology"
        self.default = load_grouping_definition()

    def _cardiology_definition(self):
        # A second governed, versioned demo definition for a different bound ontology.
        payload = json.loads((ROOT / "src/revenue_integrity/data/demo_grouping_v1.json").read_text())
        payload["grouper_id"] = "deterministic-demo-grouper-cardiology"
        payload["version"] = "demo-cardio-0.1-not-for-billing"
        payload["base_rate_cents"] = 2000000
        payload["tiers"] = [
            {"severity": "mcc", "drg": "DEMO-280", "title": "Cardiac with MCC", "relative_weight_micros": 3000000},
            {"severity": "cc", "drg": "DEMO-281", "title": "Cardiac with CC", "relative_weight_micros": 2000000},
            {"severity": "none", "drg": "DEMO-282", "title": "Cardiac without CC/MCC", "relative_weight_micros": 1500000},
        ]
        payload["diagnosis_severity"] = {"codes": {"I21.9": "mcc"}, "prefixes": {"I21": "cc"}}
        payload["hac_codes"] = []
        payload["applies_to"] = {"ontology_ids": ["cardiology-encounter-ontology"]}
        return GroupingDefinition.from_dict(payload)

    def test_default_path_is_byte_identical_for_the_demo_case(self):
        # Registering additional definitions must not change the demo case (no criterion matches it).
        single = DeterministicDemoGrouper()
        registry = GroupingRegistry(default=self.default, definitions=(self._cardiology_definition(),))
        multi = DeterministicDemoGrouper(registry=registry)
        expected = single.group(self.case, self.case.claim)
        actual = multi.group(self.case, self.case.claim)
        self.assertEqual(actual, expected)
        self.assertEqual(actual.drg, expected.drg)
        self.assertEqual(actual.estimated_payment_cents, expected.estimated_payment_cents)
        self.assertEqual(
            [s.to_dict() for s in actual.derivation],
            [s.to_dict() for s in expected.derivation],
        )
        self.assertEqual(multi.version, single.version)

    def test_selection_picks_the_matching_definition_when_multiple_registered(self):
        cardiology = self._cardiology_definition()
        registry = GroupingRegistry(default=self.default, definitions=(cardiology,))
        grouper = DeterministicDemoGrouper(registry=registry)
        # Rebind the demo case to the cardiology ontology so its selector matches.
        cardiac_case = replace(
            self.case, ontology=replace(self.case.ontology, ontology_id="cardiology-encounter-ontology")
        )
        result = grouper.group(cardiac_case, _claim(["I21.9"]))
        self.assertEqual(result.drg, "DEMO-280")
        self.assertEqual(result.grouper_version, "demo-cardio-0.1-not-for-billing")
        self.assertEqual(result.estimated_payment_cents, 2000000 * 3000000 // 1_000_000)
        # The wound-care demo case still routes to the default definition.
        self.assertEqual(grouper.group(self.case, _claim(["L89.154"])).drg, "DEMO-290")

    def test_first_matching_definition_wins_deterministically(self):
        first = replace(self._cardiology_definition(), grouper_id="first")
        second = replace(
            self._cardiology_definition(),
            grouper_id="second",
            applies_to=GroupingSelector(ontology_ids=frozenset({"cardiology-encounter-ontology"})),
        )
        registry = GroupingRegistry(default=self.default, definitions=(first, second))
        cardiac_case = replace(
            self.case, ontology=replace(self.case.ontology, ontology_id="cardiology-encounter-ontology")
        )
        self.assertIs(registry.select(cardiac_case), first)

    def test_no_match_falls_back_to_default(self):
        registry = GroupingRegistry(default=self.default, definitions=(self._cardiology_definition(),))
        self.assertIs(registry.select(self.case), self.default)

    def test_empty_selector_never_matches_on_its_own(self):
        self.assertFalse(GroupingSelector().matches(self.case))

    def test_service_line_metadata_selector_matches(self):
        selector = GroupingSelector(service_lines=frozenset({"cardiology"}))
        no_meta = self.case
        self.assertFalse(selector.matches(no_meta))
        with_meta = replace(no_meta, metadata={"service_line": "cardiology"})
        self.assertTrue(selector.matches(with_meta))

    def test_registry_rejects_duplicate_grouper_ids(self):
        with self.assertRaisesRegex(ValueError, "unique grouper_ids"):
            GroupingRegistry(default=self.default, definitions=(self.default,))

    def test_grouper_rejects_definition_and_registry_together(self):
        with self.assertRaisesRegex(ValueError, "not both"):
            DeterministicDemoGrouper(self.default, registry=GroupingRegistry.single())

    def test_applies_to_rejects_unknown_keys(self):
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            GroupingSelector.from_dict({"specialty": "cardiology"})

    def test_applies_to_rejects_non_string_ids(self):
        with self.assertRaisesRegex(ValueError, "non-empty strings"):
            GroupingSelector.from_dict({"ontology_ids": ["  "]})


if __name__ == "__main__":
    unittest.main()
