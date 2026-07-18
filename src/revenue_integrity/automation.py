from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Mapping, Sequence

from .audit import canonical_hash
from .models import Disposition, Finding, ImpactStatus

AUTOMATION_SCHEMA_VERSION = "1.0.0"


class AutomationTier(StrEnum):
    SUPPRESSED = "suppressed"
    NEEDS_ENRICHMENT = "needs_enrichment"
    AUTO_ROUTED = "auto_routed"
    QUICK_CONFIRM = "quick_confirm"
    FOCUSED_REVIEW = "focused_review"
    ESCALATED = "escalated"


class AutomationQueue(StrEnum):
    NONE = "none"
    CODING = "coding"
    CDI = "cdi"
    CHARGE = "charge"
    COMPLIANCE = "compliance"


class AutomationReason(StrEnum):
    NO_ACTIONABLE_CHANGE = "no_actionable_change"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    EXACT_DUPLICATE = "exact_duplicate"
    ROUTINE_OPERATIONAL_ROUTE = "routine_operational_route"
    MATERIAL_DRG_CHANGE = "material_drg_change"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    DOCUMENTATION_QUERY = "documentation_query"
    COMPLIANCE_SENSITIVE = "compliance_sensitive"
    LOWER_CONFIDENCE = "lower_confidence"
    UNKNOWN_IMPACT = "unknown_impact"
    INCONSISTENT_FINDING = "inconsistent_finding"


@dataclass(frozen=True, slots=True)
class AutomationPolicy:
    policy_id: str = "exception-orchestration"
    version: str = "1.0.0"
    quick_confirm_confidence: float = 0.95
    auto_route_confidence: float = 0.93
    auto_route_max_impact_cents: int = 250_000
    max_review_cases: int = 25
    max_review_seconds: int = 1_800

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or not self.version.strip():
            raise ValueError("automation policy requires an id and version")
        for name in ("quick_confirm_confidence", "auto_route_confidence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"automation policy {name} must be between 0 and 1")
        for name in ("auto_route_max_impact_cents", "max_review_cases", "max_review_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"automation policy {name} must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "quick_confirm_confidence": self.quick_confirm_confidence,
            "auto_route_confidence": self.auto_route_confidence,
            "auto_route_max_impact_cents": self.auto_route_max_impact_cents,
            "max_review_cases": self.max_review_cases,
            "max_review_seconds": self.max_review_seconds,
        }

    @property
    def digest(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True, slots=True)
class FindingAutomation:
    automation_id: str
    finding_id: str
    finding_hash: str
    semantic_fingerprint: str
    tier: AutomationTier
    queue: AutomationQueue
    reason_codes: tuple[AutomationReason, ...]
    recommended_action: str | None
    allowed_actions: tuple[str, ...]
    draft: Mapping[str, Any]
    priority_score: int
    estimated_review_seconds: int
    duplicate_of: str | None = None
    related_finding_ids: tuple[str, ...] = ()

    @property
    def needs_human(self) -> bool:
        return self.tier in {
            AutomationTier.QUICK_CONFIRM, AutomationTier.FOCUSED_REVIEW, AutomationTier.ESCALATED
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "automation_id": self.automation_id,
            "finding_id": self.finding_id,
            "finding_hash": self.finding_hash,
            "semantic_fingerprint": self.semantic_fingerprint,
            "tier": self.tier.value,
            "queue": self.queue.value,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "recommended_action": self.recommended_action,
            "allowed_actions": list(self.allowed_actions),
            "draft": dict(self.draft),
            "priority_score": self.priority_score,
            "estimated_review_seconds": self.estimated_review_seconds,
            "duplicate_of": self.duplicate_of,
            "related_finding_ids": list(self.related_finding_ids),
        }


def build_automation_plan(
    findings: Sequence[Finding],
    *,
    tenant_id: str,
    workspace_id: str,
    case_id: str,
    encounter_id: str,
    packet_id: str,
    packet_hash: str,
    policy: AutomationPolicy | None = None,
) -> dict[str, Any]:
    """Deterministically minimize reviewer work without authorizing claim mutation."""
    active_policy = policy or AutomationPolicy()
    for name, value in (
        ("tenant_id", tenant_id), ("workspace_id", workspace_id), ("case_id", case_id),
        ("encounter_id", encounter_id), ("packet_id", packet_id)
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"automation {name} must not be empty")
    if len(packet_hash) != 64 or any(character not in "0123456789abcdef" for character in packet_hash):
        raise ValueError("automation packet_hash must be a lowercase SHA-256 digest")
    finding_ids = [item.finding_id for item in findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("automation findings must have unique finding_id values")
    ordered = sorted(findings, key=lambda item: item.finding_id)
    scope_hash = canonical_hash({
        "tenant_id": tenant_id, "workspace_id": workspace_id,
        "case_id": case_id, "encounter_id": encounter_id,
    })
    artifact_scope_hash = canonical_hash({
        "scope_hash": scope_hash,
        "packet_id": packet_id,
        "packet_hash": packet_hash,
    })
    candidates = [
        _classify(item, active_policy, scope_hash, artifact_scope_hash)
        for item in ordered
    ]
    consolidated = _consolidate(candidates)
    review = sorted(
        (item for item in consolidated if item.needs_human),
        key=lambda item: (-item.priority_score, item.finding_id),
    )

    review_now: list[str] = []
    deferred: list[str] = []
    seconds = 0
    for item in review:
        bypass = _bypasses_budget(item)
        fits = (
            len(review_now) < active_policy.max_review_cases
            and seconds + item.estimated_review_seconds <= active_policy.max_review_seconds
        )
        if bypass or fits:
            review_now.append(item.finding_id)
            seconds += item.estimated_review_seconds
        else:
            deferred.append(item.finding_id)

    payloads = [item.to_dict() for item in consolidated]
    counts = {tier.value: sum(item.tier is tier for item in consolidated) for tier in AutomationTier}
    plan_body = {
        "automation_schema_version": AUTOMATION_SCHEMA_VERSION,
        "tenant": {"tenant_id": tenant_id, "workspace_id": workspace_id},
        "packet": {
            "packet_id": packet_id, "packet_hash": packet_hash,
            "case_id": case_id, "encounter_id": encounter_id,
        },
        "policy": {**active_policy.to_dict(), "digest": active_policy.digest},
        "findings": payloads,
        "review_now_finding_ids": review_now,
        "deferred_finding_ids": deferred,
        "metrics": {
            "input_findings": len(findings),
            "consolidated_findings": sum(item.duplicate_of is None for item in consolidated),
            "review_now": len(review_now),
            "deferred": len(deferred),
            "estimated_review_seconds": seconds,
            **counts,
        },
    }
    return {**plan_body, "plan_hash": canonical_hash(plan_body)}


def _classify(
    finding: Finding, policy: AutomationPolicy, scope_hash: str, artifact_scope_hash: str,
) -> FindingAutomation:
    fingerprint = _semantic_fingerprint(finding, scope_hash)
    queue, action = _route(finding.disposition)
    reasons: list[AutomationReason] = []
    drg_change = finding.current_drg != finding.simulated_drg
    has_change = bool(finding.proposed_change)
    impact = finding.estimated_impact_cents

    if finding.contradicting_evidence_ids:
        tier = AutomationTier.ESCALATED
        reasons.append(AutomationReason.CONTRADICTORY_EVIDENCE)
        if action is None:
            queue = AutomationQueue.COMPLIANCE
            action = "route_to_compliance"
    elif finding.disposition is Disposition.COMPLIANCE_REVIEW or (impact is not None and impact < 0):
        tier = AutomationTier.ESCALATED
        reasons.append(AutomationReason.COMPLIANCE_SENSITIVE)
        queue = AutomationQueue.COMPLIANCE
        action = "route_to_compliance"
    elif finding.disposition is Disposition.NO_OPPORTUNITY:
        if has_change:
            tier = AutomationTier.ESCALATED
            reasons.append(AutomationReason.INCONSISTENT_FINDING)
            queue = AutomationQueue.COMPLIANCE
            action = "route_to_compliance"
        else:
            tier = AutomationTier.SUPPRESSED
            reasons.append(AutomationReason.NO_ACTIONABLE_CHANGE)
            action = None
            queue = AutomationQueue.NONE
    elif finding.disposition is Disposition.INSUFFICIENT_EVIDENCE:
        tier = AutomationTier.NEEDS_ENRICHMENT
        reasons.append(AutomationReason.INSUFFICIENT_EVIDENCE)
        action = None
        queue = AutomationQueue.NONE
    elif impact is None or finding.impact_status is ImpactStatus.UNAVAILABLE:
        # Unknown financial impact is never treated as zero and cannot consume a
        # routine budget slot. It remains visible until a reviewer resolves it.
        tier = AutomationTier.ESCALATED
        reasons.append(AutomationReason.UNKNOWN_IMPACT)
    elif finding.disposition is Disposition.CDI_QUERY:
        tier = AutomationTier.FOCUSED_REVIEW
        reasons.append(AutomationReason.DOCUMENTATION_QUERY)
    elif drg_change and finding.confidence >= policy.quick_confirm_confidence:
        tier = AutomationTier.QUICK_CONFIRM
        reasons.append(AutomationReason.MATERIAL_DRG_CHANGE)
    elif (
        not drg_change
        and finding.confidence >= policy.auto_route_confidence
        and impact is not None
        and abs(impact) <= policy.auto_route_max_impact_cents
    ):
        tier = AutomationTier.AUTO_ROUTED
        reasons.append(AutomationReason.ROUTINE_OPERATIONAL_ROUTE)
    else:
        tier = AutomationTier.FOCUSED_REVIEW
        reasons.append(AutomationReason.LOWER_CONFIDENCE)

    review_seconds = (
        30 if tier is AutomationTier.QUICK_CONFIRM
        else 240 if tier is AutomationTier.ESCALATED
        else 180 if tier is AutomationTier.FOCUSED_REVIEW
        else 0
    )
    allowed = (
        (action, "dismiss_with_reason")
        if action is not None and tier in {
            AutomationTier.QUICK_CONFIRM, AutomationTier.FOCUSED_REVIEW, AutomationTier.ESCALATED
        }
        else ()
    )
    priority = _priority_score(finding, tier)
    material = {
        "finding_id": finding.finding_id,
        "fingerprint": fingerprint,
        "artifact_scope_hash": artifact_scope_hash,
        "policy_hash": policy.digest,
        "tier": tier.value,
    }
    return FindingAutomation(
        automation_id=f"automation-{canonical_hash(material)[:20]}",
        finding_id=finding.finding_id,
        finding_hash=canonical_hash(finding.to_dict()),
        semantic_fingerprint=fingerprint,
        tier=tier,
        queue=queue,
        reason_codes=tuple(reasons),
        recommended_action=action,
        allowed_actions=allowed,
        draft=_draft(finding, tier, action),
        priority_score=priority,
        estimated_review_seconds=review_seconds,
    )


def _consolidate(items: Sequence[FindingAutomation]) -> list[FindingAutomation]:
    groups: dict[str, list[FindingAutomation]] = {}
    for item in items:
        groups.setdefault(item.semantic_fingerprint, []).append(item)
    result: list[FindingAutomation] = []
    for fingerprint in sorted(groups):
        members = sorted(groups[fingerprint], key=lambda item: (-item.priority_score, item.finding_id))
        primary = members[0]
        related = tuple(item.finding_id for item in members[1:])
        result.append(replace(primary, related_finding_ids=related))
        for duplicate in members[1:]:
            result.append(FindingAutomation(
                automation_id=duplicate.automation_id,
                finding_id=duplicate.finding_id,
                finding_hash=duplicate.finding_hash,
                semantic_fingerprint=duplicate.semantic_fingerprint,
                tier=AutomationTier.SUPPRESSED,
                queue=AutomationQueue.NONE,
                reason_codes=(AutomationReason.EXACT_DUPLICATE,),
                recommended_action=None,
                allowed_actions=(),
                draft={},
                priority_score=0,
                estimated_review_seconds=0,
                duplicate_of=primary.finding_id,
            ))
    return sorted(result, key=lambda item: item.finding_id)


def _semantic_fingerprint(finding: Finding, scope_hash: str) -> str:
    return canonical_hash({
        "scope_hash": scope_hash,
        "disposition": finding.disposition.value,
        "rule_id": finding.rule_id,
        "title": finding.title,
        "proposed_change": finding.proposed_change,
        "subject_ids": sorted(finding.subject_ids),
        "current_drg": finding.current_drg,
        "simulated_drg": finding.simulated_drg,
        "grouper_version": finding.grouper_version,
        "rule_package_id": finding.rule_package_id,
        "rule_package_version": finding.rule_package_version,
        "impact_status": finding.impact_status.value,
        "estimated_impact_cents": finding.estimated_impact_cents,
        "confidence": finding.confidence,
        "evidence_ids": sorted(finding.evidence_ids),
        "contradicting_evidence_ids": sorted(finding.contradicting_evidence_ids),
        "rationale": finding.rationale,
    })


def _route(disposition: Disposition) -> tuple[AutomationQueue, str | None]:
    mapping = {
        Disposition.CODING_REVIEW: (AutomationQueue.CODING, "route_to_coding"),
        Disposition.CDI_QUERY: (AutomationQueue.CDI, "route_to_cdi"),
        Disposition.CHARGE_REVIEW: (AutomationQueue.CHARGE, "route_to_charge_review"),
        Disposition.COMPLIANCE_REVIEW: (AutomationQueue.COMPLIANCE, "route_to_compliance"),
        Disposition.INSUFFICIENT_EVIDENCE: (AutomationQueue.NONE, None),
        Disposition.NO_OPPORTUNITY: (AutomationQueue.NONE, None),
    }
    return mapping[disposition]


def _priority_score(finding: Finding, tier: AutomationTier) -> int:
    tier_weight = {
        AutomationTier.FOCUSED_REVIEW: 40_000,
        AutomationTier.ESCALATED: 50_000,
        AutomationTier.QUICK_CONFIRM: 30_000,
        AutomationTier.AUTO_ROUTED: 10_000,
        AutomationTier.NEEDS_ENRICHMENT: 5_000,
        AutomationTier.SUPPRESSED: 0,
    }[tier]
    impact = 5_000 if finding.estimated_impact_cents is None else min(abs(finding.estimated_impact_cents) // 100, 10_000)
    return tier_weight + int(finding.confidence * 10_000) + impact


def _draft(finding: Finding, tier: AutomationTier, action: str | None) -> dict[str, Any]:
    if tier in {AutomationTier.SUPPRESSED, AutomationTier.NEEDS_ENRICHMENT}:
        return {}
    if finding.disposition is Disposition.CDI_QUERY:
        return {
            "kind": "cdi_query",
            "title": "Clarification requested from the treating clinician",
            "body": "Clinical indicators differ across the cited documentation. Please clarify the diagnosis and relevant specificity based on your clinical judgment.",
            "editable": True,
        }
    return {
        "kind": "workflow_note",
        "title": "Evidence-supported revenue integrity recommendation",
        "body": finding.rationale,
        "action": action,
        "editable": tier is AutomationTier.FOCUSED_REVIEW,
    }


def _bypasses_budget(item: FindingAutomation) -> bool:
    protected = {
        AutomationReason.CONTRADICTORY_EVIDENCE,
        AutomationReason.COMPLIANCE_SENSITIVE,
        AutomationReason.UNKNOWN_IMPACT,
    }
    return bool(protected.intersection(item.reason_codes))


def verify_automation_plan_hash(plan: Mapping[str, Any]) -> bool:
    claimed = plan.get("plan_hash")
    if not isinstance(claimed, str):
        return False
    policy = plan.get("policy")
    if not isinstance(policy, Mapping) or not isinstance(policy.get("digest"), str):
        return False
    policy_body = {key: value for key, value in policy.items() if key != "digest"}
    if canonical_hash(policy_body) != policy["digest"]:
        return False
    return canonical_hash({key: value for key, value in plan.items() if key != "plan_hash"}) == claimed
