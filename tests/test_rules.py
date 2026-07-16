import copy
import json
from pathlib import Path
import unittest

from revenue_integrity.rules import Condition, RulePackage


ROOT = Path(__file__).parents[1]


def package():
    return json.loads((ROOT / "rules/wound_care_v1.json").read_text())


class RuleValidationTests(unittest.TestCase):
    def test_valid_package_parses(self):
        parsed = RulePackage.from_dict(package())
        self.assertEqual(len(parsed.rules), 2)
        self.assertEqual(parsed.rule_domain, "revenue_integrity")
        self.assertEqual(parsed.ontology.ontology_id, "wound-care-encounter-ontology")

    def test_duplicate_rule_ids_are_rejected(self):
        payload = package()
        payload["rules"].append(copy.deepcopy(payload["rules"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            RulePackage.from_dict(payload)

    def test_generated_code_cannot_be_added_to_action(self):
        payload = package()
        payload["rules"][0]["then"]["proposed_change"]["python"] = ["os.system('bad')"]
        with self.assertRaisesRegex(ValueError, "unsupported"):
            RulePackage.from_dict(payload)

    def test_empty_combinator_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            Condition.from_dict({"all": []})

    def test_non_revenue_rule_domain_is_rejected(self):
        payload = package()
        payload["rule_domain"] = "clinical_decision_support"
        with self.assertRaisesRegex(ValueError, "revenue_integrity"):
            RulePackage.from_dict(payload)
