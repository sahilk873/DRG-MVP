"""Deterministic CARC/RARC denial root-cause classification.

Given an :class:`~revenue_integrity.financial.FinancialSnapshot` that carries payer
denials tagged with CARC (Claim Adjustment Reason Code) and/or RARC (Remittance Advice
Remark Code) values, this module emits governed system :class:`~revenue_integrity.models.Finding`
objects describing the *root cause* of each denial and routing it to the appropriate human
reviewer disposition.

This is a pure, deterministic lookup against a versioned, governed reference table
(``data/denial_reason_codes_v1.json``). No language-model output is involved. Findings
carry an EMPTY proposed change and NEVER mutate a claim, assign a DRG, or compute
reimbursement — they only route an *already-received* denial to review. An unknown code
is never silently dropped: it produces an ``unclassified`` root-cause finding.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Mapping

from .financial import Denial, FinancialSnapshot
from .models import Disposition, EncounterCase, Finding, ImpactStatus

# Reason-code system labels used in finding derivation / rationale.
CARC = "CARC"
RARC = "RARC"

# CARC values are purely numeric; RARC values are alphanumeric (letter-led). Reason-code
# strings may bundle several tokens with optional ``CARC:``/``RARC:`` prefixes.
_TOKEN_SPLIT = re.compile(r"[\s,;/|]+")
_PREFIX = re.compile(r"^(carc|rarc)[:=-]?(.+)$", re.IGNORECASE)
# X12 835 claim-adjustment group codes that may prefix a CARC (e.g. "CO-50" / "PR96").
_GROUP_CODE = re.compile(r"^(CO|PR|OA|PI|CR)[-: ]?([0-9]+)$", re.IGNORECASE)
_CARC_PATTERN = re.compile(r"^[0-9]+$")
_RARC_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")

_UNCLASSIFIED_ROOT_CAUSE = "unclassified"

# Governed, additive ontology that defines DenialEvent as a first-class subject type.
# A denial finding's subject lineage points at a DenialEvent subject in this ontology.
DENIAL_ONTOLOGY_ID = "denial-event-ontology"
DENIAL_ONTOLOGY_VERSION = "1.0.0-draft"
DENIAL_EVENT_SUBJECT_TYPE = "DenialEvent"


def _denial_event_subject_id(case_id: str, denial: Denial) -> str:
    """Deterministic id of the governed DenialEvent subject a denial finding is bound to.

    The DenialEvent is a governed ontology subject (``DENIAL_ONTOLOGY_ID``); this id gives a
    denial finding stable subject lineage without any model involvement.
    """
    material = {
        "case_id": case_id,
        "ontology_id": DENIAL_ONTOLOGY_ID,
        "ontology_version": DENIAL_ONTOLOGY_VERSION,
        "subject_type": DENIAL_EVENT_SUBJECT_TYPE,
        "denial_id": denial.denial_id,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return f"denial-event-{digest}"


def _rule_package_version() -> str:
    # Imported lazily to avoid a module import cycle (engine imports this module).
    from .engine import ENGINE_VERSION

    return ENGINE_VERSION


@dataclass(frozen=True)
class ReasonCodeEntry:
    """A governed CARC/RARC root-cause mapping row."""

    code_system: str
    code: str
    root_cause: str
    disposition: Disposition


@dataclass(frozen=True)
class DenialReasonCodeTable:
    """Versioned, governed CARC/RARC -> root-cause + disposition reference table."""

    table_id: str
    version: str
    status: str
    carc: Mapping[str, Mapping[str, str]]
    rarc: Mapping[str, Mapping[str, str]]

    @property
    def digest(self) -> str:
        payload = {
            "table_id": self.table_id,
            "version": self.version,
            "status": self.status,
            "carc": {k: dict(v) for k, v in self.carc.items()},
            "rarc": {k: dict(v) for k, v in self.rarc.items()},
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DenialReasonCodeTable":
        if not isinstance(data, Mapping):
            raise ValueError("denial reason-code table definition must be a mapping")
        required = {"table_id", "version", "status", "carc", "rarc"}
        if missing := required - set(data):
            raise ValueError(f"denial reason-code table is missing fields: {sorted(missing)}")

        def _section(name: str) -> dict[str, dict[str, str]]:
            section = data[name]
            if not isinstance(section, Mapping):
                raise ValueError(f"denial reason-code table {name!r} section must be a mapping")
            out: dict[str, dict[str, str]] = {}
            for code, row in section.items():
                if not isinstance(code, str) or not code.strip():
                    raise ValueError(f"{name} code keys must be non-empty strings")
                if not isinstance(row, Mapping) or "root_cause" not in row or "disposition" not in row:
                    raise ValueError(f"{name} code {code!r} must map to root_cause + disposition")
                root_cause = row["root_cause"]
                disposition = row["disposition"]
                if not isinstance(root_cause, str) or not root_cause.strip():
                    raise ValueError(f"{name} code {code!r} root_cause must be a non-empty string")
                # Validate the disposition is a supported enum value (fail closed).
                Disposition(disposition)
                out[code.strip()] = {"root_cause": root_cause, "disposition": disposition}
            return out

        return cls(
            table_id=str(data["table_id"]),
            version=str(data["version"]),
            status=str(data["status"]),
            carc=_section("carc"),
            rarc=_section("rarc"),
        )

    def lookup(self, code_system: str, code: str) -> ReasonCodeEntry | None:
        section = self.carc if code_system == CARC else self.rarc if code_system == RARC else None
        if section is None:
            return None
        row = section.get(code)
        if row is None:
            return None
        return ReasonCodeEntry(
            code_system=code_system,
            code=code,
            root_cause=row["root_cause"],
            disposition=Disposition(row["disposition"]),
        )


def load_denial_reason_code_table(
    resource_name: str = "data/denial_reason_codes_v1.json",
) -> DenialReasonCodeTable:
    """Load the packaged, governed denial reason-code table."""
    resource = files("revenue_integrity").joinpath(resource_name)
    return DenialReasonCodeTable.from_dict(json.loads(resource.read_text(encoding="utf-8")))


def _classify_token(token: str) -> tuple[str, str] | None:
    """Return ``(code_system, code)`` for a single reason-code token, or ``None`` if empty.

    A leading ``CARC:``/``RARC:`` prefix is honored; otherwise the code is classified by
    shape (numeric -> CARC, letter-led alphanumeric -> RARC). Unrecognized shapes are
    treated as RARC-style so they still surface as an ``unclassified`` finding rather than
    being dropped.
    """
    token = token.strip()
    if not token:
        return None
    if match := _PREFIX.match(token):
        system = match.group(1).upper()
        code = match.group(2).strip()
        if not code:
            return None
        # A prefixed CARC token may still carry an X12 group code (e.g. "CARC:CO-50").
        if system == CARC and (group := _GROUP_CODE.match(code)):
            return (CARC, group.group(2))
        return (system, code)
    if group := _GROUP_CODE.match(token):
        return (CARC, group.group(2))
    if _CARC_PATTERN.match(token):
        return (CARC, token)
    if _RARC_PATTERN.match(token):
        return (RARC, token)
    # Unknown shape: still classify (as RARC) so it is never silently dropped.
    return (RARC, token)


def _parse_reason_code(reason_code: str) -> list[tuple[str, str]]:
    """Split a denial ``reason_code`` string into ordered, de-duplicated (system, code) pairs."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in _TOKEN_SPLIT.split(reason_code):
        classified = _classify_token(raw)
        if classified is None or classified in seen:
            continue
        seen.add(classified)
        pairs.append(classified)
    return pairs


def _finding_id(case_id: str, denial: Denial, code_system: str, code: str, root_cause: str) -> str:
    material = {
        "case_id": case_id,
        "check": "denial-rootcause",
        "denial_id": denial.denial_id,
        "code_system": code_system,
        "code": code,
        "root_cause": root_cause,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return f"finding-{digest}"


def denial_root_cause_findings(
    case: EncounterCase,
    *,
    table: DenialReasonCodeTable | None = None,
) -> list[Finding]:
    """Emit deterministic root-cause :class:`Finding` objects for the case's denials.

    Returns an empty list when the case carries no financial snapshot or no denials.
    For every CARC/RARC token on every denial, emits one finding routed to the mapped
    review disposition (or a ``compliance_review`` ``unclassified`` finding for codes not
    in the governed table). Findings carry an empty proposed change and never mutate the
    claim. Ordering is deterministic (denial order, then reason-code token order).
    """
    financial: FinancialSnapshot | None = case.financial
    if financial is None or not financial.denials:
        return []

    reference = table or load_denial_reason_code_table()
    version = _rule_package_version()
    findings: list[Finding] = []

    for denial in financial.denials:
        line_refs = tuple(denial.line_ids)
        denial_subject_id = _denial_event_subject_id(case.case_id, denial)
        for code_system, code in _parse_reason_code(denial.reason_code):
            entry = reference.lookup(code_system, code)
            if entry is None:
                root_cause = _UNCLASSIFIED_ROOT_CAUSE
                disposition = Disposition.COMPLIANCE_REVIEW
                title = (
                    f"Denial {denial.denial_id} carries an unrecognized {code_system} code "
                    f"({code})"
                )
                rationale = (
                    f"Payer denial {denial.denial_id} on line(s) {', '.join(line_refs)} cites "
                    f"{code_system} code {code}, which is not present in the governed denial "
                    f"reason-code table ({reference.table_id} v{reference.version}). It could "
                    "not be classified to a root cause; route for manual disposition. No claim "
                    "change is proposed."
                )
            else:
                root_cause = entry.root_cause
                disposition = entry.disposition
                title = (
                    f"Denial {denial.denial_id} root cause: {root_cause} "
                    f"({code_system} {code})"
                )
                rationale = (
                    f"Payer denial {denial.denial_id} on line(s) {', '.join(line_refs)} cites "
                    f"{code_system} code {code}, mapped by the governed reason-code table "
                    f"({reference.table_id} v{reference.version}) to root cause "
                    f"'{root_cause}'. Route to {disposition.value} for disposition. No claim "
                    "change is proposed; this only classifies an already-received denial."
                )
            findings.append(Finding(
                finding_id=_finding_id(case.case_id, denial, code_system, code, root_cause),
                rule_id="SYSTEM-DENIAL-ROOTCAUSE",
                rule_package_id="deterministic-system-checks",
                rule_package_version=version,
                title=title,
                disposition=disposition,
                confidence=1.0,
                proposed_change={},
                subject_ids=(denial_subject_id,),
                assertion_ids=(),
                evidence_ids=(),
                contradicting_evidence_ids=(),
                rationale=rationale,
                requires_human_review=True,
                submitted_drg=case.claim.drg,
                current_drg=case.claim.drg or "",
                simulated_drg=case.claim.drg or "",
                estimated_impact_cents=None,
                impact_status=ImpactStatus.NOT_APPLICABLE,
                grouper_version="",
                derivation={
                    "denial_ids": [denial.denial_id],
                    "reason_code_system": [code_system],
                    "reason_code": [code],
                    "root_cause": [root_cause],
                    "reason_code_table_version": [reference.version],
                    "reason_code_table_digest": [reference.digest],
                    "denial_subject_id": [denial_subject_id],
                    "denial_subject_type": [DENIAL_EVENT_SUBJECT_TYPE],
                    "denial_ontology_id": [DENIAL_ONTOLOGY_ID],
                    "denial_ontology_version": [DENIAL_ONTOLOGY_VERSION],
                },
                charge_line_refs=line_refs,
            ))
    return findings
