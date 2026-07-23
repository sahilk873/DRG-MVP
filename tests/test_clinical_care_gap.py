"""Phase 1: the walled-off clinical_care_gap rule domain.

Covers the structural domain wall (both directions), the native temporal / percentage /
co-occurrence operators, and clinical_care_gap Finding serialization. Existing
revenue_integrity behavior is exercised by tests/test_engine.py and tests/test_rules.py.
"""

import copy
import json
from dataclasses import replace
from pathlib import Path
import unittest

from datetime import datetime, timezone

from revenue_integrity.engine import RuleEngine, _iso_sort_key, evaluate_condition
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import (
    ClinicalUrgency,
    Disposition,
    EncounterCase,
    ExceptionType,
    Finding,
    GapDomain,
    GapStatus,
    ImpactStatus,
    RuleDomain,
)
from revenue_integrity.rules import Condition, RuleAction, RulePackage


ROOT = Path(__file__).parents[1]

ONTOLOGY_BINDING = {
    "ontology_id": "wound-care-encounter-ontology",
    "version": "1.1.0-draft",
    "digest": "66da3211d53adaa7cffc4fd45e0a7ca86175f5a7774d5ce80d4a34a0a0786f52",
}


def load(name: str):
    return json.loads((ROOT / name).read_text())


def gap_package(rules):
    return {
        "package_id": "wound-care-clinical-care-gap",
        "version": "0.1.0-demo",
        "rule_domain": "clinical_care_gap",
        "ontology": copy.deepcopy(ONTOLOGY_BINDING),
        "status": "approved-for-demo",
        "effective_from": "2026-01-01",
        "rules": list(rules),
    }


def gap_rule(rule_id="CG-PI-STAGE4-REVIEW", *, when=None, then=None):
    return {
        "rule_id": rule_id,
        "title": "Documented stage 4 pressure injury requires wound-care follow-through",
        "applies_to": {"subject_types": ["PressureInjury"], "include_subtypes": True},
        "when": when or {"field": "concept", "op": "eq", "value": "pressure_injury"},
        "then": then or {
            "disposition": "cdi_query",
            "requires_human_review": True,
            "proposed_change": {},
            "rationale": "Stage 4 pressure injury documented without a follow-through action.",
            "gap_domain": "missing_action",
            "expected_action": "wound_care_consult",
            "timing_window_days": 2,
            "recommended_action": "Order a wound-care consult and reassess the pressure injury.",
            "alert_urgency": "urgent",
            "clinical_impact": "Unaddressed stage 4 injuries risk osteomyelitis.",
        },
    }


class DomainWallTests(unittest.TestCase):
    def test_clinical_care_gap_rule_with_claim_change_is_rejected(self):
        rule = gap_rule()
        rule["then"]["proposed_change"] = {"add_diagnoses": ["L89.154"]}
        with self.assertRaisesRegex(ValueError, "must not carry a proposed_change"):
            RulePackage.from_dict(gap_package([rule]))

    def test_revenue_integrity_rule_with_clinical_fields_is_rejected(self):
        payload = load("rules/wound_care_v1.json")
        payload["rules"][0]["then"]["gap_domain"] = "missing_action"
        with self.assertRaisesRegex(ValueError, "must not carry clinical action fields"):
            RulePackage.from_dict(payload)

    def test_revenue_integrity_rule_with_alert_urgency_is_rejected(self):
        payload = load("rules/wound_care_v1.json")
        payload["rules"][0]["then"]["alert_urgency"] = "urgent"
        with self.assertRaisesRegex(ValueError, "must not carry clinical action fields"):
            RulePackage.from_dict(payload)

    def test_clinical_care_gap_rule_must_require_human_review(self):
        rule = gap_rule()
        rule["then"]["requires_human_review"] = False
        with self.assertRaisesRegex(ValueError, "must require human review"):
            RulePackage.from_dict(gap_package([rule]))

    def test_clinical_care_gap_package_parses_and_carries_domain(self):
        parsed = RulePackage.from_dict(gap_package([gap_rule()]))
        self.assertEqual(parsed.rule_domain, RuleDomain.CLINICAL_CARE_GAP.value)
        action = parsed.rules[0].action
        self.assertIs(action.gap_domain, GapDomain.MISSING_ACTION)
        self.assertIs(action.alert_urgency, ClinicalUrgency.URGENT)

    def test_unknown_domain_is_rejected(self):
        payload = gap_package([gap_rule()])
        payload["rule_domain"] = "clinical_decision_support"
        with self.assertRaisesRegex(ValueError, "only permits the domains"):
            RulePackage.from_dict(payload)


class TemporalOperatorTests(unittest.TestCase):
    def _match(self, payload, op, value):
        return evaluate_condition(payload, Condition.from_dict({"field": "observed_at", "op": op, "value": value}))

    def test_elapsed_days_gte_positive_and_negative(self):
        # Day 0 = 2026-06-01, reference = 2026-06-06 -> 5 calendar days elapsed.
        payload = {"observed_at": "2026-06-01T14:30:00Z", "reference_date": "2026-06-06T15:00:00Z"}
        self.assertTrue(self._match(payload, "elapsed_days_gte", 3))
        self.assertFalse(self._match(payload, "elapsed_days_gte", 7))

    def test_elapsed_days_lte_positive_and_negative(self):
        payload = {"observed_at": "2026-06-01T14:30:00Z", "reference_date": "2026-06-06T15:00:00Z"}
        self.assertTrue(self._match(payload, "elapsed_days_lte", 7))
        self.assertFalse(self._match(payload, "elapsed_days_lte", 3))

    def test_same_calendar_day_is_zero_days(self):
        payload = {"observed_at": "2026-06-06T01:00:00Z", "reference_date": "2026-06-06T23:00:00Z"}
        self.assertTrue(self._match(payload, "elapsed_days_lte", 0))
        self.assertFalse(self._match(payload, "elapsed_days_gte", 1))

    def test_missing_dates_never_fire(self):
        self.assertFalse(self._match({"observed_at": None, "reference_date": "2026-06-06T15:00:00Z"}, "elapsed_days_gte", 0))
        self.assertFalse(self._match({"observed_at": "2026-06-01T14:30:00Z", "reference_date": None}, "elapsed_days_gte", 0))
        self.assertFalse(self._match({"observed_at": "not-a-date", "reference_date": "2026-06-06T15:00:00Z"}, "elapsed_days_gte", 0))

    def test_absent_within_days_leaf_fails_safe(self):
        # With NO assertion-set context absent_within_days fails safe (never fires), so a bare
        # leaf can never silently misfire.
        payload = {"observed_at": "2026-06-01T14:30:00Z", "reference_date": "2026-06-06T15:00:00Z"}
        self.assertFalse(self._match(payload, "absent_within_days", 3))


class AbsentWithinDaysSetAwareTests(unittest.TestCase):
    """absent_within_days is set-aware: TRUE when NO assertion is dated within the window."""

    REFERENCE = "2026-06-16T12:00:00Z"

    def _condition(self, days):
        return Condition.from_dict({"field": "observed_at", "op": "absent_within_days", "value": days})

    def _run(self, assertions, days):
        # The reference date lives on each assertion payload (as the engine builds it); here we
        # place it on the driving payload too so the operator reads a grounded reference.
        payload = {"reference_date": self.REFERENCE}
        return evaluate_condition(payload, self._condition(days), assertions=assertions)

    def test_positive_fires_when_action_truly_absent_in_window(self):
        # Reference = Jun 16; the only reassessment is Jun 1 (15 days prior), outside a 3-day
        # window -> the action is absent within 3 days -> operator is TRUE.
        assertions = [{"observed_at": "2026-06-01T09:00:00Z", "reference_date": self.REFERENCE}]
        self.assertTrue(self._run(assertions, 3))

    def test_negative_does_not_fire_when_action_present_in_window(self):
        # A reassessment on Jun 14 is 2 days before the reference -> within a 3-day window ->
        # the action is present -> operator is FALSE.
        assertions = [
            {"observed_at": "2026-06-01T09:00:00Z", "reference_date": self.REFERENCE},
            {"observed_at": "2026-06-14T09:00:00Z", "reference_date": self.REFERENCE},
        ]
        self.assertFalse(self._run(assertions, 3))

    def test_negate_present_within_window_is_coherent(self):
        # `not absent_within_days` means "present within the window": true when a dated action
        # falls inside the window, false when none does. This is the leaf/negate behavior.
        present = [{"observed_at": "2026-06-14T09:00:00Z", "reference_date": self.REFERENCE}]
        absent = [{"observed_at": "2026-06-01T09:00:00Z", "reference_date": self.REFERENCE}]
        negated = Condition.from_dict({"not": {"field": "observed_at", "op": "absent_within_days", "value": 3}})
        payload = {"reference_date": self.REFERENCE}
        self.assertTrue(evaluate_condition(payload, negated, assertions=present))
        self.assertFalse(evaluate_condition(payload, negated, assertions=absent))

    def test_fails_safe_without_reference_or_undated_assertions(self):
        # No reference date -> fail safe (False). Undated assertions cannot prove presence, so a
        # set of only undated assertions is treated as absent (TRUE) when a reference exists.
        undated = [{"observed_at": None, "reference_date": self.REFERENCE}]
        self.assertFalse(evaluate_condition({}, self._condition(3), assertions=undated))
        self.assertTrue(self._run(undated, 3))


class IsoSortKeyTests(unittest.TestCase):
    """_iso_sort_key's unparseable-date sentinel must be tz-aware to sort among tz-aware dates."""

    def test_malformed_date_does_not_crash_min_over_tz_aware_dates(self):
        # A malformed date mixed with tz-aware ISO dates must not raise "can't compare
        # offset-naive and offset-aware datetimes" through min()/sorted().
        values = ["2026-06-05T00:00:00Z", "not-a-date", "2026-06-01T00:00:00Z"]
        # Sentinel is tz-aware UTC max, so the malformed value never wins min() and comparison
        # against real tz-aware dates is well-defined.
        self.assertEqual(min(values, key=_iso_sort_key), "2026-06-01T00:00:00Z")
        ordered = sorted(values, key=_iso_sort_key)
        self.assertEqual(ordered[0], "2026-06-01T00:00:00Z")
        self.assertEqual(ordered[-1], "not-a-date")

    def test_sentinel_is_tz_aware_utc(self):
        key = _iso_sort_key("not-a-date")
        self.assertIsNotNone(key.tzinfo)
        self.assertEqual(key.utcoffset(), timezone.utc.utcoffset(datetime.now(timezone.utc)))


class PctChangeOperatorTests(unittest.TestCase):
    def _match(self, actual, op, value):
        payload = {"attributes": {"area": actual}}
        return evaluate_condition(payload, Condition.from_dict({"field": "attributes.area", "op": op, "value": value}))

    def test_pct_change_gte_positive_and_negative(self):
        # [baseline, current] = [10, 15] -> +50%.
        self.assertTrue(self._match([10, 15], "pct_change_gte", 25))
        self.assertFalse(self._match([10, 15], "pct_change_gte", 75))

    def test_pct_change_lte_detects_decrease(self):
        # [20, 10] -> -50%.
        self.assertTrue(self._match([20, 10], "pct_change_lte", -25))
        self.assertFalse(self._match([20, 10], "pct_change_lte", -75))

    def test_pct_change_fails_safe_on_bad_shape_or_zero_baseline(self):
        self.assertFalse(self._match([10], "pct_change_gte", 0))
        self.assertFalse(self._match([0, 10], "pct_change_gte", 0))
        self.assertFalse(self._match(["10", "15"], "pct_change_gte", 0))
        self.assertFalse(self._match([True, 15], "pct_change_gte", 0))


class CoOccurrenceTests(unittest.TestCase):
    def _assertions(self):
        return [
            {"concept": "pressure_injury", "observed_at": "2026-06-01T00:00:00Z"},
            {"concept": "infection", "observed_at": "2026-06-03T00:00:00Z"},
        ]

    def _condition(self, window=None):
        spec = {
            "co_occurs": [
                {"field": "concept", "op": "eq", "value": "pressure_injury"},
                {"field": "concept", "op": "eq", "value": "infection"},
            ]
        }
        if window is not None:
            spec["window_days"] = window
        return Condition.from_dict(spec)

    def test_positive_both_sub_conditions_satisfied_by_the_set(self):
        self.assertTrue(evaluate_condition({}, self._condition(), assertions=self._assertions()))

    def test_negative_missing_sub_condition(self):
        assertions = [self._assertions()[0]]
        self.assertFalse(evaluate_condition({}, self._condition(), assertions=assertions))

    def test_windowed_positive_within_span(self):
        self.assertTrue(evaluate_condition({}, self._condition(window=3), assertions=self._assertions()))

    def test_windowed_negative_outside_span(self):
        self.assertFalse(evaluate_condition({}, self._condition(window=1), assertions=self._assertions()))

    def test_windowed_fails_safe_when_date_missing(self):
        assertions = [
            {"concept": "pressure_injury", "observed_at": None},
            {"concept": "infection", "observed_at": "2026-06-03T00:00:00Z"},
        ]
        self.assertFalse(evaluate_condition({}, self._condition(window=30), assertions=assertions))

    def test_co_occurs_requires_at_least_two(self):
        with self.assertRaisesRegex(ValueError, "at least two"):
            Condition.from_dict({"co_occurs": [{"field": "concept", "op": "eq", "value": "x"}]})

    def test_co_occurs_is_bounded(self):
        subs = [{"field": "concept", "op": "eq", "value": str(i)} for i in range(6)]
        with self.assertRaisesRegex(ValueError, "at most"):
            Condition.from_dict({"co_occurs": subs})


class GapFindingTests(unittest.TestCase):
    def _engine(self, rules):
        return RuleEngine(gap_package(rules), DeterministicDemoGrouper())

    def _case(self):
        return EncounterCase.from_dict(load("examples/case_pressure_injury.json"))

    def test_gap_rule_fires_and_emits_clinical_fields(self):
        findings = self._engine([gap_rule()]).evaluate(self._case())
        gap = next(f for f in findings if f.rule_id == "CG-PI-STAGE4-REVIEW")
        self.assertIs(gap.gap_domain, GapDomain.MISSING_ACTION)
        self.assertIs(gap.alert_urgency, ClinicalUrgency.URGENT)
        self.assertEqual(gap.expected_action, "wound_care_consult")
        self.assertEqual(gap.timing_window_days, 2)
        self.assertIs(gap.gap_status, GapStatus.OPEN)
        self.assertTrue(gap.requires_human_review)
        # No monetary impact: analytics identify, clinicians decide.
        self.assertIsNone(gap.estimated_impact_cents)
        self.assertIs(gap.impact_status, ImpactStatus.NOT_APPLICABLE)
        self.assertEqual(dict(gap.proposed_change), {})

    def test_temporal_gap_rule_fires_on_elapsed_days(self):
        # Evidence recorded 2026-06-01, discharge 2026-06-06 -> 5 elapsed calendar days.
        rule = gap_rule(
            rule_id="CG-PI-DELAYED",
            when={"all": [
                {"field": "concept", "op": "eq", "value": "pressure_injury"},
                {"field": "observed_at", "op": "elapsed_days_gte", "value": 3},
            ]},
        )
        rule["then"]["gap_domain"] = "delayed_action"
        findings = {f.rule_id for f in self._engine([rule]).evaluate(self._case())}
        self.assertIn("CG-PI-DELAYED", findings)

    def test_temporal_gap_rule_does_not_fire_when_window_too_long(self):
        rule = gap_rule(
            rule_id="CG-PI-DELAYED",
            when={"all": [
                {"field": "concept", "op": "eq", "value": "pressure_injury"},
                {"field": "observed_at", "op": "elapsed_days_gte", "value": 30},
            ]},
        )
        rule["then"]["gap_domain"] = "delayed_action"
        findings = {f.rule_id for f in self._engine([rule]).evaluate(self._case())}
        self.assertNotIn("CG-PI-DELAYED", findings)


class GapFindingSerializationTests(unittest.TestCase):
    def _gap_finding(self, **overrides):
        base = dict(
            finding_id="finding-abc",
            rule_id="CG-1",
            rule_package_id="pkg",
            rule_package_version="0.1.0",
            title="Gap",
            disposition=Disposition.CDI_QUERY,
            confidence=0.9,
            proposed_change={},
            subject_ids=("wound:1",),
            assertion_ids=("AS-001",),
            evidence_ids=("EV-001",),
            contradicting_evidence_ids=(),
            rationale="reason",
            requires_human_review=True,
            submitted_drg=None,
            current_drg="DEMO-292",
            simulated_drg="DEMO-292",
            estimated_impact_cents=None,
            impact_status=ImpactStatus.NOT_APPLICABLE,
            grouper_version="demo-0.2-not-for-billing",
            gap_domain=GapDomain.DELAYED_ACTION,
            expected_action="reassessment",
            actual_action="none",
            timing_window_days=3,
            alert_urgency=ClinicalUrgency.SAME_DAY,
            recommended_action="Reassess wound within 3 days.",
            clinical_impact="Delay risks deterioration.",
            exception_checks=(
                {"exception_type": ExceptionType.PATIENT_REFUSAL, "evidence_id": "EV-001", "status": "not_applicable"},
            ),
            barrier_code="BARRIER-STAFFING",
        )
        base.update(overrides)
        return Finding(**base)

    def test_clinical_gap_finding_round_trips(self):
        finding = self._gap_finding()
        data = finding.to_dict()
        self.assertEqual(data["gap_domain"], "delayed_action")
        self.assertEqual(data["expected_action"], "reassessment")
        self.assertEqual(data["actual_action"], "none")
        self.assertEqual(data["timing_window_days"], 3)
        self.assertEqual(data["alert_urgency"], "same_day")
        self.assertEqual(data["recommended_action"], "Reassess wound within 3 days.")
        self.assertEqual(data["clinical_impact"], "Delay risks deterioration.")
        self.assertEqual(data["gap_status"], "open")  # defaulted
        self.assertEqual(data["barrier_code"], "BARRIER-STAFFING")
        self.assertEqual(
            data["exception_checks"],
            [{"exception_type": "patient_refusal", "evidence_id": "EV-001", "status": "not_applicable"}],
        )
        self.assertNotIn("closed_at", data)  # None fields are omitted

    def test_gap_finding_rejects_proposed_change(self):
        with self.assertRaisesRegex(ValueError, "must not carry a claim-mutating proposed change"):
            self._gap_finding(proposed_change={"add_diagnoses": ["L89.154"]})

    def test_gap_finding_must_require_review(self):
        with self.assertRaisesRegex(ValueError, "must require human review"):
            self._gap_finding(requires_human_review=False)

    def test_revenue_finding_serialization_unchanged(self):
        # A revenue_integrity finding (no gap_domain) must serialize with NO clinical keys.
        finding = Finding(
            finding_id="finding-rev",
            rule_id="WC-1",
            rule_package_id="pkg",
            rule_package_version="1.0",
            title="Omission",
            disposition=Disposition.CODING_REVIEW,
            confidence=0.98,
            proposed_change={"add_diagnoses": ["L89.154"]},
            subject_ids=("wound:1",),
            assertion_ids=("AS-001",),
            evidence_ids=("EV-001",),
            contradicting_evidence_ids=(),
            rationale="reason",
            requires_human_review=True,
            submitted_drg="DEMO-292",
            current_drg="DEMO-292",
            simulated_drg="DEMO-290",
            estimated_impact_cents=842000,
            impact_status=ImpactStatus.ESTIMATED,
            grouper_version="demo-0.2-not-for-billing",
        )
        data = finding.to_dict()
        for clinical_key in (
            "gap_domain", "expected_action", "actual_action", "timing_window_days",
            "alert_urgency", "recommended_action", "clinical_impact", "exception_checks",
            "gap_status", "closed_at", "barrier_code",
        ):
            self.assertNotIn(clinical_key, data)
        self.assertIsNone(finding.gap_status)

    def test_gap_status_on_non_gap_finding_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "gap_status is only valid"):
            self._gap_finding(gap_domain=None, gap_status=GapStatus.ROUTED)


# ---------------------------------------------------------------------------
# C2: the SHIPPED clinical_care_gap rule library (rules/wound_care_gaps_v1.json).
# ---------------------------------------------------------------------------

from revenue_integrity.ontology import load_authoritative_wound_care_ontology  # noqa: E402

GAP_PACKAGE_PATH = ROOT / "rules/wound_care_gaps_v1.json"
_POLICY = {
    "max_documents": 200, "max_document_characters": 200000,
    "max_total_document_characters": 1000000, "max_evidence_items": 2000,
    "max_evidence_characters": 2000, "max_total_evidence_characters": 250000,
    "max_entities": 2000, "max_relations": 5000, "max_assertions": 2000,
}


def load_gap_package():
    return json.loads(GAP_PACKAGE_PATH.read_text())


def craft_wound_case(subject_type, concept, attributes):
    """A minimal valid v3 encounter case carrying one wound assertion with ``attributes``.

    When ``subject_type`` is ``WoundAssessment`` the assessment is wired under a parent Wound
    (so ``hasAssessment`` validates) and the assertion is scoped to the assessment entity;
    otherwise the assertion is scoped directly to the typed wound entity.
    """
    onto = load_authoritative_wound_care_ontology()
    entities = [
        {"entity_id": "root:patient", "entity_type": "Patient", "label": "Patient", "properties": {}},
        {"entity_id": "root:encounter", "entity_type": "Encounter", "label": "Encounter", "properties": {}},
    ]
    relations = [{
        "relation_id": "r1", "predicate": "hasEncounter", "source_id": "root:patient",
        "target_id": "root:encounter", "assertion_status": "present",
        "documentation_status": "explicit", "confidence": 1.0, "evidence_ids": [],
    }]
    if subject_type == "WoundAssessment":
        entities.append({"entity_id": "wound:1", "entity_type": "Wound", "label": "Wound", "properties": {}})
        entities.append({"entity_id": "assessment:1", "entity_type": "WoundAssessment", "label": "Assessment", "properties": {}})
        relations.append({"relation_id": "r2", "predicate": "hasWound", "source_id": "root:patient", "target_id": "wound:1", "assertion_status": "present", "documentation_status": "explicit", "confidence": 0.98, "evidence_ids": ["EV-1"]})
        relations.append({"relation_id": "r3", "predicate": "hasAssessment", "source_id": "wound:1", "target_id": "assessment:1", "assertion_status": "present", "documentation_status": "explicit", "confidence": 0.97, "evidence_ids": ["EV-1"]})
        subject_id = "assessment:1"
    else:
        entities.append({"entity_id": "wound:1", "entity_type": subject_type, "label": "Wound", "properties": {}})
        relations.append({"relation_id": "r2", "predicate": "hasWound", "source_id": "root:patient", "target_id": "wound:1", "assertion_status": "present", "documentation_status": "explicit", "confidence": 0.98, "evidence_ids": ["EV-1"]})
        subject_id = "wound:1"
    return {
        "schema_version": "2.0.0", "case_id": "CASE-CRAFTED", "patient_id": "PT-CRAFTED",
        "encounter_id": "ENC-CRAFTED", "admitted_at": "2026-06-01T09:00:00Z",
        "discharged_at": "2026-06-10T15:00:00Z",
        "evidence": [{"evidence_id": "EV-1", "document_id": "DOC-1", "author_role": "physician", "recorded_at": "2026-06-02T10:00:00Z", "text": "wound finding documented"}],
        "ontology": {"ontology_id": onto.ontology_id, "ontology_version": onto.version, "ontology_digest": onto.digest, "entities": entities, "relations": relations},
        "assertions": [{"assertion_id": "AS-1", "subject_id": subject_id, "concept": concept, "status": "present", "documentation_status": "explicit", "confidence": 0.95, "attributes": attributes, "evidence_ids": ["EV-1"], "contradicting_evidence_ids": []}],
        "claim": {"diagnoses": [], "procedures": [], "charges": ["WOUND-CARE-VISIT"]},
        "provenance": {"framework": "mastra", "model_id": "synthetic/test", "agent_id": "x", "extracted_at": "2026-06-10T16:00:00Z", "schema_version": "2.0.0", "extraction_policy": _POLICY},
    }


class ShippedGapPackageTests(unittest.TestCase):
    def test_package_loads_binds_to_authoritative_ontology(self):
        onto = load_authoritative_wound_care_ontology()
        pkg = RulePackage.from_dict(load_gap_package())
        self.assertEqual(pkg.rule_domain, RuleDomain.CLINICAL_CARE_GAP.value)
        self.assertEqual(pkg.status, "approved-for-demo")
        self.assertEqual(pkg.ontology.ontology_id, onto.ontology_id)
        self.assertEqual(pkg.ontology.version, onto.version)
        self.assertEqual(pkg.ontology.digest, onto.digest)

    def test_ships_the_full_library_and_all_rules_obey_the_wall(self):
        pkg = RulePackage.from_dict(load_gap_package())
        self.assertGreaterEqual(len(pkg.rules), 46)
        ids = [rule.rule_id for rule in pkg.rules]
        self.assertEqual(len(ids), len(set(ids)))
        for rule in pkg.rules:
            action = rule.action
            # THE WALL: every gap rule has an empty proposed_change, requires review,
            # and carries a gap_domain + urgency (analytics identify; clinicians decide).
            self.assertEqual(dict(action.proposed_change.values), {})
            self.assertTrue(action.requires_human_review)
            self.assertIsInstance(action.gap_domain, GapDomain)
            self.assertIsInstance(action.alert_urgency, ClinicalUrgency)
            self.assertTrue(action.recommended_action)

    def test_all_subject_scopes_exist_in_authoritative_ontology(self):
        onto = load_authoritative_wound_care_ontology()
        pkg = RulePackage.from_dict(load_gap_package())
        for rule in pkg.rules:
            unknown = set(rule.applies_to.subject_types) - set(onto.classes)
            self.assertFalse(unknown, f"{rule.rule_id} scopes unknown classes {sorted(unknown)}")

    def test_gap_domain_distribution_covers_all_three_kinds(self):
        pkg = RulePackage.from_dict(load_gap_package())
        kinds = {rule.action.gap_domain for rule in pkg.rules}
        self.assertEqual(kinds, set(GapDomain))


class ShippedFlagshipRuleTests(unittest.TestCase):
    """The flagship chronic-wound / stalled-healing rule must fire on the DFU episode."""

    def _engine(self):
        return RuleEngine(load_gap_package(), DeterministicDemoGrouper())

    def _dfu_case(self):
        return EncounterCase.from_dict(load("examples/case_diabetic_foot_ulcer_episode.json"))

    def test_flagship_fires_on_dfu_episode(self):
        findings = self._engine().evaluate(self._dfu_case())
        flagship = next(f for f in findings if f.rule_id == "CG-INF-002")
        self.assertIn(flagship.gap_domain, {GapDomain.DELAYED_ACTION, GapDomain.MISSING_ACTION})
        self.assertIs(flagship.alert_urgency, ClinicalUrgency.URGENT)
        self.assertEqual(flagship.expected_action, "clinician_reassessment")
        self.assertEqual(flagship.timing_window_days, 2)
        self.assertTrue(flagship.requires_human_review)
        self.assertEqual(dict(flagship.proposed_change), {})
        self.assertIsNone(flagship.estimated_impact_cents)
        self.assertIs(flagship.impact_status, ImpactStatus.NOT_APPLICABLE)
        self.assertIs(flagship.gap_status, GapStatus.OPEN)

    def test_flagship_finding_has_no_claim_mutation_across_the_whole_run(self):
        # No clinical_care_gap finding may ever carry a claim mutation.
        findings = self._engine().evaluate(self._dfu_case())
        gaps = [f for f in findings if f.is_clinical_care_gap()]
        self.assertTrue(gaps)
        for gap in gaps:
            self.assertEqual(dict(gap.proposed_change), {})
            self.assertTrue(gap.requires_human_review)


class ShippedSpotCheckRuleTests(unittest.TestCase):
    """Representative rules across groups fire on minimal crafted cases."""

    def _fired(self, case_dict):
        engine = RuleEngine(load_gap_package(), DeterministicDemoGrouper())
        return {f.rule_id for f in engine.evaluate(EncounterCase.from_dict(case_dict))}

    def test_infection_rule_fires(self):
        case = craft_wound_case("PressureInjury", "pressure_injury", {"stage": 3, "exudate_type": "purulent"})
        self.assertIn("CG-INF-001", self._fired(case))

    def test_dfu_rule_fires(self):
        case = craft_wound_case("DiabeticFootUlcer", "diabetic_foot_ulcer", {"probe_to_bone": True})
        self.assertIn("CG-DFU-002", self._fired(case))

    def test_arterial_rule_fires(self):
        case = craft_wound_case("ArterialUlcer", "arterial_ulcer", {"site": "toes", "rest_pain": True, "pulses": "diminished"})
        self.assertIn("CG-ART-001", self._fired(case))

    def test_deterioration_rule_fires(self):
        case = craft_wound_case("WoundAssessment", "wound_assessment", {"fever_or_chills": True, "infection_signs": True})
        fired = self._fired(case)
        self.assertIn("CG-DET-003", fired)
        # Emergent systemic-infection gap.
        engine = RuleEngine(load_gap_package(), DeterministicDemoGrouper())
        det = next(f for f in engine.evaluate(EncounterCase.from_dict(case)) if f.rule_id == "CG-DET-003")
        self.assertIs(det.alert_urgency, ClinicalUrgency.EMERGENT)

    def test_composite_sepsis_rule_fires(self):
        case = craft_wound_case("WoundAssessment", "wound_assessment", {
            "exudate_type": "purulent", "spreading_erythema_cm": 3,
            "temperature_c": 38.5, "wbc_elevated": True,
        })
        self.assertIn("CG-CMP-05", self._fired(case))

    def test_non_matching_case_fires_no_gap(self):
        # A benign healing wound triggers none of the alarm rules.
        case = craft_wound_case("Wound", "wound", {"tissue": "granulation", "exudate_amount": "light"})
        fired = self._fired(case)
        self.assertFalse({rid for rid in fired if rid.startswith("CG-")})


class DeteriorationGrowthWindowTests(unittest.TestCase):
    """Finding #1: CG-DET-001 detects >=20% growth vs the prior assessment at ANY episode age.

    ``size_trend_pct`` is already the change vs the ``compared_with`` prior assessment, so the
    two-week intent is a rolling comparison, not an episode-age cap. The old
    ``days_since_baseline <= 14`` gate silently blinded the deterioration rule after episode day
    14; removing it lets a >=20% growth fire regardless of episode age. CG-INF-002 (stall / no
    reduction) and CG-DET-001 (growth) stay coherent — both may legitimately fire on a wound that
    both failed to reduce and then grew.
    """

    def _engine(self):
        return RuleEngine(load_gap_package(), DeterministicDemoGrouper())

    def _dfu_case(self):
        return EncounterCase.from_dict(load("examples/case_diabetic_foot_ulcer_episode.json"))

    def test_det001_fires_on_day28_growth_beyond_day14(self):
        # Day 28 assessment grew from 4.32 cm^2 (day14) to 8.06 cm^2 (+86.6%) — well past the
        # old 14-day cap. It must now surface as a deterioration gap in its own right.
        findings = self._engine().evaluate(self._dfu_case())
        det = next((f for f in findings if f.rule_id == "CG-DET-001"), None)
        self.assertIsNotNone(det, "CG-DET-001 should fire on the day-28 growth")
        self.assertIs(det.gap_domain, GapDomain.DELAYED_ACTION)
        self.assertEqual(det.expected_action, "reassess_diagnosis_care_plan")
        self.assertEqual(det.timing_window_days, 14)
        self.assertTrue(det.requires_human_review)
        self.assertEqual(dict(det.proposed_change), {})
        self.assertIsNone(det.estimated_impact_cents)
        self.assertIs(det.impact_status, ImpactStatus.NOT_APPLICABLE)

    def test_det001_and_inf002_are_coherent_on_the_episode(self):
        # Growth (DET-001) and stall/no-reduction (INF-002) are distinct signals; both fire on
        # this episode without contradiction, and neither carries a claim mutation.
        findings = self._engine().evaluate(self._dfu_case())
        fired = {f.rule_id for f in findings}
        self.assertIn("CG-DET-001", fired)
        self.assertIn("CG-INF-002", fired)
        for f in findings:
            if f.is_clinical_care_gap():
                self.assertEqual(dict(f.proposed_change), {})
                self.assertTrue(f.requires_human_review)

    def test_det001_does_not_fire_without_20pct_growth(self):
        # A wound assessment whose prior->current area change is under +20% must not fire, even
        # with no episode-age cap. size_trend_pct is a [baseline, current] pair.
        case = craft_wound_case("WoundAssessment", "wound_assessment", {"size_trend_pct": [10.0, 11.0]})
        engine = RuleEngine(load_gap_package(), DeterministicDemoGrouper())
        fired = {f.rule_id for f in engine.evaluate(EncounterCase.from_dict(case))}
        self.assertNotIn("CG-DET-001", fired)

    def test_det001_fires_on_20pct_growth_regardless_of_episode_age(self):
        # A crafted assessment with +25% growth and a large days_since_baseline still fires,
        # proving the episode-age cap is gone.
        case = craft_wound_case(
            "WoundAssessment", "wound_assessment",
            {"size_trend_pct": [10.0, 12.5], "days_since_baseline": 90},
        )
        engine = RuleEngine(load_gap_package(), DeterministicDemoGrouper())
        fired = {f.rule_id for f in engine.evaluate(EncounterCase.from_dict(case))}
        self.assertIn("CG-DET-001", fired)


class PressureInjuryConceptGateTests(unittest.TestCase):
    """Finding #7: PressureInjury rules must not double-gate on a redundant concept-equality.

    The ``applies_to.subject_types: [PressureInjury]`` scope already enforces the type. A
    redundant ``{concept eq pressure_injury}`` condition means the rule silently never fires if
    the extractor emits any variant concept string. These rules must fire on a PressureInjury
    subject regardless of the concept string.
    """

    _PI_RULES = ("CG-INF-001", "CG-PI-001", "CG-PI-002", "CG-PI-003", "CG-PI-004", "CG-PI-005",
                 "CG-CMP-04", "CG-CMP-10")

    def _fired(self, attributes, concept):
        case = craft_wound_case("PressureInjury", concept, attributes)
        engine = RuleEngine(load_gap_package(), DeterministicDemoGrouper())
        return {f.rule_id for f in engine.evaluate(EncounterCase.from_dict(case))}

    def test_no_pi_rule_retains_a_concept_equality_condition(self):
        pkg = load_gap_package()
        by_id = {r["rule_id"]: r for r in pkg["rules"]}

        def concept_conditions(node):
            if not isinstance(node, dict):
                return 0
            if node.get("field") == "concept":
                return 1
            total = 0
            for key in ("all", "any"):
                for child in node.get(key, []):
                    total += concept_conditions(child)
            if "not" in node:
                total += concept_conditions(node["not"])
            return total

        for rule_id in self._PI_RULES:
            self.assertEqual(
                concept_conditions(by_id[rule_id]["when"]), 0,
                f"{rule_id} still carries a redundant concept-equality condition",
            )

    def test_inf001_fires_on_variant_concept_string(self):
        # Variant concept string ('pressure-injury' with a hyphen) must no longer block the rule.
        fired = self._fired({"stage": 3, "exudate_type": "purulent"}, "pressure-injury")
        self.assertIn("CG-INF-001", fired)

    def test_pi_rules_still_fire_on_canonical_concept(self):
        # Regression guard: the canonical concept path still fires each PI rule on its inputs.
        cases = {
            "CG-INF-001": {"stage": 3, "exudate_type": "purulent"},
            "CG-PI-001": {"stage": 1, "nonblanchable_erythema": True},
            "CG-PI-002": {"stage": 2, "wound_bed": "pink", "exudate_type": "serous"},
            "CG-PI-003": {"stage": 3, "tissue": "slough"},
            "CG-PI-004": {"stage": 4, "exposed_structures": True},
            "CG-PI-005": {"site": "sacral_region", "mobility": "limited"},
            "CG-CMP-04": {"stage": 2, "size_trend_pct": [10.0, 11.0], "consecutive_increases": 2, "periwound": "macerated"},
            "CG-CMP-10": {"bed_obscured": True, "depth": "unknown"},
        }
        for rule_id, attrs in cases.items():
            with self.subTest(rule_id=rule_id):
                self.assertIn(rule_id, self._fired(attrs, "pressure_injury"))


class GapExceptionModelingTests(unittest.TestCase):
    """A documented, confirmed exception on a gap finding is representable and validates.

    Downstream tiering/suppression is C3; here we only model + validate that a gap finding
    can carry a typed ``exception_checks`` entry, that a confirmed exception can move the
    gap_status to ``exception``, and that it serializes back to the public contract shape.
    """

    def _flagship_finding(self, **overrides):
        base = dict(
            finding_id="finding-flag", rule_id="CG-INF-002", rule_package_id="wound-care-clinical-care-gap",
            rule_package_version="1.0.0-demo", title="Chronic wound stalled",
            disposition=Disposition.CDI_QUERY, confidence=0.95, proposed_change={},
            subject_ids=("assessment:day14",), assertion_ids=("AS-DFU-DAY14",), evidence_ids=("EV-DFU-DAY14",),
            contradicting_evidence_ids=(), rationale="No size reduction after two weeks of standard care.",
            requires_human_review=True, submitted_drg=None, current_drg="DEMO-DFU-01", simulated_drg="DEMO-DFU-01",
            estimated_impact_cents=None, impact_status=ImpactStatus.NOT_APPLICABLE,
            grouper_version="demo-0.2-not-for-billing", gap_domain=GapDomain.DELAYED_ACTION,
            expected_action="clinician_reassessment", timing_window_days=2,
            alert_urgency=ClinicalUrgency.URGENT, recommended_action="Reassess the wound.",
            clinical_impact="A stalled chronic wound may harbor infection.",
        )
        base.update(overrides)
        return Finding(**base)

    def test_confirmed_exception_is_representable_and_serializes(self):
        finding = self._flagship_finding(
            exception_checks=(
                {"exception_type": ExceptionType.HOSPICE, "evidence_id": "EV-DFU-DAY14", "status": "confirmed"},
            ),
            gap_status=GapStatus.EXCEPTION,
        )
        data = finding.to_dict()
        self.assertEqual(data["gap_status"], "exception")
        self.assertEqual(
            data["exception_checks"],
            [{"exception_type": "hospice", "evidence_id": "EV-DFU-DAY14", "status": "confirmed"}],
        )
        # The wall still holds: no claim mutation, review required.
        self.assertEqual(data["proposed_change"], {})
        self.assertTrue(data["requires_human_review"])

    def test_unconfirmed_exception_leaves_gap_open(self):
        finding = self._flagship_finding(
            exception_checks=(
                {"exception_type": ExceptionType.PATIENT_REFUSAL, "evidence_id": "EV-DFU-DAY14", "status": "not_applicable"},
            ),
        )
        # gap_status defaults to OPEN when not explicitly resolved.
        self.assertIs(finding.gap_status, GapStatus.OPEN)


if __name__ == "__main__":
    unittest.main()
