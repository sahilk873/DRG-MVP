"""Additive sepsis vertical: ontology + grouping + rules + example case.

Proves the second specialty is fully self-contained and does not disturb the
wound-care artifacts. The grouper is specialty-agnostic: it selects the sepsis
definition for a sepsis case (severity driven by SOI/ROM escalation) and the
pressure-injury default for a wound-care case (byte-identical). The sepsis rule
package fires on documented severe sepsis and abstains otherwise.
"""
import json
import unittest
from dataclasses import replace
from pathlib import Path

from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import (
    DeterministicDemoGrouper,
    GroupingDefinition,
    default_demo_registry,
    load_grouping_definition,
)
from revenue_integrity.models import Claim, DocumentationStatus, EncounterCase
from revenue_integrity.ontology import OntologyDefinition, load_builtin_ontology
from revenue_integrity.rules import RulePackage

ROOT = Path(__file__).parents[1]
SEPSIS_ONTOLOGY = ROOT / "src/revenue_integrity/data/sepsis_ontology_v1.json"
SEPSIS_GROUPING = ROOT / "src/revenue_integrity/data/sepsis_grouping_v1.json"
SEPSIS_RULES = ROOT / "rules/sepsis_v1.json"
SEPSIS_CASE = ROOT / "examples/case_sepsis.json"
WOUND_CASE = ROOT / "examples/case_pressure_injury.json"

SEPSIS_ONTOLOGY_ID = "sepsis-encounter-ontology"
SEPSIS_ONTOLOGY_VERSION = "1.0.0-draft"


def _sepsis_case() -> EncounterCase:
    return EncounterCase.from_dict(json.loads(SEPSIS_CASE.read_text(encoding="utf-8")))


def _wound_case() -> EncounterCase:
    return EncounterCase.from_dict(json.loads(WOUND_CASE.read_text(encoding="utf-8")))


class SepsisOntologyTests(unittest.TestCase):
    def test_ontology_loads_and_digest_is_self_consistent(self):
        data = json.loads(SEPSIS_ONTOLOGY.read_text(encoding="utf-8"))
        definition = OntologyDefinition.from_dict(data)
        builtin = load_builtin_ontology(SEPSIS_ONTOLOGY_ID, SEPSIS_ONTOLOGY_VERSION)
        self.assertEqual(builtin.digest, definition.digest)
        self.assertEqual(len(definition.digest), 64)

    def test_soi_and_rom_are_first_class_concepts_with_value_sets(self):
        definition = load_builtin_ontology(SEPSIS_ONTOLOGY_ID, SEPSIS_ONTOLOGY_VERSION)
        self.assertIn("SeverityOfIllness", definition.classes)
        self.assertIn("RiskOfMortality", definition.classes)
        self.assertEqual(definition.classes["SeverityOfIllness"].value_set, "soi_subclass")
        self.assertEqual(definition.classes["RiskOfMortality"].value_set, "rom_subclass")
        self.assertEqual(
            definition.value_sets["soi_subclass"], ("minor", "moderate", "major", "extreme")
        )
        self.assertEqual(
            definition.value_sets["rom_subclass"], ("minor", "moderate", "major", "extreme")
        )

    def test_wound_care_ontology_is_untouched(self):
        # Additive registration must not shadow or alter the wound-care definition.
        wound = load_builtin_ontology("wound-care-encounter-ontology", "1.1.0-draft")
        self.assertNotEqual(wound.ontology_id, SEPSIS_ONTOLOGY_ID)


class SepsisGroupingTests(unittest.TestCase):
    def test_grouping_definition_loads_and_is_not_for_billing(self):
        definition = load_grouping_definition("data/sepsis_grouping_v1.json")
        self.assertEqual(definition.grouper_id, "deterministic-demo-grouper-sepsis")
        self.assertIn("not-for-billing", definition.version)
        self.assertIn(SEPSIS_ONTOLOGY_ID, definition.applies_to.ontology_ids)

    def test_soi_rom_escalation_drives_severity_and_pricing(self):
        grouper = DeterministicDemoGrouper(registry=default_demo_registry())
        case = _sepsis_case()

        def claim(codes):
            return Claim(diagnoses=tuple(codes), procedures=(), charges=())

        shock = grouper.group(case, claim(["A41.9", "R65.21"]))
        severe = grouper.group(case, claim(["A41.9", "R65.20"]))
        plain = grouper.group(case, claim(["A41.9"]))
        self.assertEqual((shock.drg, shock.estimated_payment_cents), ("DEMO-871", 1_980_000))
        self.assertEqual((severe.drg, severe.estimated_payment_cents), ("DEMO-872", 1_350_000))
        self.assertEqual((plain.drg, plain.estimated_payment_cents), ("DEMO-873", 1_000_000))
        self.assertTrue(all("not-for-billing" in r.grouper_version for r in (shock, severe, plain)))

    def test_registry_selects_sepsis_for_sepsis_case(self):
        registry = default_demo_registry()
        self.assertEqual(registry.select(_sepsis_case()).grouper_id, "deterministic-demo-grouper-sepsis")

    def test_registry_selects_default_for_wound_case_byte_identical(self):
        registry = default_demo_registry()
        wound = _wound_case()
        self.assertEqual(registry.select(wound).grouper_id, "deterministic-demo-grouper")
        single = DeterministicDemoGrouper()
        multi = DeterministicDemoGrouper(registry=registry)
        expected = single.group(wound, wound.claim)
        actual = multi.group(wound, wound.claim)
        self.assertEqual(actual, expected)
        self.assertEqual(
            [s.to_dict() for s in actual.derivation],
            [s.to_dict() for s in expected.derivation],
        )
        self.assertEqual(multi.version, single.version)


class SepsisRulePackageTests(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads(SEPSIS_RULES.read_text(encoding="utf-8"))
        self.grouper = DeterministicDemoGrouper(registry=default_demo_registry())

    def test_rule_package_loads_and_binds_to_sepsis_ontology(self):
        package = RulePackage.from_dict(self.rules)
        self.assertEqual(package.status, "approved-for-demo")
        self.assertEqual(package.ontology.ontology_id, SEPSIS_ONTOLOGY_ID)
        ontology = load_builtin_ontology(package.ontology.ontology_id, package.ontology.version)
        self.assertEqual(ontology.digest, package.ontology.digest)

    def test_positive_case_fires_the_soi_rom_rule(self):
        findings = RuleEngine(self.rules, self.grouper).evaluate(_sepsis_case())
        fired = {f.rule_id for f in findings}
        self.assertIn("SEP-SOI-ROM-001", fired)
        finding = next(f for f in findings if f.rule_id == "SEP-SOI-ROM-001")
        self.assertEqual(finding.proposed_change, {"add_diagnoses": ["R65.20"]})
        self.assertTrue(finding.requires_human_review)
        # No spurious system finding: the submitted DRG reproduces cleanly.
        self.assertNotIn("SYSTEM-DRG-REPRODUCTION", fired)

    def test_negative_case_abstains_when_severity_is_low(self):
        # Downgrade the SOI/ROM below the rule threshold; the rule must NOT fire.
        case = _sepsis_case()
        low = replace(
            case.assertions[0],
            attributes={**case.assertions[0].attributes, "severity_of_illness": "minor", "risk_of_mortality": "minor"},
        )
        case = replace(case, assertions=(low,))
        findings = RuleEngine(self.rules, self.grouper).evaluate(case)
        self.assertNotIn("SEP-SOI-ROM-001", {f.rule_id for f in findings})

    def test_negative_case_abstains_when_diagnosis_already_present(self):
        # Same explicit severe-sepsis assertion, but R65.20 already coded -> no opportunity.
        case = _sepsis_case()
        case = replace(case, claim=replace(case.claim, diagnoses=("A41.9", "R65.20"), drg="DEMO-872"))
        findings = RuleEngine(self.rules, self.grouper).evaluate(case)
        self.assertNotIn("SEP-SOI-ROM-001", {f.rule_id for f in findings})

    def test_cdi_query_rule_fires_on_inferred_documentation(self):
        case = _sepsis_case()
        inferred = replace(case.assertions[0], documentation_status=DocumentationStatus("inferred"))
        case = replace(case, assertions=(inferred,))
        findings = RuleEngine(self.rules, self.grouper).evaluate(case)
        fired = {f.rule_id for f in findings}
        self.assertIn("SEP-QUERY-002", fired)
        # Inferred documentation does not support direct coding, so the coding rule abstains.
        self.assertNotIn("SEP-SOI-ROM-001", fired)


class SepsisMalformedArtifactTests(unittest.TestCase):
    def test_grouping_definition_rejects_billing_version(self):
        payload = json.loads(SEPSIS_GROUPING.read_text(encoding="utf-8"))
        payload["version"] = "prod-sepsis-1.0"
        with self.assertRaisesRegex(ValueError, "not-for-billing"):
            GroupingDefinition.from_dict(payload)

    def test_mutated_rule_ontology_digest_is_rejected_by_engine(self):
        payload = json.loads(SEPSIS_RULES.read_text(encoding="utf-8"))
        payload["ontology"]["digest"] = "0" * 64
        with self.assertRaises(ValueError):
            RuleEngine(payload, DeterministicDemoGrouper(registry=default_demo_registry()))

    def test_case_with_out_of_range_soi_value_fails_closed(self):
        payload = json.loads(SEPSIS_CASE.read_text(encoding="utf-8"))
        for entity in payload["ontology"]["entities"]:
            if entity["entity_id"] == "soi:major":
                entity["properties"]["value"] = "catastrophic"
        with self.assertRaises(ValueError):
            EncounterCase.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
