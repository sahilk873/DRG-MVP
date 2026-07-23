"""Deterministic plain-language narratives for review-packet findings.

``render_finding_narrative`` turns an already-serialized finding dict (one produced by
``Finding.to_dict()``) into a single, reproducible sentence a reviewer can read at a
glance. It is purely presentational: it restates fields the deterministic engine already
produced (rule, disposition, DRG delta, estimated impact, review requirement) and never
introduces a new authoritative field, mutates a claim, or consults a language model.
Given the same finding dict and case context, it always yields the same string.
"""
from __future__ import annotations

from typing import Any, Mapping


_DISPOSITION_PHRASES = {
    "coding_review": "coding review",
    "cdi_query": "a CDI query",
    "charge_review": "charge review",
    "compliance_review": "compliance review",
    "insufficient_evidence": "no action (insufficient evidence)",
    "no_opportunity": "no action (no opportunity)",
}


def _format_cents(cents: int) -> str:
    """Deterministic, sign-aware USD rendering of an integer-cent amount."""
    sign = "-" if cents < 0 else ""
    magnitude = abs(int(cents))
    dollars, remainder = divmod(magnitude, 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


_URGENCY_PHRASES = {
    "routine": "routine",
    "same_day": "same-day",
    "urgent": "urgent",
    "emergent": "emergent",
}


def _format_window(days: Any) -> str:
    """Deterministic rendering of a timing window in days (accepts int or float)."""
    if isinstance(days, bool) or not isinstance(days, (int, float)):
        return ""
    if float(days) == int(days):
        days = int(days)
    unit = "day" if days == 1 else "days"
    return f"{days} {unit}"


def render_gap_finding_narrative(
    finding_dict: Mapping[str, Any],
    case_context: Mapping[str, Any],
) -> str:
    """Render a deterministic, clinician-legible narrative for a clinical_care_gap finding.

    Surfaces the rule, cited evidence, the expected action and its timing window, the urgency,
    which documented exceptions were checked, and the gap's resolution status. Purely
    presentational: it restates fields the deterministic engine already produced from the
    walled-off clinical_care_gap domain. It never introduces an authoritative field, mutates a
    claim, assigns a DRG, computes reimbursement, or bypasses review — analytics identify the
    gap; clinicians decide. Given the same inputs it always yields the same string.
    """
    case_id = str(case_context.get("case_id", "")).strip() or "the encounter"
    title = str(finding_dict.get("title", "")).strip() or "Care gap"
    rule_id = str(finding_dict.get("rule_id", "")).strip() or "unknown-rule"

    gap_domain = str(finding_dict.get("gap_domain", "")).strip().replace("_", " ") or "care gap"
    parts = [f"On {case_id}, care-gap rule {rule_id} identified a {gap_domain}: {title}."]

    evidence_ids = finding_dict.get("evidence_ids") or ()
    if isinstance(evidence_ids, (list, tuple)) and evidence_ids:
        parts.append(f"Grounded in evidence {', '.join(str(item) for item in evidence_ids)}.")

    expected = str(finding_dict.get("expected_action", "")).strip()
    window = _format_window(finding_dict.get("timing_window_days"))
    if expected and window:
        parts.append(f"Expected action: {expected.replace('_', ' ')} within {window}.")
    elif expected:
        parts.append(f"Expected action: {expected.replace('_', ' ')}.")

    recommended = str(finding_dict.get("recommended_action", "")).strip()
    if recommended:
        parts.append(f"Recommended: {recommended}")
        if not recommended.endswith((".", "!", "?")):
            parts[-1] = parts[-1] + "."

    urgency = str(finding_dict.get("alert_urgency", "")).strip()
    if urgency:
        parts.append(f"Alert urgency: {_URGENCY_PHRASES.get(urgency, urgency)}.")

    exception_checks = finding_dict.get("exception_checks") or ()
    if isinstance(exception_checks, (list, tuple)) and exception_checks:
        confirmed = [
            str(check.get("exception_type", "")).replace("_", " ")
            for check in exception_checks
            if isinstance(check, Mapping) and check.get("status") == "confirmed"
        ]
        checked = [
            str(check.get("exception_type", "")).replace("_", " ")
            for check in exception_checks
            if isinstance(check, Mapping)
        ]
        if confirmed:
            parts.append(f"Documented exception confirmed: {', '.join(confirmed)}.")
        else:
            parts.append(f"Exceptions checked (none confirmed): {', '.join(checked)}.")

    gap_status = str(finding_dict.get("gap_status", "")).strip() or "open"
    parts.append(
        f"Resolution status: {gap_status} — analytics identify the gap; a clinician decides "
        "(no claim change, review required)."
    )
    return " ".join(parts)


def render_finding_narrative(
    finding_dict: Mapping[str, Any],
    case_context: Mapping[str, Any],
) -> str:
    """Render a deterministic, plain-language summary sentence for one finding.

    ``finding_dict`` is a serialized finding (``Finding.to_dict()`` output). ``case_context``
    supplies encounter identity (e.g. ``case_id``) for grounding; only its ``case_id`` is
    read. No authoritative field is created — the sentence is a restatement of existing
    deterministic finding fields.

    A clinical_care_gap finding (one carrying ``gap_domain``) is routed to the clinician-legible
    gap narrative; a revenue_integrity finding renders exactly as before (byte-identical).
    """
    if finding_dict.get("gap_domain") is not None:
        return render_gap_finding_narrative(finding_dict, case_context)
    case_id = str(case_context.get("case_id", "")).strip() or "the encounter"
    title = str(finding_dict.get("title", "")).strip() or "Finding"
    rule_id = str(finding_dict.get("rule_id", "")).strip() or "unknown-rule"

    current_drg = str(finding_dict.get("current_drg", "")).strip()
    simulated_drg = str(finding_dict.get("simulated_drg", "")).strip()

    parts = [f"On {case_id}, rule {rule_id} flagged: {title}."]

    if current_drg and simulated_drg and current_drg != simulated_drg:
        parts.append(f"DRG would move from {current_drg} to {simulated_drg}.")
    elif current_drg:
        parts.append(f"DRG remains {current_drg}.")

    impact_status = str(finding_dict.get("impact_status", "")).strip()
    impact_cents = finding_dict.get("estimated_impact_cents")
    if impact_status == "estimated" and isinstance(impact_cents, int) and not isinstance(impact_cents, bool):
        direction = "upside" if impact_cents >= 0 else "downside exposure"
        parts.append(
            f"Estimated {direction} of {_format_cents(impact_cents)} (synthetic demo grouper, not for billing)."
        )
    elif impact_status == "unavailable":
        parts.append("Estimated impact is unavailable.")
    else:
        parts.append("No estimated impact applies.")

    disposition = str(finding_dict.get("disposition", "")).strip()
    disposition_phrase = _DISPOSITION_PHRASES.get(disposition, disposition or "review")
    if finding_dict.get("requires_human_review"):
        parts.append(f"Requires human review; route to {disposition_phrase}.")
    else:
        parts.append(f"Suggested routing: {disposition_phrase}.")

    return " ".join(parts)
