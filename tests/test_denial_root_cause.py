import json
import unittest
from pathlib import Path

from revenue_integrity.denial_root_cause import (
    DenialReasonCodeTable,
    denial_root_cause_findings,
    load_denial_reason_code_table,
)
from revenue_integrity.models import Disposition, EncounterCase, ImpactStatus

ROOT = Path(__file__).parents[1]


def load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _case_with_financial(denials: list[dict]) -> EncounterCase:
    payload = load("examples/case_pressure_injury_v2.json")
    line_ids = sorted({lid for d in denials for lid in d["line_ids"]})
    payload["financial"] = {
        "schema_version": "1.0.0",
        "payer_id": "payer-1",
        "claim_id": "claim-1",
        "claim_lines": [
            {
                "line_id": lid,
                "code": "97597",
                "code_system": "CPT",
                "units": 1,
                "charged_amount_cents": 10000,
            }
            for lid in line_ids
        ],
        "denials": denials,
    }
    return EncounterCase.from_dict(payload)


class DenialReasonCodeTableTests(unittest.TestCase):
    def test_loads_governed_table(self):
        table = load_denial_reason_code_table()
        self.assertEqual(table.table_id, "denial-reason-codes")
        self.assertEqual(table.version, "1.0.0")
        self.assertEqual(table.status, "approved-for-demo")
        self.assertEqual(len(table.digest), 64)

    def test_lookup_known_and_unknown(self):
        table = load_denial_reason_code_table()
        entry = table.lookup("CARC", "50")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.root_cause, "medical_necessity_not_established")
        self.assertEqual(entry.disposition, Disposition.CDI_QUERY)
        self.assertIsNone(table.lookup("CARC", "999"))
        self.assertIsNone(table.lookup("BOGUS", "50"))

    def test_from_dict_rejects_unknown_disposition(self):
        with self.assertRaises(ValueError):
            DenialReasonCodeTable.from_dict({
                "table_id": "t", "version": "1", "status": "approved-for-demo",
                "carc": {"1": {"root_cause": "x", "disposition": "not_a_real_disposition"}},
                "rarc": {},
            })


class DenialRootCauseFindingTests(unittest.TestCase):
    def test_known_carc_produces_expected_finding(self):
        case = _case_with_financial([
            {"denial_id": "d1", "line_ids": ["line-1"], "reason_code": "CO-50", "status": "open", "amount_cents": 10000},
        ])
        findings = denial_root_cause_findings(case)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule_id, "SYSTEM-DENIAL-ROOTCAUSE")
        self.assertEqual(f.rule_package_id, "deterministic-system-checks")
        self.assertEqual(f.disposition, Disposition.CDI_QUERY)
        self.assertEqual(f.charge_line_refs, ("line-1",))
        self.assertEqual(dict(f.proposed_change), {})
        self.assertEqual(f.impact_status, ImpactStatus.NOT_APPLICABLE)
        self.assertIsNone(f.estimated_impact_cents)
        self.assertTrue(f.requires_human_review)
        self.assertEqual(f.derivation["root_cause"], ["medical_necessity_not_established"])
        self.assertEqual(f.derivation["reason_code_system"], ["CARC"])

    def test_known_rarc_produces_expected_finding(self):
        case = _case_with_financial([
            {"denial_id": "d2", "line_ids": ["line-2"], "reason_code": "N657", "status": "open"},
        ])
        findings = denial_root_cause_findings(case)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].disposition, Disposition.CODING_REVIEW)
        self.assertEqual(findings[0].charge_line_refs, ("line-2",))
        self.assertEqual(findings[0].derivation["reason_code"], ["N657"])

    def test_compound_reason_code_emits_one_finding_per_token(self):
        case = _case_with_financial([
            {"denial_id": "d3", "line_ids": ["line-1", "line-2"], "reason_code": "CARC:16;RARC:N657", "status": "open"},
        ])
        findings = denial_root_cause_findings(case)
        self.assertEqual(len(findings), 2)
        dispositions = {f.derivation["reason_code"][0]: f.disposition for f in findings}
        self.assertEqual(dispositions["16"], Disposition.CHARGE_REVIEW)
        self.assertEqual(dispositions["N657"], Disposition.CODING_REVIEW)
        for f in findings:
            self.assertEqual(f.charge_line_refs, ("line-1", "line-2"))

    def test_unknown_code_produces_unclassified_finding_never_dropped(self):
        case = _case_with_financial([
            {"denial_id": "d4", "line_ids": ["line-1"], "reason_code": "CARC:999", "status": "open"},
        ])
        findings = denial_root_cause_findings(case)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.derivation["root_cause"], ["unclassified"])
        self.assertEqual(f.disposition, Disposition.COMPLIANCE_REVIEW)
        self.assertEqual(f.charge_line_refs, ("line-1",))

    def test_no_financial_no_findings(self):
        case = EncounterCase.from_dict(load("examples/case_pressure_injury_v2.json"))
        self.assertIsNone(case.financial)
        self.assertEqual(denial_root_cause_findings(case), [])

    def test_deterministic_finding_ids(self):
        denials = [{"denial_id": "d1", "line_ids": ["line-1"], "reason_code": "CO-50", "status": "open"}]
        a = denial_root_cause_findings(_case_with_financial(denials))
        b = denial_root_cause_findings(_case_with_financial(denials))
        self.assertEqual([f.finding_id for f in a], [f.finding_id for f in b])

    def test_denial_finding_carries_denial_event_subject_lineage(self):
        from revenue_integrity.denial_root_cause import (
            DENIAL_EVENT_SUBJECT_TYPE,
            DENIAL_ONTOLOGY_ID,
            DENIAL_ONTOLOGY_VERSION,
            _denial_event_subject_id,
        )
        from revenue_integrity.financial import Denial
        from revenue_integrity.ontology import load_builtin_ontology

        case = _case_with_financial([
            {"denial_id": "d1", "line_ids": ["line-1"], "reason_code": "CO-50", "status": "open", "amount_cents": 10000},
        ])
        findings = denial_root_cause_findings(case)
        self.assertEqual(len(findings), 1)
        f = findings[0]

        expected_subject = _denial_event_subject_id(
            case.case_id, Denial(denial_id="d1", line_ids=("line-1",), reason_code="CO-50", status="open", amount_cents=10000)
        )
        # The finding's subject lineage points at a governed DenialEvent subject.
        self.assertEqual(f.subject_ids, (expected_subject,))
        self.assertEqual(f.derivation["denial_subject_id"], [expected_subject])
        self.assertEqual(f.derivation["denial_subject_type"], [DENIAL_EVENT_SUBJECT_TYPE])
        self.assertEqual(f.derivation["denial_ontology_id"], [DENIAL_ONTOLOGY_ID])
        self.assertEqual(f.derivation["denial_ontology_version"], [DENIAL_ONTOLOGY_VERSION])

        # The referenced subject type is a real, concrete class in the governed ontology.
        definition = load_builtin_ontology(DENIAL_ONTOLOGY_ID, DENIAL_ONTOLOGY_VERSION)
        self.assertIn(DENIAL_EVENT_SUBJECT_TYPE, definition.classes)
        self.assertFalse(definition.classes[DENIAL_EVENT_SUBJECT_TYPE].abstract)

    def test_denial_event_subject_id_is_deterministic_and_per_denial(self):
        case = _case_with_financial([
            {"denial_id": "d1", "line_ids": ["line-1"], "reason_code": "CARC:16;RARC:N657", "status": "open"},
            {"denial_id": "d2", "line_ids": ["line-2"], "reason_code": "CO-50", "status": "open"},
        ])
        findings = denial_root_cause_findings(case)
        by_denial: dict[str, set[str]] = {}
        for f in findings:
            by_denial.setdefault(f.derivation["denial_ids"][0], set()).update(f.subject_ids)
        # All findings for the same denial share one DenialEvent subject; different
        # denials get distinct subjects; a subject is always present.
        self.assertEqual(len(by_denial["d1"]), 1)
        self.assertEqual(len(by_denial["d2"]), 1)
        self.assertNotEqual(by_denial["d1"], by_denial["d2"])

    def test_engine_emits_denial_findings(self):
        from revenue_integrity.engine import RuleEngine
        from revenue_integrity.grouper import DeterministicDemoGrouper

        case = _case_with_financial([
            {"denial_id": "d1", "line_ids": ["line-1"], "reason_code": "CO-50", "status": "open"},
        ])
        rules = load("rules/wound_care_v2.json")
        findings = RuleEngine(rules, DeterministicDemoGrouper()).evaluate(case)
        rootcause = [f for f in findings if f.rule_id == "SYSTEM-DENIAL-ROOTCAUSE"]
        self.assertEqual(len(rootcause), 1)
        self.assertEqual(rootcause[0].disposition, Disposition.CDI_QUERY)


if __name__ == "__main__":
    unittest.main()
