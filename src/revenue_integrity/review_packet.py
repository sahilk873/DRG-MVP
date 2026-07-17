from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .audit import audit_record
from .models import EncounterCase, Finding


REVIEW_PACKET_SCHEMA_VERSION = "1.0.0"
REVIEW_PACKET_ENVIRONMENTS = frozenset({"development", "synthetic", "validation", "production"})


def build_review_packet(
    *,
    case: EncounterCase,
    case_payload: Mapping[str, Any],
    rule_package: Mapping[str, Any],
    findings: Sequence[Finding],
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
    _verify_case_payload(case, case_payload)

    finding_payloads = [finding.to_dict() for finding in findings]
    audit = audit_record(
        case_payload=case_payload,
        rule_package=rule_package,
        findings=finding_payloads,
        clock=clock,
        previous_record_hash=previous_record_hash,
    )
    packet_digest = hashlib.sha256(
        json.dumps(
            {"case_id": case.case_id, "record_hash": audit["record_hash"]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]

    return {
        "review_packet_schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "packet_id": f"packet-{packet_digest}",
        "environment": environment,
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
        "evidence": list(case_payload["evidence"]),
        "ontology": dict(case_payload["ontology"]),
        "assertions": list(case_payload["assertions"]),
        "findings": finding_payloads,
        "controls": {
            "claim_mutation_allowed": False,
            "human_review_required": any(item.requires_human_review for item in findings),
            "permitted_actions": [
                "route_to_coding",
                "route_to_cdi",
                "route_to_charge_review",
                "route_to_compliance",
                "dismiss_with_reason",
            ],
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
