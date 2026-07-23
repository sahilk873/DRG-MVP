from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .audit import audit_record, canonical_hash
from .models import EncounterCase, Finding, ImpactStatus
from .narrative import render_finding_narrative


# 3.5.0 extends the wire format with OPTIONAL clinical_care_gap finding fields (emitted only for
# findings from the walled-off clinical_care_gap domain). A revenue_integrity packet carries none of
# these keys, so its serialized shape is byte-for-byte unchanged except for this version string.
REVIEW_PACKET_SCHEMA_VERSION = "3.5.0"
REVIEW_PACKET_ENVIRONMENTS = frozenset({"development", "synthetic", "validation", "production"})


def _evidence_with_source_locator(evidence_items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach a deterministic, read-only deep-link locator to each surfaced evidence item.

    The locator is a pure function of grounding the trust boundary already validated: the
    agent (or authored reader) guarantees ``text`` is an exact, contiguous substring of the
    source document ``document_id``. From that guarantee alone we derive a stable deep-link
    target for the reviewer UI — no language-model output participates and no authoritative
    field is created.

    Three mutually exclusive shapes, discriminated by ``kind``:

    - Evidence that already carries a structured-adapter ``source_locator`` (path/row) keeps
      that exact locator, re-tagged ``kind="structured_source_record"`` so the UI can render a
      row-level deep link. Adapter evidence already has a precise deterministic address.
    - Evidence that already carries a well-formed ``clinical_note_excerpt`` locator (real
      ``char_start``/``char_end`` offsets, no adapter ``path``) is preserved byte-for-byte.
      A precomputed excerpt span already addresses the exact region of the source document,
      so re-synthesizing a 0..len span would silently corrupt it.
    - Clinical-note excerpts with no usable locator receive a synthesized
      ``kind="clinical_note_excerpt"`` with ``document_id`` plus the excerpt span
      (``char_start``/``char_end``/``length``) and a content-addressing ``excerpt_sha256``.
      The span is expressed relative to the surfaced excerpt window; a viewer
      content-addresses the excerpt inside ``document_id`` to place it.

    Fails closed: an evidence item with no usable locator and no ``text`` cannot be
    deep-linked, so a clear domain ``ValueError`` naming the offending item is raised rather
    than a bare ``KeyError``.
    """
    surfaced: list[dict[str, Any]] = []
    for raw in evidence_items:
        item = dict(raw)
        existing = item.get("source_locator")
        if isinstance(existing, Mapping) and "path" in existing:
            # Structured-adapter row address: keep the precise locator, re-tag for the UI.
            locator = dict(existing)
            locator["kind"] = "structured_source_record"
            item["source_locator"] = locator
        elif isinstance(existing, Mapping) and _is_excerpt_locator(existing):
            # Pre-computed excerpt span: preserve intact — never clobber real offsets.
            item["source_locator"] = dict(existing)
        else:
            text = item.get("text")
            if not isinstance(text, str):
                evidence_id = item.get("evidence_id", "<unknown>")
                raise ValueError(
                    f"evidence {evidence_id!r} has no usable source_locator and no text "
                    "to synthesize a clinical_note_excerpt deep link"
                )
            length = len(text)
            item["source_locator"] = {
                "kind": "clinical_note_excerpt",
                "document_id": item["document_id"],
                "char_start": 0,
                "char_end": length,
                "length": length,
                "excerpt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        surfaced.append(item)
    return surfaced


def _is_excerpt_locator(locator: Mapping[str, Any]) -> bool:
    """True when a locator already carries an explicit clinical-note excerpt span.

    Recognised by an integer ``char_start``/``char_end`` pair (booleans excluded) or an
    explicit ``kind == "clinical_note_excerpt"``. Such a locator has already resolved the
    exact region of the source document, so the synthesizer must leave it untouched.
    """
    if locator.get("kind") == "clinical_note_excerpt":
        return True
    start = locator.get("char_start")
    end = locator.get("char_end")
    return (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
    )


def summarize_finding_impact(findings: Sequence[Finding]) -> dict[str, Any]:
    """Aggregate deterministic, hash-coverable ROI context from validated findings.

    Pure integer-cent arithmetic over fields that already crossed the trust boundary
    (``estimated_impact_cents`` / ``impact_status`` / ``disposition``). No language-model
    output participates: this only rolls up numbers the deterministic engine already
    produced, so a reviewer or CFO can reproduce every figure from the finding list.

    Sign convention: ``positive_opportunity_cents`` sums under-coded upside (impact > 0);
    ``at_risk_cents`` reports the magnitude of downside exposure (impact < 0) as a
    non-negative number; ``net_estimated_impact_cents`` is their signed sum.
    """
    positive = 0
    at_risk = 0
    estimated_count = 0
    unavailable_count = 0
    not_applicable_count = 0
    by_disposition: dict[str, int] = {}
    for finding in findings:
        by_disposition[finding.disposition.value] = by_disposition.get(finding.disposition.value, 0) + 1
        if finding.impact_status is ImpactStatus.ESTIMATED and finding.estimated_impact_cents is not None:
            estimated_count += 1
            if finding.estimated_impact_cents >= 0:
                positive += finding.estimated_impact_cents
            else:
                at_risk += -finding.estimated_impact_cents
        elif finding.impact_status is ImpactStatus.UNAVAILABLE:
            unavailable_count += 1
        else:
            not_applicable_count += 1
    return {
        "currency": "USD",
        "net_estimated_impact_cents": positive - at_risk,
        "positive_opportunity_cents": positive,
        "at_risk_cents": at_risk,
        "estimated_finding_count": estimated_count,
        "unavailable_impact_count": unavailable_count,
        "not_applicable_impact_count": not_applicable_count,
        "total_findings": len(findings),
        "findings_requiring_review": sum(1 for item in findings if item.requires_human_review),
        "findings_by_disposition": dict(sorted(by_disposition.items())),
        "basis": "synthetic-demo-grouper-not-for-billing",
    }


def summarize_denial_exposure(case: EncounterCase) -> dict[str, Any]:
    """Deterministic, hash-coverable payer-denial rollup from the case financial snapshot.

    Pure integer-cent arithmetic over the immutable ``FinancialSnapshot`` that already
    crossed the trust boundary (``denials`` + ``claim_lines``). No language-model output
    participates. Zeros out cleanly when the case carries no financial context, so the
    field is always well-typed and additive.

    - ``denied_amount_cents`` — sum of denial amounts (``0`` for denials without an amount);
    - ``denial_count`` — number of denials;
    - ``at_risk_line_ids`` — sorted, de-duplicated charge-line IDs referenced by any denial.
    """
    financial = case.financial
    if financial is None:
        return {
            "currency": "USD",
            "denied_amount_cents": 0,
            "denial_count": 0,
            "at_risk_line_count": 0,
            "at_risk_line_ids": [],
        }
    at_risk: set[str] = set()
    for denial in financial.denials:
        at_risk.update(denial.line_ids)
    at_risk_line_ids = sorted(at_risk)
    return {
        "currency": "USD",
        "denied_amount_cents": financial.denied_amount_cents,
        "denial_count": len(financial.denials),
        "at_risk_line_count": len(at_risk_line_ids),
        "at_risk_line_ids": at_risk_line_ids,
    }


def build_review_packet(
    *,
    case: EncounterCase,
    case_payload: Mapping[str, Any],
    rule_package: Mapping[str, Any],
    findings: Sequence[Finding],
    tenant_id: str,
    workspace_id: str,
    environment: str = "development",
    clock: Callable[[], datetime] | None = None,
    previous_record_hash: str | None = None,
) -> dict[str, Any]:
    """Build the versioned handoff between deterministic evaluation and reviewer UI.

    The packet is intentionally a review artifact, not a claim transaction. It contains
    the validated evidence graph and immutable claim snapshot needed to reproduce a
    finding, plus explicit controls that forbid claim mutation.
    """
    if environment not in REVIEW_PACKET_ENVIRONMENTS:
        raise ValueError(f"unsupported review-packet environment: {environment!r}")
    tenant_id = _identifier(tenant_id, "tenant_id")
    workspace_id = _identifier(workspace_id, "workspace_id")
    _verify_case_payload(case, case_payload)

    case_context = {"case_id": case.case_id, "encounter_id": case.encounter_id}
    finding_payloads = []
    for finding in findings:
        payload = finding.to_dict()
        payload["narrative"] = render_finding_narrative(payload, case_context)
        finding_payloads.append(payload)
    audit = audit_record(
        case_payload=case_payload,
        rule_package=rule_package,
        findings=finding_payloads,
        clock=clock,
        previous_record_hash=previous_record_hash,
    )
    packet_digest = hashlib.sha256(
        json.dumps(
            {"tenant_id": tenant_id, "workspace_id": workspace_id, "case_id": case.case_id, "record_hash": audit["record_hash"]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]

    packet = {
        "review_packet_schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "packet_id": f"packet-{packet_digest}",
        "environment": environment,
        "tenant": {"tenant_id": tenant_id, "workspace_id": workspace_id},
        "case": {
            "schema_version": case.schema_version,
            "case_id": case.case_id,
            "patient_id": case.patient_id,
            "encounter_id": case.encounter_id,
            "admitted_at": case.admitted_at,
            "discharged_at": case.discharged_at,
            "metadata": dict(case.metadata),
            "claim": dict(case_payload["claim"]),
        },
        "evidence": _evidence_with_source_locator(case_payload["evidence"]),
        "ontology": dict(case_payload["ontology"]),
        "assertions": list(case_payload["assertions"]),
        "findings": finding_payloads,
        "impact_summary": summarize_finding_impact(findings),
        "denial_summary": summarize_denial_exposure(case),
        "controls": {
            "claim_mutation_allowed": False,
            "human_review_required": any(item.requires_human_review for item in findings),
            "permitted_actions": _permitted_actions(findings),
        },
        "provenance": {
            "evaluated_at": audit["evaluated_at"],
            "engine_version": audit["engine_version"],
            "case_hash": audit["case_hash"],
            "rule_package_id": audit["rule_package_id"],
            "rule_package_version": audit["rule_package_version"],
            "rule_package_hash": audit["rule_package_hash"],
            "record_hash": audit["record_hash"],
            "previous_record_hash": audit["previous_record_hash"],
            "grouper_versions": sorted({item.grouper_version for item in findings}),
        },
    }
    packet["provenance"]["packet_hash"] = canonical_hash(packet)
    return packet


# Revenue-integrity reviewer actions: the exact, ordered list every RI packet has always
# carried. Kept as a literal so an RI-only packet is byte-for-byte identical to prior versions.
_REVENUE_INTEGRITY_ACTIONS: tuple[str, ...] = (
    "route_to_coding",
    "route_to_cdi",
    "route_to_charge_review",
    "route_to_compliance",
    "dismiss_with_reason",
)
# Clinical-care-gap reviewer actions. These NEVER mutate a claim; they route a surfaced gap to
# a clinician or record an evidence-grounded closure. Appended only when a clinical_care_gap
# finding is present, so revenue_integrity packets keep their historical permitted_actions.
_CLINICAL_CARE_GAP_ACTIONS: tuple[str, ...] = (
    "route_to_care_team",
    "close_gap_with_evidence",
)


def _permitted_actions(findings: Sequence[Finding]) -> list[str]:
    actions = list(_REVENUE_INTEGRITY_ACTIONS)
    if any(finding.is_clinical_care_gap() for finding in findings):
        actions.extend(_CLINICAL_CARE_GAP_ACTIONS)
    return actions


def verify_review_packet_hash(packet: Mapping[str, Any]) -> bool:
    provenance = packet.get("provenance")
    if not isinstance(provenance, Mapping):
        return False
    claimed = provenance.get("packet_hash")
    if not isinstance(claimed, str):
        return False
    copy = dict(packet)
    copy["provenance"] = {key: value for key, value in provenance.items() if key != "packet_hash"}
    return canonical_hash(copy) == claimed


def _verify_case_payload(case: EncounterCase, payload: Mapping[str, Any]) -> None:
    expected = {
        "schema_version": case.schema_version,
        "case_id": case.case_id,
        "patient_id": case.patient_id,
        "encounter_id": case.encounter_id,
        "admitted_at": case.admitted_at,
        "discharged_at": case.discharged_at,
    }
    mismatched = [name for name, value in expected.items() if payload.get(name) != value]
    if mismatched:
        raise ValueError(f"review-packet case payload does not match validated case: {mismatched}")
    for name in ("claim", "evidence", "ontology", "assertions"):
        if name not in payload:
            raise ValueError(f"review-packet case payload missing {name}")


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be a non-empty string of at most 128 characters")
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for character in value):
        raise ValueError(f"{name} contains unsupported characters")
    return value
