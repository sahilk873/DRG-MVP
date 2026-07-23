from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping, Sequence

from .audit import canonical_hash
from .models import ClinicalUrgency, Disposition, EncounterCase, Finding, GapStatus, ImpactStatus

AUTOMATION_SCHEMA_VERSION = "1.3.0"


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
    #: Clinical-care-gap lane. Findings routed here are analytics alerts for a clinical
    #: coordinator; they NEVER mutate a claim, assign a DRG, or bypass review. This lane is
    #: fully separate from the revenue queues above so revenue routing stays byte-identical.
    CARE_GAP = "care_gap"


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
    DENIAL_EXPOSURE = "denial_exposure"
    # ---- clinical_care_gap lane reason codes (additive; never seen on revenue findings) ----
    #: A time-critical clinical care gap that a clinician must review promptly.
    EMERGENT_CARE_GAP = "emergent_care_gap"
    #: A same-day clinical care gap surfaced for focused clinician review.
    SAME_DAY_CARE_GAP = "same_day_care_gap"
    #: A routine clinical care gap safely routed to the care team as an alert.
    ROUTINE_CARE_GAP = "routine_care_gap"
    #: A gap that lacks a recommended action and needs enrichment before routing.
    GAP_NEEDS_ACTION = "gap_needs_action"
    #: A gap whose documented, evidence-grounded exception has been confirmed and is
    #: undisputed; it is suppressed (downgraded to an exception) rather than reviewed.
    GAP_EXCEPTION_CONFIRMED = "gap_exception_confirmed"


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
    priority_components: Mapping[str, int] = field(default_factory=dict)
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
            "priority_components": dict(self.priority_components),
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
    case: EncounterCase | None = None,
    policy: AutomationPolicy | None = None,
) -> dict[str, Any]:
    """Deterministically minimize reviewer work without authorizing claim mutation.

    When ``case`` carries a :class:`~revenue_integrity.financial.FinancialSnapshot`, the
    charge lines a payer has denied or placed at risk are read (never mutated) so a
    finding bound to one of those lines earns extra ``urgency_weight`` and a governed
    ``denial_exposure`` routing signal. Denial exposure only *raises* urgency — it can
    never move a review-required finding to ``suppressed`` or bypass a person.
    """
    active_policy = policy or AutomationPolicy()
    denied_line_ids = _denied_line_ids(case)
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
        _classify(item, active_policy, scope_hash, artifact_scope_hash, denied_line_ids)
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
    reviewer_effort = _reviewer_effort(consolidated, counts, seconds, len(findings))
    gap_worklist = _gap_worklist(ordered, consolidated)
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
            "reviewer_effort": reviewer_effort,
            "gap_worklist": gap_worklist,
            **counts,
        },
    }
    return {**plan_body, "plan_hash": canonical_hash(plan_body)}


#: Counterfactual human minutes a focused review would have consumed per no-touch finding.
_FOCUSED_REVIEW_SECONDS = 180


def _reviewer_effort(
    consolidated: Sequence[FindingAutomation],
    counts: Mapping[str, int],
    review_now_seconds: int,
    input_findings: int,
) -> dict[str, Any]:
    """Deterministic reviewer-productivity rollup.

    ``no_touch_rate`` and ``seconds_avoided_estimate`` are labelled estimates: they count
    findings the deterministic policy cleared without a person (suppressed, auto-routed,
    enrichment) against the input population. They never authorize a claim change.
    """
    suppressed = counts.get(AutomationTier.SUPPRESSED.value, 0)
    auto_routed = counts.get(AutomationTier.AUTO_ROUTED.value, 0)
    needs_enrichment = counts.get(AutomationTier.NEEDS_ENRICHMENT.value, 0)
    no_touch = suppressed + auto_routed + needs_enrichment
    duplicates = sum(item.duplicate_of is not None for item in consolidated)
    return {
        "estimated_review_seconds": review_now_seconds,
        "seconds_avoided_estimate": (suppressed + auto_routed) * _FOCUSED_REVIEW_SECONDS,
        "no_touch_rate": round(no_touch / input_findings, 4) if input_findings else 0.0,
        "no_touch_finding_count": no_touch,
        "consolidated_duplicate_count": duplicates,
        "is_estimate": True,
    }


#: Urgencies a care-gap coordinator treats as "high risk" for the open-gap worklist count.
_HIGH_RISK_GAP_URGENCIES = frozenset(
    {ClinicalUrgency.EMERGENT, ClinicalUrgency.URGENT, ClinicalUrgency.SAME_DAY}
)


def _round_days(value: float) -> int | float:
    """Round a day-count to 2 decimals, collapsing whole values back to int.

    Fractional timing windows (e.g. 0.5d) must survive; whole windows stay ints so
    the plan hash and schema stay clean. All arithmetic is deterministic.
    """
    rounded = round(float(value), 2)
    integral = int(rounded)
    return integral if rounded == integral else rounded


def _median_days(values: Sequence[int | float]) -> int | float:
    """Deterministic median (lower-middle for even counts) — no averaging, no float drift.

    Picking the lower-middle element (rather than averaging the two central values)
    keeps the result an exact input value, so fractional windows are preserved without
    introducing non-terminating decimals into the hashed plan.
    """
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return 0
    return _round_days(ordered[(count - 1) // 2])


def _gap_worklist(
    findings: Sequence[Finding], consolidated: Sequence[FindingAutomation]
) -> dict[str, Any]:
    """Deterministic clinical_care_gap coordinator rollup.

    Every field is derived by Python from the already-validated gap findings and their
    (also deterministic) tiering; no language model participates and nothing here mutates a
    claim, closes a gap, or bypasses review. The section is placed inside ``plan_body`` so it
    is covered by ``plan_hash`` — tampering with any figure breaks plan integrity. When no
    clinical_care_gap findings are present it is a stable empty rollup so revenue_integrity
    plans carry a byte-identical worklist regardless of population.
    """
    gaps = [finding for finding in findings if finding.is_clinical_care_gap()]
    tier_by_id = {item.finding_id: item.tier for item in consolidated}

    total = len(gaps)
    closed = sum(
        finding.gap_status in {GapStatus.CLOSED, GapStatus.EXCEPTION} for finding in gaps
    )
    # High-risk == open (not closed/exception/withdrawn), high-urgency, still routed to a person.
    open_high_risk = sum(
        (finding.gap_status in {None, GapStatus.OPEN, GapStatus.ROUTED})
        and (finding.alert_urgency in _HIGH_RISK_GAP_URGENCIES)
        and (tier_by_id.get(finding.finding_id) is not AutomationTier.SUPPRESSED)
        for finding in gaps
    )

    # These aggregate the rule-CONFIGURED expected window (timing_window_days), not an
    # observed expected->actual lateness — no observed-delay datum exists at plan-build
    # time. Named honestly (avg_expected_window_days) so the dashboard cannot read a
    # configured threshold as measured delay. Fractional windows (e.g. 0.5d) are preserved.
    windows = [
        float(finding.timing_window_days)
        for finding in gaps
        if finding.timing_window_days is not None
    ]
    avg_expected_window_days = _round_days(sum(windows) / len(windows)) if windows else 0.0

    closure_days = [
        float(finding.timing_window_days)
        for finding in gaps
        if finding.gap_status in {GapStatus.CLOSED, GapStatus.EXCEPTION}
        and finding.timing_window_days is not None
    ]
    median_closure_days = _median_days(closure_days)

    top_alert_reason = _top_counted(
        finding.alert_urgency.value for finding in gaps if finding.alert_urgency is not None
    )
    top_barrier = _top_counted(
        finding.barrier_code for finding in gaps if finding.barrier_code
    )

    return {
        "open_high_risk_gaps": open_high_risk,
        "avg_expected_window_days": avg_expected_window_days,
        "top_alert_reason": top_alert_reason,
        "gaps_closed_pct": round(closed / total, 4) if total else 0.0,
        "median_closure_days": median_closure_days,
        "top_barrier": top_barrier,
        "total_gaps": total,
        "is_estimate": True,
    }


def _top_counted(values: Any) -> str | None:
    """Deterministic mode: most frequent value, ties broken by the value's sort order."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _denied_line_ids(case: EncounterCase | None) -> frozenset[str]:
    """Read-only set of charge-line IDs a payer has denied or placed at risk.

    Sourced purely from the immutable ``FinancialSnapshot`` that already crossed the
    trust boundary. No language-model output participates; the case is never mutated.
    """
    if case is None or case.financial is None:
        return frozenset()
    at_risk: set[str] = set()
    for denial in case.financial.denials:
        at_risk.update(denial.line_ids)
    return frozenset(at_risk)


def _has_denial_exposure(finding: Finding, denied_line_ids: frozenset[str]) -> bool:
    """True when a finding is bound to at least one denied/at-risk charge line."""
    return bool(denied_line_ids) and bool(denied_line_ids.intersection(finding.charge_line_refs))


#: The single governed clinician-facing route action for a surfaced clinical care gap. It
#: routes an analytics alert to the care team; it never mutates a claim or bypasses review.
_CARE_GAP_ACTION = "route_to_care_team"

#: Deterministic urgency -> tier map for clinical_care_gap findings. Emergent and urgent
#: gaps escalate to a clinician immediately; same-day gaps get focused review; routine gaps
#: are safely auto-routed to the care-team alert lane (or held for enrichment when no
#: recommended action is present). Independent of the revenue tiering above.
_GAP_URGENCY_TIER: Mapping[ClinicalUrgency, AutomationTier] = {
    ClinicalUrgency.EMERGENT: AutomationTier.ESCALATED,
    ClinicalUrgency.URGENT: AutomationTier.ESCALATED,
    ClinicalUrgency.SAME_DAY: AutomationTier.FOCUSED_REVIEW,
    ClinicalUrgency.ROUTINE: AutomationTier.AUTO_ROUTED,
}
_GAP_URGENCY_REASON: Mapping[ClinicalUrgency, AutomationReason] = {
    ClinicalUrgency.EMERGENT: AutomationReason.EMERGENT_CARE_GAP,
    ClinicalUrgency.URGENT: AutomationReason.EMERGENT_CARE_GAP,
    ClinicalUrgency.SAME_DAY: AutomationReason.SAME_DAY_CARE_GAP,
    ClinicalUrgency.ROUTINE: AutomationReason.ROUTINE_CARE_GAP,
}


def _has_confirmed_undisputed_exception(finding: Finding) -> bool:
    """True when the gap carries at least one ``confirmed`` exception check and none disputed.

    A confirmed, undisputed exception is an evidence-grounded, human-recorded reason the
    surfaced gap is a legitimate non-gap. The deterministic policy honors it by suppressing
    the finding (downgrading it to an exception) so a resolved gap never re-consumes a
    clinician's focused-review budget. A single ``disputed`` check keeps the gap live.
    """
    statuses = [str(check.get("status", "")).strip().lower() for check in finding.exception_checks]
    if any(status == "disputed" for status in statuses):
        return False
    return any(status == "confirmed" for status in statuses)


def _classify_care_gap(
    finding: Finding, fingerprint: str, artifact_scope_hash: str, policy: AutomationPolicy,
) -> FindingAutomation:
    """Tier a clinical_care_gap finding on its own lane.

    Analytics identify the gap; a clinician decides. The finding never mutates a claim
    (its proposed_change is empty by the Finding wall) and always requires human review
    unless a confirmed, undisputed exception has already resolved it.
    """
    reasons: list[AutomationReason] = []
    if _has_confirmed_undisputed_exception(finding):
        # Downgraded to an exception: suppressed, off every review queue, no action.
        tier = AutomationTier.SUPPRESSED
        queue = AutomationQueue.NONE
        action: str | None = None
        reasons.append(AutomationReason.GAP_EXCEPTION_CONFIRMED)
    else:
        urgency = finding.alert_urgency or ClinicalUrgency.ROUTINE
        tier = _GAP_URGENCY_TIER[urgency]
        reasons.append(_GAP_URGENCY_REASON[urgency])
        # A routine gap can only auto-route when it names a concrete recommended action;
        # otherwise it is held for enrichment (never silently dropped).
        if tier is AutomationTier.AUTO_ROUTED and not (finding.recommended_action or "").strip():
            tier = AutomationTier.NEEDS_ENRICHMENT
            reasons.append(AutomationReason.GAP_NEEDS_ACTION)
        if tier is AutomationTier.NEEDS_ENRICHMENT:
            queue = AutomationQueue.NONE
            action = None
        else:
            queue = AutomationQueue.CARE_GAP
            action = _CARE_GAP_ACTION

    review_seconds = (
        240 if tier is AutomationTier.ESCALATED
        else 180 if tier is AutomationTier.FOCUSED_REVIEW
        else 0
    )
    allowed = (
        (action, "dismiss_with_reason")
        if action is not None and tier in {
            AutomationTier.FOCUSED_REVIEW, AutomationTier.ESCALATED
        }
        else ()
    )
    components = _priority_components(finding, tier, False)
    priority = sum(components.values())
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
        draft=_gap_draft(finding, tier, action),
        priority_score=priority,
        priority_components=components,
        estimated_review_seconds=review_seconds,
    )


def _gap_draft(finding: Finding, tier: AutomationTier, action: str | None) -> dict[str, Any]:
    if tier in {AutomationTier.SUPPRESSED, AutomationTier.NEEDS_ENRICHMENT}:
        return {}
    return {
        "kind": "care_gap_alert",
        "title": finding.title,
        "body": finding.recommended_action or finding.rationale,
        "action": action,
        # A gap alert is presentational only; a clinician records the terminal decision.
        "editable": False,
    }


def _classify(
    finding: Finding, policy: AutomationPolicy, scope_hash: str, artifact_scope_hash: str,
    denied_line_ids: frozenset[str] = frozenset(),
) -> FindingAutomation:
    fingerprint = _semantic_fingerprint(finding, scope_hash)
    # Clinical care gaps ride a fully separate lane so revenue tiering stays byte-identical.
    if finding.is_clinical_care_gap():
        return _classify_care_gap(finding, fingerprint, artifact_scope_hash, policy)
    queue, action = _route(finding.disposition)
    reasons: list[AutomationReason] = []
    drg_change = finding.current_drg != finding.simulated_drg
    has_change = bool(finding.proposed_change)
    impact = finding.estimated_impact_cents
    denial_exposure = _has_denial_exposure(finding, denied_line_ids)

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

    # Governed high-urgency signal. Payer denial exposure adds a routing reason and
    # raises priority, but it must never suppress a review-required finding: a finding
    # that resolved to a no-opportunity/no-actionable-change suppression keeps that
    # deterministic disposition. Only findings that still route to a person are flagged.
    if denial_exposure and tier is not AutomationTier.SUPPRESSED:
        reasons.append(AutomationReason.DENIAL_EXPOSURE)

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
    components = _priority_components(finding, tier, denial_exposure)
    priority = sum(components.values())
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
        priority_components=components,
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
                priority_components={
                    "tier_weight": 0, "confidence_weight": 0, "impact_weight": 0, "urgency_weight": 0,
                },
                estimated_review_seconds=0,
                duplicate_of=primary.finding_id,
            ))
    return sorted(result, key=lambda item: item.finding_id)


def _semantic_fingerprint(finding: Finding, scope_hash: str) -> str:
    body: dict[str, Any] = {
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
    }
    # Additive: gap-distinguishing fields join the fingerprint ONLY for clinical_care_gap
    # findings, so revenue_integrity fingerprints (and consolidation) stay byte-identical.
    # This keeps an open gap from being consolidated away by a confirmed-exception variant.
    if finding.is_clinical_care_gap():
        body["gap_domain"] = finding.gap_domain.value if finding.gap_domain else None
        body["alert_urgency"] = finding.alert_urgency.value if finding.alert_urgency else None
        body["gap_exception_confirmed"] = _has_confirmed_undisputed_exception(finding)
    return canonical_hash(body)


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


#: Neutral impact weight for findings whose dollar effect is unknown. It keeps an
#: unknown-impact escalation ahead of low-dollar routine work without inventing a figure.
_UNKNOWN_IMPACT_WEIGHT = 5_000

#: Base urgency by tier (severity of the deterministic disposition). Scaled by
#: ``_URGENCY_TIER_SCALE`` so it dominates the bounded impact ramp below.
_URGENCY_TIER_RANK = {
    AutomationTier.ESCALATED: 4,
    AutomationTier.QUICK_CONFIRM: 3,
    AutomationTier.FOCUSED_REVIEW: 3,
    AutomationTier.AUTO_ROUTED: 1,
    AutomationTier.NEEDS_ENRICHMENT: 1,
    AutomationTier.SUPPRESSED: 0,
}
_URGENCY_TIER_SCALE = 1_000
#: Per-$1,000 step of the bounded impact ramp, capped at ``_URGENCY_IMPACT_MAX`` steps so
#: dollar magnitude nudges urgency without swamping the (uncapped) ``impact_weight``.
_URGENCY_IMPACT_STEP_CENTS = 100_000
_URGENCY_IMPACT_MAX_STEPS = 25
_URGENCY_IMPACT_STEP_WEIGHT = 100
#: Neutral urgency contribution for a finding whose dollar effect is unknown.
_URGENCY_UNKNOWN_IMPACT = 500
#: Fixed high-urgency bump when a finding is bound to a denied/at-risk charge line.
_URGENCY_DENIAL_EXPOSURE = 10_000


def _urgency_weight(finding: Finding, tier: AutomationTier, denial_exposure: bool) -> int:
    """Deterministic integer urgency score.

    ``urgency_weight = tier_rank * 1000``
        ``+ min(abs(impact_cents) // 100_000, 25) * 100`` (or a neutral 500 when impact is
        unknown, never treated as zero)
        ``+ 10_000`` when the finding is bound to a payer-denied / at-risk charge line.

    All terms are integers so the plan hash stays reproducible. Denial exposure only
    raises the score; it is applied after tier classification and never suppresses a
    finding. Suppressed / duplicate findings carry ``urgency_weight = 0`` (see
    ``_consolidate``).
    """
    if tier is AutomationTier.SUPPRESSED:
        return 0
    tier_component = _URGENCY_TIER_RANK[tier] * _URGENCY_TIER_SCALE
    if finding.estimated_impact_cents is None:
        impact_component = _URGENCY_UNKNOWN_IMPACT
    else:
        steps = min(abs(finding.estimated_impact_cents) // _URGENCY_IMPACT_STEP_CENTS, _URGENCY_IMPACT_MAX_STEPS)
        impact_component = steps * _URGENCY_IMPACT_STEP_WEIGHT
    denial_component = _URGENCY_DENIAL_EXPOSURE if denial_exposure else 0
    return tier_component + impact_component + denial_component


def _priority_components(
    finding: Finding, tier: AutomationTier, denial_exposure: bool = False,
) -> dict[str, int]:
    """Transparent, integer, deterministic priority breakdown.

    The impact weight is intentionally UNCAPPED (dollars = ``abs(cents) // 100``) so a
    six-figure recovery outranks a routine one instead of saturating at a shared ceiling.
    ``urgency_weight`` layers disposition severity, a bounded impact ramp, and a fixed
    denial-exposure bump (see :func:`_urgency_weight`). Every component is an integer to
    keep the plan hash reproducible; ``priority_score`` is simply their sum.
    """
    tier_weight = {
        AutomationTier.FOCUSED_REVIEW: 40_000,
        AutomationTier.ESCALATED: 50_000,
        AutomationTier.QUICK_CONFIRM: 30_000,
        AutomationTier.AUTO_ROUTED: 10_000,
        AutomationTier.NEEDS_ENRICHMENT: 5_000,
        AutomationTier.SUPPRESSED: 0,
    }[tier]
    impact_weight = (
        _UNKNOWN_IMPACT_WEIGHT
        if finding.estimated_impact_cents is None
        else abs(finding.estimated_impact_cents) // 100
    )
    return {
        "tier_weight": tier_weight,
        "confidence_weight": int(finding.confidence * 10_000),
        "impact_weight": impact_weight,
        "urgency_weight": _urgency_weight(finding, tier, denial_exposure),
    }


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
