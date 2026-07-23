import json
from datetime import UTC, datetime
from pathlib import Path
import unittest

from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.models import EncounterCase
from revenue_integrity.models import (
    ClinicalUrgency,
    Disposition,
    ExceptionType,
    Finding,
    GapDomain,
    GapStatus,
    ImpactStatus,
)
from revenue_integrity.review_packet import (
    REVIEW_PACKET_SCHEMA_VERSION,
    build_review_packet,
    summarize_denial_exposure,
    summarize_finding_impact,
    verify_review_packet_hash,
)


ROOT = Path(__file__).parents[1]


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class ReviewPacketTests(unittest.TestCase):
    def setUp(self):
        self.case_payload = load("examples/case_pressure_injury.json")
        self.rules = load("rules/wound_care_v1.json")
        self.case = EncounterCase.from_dict(self.case_payload)
        self.findings = RuleEngine(self.rules, DeterministicDemoGrouper()).evaluate(self.case)

    def packet(self):
        return build_review_packet(
            case=self.case,
            case_payload=self.case_payload,
            rule_package=self.rules,
            findings=self.findings,
            tenant_id="tenant-demo-alpha",
            workspace_id="workspace-revenue-integrity",
            environment="synthetic",
            clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        )

    def test_packet_is_a_self_contained_human_review_handoff(self):
        packet = self.packet()
        self.assertEqual(packet["review_packet_schema_version"], "3.5.0")
        self.assertEqual(packet["tenant"]["tenant_id"], "tenant-demo-alpha")
        self.assertEqual(packet["environment"], "synthetic")
        self.assertEqual(packet["case"]["encounter_id"], self.case.encounter_id)
        self.assertEqual(packet["evidence"][0]["evidence_id"], "EV-001")
        self.assertEqual(packet["findings"][0]["estimated_impact_cents"], 842000)
        self.assertFalse(packet["controls"]["claim_mutation_allowed"])
        self.assertTrue(packet["controls"]["human_review_required"])
        self.assertIn("route_to_coding", packet["controls"]["permitted_actions"])
        self.assertTrue(verify_review_packet_hash(packet))

    def test_evidence_carries_deterministic_source_locator_deep_link(self):
        packet = self.packet()
        evidence = packet["evidence"][0]
        locator = evidence["source_locator"]
        # Clinical-note excerpt: deep-link derived purely from validated substring grounding.
        self.assertEqual(locator["kind"], "clinical_note_excerpt")
        self.assertEqual(locator["document_id"], evidence["document_id"])
        # Offsets describe the surfaced excerpt window and match its exact length.
        self.assertEqual(locator["char_start"], 0)
        self.assertEqual(locator["char_end"], len(evidence["text"]))
        self.assertEqual(locator["length"], len(evidence["text"]))
        # Content-addressing hash lets a viewer locate the excerpt inside the document.
        import hashlib
        self.assertEqual(
            locator["excerpt_sha256"],
            hashlib.sha256(evidence["text"].encode("utf-8")).hexdigest(),
        )
        # Deterministic: regenerating the packet yields the identical locator.
        self.assertEqual(locator, self.packet()["evidence"][0]["source_locator"])

    def test_source_locator_is_hash_covered(self):
        packet = self.packet()
        tampered = json.loads(json.dumps(packet))
        tampered["evidence"][0]["source_locator"]["char_end"] = 999
        self.assertFalse(verify_review_packet_hash(tampered))

    def test_structured_adapter_source_locator_is_preserved_as_deep_link(self):
        from revenue_integrity.review_packet import _evidence_with_source_locator

        surfaced = _evidence_with_source_locator([
            {
                "evidence_id": "EV-STRUCT",
                "document_id": "encounters.csv#7",
                "author_role": "system",
                "recorded_at": "2026-06-01T14:30:00Z",
                "text": "stage 4",
                "source_locator": {
                    "adapter_id": "clinic_alpha_wound_care",
                    "adapter_version": "1.0.0",
                    "resource": "encounters.csv",
                    "path": "encounters.csv",
                    "row_number": 7,
                    "source_record_id": "REC-7",
                    "field_names": ["wound_stage", "poa_flag"],
                },
            }
        ])
        locator = surfaced[0]["source_locator"]
        # Adapter evidence keeps its precise row-level address, re-tagged for the UI.
        self.assertEqual(locator["kind"], "structured_source_record")
        self.assertEqual(locator["row_number"], 7)
        self.assertEqual(locator["path"], "encounters.csv")
        self.assertNotIn("excerpt_sha256", locator)

    def test_existing_clinical_note_excerpt_locator_is_preserved(self):
        from revenue_integrity.review_packet import _evidence_with_source_locator

        # A pre-computed excerpt locator (real char offsets, no adapter "path") must be
        # kept intact — the synthesizer must NOT clobber it to a 0..len span.
        import hashlib

        text = "unstageable pressure injury of the sacrum"
        existing = {
            "kind": "clinical_note_excerpt",
            "document_id": "DOC-9",
            "char_start": 128,
            "char_end": 128 + len(text),
            "length": len(text),
            "excerpt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        surfaced = _evidence_with_source_locator([
            {
                "evidence_id": "EV-EXCERPT",
                "document_id": "DOC-9",
                "author_role": "md",
                "recorded_at": "2026-06-01T14:30:00Z",
                "text": text,
                "source_locator": dict(existing),
            }
        ])
        locator = surfaced[0]["source_locator"]
        # Preserved byte-for-byte — offsets are NOT collapsed to 0..len(text).
        self.assertEqual(locator, existing)
        self.assertEqual(locator["char_start"], 128)
        self.assertEqual(locator["char_end"], 128 + len(text))

    def test_source_locator_malformed_evidence_missing_text_raises_domain_error(self):
        from revenue_integrity.review_packet import _evidence_with_source_locator

        # Fail closed with a clear domain error (ValueError), never a bare KeyError, and
        # the message must name the offending evidence item so the operator can locate it.
        with self.assertRaises(ValueError) as ctx:
            _evidence_with_source_locator([
                {"evidence_id": "EV-BAD", "document_id": "DOC", "author_role": "md", "recorded_at": "x"}
            ])
        self.assertNotIsInstance(ctx.exception, KeyError)
        self.assertIn("EV-BAD", str(ctx.exception))

    def test_impact_summary_rolls_up_findings_deterministically(self):
        summary = self.packet()["impact_summary"]
        # The demo encounter carries a single estimated +$8,420.00 opportunity.
        self.assertEqual(summary["positive_opportunity_cents"], 842000)
        self.assertEqual(summary["at_risk_cents"], 0)
        self.assertEqual(summary["net_estimated_impact_cents"], 842000)
        self.assertEqual(summary["estimated_finding_count"], 1)
        self.assertEqual(summary["total_findings"], len(self.findings))
        self.assertEqual(summary["currency"], "USD")
        self.assertIn("not-for-billing", summary["basis"])

    def test_finding_carries_a_hash_covered_derivation_trace(self):
        packet = self.packet()
        derivation = packet["findings"][0]["derivation"]
        # Reviewer explainability: current + simulated grouping steps are present and ordered.
        self.assertEqual([step["step"] for step in derivation["current"]],
                         ["severity_resolution", "tier_selection", "pricing"])
        self.assertEqual([step["step"] for step in derivation["simulated"]],
                         ["severity_resolution", "tier_selection", "pricing"])
        self.assertEqual(derivation["simulated"][1]["value"], "DEMO-290")
        # Tampering with the trace breaks the packet hash.
        tampered = json.loads(json.dumps(packet))
        tampered["findings"][0]["derivation"]["simulated"][1]["value"] = "DEMO-999"
        self.assertFalse(verify_review_packet_hash(tampered))

    def test_findings_carry_narrative_and_charge_line_refs(self):
        packet = self.packet()
        finding = packet["findings"][0]
        # charge_line_refs is present and read-only (empty for the demo, which has no charge lines).
        self.assertIn("charge_line_refs", finding)
        self.assertEqual(finding["charge_line_refs"], [])
        # narrative is a non-empty deterministic sentence restating existing finding fields.
        self.assertIn("narrative", finding)
        self.assertIsInstance(finding["narrative"], str)
        self.assertTrue(finding["narrative"])
        self.assertIn(self.case.case_id, finding["narrative"])
        self.assertIn(finding["rule_id"], finding["narrative"])
        # Deterministic: regenerating the packet yields the identical narrative.
        self.assertEqual(finding["narrative"], self.packet()["findings"][0]["narrative"])

    def test_narrative_is_hash_covered(self):
        packet = self.packet()
        tampered = json.loads(json.dumps(packet))
        tampered["findings"][0]["narrative"] = "tampered narrative"
        self.assertFalse(verify_review_packet_hash(tampered))

    def test_charge_line_refs_are_hash_covered(self):
        packet = self.packet()
        tampered = json.loads(json.dumps(packet))
        tampered["findings"][0]["charge_line_refs"] = ["LINE-tampered"]
        self.assertFalse(verify_review_packet_hash(tampered))

    def test_denial_summary_is_zeroed_without_financial_context(self):
        packet = self.packet()
        summary = packet["denial_summary"]
        # The demo case carries no financial snapshot, so denial exposure is all-zero.
        self.assertEqual(summary["denied_amount_cents"], 0)
        self.assertEqual(summary["denial_count"], 0)
        self.assertEqual(summary["at_risk_line_count"], 0)
        self.assertEqual(summary["at_risk_line_ids"], [])
        self.assertEqual(summary["currency"], "USD")

    def test_denial_summary_is_hash_covered(self):
        packet = self.packet()
        tampered = json.loads(json.dumps(packet))
        tampered["denial_summary"]["denied_amount_cents"] = 123456
        self.assertFalse(verify_review_packet_hash(tampered))

    def test_summarize_denial_exposure_reads_financial_snapshot(self):
        from revenue_integrity.financial import ClaimLine, Denial, FinancialSnapshot
        from dataclasses import replace

        financial = FinancialSnapshot(
            schema_version="1.0.0", payer_id="payer-x", claim_id="claim-x",
            claim_lines=(
                ClaimLine(line_id="L1", code="C1", code_system="cpt", units=1, charged_amount_cents=1000),
                ClaimLine(line_id="L2", code="C2", code_system="cpt", units=1, charged_amount_cents=2000),
            ),
            denials=(
                Denial(denial_id="D1", line_ids=("L1",), reason_code="CO-50", status="open", amount_cents=1000),
                Denial(denial_id="D2", line_ids=("L2", "L1"), reason_code="CO-97", status="open", amount_cents=500),
            ),
        )
        case = replace(self.case, financial=financial)
        summary = summarize_denial_exposure(case)
        self.assertEqual(summary["denied_amount_cents"], 1500)
        self.assertEqual(summary["denial_count"], 2)
        self.assertEqual(summary["at_risk_line_count"], 2)
        self.assertEqual(summary["at_risk_line_ids"], ["L1", "L2"])

    def test_summarize_denial_exposure_is_zeroed_when_absent(self):
        summary = summarize_denial_exposure(self.case)
        self.assertEqual(summary["denied_amount_cents"], 0)
        self.assertEqual(summary["denial_count"], 0)
        self.assertEqual(summary["at_risk_line_ids"], [])

    def test_impact_summary_is_hash_covered(self):
        packet = self.packet()
        tampered = json.loads(json.dumps(packet))
        tampered["impact_summary"]["positive_opportunity_cents"] = 999999999
        self.assertFalse(verify_review_packet_hash(tampered))

    def test_summarize_finding_impact_handles_mixed_signs_and_statuses(self):
        def finding(finding_id, cents, status, disposition=Disposition.CODING_REVIEW):
            return Finding(
                finding_id=finding_id, rule_id="R", rule_package_id="P", rule_package_version="1",
                title="t", disposition=disposition, confidence=0.9, proposed_change={},
                subject_ids=(), assertion_ids=(), evidence_ids=(), contradicting_evidence_ids=(),
                rationale="r", requires_human_review=True, submitted_drg="A",
                current_drg="A", simulated_drg="B", estimated_impact_cents=cents,
                impact_status=status, grouper_version="demo-x",
            )

        summary = summarize_finding_impact([
            finding("f1", 500_00, ImpactStatus.ESTIMATED),
            finding("f2", -200_00, ImpactStatus.ESTIMATED, Disposition.COMPLIANCE_REVIEW),
            finding("f3", None, ImpactStatus.UNAVAILABLE),
            finding("f4", None, ImpactStatus.NOT_APPLICABLE, Disposition.NO_OPPORTUNITY),
        ])
        self.assertEqual(summary["positive_opportunity_cents"], 500_00)
        self.assertEqual(summary["at_risk_cents"], 200_00)
        self.assertEqual(summary["net_estimated_impact_cents"], 300_00)
        self.assertEqual(summary["estimated_finding_count"], 2)
        self.assertEqual(summary["unavailable_impact_count"], 1)
        self.assertEqual(summary["not_applicable_impact_count"], 1)
        self.assertEqual(summary["findings_requiring_review"], 4)
        self.assertEqual(summary["findings_by_disposition"]["coding_review"], 2)
        self.assertEqual(summary["findings_by_disposition"]["compliance_review"], 1)

    def test_summarize_finding_impact_empty_is_zeroed(self):
        summary = summarize_finding_impact([])
        self.assertEqual(summary["net_estimated_impact_cents"], 0)
        self.assertEqual(summary["positive_opportunity_cents"], 0)
        self.assertEqual(summary["at_risk_cents"], 0)
        self.assertEqual(summary["total_findings"], 0)

    def test_packet_hash_covers_scope_controls_and_findings(self):
        packet = self.packet()
        for path, value in (
            (("tenant", "tenant_id"), "tenant-other"),
            (("controls", "claim_mutation_allowed"), True),
            (("findings", 0, "confidence"), 0.01),
        ):
            tampered = json.loads(json.dumps(packet))
            target = tampered
            for segment in path[:-1]:
                target = target[segment]
            target[path[-1]] = value
            self.assertFalse(verify_review_packet_hash(tampered))

    def test_packet_is_reproducible_with_a_fixed_clock(self):
        self.assertEqual(self.packet(), self.packet())

    def test_packet_identity_is_tenant_scoped(self):
        other = build_review_packet(
            case=self.case, case_payload=self.case_payload, rule_package=self.rules,
            findings=self.findings, tenant_id="tenant-other",
            workspace_id="workspace-revenue-integrity", environment="synthetic",
            clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        )
        self.assertNotEqual(other["packet_id"], self.packet()["packet_id"])

    # ---- clinical_care_gap wire-format extension (schema 3.5.0) ----

    GAP_FIELD_NAMES = (
        "gap_domain",
        "expected_action",
        "actual_action",
        "timing_window_days",
        "alert_urgency",
        "recommended_action",
        "clinical_impact",
        "exception_checks",
        "gap_status",
        "closed_at",
        "barrier_code",
    )

    def _gap_finding(self):
        # A fully-populated clinical_care_gap finding: carries clinical action fields, no claim
        # mutation, and requires human review (the domain wall, enforced in Finding.__post_init__).
        return Finding(
            finding_id="FIND-GAP-001",
            rule_id="wound-gap-offloading-overdue",
            rule_package_id="wound_care_gaps",
            rule_package_version="1.0.0",
            title="Offloading order overdue for stage-3 pressure injury",
            disposition=Disposition.NO_OPPORTUNITY,
            confidence=0.91,
            proposed_change={},
            subject_ids=("ENT-WOUND-1",),
            assertion_ids=("A-1",),
            evidence_ids=("EV-001",),
            contradicting_evidence_ids=(),
            rationale="No offloading order documented within the expected window.",
            requires_human_review=True,
            submitted_drg=None,
            current_drg="DEMO-000",
            simulated_drg="DEMO-000",
            estimated_impact_cents=None,
            impact_status=ImpactStatus.NOT_APPLICABLE,
            grouper_version="demo-grouper-not-for-billing",
            gap_domain=GapDomain.DELAYED_ACTION,
            expected_action="Document pressure-offloading order",
            actual_action="No offloading order found",
            timing_window_days=2,
            alert_urgency=ClinicalUrgency.URGENT,
            recommended_action="Place offloading order and document",
            clinical_impact="Delayed offloading increases pressure-injury progression risk.",
            exception_checks=(
                {"exception_type": ExceptionType.PATIENT_REFUSAL, "evidence_id": "EV-001", "status": "not_applicable"},
            ),
            gap_status=GapStatus.OPEN,
            closed_at=None,
            barrier_code="workflow_backlog",
        )

    def test_clinical_care_gap_finding_round_trips_through_packet(self):
        finding = self._gap_finding()
        packet = build_review_packet(
            case=self.case,
            case_payload=self.case_payload,
            rule_package=self.rules,
            findings=[finding],
            tenant_id="tenant-demo-alpha",
            workspace_id="workspace-revenue-integrity",
            environment="synthetic",
            clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        )
        self.assertEqual(packet["review_packet_schema_version"], "3.5.0")
        emitted = packet["findings"][0]
        # Every gap field the Finding populated survives the packet round-trip with its value.
        self.assertEqual(emitted["gap_domain"], "delayed_action")
        self.assertEqual(emitted["expected_action"], "Document pressure-offloading order")
        self.assertEqual(emitted["actual_action"], "No offloading order found")
        self.assertEqual(emitted["timing_window_days"], 2)
        self.assertEqual(emitted["alert_urgency"], "urgent")
        self.assertEqual(emitted["recommended_action"], "Place offloading order and document")
        self.assertIn("progression risk", emitted["clinical_impact"])
        self.assertEqual(emitted["exception_checks"], [
            {"exception_type": "patient_refusal", "evidence_id": "EV-001", "status": "not_applicable"},
        ])
        self.assertEqual(emitted["gap_status"], "open")
        self.assertEqual(emitted["barrier_code"], "workflow_backlog")
        # closed_at is None on an open gap, so it is not emitted (optional field, omitted when unset).
        self.assertNotIn("closed_at", emitted)
        # The gap finding is hash-covered like any other finding.
        self.assertTrue(verify_review_packet_hash(packet))
        # A gap finding opens the care-gap reviewer actions without dropping the RI actions.
        self.assertIn("route_to_care_team", packet["controls"]["permitted_actions"])
        self.assertIn("close_gap_with_evidence", packet["controls"]["permitted_actions"])
        self.assertIn("route_to_coding", packet["controls"]["permitted_actions"])

    def test_gap_field_names_match_the_schema_finding_properties_exactly(self):
        # additionalProperties:false means the schema's finding properties must be a superset of
        # every key a gap Finding can emit; a drifted key would be rejected by the wire contract.
        schema = load("schemas/review_packet.schema.json")
        finding_props = set(schema["$defs"]["finding"]["properties"])
        emitted_gap_keys = set(self._gap_finding().to_dict()) - set(
            Finding(
                finding_id="FIND-RI", rule_id="r", rule_package_id="p", rule_package_version="1",
                title="t", disposition=Disposition.CODING_REVIEW, confidence=0.9, proposed_change={},
                subject_ids=(), assertion_ids=(), evidence_ids=(), contradicting_evidence_ids=(),
                rationale="r", requires_human_review=True, submitted_drg="A",
                current_drg="A", simulated_drg="B", estimated_impact_cents=100,
                impact_status=ImpactStatus.ESTIMATED, grouper_version="demo-x",
            ).to_dict()
        )
        # The gap keys the Finding actually emits are exactly the ones we documented, all present.
        self.assertTrue(emitted_gap_keys.issubset(finding_props), sorted(emitted_gap_keys - finding_props))
        for name in self.GAP_FIELD_NAMES:
            self.assertIn(name, finding_props)

    def test_revenue_integrity_finding_packet_has_no_gap_keys(self):
        # The demo case is pure revenue_integrity: its packet finding must not carry any gap key,
        # so revenue_integrity packet content is byte-for-byte unchanged aside from the version.
        packet = self.packet()
        finding = packet["findings"][0]
        for name in self.GAP_FIELD_NAMES:
            self.assertNotIn(name, finding)
        # And the RI permitted_actions list is exactly the historical five (no care-gap actions).
        self.assertEqual(
            packet["controls"]["permitted_actions"],
            ["route_to_coding", "route_to_cdi", "route_to_charge_review", "route_to_compliance", "dismiss_with_reason"],
        )

    def test_schema_version_constant_is_bumped(self):
        self.assertEqual(REVIEW_PACKET_SCHEMA_VERSION, "3.5.0")

    def test_packet_rejects_invalid_environment_and_mismatched_payload(self):
        with self.assertRaisesRegex(ValueError, "unsupported review-packet environment"):
            build_review_packet(
                case=self.case,
                case_payload=self.case_payload,
                rule_package=self.rules,
                findings=self.findings,
                environment="customer-demo",
                tenant_id="tenant-demo-alpha",
                workspace_id="workspace-revenue-integrity",
            )
        changed = dict(self.case_payload)
        changed["case_id"] = "different-case"
        with self.assertRaisesRegex(ValueError, "does not match validated case"):
            build_review_packet(
                case=self.case,
                case_payload=changed,
                rule_package=self.rules,
                findings=self.findings,
                tenant_id="tenant-demo-alpha",
                workspace_id="workspace-revenue-integrity",
            )
