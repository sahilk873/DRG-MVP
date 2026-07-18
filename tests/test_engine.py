import json
from dataclasses import replace
from pathlib import Path
import unittest

from revenue_integrity.agents import accept_agent_output
from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper, GroupingResult
from revenue_integrity.models import Disposition, EncounterCase, ImpactStatus


ROOT = Path(__file__).parents[1]


def load(name: str):
    return json.loads((ROOT / name).read_text())


class RuleEngineTests(unittest.TestCase):
    def test_explicit_omitted_pressure_injury_generates_review(self):
        case = EncounterCase.from_dict(load("examples/case_pressure_injury.json"))
        engine = RuleEngine(load("rules/wound_care_v1.json"), DeterministicDemoGrouper())

        findings = engine.evaluate(case)

        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].disposition, Disposition.CODING_REVIEW)
        self.assertEqual(findings[0].proposed_change, {"add_diagnoses": ["L89.154"]})
        self.assertEqual(findings[0].current_drg, "DEMO-292")
        self.assertEqual(findings[0].simulated_drg, "DEMO-290")
        self.assertEqual(findings[0].estimated_impact_cents, 842000)
        self.assertEqual(findings[0].subject_ids, ("wound:1",))
        self.assertEqual(findings[0].assertion_ids, ("AS-001",))
        self.assertEqual(findings[0].grouper_version, "demo-0.2-not-for-billing")
        self.assertTrue(findings[0].requires_human_review)


    def test_existing_code_does_not_generate_omission_finding(self):
        payload = load("examples/case_pressure_injury.json")
        payload["claim"]["diagnoses"].append("L89.154")
        case = EncounterCase.from_dict(payload)
        findings = RuleEngine(load("rules/wound_care_v1.json"), DeterministicDemoGrouper()).evaluate(case)
        self.assertFalse(any(item.rule_id == "WC-PI-OMITTED-001" for item in findings))
        self.assertTrue(any(item.rule_id == "SYSTEM-DRG-REPRODUCTION" for item in findings))


    def test_agent_output_rejects_dangling_evidence_reference(self):
        payload = load("examples/case_pressure_injury.json")
        payload["assertions"][0]["evidence_ids"] = ["MISSING"]
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            accept_agent_output(payload)


    def test_inferred_assertion_creates_query_without_code_change(self):
        payload = load("examples/case_pressure_injury.json")
        payload["assertions"][0]["documentation_status"] = "inferred"
        payload["assertions"][0]["confidence"] = 0.82
        case = EncounterCase.from_dict(payload)
        findings = RuleEngine(load("rules/wound_care_v1.json"), DeterministicDemoGrouper()).evaluate(case)
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].disposition, Disposition.CDI_QUERY)
        self.assertEqual(findings[0].proposed_change, {})
        self.assertEqual(findings[0].estimated_impact_cents, 0)

    def test_unapproved_rule_package_fails_closed(self):
        rules = load("rules/wound_care_v1.json")
        rules["status"] = "clinical-review-required"
        with self.assertRaisesRegex(ValueError, "not executable"):
            RuleEngine(rules, DeterministicDemoGrouper())

    def test_incompatible_rule_ontology_is_rejected(self):
        rules = load("rules/wound_care_v1.json")
        rules["ontology"]["version"] = "different-version"
        case = EncounterCase.from_dict(load("examples/case_pressure_injury.json"))
        with self.assertRaisesRegex(ValueError, "incompatible ontology"):
            RuleEngine(rules, DeterministicDemoGrouper()).evaluate(case)

    def test_rule_package_digest_drift_is_rejected(self):
        rules = load("rules/wound_care_v1.json")
        rules["ontology"]["digest"] = "0" * 64
        case = EncounterCase.from_dict(load("examples/case_pressure_injury.json"))
        with self.assertRaisesRegex(ValueError, "incompatible ontology"):
            RuleEngine(rules, DeterministicDemoGrouper()).evaluate(case)

    def test_rules_can_target_generic_ontology_subject_fields(self):
        rules = load("rules/wound_care_v1.json")
        rules["rules"][0]["when"]["all"].append({
            "field": "subject.entity_type",
            "op": "eq",
            "value": "PressureInjury",
        })
        case = EncounterCase.from_dict(load("examples/case_pressure_injury.json"))
        findings = RuleEngine(rules, DeterministicDemoGrouper()).evaluate(case)
        self.assertTrue(any(item.rule_id == "WC-PI-OMITTED-001" for item in findings))

    def test_rule_scope_rejects_matching_free_form_fields_on_wrong_entity_type(self):
        payload = load("examples/case_pressure_injury.json")
        payload["assertions"][0]["subject_id"] = "root:patient"
        case = EncounterCase.from_dict(payload)
        findings = RuleEngine(
            load("rules/wound_care_v1.json"), DeterministicDemoGrouper()
        ).evaluate(case)
        self.assertEqual(findings, [])

    def test_rule_scope_can_exclude_subclasses(self):
        rules = load("rules/wound_care_v1.json")
        rules["rules"][0]["applies_to"]["include_subtypes"] = False
        case = EncounterCase.from_dict(load("examples/case_pressure_injury.json"))
        findings = RuleEngine(rules, DeterministicDemoGrouper()).evaluate(case)
        self.assertFalse(any(item.rule_id == "WC-PI-OMITTED-001" for item in findings))

    def test_unknown_rule_scope_class_is_rejected_before_evaluation(self):
        rules = load("rules/wound_care_v1.json")
        rules["rules"][0]["applies_to"]["subject_types"] = ["NotAClass"]
        with self.assertRaisesRegex(ValueError, "unknown ontology classes"):
            RuleEngine(rules, DeterministicDemoGrouper())

    def test_finding_retains_contradicting_evidence(self):
        payload = load("examples/case_pressure_injury.json")
        payload["evidence"].append({
            "evidence_id": "EV-002",
            "document_id": "WOUND-NOTE-002",
            "author_role": "nurse",
            "recorded_at": "2026-06-01T15:00:00Z",
            "text": "Earlier assessment described unstageable eschar."
        })
        payload["assertions"][0]["contradicting_evidence_ids"] = ["EV-002"]
        finding = RuleEngine(
            load("rules/wound_care_v1.json"), DeterministicDemoGrouper()
        ).evaluate(EncounterCase.from_dict(payload))[0]
        self.assertEqual(finding.contradicting_evidence_ids, ("EV-002",))

    def test_mixed_grouper_versions_are_rejected(self):
        class UnstableGrouper:
            calls = 0

            def group(self, case, claim):
                self.calls += 1
                return GroupingResult("TEST", 100, f"version-{self.calls}")

        case = EncounterCase.from_dict(load("examples/case_pressure_injury.json"))
        with self.assertRaisesRegex(ValueError, "same grouper version"):
            RuleEngine(load("rules/wound_care_v1.json"), UnstableGrouper()).evaluate(case)

    def test_submitted_drg_is_independently_reproduced(self):
        payload = load("examples/case_pressure_injury.json")
        payload["claim"]["drg"] = "DEMO-WRONG"
        findings = RuleEngine(
            load("rules/wound_care_v1.json"), DeterministicDemoGrouper()
        ).evaluate(EncounterCase.from_dict(payload))
        mismatch = next(item for item in findings if item.rule_id == "SYSTEM-DRG-REPRODUCTION")
        self.assertEqual(mismatch.submitted_drg, "DEMO-WRONG")
        self.assertEqual(mismatch.current_drg, "DEMO-292")
        self.assertTrue(mismatch.requires_human_review)
        self.assertIs(mismatch.impact_status, ImpactStatus.ESTIMATED)

    def test_missing_allowed_amount_is_unknown_not_zero(self):
        payload = load("examples/case_pressure_injury.json")
        payload["claim"]["drg"] = "DEMO-WRONG"
        payload["claim"]["allowed_amount_cents"] = None
        mismatch = next(
            item for item in RuleEngine(
                load("rules/wound_care_v1.json"), DeterministicDemoGrouper()
            ).evaluate(EncounterCase.from_dict(payload))
            if item.rule_id == "SYSTEM-DRG-REPRODUCTION"
        )
        self.assertIsNone(mismatch.estimated_impact_cents)
        self.assertIs(mismatch.impact_status, ImpactStatus.UNAVAILABLE)

    def test_finding_rejects_inconsistent_impact_state(self):
        case = EncounterCase.from_dict(load("examples/case_pressure_injury.json"))
        finding = RuleEngine(
            load("rules/wound_care_v1.json"), DeterministicDemoGrouper()
        ).evaluate(case)[0]
        with self.assertRaisesRegex(ValueError, "requires estimated_impact_cents"):
            replace(finding, estimated_impact_cents=None)
        with self.assertRaisesRegex(ValueError, "cannot carry an estimate"):
            replace(finding, impact_status=ImpactStatus.UNAVAILABLE)
