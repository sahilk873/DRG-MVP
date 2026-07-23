"""Finding #9: the rule_package JSON Schema must itself enforce the clinical/revenue
domain wall, not merely the Python parse-time wall in ``rules.py``.

The project ships zero runtime dependencies (``pyproject.toml`` -> ``dependencies = []``)
and CI runs ``python -m unittest`` with no ``jsonschema`` package available, so this test
carries a small, self-contained evaluator for exactly the JSON-Schema keywords the
domain wall uses (``allOf`` / ``if`` / ``then`` / ``const`` / ``properties`` /
``maxProperties`` / ``required`` / boolean-``false`` property schemas / ``type``).

The full-grammar JSON-Schema validation (``$ref``/``oneOf``/``pattern``/``enum`` etc.)
of these same fixtures runs against the real Ajv validator in
``agent/src/json-schema.test.ts``. Here we assert (1) the shipped packages satisfy the
wall subschemas, (2) synthetic cross-domain rules are rejected by the schema's wall, and
(3) the schema file structurally carries the wall so it cannot silently be dropped.
"""

import copy
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


# The clinical-care-gap action fields a revenue_integrity rule must never carry. Mirrors
# ``revenue_integrity.rules.CLINICAL_ACTION_FIELDS`` and the schema's revenue-domain branch.
GAP_ACTION_FIELDS = (
    "gap_domain",
    "alert_urgency",
    "recommended_action",
    "expected_action",
    "timing_window_days",
    "clinical_impact",
)


def _validate(schema, instance) -> bool:
    """Evaluate ``instance`` against the subset of JSON-Schema keywords used by the
    rule_package domain wall. Returns True on success, False on any violation.

    Deliberately narrow: it only understands the keywords the wall relies on. It fails
    closed (returns False) if asked to interpret a keyword it does not support so a
    future schema change cannot be silently under-checked here.
    """
    if schema is True:
        return True
    if schema is False:
        return False
    if not isinstance(schema, dict):
        raise AssertionError(f"unsupported schema node: {schema!r}")

    supported = {
        "allOf", "if", "then", "else", "const", "properties",
        "maxProperties", "required", "type", "items",
    }
    unknown = set(schema) - supported - {"$comment", "$schema", "$id", "title", "description"}
    if unknown:
        raise AssertionError(f"evaluator does not support keywords {sorted(unknown)}")

    if "type" in schema:
        expected = schema["type"]
        types = expected if isinstance(expected, list) else [expected]
        py = {
            "object": dict, "array": list, "string": str,
            "boolean": bool, "integer": int, "number": (int, float), "null": type(None),
        }
        if not any(
            (t == "integer" and isinstance(instance, int) and not isinstance(instance, bool))
            or (t != "integer" and isinstance(instance, py[t]))
            for t in types
        ):
            return False

    if "const" in schema and instance != schema["const"]:
        return False

    if "maxProperties" in schema:
        if not isinstance(instance, dict) or len(instance) > schema["maxProperties"]:
            return False

    if "required" in schema:
        if not isinstance(instance, dict):
            return False
        if not all(key in instance for key in schema["required"]):
            return False

    if "properties" in schema and isinstance(instance, dict):
        for key, subschema in schema["properties"].items():
            if key in instance and not _validate(subschema, instance[key]):
                return False

    if "items" in schema and isinstance(instance, list):
        if not all(_validate(schema["items"], element) for element in instance):
            return False

    for sub in schema.get("allOf", []):
        if not _validate(sub, instance):
            return False

    if "if" in schema:
        if _validate(schema["if"], instance):
            if "then" in schema and not _validate(schema["then"], instance):
                return False
        elif "else" in schema and not _validate(schema["else"], instance):
            return False

    return True


class RulePackageDomainWallSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = load("schemas/rule_package.schema.json")
        # Evaluate the instance against ONLY the top-level domain-wall (the ``allOf``),
        # which is the finding-#9 constraint under test. Full-grammar validation of the
        # rest of the schema ($ref/oneOf/pattern/enum) runs against Ajv in the agent.
        self.wall = {"allOf": self.schema["allOf"]}
        self.gap_pkg = load("rules/wound_care_gaps_v1.json")
        self.rev_pkg = load("rules/wound_care_v1.json")

    def test_schema_file_structurally_carries_the_domain_wall(self):
        # The wall must be present so schema-only tooling (finding #9) enforces it.
        wall = self.schema.get("allOf")
        self.assertIsInstance(wall, list)
        conditions = [
            branch["if"]["properties"]["rule_domain"]["const"]
            for branch in wall
            if "if" in branch
        ]
        self.assertIn("clinical_care_gap", conditions)
        self.assertIn("revenue_integrity", conditions)

    def test_shipped_packages_satisfy_the_wall(self):
        self.assertTrue(_validate(self.wall, self.gap_pkg))
        self.assertTrue(_validate(self.wall, self.rev_pkg))

    def test_clinical_gap_rule_with_proposed_change_is_rejected_by_schema(self):
        payload = copy.deepcopy(self.gap_pkg)
        payload["rules"][0]["then"]["proposed_change"] = {"add_diagnoses": ["L89.153"]}
        self.assertFalse(_validate(self.wall, payload))

    def test_clinical_gap_rule_without_human_review_is_rejected_by_schema(self):
        payload = copy.deepcopy(self.gap_pkg)
        payload["rules"][0]["then"]["requires_human_review"] = False
        self.assertFalse(_validate(self.wall, payload))

    def test_revenue_rule_carrying_any_gap_field_is_rejected_by_schema(self):
        samples = {
            "gap_domain": "missing_action",
            "alert_urgency": "urgent",
            "recommended_action": "consult wound-care nurse",
            "expected_action": "reposition patient",
            "timing_window_days": 2,
            "clinical_impact": "pressure injury progression",
        }
        for field in GAP_ACTION_FIELDS:
            with self.subTest(gap_field=field):
                payload = copy.deepcopy(self.rev_pkg)
                payload["rules"][0]["then"][field] = samples[field]
                self.assertFalse(_validate(self.wall, payload))

    def test_evaluator_rejects_unsupported_keywords_fails_closed(self):
        # Guardrail on the evaluator itself: if a future wall keyword is not modelled,
        # the test blows up rather than silently passing.
        with self.assertRaises(AssertionError):
            _validate({"pattern": "^x$"}, "x")


if __name__ == "__main__":
    unittest.main()
