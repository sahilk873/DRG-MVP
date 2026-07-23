import copy
import json
import unittest
from pathlib import Path

from revenue_integrity.ontology import load_builtin_ontology
from revenue_integrity.ontology_extension import (
    OntologyDelta,
    apply_delta,
    propose_and_verify,
    verify_promotion_preflight,
)

ROOT = Path(__file__).parents[1]


def base_definition() -> dict:
    return json.loads((ROOT / "src/revenue_integrity/data/wound_care_ontology_v1.json").read_text())


class OntologyExtensionTests(unittest.TestCase):
    def test_additive_delta_passes_and_yields_a_new_digest(self):
        base = base_definition()
        delta = OntologyDelta(
            new_version="1.2.0-draft",
            classes=(
                {"class_id": "NutritionRisk", "label": "Nutrition risk", "parent": "RiskFactor"},
                {"class_id": "BradenScore", "label": "Braden score", "parent": "ClinicalEntity", "value_set": "braden_band"},
            ),
            relations=(
                {"relation_id": "hasNutritionRisk", "domain": ["Patient"], "range": ["NutritionRisk"], "requires_evidence": True},
            ),
            value_sets={"braden_band": ["low", "moderate", "high", "severe"]},
        )
        proposed, result = propose_and_verify(base, delta)
        self.assertTrue(result.ok, result.reasons)
        self.assertEqual(result.new_version, "1.2.0-draft")
        self.assertEqual(len(result.new_digest), 64)
        # Digest genuinely changed vs the base (recomputed from real content).
        base_digest = load_builtin_ontology(base["ontology_id"], base["version"]).digest
        self.assertNotEqual(result.new_digest, base_digest)
        # The new class is present in the merged definition.
        self.assertIn("NutritionRisk", {c["class_id"] for c in proposed["classes"]})

    def test_modifying_an_existing_class_is_rejected(self):
        base = base_definition()
        proposed = copy.deepcopy(base)
        proposed["version"] = "1.2.0-draft"
        pressure = next(c for c in proposed["classes"] if c["class_id"] == "PressureInjury")
        pressure["label"] = "Redefined pressure injury"
        result = verify_promotion_preflight(base, proposed)
        self.assertFalse(result.ok)
        self.assertTrue(any("PressureInjury was modified" in r for r in result.reasons))

    def test_removing_an_existing_relation_is_rejected(self):
        base = base_definition()
        proposed = copy.deepcopy(base)
        proposed["version"] = "1.2.0-draft"
        proposed["relations"] = [r for r in proposed["relations"] if r["relation_id"] != "hasStage"]
        # add something so it's not "adds nothing" that trips first
        proposed["classes"].append({"class_id": "Extra", "label": "Extra", "parent": "ClinicalEntity"})
        result = verify_promotion_preflight(base, proposed)
        self.assertFalse(result.ok)
        self.assertTrue(any("hasStage was removed" in r for r in result.reasons))

    def test_missing_version_bump_is_rejected(self):
        base = base_definition()
        delta = OntologyDelta(new_version=base["version"], classes=({"class_id": "X", "label": "X", "parent": "Entity"},))
        _, result = propose_and_verify(base, delta)
        self.assertFalse(result.ok)
        self.assertTrue(any("bump the version" in r for r in result.reasons))

    def test_internally_invalid_delta_is_rejected(self):
        base = base_definition()
        # New class references a parent that does not exist -> invalid ontology.
        delta = OntologyDelta(
            new_version="1.2.0-draft",
            classes=({"class_id": "Orphan", "label": "Orphan", "parent": "DoesNotExist"},),
        )
        _, result = propose_and_verify(base, delta)
        self.assertFalse(result.ok)
        self.assertTrue(any("not internally valid" in r for r in result.reasons))

    def test_empty_delta_adds_nothing_is_rejected(self):
        base = base_definition()
        proposed = copy.deepcopy(base)
        proposed["version"] = "1.2.0-draft"
        result = verify_promotion_preflight(base, proposed)
        self.assertFalse(result.ok)
        self.assertTrue(any("adds nothing new" in r for r in result.reasons))

    def test_apply_delta_preserves_base_content(self):
        base = base_definition()
        delta = OntologyDelta(new_version="1.2.0-draft", classes=({"class_id": "X", "label": "X", "parent": "Entity"},))
        proposed = apply_delta(base, delta)
        base_ids = {c["class_id"] for c in base["classes"]}
        self.assertTrue(base_ids.issubset({c["class_id"] for c in proposed["classes"]}))
        self.assertEqual(proposed["version"], "1.2.0-draft")


if __name__ == "__main__":
    unittest.main()
