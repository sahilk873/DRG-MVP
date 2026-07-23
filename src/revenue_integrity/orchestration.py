"""Deterministic backend handoff for clinical-financial investigations."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from .grouper import Grouper, GroupingResult, derivation_pair
from .investigation import (
    InvestigationPacket,
    OpportunityHypothesis,
    promote_hypotheses_to_findings,
    validate_hypotheses,
)
from .models import Disposition, EncounterCase, Finding, ImpactStatus


@dataclass(frozen=True, slots=True)
class InvestigationRun:
    packet_id: str
    encounter_id: str
    findings: tuple[Finding, ...]
    rejected_hypothesis_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "encounter_id": self.encounter_id,
            "findings": [item.to_dict() for item in self.findings],
            "rejected_hypothesis_count": self.rejected_hypothesis_count,
        }


def run_investigation(
    packet: InvestigationPacket,
    hypotheses: Sequence[OpportunityHypothesis],
    grouper: Grouper,
) -> InvestigationRun:
    """Run the governed agent-to-review handoff without mutating source data.

    In addition to promoting safe hypotheses into reviewable findings, this emits a
    deterministic ``AGENT_DISAGREEMENT`` escalation finding whenever two *already
    validated* agent outputs conflict (see :func:`_agent_disagreement_findings`). The
    disagreement finding is computed from the validated hypotheses only — never generated
    by a model — and never mutates the claim.
    """
    findings = promote_hypotheses_to_findings(packet, hypotheses, grouper)
    safe = validate_hypotheses(packet, hypotheses)
    baseline = grouper.group(packet.case, packet.case.claim)
    disagreements = _agent_disagreement_findings(packet.case, safe, baseline)
    combined = tuple(findings) + tuple(disagreements)
    return InvestigationRun(
        packet_id=packet.packet_id,
        encounter_id=packet.case.encounter_id,
        findings=combined,
        rejected_hypothesis_count=len(hypotheses) - len(findings),
    )


def _agent_identity(hypothesis: OpportunityHypothesis) -> str:
    """Stable agent identity for a validated hypothesis, from governed provenance."""
    provenance = hypothesis.provenance
    for key in ("agent_id", "agent", "source_agent"):
        value = provenance.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "unknown-agent"


def _agent_disagreement_findings(
    case: EncounterCase,
    safe_hypotheses: Sequence[OpportunityHypothesis],
    baseline: GroupingResult,
) -> list[Finding]:
    """Deterministically detect conflicts between already-validated agent outputs.

    Two validated hypotheses conflict when one cites a piece of evidence as *supporting*
    while the other cites the *same* evidence as *contradicting* — the canonical shape of
    an investigation-critic contradicting an encounter-extractor hypothesis, or two agents
    asserting contradictory dispositions grounded in the same source. For each such
    conflicting pair we emit exactly one escalation :class:`Finding` (like the engine's
    ``SYSTEM-DRG-*`` findings) that routes to human escalation, carries both conflicting
    agent identities and a rationale, and retains subject/assertion/evidence lineage.

    Pure function of the validated hypotheses; no language-model output participates and
    the claim is never mutated.
    """
    assertion_subjects = {item.assertion_id: item.subject_id for item in case.assertions}
    findings: list[Finding] = []
    seen_pairs: set[tuple[str, str]] = set()
    # Deterministic ordering by hypothesis_id so the pair scan and IDs are reproducible.
    ordered = sorted(safe_hypotheses, key=lambda item: item.hypothesis_id)
    for outer in range(len(ordered)):
        left = ordered[outer]
        left_support = set(left.evidence_ids)
        left_contra = set(left.contradicting_evidence_ids)
        for inner in range(outer + 1, len(ordered)):
            right = ordered[inner]
            # A conflict: one side supports evidence the other side contradicts.
            conflicting_evidence = (left_support & set(right.contradicting_evidence_ids)) | (
                left_contra & set(right.evidence_ids)
            )
            if not conflicting_evidence:
                continue
            pair_key = tuple(sorted((left.hypothesis_id, right.hypothesis_id)))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            findings.append(
                _build_disagreement_finding(
                    case, left, right, tuple(sorted(conflicting_evidence)), assertion_subjects, baseline
                )
            )
    return findings


def _build_disagreement_finding(
    case: EncounterCase,
    left: OpportunityHypothesis,
    right: OpportunityHypothesis,
    conflicting_evidence: tuple[str, ...],
    assertion_subjects: dict[str, str],
    baseline: GroupingResult,
) -> Finding:
    left_agent = _agent_identity(left)
    right_agent = _agent_identity(right)
    assertion_ids = tuple(dict.fromkeys(left.assertion_ids + right.assertion_ids))
    subject_ids = tuple(dict.fromkeys(
        assertion_subjects[item] for item in assertion_ids if item in assertion_subjects
    ))
    evidence_ids = tuple(dict.fromkeys(left.evidence_ids + right.evidence_ids))
    contradicting_evidence_ids = tuple(dict.fromkeys(
        left.contradicting_evidence_ids + right.contradicting_evidence_ids
    ))
    material = {
        "case_id": case.case_id,
        "check": "agent-disagreement",
        "hypotheses": sorted((left.hypothesis_id, right.hypothesis_id)),
        "conflicting_evidence": list(conflicting_evidence),
        "grouper_version": baseline.grouper_version,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    # Deterministic, ordered pairing of agent identity to the hypothesis it produced.
    agents = sorted({left_agent, right_agent})
    rationale = (
        f"Validated agent outputs conflict: hypothesis {left.hypothesis_id} "
        f"(agent {left_agent}) and hypothesis {right.hypothesis_id} (agent {right_agent}) "
        f"take contradictory positions on evidence {', '.join(conflicting_evidence)}. "
        "Escalated for human adjudication before any coding or billing action."
    )
    metadata = {
        **derivation_pair(baseline, baseline),
        "conflicting_agents": agents,
        "conflicting_hypotheses": sorted((left.hypothesis_id, right.hypothesis_id)),
        "conflicting_evidence_ids": list(conflicting_evidence),
    }
    return Finding(
        finding_id=f"finding-disagreement-{digest}",
        rule_id="AGENT_DISAGREEMENT",
        rule_package_id="deterministic-system-checks",
        rule_package_version=ENGINE_HANDOFF_VERSION,
        title="Validated agent outputs disagree; escalated for human adjudication",
        disposition=Disposition.COMPLIANCE_REVIEW,
        confidence=1.0,
        proposed_change={},
        subject_ids=subject_ids,
        assertion_ids=assertion_ids,
        evidence_ids=evidence_ids,
        contradicting_evidence_ids=contradicting_evidence_ids,
        rationale=rationale,
        requires_human_review=True,
        submitted_drg=case.claim.drg,
        current_drg=baseline.drg,
        simulated_drg=baseline.drg,
        estimated_impact_cents=None,
        impact_status=ImpactStatus.NOT_APPLICABLE,
        grouper_version=baseline.grouper_version,
        derivation=metadata,
    )


#: Governance version stamped on the deterministic agent-disagreement escalation finding.
ENGINE_HANDOFF_VERSION = "0.1.0"
