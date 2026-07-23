import unittest

from revenue_integrity.narrative import render_finding_narrative, render_gap_finding_narrative


def _finding(**overrides):
    base = {
        "finding_id": "finding-abc",
        "rule_id": "WC-UPCODE-001",
        "title": "Stage 4 pressure injury supports a higher-severity DRG",
        "disposition": "coding_review",
        "requires_human_review": True,
        "current_drg": "DEMO-291",
        "simulated_drg": "DEMO-290",
        "estimated_impact_cents": 842000,
        "impact_status": "estimated",
    }
    base.update(overrides)
    return base


class RenderFindingNarrativeTests(unittest.TestCase):
    def test_positive_estimated_upside_narrative(self):
        text = render_finding_narrative(_finding(), {"case_id": "case-demo-001"})
        self.assertIn("case-demo-001", text)
        self.assertIn("WC-UPCODE-001", text)
        self.assertIn("DEMO-291 to DEMO-290", text)
        self.assertIn("upside of $8,420.00", text)
        self.assertIn("Requires human review", text)
        self.assertIn("coding review", text)
        self.assertNotIn("not for billing", text.split("(synthetic")[0])

    def test_deterministic_for_same_inputs(self):
        a = render_finding_narrative(_finding(), {"case_id": "case-x"})
        b = render_finding_narrative(_finding(), {"case_id": "case-x"})
        self.assertEqual(a, b)

    def test_negative_downside_exposure_rendered_as_downside(self):
        text = render_finding_narrative(
            _finding(estimated_impact_cents=-15050, disposition="compliance_review"),
            {"case_id": "case-y"},
        )
        self.assertIn("downside exposure of -$150.50", text)
        self.assertIn("compliance review", text)

    def test_unavailable_impact_status(self):
        text = render_finding_narrative(
            _finding(estimated_impact_cents=None, impact_status="unavailable"),
            {"case_id": "case-z"},
        )
        self.assertIn("unavailable", text)

    def test_not_applicable_impact_status(self):
        text = render_finding_narrative(
            _finding(estimated_impact_cents=None, impact_status="not_applicable"),
            {"case_id": "case-z"},
        )
        self.assertIn("No estimated impact applies", text)

    def test_same_drg_reports_no_move(self):
        text = render_finding_narrative(
            _finding(current_drg="DEMO-291", simulated_drg="DEMO-291"),
            {"case_id": "case-z"},
        )
        self.assertIn("DRG remains DEMO-291", text)

    def test_no_human_review_uses_suggested_routing(self):
        text = render_finding_narrative(
            _finding(requires_human_review=False, disposition="no_opportunity"),
            {"case_id": "case-z"},
        )
        self.assertIn("Suggested routing", text)
        self.assertNotIn("Requires human review", text)

    def test_missing_case_id_falls_back(self):
        text = render_finding_narrative(_finding(), {})
        self.assertIn("the encounter", text)


def _gap_finding(**overrides):
    base = {
        "finding_id": "finding-gap",
        "rule_id": "CG-INF-002",
        "title": "Chronic wound with no size reduction after two weeks needs clinician reassessment",
        "disposition": "cdi_query",
        "requires_human_review": True,
        "current_drg": "DEMO-DFU-01",
        "simulated_drg": "DEMO-DFU-01",
        "estimated_impact_cents": None,
        "impact_status": "not_applicable",
        "gap_domain": "delayed_action",
        "expected_action": "clinician_reassessment",
        "timing_window_days": 2,
        "alert_urgency": "urgent",
        "recommended_action": "Reassess the wound and evaluate for infection.",
        "clinical_impact": "A stalled chronic wound may harbor infection.",
        "evidence_ids": ["EV-DFU-DAY14"],
        "gap_status": "open",
    }
    base.update(overrides)
    return base


class RenderGapFindingNarrativeTests(unittest.TestCase):
    def test_gap_narrative_surfaces_rule_action_window_and_status(self):
        text = render_gap_finding_narrative(_gap_finding(), {"case_id": "CASE-DFU-EPISODE-001"})
        self.assertIn("CASE-DFU-EPISODE-001", text)
        self.assertIn("CG-INF-002", text)
        self.assertIn("delayed action", text)
        self.assertIn("EV-DFU-DAY14", text)
        self.assertIn("clinician reassessment within 2 days", text)
        self.assertIn("urgent", text)
        self.assertIn("Resolution status: open", text)
        # Analytics identify; clinicians decide — never a claim change.
        self.assertIn("no claim change", text)

    def test_render_dispatches_gap_findings_to_gap_narrative(self):
        text = render_finding_narrative(_gap_finding(), {"case_id": "CASE-X"})
        self.assertIn("care-gap rule CG-INF-002", text)
        # A gap finding never renders a DRG-move / dollar-impact clause.
        self.assertNotIn("DRG would move", text)
        self.assertNotIn("upside", text)

    def test_gap_narrative_reports_confirmed_exception(self):
        finding = _gap_finding(
            gap_status="exception",
            exception_checks=[{"exception_type": "hospice", "evidence_id": "EV-DFU-DAY14", "status": "confirmed"}],
        )
        text = render_gap_finding_narrative(finding, {"case_id": "CASE-X"})
        self.assertIn("Documented exception confirmed: hospice", text)
        self.assertIn("Resolution status: exception", text)

    def test_gap_narrative_reports_checked_but_unconfirmed_exception(self):
        finding = _gap_finding(
            exception_checks=[{"exception_type": "patient_refusal", "evidence_id": "EV-1", "status": "not_applicable"}],
        )
        text = render_gap_finding_narrative(finding, {"case_id": "CASE-X"})
        self.assertIn("Exceptions checked (none confirmed): patient refusal", text)

    def test_gap_narrative_is_deterministic(self):
        a = render_gap_finding_narrative(_gap_finding(), {"case_id": "CASE-X"})
        b = render_gap_finding_narrative(_gap_finding(), {"case_id": "CASE-X"})
        self.assertEqual(a, b)


class RevenueNarrativeUnchangedTests(unittest.TestCase):
    def test_revenue_finding_narrative_is_byte_identical(self):
        # A revenue_integrity finding (no gap_domain) renders exactly as before.
        expected = (
            "On case-demo-001, rule WC-UPCODE-001 flagged: "
            "Stage 4 pressure injury supports a higher-severity DRG. "
            "DRG would move from DEMO-291 to DEMO-290. "
            "Estimated upside of $8,420.00 (synthetic demo grouper, not for billing). "
            "Requires human review; route to coding review."
        )
        self.assertEqual(render_finding_narrative(_finding(), {"case_id": "case-demo-001"}), expected)


if __name__ == "__main__":
    unittest.main()
