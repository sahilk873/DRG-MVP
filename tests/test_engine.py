import json
from dataclasses import replace
from pathlib import Path
import unittest

from revenue_integrity.agents import accept_agent_output
from revenue_integrity.engine import RuleEngine, evaluate_condition
from revenue_integrity.grouper import DeterministicDemoGrouper, GroupingResult
from revenue_integrity.models import Disposition, EncounterCase, ImpactStatus
from revenue_integrity.rules import Condition


ROOT = Path(__file__).parents[1]


def _match(payload, field, op, value=None):
    spec = {"field": field, "op": op}
    if op != "exists" or value is not None:
        spec["value"] = value
    return evaluate_condition(payload, Condition.from_dict(spec))


class SeverityRulePackageTests(unittest.TestCase):
    """The governed v2 package (rules/wound_care_v2.json) exercising the new DSL operators."""

    def setUp(self):
        self.payload = load("examples/case_pressure_injury_v2.json")
        self.rules = load("rules/wound_care_v2.json")

    def _findings(self, payload=None):
        case = EncounterCase.from_dict(payload or self.payload)
        return RuleEngine(self.rules, DeterministicDemoGrouper()).evaluate(case)

    def test_positive_both_rules_fire(self):
        findings = {f.rule_id: f for f in self._findings()}
        self.assertIn("WC-PI-SEVERITY-001", findings)
        self.assertIn("WC-PI-POA-002", findings)
        coding = findings["WC-PI-SEVERITY-001"]
        self.assertEqual(dict(coding.proposed_change), {"add_diagnoses": ["L89.154"]})
        self.assertEqual((coding.current_drg, coding.simulated_drg), ("DEMO-292", "DEMO-290"))
        self.assertEqual(coding.estimated_impact_cents, 842000)
        self.assertEqual(findings["WC-PI-POA-002"].disposition.value, "compliance_review")

    def test_negative_stage_outside_between_range_suppresses_coding_rule(self):
        # between [3,4] must exclude a stage-2 injury; the POA rule still fires.
        payload = load("examples/case_pressure_injury_v2.json")
        payload["assertions"][0]["attributes"]["stage"] = 2
        stage_entity = next(e for e in payload["ontology"]["entities"] if e["entity_type"] == "PressureInjuryStage")
        stage_entity["properties"]["value"] = "2"
        rule_ids = {f.rule_id for f in self._findings(payload)}
        self.assertNotIn("WC-PI-SEVERITY-001", rule_ids)
        self.assertIn("WC-PI-POA-002", rule_ids)

    def test_negative_poa_present_suppresses_compliance_rule(self):
        payload = load("examples/case_pressure_injury_v2.json")
        payload["assertions"][0]["attributes"]["poa"] = "Y"
        rule_ids = {f.rule_id for f in self._findings(payload)}
        self.assertIn("WC-PI-SEVERITY-001", rule_ids)
        self.assertNotIn("WC-PI-POA-002", rule_ids)

    def test_contradictory_evidence_suppresses_the_coding_rule(self):
        # has_contradicting_evidence eq false must fail when a contradiction is cited.
        payload = load("examples/case_pressure_injury_v2.json")
        payload["assertions"][0]["contradicting_evidence_ids"] = ["EV-001"]
        rule_ids = {f.rule_id for f in self._findings(payload)}
        self.assertNotIn("WC-PI-SEVERITY-001", rule_ids)

    def test_already_coded_claim_suppresses_the_coding_rule(self):
        payload = load("examples/case_pressure_injury_v2.json")
        payload["claim"]["diagnoses"] = ["A41.9", "L89.154"]
        rule_ids = {f.rule_id for f in self._findings(payload)}
        self.assertNotIn("WC-PI-SEVERITY-001", rule_ids)


class NewOperatorEvaluationTests(unittest.TestCase):
    def test_between_is_inclusive_and_type_safe(self):
        payload = {"attributes": {"stage": 4}}
        self.assertTrue(_match(payload, "attributes.stage", "between", [3, 4]))
        self.assertFalse(_match({"attributes": {"stage": 2}}, "attributes.stage", "between", [3, 4]))
        self.assertFalse(_match({"attributes": {"stage": True}}, "attributes.stage", "between", [0, 5]))

    def test_starts_with_matches_prefix_and_ignores_non_strings(self):
        self.assertTrue(_match({"concept": "L89.154"}, "concept", "starts_with", "L89"))
        self.assertFalse(_match({"concept": "E11.9"}, "concept", "starts_with", "L89"))
        self.assertFalse(_match({"concept": 189}, "concept", "starts_with", "L89"))

    def test_count_operators_count_collections_not_characters(self):
        claim = {"claim": {"diagnoses": ["A", "B", "C"]}}
        self.assertTrue(_match(claim, "claim.diagnoses", "count_gte", 2))
        self.assertFalse(_match({"claim": {"diagnoses": ["A"]}}, "claim.diagnoses", "count_gte", 2))
        self.assertTrue(_match(claim, "claim.diagnoses", "count_lte", 3))
        # A string field must never be counted as characters.
        self.assertFalse(_match({"claim": {"diagnoses": "ABCDE"}}, "claim.diagnoses", "count_gte", 2))

    def test_derived_assertion_fields_are_available_to_rules(self):
        # Exercise the exact payload the engine exposes for an assertion, via _matches_assertion.
        case = EncounterCase.from_dict(load("examples/case_pressure_injury.json"))
        assertion = case.assertions[0]
        subject = next(e for e in case.ontology.entities if e.entity_id == assertion.subject_id)

        def matches(field, op, value):
            return RuleEngine._matches_assertion(assertion, subject, Condition.from_dict(
                {"field": field, "op": op, "value": value}
            ))

        self.assertTrue(matches("evidence_count", "gte", 1))
        self.assertTrue(matches("has_contradicting_evidence", "eq", False))
        self.assertFalse(matches("has_contradicting_evidence", "eq", True))

        contradicted = replace(assertion, contradicting_evidence_ids=(assertion.evidence_ids[0] + "-x",))
        self.assertTrue(RuleEngine._matches_assertion(
            contradicted, subject,
            Condition.from_dict({"field": "has_contradicting_evidence", "op": "eq", "value": True}),
        ))


class DenialFactsCaseConditionTests(unittest.TestCase):
    """Read-only denial facts exposed to the declarative case-condition DSL (additive)."""

    def setUp(self):
        self.rules = load("rules/wound_care_v2.json")
        # Gate the coding rule on a denial-amount case condition.
        self.rules["rules"][0]["case_conditions"].append(
            {"field": "financial.denied_amount_cents", "op": "gte", "value": 1}
        )

    def _financial(self, claim_id):
        return {
            "schema_version": "1.0.0",
            "payer_id": "PAYER-1",
            "claim_id": claim_id,
            "denials": [{
                "denial_id": "DEN-1",
                "line_ids": ["L1"],
                "reason_code": "CO-97",
                "status": "open",
                "amount_cents": 5000,
            }],
            "claim_lines": [{
                "line_id": "L1", "code": "L89.154", "code_system": "ICD-10-CM",
                "units": 1, "charged_amount_cents": 5000,
            }],
        }

    def _rule_ids(self, payload):
        case = EncounterCase.from_dict(payload)
        return {f.rule_id for f in RuleEngine(self.rules, DeterministicDemoGrouper()).evaluate(case)}

    def test_rule_fires_when_case_has_denials(self):
        payload = load("examples/case_pressure_injury_v2.json")
        payload["financial"] = self._financial(payload["case_id"])
        self.assertIn("WC-PI-SEVERITY-001", self._rule_ids(payload))

    def test_rule_does_not_fire_without_financial_snapshot(self):
        payload = load("examples/case_pressure_injury_v2.json")
        self.assertNotIn("financial", payload)
        self.assertNotIn("WC-PI-SEVERITY-001", self._rule_ids(payload))

    def test_financial_facts_all_zero_when_snapshot_absent(self):
        from revenue_integrity.engine import _financial_facts
        self.assertEqual(
            _financial_facts(None),
            {"has_denials": False, "denied_amount_cents": 0, "denial_count": 0},
        )


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


class DrgSequencingFindingTests(unittest.TestCase):
    """Deterministic SYSTEM-DRG-SEQUENCING counterfactual (only fires with diagnosis_details)."""

    def _findings(self, payload):
        return RuleEngine(
            load("rules/wound_care_v1.json"), DeterministicDemoGrouper()
        ).evaluate(EncounterCase.from_dict(payload))

    def _sequencing(self, findings):
        return [item for item in findings if item.rule_id == "SYSTEM-DRG-SEQUENCING"]

    def test_positive_mis_sequenced_details_emit_finding_with_drg_delta(self):
        # Submitted DRG claims MCC (DEMO-290), but the MCC-severity code L89.154 is a HAC
        # documented as NOT present on admission (poa='N'); re-grouping under the documented
        # principal + POA/HAC drops severity to CC -> DEMO-291. The finding must appear.
        payload = load("examples/case_pressure_injury.json")
        payload["claim"]["drg"] = "DEMO-290"
        payload["claim"]["allowed_amount_cents"] = 1_842_000
        payload["claim"]["diagnoses"] = ["L89.154", "L89.100"]
        payload["claim"]["diagnosis_details"] = [
            {"code": "L89.100", "sequence": 1, "poa": "Y"},
            {"code": "L89.154", "sequence": 2, "poa": "N"},
        ]
        findings = self._sequencing(self._findings(payload))
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.rule_package_id, "deterministic-system-checks")
        self.assertEqual(finding.submitted_drg, "DEMO-290")
        self.assertEqual(finding.simulated_drg, "DEMO-291")
        self.assertEqual(dict(finding.proposed_change), {"replace_drg": ["DEMO-291"]})
        self.assertTrue(finding.requires_human_review)
        self.assertIs(finding.disposition, Disposition.CODING_REVIEW)
        self.assertIs(finding.impact_status, ImpactStatus.ESTIMATED)
        # Payment for DEMO-291 (1,280,000c) minus submitted allowed (1,842,000c).
        self.assertEqual(finding.estimated_impact_cents, 1_280_000 - 1_842_000)
        self.assertTrue(finding.derivation["simulated"])

    def test_negative_no_diagnosis_details_emits_no_sequencing_finding(self):
        # The demo case carries no diagnosis_details -> the counterfactual never runs.
        payload = load("examples/case_pressure_injury.json")
        self.assertNotIn("diagnosis_details", payload["claim"])
        self.assertEqual(self._sequencing(self._findings(payload)), [])

    def test_negative_correctly_sequenced_details_emit_no_finding(self):
        # Documented principal + POA reproduce exactly the submitted DRG -> no finding.
        payload = load("examples/case_pressure_injury.json")
        payload["claim"]["drg"] = "DEMO-290"
        payload["claim"]["diagnoses"] = ["L89.154", "L89.100"]
        payload["claim"]["diagnosis_details"] = [
            {"code": "L89.154", "sequence": 1, "poa": "Y"},
            {"code": "L89.100", "sequence": 2, "poa": "Y"},
        ]
        self.assertEqual(self._sequencing(self._findings(payload)), [])

    def test_missing_allowed_amount_is_unavailable_not_zero(self):
        payload = load("examples/case_pressure_injury.json")
        payload["claim"]["drg"] = "DEMO-290"
        payload["claim"]["allowed_amount_cents"] = None
        payload["claim"]["diagnoses"] = ["L89.154", "L89.100"]
        payload["claim"]["diagnosis_details"] = [
            {"code": "L89.100", "sequence": 1, "poa": "Y"},
            {"code": "L89.154", "sequence": 2, "poa": "N"},
        ]
        finding = self._sequencing(self._findings(payload))[0]
        self.assertIsNone(finding.estimated_impact_cents)
        self.assertIs(finding.impact_status, ImpactStatus.UNAVAILABLE)
