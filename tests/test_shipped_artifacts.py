"""Golden-artifact conformance: every shipped ontology / rule / adapter must load and
have a matching ontology digest and an executable status.

This is regression armor for the "version everything governed" rule. A mis-digested,
wrong-status, or dangling-binding package fails HERE (a red test) instead of only at demo
time. The globs auto-cover future packages with no edits.
"""
import json
import unittest
from pathlib import Path

from revenue_integrity.ingestion.models import AdapterDefinition
from revenue_integrity.ontology import OntologyDefinition, load_builtin_ontology
from revenue_integrity.rules import RulePackage

ROOT = Path(__file__).parents[1]
EXECUTABLE_STATUSES = {"approved", "approved-for-demo"}


class ShippedOntologyTests(unittest.TestCase):
    def test_every_shipped_ontology_loads_with_a_valid_digest(self):
        paths = sorted((ROOT / "src/revenue_integrity/data").glob("*_ontology_*.json"))
        self.assertTrue(paths, "expected at least one shipped ontology definition")
        for path in paths:
            with self.subTest(ontology=path.name):
                definition = OntologyDefinition.from_dict(json.loads(path.read_text(encoding="utf-8")))
                self.assertEqual(len(definition.digest), 64)
                # The registered builtin loader must return a byte-identical digest.
                builtin = load_builtin_ontology(definition.ontology_id, definition.version)
                self.assertEqual(builtin.digest, definition.digest)


class ShippedRulePackageTests(unittest.TestCase):
    def test_every_rule_package_is_executable_and_digest_compatible(self):
        paths = sorted((ROOT / "rules").glob("*.json"))
        self.assertTrue(paths, "expected at least one shipped rule package")
        for path in paths:
            with self.subTest(rules=path.name):
                package = RulePackage.from_dict(json.loads(path.read_text(encoding="utf-8")))
                self.assertIn(package.status, EXECUTABLE_STATUSES)
                ontology = load_builtin_ontology(package.ontology.ontology_id, package.ontology.version)
                self.assertEqual(
                    ontology.digest, package.ontology.digest,
                    "rule package ontology digest must match the loadable definition",
                )
                for rule in package.rules:
                    self.assertTrue(rule.applies_to.subject_types, f"{rule.rule_id} lacks an ontology subject scope")
                    unknown = set(rule.applies_to.subject_types) - set(ontology.classes)
                    self.assertFalse(unknown, f"{rule.rule_id} scopes unknown classes {sorted(unknown)}")


class ShippedAdapterTests(unittest.TestCase):
    def test_every_adapter_loads_and_resolves_its_ontology(self):
        paths = sorted((ROOT / "examples/adapters").glob("*.json"))
        self.assertTrue(paths, "expected at least one shipped adapter")
        for path in paths:
            with self.subTest(adapter=path.name):
                adapter = AdapterDefinition.from_dict(json.loads(path.read_text(encoding="utf-8")))
                ontology = load_builtin_ontology(adapter.ontology.ontology_id, adapter.ontology.version)
                self.assertEqual(ontology.digest, adapter.ontology.digest)


class SepsisVerticalArtifactTests(unittest.TestCase):
    """Explicit coverage that the additive sepsis vertical's artifacts are all shipped,
    executable, and mutually digest-compatible (belt-and-suspenders over the globs)."""

    def test_sepsis_ontology_grouping_rule_and_case_are_consistent(self):
        from revenue_integrity.grouper import load_grouping_definition
        from revenue_integrity.models import EncounterCase

        ontology = load_builtin_ontology("sepsis-encounter-ontology", "1.0.0-draft")
        self.assertIn("SeverityOfIllness", ontology.classes)
        self.assertIn("RiskOfMortality", ontology.classes)

        grouping = load_grouping_definition("data/sepsis_grouping_v1.json")
        self.assertIn("not-for-billing", grouping.version)
        self.assertIn("sepsis-encounter-ontology", grouping.applies_to.ontology_ids)

        package = RulePackage.from_dict(json.loads((ROOT / "rules/sepsis_v1.json").read_text(encoding="utf-8")))
        self.assertIn(package.status, EXECUTABLE_STATUSES)
        self.assertEqual(package.ontology.ontology_id, "sepsis-encounter-ontology")
        self.assertEqual(package.ontology.digest, ontology.digest)

        case = EncounterCase.from_dict(json.loads((ROOT / "examples/case_sepsis.json").read_text(encoding="utf-8")))
        self.assertEqual(case.ontology.ontology_id, "sepsis-encounter-ontology")
        self.assertEqual(case.ontology.ontology_digest, ontology.digest)


class GuardBitesTests(unittest.TestCase):
    """Negative self-checks proving the conformance guards actually reject bad artifacts."""

    def _wound_care_rules(self):
        return json.loads((ROOT / "rules/wound_care_v1.json").read_text(encoding="utf-8"))

    def test_draft_status_would_be_rejected(self):
        payload = self._wound_care_rules()
        payload["status"] = "clinical-review-required"
        package = RulePackage.from_dict(payload)
        self.assertNotIn(package.status, EXECUTABLE_STATUSES)

    def test_mutated_ontology_digest_would_be_rejected(self):
        payload = self._wound_care_rules()
        payload["ontology"]["digest"] = "0" * 64
        package = RulePackage.from_dict(payload)
        ontology = load_builtin_ontology(package.ontology.ontology_id, package.ontology.version)
        self.assertNotEqual(ontology.digest, package.ontology.digest)


if __name__ == "__main__":
    unittest.main()
