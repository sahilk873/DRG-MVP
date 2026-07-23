import copy
import json
from pathlib import Path
import unittest

from revenue_integrity.audit import canonical_hash
from revenue_integrity.engine import evaluate_condition
from revenue_integrity.rules import Condition, RulePackage
from revenue_integrity.subsumption import (
    CodeSubsumptionTable,
    load_code_subsumption_table,
)


ROOT = Path(__file__).parents[1]


def package():
    return json.loads((ROOT / "rules/wound_care_v1.json").read_text())


class RuleValidationTests(unittest.TestCase):
    def test_valid_package_parses(self):
        parsed = RulePackage.from_dict(package())
        self.assertEqual(len(parsed.rules), 2)
        self.assertEqual(parsed.rule_domain, "revenue_integrity")
        self.assertEqual(parsed.ontology.ontology_id, "wound-care-encounter-ontology")
        self.assertEqual(len(parsed.ontology.digest), 64)
        self.assertEqual(parsed.rules[0].applies_to.subject_types, ("Wound",))

    def test_malformed_ontology_digest_is_rejected(self):
        payload = package()
        payload["ontology"]["digest"] = "not-a-digest"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            RulePackage.from_dict(payload)

    def test_rule_scope_boolean_fails_closed(self):
        payload = package()
        payload["rules"][0]["applies_to"]["include_subtypes"] = "true"
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            RulePackage.from_dict(payload)

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


class NewOperatorParsingTests(unittest.TestCase):
    def test_new_operators_parse_with_valid_values(self):
        Condition.from_dict({"field": "attributes.stage", "op": "between", "value": [3, 4]})
        Condition.from_dict({"field": "concept", "op": "starts_with", "value": "L89"})
        Condition.from_dict({"field": "claim.diagnoses", "op": "count_gte", "value": 2})
        Condition.from_dict({"field": "claim.diagnoses", "op": "count_lte", "value": 5})

    def test_between_requires_ordered_numeric_pair(self):
        for bad in ([3], [3, 4, 5], [4, 3], ["3", "4"], [True, 4]):
            with self.assertRaisesRegex(ValueError, "between"):
                Condition.from_dict({"field": "attributes.stage", "op": "between", "value": bad})

    def test_count_operators_require_non_negative_int(self):
        for bad in (2.5, "2", True, -1):
            with self.assertRaisesRegex(ValueError, "count_gte"):
                Condition.from_dict({"field": "claim.diagnoses", "op": "count_gte", "value": bad})

    def test_starts_with_requires_non_empty_string(self):
        for bad in ("", 5, ["L89"]):
            with self.assertRaisesRegex(ValueError, "starts_with"):
                Condition.from_dict({"field": "concept", "op": "starts_with", "value": bad})

    def test_temporal_operators_parse_with_valid_day_thresholds(self):
        for op in ("elapsed_days_gte", "elapsed_days_lte", "absent_within_days"):
            Condition.from_dict({"field": "observed_at", "op": op, "value": 3})
            Condition.from_dict({"field": "observed_at", "op": op, "value": 1.5})

    def test_temporal_operators_reject_negative_or_non_numeric(self):
        for op in ("elapsed_days_gte", "elapsed_days_lte", "absent_within_days"):
            for bad in (-1, "3", True, [3]):
                with self.assertRaisesRegex(ValueError, "days"):
                    Condition.from_dict({"field": "observed_at", "op": op, "value": bad})

    def test_pct_change_operators_parse_with_numeric_threshold(self):
        Condition.from_dict({"field": "attributes.area", "op": "pct_change_gte", "value": 25})
        Condition.from_dict({"field": "attributes.area", "op": "pct_change_lte", "value": -50})

    def test_pct_change_operators_reject_non_numeric(self):
        for bad in ("25", True, [25]):
            with self.assertRaisesRegex(ValueError, "percentage"):
                Condition.from_dict({"field": "attributes.area", "op": "pct_change_gte", "value": bad})

    def test_co_occurs_parses_with_bounded_sub_conditions_and_window(self):
        parsed = Condition.from_dict({
            "co_occurs": [
                {"field": "concept", "op": "eq", "value": "a"},
                {"field": "concept", "op": "eq", "value": "b"},
            ],
            "window_days": 5,
        })
        self.assertEqual(len(parsed.co_occurs), 2)
        self.assertEqual(parsed.co_occurs_window_days, 5)

    def test_subsumed_by_parses_with_valid_code_reference(self):
        parsed = Condition.from_dict({"field": "concept", "op": "subsumed_by", "value": "L89"})
        self.assertEqual(parsed.operator, "subsumed_by")
        self.assertEqual(parsed.value, "L89")

    def test_subsumed_by_requires_non_empty_string(self):
        for bad in ("", 5, ["L89"], None):
            with self.assertRaisesRegex(ValueError, "subsumed_by"):
                Condition.from_dict({"field": "concept", "op": "subsumed_by", "value": bad})


def _subsumed_by(actual, ancestor):
    """Evaluate a subsumed_by condition against a single-field payload."""
    condition = Condition.from_dict({"field": "concept", "op": "subsumed_by", "value": ancestor})
    return evaluate_condition({"concept": actual}, condition)


class SubsumedByOperatorTests(unittest.TestCase):
    def test_governed_table_loads_and_verifies(self):
        table = load_code_subsumption_table()
        self.assertEqual(table.table_id, "code-subsumption")
        self.assertEqual(table.version, "1.0.0")
        self.assertEqual(len(table.digest), 64)

    def test_positive_specific_code_rolls_up_to_parent(self):
        # L89.153 -> L89.15 -> L89.1 -> L89 (multi-hop) and a direct-parent case.
        self.assertTrue(_subsumed_by("L89.153", "L89"))
        self.assertTrue(_subsumed_by("L89.153", "L89.15"))
        self.assertTrue(_subsumed_by("E11.65", "E11"))
        # A code is subsumed by itself.
        self.assertTrue(_subsumed_by("L89", "L89"))

    def test_negative_unrelated_code_is_not_subsumed(self):
        self.assertFalse(_subsumed_by("E11.9", "L89"))
        # Wrong direction: a parent is not subsumed by its child.
        self.assertFalse(_subsumed_by("L89", "L89.153"))
        # Code absent from the hierarchy only matches itself.
        self.assertFalse(_subsumed_by("Z99.99", "L89"))
        # Non-string coded value never spuriously matches.
        self.assertFalse(_subsumed_by(["L89.153"], "L89"))
        self.assertFalse(_subsumed_by(None, "L89"))

    def test_malformed_table_missing_field_fails_closed(self):
        payload = json.loads(
            (
                ROOT / "src/revenue_integrity/data/code_subsumption_v1.json"
            ).read_text()
        )
        del payload["parents"]
        with self.assertRaisesRegex(ValueError, "missing fields"):
            CodeSubsumptionTable.from_dict(payload)

    def test_malformed_table_bad_digest_fails_closed(self):
        payload = json.loads(
            (
                ROOT / "src/revenue_integrity/data/code_subsumption_v1.json"
            ).read_text()
        )
        payload["parents"]["X99"] = "X9"  # mutate content without updating the digest
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            CodeSubsumptionTable.from_dict(payload)

    def test_malformed_table_bad_shape_fails_closed(self):
        payload = json.loads(
            (
                ROOT / "src/revenue_integrity/data/code_subsumption_v1.json"
            ).read_text()
        )
        payload["parents"] = "not-an-object"
        with self.assertRaisesRegex(ValueError, "non-empty object"):
            CodeSubsumptionTable.from_dict(payload)

    def test_cycle_in_table_fails_closed(self):
        content = {
            "table_id": "code-subsumption",
            "version": "1.0.0",
            "status": "approved-for-demo",
            "parents": {"A": "B", "B": "A"},
        }
        content["digest"] = canonical_hash(content)
        with self.assertRaisesRegex(ValueError, "cycle"):
            CodeSubsumptionTable.from_dict(content)
